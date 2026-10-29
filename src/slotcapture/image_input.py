"""Local image import and a user-operated drop/paste panel."""
import hashlib
import io
from pathlib import Path
import shutil
import tkinter as tk
from tkinter import ttk, filedialog
from PIL import Image, ImageGrab, ImageOps, ImageTk, UnidentifiedImageError
from tkinterdnd2 import DND_FILES, COPY


MAX_BYTES = 20*1024*1024
MAX_PIXELS = 40_000_000
MAX_IMAGES = 12
FORMATS = {'.png', '.jpg', '.jpeg', '.webp'}


class ImageInbox:
    def __init__(self, store):
        self.directory=Path(store)/'image-inbox'
        self.directory.mkdir(parents=True, exist_ok=True)

    def validate(self, path):
        path=Path(path)
        if path.suffix.lower() not in FORMATS or not path.is_file():
            raise ValueError('请选择PNG、JPEG或WebP图片')
        if path.stat().st_size>MAX_BYTES:
            raise ValueError('单张图片不能超过20MiB')
        try:
            with Image.open(path) as image:
                if image.format not in ('PNG','JPEG','WEBP'):
                    raise ValueError('图片内容需为PNG、JPEG或WebP格式')
                if image.width*image.height>MAX_PIXELS:
                    raise ValueError('图片像素过大，请拆分截图后导入')
                image.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError('图片无法读取或文件已损坏') from exc
        return path

    def import_file(self, path):
        path=self.validate(path)
        content=path.read_bytes()
        with Image.open(path) as image:
            extension={'PNG':'.png','JPEG':'.jpg','WEBP':'.webp'}[image.format]
        destination=self.directory/(hashlib.sha256(content).hexdigest()+extension)
        if not destination.exists():shutil.copyfile(path, destination)
        return str(destination.resolve())

    def import_bitmap(self, image):
        if not isinstance(image,Image.Image):raise ValueError('剪贴板中没有图片')
        if image.width*image.height>MAX_PIXELS:raise ValueError('图片像素过大，请拆分截图后粘贴')
        data=io.BytesIO()
        # Encode bitmap clipboard data without changing its dimensions or alpha channel.
        if image.mode not in ('1','L','LA','RGB','RGBA','P'):image=image.convert('RGBA')
        image.save(data,format='PNG')
        content=data.getvalue()
        if len(content)>MAX_BYTES:raise ValueError('粘贴图片超过20MiB，请拆分截图')
        destination=self.directory/(hashlib.sha256(content).hexdigest()+'.png')
        if not destination.exists():destination.write_bytes(content)
        return str(destination.resolve())


