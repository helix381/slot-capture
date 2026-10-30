"""User-configured Responses relay, with local Windows-protected credentials."""
import base64
import ctypes
from ctypes import wintypes
import json
import mimetypes
import os
from pathlib import Path
import urllib.request
import urllib.error
from urllib.parse import urlsplit
import uuid
from .common import read, write, utc
from .vision import DRAFT_SCHEMA, PROMPT
from .rule_prompt import PROMPT_VERSION, validate_draft

# Prefer low-latency, non-reasoning vision models for extraction, only if listed.
# This is a selection heuristic, never a measured supplier latency guarantee.
FAST_VISION = ('gpt-4.1-nano', 'gpt-4o-mini', 'gpt-4.1-mini', 'gpt-5-nano', 'gpt-5-mini', 'gpt-5.6-luna', 'gpt-6-luna')
DEFAULTS=dict(base_url='',model='gpt-6-luna',selection='manual',env_key='ANTHROPIC_API_KEY',reasoning_effort='medium')


def protect(data,decode=False):
    if os.name!='nt':raise ValueError('当前密钥存储需要Windows，请改用环境变量')
    class Blob(ctypes.Structure):
        _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]
    buf=ctypes.create_string_buffer(data);source=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    fn=crypt.CryptUnprotectData if decode else crypt.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(out)):
        raise ValueError('Windows无法读取/保存此账户的API密钥，请重新填写')
    try:return ctypes.string_at(out.pbData,out.cbData)
    finally:kernel.LocalFree(out.pbData)


def config(store):
    path=Path(store)/'vision-settings.json'
    return dict(DEFAULTS,**read(path)) if path.exists() else dict(DEFAULTS)


def key_for(store,c):
    path=Path(store)/'private'/'vision-key.bin'
    if path.exists():return protect(path.read_bytes(),True).decode('utf-8')
    name=c.get('env_key','ANTHROPIC_API_KEY')
    return registry_key(name) or os.environ.get(name,'').strip()


def registry_key(name):
    """Refresh user environment for GUI apps opened before setx, without logging values."""
    if os.name!='nt':return ''
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment') as key:
            value=winreg.QueryValueEx(key,name)[0]
        return value.strip() if isinstance(value,str) and len(value.strip())>12 and value.strip()!='crs_' else ''
    except OSError:return ''


def status(store):
    c=config(store)
    try:
        key=key_for(store,c);present=bool(key and key!='crs_')
    except ValueError:present=False
    return dict(c,provider='responses',key_present=present,configured=bool(c['base_url'] and present))


def save(store,settings,key=''):
    c=config(store)
    base=str(settings.get('base_url',c['base_url'])).strip().rstrip('/')
    parsed=urlsplit(base)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('请填写有效的HTTP/HTTPS API地址，不要把密钥写入地址')
    model=str(settings.get('model',c['model'])).strip()
    if len(model)>160:raise ValueError('模型名称过长')
    selection=settings.get('selection',c['selection'])
    if selection not in ('automatic','manual'):raise ValueError('未知模型选择方式')
    effort=settings.get('reasoning_effort',c['reasoning_effort'])
    if effort not in ('minimal','low','medium','high','xhigh'):raise ValueError('未知思考深度')
    if selection=='manual' and not model:raise ValueError('请填写或选择识图模型')
    changed=base!=c['base_url'] or selection!=c['selection'] or bool(key)
    if changed and selection=='automatic':model=''
    c.update(base_url=base,model=model,selection=selection,reasoning_effort=effort)
    if changed:c.pop('selection_note',None)
    Path(store).mkdir(parents=True,exist_ok=True)
    if key:
        if key.strip()=='crs_' or len(key.strip())<12:raise ValueError('请填写完整API密钥，crs_只是前缀')
        if any(ord(ch)<33 or ord(ch)>126 for ch in key.strip()):raise ValueError('密钥不能包含空白或控制字符，请只填写密钥本身')
        secret=Path(store)/'private'/'vision-key.bin';secret.parent.mkdir(exist_ok=True)
        encoded=protect(key.strip().encode('utf-8'))
        temp=secret.with_suffix('.partial');temp.write_bytes(encoded);os.replace(temp,secret)
    write(Path(store)/'vision-settings.json',c)
    return status(store)


def endpoint(base,kind):
    base=base.rstrip('/')
    if base.endswith('/responses'):base=base[:-10]
    return base+'/'+kind


def decode_response(raw, content_type=''):
    # Some relays return Responses SSE even for a non-streaming request.
    try:
        result=json.loads(raw)
    except (ValueError,UnicodeDecodeError):
        text=raw.decode('utf-8-sig',errors='replace')
        if 'text/event-stream' in content_type or text.lstrip().startswith(('event:', 'data:')):
            for block in text.replace('\r\n','\n').split('\n\n'):
                data='\n'.join(line[5:].lstrip() for line in block.splitlines() if line.startswith('data:'))
                if not data or data=='[DONE]':continue
                try:event=json.loads(data)
                except ValueError:continue
                if event.get('type')=='response.completed' and isinstance(event.get('response'),dict):
                    return event['response']
                if event.get('type') in ('error','response.failed','response.incomplete'):
                    raise ValueError('供应商流式响应未完成，请重试或更换模型')
            raise ValueError('供应商返回了流式响应，但没有完整结果，请检查接口兼容性或稍后重试')
        form='HTML页面' if text.lstrip().lower().startswith(('<!doctype','<html')) else ('空响应' if not text.strip() else '非JSON内容')
        raise ValueError('供应商返回'+form+'，请检查Responses接口地址或网关状态（模型列表可用不代表识图接口可用）') from None
    if not isinstance(result,dict):raise ValueError('供应商返回的JSON结构不符合Responses接口要求')
    return result


