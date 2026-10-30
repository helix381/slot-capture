"""Machine library and evidence-backed onboarding, independent of GUI widgets."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import uuid
from .common import read, write, utc, normalize, digest


def default_store():
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
    versioned=bool(re.fullmatch(r'v\d+\.\d+\.\d+', base.name))
    if getattr(sys, 'frozen', False) and (versioned or base.name=='dist'):
        distribution=base.parent if versioned else base
        shared = distribution/'data'
        if shared.exists():
            return shared
        # Reuse an existing library in place; do not silently copy large raw captures.
        legacy=sorted(distribution.glob('v*/data'), reverse=True)
        for old in legacy:
            if next((old/'machines').glob('*.json'), None) is not None:
                return old
        for old in legacy:
            if (old/'trash'/'machines').is_dir():return old
        return shared
    return base / 'data'


def profile_path(store, identity):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', identity):
        raise ValueError('Invalid machine ID')
    return Path(store) / 'machines' / (identity + '.json')


def revision(profile):
    content = {k: profile.get(k) for k in ('name', 'adapter', 'symbols', 'features', 'notes', 'rules_confirmed')}
    if 'rule_details' in profile:content['rule_details']=profile['rule_details']
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def save_profile(store, data):
    p = dict(data)
    p.pop('first_spin_verified', None)
    identity = p.get('id') or uuid.uuid4().hex[:16]
    path = profile_path(store, identity)
    old = read(path) if path.exists() else {}
    if not isinstance(p.get('name'), str) or not p['name'].strip():
        raise ValueError('请填写机台名称')
    if p.get('adapter') not in ('mia-bole-v1', 'unadapted'):
        raise ValueError('未知适配器')
    for key in ('symbols', 'features'):
        if not isinstance(p.get(key), list):
            raise ValueError(key + ' must be a list')
        for item in p[key]:
            if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name'].strip():
                raise ValueError(key + '中每行必须有名称')
    if not isinstance(p.get('rules_confirmed', False), bool):
        raise ValueError('rules_confirmed must be boolean')
    cap = normalize(dict(schema_version='slotcapture/v1', adapter='mia-bole-v1', bet=None,
                         auto_bet=True, target_paid=1, capture=p.get('capture', {})))['capture']
    p.update(id=identity, capture=cap, schema_version='slot-machine/v1', updated_utc=utc(),
             created_utc=old.get('created_utc', utc()))
    p['revision'] = revision(p)
    # A client cannot manufacture a passed first-spin check through profile-save.
    p.pop('verification', None)
    if (old.get('revision') == p['revision'] and old.get('capture', {}).get('package') == cap.get('package')
            and old.get('verification')):
        p['verification'] = old['verification']
    path.parent.mkdir(parents=True, exist_ok=True)
    if p.get('screenshots'):
        p['screenshots'] = archive_images(store, identity, p['screenshots'])
    write(path, p)
    return dict(p, first_spin_verified=verified(p))


def load_profile(store, identity):
    return read(profile_path(store, identity))


def list_profiles(store):
    directory = Path(store) / 'machines'
    return [read(path) for path in sorted(directory.glob('*.json'))] if directory.exists() else []


def delete_profile(store, identity):
    """Move only the profile to recoverable storage; leave all run evidence intact."""
    source=profile_path(store,identity)
    profile=read(source)
    from .runtime import status
    runs=list((Path(store)/'runs'/identity).glob('*/plan.json'))
    index=Path(store)/'machine-runs'/(identity+'.json')
    if index.exists():
        runs += [Path(run)/'plan.json' for run in read(index)['runs']]
    if profile.get('verification',{}).get('run'):
        runs.append(Path(profile['verification']['run'])/'plan.json')
    for plan_path in runs:
        run=plan_path.parent
        if not (run/'status.json').exists():continue
        state=status(run)
        if state.get('worker_alive') or state['state']=='STARTING':
            raise ValueError('该机台仍有采集任务，请先等待任务结束再删除档案')
    token=uuid.uuid4().hex
    destination=Path(store)/'trash'/'machines'/identity/(token+'.json')
    destination.parent.mkdir(parents=True,exist_ok=True)
    source.rename(destination)
    return dict(id=identity,name=profile['name'],deleted=token,archived_path=str(destination.resolve()),
                history_preserved=True)


def restore_profile(store, identity, deleted):
    destination=profile_path(store,identity)
    if not re.fullmatch(r'[a-f0-9]{32}',deleted):raise ValueError('Invalid deletion token')
    source=Path(store)/'trash'/'machines'/identity/(deleted+'.json')
    if destination.exists():raise FileExistsError('同ID档案已存在，不会覆盖；请先检查当前档案')
    profile=read(source)
    if profile.get('id')!=identity:raise ValueError('回收档案ID不一致')
    destination.parent.mkdir(parents=True,exist_ok=True)
    source.rename(destination)
    return dict(profile,first_spin_verified=verified(profile))


def verified(profile):
    v = profile.get('verification') or {}
    if v.get('revision') != revision(profile):
        return False
    try:
        root = Path(v['run'])
        plan, audit = read(root/'plan.json'), read(root/'audit.json')
        return (v['audit_sha256'] == digest(root/'audit.json') and audit['complete'] and
                plan.get('purpose') == 'first_spin' and plan.get('machine_revision') == profile['revision'] and
                plan.get('machine_id') == profile['id'] and
                plan['capture'].get('package') == profile['capture'].get('package'))
    except (OSError, ValueError, KeyError):
        return False


def connection_ready(profile, run):
    try:
        root = Path(run)
        plan, s = read(root/'plan.json'), read(root/'status.json')
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(s['checked_utc'])).total_seconds()
        return (s['state'] == 'COMPLETE' and s.get('heartbeat_matched') is True and 0 <= age < 600 and
                plan.get('purpose') == 'connection' and plan.get('machine_id') == profile['id'] and
                plan['capture'] == dict(profile['capture'], seconds=60))
    except (OSError, KeyError, ValueError, TypeError):
        return False


def prepare_session(store, identity, purpose, run, target=1, connection=None):
    from .cli import init_run
    p = load_profile(store, identity)
    if p['adapter'] != 'mia-bole-v1':
        raise ValueError('档案已保存，但该机台尚无采集适配器')
    if purpose not in ('connection', 'first_spin', 'sampling'):
        raise ValueError('Unsupported purpose')
    if purpose != 'connection':
        if not p.get('rules_confirmed') or not p['symbols']:
            raise ValueError('请先确认规则与Symbol清单')
        if not connection or not connection_ready(p, connection):
            raise ValueError('请先重新检测环境与双向游戏心跳（10分钟内有效）')
    if purpose == 'sampling' and not verified(p):
        raise ValueError('该规则版本尚未通过完整首转审计')
    cap = dict(p['capture'])
    if purpose == 'connection':
        cap['seconds'] = 60
    plan = dict(schema_version='slotcapture/v1', run_id=Path(run).name, adapter=p['adapter'],
                auto_bet=True, bet=None, target_paid=target if purpose == 'sampling' else 1,
                purpose=purpose, capture=cap, machine_id=identity, machine_revision=p['revision'],
                machine_store=str(Path(store).resolve()), connection_run=str(connection) if connection else None)
    root = init_run(run, plan)
    # Snapshot the human-approved labels so later edits never rewrite old results.
    write(root/'machine.json', p)
    index=Path(store)/'machine-runs'/(identity+'.json')
    index.parent.mkdir(parents=True,exist_ok=True)
    runs=read(index)['runs'] if index.exists() else []
    if str(root) not in runs:runs.append(str(root))
    write(index,dict(runs=runs))
    return dict(run=str(root), purpose=purpose, state='PREPARED', bet=None)


def record_verification(root, plan, report, final_state):
    if plan.get('purpose') != 'first_spin' or final_state != 'COMPLETE' or not report.get('complete'):
        return
    path = profile_path(plan['machine_store'], plan['machine_id'])
    p = read(path)
    if revision(p) != plan['machine_revision'] or p['capture'].get('package') != plan['capture'].get('package'):
        return
    p['verification'] = dict(run=str(Path(root).resolve()), passed_utc=utc(), revision=p['revision'],
                             audit_sha256=digest(Path(root)/'audit.json'), detected_bet=plan['bet'])
    write(path, p)


def archive_images(store, identity, images):
    folder = Path(store) / 'rule-images' / identity
    profile_path(store, identity)  # validate ID before constructing writable targets
    folder.mkdir(parents=True, exist_ok=True)
    result = []
    for name in images:
        src = Path(name)
        if src.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp') or src.stat().st_size > 20*1024*1024:
            raise ValueError('截图仅支持20MiB以内PNG/JPEG/WebP')
        dest = folder / (digest(src)[:20] + src.suffix.lower())
        if not dest.exists():
            shutil.copyfile(src, dest)
        result.append(str(dest.resolve()))
    return result
