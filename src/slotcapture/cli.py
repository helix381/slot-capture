import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import time
from . import __version__
from .common import normalize, read, write, utc, Journal, emit_analysis, jsonl, digest, STATES, TERMINAL
from .audit import analyze, compact
from .runtime import start, status, doctor, worker, decode_snapshot, session_report
from .vendor.slot_capture import Recorder, complete_packets

def output(data, ok=True, code=None):
    print(json.dumps(dict(ok=ok, api_version=1, data=data) if ok else
                     dict(ok=False, api_version=1, error=dict(code=code, message=str(data))),
                     ensure_ascii=False, allow_nan=False), flush=True)

def make_plan(args):
    cap = dict(adb=args.adb, device=args.device, game_ip=args.game_ip, package=args.package,
               seconds=args.seconds, tail_seconds=args.tail_seconds)
    return normalize(dict(schema_version='slotcapture/v1', run_id=Path(args.run).name,
                          adapter='mia-bole-v1', bet=args.bet, target_paid=args.paid,
                          max_wager=args.bet*args.paid, max_net_loss=args.max_net_loss,capture=cap))

def init_run(path, plan, mode='live'):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=False)
    plan = normalize(plan)
    write(path/'plan.json', plan)
    write(path/'status.json', dict(state='PREPARED', checked_utc=utc(), mode=mode,
                                 paid=0, free=0, target_paid=plan['target_paid'], run_id=plan['run_id']))
    Journal(path).emit('run_prepared', {'mode': mode, 'bet': plan['bet'], 'target_paid': plan['target_paid']})
    return path

def replay(args):
    plan = normalize(read(args.plan))
    plan['run_id'] = Path(args.out).name
    root = init_run(args.out, plan, 'offline_replay')
    quality = None
    if args.input_run:
        source = Path(args.input_run).resolve()
        for filename in ['messages.jsonl', 'messages.jsonl.spins.jsonl']:
            shutil.copyfile(source/filename, root/filename)
        status_file = source/'messages.jsonl.status.json'
        if status_file.exists():
            quality = read(status_file)
        manifest = dict(source_type='decoded_messages', source=str(source),
                        source_sha256={f: digest(source/f) for f in ['messages.jsonl', 'messages.jsonl.spins.jsonl']})
    else:
        if not args.game_ip:
            raise ValueError('PCAP replay requires --game-ip')
        legacy = dict(plan_id=plan['run_id'], run_id=plan['run_id'],
                      conditions={'fixed_bet': plan['bet']}, sample={'target_paid_spins': plan['target_paid']})
        rec = Recorder(root/'messages.jsonl', args.game_ip, legacy)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                for ts, packet, link in complete_packets(Path(args.pcap).read_bytes(), strict=True):
                    try:
                        rec.packet(ts, packet, link)
                    except Exception as exc:
                        rec.errors += 1
                        rec.event({'kind': 'packet_error', 'error': str(exc), 'timestamp': ts})
            quality = rec.summary()
        except Exception as exc:
            rec.errors += 1
            rec.event({'kind': 'capture_error', 'error': str(exc)})
            quality = rec.summary()
        finally:
            rec.out.close()
            rec.spins.close()
        manifest = dict(source_type='pcap', source=str(Path(args.pcap).resolve()), source_sha256=digest(args.pcap))
    report = analyze(root, plan, quality, final=True)
    write(root/'audit.json', report)
    write(root/'paid-cycles.json', report['cycles'])
    write(root/'feature-events.json', report['features'])
    write(root/'manifest.json', dict(manifest, replayed_utc=utc(), no_game_action_sent=True))
    if quality:
        write(root/'messages.jsonl.status.json', quality)
    j = Journal(root)
    emit_analysis(j, report)
    state = 'COMPLETE' if report['complete'] else 'STOPPED_INCOMPLETE'
    j.emit('replay_finished', {'state': state})
    write(root/'status.json', dict(state=state, checked_utc=utc(), mode='offline_replay', **compact(report)))
    return dict(run=str(root), state=state, **compact(report))

class JSONParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)

