import datetime as dt
import hashlib
import ipaddress
import json
import os
import re
from pathlib import Path

STATES = ['PREPARED', 'STARTING', 'CHECKING_CONNECTION', 'WAITING_FOR_SPIN', 'CAPTURING', 'DRAINING',
          'COMPLETE', 'STOPPED_EARLY', 'STOPPED_INCOMPLETE', 'FAILED']
TERMINAL = set(STATES[-4:])

def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def write(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.partial')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temp, path)

def jsonl(path):
    """Ignore only an unterminated live last line; malformed complete lines fail."""
    p = Path(path)
    if not p.exists():
        return []
    data = p.read_bytes()
    return [json.loads(line) for line in data.split(b'\n')[:-1] if line.strip()]

def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(name + ' must be a positive integer')
    return value

def normalize(plan):
    if plan.get('schema_version') != 'slotcapture/v1':
        # Accept the already-authorized research contract as offline input.
        plan = dict(schema_version='slotcapture/v1', run_id=plan.get('run_id', 'imported'),
                    adapter='mia-bole-v1', bet=plan.get('conditions', {}).get('fixed_bet'),
                    target_paid=plan.get('sample', {}).get('target_paid_spins'),
                    max_wager=plan.get('budget', {}).get('max_gross_paid_wager'),
                    max_net_loss=plan.get('budget', {}).get('net_loss_limit'),
                    capture={}, source_plan=plan)
    plan = dict(plan)
    if plan.get('adapter') != 'mia-bole-v1':
        raise ValueError('Unsupported adapter; do not apply Mia rules to another machine')
    auto_bet = plan.get('auto_bet', False)
    if not isinstance(auto_bet, bool):
        raise ValueError('auto_bet must be boolean')
    if plan.get('bet') is None and not auto_bet:
        raise ValueError('bet is required unless auto_bet is enabled')
    if plan.get('bet') is not None:
        positive(plan['bet'], 'bet')
    positive(plan.get('target_paid'), 'target_paid')
    if plan['target_paid'] > 1_000_000:
        raise ValueError('target_paid exceeds implementation limit')
    if plan.get('bet') is None:
        if plan.get('max_wager') is not None:
            raise ValueError('automatic bet cannot have a fixed wager until detected')
        plan['max_wager'] = None
    else:
        plan.setdefault('max_wager', plan['bet'] * plan['target_paid'])
        positive(plan['max_wager'], 'max_wager')
        if plan['max_wager'] < plan['bet'] * plan['target_paid']:
            raise ValueError('planned wager exceeds max_wager')
    if plan.get('purpose', 'sampling') not in ('sampling', 'first_spin', 'connection'):
        raise ValueError('unsupported capture purpose')
    plan.setdefault('max_net_loss', None)
    if plan['max_net_loss'] is not None:
        positive(plan['max_net_loss'], 'max_net_loss')
    cap = dict(plan.get('capture') or {})
    defaults = dict(seconds=3600, tail_seconds=120, quiet_seconds=5, poll_seconds=2,
                    max_raw_bytes=256 * 1024 * 1024, tcpdump='tcpdump', package=None)
    for k, v in defaults.items():
        cap.setdefault(k, v)
    for key in ['seconds', 'tail_seconds', 'quiet_seconds', 'max_raw_bytes']:
        positive(cap[key], key)
    user=cap.get('android_user')
    if user is not None and (isinstance(user,bool) or not isinstance(user,int) or not 0<=user<10000):
        raise ValueError('android_user must be an integer in 0..9999')
    if not 0.2 <= cap['poll_seconds'] <= 30:
        raise ValueError('poll_seconds must be between 0.2 and 30')
    if cap['seconds'] > 86400 or cap['quiet_seconds'] > cap['tail_seconds']:
        raise ValueError('invalid duration or tail limits')
    if cap.get('game_ip'):
        ipaddress.IPv4Address(cap['game_ip'])
    for key, pattern in [('device', r'[A-Za-z0-9_.:-]+'),
                         ('tcpdump', r'[A-Za-z0-9_./-]+'),
                         ('package', r'[A-Za-z][A-Za-z0-9_.]+')]:
        if cap.get(key) and not re.fullmatch(pattern, cap[key]):
            raise ValueError('unsafe or invalid ' + key)
    plan['capture'] = cap
    return plan

class Journal:
    def __init__(self, root):
        self.path = Path(root) / 'events.jsonl'
        old = jsonl(self.path)
        self.seen = {x['event_id'] for x in old}
        self.cursor = max((x['cursor'] for x in old), default=0)

    def emit(self, kind, detail, key=None, severity='info'):
        key = key or kind + ':' + json.dumps(detail, sort_keys=True, ensure_ascii=False)
        identity = hashlib.sha256(key.encode()).hexdigest()[:24]
        if identity in self.seen:
            return
        self.cursor += 1
        event = dict(cursor=self.cursor, event_id=identity, time_utc=utc(), kind=kind,
                     severity=severity, detail=detail)
        with self.path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n')
        self.seen.add(identity)

def emit_analysis(journal, report):
    if report['paid'] and not report['issues']:
        journal.emit('first_spin_verified', {'bet': report['cycles'][0]['bet'],
                     'matched_paid_result': True}, key='first-spin')
    for f in report['features']:
        journal.emit('feature_confirmed' if f['confirmed'] else 'feature_expected', f,
                     key=f"feature:{f['identity']}:{f['confirmed']}:{f['award']}")
    for issue in report['issues']:
        journal.emit('capture_issue', issue, severity='critical')
    if report['paid'] >= report['target_paid']:
        journal.emit('paid_target_reached', {'paid': report['paid'],
                     'action': 'Stop new paid spins; complete the remaining reward chain manually.'},
                     key='paid-target', severity='action_required')
    if report.get('net_loss_stop_at_paid'):
        journal.emit('net_loss_stop_reached', {'at_paid': report['net_loss_stop_at_paid'],
                     'action': 'Stop new paid spins; capture cannot stop the game.'},
                     key='net-loss-stop', severity='action_required')
