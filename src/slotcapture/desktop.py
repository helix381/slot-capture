"""CSS-rendered desktop shell; all capture operations still use the public CLI."""
import base64
from functools import wraps
import io
import json
import os
from pathlib import Path
import uuid
import threading
from . import __version__
from .common import read, write, digest, TERMINAL
from .gui import invoke, event_summary
from .workflow import default_store, connection_ready


def response(method):
    @wraps(method)
    def wrapped(self,*args,**kwargs):
        try:
            with self._lock:return dict(ok=True,data=method(self,*args,**kwargs))
        except Exception as exc:return dict(ok=False,error=dict(message=str(exc)))
    return wrapped


class DesktopAPI:
    def __init__(self,store=None,command=None):
        self._lock=threading.RLock()
        self._store=Path(store or default_store()).resolve()
        self._store.mkdir(parents=True,exist_ok=True)
        self._invoke=command or invoke
        self._window=None
        self._maximized=False
        self._last_deleted=None
        path=self._store/'desktop-session.json'
        self._session=read(path) if path.exists() else {}
        self._run=self._session.get('run')
        self._connection=self._session.get('connection')
        self._diagnostic=self._session.get('diagnostic')

    def _cli(self,*args):return self._invoke([*args,'--store',str(self._store)])

    def _persist(self):
        self._session.update(run=self._run,connection=self._connection,diagnostic=self._diagnostic)
        write(self._store/'desktop-session.json',self._session)

    def _idle(self):
        if self._run and (Path(self._run)/'plan.json').exists():
            s=self._invoke(['status','--run',self._run])
            if s.get('worker_alive') or (s['state'] not in TERMINAL and s['state']!='PREPARED'):
                raise ValueError('当前监听任务尚未结束，请先收尾或等待任务结束')

    def _save(self,profile):
        profile=dict(profile)
        profile.setdefault('id',uuid.uuid4().hex[:16])
        path=self._store/'drafts'/(uuid.uuid4().hex+'.json')
        path.parent.mkdir(exist_ok=True);write(path,profile)
        try:result=self._cli('machine-save','--input',str(path))
        finally:path.unlink(missing_ok=True)
        self._session['selected_id']=result['id'];self._persist()
        return result

    def _start(self,profile,purpose,paid=1):
        self._idle()
        profile=self._save(profile)
        root=self._store/'runs'/profile['id']/(purpose+'-'+uuid.uuid4().hex[:16])
        args=['session-prepare','--id',profile['id'],'--purpose',purpose,'--run',str(root),'--paid',str(paid)]
        if self._connection:args+=['--connection',self._connection]
        self._cli(*args)
        self._run=str(root);self._persist()
        self._invoke(['start','--run',str(root)])
        return dict(profile=profile,run=self._run,purpose=purpose)

    @response
    def bootstrap(self):
        machines=self._cli('machine-list')['machines']
        selected=next((p for p in machines if p['id']==self._session.get('selected_id')),machines[0] if machines else None)
        ready=bool(selected and self._connection and connection_ready(selected,self._connection))
        if not ready:self._connection=None
        return dict(version=__version__,machines=machines,selected=selected,run=self._run,
                    connection_ready=ready,diagnostic=self._diagnostic)

    @response
    def select_machine(self,identity):
        self._idle()
        result=self._cli('machine-show','--id',identity) if identity else None
        self._run=None;self._connection=None;self._session['selected_id']=identity;self._persist()
        return result

    @response
    def save_machine(self,profile):
        self._idle()
        return self._save(profile)

    @response
    def discover(self,profile,device=None,adb=None):
        self._idle();self._connection=None;self._persist()
        args=['discover'];hint=adb or profile.get('capture',{}).get('adb')
        if hint:args+=['--adb',hint]
        if device:args+=['--device',device]
        result=self._cli(*args)
        self._diagnostic=result.get('diagnostic_path');self._persist()
        return result

    @response
    def connect(self,profile,capture):
        profile=dict(profile,name=profile.get('name') or '未命名机台',capture=dict(profile.get('capture',{}),**capture))
        return self._start(profile,'connection')

    @response
    def start_session(self,profile,purpose,paid=1):
        if purpose not in ('first_spin','sampling'):raise ValueError('未知任务类型')
        if isinstance(paid,bool) or not isinstance(paid,int) or not 1<=paid<=1000000:raise ValueError('请输入有效付费轮数')
        return self._start(profile,purpose,paid if purpose=='sampling' else 1)

    @response
    def poll(self,cursor=0):
        if not self._run or not (Path(self._run)/'plan.json').exists():return dict(run=None)
        status=self._invoke(['status','--run',self._run])
        events=self._invoke(['events','--run',self._run,'--after',str(cursor),'--limit','100'])
        for e in events['events']:e['display']=event_summary(e)
        plan=read(Path(self._run)/'plan.json')
        purpose=plan.get('purpose','sampling')
        profile=None
        if status['state'] in TERMINAL:
            if purpose=='connection' and status['state']=='COMPLETE' and status.get('heartbeat_matched'):
                self._connection=self._run;self._persist()
            if plan.get('machine_id'):
                try:profile=self._cli('machine-show','--id',plan['machine_id'])
                except FileNotFoundError:pass
                except RuntimeError:pass
        current=None
        identity=self._session.get('selected_id')
        if identity:
            try:current=self._cli('machine-show','--id',identity)
            except RuntimeError:pass
        ready=bool(current and self._connection and connection_ready(current,self._connection))
        # Only show totals with final audit semantics. Interim rewards remain provisional.
        return dict(run=self._run,status=status,purpose=purpose,events=events,profile=profile,
                    capture=plan['capture'],connection_ready=ready)

    @response
    def stop(self):
        if not self._run:raise ValueError('尚无监听任务')
        return self._invoke(['stop','--run',self._run])

    @response
    def delete_machine(self,identity):
        self._idle();result=self._cli('machine-delete','--id',identity)
        self._last_deleted=result
        if self._session.get('selected_id')==identity:
            self._session['selected_id']=None;self._connection=None;self._run=None;self._persist()
        return result

    @response
    def restore_machine(self):
        self._idle()
        if not self._last_deleted:raise ValueError('本次会话没有可撤销的删除')
        p=self._cli('machine-restore','--id',self._last_deleted['id'],'--deleted',self._last_deleted['deleted'])
        self._last_deleted=None;self._run=None;self._connection=None;self._session['selected_id']=p['id'];self._persist()
        return p

    @response
    def import_images(self,files):
        from .image_input import ImageInbox
        if len(files)>12:raise ValueError('最多12张截图')
        inbox=ImageInbox(self._store);paths=[]
        for item in files:
            name=Path(item.get('name','image.png')).name
            suffix=Path(name).suffix.lower()
            if suffix not in ('.png','.jpg','.jpeg','.webp'):raise ValueError('不支持的图片格式')
            encoded=item.get('base64','')
            if len(encoded)>28*1024*1024:raise ValueError('单张图片不能超过20MiB')
            raw=base64.b64decode(encoded,validate=True)
            temp=inbox.directory/('upload-'+uuid.uuid4().hex+suffix)
            try:
                temp.write_bytes(raw);path=inbox.import_file(temp)
            finally:temp.unlink(missing_ok=True)
            if path not in paths:paths.append(path)
        return dict(paths=paths)

    @response
    def preview_images(self,paths):
        from PIL import Image,ImageOps
        result=[]
        for path in paths[:12]:
            path=Path(path).resolve()
            if not path.is_relative_to(self._store):raise ValueError('截图路径不在档案库中')
            with Image.open(path) as original:
                image=ImageOps.exif_transpose(original).convert('RGB');image.thumbnail((480,360))
                data=io.BytesIO();image.save(data,format='JPEG',quality=85)
            result.append(dict(path=str(path),name=path.name,sha256=digest(path),preview='data:image/jpeg;base64,'+base64.b64encode(data.getvalue()).decode()))
        return result

    @response
    def paste_images(self):
        from PIL import Image,ImageGrab
        from .image_input import ImageInbox
        inbox=ImageInbox(self._store);content=ImageGrab.grabclipboard()
        if isinstance(content,Image.Image):paths=[inbox.import_bitmap(content)]
        elif isinstance(content,list):paths=[inbox.import_file(p) for p in content[:12]]
        else:raise ValueError('剪贴板中没有截图或图片文件')
        # Return metadata only; JS can request bounded thumbnails separately.
        return dict(paths=list(dict.fromkeys(paths)))

    @response
    def recognize(self,paths,notes=''):
        if not paths or len(paths)>12:raise ValueError('请选择1至12张规则截图')
        for path in paths:
            if not Path(path).resolve().is_relative_to(self._store):raise ValueError('图片尚未导入本地档案库')
        args=['vision-read']
        for path in paths:args+=['--image',path]
        if notes:args+=['--notes',notes[:4000]]
        return self._cli(*args)

    @response
    def open_history(self):
        self._idle()
        if not self._window:raise ValueError('尚未连接桌面窗口')
        import webview
        selected=self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not selected:return dict(cancelled=True)
        root=Path(selected[0]).resolve()
        if not (root/'plan.json').is_file():raise ValueError('所选目录不是采集批次')
        self._run=str(root);self._persist();return dict(run=self._run)

    @response
    def export(self):
        if not self._run or not (Path(self._run)/'audit.json').exists():raise ValueError('请先等待本批次收尾完成')
        if not self._window:raise ValueError('尚未连接桌面窗口')
        import webview
        selected=self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not selected:return dict(cancelled=True)
        out=Path(selected[0])/(Path(self._run).name+'-export-'+uuid.uuid4().hex[:6])
        return self._invoke(['export','--run',self._run,'--out',str(out)])

    @response
    def open_local(self,kind):
        path={'records':self._run or str(self._store),'diagnostic':self._diagnostic,'library':str(self._store)}.get(kind)
        if not path:raise ValueError('尚无对应的本地记录')
        os.startfile(path);return dict(opened=True)

    def window_command(self,action):
        """User-operated chrome, independent of a slow discovery/model operation."""
        try:
            if not self._window:raise ValueError('尚未连接桌面窗口')
            if action=='minimize':self._window.minimize()
            elif action=='maximize':
                desired=not self._maximized
                if desired:self._window.maximize()
                else:self._window.restore()
                self._maximized=desired
            elif action=='close':self._window.destroy()
            else:raise ValueError('未知窗口操作')
            return dict(ok=True,data=dict(maximized=self._maximized))
        except Exception as exc:return dict(ok=False,error=dict(message=str(exc)))

    def resize_window(self,width,height):
        try:
            if not self._window:raise ValueError('尚未连接桌面窗口')
            if any(isinstance(n,bool) or not isinstance(n,(int,float)) for n in (width,height)):
                raise ValueError('窗口尺寸无效')
            if not self._maximized:
                self._window.resize(max(960,min(7680,int(width))),max(640,min(4320,int(height))))
            return dict(ok=True,data={})
        except Exception as exc:return dict(ok=False,error=dict(message=str(exc)))

    def _on_maximized(self):self._maximized=True
    def _on_restored(self):self._maximized=False


def main():
    import argparse
    import webview
    api=DesktopAPI()
    parser=argparse.ArgumentParser(description='Slot Capture desktop')
    parser.add_argument('--run',help='Read an existing capture directory without starting it')
    args=parser.parse_args()
    if args.run:
        root=Path(args.run).resolve()
        if not (root/'plan.json').is_file():parser.error('所选目录不是采集批次')
        api._run=str(root);api._persist()
    page=Path(__file__).parent/'webui'/'index.html'
    api._window=webview.create_window('Slot Capture · v'+__version__,url=str(page),js_api=api,
                                      width=1280,height=800,min_size=(960,640),background_color='#EAEAE7',
                                      text_select=True,frameless=True,easy_drag=False)
    api._window.events.maximized+=api._on_maximized
    api._window.events.restored+=api._on_restored
    webview.start(gui='edgechromium',debug=False,private_mode=True)
