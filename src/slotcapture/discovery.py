"""Read-only device discovery. Passive protocol probes own their capture tasks."""
import contextlib
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid
import psutil
from .common import normalize, read, write, utc
from .runtime import ADB
from .android import foreground_candidates, resolve_package_uid, UIDResolutionError, inspect


def adb_candidates(hint=None):
    candidates = [hint, shutil.which('adb')]
    for env in ('ANDROID_HOME', 'ANDROID_SDK_ROOT', 'LOCALAPPDATA'):
        base = os.environ.get(env)
        if base:
            candidates += [str(Path(base)/'platform-tools'/'adb.exe'),
                           str(Path(base)/'Android'/'Sdk'/'platform-tools'/'adb.exe')]
    # Running emulator paths are more reliable than an assumed drive letter.
    for proc in psutil.process_iter(['name', 'exe']):
        try:
            if any(t in (proc.info['name'] or '').lower() for t in ('dnplayer', 'ldplayer', 'ldvbox', 'ld9box', 'nox', 'mumu', 'hd-player', 'memu')):
                parent = Path(proc.info['exe']).parent if proc.info['exe'] else None
                if parent:
                    for base in (parent, parent.parent):
                        candidates += [str(base/name) for name in ('adb.exe', 'adb/adb.exe', 'shell/adb.exe', 'vmonitor/bin/adb.exe')]
        except (psutil.Error, OSError):
            pass
    if os.name == 'nt':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for access in (winreg.KEY_READ | winreg.KEY_WOW64_32KEY, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
                try:
                    with winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\Uninstall', 0, access) as key:
                        for i in range(winreg.QueryInfoKey(key)[0]):
                            try:
                                with winreg.OpenKey(key, winreg.EnumKey(key, i)) as app:
                                    name = str(winreg.QueryValueEx(app, 'DisplayName')[0]).lower()
                                    if any(t in name for t in ('雷电', 'ldplayer', 'mumu', 'nox', '夜神', '逍遥', 'memu', 'bluestacks')):
                                        folder = Path(winreg.QueryValueEx(app, 'InstallLocation')[0])
                                        candidates += [str(folder/p) for p in ('adb.exe', 'shell/adb.exe', 'adb/adb.exe')]
                            except OSError: pass
                except OSError: pass
        for drive in ('C:', 'D:', 'E:', 'F:'):
            for suffix in ('leidian/LDPlayer9', 'leidian/LDPlayer14', 'LDPlayer/LDPlayer9',
                           'Program Files/Netease/MuMuPlayer-12.0/shell', 'Program Files/Nox/bin',
                           'Program Files/BlueStacks_nxt', 'Microvirt/MEmu'):
                candidates.append(str(Path(drive+'/')/suffix/'adb.exe'))
    found = []
    seen = set()
    for candidate in candidates:
        if not candidate: continue
        path = Path(candidate).expanduser()
        key = os.path.normcase(str(path.resolve()))
        if key not in seen and path.is_file():
            seen.add(key); found.append(str(path.resolve()))
    return found


def device_rows(text):
    rows = []
    for line in text.splitlines():
        cols = line.split()
        if len(cols) < 2 or line.startswith(('List of devices', '*')): continue
        serial, state = cols[:2]
        if not re.fullmatch(r'[A-Za-z0-9_.:-]+', serial): continue
        details = dict(c.split(':', 1) for c in cols[2:] if ':' in c)
        rows.append(dict(device=serial, state=state, model=details.get('model', serial).replace('_', ' ')))
    return rows


def foreground_package(text):
    candidates=foreground_candidates(text)
    return candidates[0]['package'] if candidates else None


def ipv4_sockets(text, uid):
    results = {}
    for line in text.splitlines():
        cols = line.split()
        if len(cols) < 8 or cols[3] != '01' or cols[7] != str(uid): continue
        try:
            raw, port = cols[2].split(':')
            if len(raw) == 32:
                # /proc/net/tcp6 writes each 32-bit word in host order.
                packed = b''.join(bytes.fromhex(raw[i:i+8])[::-1] for i in range(0, 32, 8))
                if packed[:12] != b'\0'*10+b'\xff\xff': continue
                octets = packed[12:]
            elif len(raw) == 8:
                octets = bytes.fromhex(raw)[::-1]
            else: continue
            ip = '.'.join(map(str, octets))
            if ip == '0.0.0.0': continue
            results.setdefault(ip, set()).add(int(port, 16))
        except (ValueError, IndexError): continue
    return [dict(ip=ip, ports=sorted(ports)) for ip, ports in sorted(results.items())]


def probe_ip(cap, ip, store):
    """Bounded passive check, using the existing audited ownership/cleanup path."""
    from .cli import init_run
    from .runtime import worker
    root = Path(store)/'discovery'/uuid.uuid4().hex
    plan = normalize(dict(schema_version='slotcapture/v1', run_id=root.name, adapter='mia-bole-v1',
                          auto_bet=True, bet=None, target_paid=1, purpose='connection',
                          capture=dict(cap, game_ip=ip, seconds=12)))
    init_run(root, plan)
    token = uuid.uuid4().hex
    (root/'owner.lock').write_text(token, encoding='utf-8')
    with contextlib.redirect_stdout(io.StringIO()):
        report = worker(root, token)
    return dict(ip=ip, matched=report['state']=='COMPLETE' and bool(report.get('heartbeat_matched')),
                run=str(root.resolve()), error=report.get('error'),
                cleanup_verified=bool(read(root/'manifest.json').get('remote_stopped_verified')))


def _discover(store, adb_hint=None, device=None, package=None, probe=True, trace=None):
    paths = adb_candidates(adb_hint)
    if not paths:
        return dict(stage='adb_missing', message='未找到ADB，请展开连接详情选择模拟器自带的adb.exe。', devices=[])
    rows, diagnostics = [], []
    for path in paths:
        try:
            p = subprocess.run([path, 'devices', '-l'], capture_output=True, timeout=10,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            found = device_rows(p.stdout.decode(errors='replace')) if not p.returncode else []
            rows += [dict(r, adb=path) for r in found]
            if any(r['state']=='device' and (not device or r['device']==device) for r in found): break
        except (OSError, subprocess.TimeoutExpired) as exc:
            diagnostics.append(str(exc))
    online = [r for r in rows if r['state']=='device']
    if device: online = [r for r in online if r['device']==device]
    if not online:
        return dict(stage='device_missing', adb=paths[0], devices=rows,
                    message='未找到在线模拟器，请打开模拟器并确认ADB调试已开启。', diagnostics=diagnostics)
    if len(online)>1:
        return dict(stage='choose_device', devices=online, message='检测到多个在线设备，请选择要采集的模拟器。')
    selected = online[0]
    cap = dict(adb=selected['adb'], device=selected['device'])
    adb = ADB(cap)
    android_user=None
    uid=None
    if not package:
        _,activity,_=inspect(adb,'dumpsys activity activities',trace)
        _,window,_=inspect(adb,'dumpsys window windows',trace)
        candidates=foreground_candidates(activity+'\n'+window)
        uid_errors=[]
        for candidate in candidates:
            target=candidate['package']
            if any(t in target.lower() for t in ('launcher','systemui','settings')):
                package=target; break
            try:
                uid=resolve_package_uid(adb,target,candidate['android_user'],trace)
                package=target;android_user=candidate['android_user'];break
            except UIDResolutionError as exc:
                uid_errors.append(str(exc))
        else:
            if candidates:
                return dict(stage='uid_missing',capture=dict(cap,package=candidates[0]['package']),devices=[selected],
                            message=uid_errors[-1]+' 检测诊断已保存，连接详情可查看识别结果。')
    if not package or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]+',package):
        return dict(stage='package_missing', **cap, devices=[selected], message='未识别到前台应用，请把目标游戏切到前台。')
    if any(t in package.lower() for t in ('launcher', 'systemui', 'settings')):
        return dict(stage='package_missing', **cap, devices=[selected], message='当前前台是系统或桌面，请打开目标游戏。')
    cap['package']=package
    if android_user is not None: cap['android_user']=android_user
    try:
        if uid is None: uid=resolve_package_uid(adb,package,android_user,trace)
    except UIDResolutionError as exc:
        return dict(stage='uid_missing',capture=cap,devices=[selected],message=str(exc)+' 检测诊断已保存。')
    sockets = []
    for file in ('tcp','tcp6'):
        result = adb.run('shell',f"su -c 'cat /proc/net/{file}'",check=False)
        if result.returncode==0: sockets += ipv4_sockets(result.stdout.decode(errors='replace'),uid)
    merged = {}
    for s in sockets: merged.setdefault(s['ip'],set()).update(s['ports'])
    candidates = [dict(ip=ip,ports=sorted(ports)) for ip,ports in sorted(merged.items())]
    base = dict(capture=cap, devices=[selected], uid=uid, candidates=candidates,
                scope='foreground application; machine identity still caller-selected')
    if not candidates:
        return dict(stage='server_missing', **base, message='前台应用暂无可用IPv4连接，请等游戏加载完成后重新检测。')
    probes=[]
    if probe and len(candidates)<=8:
        for candidate in candidates:
            try:
                result=probe_ip(cap,candidate['ip'],store)
                probes.append(result)
                if not result['cleanup_verified']:
                    return dict(stage='probe_cleanup_required', **base, probes=probes,
                                message='候选探测进程未确认收尾，请检查记录；未继续探测其他连接。')
            except Exception as exc:
                # Unexpected probe startup failures must not silently launch another capture.
                return dict(stage='probe_failed', **base, probes=probes, message=str(exc))
    matched = [p['ip'] for p in probes if p['matched']]
    if len(matched)==1:
        return dict(stage='detected', **dict(base,capture=dict(cap,game_ip=matched[0])), probes=probes,
                    message='已自动识别设备、前台应用及游戏心跳连接。')
    if len(candidates)==1:
        return dict(stage='candidate', **dict(base,capture=dict(cap,game_ip=candidates[0]['ip'])),probes=probes,
                    message='已读取唯一候选连接，将继续执行正式心跳核验。')
    choices = [c for c in candidates if not matched or c['ip'] in matched]
    return dict(stage='choose_server', **dict(base,candidates=choices),probes=probes,
                message='发现多个候选连接，请选择后核验。' if not matched else '多个连接匹配心跳，请选择要采集的连接。')


def discover(store, adb_hint=None, device=None, package=None, probe=True):
    trace=[]
    try:
        result=_discover(store,adb_hint,device,package,probe,trace)
    except Exception as exc:
        result=dict(stage='discovery_failed',message='自动检测未完成：'+str(exc))
    path=Path(store)/'diagnostics'/(uuid.uuid4().hex+'.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    write(path,dict(checked_utc=utc(),result=result,inspection=trace,no_game_action_sent=True))
    return dict(result,diagnostic_path=str(path.resolve()))