class RuleImageDialog:
    def __init__(self, parent, store, images, on_accept):
        self.inbox=ImageInbox(store)
        self.on_accept=on_accept
        self.items=[]
        self.window=tk.Toplevel(parent)
        self.window.title('导入规则截图')
        self.window.geometry('900x610')
        self.window.minsize(760,530)
        self.window.transient(parent)
        outer=ttk.Frame(self.window,padding=18);outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='把规则截图放到这里',style='Metric.TLabel').pack(anchor='w')
        drop=tk.Frame(outer,background='#e5efe8',highlightbackground='#68a27c',highlightthickness=2,height=105)
        drop.pack(fill='x',pady=12);drop.pack_propagate(False)
        message=tk.Label(drop,text='拖拽图片到此处，或按 Ctrl+V 粘贴截图\n可连续追加多张，也可以粘贴复制的图片文件',
                         bg='#e5efe8',fg='#285b3a',font=('Microsoft YaHei UI',11))
        message.pack(expand=True,fill='both')
        toolbar=ttk.Frame(outer);toolbar.pack(fill='x')
        ttk.Button(toolbar,text='从文件选择',command=self.choose_files).pack(side='left')
        ttk.Button(toolbar,text='粘贴截图 / 文件',command=self.paste).pack(side='left',padx=8)
        ttk.Button(toolbar,text='移除选中',command=self.remove).pack(side='left')
        ttk.Button(toolbar,text='清空列表',command=self.clear).pack(side='left',padx=8)
        body=ttk.Frame(outer);body.pack(fill='both',expand=True,pady=12)
        left=ttk.Frame(body);left.pack(side='left',fill='both',expand=True)
        self.listbox=tk.Listbox(left,font=('Microsoft YaHei UI',10),relief='flat',exportselection=False,
                               selectmode='extended',background='white',selectbackground='#d8e8dc',selectforeground='#173b27')
        scroll=ttk.Scrollbar(left,command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        preview_box=ttk.Frame(body,padding=(12,0,0,0));preview_box.pack(side='right',fill='both')
        self.preview=tk.Label(preview_box,text='选择一张图片预览',width=40,bg='white',fg='#65766b')
        self.preview.pack(fill='both',expand=True)
        self.status=tk.StringVar(value='最多12张；单张20MiB以内。图片只导入本地，点击“用Codex识图”后才发送。')
        ttk.Label(outer,textvariable=self.status,wraplength=840).pack(anchor='w')
        bottom=ttk.Frame(outer);bottom.pack(fill='x',pady=(12,0))
        ttk.Button(bottom,text='取消',command=self.window.destroy).pack(side='right')
        self.accept_button=ttk.Button(bottom,text='使用这些截图',style='Accent.TButton',command=self.accept)
        self.accept_button.pack(side='right',padx=8)
        self.listbox.bind('<<ListboxSelect>>',self.show_preview)
        self.window.bind('<Control-v>',self.paste)
        self.window.bind('<Control-V>',self.paste)
        for target in (drop,message,self.listbox):
            target.drop_target_register(DND_FILES)
            target.dnd_bind('<<Drop>>',self.drop)
        self.add_files(images)
        self.window.focus_set()

    def choose_files(self):
        paths=filedialog.askopenfilenames(parent=self.window,filetypes=[('规则截图','*.png *.jpg *.jpeg *.webp')])
        if paths:self.add_files(paths)

    def append(self,path,name):
        if any(item['path']==path for item in self.items):return False
        if len(self.items)>=MAX_IMAGES:raise ValueError('最多12张，请先移除不需要的截图')
        self.items.append(dict(path=path,name=name));return True

    def add_files(self, paths):
        added=0;errors=[]
        for path in paths:
            try:
                saved=self.inbox.import_file(path)
                added+=self.append(saved,Path(path).name)
            except (ValueError,OSError) as exc:errors.append(f'{Path(path).name}：{exc}')
        self.refresh()
        if errors:self.status.set('；'.join(errors[:3]))
        elif added:self.status.set(f'已添加{added}张，共{len(self.items)}张；可继续拖入或粘贴。')
        elif paths:self.status.set('这些图片已在列表中，没有重复添加。')

    def drop(self,event):
        try:self.add_files(self.window.tk.splitlist(event.data))
        except tk.TclError:self.status.set('无法解析拖入的文件，请改用文件选择或粘贴。')
        return COPY

    def paste(self,event=None):
        try:
            content=ImageGrab.grabclipboard()
            if isinstance(content,Image.Image):
                path=self.inbox.import_bitmap(content)
                added=self.append(path,f'粘贴截图 {len(self.items)+1}')
                self.refresh()
                self.status.set(f'已粘贴，共{len(self.items)}张。' if added else '这张截图已在列表中，没有重复添加。')
            elif isinstance(content,list):self.add_files(content)
            else:self.status.set('剪贴板中没有图片。请先截图，或在文件管理器复制图片文件。')
        except (OSError,ValueError,Image.DecompressionBombError) as exc:self.status.set('粘贴失败：'+str(exc))
        return 'break'

    def refresh(self):
        self.listbox.delete(0,'end')
        for i,item in enumerate(self.items,1):self.listbox.insert('end',f"{i:02d}  {item['name']}")
        self.accept_button.configure(text=f'使用这{len(self.items)}张截图')
        if self.items:
            self.listbox.selection_set(len(self.items)-1);self.show_preview()
        else:
            self.preview.configure(image='',text='选择一张图片预览',width=40,height=10);self.preview.image=None

    def show_preview(self,event=None):
        selection=self.listbox.curselection()
        if not selection:return
        try:
            with Image.open(self.items[selection[0]]['path']) as image:
                image=ImageOps.exif_transpose(image)
                image.thumbnail((330,260))
                photo=ImageTk.PhotoImage(image,master=self.window)
            self.preview.configure(image=photo,text='',width=330,height=260);self.preview.image=photo
        except (OSError,ValueError) as exc:self.status.set('无法预览：'+str(exc))

    def remove(self):
        for index in reversed(self.listbox.curselection()):self.items.pop(index)
        self.refresh();self.status.set(f'当前{len(self.items)}张。移除仅影响本次选择。')

    def clear(self):
        self.items=[];self.refresh();self.status.set('列表已清空，可继续拖入或粘贴。')

    def accept(self):
        self.on_accept([item['path'] for item in self.items])
        self.window.destroy()
