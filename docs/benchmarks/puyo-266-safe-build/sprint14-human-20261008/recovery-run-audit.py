"""Audit saved matches offline; full board is diagnostic only, never runtime input."""
import argparse
import json
from pathlib import Path

import numpy as np
from agents import nextgen_contracts as c
from agents.compact_search import CompactSearchState
from eval.nextgen_realtime_audit import read_json
from eval.realtime_arena import match_from_replay
from src.core.realtime import TickInput


def load(root, name):
    path = root / (name + '.json')
    return read_json(path if path.exists() else path.with_suffix('.json.gz'))


def summarize(root, baseline):
    report, replay, audit = (load(root, name) for name in ('report', 'replay', 'audit'))
    old_report, old_replay = (load(baseline, name) for name in ('report', 'replay'))
    by_tick = {}
    quotas = []
    for attempt in report['attempts']:
        wire = attempt['payload']['nextgen']
        req = c.NextgenRequest.from_dict(wire['request'])
        by_tick.setdefault(req.execution.request_tick, []).append(req)
        counters = wire['batch']['counters']
        survival = attempt['payload']['search']['survival']['nodes']
        if (counters['response_nodes'] > 256 or survival > 128
                or counters['shared_nodes'] > 600000 or counters['template_nodes'] > 128):
            quotas.append(req.identity.decision_id)
    match = match_from_replay(replay)
    hidden, garbage_events = [], []
    for tick in replay['ticks']:
        game = match.player_states['player_0'].simulator.game
        for req in by_tick.get(match.tick, ()):
            actual = tuple(tuple(c.PUBLIC_CELL_TO_COLOR.index(game.field.grid[y][x].color)
                                 for x in range(6)) for y in (12, 13))
            hidden.append({'decision': req.identity.decision_id,
                           'known': req.known_inference() is not None,
                           'offline_hidden_match': req.inference.hidden_rows == actual})
        before = CompactSearchState.from_game(game).planes[5].bit_count()
        result = match.step({key: TickInput.from_names(**value) for key, value in tick['inputs'].items()})
        after = CompactSearchState.from_game(game).planes[5].bit_count()
        if after != before:
            garbage_events.append({'tick': result.tick, 'before': before, 'after': after,
                                   'dropped': result.dropped_ojama.get('player_0', 0)})
    assert match.state_hash() == audit['final_hash_verified']
    recoveries = []
    for decision in audit['decisions']:
        recovery = decision['survival'].get('control_proof', {}).get('landed_garbage_recovery', {})
        if recovery.get('preferred_root') is None:
            continue
        lock, resolution = decision.get('lock', {}), decision.get('resolution', {})
        removals = [event for event in garbage_events
                    if lock.get('tick', replay['ticks'][-1]['tick'] + 1) <= event['tick'] <= resolution.get('tick', -1)
                    and event['before'] > event['after']]
        recoveries.append({'decision': decision['decision'], 'selected': decision['selected_action'],
                           'preferred': recovery['preferred_root'], 'nodes': decision['survival']['nodes'],
                           'predicted_removed': recovery['garbage_removed'],
                           'actual_removed': sum(e['before'] - e['after'] for e in removals),
                           'actual_chain': resolution.get('chain_count'), 'lock_tick': lock.get('tick'),
                           'root_matches': decision.get('root_matches'), 'garbage_events': removals})
    old = [a for a in old_report['attempts'] if a['record']['outcome'] == 'activated']
    new = [a for a in report['attempts'] if a['record']['outcome'] == 'activated']
    comparisons = []
    for index, (a, b) in enumerate(zip(old, new), 1):
        wa, wb = a['payload']['nextgen'], b['payload']['nextgen']
        pa, pb = wa['request']['public'], wb['request']['public']
        def ranks(wire):
            by_id = {v['candidate_id']: v for v in wire['batch']['candidates']}
            return {t['tactic_id']: [by_id[cid]['root_action'] for cid in t['candidate_ids']]
                    for t in wire['batch']['tactics']}
        comparisons.append({'lock_index': index, 'public_equal': pa == pb,
                            'visible_equal': pa['own']['visible_board'] == pb['own']['visible_board'],
                            'known_pieces_equal': pa['own']['known_pieces'] == pb['own']['known_pieces'],
                            'ranked_roots_equal': ranks(wa) == ranks(wb),
                            'action_equal': a['record']['executed_action'] == b['record']['executed_action'],
                            'baseline_action': a['record']['executed_action'], 'action': b['record']['executed_action']})
    prefix = {}
    for key in ('inputs', 'snapshot_hash'):
        prefix[key + '_first_different_tick'] = next((b['tick'] for a, b in zip(old_replay['ticks'], replay['ticks'])
                                                    if a.get(key) != b.get(key)), None)
    durations = [a['record']['policy_elapsed_seconds'] for a in report['attempts']]
    return {'source': report['source']['commit'], 'source_changed': report['source_changed_during_run'],
            'baseline_source': old_report['source']['commit'], 'scope': 'new normal run, wall-clock dependent; not paired policy A/B',
            'source_file_fingerprints_equal': report['source']['files_sha256'] == old_report['source']['files_sha256'],
            'source_file_count': len(report['source']['files_sha256']),
            'config_differences': {key: [old_report['config'].get(key), value] for key, value in report['config'].items()
                                   if old_report['config'].get(key) != value},
            'ticks': report['ticks'], 'elapsed_seconds': report['elapsed_seconds'],
            'target_placements': report['target_placements'], 'actual_locks': report['observed_placements'],
            'outcomes': report['summary']['outcomes'], 'fallbacks': report['summary']['fallbacks'],
            'game_over': audit['game_over'], 'final_hash_verified': audit['final_hash_verified'],
            'lock_mismatches': audit['lock_mismatch_decisions'], 'unlocked': audit['unlocked_decisions'],
            'unattributed': audit['unattributed_locks'], 'quota_violations': quotas,
            'decision_seconds': dict(zip(('p50', 'p95', 'max'), map(float, np.percentile(durations, [50, 95, 100])))),
            'inference_comparisons': hidden, 'recoveries': recoveries, 'garbage_events': garbage_events,
            'actual_clears': [[d['decision'], d['resolution']['chain_count']] for d in audit['decisions']
                              if d.get('resolution', {}).get('chain_count', 0)],
            'attack_events': [e for e in audit['events'] if e['type'] == 'drop' or (
                e['player'] == 'player_1' and e['type'] == 'resolution_complete' and e.get('chain_count'))],
            'prefix_comparison': prefix, 'activation_order_comparisons': comparisons}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    value = summarize(args.run, args.baseline)
    args.output.write_text(json.dumps(value, indent=2) + '\n')
    print({k: value[k] for k in ('ticks', 'actual_locks', 'outcomes', 'game_over', 'lock_mismatches', 'unlocked', 'quota_violations', 'decision_seconds', 'recoveries', 'prefix_comparison')})
