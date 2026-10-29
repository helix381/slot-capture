import contextlib
import datetime as dt
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import psutil
from .common import read, write, utc, normalize, Journal, emit_analysis, digest, TERMINAL, jsonl, positive
from .audit import analyze, compact
from .vendor.slot_capture import Recorder, complete_packets

def process_identity(pid):
    try:
        p = psutil.Process(pid)
        return dict(pid=pid, created=p.create_time(), argv=p.cmdline())
    except (psutil.Error, OSError):
        return None

def is_owned(identity):
    if not identity:
        return False
    current = process_identity(identity['pid'])
    return bool(current and current['created'] == identity['created'] and current['argv'] == identity['argv'])

class ADB:
    def __init__(self, cap):
        self.cap = cap
        self.argv = [cap['adb'], '-s', cap['device']]

    def run(self, *args, timeout=15, check=True):
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        p = subprocess.run(self.argv + list(args), capture_output=True, timeout=timeout, creationflags=flags)
        if check and p.returncode:
            raise RuntimeError('ADB command failed: ' + p.stderr.decode(errors='replace')[:300])
        return p

    def remote_identity(self, pid):
        if not isinstance(pid, int) or pid <= 0:
            raise ValueError('invalid remote PID')
        p = self.run('shell', f"su -c 'cat /proc/{pid}/cmdline'")
        return [v.decode(errors='replace') for v in p.stdout.split(b'\0') if v]

    def stop_owned(self, pid, argv):
        current = self.remote_identity(pid)
        if current != argv:
            raise RuntimeError('Remote PID command changed; refusing to signal it')
        self.run('shell', f"su -c 'kill -2 {pid}'")


class SocketUnavailableError(RuntimeError):
    code='socket_unavailable'

def doctor(cap):
    for k in ['adb', 'device', 'game_ip', 'package']:
        if not cap.get(k):
            raise ValueError('Live capture requires capture.' + k)
    adb = ADB(cap)
    state = adb.run('get-state').stdout.decode().strip()
    if state != 'device':
        raise RuntimeError('Configured device is not online: ' + state)
    from .android import resolve_package_uid
    uid=resolve_package_uid(adb,cap['package'],cap.get('android_user'))
    from .discovery import ipv4_sockets
    sockets = []
    for filename in ('tcp', 'tcp6'):
        result = adb.run('shell', f"su -c 'cat /proc/net/{filename}'", check=False)
        if result.returncode == 0:
            for item in ipv4_sockets(result.stdout.decode(errors='replace'), uid):
                if item['ip'] == cap['game_ip']:
                    sockets += [dict(ip=item['ip'],port=p,state='01') for p in item['ports']]
    if not sockets:
        raise SocketUnavailableError('候选连接已断开或变化，尚未启动抓包')
    tool = adb.run('shell', f"su -c 'command -v {cap['tcpdump']}'").stdout.decode(errors='replace').strip()
    if not tool:
        raise RuntimeError('tcpdump not available under existing permissions')
    return dict(device=cap['device'], package=cap['package'], uid=uid,
                confirmed_sockets=sockets, tcpdump=tool,
                machine_identity='Caller-selected Mia adapter; socket UID does not prove the current machine.',
                no_game_action_sent=True)

def status(root):
    root = Path(root)
    s = read(root / 'status.json')
    m = read(root / 'manifest.json') if (root / 'manifest.json').exists() else {}
    alive = is_owned(m.get('worker'))
    age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(s['checked_utc'])).total_seconds()
    live = alive and age < 20 and s['state'] not in TERMINAL
    return dict(s, worker_alive=alive, status_age_seconds=round(age, 2), live=live,
                health='terminal' if s['state'] in TERMINAL else 'online' if live else 'stale_or_process_missing')

