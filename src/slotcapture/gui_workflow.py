"""User-led onboarding: connect, confirm a machine, verify one spin, sample."""
import datetime as dt
import json
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import uuid
from .common import read, write
from .workflow import default_store
from .gui import invoke, STATES, event_summary
from .vision import DEFAULT_MODEL


class WorkflowApp:
    def __init__(self, root):
        self.root = root
        self.store = default_store()
        self.store.mkdir(parents=True, exist_ok=True)
        self.queue = queue.Queue()
        self.busy = False
        self.profile = None
        self.connection = None
        self.active = None
        self.cursor = 0
        self.records = {}
        self.images = []
        self.detecting = False
        self.last_diagnostic = None
        self.detected_android_user = None
        self.last_deleted = None
        root.title('Slot Capture · 机台采集工作台')
        root.geometry('1180x820')
        root.minsize(1000, 740)
        root.configure(bg='#f3f6f3')
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), background='#f3f6f3', foreground='#203d2e')
        style.configure('TFrame', background='#f3f6f3')
        style.configure('TButton', padding=(12, 6), background='#e0ebe2')
        style.configure('Accent.TButton', background='#287448', foreground='white')
        style.map('Accent.TButton', background=[('active', '#1d5a36')])
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 18, 'bold'))
        style.configure('Metric.TLabel', font=('Microsoft YaHei UI', 15, 'bold'))
        style.configure('Treeview', rowheight=28, background='white', fieldbackground='white')
        style.configure('Treeview.Heading', background='#dce9df', padding=5)
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='机台采集工作台', style='Title.TLabel').pack(anchor='w')
        self.notice = tk.StringVar(value='先在模拟器中打开准备采集的机台，停在可旋转界面。')
        ttk.Label(outer, textvariable=self.notice, wraplength=1100).pack(anchor='w', pady=(6, 12))
        body = ttk.Frame(outer)
        body.pack(fill='both', expand=True)
        library = ttk.Frame(body, padding=(0, 0, 14, 0))
        library.pack(side='left', fill='y')
        ttk.Label(library, text='机台档案').pack(anchor='w', pady=(0, 8))
        self.machines = tk.Listbox(library, width=23, height=16, relief='flat', exportselection=False,
                                   font=('Microsoft YaHei UI', 10), bg='white', selectbackground='#d8e8dc', selectforeground='#173b27')
        self.machines.pack(fill='both', expand=True)
        self.machines.bind('<<ListboxSelect>>', self.choose_machine)
        ttk.Button(library, text='＋ 新建机台', command=self.new_machine).pack(fill='x', pady=(10, 5))
        ttk.Button(library, text='刷新档案', command=self.refresh_library).pack(fill='x')
        ttk.Button(library, text='删除选中档案', command=self.delete_machine).pack(fill='x',pady=(6,0))
        self.undo_delete_button=ttk.Button(library,text='撤销删除',command=self.undo_delete,state='disabled')
        self.undo_delete_button.pack(fill='x',pady=(6,0))
        self.tabs = ttk.Notebook(body)
        self.tabs.pack(side='left', fill='both', expand=True)
        self.pages = {}
        for key, title in [('home', '① 连接机台'), ('rules', '② 规则录入'), ('verify', '③ 首转测试'), ('capture', '④ 正式采集')]:
            page = ttk.Frame(self.tabs, padding=16)
            self.pages[key] = page
            self.tabs.add(page, text=title)
        self.fields = {}
        home = self.pages['home']
        ttk.Label(home, text='先打开游戏，再检测连接', style='Metric.TLabel').pack(anchor='w')
        ttk.Label(home, text='自动查找模拟器、读取前台游戏，再筛选游戏连接并验证双向心跳。\n请保持目标游戏在模拟器前台；检测不会点击、旋转或改Bet。',
                  wraplength=820).pack(anchor='w', pady=10)
        name_form = ttk.Frame(home); name_form.pack(fill='x', pady=6)
        ttk.Label(name_form, text='机台名称（可稍后填写）').pack(side='left', padx=(0, 12))
        self.fields['name'] = tk.StringVar()
        ttk.Entry(name_form, textvariable=self.fields['name']).pack(side='left', fill='x', expand=True)
        self.detected_summary = tk.StringVar(value='设备：待检测\n游戏：待检测\n连接：待检测')
        ttk.Label(home, textvariable=self.detected_summary, wraplength=820, padding=(10, 10)).pack(fill='x', pady=6)
        self.details_open = tk.BooleanVar(value=False)
        ttk.Checkbutton(home, text='显示连接详情 / 手动修正', variable=self.details_open,
                        command=self.toggle_details).pack(anchor='w', pady=5)
        self.connection_details = ttk.Frame(home)
        form = self.connection_details
        for row, (key, label) in enumerate([('adb', 'ADB路径'), ('device', '设备序列号'),
                                            ('package', '游戏包名'), ('game_ip', '当前服务器IPv4')]):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', pady=6, padx=(0, 12))
            var = tk.StringVar()
            self.fields[key] = var
            ttk.Entry(form, textvariable=var).grid(row=row, column=1, sticky='ew', pady=6)
            if key == 'adb':
                ttk.Button(form, text='浏览', command=self.browse_adb).grid(row=row, column=2, padx=6)
        form.columnconfigure(1, weight=1)
        ttk.Button(form, text='按以上详情直接核验', command=lambda: self.launch('connection')).grid(row=4, column=1, sticky='w', pady=6)
        ttk.Button(form, text='打开检测诊断', command=self.open_diagnostic).grid(row=4, column=2, padx=6)
        self.details_anchor = ttk.Frame(home); self.details_anchor.pack(fill='x')
        self.adapter = tk.StringVar(value='米亚 / Bole（已适配）')
        ttk.Label(home, text='采集适配器').pack(anchor='w')
        ttk.Combobox(home, textvariable=self.adapter, values=['米亚 / Bole（已适配）', '其他机台（仅建档，待适配）'],
                     state='readonly').pack(fill='x', pady=7)
        ttk.Button(home, text='自动检测并连接', style='Accent.TButton', command=self.connect).pack(anchor='w', pady=10)
        self.connection_text = tk.StringVar(value='尚未检测。检测期间可以切到规则页录入资料。')
        ttk.Label(home, textvariable=self.connection_text, wraplength=820).pack(anchor='w', pady=6)
        ttk.Button(home, text='下一步：规则录入', command=lambda: self.tabs.select(self.pages['rules'])).pack(anchor='e', pady=10)
        self.build_rules()
        verify = self.pages['verify']
        ttk.Label(verify, text='只转一次，先验证能否正确读懂', style='Metric.TLabel').pack(anchor='w')
        ttk.Label(verify, text='先确认规则并通过连接检测，再点击下面按钮开启监听。\n随后由你在游戏里手动Spin一次；若触发奖励，按正常流程完成。\n程序等待完整奖励链与消息静默，再做最终审计。首转单独存档，不混入正式采集轮数。',
                  wraplength=820).pack(anchor='w', pady=14)
        self.verification_text = tk.StringVar(value='尚未通过首转测试')
        ttk.Label(verify, textvariable=self.verification_text, wraplength=820).pack(anchor='w', pady=10)
        ttk.Button(verify, text='开始首转监听', style='Accent.TButton', command=lambda: self.launch('first_spin')).pack(anchor='w', pady=10)
        ttk.Button(verify, text='下一步：正式采集', command=lambda: self.tabs.select(self.pages['capture'])).pack(anchor='e')
        self.build_capture()
        self.session_text = tk.StringVar(value='没有正在查看的采集任务')
        ttk.Label(outer, textvariable=self.session_text, wraplength=1100).pack(anchor='w', pady=(10, 0))
        ttk.Label(outer, text='关闭窗口后监听仍在后台运行；本程序不会自动操作游戏。', foreground='#65766b').pack(anchor='w', pady=(4, 0))
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.drain)
        root.after(2000, self.poll)
        self.new_machine()
        self.refresh_library()

    def task(self, work, callback, label):
        if self.busy:
            self.notice.set('正在执行上一项操作，请稍后。')
            return
        self.busy = True
        self.notice.set(label)
        def run():
            try: self.queue.put((callback, work(), None))
            except Exception as exc: self.queue.put((callback, None, str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def drain(self):
        try:
            while True:
                callback, data, error = self.queue.get_nowait()
                self.busy = False
                if error:
                    self.notice.set(error)
                    if self.detecting:
                        self.detecting=False
                        self.connection_text.set('检测失败：'+error)
                        self.detected_summary.set('设备 / 游戏：检测未完成\n连接：未核验')
                    messagebox.showerror('操作未完成', error, parent=self.root)
                else:
                    callback(data)
        except queue.Empty: pass
        self.root.after(100, self.drain)

    def cli(self, *args):
        return invoke([*args, '--store', str(self.store)])

    def refresh_library(self):
        self.task(lambda: self.cli('machine-list'), self.library_loaded, '正在读取本地机台档案…')

    def library_loaded(self, data):
        self.records = {p['id']: p for p in data['machines']}
        self.render_library()
        self.notice.set('先在模拟器中打开准备采集的机台，再检测环境与心跳。')

    def render_library(self):
        self.machines.delete(0, 'end')
        for p in self.records.values():
            self.machines.insert('end', p['name'] + ('  ✓' if p.get('first_spin_verified') else ''))

    def choose_machine(self, _event):
        selection = self.machines.curselection()
        if not selection or self.busy: return
        if self.active:
            messagebox.showinfo('任务尚未结束', '请先等待当前连接检测或采集任务结束。')
            return
        self.load_profile(list(self.records.values())[selection[0]])

    def new_machine(self):
        if self.active or self.busy: return
        self.machines.selection_clear(0,'end')
        self.load_profile(dict(id=uuid.uuid4().hex[:16], name='', adapter='mia-bole-v1', capture={},
                               symbols=[], features=[], notes='', screenshots=[], rules_confirmed=False))
        self.tabs.select(self.pages['home'])

    def delete_machine(self):
        if self.busy or self.active:
            self.notice.set('请先等待当前检测或采集任务结束，再删除档案。');return
        selected=self.machines.curselection()
        if not selected:
            self.notice.set('请先在左侧选中要删除的机台档案。');return
        profile=list(self.records.values())[selected[0]]
        if not messagebox.askyesno('删除机台档案',f"删除「{profile['name']}」？\n档案可撤销恢复，历史采集数据与规则截图保留。",parent=self.root):return
        identity=profile['id']
        self.task(lambda:self.cli('machine-delete','--id',identity),self.machine_deleted,'正在删除档案…')

    def machine_deleted(self,result):
        self.last_deleted=result
        self.records.pop(result['id'],None)
        self.render_library()
        if self.profile and self.profile['id']==result['id']:self.new_machine()
        self.undo_delete_button.configure(state='normal')
        self.notice.set(f"已删除「{result['name']}」；历史数据保留，可点击“撤销删除”恢复。")

    def undo_delete(self):
        if self.busy or self.active:
            self.notice.set('请先等待当前检测或采集任务结束。');return
        if not self.last_deleted:return
        deleted=self.last_deleted
        self.task(lambda:self.cli('machine-restore','--id',deleted['id'],'--deleted',deleted['deleted']),
                  self.machine_restored,'正在恢复档案…')

    def machine_restored(self,profile):
        self.records[profile['id']]=profile
        self.render_library()
        self.load_profile(profile)
        index=list(self.records).index(profile['id'])
        self.machines.selection_set(index)
        self.last_deleted=None
        self.undo_delete_button.configure(state='disabled')
        self.notice.set(f"已恢复「{profile['name']}」。")

    def load_profile(self, p):
        self.profile = p
        self.connection = None
        self.detected_android_user=p.get('capture',{}).get('android_user')
        for k, var in self.fields.items(): var.set((p.get('name', '') if k == 'name' else p.get('capture', {}).get(k, '')) or '')
        self.adapter.set('米亚 / Bole（已适配）' if p['adapter'] == 'mia-bole-v1' else '其他机台（仅建档，待适配）')
        self.confirmed.set(p.get('rules_confirmed', False))
        self.images = list(p.get('screenshots', []))
        self.images_text.set(f'已选择 {len(self.images)} 张截图')
        self.put_rows(self.symbols, p['symbols'], ('name', 'role', 'size', 'notes', 'protocol_id'))
        self.put_rows(self.features, p['features'], ('name', 'notes'))
        self.notes.delete('1.0', 'end'); self.notes.insert('1.0', p.get('notes', ''))
        self.connection_text.set('本次尚未检测；已保存设备配置，可直接点击检测。')
        cap = p.get('capture', {})
        self.detected_summary.set(f"设备：{cap.get('device') or '待检测'}\n游戏：{cap.get('package') or '待检测'}\n连接：{cap.get('game_ip') or '待检测'}（需本次重新核验）")
        self.verification_text.set('已通过首转，可在连接成功后直接正式采集。' if p.get('first_spin_verified') else '尚未通过首转测试')

    def browse_adb(self):
        p = filedialog.askopenfilename(filetypes=[('ADB', '*.exe')])
        if p: self.fields['adb'].set(p)

    def toggle_details(self):
        if self.details_open.get():
            self.connection_details.pack(fill='x', before=self.details_anchor, pady=5)
        else:
            self.connection_details.pack_forget()

    def table(self, parent, columns, height=5):
        box = ttk.Frame(parent); box.pack(fill='both', expand=True)
        tree = ttk.Treeview(box, columns=[c[0] for c in columns], show='headings', height=height)
        for key, title, width in columns:
            tree.heading(key, text=title); tree.column(key, width=width, minwidth=45, anchor='w')
        scroll = ttk.Scrollbar(box, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left', fill='both', expand=True); scroll.pack(side='right', fill='y')
        return tree

    def build_rules(self):
        page = self.pages['rules']
        top = ttk.Frame(page); top.pack(fill='x')
        ttk.Button(top, text='选择规则截图', command=self.choose_images).pack(side='left')
        ttk.Button(top, text='用Codex识图', command=self.recognize).pack(side='left', padx=6)
        self.images_text = tk.StringVar(value='未选择截图；也可直接手动录入')
        ttk.Label(top, textvariable=self.images_text).pack(side='left', padx=6)
        model = ttk.Frame(page); model.pack(fill='x', pady=8)
        self.codex = tk.StringVar(value='')
        self.model = tk.StringVar(value=DEFAULT_MODEL)
        ttk.Label(model, text='Codex路径（空=自动）').pack(side='left')
        ttk.Entry(model, textvariable=self.codex, width=26).pack(side='left', padx=6)
        ttk.Label(model, text='模型（经济档）').pack(side='left')
        ttk.Entry(model, textvariable=self.model, width=20).pack(side='left', padx=6)
        ttk.Label(model, text='思考：最低 low').pack(side='left')
        ttk.Label(page, text='Symbol名称与玩法需人工确认；协议ID可留空，识图不会推断数字ID。', wraplength=820).pack(anchor='w', pady=5)
        ttk.Label(page, text='识图会将所选截图发送给Codex，使用CLI当前登录的额度。', foreground='#65766b').pack(anchor='w')
        self.symbols = self.table(page, [('name', '名称', 115), ('role', '类型', 80), ('size', '占格', 70), ('notes', '说明', 300), ('protocol_id', '协议ID', 65)], 4)
        bar = ttk.Frame(page); bar.pack(fill='x', pady=5)
        self.row_buttons(bar, self.symbols, ['名称', '类型', '占格', '说明', '协议ID'])
        ttk.Label(page, text='可能的特殊玩法').pack(anchor='w', pady=(5, 3))
        self.features = self.table(page, [('name', '玩法名称', 140), ('notes', '说明 / 待确认', 550)], 3)
        bar = ttk.Frame(page); bar.pack(fill='x', pady=5)
        self.row_buttons(bar, self.features, ['玩法名称', '说明 / 待确认'])
        self.notes = tk.Text(page, height=2, font=('Microsoft YaHei UI', 9), wrap='word', relief='flat')
        self.notes.pack(fill='x', pady=5)
        self.confirmed = tk.BooleanVar(value=False)
        ttk.Checkbutton(page, text='我已核对以上Symbol和玩法信息', variable=self.confirmed).pack(anchor='w')
        ttk.Button(page, text='保存机台档案', style='Accent.TButton', command=self.save).pack(anchor='e', pady=7)

    def row_buttons(self, parent, tree, labels):
        ttk.Button(parent, text='新增', command=lambda: self.edit_row(tree, labels)).pack(side='left')
        ttk.Button(parent, text='修改选中行', command=lambda: self.edit_row(tree, labels, True)).pack(side='left', padx=6)
        ttk.Button(parent, text='删除选中行', command=lambda: tree.delete(*tree.selection())).pack(side='left')

    def edit_row(self, tree, labels, edit=False):
        selection = tree.selection()
        if edit and not selection: return
        window = tk.Toplevel(self.root); window.title('编辑资料'); window.transient(self.root)
        window.geometry('560x330')
        old = list(tree.item(selection[0], 'values')) if edit else ['']*len(labels)
        variables = []
        for row, label in enumerate(labels):
            ttk.Label(window, text=label).grid(row=row, column=0, padx=12, pady=8, sticky='w')
            var = tk.StringVar(value=old[row]); variables.append(var)
            ttk.Entry(window, textvariable=var, width=45).grid(row=row, column=1, padx=10, pady=8, sticky='ew')
        def commit():
            values = [v.get().strip() for v in variables]
            if not values[0]: return
            if edit: tree.item(selection[0], values=values)
            else: tree.insert('', 'end', values=values)
            self.confirmed.set(False)
            window.destroy()
        ttk.Button(window, text='保存这一行', command=commit).grid(row=len(labels), column=1, sticky='e', padx=10, pady=10)
        window.columnconfigure(1, weight=1)

    def put_rows(self, tree, rows, columns):
        tree.delete(*tree.get_children())
        for row in rows: tree.insert('', 'end', values=['' if row.get(k) is None else str(row[k]) for k in columns])

    def choose_images(self):
        from .image_input import RuleImageDialog
        RuleImageDialog(self.root,self.store,self.images,self.images_selected)

    def images_selected(self, images):
        self.images=list(images)
        self.images_text.set(f'已选择 {len(self.images)} 张截图')

    def recognize(self):
        if not self.images:
            self.notice.set('请先选择规则截图，或直接手动新增Symbol与玩法。'); return
        args = ['vision-read']
        for p in self.images: args += ['--image', p]
        if self.codex.get().strip(): args += ['--codex', self.codex.get().strip()]
        if self.model.get().strip(): args += ['--model', self.model.get().strip()]
        self.task(lambda: self.cli(*args), self.vision_done, 'Codex正在读取规则截图…')

    def vision_done(self, result):
        draft = result['draft']
        if not messagebox.askyesno('识别草稿已生成', '是否用识别草稿替换当前Symbol与玩法清单？替换后仍可逐行修改。', parent=self.root):
            self.notice.set('草稿已保存，但未替换当前填写内容。'); return
        if draft.get('name'): self.fields['name'].set(draft['name'])
        self.put_rows(self.symbols, draft['symbols'], ('name', 'role', 'size', 'notes', 'protocol_id'))
        self.put_rows(self.features, draft['features'], ('name', 'notes'))
        self.notes.delete('1.0', 'end'); self.notes.insert('1.0', draft.get('notes', ''))
        self.confirmed.set(False)
        self.notice.set('识别结果是草稿，请核对、修改后勾选确认并保存。')

    def profile_data(self):
        p = dict(self.profile)
        cap = dict(p.get('capture', {}))
        for k in ('adb', 'device', 'package', 'game_ip'): cap[k] = self.fields[k].get().strip() or None
        if self.detected_android_user is None: cap.pop('android_user',None)
        else: cap['android_user']=self.detected_android_user
        p.update(name=self.fields['name'].get().strip(), capture=cap,
                 adapter='mia-bole-v1' if self.adapter.get().startswith('米亚') else 'unadapted',
                 rules_confirmed=self.confirmed.get(), screenshots=self.images, notes=self.notes.get('1.0', 'end').strip())
        for key, tree, columns in [('symbols', self.symbols, ('name', 'role', 'size', 'notes', 'protocol_id')),
                                   ('features', self.features, ('name', 'notes'))]:
            p[key] = [dict(zip(columns, tree.item(row, 'values'))) for row in tree.get_children()]
        return p

    def save_sync(self, data):
        path = self.store/'drafts'/(uuid.uuid4().hex+'.json')
        path.parent.mkdir(exist_ok=True)
        write(path, data)
        return self.cli('machine-save', '--input', str(path))

    def save(self):
        data = self.profile_data()
        self.task(lambda: self.save_sync(data), self.saved, '正在保存机台档案…')

    def saved(self, p):
        self.profile = p
        self.records[p['id']] = p; self.render_library()
        self.images = p.get('screenshots', [])
        self.notice.set('机台档案已保存。')
        self.verification_text.set('已通过首转，可跳过测试。' if p.get('first_spin_verified') else '保存后可进行首转测试。')

    def new_run_path(self, identity, purpose):
        return str(self.store/'runs'/identity/(purpose+'-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:5]))

    def connect(self):
        self.detect_connection()

    def detect_connection(self, device=None, adb=None):
        if self.busy:
            self.notice.set('正在执行上一项操作，请稍后。'); return
        if self.active:
            self.notice.set('已有监听任务，先等待当前任务结束。'); return
        self.connection = None
        self.detecting = True
        self.detected_android_user=None
        self.fields['game_ip'].set('')
        self.fields['package'].set('')
        self.fields['device'].set('')
        self.detected_summary.set('设备：检测中\n游戏：检测中\n连接：检测中')
        args = ['discover']
        hint = adb or self.fields['adb'].get().strip()
        if hint: args += ['--adb', hint]
        if device: args += ['--device', device]
        self.connection_text.set('自动检测中：查找设备、前台游戏和实时连接；多个候选逐一探测心跳，可能需要约2分钟。')
        self.task(lambda: self.cli(*args), self.discovered, '正在自动检测，请保持游戏在模拟器前台…')

    def discovered(self, result):
        self.detecting=False
        self.last_diagnostic=result.get('diagnostic_path')
        stage = result['stage']
        cap = result.get('capture', {})
        self.detected_android_user=cap.get('android_user')
        for key in ('adb', 'device', 'package', 'game_ip'):
            value = cap.get(key) or result.get(key)
            if value: self.fields[key].set(value)
        self.notice.set(result.get('message', '检测完成'))
        self.connection_text.set(result.get('message', '检测完成'))
        self.show_detected(cap or {k:result.get(k) for k in ('device','package','game_ip')})
        if stage == 'choose_device':
            rows = result['devices']
            self.choose_candidate('选择模拟器', [f"{r['model']}  ·  {r['device']}" for r in rows],
                                  lambda i: self.detect_connection(rows[i]['device'], rows[i]['adb']))
        elif stage == 'choose_server':
            self.show_detected(cap)
            rows = result['candidates']
            self.choose_candidate('选择候选连接', [f"{r['ip']}  ·  端口 {', '.join(map(str, r['ports']))}" for r in rows],
                                  lambda i: self.use_detected(dict(cap, game_ip=rows[i]['ip'])))
        elif stage in ('detected', 'candidate'):
            self.use_detected(cap)
        elif stage == 'probe_cleanup_required':
            self.notice.set(result['message'])
        else:
            self.detected_summary.set(self.detected_summary.get()+'\n状态：检测未通过')
            self.details_open.set(True); self.toggle_details()

    def show_detected(self, cap):
        self.detected_summary.set(f"设备：{cap.get('device') or '待检测'}\n游戏：{cap.get('package') or '待检测'}\n连接：{cap.get('game_ip') or '待选择'}")

    def use_detected(self, cap):
        self.detected_android_user=cap.get('android_user')
        for key in ('adb', 'device', 'package', 'game_ip'): self.fields[key].set(cap.get(key) or '')
        self.show_detected(cap)
        self.launch('connection')

    def open_diagnostic(self):
        if self.last_diagnostic:
            os.startfile(self.last_diagnostic)
        else:
            self.notice.set('尚无检测诊断，请先执行一次自动检测。')

    def choose_candidate(self, title, rows, callback):
        window = tk.Toplevel(self.root); window.title(title); window.transient(self.root)
        window.geometry('660x330')
        ttk.Label(window, text=title+'；取消后也可展开连接详情手动修正。', wraplength=600).pack(anchor='w', padx=15, pady=12)
        choices = tk.Listbox(window, height=8, font=('Microsoft YaHei UI', 10), exportselection=False)
        choices.pack(fill='both', expand=True, padx=15)
        for row in rows: choices.insert('end', row)
        def select():
            indexes = choices.curselection()
            if indexes:
                index = indexes[0]; window.destroy(); callback(index)
        ttk.Button(window, text='选择并继续', command=select).pack(anchor='e', padx=15, pady=12)

    def launch(self, purpose):
        if self.active:
            self.notice.set('已有监听任务，先等待当前任务结束。'); return
        data = self.profile_data()
        if purpose == 'connection' and not data['name']:
            data['name'] = '未命名机台'
            self.fields['name'].set(data['name'])
        try:
            target = int(self.paid_target.get()) if purpose == 'sampling' else 1
            if not 1 <= target <= 1000000: raise ValueError()
        except ValueError:
            self.notice.set('请填写1至1000000之间的付费轮数。'); return
        run = self.new_run_path(data['id'], purpose)
        connection = self.connection
        def work():
            p = self.save_sync(data)
            args = ['session-prepare', '--id', p['id'], '--purpose', purpose, '--run', run, '--paid', str(target)]
            if connection: args += ['--connection', connection]
            self.cli(*args)
            invoke(['start', '--run', run])
            return p, run, purpose
        self.task(work, self.launched, '正在检查并启动监听…')

    def launched(self, data):
        self.profile, self.active, self.purpose = data
        self.records[self.profile['id']] = self.profile; self.render_library()
        self.cursor = 0
        self.events.delete(*self.events.get_children())
        self.notice.set('正在检测双向心跳；可同时录入规则。' if self.purpose == 'connection' else
                        '监听已启动：请在游戏中手动Spin一次并完成奖励流程。' if self.purpose == 'first_spin' else
                        '正式采集已启动，请在游戏中按计划旋转；Bet从首个有效付费结果读取。')
        self.session_text.set(self.active)
        if self.purpose == 'connection': self.connection_text.set('检测中：等待游戏双向心跳…')
        elif self.purpose == 'first_spin': self.verification_text.set('等待一次付费Spin及完整奖励链…')

    def build_capture(self):
        page = self.pages['capture']
        top = ttk.Frame(page); top.pack(fill='x')
        ttk.Label(top, text='付费轮数').pack(side='left')
        self.paid_target = tk.StringVar(value='100')
        ttk.Entry(top, textvariable=self.paid_target, width=10).pack(side='left', padx=10)
        ttk.Button(top, text='开始正式采集', style='Accent.TButton', command=lambda: self.launch('sampling')).pack(side='left')
        ttk.Button(top, text='结束监听并收尾', command=self.stop).pack(side='left', padx=8)
        self.metrics = tk.StringVar(value='Bet 待检测  ·  付费 0  ·  免费 0')
        ttk.Label(page, textvariable=self.metrics, style='Metric.TLabel', wraplength=820).pack(anchor='w', pady=18)
        self.stage = tk.StringVar(value='尚未开始')
        ttk.Label(page, textvariable=self.stage, wraplength=820).pack(anchor='w', pady=4)
        self.progress = ttk.Progressbar(page); self.progress.pack(fill='x', pady=12)
        ttk.Label(page, text='免费局另计；达到付费目标后请手动停止新增付费，等待奖励链收尾。', wraplength=820).pack(anchor='w', pady=5)
        self.events = self.table(page, [('time', '时间 / UTC', 150), ('event', '事件', 590)], 8)
        bar = ttk.Frame(page); bar.pack(fill='x', pady=10)
        ttk.Button(bar, text='打开记录目录', command=self.open_folder).pack(side='left')
        ttk.Button(bar, text='查看已有批次', command=self.open_existing).pack(side='left', padx=8)
        ttk.Button(bar, text='导出结果', command=self.export).pack(side='left')

    def poll(self):
        if self.active and not self.busy:
            run = self.active
            cursor = self.cursor
            def work():
                return run, invoke(['status', '--run', run]), invoke(['events', '--run', run, '--after', str(cursor), '--limit', '100'])
            self.task(work, self.polled, self.notice.get())
        self.root.after(2000, self.poll)

    def polled(self, result):
        run, s, events = result
        if self.active != run: return
        self.last_run = run
        bet = s.get('detected_bet')
        self.metrics.set(f"Bet {bet:,}" if bet else 'Bet 待检测')
        self.metrics.set(self.metrics.get()+f"  ·  付费 {s.get('paid', 0)} / {s.get('target_paid', 0)}  ·  免费 {s.get('free', 0)}")
        self.stage.set(STATES.get(s['state'], s['state'])+f"  ·  {'在线' if s.get('live') else '后台未在线'}  ·  问题 {s.get('issue_count', 0)}")
        self.progress.configure(maximum=max(1, s.get('target_paid', 1)), value=s.get('paid', 0))
        for e in events['events']:
            self.events.insert('', 'end', values=(e.get('time_utc', '')[:19], event_summary(e)))
            if e.get('severity') in ('critical', 'action_required'): self.notice.set(event_summary(e))
        self.cursor = events['next_cursor']
        if s['state'] == 'DRAINING': self.notice.set('请停止新增付费，完成当前奖励流程，等待最终审计。')
        if s['state'] in ('COMPLETE', 'STOPPED_EARLY', 'STOPPED_INCOMPLETE', 'FAILED'):
            purpose = getattr(self, 'purpose', s.get('purpose', 'sampling'))
            self.active = None
            if purpose == 'connection' and s['state'] == 'COMPLETE' and s.get('heartbeat_matched'):
                self.connection = run
                self.connection_text.set('连接成功：已匹配双向游戏心跳。完成规则确认后可首转，已验证机台可直接采集。')
                self.notice.set('环境准备就绪。连接证据10分钟内有效；开始首转或采集时还会复查设备连接。')
            elif purpose == 'first_spin' and s['state'] == 'COMPLETE':
                self.verification_text.set('首转通过：完整奖励链与最终审计已通过，记录已存入机台档案。')
                self.notice.set('首转通过，可填写正式付费轮数后开始采集。')
                self.reload_current()
            elif s['state'] == 'COMPLETE': self.notice.set('本批次完整完成，结果已归档。')
            else: self.notice.set('监听未完整完成：'+str(s.get('error') or s['state']))

    def reload_current(self):
        identity = self.profile['id']
        def done(p):
            self.profile = p
            self.records[p['id']] = p; self.render_library()
            self.verification_text.set('首转已通过，后续连接成功可直接采集。' if p.get('first_spin_verified') else '首转档案未通过，请检查审计记录。')
        self.task(lambda: self.cli('machine-show', '--id', identity), done, self.notice.get())

    def stop(self):
        if self.active:
            run = self.active
            self.task(lambda: invoke(['stop', '--run', run]), lambda _: self.notice.set('已请求结束监听；请停止新增付费并完成奖励流程。'), '正在请求收尾…')

    def open_folder(self):
        run = self.active or getattr(self, 'last_run', None)
        if run: os.startfile(run)

    def open_existing(self):
        if self.active: return
        run = filedialog.askdirectory(title='选择包含plan.json的批次目录')
        if run and (Path(run)/'plan.json').exists():
            self.active = run; self.purpose = read(Path(run)/'plan.json').get('purpose', 'sampling'); self.cursor = 0
            self.events.delete(*self.events.get_children()); self.tabs.select(self.pages['capture'])

    def export(self):
        run = self.active or getattr(self, 'last_run', None)
        if not run: return
        parent = filedialog.askdirectory(title='选择导出父目录')
        if parent:
            out = str(Path(parent)/(Path(run).name+'-export-'+uuid.uuid4().hex[:5]))
            self.task(lambda: invoke(['export', '--run', run, '--out', out]), lambda d: self.notice.set('已导出到 '+d['output']), '正在导出…')

    def close(self):
        if self.busy:
            self.notice.set('正在完成当前操作，请稍后关闭。'); return
        self.root.destroy()


def main():
    from tkinterdnd2 import TkinterDnD
    root = TkinterDnD.Tk()
    app = WorkflowApp(root)
    if len(sys.argv) == 3 and sys.argv[1] == '--run':
        run = str(Path(sys.argv[2]).resolve())
        if (Path(run)/'plan.json').is_file():
            app.active = run
            app.purpose = read(Path(run)/'plan.json').get('purpose', 'sampling')
            app.tabs.select(app.pages['capture'])
    root.mainloop()
