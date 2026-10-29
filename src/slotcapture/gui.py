"""Desktop front end. All capture operations use the public JSON CLI."""
import datetime
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


STATES = {'PREPARED': '准备就绪', 'STARTING': '正在连接', 'CHECKING_CONNECTION': '检测游戏心跳',
          'WAITING_FOR_SPIN': '等待首转核验', 'CAPTURING': '正在采集',
          'DRAINING': '等待奖励链收尾', 'COMPLETE': '采集完成',
          'STOPPED_EARLY': '提前停止', 'STOPPED_INCOMPLETE': '数据不完整', 'FAILED': '采集失败'}
EVENTS = {'run_prepared': '计划已建立', 'first_spin_verified': '首转核验通过',
          'paid_target_reached': '付费次数已达目标，请停止新增付费', 'replay_finished': '回放结束',
          'feature_confirmed': '特殊奖励已确认', 'feature_expected': '特殊奖励待结算',
          'capture_issue': '采集异常，请暂停新增付费', 'capture_failed': '采集失败',
          'capture_finished': '采集已结束', 'state_changed': '阶段变化',
          'net_loss_stop_reached': '已达净亏损停止线，请停止新增付费', 'heartbeat_verified': '游戏双向心跳匹配成功'}
LEVELS = {'info': '信息', 'warning': '注意', 'error': '错误', 'critical': '异常', 'action_required': '需操作'}


def event_summary(event):
    detail = event.get('detail', {})
    title = EVENTS.get(event['kind'], event['kind'])
    if event['kind'] in ('feature_confirmed', 'feature_expected'):
        feature = {'wheel': '转盘', 'jackpot': 'Pick / JP', 'beer_final': '啤酒终局', 'free': '免费游戏'}.get(detail.get('feature'), detail.get('feature', ''))
        return f"{title} · {feature} · 第{detail.get('paid_cycle', '?')}次付费 · 奖励 {detail.get('award', 0):,}"
    if event['kind'] == 'state_changed':
        return title + ' · ' + STATES.get(detail.get('state'), detail.get('state', ''))
    if event['kind'] in ('paid_target_reached', 'net_loss_stop_reached'):
        return title
    if event['kind'] == 'first_spin_verified':
        return title + f" · 总下注 {detail.get('bet', 0):,}"
    if event['kind'] in ('capture_finished', 'replay_finished'):
        return title + ' · ' + STATES.get(detail.get('state'), detail.get('state', ''))
    return title + '  ' + json.dumps(detail, ensure_ascii=False)


def cli_prefix():
    if getattr(sys, 'frozen', False):
        return [str(Path(sys.executable).with_name('slot-capture.exe'))]
    return [sys.executable, '-m', 'slotcapture.cli']


def invoke(arguments):
    result = subprocess.run(cli_prefix() + list(arguments), capture_output=True,
                            encoding='utf-8', errors='replace',
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        envelope = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'CLI未返回JSON')
    if not envelope.get('ok'):
        raise RuntimeError(envelope.get('error', {}).get('message', '命令失败'))
    return envelope['data']