def start(root):
    root = Path(root).resolve()
    p = normalize(read(root / 'plan.json'))
    s = status(root)
    if s['state'] != 'PREPARED':
        if s['live']:
            return dict(already_running=True, **s)
        raise ValueError('Use a fresh run; an existing run is never silently restarted or overwritten')
    doctor(p['capture'])
    lock = root / 'owner.lock'
    token = os.urandom(16).hex()
    with lock.open('x', encoding='utf-8') as f:
        f.write(token)
    argv = ([sys.executable] if getattr(sys, 'frozen', False)
            else [sys.executable, '-m', 'slotcapture.cli'])
    argv += ['_worker', '--run', str(root), '--owner-token', token]
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
    with (root / 'worker.log').open('ab', buffering=0) as log:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 creationflags=flags, start_new_session=os.name != 'nt')
    write(root / 'status.json', dict(state='STARTING', checked_utc=utc(), paid=0, free=0,
                                   run_id=p['run_id'], target_paid=p['target_paid']))
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError('Worker stopped during startup; inspect status.json and worker.log')
        s = status(root)
        if s['state'] != 'STARTING':
            return s
        time.sleep(0.2)
    return status(root)

def decode_snapshot(recorder, data, offset):
    if len(data) < 24:
        return offset
    if len(data) < offset:
        raise RuntimeError('Raw capture shrank; refusing to hide a capture gap')
    chunk = data[:24] + data[offset:]
    consumed = offset
    for ts, packet, linktype in complete_packets(chunk):
        recorder.packet(ts, packet, linktype)
        consumed += 16 + len(packet)
    return consumed


def session_report(root, plan, quality, final=False):
    """Discover the batch Bet once, then use the existing fixed-Bet audit."""
    if plan.get('purpose') != 'connection' and plan.get('bet') is None:
        rows = jsonl(Path(root)/'messages.jsonl.spins.jsonl')
        first = next((s for s in rows if s.get('matched_request') and s.get('mode') == 'base'), None)
        if first:
            positive(first.get('bet'), 'detected bet')
            plan['bet'] = first['bet']
            plan['max_wager'] = plan['bet'] * plan['target_paid']
            plan['bet_detected_utc'] = utc()
            write(Path(root)/'plan.json', plan)
    if plan.get('purpose') != 'connection' and plan.get('bet') is not None:
        return analyze(root, plan, quality, final=final)
    heartbeats = quality.get('heartbeats', {})
    matched = quality.get('heartbeat_pairs', 0) > 0
    issues = []
    for key in ('errors', 'incomplete_streams') if final else ('errors',):
        if quality.get(key):
            issues.append(dict(kind=key, count=quality[key]))
    good = bool(matched) and not issues and plan.get('purpose') == 'connection'
    return dict(adapter=plan['adapter'], paid=0, free=0, target_paid=plan['target_paid'],
                paid_wager=0, expected_return=0, return_excluding_beer_final=0,
                pending_requests=quality.get('pending_spin_requests', 0), pending_rewards=0,
                pending_reassembly=quality.get('incomplete_streams', 0), heartbeat_matched=bool(matched),
                heartbeat_counts=heartbeats, heartbeat_pairs=quality.get('heartbeat_pairs', 0),
                tail_complete=good, complete=good,
                cycles=[], features=[], issues=issues)