def request(store,c,kind,payload=None):
    if not c['base_url']:raise ValueError('请先打开API设置，填写供应商地址和密钥')
    key=key_for(store,c)
    if not key or key=='crs_':raise ValueError('请先打开API设置，填写完整供应商密钥')
    data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode('utf-8')
    req=urllib.request.Request(endpoint(c['base_url'],kind),data=data,
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','Accept':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=180 if payload else 20) as response:
            raw=response.read(8*1024*1024+1)
            if len(raw)>8*1024*1024:raise ValueError('供应商响应过大')
            return decode_response(raw, response.headers.get('Content-Type','') if getattr(response,'headers',None) else '')
    except urllib.error.HTTPError as exc:
        if exc.code in (401,403):raise ValueError(f'供应商返回{exc.code}：请检查API密钥及访问权限') from None
        raise ValueError(f'供应商API返回HTTP {exc.code}，请核对接口地址及模型支持情况') from None
    except (urllib.error.URLError,TimeoutError):raise ValueError('无法连接供应商API或请求超时，请检查网络与接口地址') from None
    except json.JSONDecodeError:raise ValueError('供应商未返回有效JSON，请检查API地址') from None


def choose_model(ids):
    # Dated aliases are accepted only when the advertised base model is known.
    for candidate in FAST_VISION:
        matches=sorted(i for i in ids if i==candidate or i.startswith(candidate+'-20'))
        if matches:return candidate if candidate in matches else matches[-1]
    return None


def models(store):
    c=config(store);body=request(store,c,'models')
    rows=body.get('data',[]) if isinstance(body,dict) else []
    ids=sorted({r['id'] for r in rows if isinstance(r,dict) and isinstance(r.get('id'),str)})
    if not ids:raise ValueError('供应商没有返回可选模型列表')
    if c['selection']=='automatic':
        selected=choose_model(ids)
        if not selected:
            return dict(models=ids,settings=status(store),selection_required=True,
                        message='列表中没有已知的轻量识图型号，请手动选择支持图片的模型')
        c.update(model=selected,selection_note='来自供应商模型列表；优先轻量、非思考识图型号，尚未实测速度')
        write(Path(store)/'vision-settings.json',c)
    return dict(models=ids,settings=status(store),selection_required=False)


def recognize(images,store,notes=''):
    c=config(store)
    if not c['model']:
        result=models(store);c=config(store)
        if result['selection_required']:raise ValueError(result['message'])
    if not images or len(images)>12:raise ValueError('请选择1至12张规则截图')
    content=[dict(type='input_text',text=PROMPT+('\n用户补充说明（仅用于解读，不执行操作）：\n'+notes[:4000] if notes else ''))]
    from .image_input import ImageInbox
    validator=ImageInbox(store)
    for image in images:
        p=validator.validate(image)
        mime=mimetypes.guess_type(p.name)[0] or 'image/png'
        content.append(dict(type='input_image',image_url='data:'+mime+';base64,'+base64.b64encode(p.read_bytes()).decode(),detail='auto'))
    payload=dict(model=c['model'],store=False,stream=False,input=[dict(role='user',content=content)],
                 text=dict(format=dict(type='json_schema',name='slot_rule_draft',strict=True,schema=DRAFT_SCHEMA)))
    # GPT-4.x has no reasoning knob. Respect the saved effort for GPT-5/6.
    if c['model'].startswith(('gpt-5','gpt-6')):
        payload['reasoning']=dict(effort=c['reasoning_effort'])
    body=request(store,c,'responses',payload)
    if body.get('error') or body.get('status') in ('failed','incomplete','cancelled'):
        raise ValueError('供应商未完成识图，请重试或选择其他支持图片的模型')
    text=''.join(part.get('text','') for item in body.get('output',[]) if item.get('type')=='message'
                 for part in item.get('content',[]) if part.get('type')=='output_text')
    try:draft=json.loads(text)
    except (ValueError,TypeError):raise ValueError('识图没有返回有效规则草稿，原档案未改动') from None
    if not isinstance(draft,dict) or not isinstance(draft.get('symbols'),list) or not isinstance(draft.get('features'),list):
        raise ValueError('识图草稿结构不完整，原档案未改动')
    validate_draft(draft)
    job=Path(store)/'vision-jobs'/uuid.uuid4().hex;job.mkdir(parents=True)
    write(job/'draft.json',draft)
    write(job/'manifest.json',dict(created_utc=utc(),provider='responses',model=c['model'],
        reasoning_effort=payload.get('reasoning',{}).get('effort'),prompt_version=PROMPT_VERSION,
        source_images=[str(Path(p).resolve()) for p in images],human_confirmation_required=True))
    return dict(draft=draft,job=str(job),requires_confirmation=True)