def parser():
    p = JSONParser(description='Passive local capture controller. Does not click, spin, collect, or change bets.')
    p.add_argument('--version', action='version', version=__version__)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('schema', help='JSON command, state and exit-code contract')
    v = sub.add_parser('validate'); v.add_argument('--plan', required=True)
    d = sub.add_parser('doctor'); d.add_argument('--plan', required=True)
    i = sub.add_parser('init', help='Prepare a fresh run; does not start capture')
    i.add_argument('--run', required=True); i.add_argument('--bet', type=int, required=True)
    i.add_argument('--paid', type=int, required=True)
    i.add_argument('--max-net-loss',type=int,help='Optional settled-cycle net-loss stop; omitted means no separate limit')
    for key in ['adb', 'device', 'game-ip', 'package']:
        i.add_argument('--'+key)
    i.add_argument('--seconds', type=int, default=3600); i.add_argument('--tail-seconds', type=int, default=120)
    for name in ['start', 'status', 'audit', 'stop', 'events', 'wait', 'export']:
        c = sub.add_parser(name); c.add_argument('--run', required=True)
        if name=='stop': c.add_argument('--force', action='store_true', help='Stop capture immediately, preserving incomplete classification')
        if name=='events':
            c.add_argument('--after', type=int, default=0); c.add_argument('--limit', type=int, default=20)
        if name=='wait':
            c.add_argument('--after', type=int, default=0); c.add_argument('--timeout', type=float, default=30)
        if name=='export': c.add_argument('--out', required=True)
    r = sub.add_parser('replay');r.add_argument('--plan', required=True);r.add_argument('--out', required=True)
    source=r.add_mutually_exclusive_group(required=True)
    source.add_argument('--pcap');source.add_argument('--input-run');r.add_argument('--game-ip')
    w = sub.add_parser('_worker', help=argparse.SUPPRESS)
    w.add_argument('--run', required=True);w.add_argument('--owner-token', required=True)
    c = sub.add_parser('discover', help='Discover ADB, online devices, foreground app and passive heartbeat candidates')
    c.add_argument('--store'); c.add_argument('--adb'); c.add_argument('--device'); c.add_argument('--package')
    c.add_argument('--no-probe',action='store_true',help='List sockets without passive heartbeat probes')
    for name in ['machine-list', 'machine-save', 'machine-show', 'machine-delete', 'machine-restore', 'session-prepare', 'vision-status', 'vision-read']:
        c = sub.add_parser(name)
        c.add_argument('--store')
        if name in ('machine-show', 'machine-delete', 'machine-restore', 'session-prepare'): c.add_argument('--id', required=True)
        if name=='machine-restore':c.add_argument('--deleted',required=True)
        if name == 'machine-save': c.add_argument('--input', required=True)
        if name == 'session-prepare':
            c.add_argument('--purpose', choices=['connection', 'first_spin', 'sampling'], required=True)
            c.add_argument('--run', required=True); c.add_argument('--paid', type=int, default=1)
            c.add_argument('--connection')
        if name in ('vision-status', 'vision-read'): c.add_argument('--codex')
        if name == 'vision-read':
            c.add_argument('--image', action='append', required=True); c.add_argument('--model')
            c.add_argument('--notes',default='')
    return p