def worker(root, token):
    root = Path(root).resolve()
    if (root / 'owner.lock').read_text(encoding='utf-8') != token:
        raise ValueError('Invalid worker ownership token')
    plan = normalize(read(root / 'plan.json'))
    cap = plan['capture']
    adb = ADB(cap)
    prefix = '/data/local/tmp/slotcap-' + token
    journal = Journal(root)
    manifest = dict(run_id=plan['run_id'], created_utc=utc(), worker=process_identity(os.getpid()),
                    remote_prefix=prefix, remote_pid=None, remote_argv=None,
                    capture_launch_attempted=False,
                    device=cap['device'], no_game_action_sent=True)
    write(root / 'manifest.json', manifest)
    rec = None
    report = None
    child = None
    log = None
    final_state = 'FAILED'
    failure = None
    failure_code = None
    deadline = time.monotonic() + cap['seconds']
    drain_started = None
    last_activity = time.monotonic()
    old_state = None
    offset = 24
    raw_bytes = b''
    recorded_before = 0
    try:
        doctor(cap)
        command = (f"su -c 'echo $$ > {prefix}.pid; exec {cap['tcpdump']} -i any -s 0 -U "
                   f"-w {prefix}.pcap tcp and host {cap['game_ip']}'")
        log = (root / 'tcpdump.log').open('ab', buffering=0)
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        manifest['capture_launch_attempted']=True
        write(root/'manifest.json',manifest)
        child = subprocess.Popen(adb.argv + ['shell', command], stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log, creationflags=flags)
        manifest['adb_host'] = process_identity(child.pid)
        write(root / 'manifest.json', manifest)
        for attempt in range(15):
            if child.poll() is not None:
                raise RuntimeError('tcpdump exited during startup')
            result = adb.run('shell', 'cat ' + prefix + '.pid', check=False)
            try:
                pid = int(result.stdout.strip())
                remote_argv = adb.remote_identity(pid)
                if cap['game_ip'] in remote_argv and prefix+'.pcap' in remote_argv:
                    manifest.update(remote_pid=pid, remote_argv=remote_argv)
                    write(root / 'manifest.json', manifest)
                    break
            except (ValueError, RuntimeError):
                pass
            time.sleep(0.2)
        if not manifest['remote_pid']:
            raise RuntimeError('Cannot verify task-owned tcpdump process')
        legacy = dict(plan_id=plan['run_id'], run_id=plan['run_id'],
                      conditions={'fixed_bet': plan['bet']}, sample={'target_paid_spins': plan['target_paid']})
        rec = Recorder(root / 'messages.jsonl', cap['game_ip'], legacy)
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError('Capture transport exited')
            if adb.remote_identity(manifest['remote_pid']) != manifest['remote_argv']:
                raise RuntimeError('Remote process identity no longer matches')
            data = adb.run('exec-out', 'cat', prefix+'.pcap', timeout=20).stdout
            if len(data) > cap['max_raw_bytes']:
                raise RuntimeError('Raw size limit exceeded; pause new paid spins')
            if len(data) != len(raw_bytes):
                offset = decode_snapshot(rec, data, offset)
                raw_bytes = data
                (root / 'raw.pcap').write_bytes(data)
            quality = rec.summary()
            write(root / 'messages.jsonl.status.json', dict(quality, checked_time_utc=utc()))
            report = session_report(root, plan, quality)
            if rec.recorded != recorded_before:
                last_activity = time.monotonic()
                recorded_before = rec.recorded
            emit_analysis(journal, report)
            stop = read(root / 'control.json') if (root / 'control.json').exists() else {}
            if stop.get('force'):
                final_state = 'STOPPED_INCOMPLETE'
                break
            if report['issues']:
                raise RuntimeError('Deterministic validation failed; pause new paid spins and inspect issues')
            if plan.get('purpose') == 'connection' and report['complete']:
                journal.emit('heartbeat_verified', {'heartbeats': quality.get('heartbeats'), 'scope': 'configured_app_server'})
                final_state = 'COMPLETE'
                break
            draining = report['paid'] >= plan['target_paid'] or bool(stop) or bool(report.get('net_loss_stop_at_paid'))
            if draining and drain_started is None:
                drain_started = time.monotonic()
            state = ('CHECKING_CONNECTION' if plan.get('purpose') == 'connection' else
                     'DRAINING' if draining else 'CAPTURING' if report['paid'] else 'WAITING_FOR_SPIN')
            if state != old_state:
                journal.emit('state_changed', {'state': state}, key='state:'+state)
                old_state = state
            write(root / 'status.json', dict(state=state, checked_utc=utc(), run_id=plan['run_id'],
                  **compact(report), event_cursor=journal.cursor,
                  detected_bet=plan.get('bet'), purpose=plan.get('purpose', 'sampling'),
                  ready_level='matched_spin_verified' if report['paid'] else 'transport_started_no_spin_verified'))
            if draining and report['tail_complete'] and time.monotonic()-last_activity >= cap['quiet_seconds']:
                final_state = 'COMPLETE' if report['complete'] else 'STOPPED_EARLY'
                break
            if draining and time.monotonic()-drain_started > cap['tail_seconds']:
                raise RuntimeError('Reward tail timeout; preserve incomplete evidence and pause paid spins')
            time.sleep(cap['poll_seconds'])
        else:
            failure = '等待游戏心跳超时' if plan.get('purpose')=='connection' else 'Capture duration expired; pause new paid spins'
            failure_code = 'heartbeat_timeout' if plan.get('purpose')=='connection' else 'capture_timeout'
            final_state = 'STOPPED_INCOMPLETE'
    except Exception as exc:
        failure = str(exc)
        failure_code = getattr(exc,'code','capture_failed')
        final_state = 'STOPPED_INCOMPLETE'
        journal.emit('capture_failed', {'error': failure, 'action': 'Pause new paid spins.'}, severity='critical')
    finally:
        try:
            if manifest.get('remote_pid'):
                adb.stop_owned(manifest['remote_pid'], manifest['remote_argv'])
                # SIGINT flushes libpcap. Wait for this exact process to leave before final pull.
                remote_stopped = False
                for _ in range(25):
                    result = adb.run('shell', f"su -c 'test -e /proc/{manifest['remote_pid']}/cmdline'", check=False)
                    if result.returncode:
                        remote_stopped = True
                        break
                    time.sleep(0.2)
                if not remote_stopped:
                    raise RuntimeError('Owned tcpdump did not exit after SIGINT; capture is not confirmed stopped')
                manifest['remote_stopped_verified'] = True
                data = adb.run('exec-out', 'cat', prefix+'.pcap', timeout=30).stdout
                (root / 'raw-final.pcap').write_bytes(data)
                list(complete_packets(data, strict=True))
                if rec:
                    offset = decode_snapshot(rec, data, offset)
                    quality = rec.summary()
                    report = session_report(root, plan, quality, final=True)
                    write(root/'messages.jsonl.status.json', dict(quality, checked_time_utc=utc()))
                manifest.update(raw_sha256=digest(root/'raw-final.pcap'), raw_bytes=len(data), stopped_utc=utc())
                if final_state == 'COMPLETE' and not report['complete']:
                    final_state = 'STOPPED_INCOMPLETE'
                    failure = 'Final archive audit differs from provisional completion'
            elif child:
                failure = (failure or '') + '; remote capture PID not verified; inspect task prefix before manual cleanup'
        except Exception as exc:
            failure = (failure or '') + '; archive/ownership check: ' + str(exc)
            failure_code = 'archive_failed'
            final_state = 'STOPPED_INCOMPLETE'
        if rec:
            rec.out.close()
            rec.spins.close()
        if child and is_owned(manifest.get('adb_host')):
            # Never terminate a shared ADB server or an unverified PID.
            psutil.Process(child.pid).terminate()
        if log:
            log.close()
        if report:
            write(root/'audit.json', report)
            write(root/'paid-cycles.json', report['cycles'])
            write(root/'feature-events.json', report['features'])
            emit_analysis(journal, report)
            if plan.get('purpose') == 'first_spin':
                from .workflow import record_verification
                try:
                    record_verification(root, plan, report, final_state)
                except Exception as exc:
                    failure = '首转档案更新失败: ' + str(exc)
                    final_state = 'STOPPED_INCOMPLETE'
        write(root/'manifest.json', manifest)
        journal.emit('capture_finished', {'state': final_state, 'error': failure}, severity='info' if final_state=='COMPLETE' else 'action_required')
        final = dict(state=final_state, checked_utc=utc(), run_id=plan['run_id'],
                     detected_bet=plan.get('bet'), purpose=plan.get('purpose', 'sampling'),
                     event_cursor=journal.cursor, error=failure, **(compact(report) if report else {}))
        final['failure_code']=failure_code
        write(root/'status.json', final)
        (root/'owner.lock').unlink(missing_ok=True)
    return final
