"""Deterministic adapter for the observed Mia/Bole reward contract, not a probability model."""
from pathlib import Path
from .common import jsonl

PAY = {2: {2: 500, 3: 2000, 4: 6000, 5: 30000},
       3: {3: 1500, 4: 5000, 5: 25000}, 4: {3: 1500, 4: 4000, 5: 20000},
       5: {3: 500, 4: 3000, 5: 15000}, 6: {3: 500, 4: 3000, 5: 15000},
       7: {3: 500, 4: 2000, 5: 10000}, 8: {3: 500, 4: 2000, 5: 10000},
       9: {3: 500, 4: 2000, 5: 10000}}
LADDER = [1, 2, 3, 5, 10, 25]

def analyze(root, plan, quality=None, final=False):
    root = Path(root)
    messages = jsonl(root / 'messages.jsonl')
    spins = jsonl(root / 'messages.jsonl.spins.jsonl')
    issues, cycles, features = [], [], []
    if final:
        for filename in ['messages.jsonl', 'messages.jsonl.spins.jsonl']:
            data = (root/filename).read_bytes() if (root/filename).exists() else b''
            if data and not data.endswith(b'\n'):
                issues.append({'kind': 'unterminated_jsonl_line', 'file': filename})
    pending, receipts, used = 0, [], set()
    raw_results = {}
    for e in messages:
        if e.get('kind') in {'decode_error', 'packet_error', 'capture_error', 'poll_error',
                             'connection_reset_with_unfinished_data', 'reassembly_limit', 'framing_error'}:
            issues.append({'kind': e['kind'], 'evidence': e.get('payload_sha256'), 'error': e.get('error')})
        cmd, direction = e.get('command'), e.get('direction')
        if cmd == 'batch_spin':
            pending += 1 if direction == 'request' else -1
            if pending < 0:
                issues.append({'kind': 'response_without_request'})
                pending = 0
            if direction == 'response':
                raw_results[e.get('payload_sha256')] = e['message'][1].get('list', [])
        if cmd == 'collect_coins' and direction == 'response':
            receipts.append(dict(e['message'][1], _time=e['time_utc'], _sha=e['payload_sha256']))
    receipts.sort(key=lambda r: r['_time'])

    def receipt(amount, types, since, strictly=False):
        for r in receipts:
            if r['_sha'] in used or r.get('type') not in types or r.get('collect') != amount:
                continue
            if (r['_time'] <= since if strictly else r['_time'] < since):
                continue
            used.add(r['_sha'])
            return r
        return None

    line_checks = 0
    last_mode = None
    for s in spins:
        sample = s.get('sample')
        mode = s.get('mode')
        if not s.get('matched_request') or mode not in ['base', 'free'] or s.get('batch_size') != 1:
            issues.append({'kind': 'unmatched_or_ambiguous_spin', 'sample': sample})
            continue
        if s.get('bet') != plan['bet']:
            issues.append({'kind': 'bet_mismatch', 'sample': sample, 'actual': s.get('bet'), 'expected': plan['bet']})
        if mode == 'base':
            cycles.append(dict(paid_ordinal=len(cycles)+1, bet=s['bet'], base_win=s.get('base_win', 0),
                               free_line_win=0, wheel_win=0, base_jp_win=0, free_jp_win=0,
                               beer_final_win=0, free_spins=0, free_expected=0, samples=[]))
        if not cycles:
            issues.append({'kind': 'orphan_free', 'sample': sample})
            continue
        c = cycles[-1]
        if mode == 'free':
            c['free_spins'] += 1
            c['free_line_win'] += s.get('base_win', 0)
        c['samples'].append(sample)
        raw = raw_results.get(s.get('response_sha256'), [])
        raw = raw[0] if len(raw) == 1 else {}
        if not raw:
            issues.append({'kind': 'raw_result_missing', 'sample': sample})
        if mode == 'base' and raw.get('free_spins'):
            if isinstance(raw['free_spins'], int):
                c['free_expected'] = raw['free_spins']
            else:
                issues.append({'kind': 'unknown_free_counter', 'sample': sample})
        if mode == 'free' and raw.get('free_spins'):
            issues.append({'kind': 'unsupported_free_retrigger', 'sample': sample})
        if s.get('win_lines'):
            try:
                amount = sum(PAY[w['symbol']][w['count']] for w in s['win_lines']) * s['bet']
                amount = amount * LADDER[s['multiplier_state'][0]-1]
                if amount != s['base_win'] * 6000:
                    issues.append({'kind': 'reported_line_amount_mismatch', 'sample': sample})
                line_checks += 1
            except (KeyError, IndexError, TypeError):
                issues.append({'kind': 'unknown_line_rule', 'sample': sample})
        bonus = s.get('bonus_game') or {}
        unknown = set(bonus) - {'bonus_id', 'wheel', 'jackpot', 'progressive_list'}
        if unknown:
            issues.append({'kind': 'unknown_feature', 'keys': sorted(unknown), 'sample': sample})
        if 'wheel' in bonus and 'jackpot' in bonus:
            issues.append({'kind': 'unsupported_same_spin_feature_overlap', 'sample': sample})
        for kind in ['wheel', 'jackpot']:
            if kind not in bonus:
                continue
            award = bonus[kind]['total_win' if kind == 'wheel' else 'jp_win']
            c['wheel_win' if kind == 'wheel' else 'free_jp_win' if mode == 'free' else 'base_jp_win'] += award
            expected = s['base_win'] + award
            match = receipt(expected, [1, 2] if kind == 'wheel' else [1], s['response_time_utc'])
            identity = f"{bonus.get('bonus_id')}:{kind}:{sample}"
            features.append(dict(identity=identity, feature=kind, paid_cycle=c['paid_ordinal'],
                                 sample=sample, award=award, same_spin_line_win=s['base_win'],
                                 expected_collect=expected, confirmed=bool(match),
                                 receipt_sha256=match['_sha'] if match else None))
            final = (match.get('receive_data') or {}).get('final_bonus') if match else None
            if final:
                final_amount = final['total_win']
                if kind != 'wheel' or mode != 'base' or sum(final['win_list']) != final_amount:
                    issues.append({'kind': 'unsupported_or_inconsistent_final_bonus', 'sample': sample})
                final_match = receipt(final_amount, [2], match['_time'], strictly=True)
                if final_match and final_match.get('receive_data'):
                    issues.append({'kind': 'nested_final_bonus', 'sample': sample})
                # Announced and actually collected are explicit; not a balance difference.
                c['beer_final_win'] += final_amount
                features.append(dict(identity=identity+':final', feature='beer_final',
                                     paid_cycle=c['paid_ordinal'], sample=sample, award=final_amount,
                                     confirmed=bool(final_match), historical_carryover=True,
                                     receipt_sha256=final_match['_sha'] if final_match else None))
        last_mode = mode
    free_pending = 0
    for c in cycles:
        c['return'] = sum(c[k] for k in ['base_win', 'free_line_win', 'wheel_win',
                                         'base_jp_win', 'free_jp_win', 'beer_final_win'])
        c['return_excluding_beer_final'] = c['return'] - c['beer_final_win']
        c['return_x'] = c['return'] / c['bet']
        if c['free_expected'] or c['free_spins']:
            end_time = next(s['response_time_utc'] for s in reversed(spins) if s.get('sample') == c['samples'][-1])
            match = receipt(c['return'], [0], end_time)
            c['free_chain_confirmed'] = bool(match)
            if c['free_spins'] != c['free_expected'] or not match:
                free_pending += 1
        else:
            c['free_chain_confirmed'] = True
    quality = quality or {}
    for k in ['errors'] + (['incomplete_streams'] if final else []):
        if quality.get(k):
            issues.append({'kind': k, 'count': quality[k]})
    paid = len(cycles)
    wager = sum(c['bet'] for c in cycles)
    if paid > plan['target_paid'] or wager > plan['max_wager']:
        issues.append({'kind': 'paid_or_wager_limit_exceeded', 'paid': paid, 'wager': wager})
    unmatched_receipts = len(receipts) - len(used)
    if unmatched_receipts:
        issues.append({'kind': 'unattributed_collect', 'count': unmatched_receipts})
    waiting = sum(not f['confirmed'] for f in features) + free_pending
    net_loss_stop = None
    if plan.get('max_net_loss') is not None:
        cumulative_net = 0
        for c in cycles:
            if not c['free_chain_confirmed'] or any(not f['confirmed'] for f in features if f['paid_cycle']==c['paid_ordinal']):
                break
            cumulative_net += c['return'] - c['bet']
            if cumulative_net <= -plan['max_net_loss']:
                net_loss_stop = c['paid_ordinal']
                break
        if net_loss_stop and paid > net_loss_stop:
            issues.append({'kind': 'paid_after_net_loss_stop', 'stop_at_paid': net_loss_stop, 'actual_paid': paid})
    tail_complete = bool(cycles) and waiting == 0 and pending == 0 and not quality.get('incomplete_streams') and not issues
    return dict(adapter=plan['adapter'], paid=paid, free=sum(c['free_spins'] for c in cycles),
                target_paid=plan['target_paid'], paid_wager=wager,
                expected_return=sum(c['return'] for c in cycles),
                return_excluding_beer_final=sum(c['return_excluding_beer_final'] for c in cycles),
                pending_requests=pending, pending_rewards=waiting,
                pending_reassembly=quality.get('incomplete_streams', 0),
                net_loss_stop_at_paid=net_loss_stop,
                last_mode=last_mode, tail_complete=tail_complete,
                complete=paid == plan['target_paid'] and tail_complete,
                line_winning_boards_checked=line_checks, unique_receipts_used=len(used),
                features=features, cycles=cycles, issues=issues,
                note='Expected return remains provisional until tail_complete. Reported line amounts are checked; missed lines and backend odds are not inferred.')

def compact(report):
    data = {k: v for k, v in report.items() if k not in ['features', 'cycles', 'note', 'issues']}
    data.update(issue_count=len(report['issues']), issues=report['issues'][:5])
    return data