def run(args):
    command = args.command
    if command == 'discover':
        from .discovery import discover
        from .workflow import default_store
        return discover(args.store or default_store(), args.adb, args.device, args.package, not args.no_probe)
    if command.startswith(('machine-', 'session-')) or command in ('vision-status', 'vision-read'):
        from .workflow import default_store, list_profiles, save_profile, load_profile, verified, prepare_session, delete_profile, restore_profile
        store = args.store or default_store()
        if command == 'machine-list':
            return dict(store=str(store), machines=[dict(p, first_spin_verified=verified(p)) for p in list_profiles(store)])
        if command == 'machine-show':
            p = load_profile(store, args.id)
            return dict(p, first_spin_verified=verified(p))
        if command == 'machine-save': return save_profile(store, read(args.input))
        if command == 'machine-delete': return delete_profile(store,args.id)
        if command == 'machine-restore': return restore_profile(store,args.id,args.deleted)
        if command == 'session-prepare':
            return prepare_session(store, args.id, args.purpose, args.run, args.paid, args.connection)
        from .vision import codex_status, recognize
        if command == 'vision-status': return codex_status(args.codex)
        if command == 'vision-read': return recognize(args.image, store, args.codex, args.model,args.notes)
    if command=='schema':
        return dict(version=__version__, states=STATES, adapter='mia-bole-v1',
                    commands=['init','validate','doctor','start','status','events','wait','stop','replay','audit','export',
                              'machine-list','machine-show','machine-save','machine-delete','machine-restore',
                              'session-prepare','vision-status','vision-read','discover'],
                    exit_codes={'0':'command succeeded (inspect state/health)', '2':'invalid input', '3':'audit/replay incomplete', '4':'runtime failure'},
                    events='events --after CURSOR returns only new durable events; stdout is one JSON envelope per command',
                    stop='normal stop drains rewards, force marks capture incomplete; neither stops the game',
                    process='only verified task-owned processes may be signalled',
                    extension='protocol decoder and machine accounting adapter are separate modules')
    if command=='validate':return normalize(read(args.plan))
    if command=='doctor':return doctor(normalize(read(args.plan))['capture'])
    if command=='init':
        p=make_plan(args);root=init_run(args.run,p)
        return dict(run=str(root), state='PREPARED', plan=p)
    if command=='replay':return replay(args)
    if command=='_worker':
        # Verbose decoder stdout stays in task log, never floods the CLI caller.
        return worker(args.run,args.owner_token)
    root=Path(args.run).resolve()
    if not (root/'plan.json').is_file():
        raise FileNotFoundError('Run plan is missing: '+str(root))
    if command=='start':return start(root)
    if command=='status':return status(root)
    if command=='events':
        if args.after<0 or not 1<=args.limit<=1000:raise ValueError('Invalid cursor or limit')
        events=[e for e in jsonl(root/'events.jsonl') if e['cursor']>args.after]
        selected=events[:args.limit]
        return dict(events=selected,next_cursor=selected[-1]['cursor'] if selected else args.after,has_more=len(events)>args.limit)
    if command=='wait':
        if not 0<=args.timeout<=60:raise ValueError('wait timeout must be 0..60 seconds')
        deadline=time.monotonic()+args.timeout
        while True:
            events=[e for e in jsonl(root/'events.jsonl') if e['cursor']>args.after]
            s=status(root)
            if events or not s['live'] or time.monotonic()>=deadline:
                return dict(status=s,events=events[:20],next_cursor=events[min(19,len(events)-1)]['cursor'] if events else args.after)
            time.sleep(0.25)
    if command=='stop':
        s=status(root)
        if s['state'] in TERMINAL:return dict(already_stopped=True,**s)
        if not s['worker_alive']:
            raise RuntimeError('Worker is not verifiably alive; inspect manifest. No process was signalled.')
        write(root/'control.json',dict(requested_utc=utc(),force=args.force))
        return dict(stop_requested=True,force=args.force,
                    action='Pause new paid spins manually; this command controls capture only.')
    if command=='audit':
        plan=normalize(read(root/'plan.json'))
        q=read(root/'messages.jsonl.status.json') if (root/'messages.jsonl.status.json').exists() else None
        report=session_report(root,plan,q or {},final=True);write(root/'audit.json',report)
        existing=read(root/'status.json')
        if existing.get('mode')=='offline_replay':
            write(root/'status.json',dict(state='COMPLETE' if report['complete'] else 'STOPPED_INCOMPLETE',
                  checked_utc=utc(),mode='offline_replay',**compact(report)))
        return compact(report)
    if command=='export':
        report=read(root/'audit.json');out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=False)
        for f in ['plan.json','machine.json','audit.json','paid-cycles.json','feature-events.json','events.jsonl']:
            if (root/f).exists():shutil.copyfile(root/f,out/f)
        write(out/'export-manifest.json',dict(created_utc=utc(),source=str(root),files={p.name:digest(p) for p in out.iterdir() if p.is_file()}))
        return dict(output=str(out), complete=report['complete'], includes_raw_packets=False)
    raise ValueError('Unknown command')

def main():
    try:
        if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
        args=parser().parse_args()
        data=run(args);output(data)
        if args.command in ['replay','audit'] and not data.get('complete',False):return 3
        return 0
    except (ValueError,FileExistsError,FileNotFoundError) as exc:
        output(exc,False,'invalid_input');return 2
    except Exception as exc:
        output(exc,False,'runtime_failure');return 4

if __name__=='__main__':
    raise SystemExit(main())