class CaptureApp:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.busy = False
        self.polling = False
        self.run = None
        self.cursor = 0
        root.title('Slot Capture · 本地采集工作台')
        root.geometry('1080x760')
        root.minsize(960, 700)
        root.configure(bg='#eef5f0')
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), background='#eef5f0')
        style.configure('TFrame', background='#eef5f0')
        style.configure('TLabel', background='#eef5f0', foreground='#193c2e')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 21, 'bold'))
        style.configure('Card.TLabel', font=('Microsoft YaHei UI', 17, 'bold'))
        style.configure('TButton', padding=(12, 7), background='#d8e9dd')
        style.configure('Accent.TButton', background='#23784d', foreground='white')
        style.map('Accent.TButton', background=[('active', '#195e3b')])
        style.configure('Treeview', rowheight=29, background='white', fieldbackground='white')
        style.configure('Treeview.Heading', background='#d8e9dd', padding=6)
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='Slot Capture', style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer, text='本地采集工作台  /  米亚 · Bole v1').pack(anchor='w', pady=(0, 14))
        self.note = tk.StringVar(value='配置新批次，或打开已有运行目录查看记录。')
        ttk.Label(outer, textvariable=self.note, wraplength=1030).pack(anchor='w', pady=(0, 12))
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill='both', expand=True)
        self.monitor = ttk.Frame(self.tabs, padding=16)
        setup = ttk.Frame(self.tabs, padding=16)
        self.tabs.add(self.monitor, text='  实时看板  ')
        self.tabs.add(setup, text='  新建采集  ')
        toolbar = ttk.Frame(self.monitor)
        toolbar.pack(fill='x')
        for title, action in [('打开批次', self.open_run), ('打开文件夹', self.open_folder),
                              ('检查环境', self.doctor), ('导出', self.export)]:
            ttk.Button(toolbar, text=title, command=action).pack(side='left', padx=(0, 8))
        self.path_label = tk.StringVar(value='尚未选择批次')
        ttk.Label(self.monitor, textvariable=self.path_label, wraplength=990).pack(anchor='w', pady=12)
        cards = ttk.Frame(self.monitor)
        cards.pack(fill='x')
        self.cards = {}
        for key, label in [('state', '当前阶段'), ('paid', '付费 / 目标'), ('free', '免费次数'), ('money', '已核算返奖')]:
            box = ttk.Frame(cards, padding=(12, 5))
            box.pack(side='left', expand=True, fill='x')
            ttk.Label(box, text=label).pack(anchor='w')
            value = tk.StringVar(value='—')
            self.cards[key] = value
            ttk.Label(box, textvariable=value, style='Card.TLabel').pack(anchor='w', pady=4)
        self.progress = ttk.Progressbar(self.monitor)
        self.progress.pack(fill='x', pady=10)
        self.health = tk.StringVar(value='连接：未检查')
        ttk.Label(self.monitor, textvariable=self.health, wraplength=990).pack(anchor='w')
        actions = ttk.Frame(self.monitor)
        actions.pack(fill='x', pady=8)
        self.start_button = ttk.Button(actions, text='开始被动采集', style='Accent.TButton', command=self.start)
        self.start_button.pack(side='left', padx=(0, 8))
        self.stop_button = ttk.Button(actions, text='停止采集 · 等待尾链', command=self.stop)
        self.stop_button.pack(side='left', padx=(0, 8))
        ttk.Button(actions, text='审计数据', command=self.audit).pack(side='left')
        ttk.Label(self.monitor, text='到达目标后，请手动停止游戏自动旋转；本程序不会代替你停止游戏。',
                  foreground='#855b22').pack(anchor='w', pady=(0, 10))
        event_box = ttk.Frame(self.monitor)
        event_box.pack(fill='both', expand=True)
        self.events = ttk.Treeview(event_box, columns=('time', 'level', 'event'), show='headings', height=5)
        for key, title, width in [('time', '时间 / UTC', 160), ('level', '级别', 70), ('event', '事件', 650)]:
            self.events.heading(key, text=title)
            self.events.column(key, width=width, anchor='w')
        scroll = ttk.Scrollbar(event_box, orient='vertical', command=self.events.yview)
        self.events.configure(yscrollcommand=scroll.set)
        self.events.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.events.tag_configure('error', foreground='#aa2929')
        self.events.tag_configure('warning', foreground='#855b22')
        self.events.tag_configure('critical', foreground='#aa2929')
        self.events.tag_configure('action_required', foreground='#aa2929')
        self.events.bind('<<TreeviewSelect>>', self.show_event)
        self.event_data = {}
        self.detail = tk.Text(self.monitor, height=3, wrap='word', relief='flat', font=('Microsoft YaHei UI', 9))
        self.detail.pack(fill='x', pady=(10, 0))
        self.detail.configure(state='disabled')
        self.fields = {}
        defaults = [('run', '新批次目录', str(Path.cwd() / 'captures' / datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))),
                    ('bet', '总下注 Bet', '13500000'), ('paid', '付费旋转目标', '100'),
                    ('adb', 'ADB 程序路径', ''), ('device', '设备序列号', ''),
                    ('game-ip', '当前游戏服务器 IPv4', ''), ('package', '游戏包名', ''),
                    ('seconds', '采集最长秒数', '3600'), ('tail-seconds', '奖励尾链等待秒数', '120'),
                    ('max-net-loss', '净亏损停止线（可空）', '')]
        for row, (key, label, value) in enumerate(defaults):
            ttk.Label(setup, text=label).grid(row=row, column=0, sticky='w', pady=7, padx=(0, 16))
            var = tk.StringVar(value=value)
            self.fields[key] = var
            ttk.Entry(setup, textvariable=var).grid(row=row, column=1, sticky='ew', pady=7)
            if key in ('run', 'adb'):
                ttk.Button(setup, text='浏览', command=lambda k=key: self.browse(k)).grid(row=row, column=2, padx=8)
        setup.columnconfigure(1, weight=1)
        ttk.Label(setup, text='新批次目录必须尚未存在。设备、包名和IP需确认是当前目标；仅支持米亚适配器。',
                  wraplength=900).grid(row=10, column=0, columnspan=3, sticky='w', pady=14)
        ttk.Button(setup, text='建立采集计划', style='Accent.TButton', command=self.create).grid(row=11, column=1, sticky='w')
        ttk.Button(setup, text='从已有 plan.json 填入配置', command=self.import_plan).grid(row=12, column=1, sticky='w', pady=12)
        ttk.Label(outer, text='关闭窗口不停止后台采集  ·  CLI 与 GUI 共用同一份状态和事件', foreground='#587061').pack(anchor='w', pady=(12, 0))
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(100, self.drain)
        self.root.after(1000, self.poll)
        self.tabs.select(setup)

    def submit(self, args, callback, label):
        if self.busy or self.polling:
            self.note.set('正在处理上一项操作，请稍后再试。')
            return
        self.busy = True
        self.note.set(label)
        def work():
            try:
                result = invoke(args)
                self.queue.put((callback, result, None))
            except Exception as exc:
                self.queue.put((callback, None, str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def drain(self):
        try:
            while True:
                callback, value, error = self.queue.get_nowait()
                self.busy = False
                if error:
                    self.note.set('操作未完成：' + error)
                    messagebox.showerror('操作未完成', error, parent=self.root)
                else:
                    callback(value)
        except queue.Empty:
            pass
        self.root.after(100, self.drain)

    def browse(self, key):
        if key == 'adb':
            value = filedialog.askopenfilename(filetypes=[('ADB', '*.exe'), ('所有文件', '*')])
        else:
            parent = filedialog.askdirectory(title='选择新批次的父目录')
            value = str(Path(parent) / datetime.datetime.now().strftime('%Y%m%d-%H%M%S')) if parent else ''
        if value:
            self.fields[key].set(value)

    def import_plan(self):
        path = filedialog.askopenfilename(filetypes=[('采集计划', '*.json')])
        if not path:
            return
        self.submit(['validate', '--plan', path], self.fill_plan, '正在读取配置…')

    def fill_plan(self, plan):
        mapping = {'bet': plan['bet'], 'paid': plan['target_paid'], 'max-net-loss': plan.get('max_net_loss')}
        mapping.update({k: plan['capture'].get(k.replace('-', '_')) for k in ('adb', 'device', 'game-ip', 'package', 'seconds', 'tail-seconds')})
        for key, value in mapping.items():
            self.fields[key].set('' if value is None else str(value))
        self.note.set('配置已填入，请核验当前设备与IP，再建立新批次。')

    def create(self):
        args = ['init']
        for key, var in self.fields.items():
            value = var.get().strip()
            if value:
                args += ['--' + key, value]
        self.submit(args, lambda d: self.select_run(d['run']), '正在建立计划…')

    def open_run(self):
        if self.busy or self.polling:
            return
        path = filedialog.askdirectory(title='打开包含 plan.json 的运行目录')
        if path:
            if not (Path(path) / 'plan.json').is_file():
                messagebox.showerror('不是运行目录', '该目录中没有 plan.json。')
                return
            self.select_run(path)

    def select_run(self, path):
        self.run = str(Path(path).resolve())
        self.cursor = 0
        self.events.delete(*self.events.get_children())
        self.event_data.clear()
        self.path_label.set(self.run)
        self.tabs.select(self.monitor)
        self.note.set('批次已打开，正在读取状态。')

    def command(self, name, callback=None, extra=None):
        if not self.run:
            messagebox.showinfo('先选择批次', '请新建计划或打开已有运行目录。')
            return
        args = [name, '--plan', str(Path(self.run) / 'plan.json')] if name == 'doctor' else [name, '--run', self.run]
        self.submit(args + (extra or []), callback or self.show_result, '正在执行：' + name)

    def show_result(self, data):
        self.note.set('操作完成，详情见下方。')
        self.set_detail(json.dumps(data, ensure_ascii=False, indent=2))

    def doctor(self): self.command('doctor')
    def audit(self): self.command('audit')
    def start(self): self.command('start')
    def stop(self): self.command('stop')

    def export(self):
        if not self.run:
            return
        parent = filedialog.askdirectory(title='选择导出父目录（自动建立新文件夹）')
        if parent:
            target = Path(parent) / (Path(self.run).name + '-export-' + datetime.datetime.now().strftime('%H%M%S'))
            self.command('export', extra=['--out', str(target)])

    def open_folder(self):
        if self.run:
            os.startfile(self.run)

    def poll(self):
        if self.run and not self.busy and not self.polling:
            self.polling = True
            target, cursor = self.run, self.cursor
            def work():
                try:
                    status = invoke(['status', '--run', target])
                    events = invoke(['events', '--run', target, '--after', str(cursor), '--limit', '100'])
                    self.queue.put((lambda data: self.update_monitor(target, *data), (status, events), None))
                except Exception as exc:
                    self.queue.put((self.poll_error, str(exc), None))
            threading.Thread(target=work, daemon=True).start()
        self.root.after(2500, self.poll)

    def poll_error(self, error):
        self.polling = False
        self.health.set('状态读取失败：' + error)

    def update_monitor(self, target, status, events):
        self.polling = False
        if self.run != target:
            return
        self.cards['state'].set(STATES.get(status['state'], status['state']))
        self.cards['paid'].set(f"{status.get('paid', 0)} / {status.get('target_paid', '—')}")
        self.cards['free'].set(str(status.get('free', 0)))
        self.cards['money'].set(f"{status.get('expected_return', 0):,}")
        self.progress.configure(maximum=max(1, status.get('target_paid', 1)), value=status.get('paid', 0))
        self.health.set(('连接：在线' if status.get('live') else '连接：未在线（准备 / 离线 / 已结束）') +
                        f"  |  更新时间 UTC：{status.get('checked_utc', '—')}  |  审计问题：{status.get('issue_count', 0)}")
        self.start_button.configure(state='normal' if status['state'] == 'PREPARED' else 'disabled')
        self.stop_button.configure(state='normal' if status.get('worker_alive') and status['state'] not in ('COMPLETE', 'FAILED', 'STOPPED_EARLY', 'STOPPED_INCOMPLETE') else 'disabled')
        for event in events['events']:
            key = str(event['cursor'])
            self.event_data[key] = event
            text = event_summary(event)
            self.events.insert('', 'end', iid=key, values=(event.get('time_utc', '')[:19], LEVELS.get(event.get('severity'), event.get('severity', '')), text[:220]), tags=(event.get('severity', ''),))
            if event.get('severity') in ('warning', 'error', 'critical', 'action_required'):
                self.note.set('请注意：' + text[:250])
                self.root.bell()
        self.cursor = events['next_cursor']
        if events['events']:
            self.events.see(str(self.cursor))
        if status['state'] == 'DRAINING':
            self.note.set('请停止新增付费旋转，正常完成奖励界面；采集器正在等待尾链。')
        elif status['state'] == 'COMPLETE':
            self.note.set('本批次已完成，当前显示已存档结果。可查看事件、审计或导出数据。')

    def set_detail(self, text):
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('1.0', text)
        self.detail.configure(state='disabled')

    def show_event(self, _event):
        selection = self.events.selection()
        if selection:
            self.set_detail(json.dumps(self.event_data[selection[0]], ensure_ascii=False, indent=2))

    def close(self):
        if self.busy or self.polling:
            self.note.set('正在完成本次读取或操作，请稍后关闭。')
            return
        self.root.destroy()


def main():
    root = tk.Tk()
    app = CaptureApp(root)
    if len(sys.argv) == 3 and sys.argv[1] == '--run':
        app.select_run(sys.argv[2])
    root.mainloop()
