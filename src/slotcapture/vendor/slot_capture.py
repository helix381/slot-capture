"""Passive PCAP/TCP reconstruction and game message recording. Never sends game requests."""
import argparse
import collections
import datetime as dt
import hashlib
import json
import ipaddress
import os
import pathlib
import re
import socket
import struct
import subprocess
import sys
import time

if os.environ.get('SLOT_CAPTURE_DEPS'):
    sys.path.insert(0, os.environ['SLOT_CAPTURE_DEPS'])
import dpkt
if __package__:
    from .bole_value_codec import decode, Reader
else:
    from bole_value_codec import decode, Reader


class Recorder:
    def __init__(self, path, game_ip, plan=None):
        self.out = open(path, 'x', encoding='utf-8', buffering=1)
        self.game_ip = game_ip
        self.flows = {}
        self.seen = set()
        self.counts = {}
        self.errors = 0
        self.recorded = 0
        self.pending_spins = collections.defaultdict(collections.deque)
        self.connection_syn = {}
        self.seen_syn = set()
        self.seen_packets = set()
        self.connection_bets = {}
        self.last_bet = None
        self.spin_number = 0
        self.plan = plan or {}
        self.paid_requests = 0
        self.paid_matched = 0
        self.free_matched = 0
        self.plan_violations = 0
        self.requested_paid_wager = 0
        self.zero_counter_requests = 0
        self.last_message_ts = None
        self.heartbeats = {k: {'request': 0, 'response': 0} for k in ('tick', 'keepalive')}
        self.pending_heartbeats = collections.Counter()
        self.heartbeat_pairs = 0
        self.spins = open(str(path) + '.spins.jsonl', 'x', encoding='utf-8', buffering=1)

    def event(self, obj):
        self.out.write(json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')

    def message(self, ts, direction, payload, key, seq):
        identity = (key, seq, hashlib.sha256(payload).hexdigest())
        if identity in self.seen:
            return
        self.seen.add(identity)
        connection = key if direction == 'request' else (key[2], key[3], key[0], key[1])
        queue = self.pending_spins[connection]
        try:
            # Scope by the independently readable command header. Unrelated bodies
            # stay in raw PCAP and cannot contaminate target-game decoding status.
            probe = Reader(payload)
            outer = probe.number('<B')
            if outer in (4, 5):
                count = probe.number('<B' if outer == 4 else '<H')
                command_hint = probe.value() if count else None
                if isinstance(command_hint, str) and command_hint.lower() not in {'batch_spin', 'enter_theme', 'collect_coins'}:
                    if command_hint.lower() in {'tick', 'keepalive'}:
                        self.heartbeats[command_hint.lower()][direction] += 1
                        heartbeat_key = (connection, command_hint.lower())
                        if direction == 'request':
                            self.pending_heartbeats[heartbeat_key] += 1
                        elif self.pending_heartbeats[heartbeat_key]:
                            self.pending_heartbeats[heartbeat_key] -= 1
                            self.heartbeat_pairs += 1
                    self.last_message_ts = ts
                    self.counts[command_hint] = self.counts.get(command_hint, 0) + 1
                    return
            value = decode(payload)
            if not isinstance(value, list) or not value or not isinstance(value[0], str):
                raise ValueError('message is not a command array')
            cmd = value[0]
            self.last_message_ts = ts
            self.counts[cmd] = self.counts.get(cmd, 0) + 1
            # Credentials, login messages and unrelated account events are not exported.
            if cmd.lower() in {'batch_spin', 'enter_theme', 'collect_coins'}:
                self.event({'kind': 'game_message', 'time_utc': dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(),
                            'direction': direction, 'command': cmd, 'tcp_seq': seq,
                            'payload_sha256': identity[2], 'message': value})
                self.recorded += 1
                print(json.dumps({'recorded': cmd, 'direction': direction, 'count': self.recorded}), flush=True)
                if cmd.lower() == 'batch_spin':
                    body = value[1] if len(value) > 1 else {}
                    if direction == 'request':
                        previous_bet = self.connection_bets.get(connection)
                        changed = previous_bet is not None and body.get('bet') != previous_bet
                        self.last_bet = body.get('bet')
                        self.connection_bets[connection] = self.last_bet
                        paid_ordinal = None
                        if body.get('free_spins') == 0:
                            self.zero_counter_requests += 1
                        queue.append({'body': body, 'timestamp': ts, 'changed': changed, 'paid_ordinal': paid_ordinal})
                    elif isinstance(body, dict) and isinstance(body.get('list'), list):
                        req = queue.popleft() if queue else None
                        for index, result in enumerate(body['list']):
                            if not isinstance(result, dict):
                                continue
                            self.spin_number += 1
                            request = req['body'] if req else {}
                            info = result.get('theme_info') or {}
                            free_request = request.get('free_spins')
                            mode = 'free' if isinstance(free_request, (int, float)) and free_request > 0 else ('base' if free_request == 0 else 'unknown')
                            mode_evidence = 'request.free_spins'
                            if isinstance(info.get('free_game'), dict):
                                mode = 'free'
                                mode_evidence = 'response.theme_info.free_game'
                            if len(body['list']) != 1:
                                mode = 'ambiguous_batch'
                            if mode == 'base' and req:
                                self.paid_requests += 1
                                self.paid_matched += 1
                                req['paid_ordinal'] = self.paid_requests
                                if isinstance(request.get('bet'), (int, float)):
                                    self.requested_paid_wager += request['bet']
                                expected = self.plan.get('conditions', {}).get('fixed_bet')
                                if expected is not None and request.get('bet') != expected:
                                    self.plan_violations += 1
                                    self.event({'kind': 'plan_violation', 'reason': 'paid_bet_mismatch', 'expected': expected, 'actual': request.get('bet')})
                                target = self.plan.get('sample', {}).get('target_paid_spins')
                                if target and self.paid_requests > target:
                                    self.plan_violations += 1
                                    self.event({'kind': 'plan_violation', 'reason': 'paid_target_exceeded', 'actual': self.paid_requests, 'target': target})
                            elif mode == 'free' and req:
                                self.free_matched += 1
                                req['paid_ordinal'] = None
                            row = {'sample': self.spin_number, 'response_time_utc': dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(),
                                   'request_time_utc': dt.datetime.fromtimestamp(req['timestamp'], dt.timezone.utc).isoformat() if req else None,
                                   'matched_request': req is not None, 'batch_index': index, 'batch_size': len(body['list']),
                                   'capture_errors_at_recording': self.errors,
                                   'plan_id': self.plan.get('plan_id'), 'run_id': self.plan.get('run_id'),
                                   'paid_ordinal': req.get('paid_ordinal') if req else None,
                                   'mode_from_request': mode, 'bet': request.get('bet'), 'lines': request.get('lines'),
                                   'mode': mode, 'mode_evidence': mode_evidence,
                                   'bet_changed': req['changed'] if req else None, 'free_spins_request': free_request,
                                   'auto_spin': request.get('autoSpin'), 'turbo': request.get('turbo'),
                                   'client_spin_count': request.get('spin_count'),
                                   'base_win': result.get('base_win'), 'total_win': result.get('total_win'),
                                   'jp_win': result.get('jp_win'), 'bonus_base_win': result.get('bonus_base_win'),
                                   'item_list': result.get('item_list'), 'win_lines': result.get('win_lines'),
                                   'win_pos_list': result.get('win_pos_list'), 'multiplier_state': info.get('multi_info'),
                                   'nudge_direction': info.get('nudge_direction'), 'free_spins_response': result.get('free_spins'),
                                   'free_game': result.get('free_game'), 'bonus_game': result.get('bonus_game'),
                                   'collect_info': info.get('collect_info'),
                                   'girl_symbol_count': sum(v == 2 for col in result.get('item_list', []) for v in col),
                                   'response_sha256': identity[2]}
                            self.spins.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')
                            print(json.dumps({'spin': self.spin_number, 'mode': mode, 'bet': row['bet'], 'base_win': row['base_win'],
                                              'total_win': row['total_win'], 'multiplier': row['multiplier_state'],
                                              'matched_request': row['matched_request']}), flush=True)
        except Exception as exc:
            self.errors += 1
            self.event({'kind': 'decode_error', 'timestamp': ts, 'direction': direction,
                        'payload_bytes': len(payload), 'payload_sha256': identity[2], 'error': str(exc)})

    def packet(self, ts, raw, linktype):
        if linktype == 276:
            if len(raw) < 20:
                raise ValueError('short SLL2 header')
            if struct.unpack('>H', raw[:2])[0] != 0x0800:
                return
            ip = dpkt.ip.IP(raw[20:])
        elif linktype == 113:
            if len(raw) < 16:
                raise ValueError('short SLL header')
            if struct.unpack('>H', raw[14:16])[0] != 0x0800:
                return
            ip = dpkt.ip.IP(raw[16:])
        elif linktype == 1:
            ip = dpkt.ethernet.Ethernet(raw).data
        else:
            raise ValueError('unsupported link type: ' + str(linktype))
        if not isinstance(ip, dpkt.ip.IP) or not isinstance(ip.data, dpkt.tcp.TCP):
            return
        src, dst = socket.inet_ntoa(ip.src), socket.inet_ntoa(ip.dst)
        if self.game_ip not in (src, dst):
            return
        tcp = ip.data
        key = (src, tcp.sport, dst, tcp.dport)
        if tcp.data or tcp.flags & dpkt.tcp.TH_SYN:
            packet_id = (ts, key, tcp.seq, tcp.flags, hashlib.sha256(bytes(tcp.data)).digest())
            if packet_id in self.seen_packets:
                return
            self.seen_packets.add(packet_id)
        direction = 'response' if src == self.game_ip else 'request'
        connection = key if direction == 'request' else (key[2], key[3], key[0], key[1])
        if tcp.flags & dpkt.tcp.TH_SYN:
            syn_id = (ts, key, tcp.seq)
            if syn_id not in self.seen_syn:
                self.seen_syn.add(syn_id)
                if not tcp.flags & dpkt.tcp.TH_ACK and self.connection_syn.get(connection) != tcp.seq:
                    reverse = (key[2], key[3], key[0], key[1])
                    old_states = [self.flows.get(k) for k in (key, reverse)]
                    if self.pending_spins.get(connection) or any(s and (s['buffer'] or s['pending']) for s in old_states):
                        self.errors += 1
                        self.event({'kind': 'connection_reset_with_unfinished_data', 'timestamp': ts})
                    self.flows.pop(key, None)
                    self.flows.pop(reverse, None)
                    self.pending_spins.pop(connection, None)
                    self.connection_bets.pop(connection, None)
                    for heartbeat_key in list(self.pending_heartbeats):
                        if heartbeat_key[0] == connection:
                            del self.pending_heartbeats[heartbeat_key]
                    self.connection_syn[connection] = tcp.seq
                self.flows.setdefault(key, {'next': tcp.seq + 1, 'start': tcp.seq + 1, 'pending': {}, 'buffer': bytearray(), 'disabled': False})
        if not tcp.data:
            return
        state = self.flows.setdefault(key, {'next': tcp.seq, 'start': tcp.seq, 'pending': {}, 'buffer': bytearray(), 'disabled': False})
        if state['disabled']:
            return
        seq, data = tcp.seq, bytes(tcp.data)
        if seq < state['next']:
            data = data[state['next'] - seq:]
            seq = state['next']
        if not data:
            return
        if len(state['buffer']) + sum(map(len, state['pending'].values())) + len(data) > 32 * 1024 * 1024:
            state['disabled'] = True
            self.errors += 1
            self.event({'kind': 'reassembly_limit', 'direction': direction, 'timestamp': ts})
            return
        old = state['pending'].get(seq, b'')
        if len(data) > len(old):
            state['pending'][seq] = data
        while state['pending']:
            first = min(state['pending'])
            if first > state['next']:
                break
            block = state['pending'].pop(first)
            block = block[max(0, state['next'] - first):]
            if not block:
                continue
            state['buffer'].extend(block)
            state['next'] += len(block)
        buf = state['buffer']
        while len(buf) >= 4:
            size = struct.unpack('>I', buf[:4])[0]
            if not 1 <= size <= 16 * 1024 * 1024:
                state['disabled'] = True
                self.errors += 1
                self.event({'kind': 'framing_error', 'direction': direction, 'timestamp': ts,
                            'error': 'capture began mid-message or unsupported frame; stream excluded'})
                return
            if len(buf) < size + 4:
                break
            payload = bytes(buf[4:size + 4])
            self.message(ts, direction, payload, key, state['start'])
            del buf[:size + 4]
            state['start'] += size + 4

    def summary(self):
        return {'commands': self.counts, 'heartbeats': self.heartbeats, 'heartbeat_pairs': self.heartbeat_pairs,
                'recorded_messages': self.recorded, 'errors': self.errors,
                'spins': self.spin_number, 'last_bet': self.last_bet, 'last_message_timestamp': self.last_message_ts,
                'pending_spin_requests': sum(map(len, self.pending_spins.values())),
                'plan_id': self.plan.get('plan_id'), 'run_id': self.plan.get('run_id'),
                'paid_request_count': self.paid_requests, 'paid_matched_count': self.paid_matched,
                'zero_counter_requests_seen': self.zero_counter_requests,
                'free_matched_count': self.free_matched, 'requested_paid_wager': self.requested_paid_wager,
                'target_paid_spins': self.plan.get('sample', {}).get('target_paid_spins'),
                'plan_violations': self.plan_violations,
                'incomplete_streams': sum(bool(s['buffer'] or s['pending']) for s in self.flows.values())}


def main():
    ap = argparse.ArgumentParser()
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument('--pcap')
    source.add_argument('--poll', action='store_true', help='Read original device PCAP files; avoids su PTY byte conversion')
    ap.add_argument('--out', required=True)
    ap.add_argument('--raw')
    ap.add_argument('--game-ip', required=True)
    ap.add_argument('--codec', required=True, choices=['bole-value'])
    ap.add_argument('--adb')
    ap.add_argument('--device')
    ap.add_argument('--remote-prefix')
    ap.add_argument('--plan', help='Research sampling-plan.json, optional')
    ap.add_argument('--seconds', type=int, default=7200)
    args = ap.parse_args()
    try:
        ipaddress.IPv4Address(args.game_ip)
    except ValueError:
        ap.error('--game-ip must be a confirmed IPv4 address')
    if args.poll and (not args.adb or not args.device or not args.remote_prefix):
        ap.error('--poll requires --adb, --device and --remote-prefix')
    if args.remote_prefix and not re.fullmatch(r'/data/local/tmp/[A-Za-z0-9_-]+', args.remote_prefix):
        ap.error('--remote-prefix must be a safe unique path under /data/local/tmp')
    if args.seconds <= 0:
        ap.error('--seconds must be positive')
    for suffix in ['', '.spins.jsonl', '.status.json', '.stop']:
        if pathlib.Path(args.out + suffix).exists():
            ap.error('use fresh output paths; existing output/control file: ' + args.out + suffix)
    pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    plan = json.loads(pathlib.Path(args.plan).read_text(encoding='utf-8-sig')) if args.plan else None
    recorder = Recorder(args.out, args.game_ip, plan)
    try:
        if args.poll:
            poll_original_files(args, recorder)
            return
        else:
            packets = complete_packets(pathlib.Path(args.pcap).read_bytes(), strict=True)
        print(json.dumps({'state': 'reading'}), flush=True)
        for timestamp, raw, linktype in packets:
            try:
                recorder.packet(timestamp, raw, linktype)
            except (dpkt.UnpackError, struct.error, ValueError) as exc:
                recorder.errors += 1
                recorder.event({'kind': 'packet_error', 'timestamp': timestamp, 'error': str(exc)})
    except KeyboardInterrupt:
        pass
    except (dpkt.UnpackError, ValueError, struct.error) as exc:
        recorder.errors += 1
        recorder.event({'kind': 'capture_error', 'error': str(exc)})
    finally:
        summary = recorder.summary()
        recorder.event({'kind': 'capture_summary', **summary})
        recorder.out.close()
        recorder.spins.close()
        print(json.dumps(summary), flush=True)


def complete_packets(data, strict=False):
    """Never feed a partially copied last packet into TCP reconstruction."""
    if len(data) < 24:
        if strict:
            raise ValueError('truncated PCAP header')
        return
    if data[:4] == bytes.fromhex('d4c3b2a1'):
        endian = '<'
    elif data[:4] == bytes.fromhex('a1b2c3d4'):
        endian = '>'
    else:
        raise ValueError('unsupported PCAP magic')
    linktype = struct.unpack(endian + 'I', data[20:24])[0]
    offset = 24
    while offset + 16 <= len(data):
        sec, usec, caplen, full = struct.unpack(endian + 'IIII', data[offset:offset + 16])
        if offset + 16 + caplen > len(data):
            if strict:
                raise ValueError('truncated final PCAP packet')
            break
        if caplen != full:
            raise ValueError('truncated capture packet')
        yield sec + usec / 1_000_000, data[offset + 16:offset + 16 + caplen], linktype
        offset += 16 + caplen
    if strict and offset != len(data):
        raise ValueError('truncated final PCAP record')


def poll_original_files(args, recorder):
    deadline = time.monotonic() + args.seconds
    raw_base = pathlib.Path(args.raw or args.out + '.pcap')
    status_path = pathlib.Path(args.out + '.status.json')
    print(json.dumps({'state': 'capturing_original_files', 'seconds': args.seconds, 'poll_seconds': 2}), flush=True)
    sizes = {}
    stop_path = pathlib.Path(args.out + '.stop')
    while time.monotonic() < deadline and not stop_path.exists():
        try:
            listing = subprocess.run([args.adb, '-s', args.device, 'shell', 'ls -tr ' + args.remote_prefix + '.pcap[0-3]'],
                                     capture_output=True, timeout=15)
        except (subprocess.SubprocessError, OSError) as exc:
            recorder.event({'kind': 'device_error', 'error': str(exc)})
            break
        if listing.returncode:
            status = {'state': 'device_or_capture_unavailable', **recorder.summary()}
        else:
            try:
                for remote in listing.stdout.decode().splitlines():
                    remote = remote.strip()
                    if remote not in [args.remote_prefix + '.pcap' + str(i) for i in range(4)]:
                        continue
                    data = subprocess.check_output([args.adb, '-s', args.device, 'exec-out', 'cat', remote], timeout=20)
                    digest = hashlib.sha256(data).hexdigest()
                    if sizes.get(remote) == digest:
                        continue
                    sizes[remote] = digest
                    dest = pathlib.Path(str(raw_base) + remote[-1])
                    temp = pathlib.Path(str(dest) + '.partial')
                    temp.write_bytes(data)
                    temp.replace(dest)
                    for timestamp, raw, linktype in complete_packets(data):
                        recorder.packet(timestamp, raw, linktype)
                status = {'state': 'capturing', **recorder.summary()}
                last = recorder.last_message_ts
                if last is None or time.time() - last > 120:
                    status['state'] = 'stale'
                if recorder.errors:
                    status['state'] = 'degraded'
                if recorder.plan_violations:
                    status['state'] = 'action_required'
                target = recorder.plan.get('sample', {}).get('target_paid_spins')
                if target and recorder.paid_matched >= target and status['state'] == 'capturing':
                    status['state'] = 'paid_target_reached_finish_reward_chain'
            except (subprocess.SubprocessError, dpkt.UnpackError, ValueError, struct.error) as exc:
                status = {'state': 'capture_error', 'error': str(exc), **recorder.summary()}
                recorder.event({'kind': 'poll_error', 'error': str(exc)})
        status['checked_time_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
        temp_status = pathlib.Path(str(status_path) + '.partial')
        temp_status.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
        temp_status.replace(status_path)
        time.sleep(2)
    stopped = {'state': 'stopped', **recorder.summary(), 'checked_time_utc': dt.datetime.now(dt.timezone.utc).isoformat()}
    status_path.write_text(json.dumps(stopped, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
