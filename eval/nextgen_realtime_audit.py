"""Offline audit of public candidates, receipts and authoritative GUI locks.

The full board is read only while replaying saved inputs. It never enters a
policy request. Replaying does not run policies or consume live search quota.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from agents import nextgen_contracts as c
from agents.compact_search import CompactSearchState, legal_action_indices, transition
from agents.nextgen_shared_search import _pairs, _public_state
from eval.realtime_arena import match_from_replay, replay_realtime_match
from puyo_env.actions import action_to_placement
from src.core.realtime import TickInput


def audit(report, replay):
    verified = replay_realtime_match(replay)
    match = match_from_replay(replay)
    attempts = [a for a in report['attempts'] if a['record']['outcome'] == 'activated']
    activated = {a['record']['activation_tick']: a for a in attempts}
    if len(activated) != len(attempts):
        raise ValueError('ambiguous activation ticks')
    rows, events, unattributed_locks, active = [], [], [], None
    for tick in replay['ticks']:
        if match.tick in activated:
            attempt = activated[match.tick]
            d = c.Diagnostics.from_dict(attempt['payload']['nextgen'])
            public, complete = _public_state(d.request)
            pair = _pairs(d.request.public.own.known_pieces)[0]
            full = CompactSearchState.from_game(match.player_states['player_0'].simulator.game)
            predictions = {}
            for name, state in (('public', public), ('offline_actual', full)):
                predicted = [(action, transition(state, pair, action))
                             for action in legal_action_indices(state)]
                predictions[name] = {
                    'immediate_clears': [a for a, r in predicted if r.valid and r.chain_count],
                    'reachable_clears': [a for a, r in predicted if r.valid and r.chain_count
                                         and d.request.execution.reachable_mask[a]],
                    # Does not include future incoming garbage or movement.
                    'reachable_nonfatal_clears': [a for a, r in predicted
                        if r.valid and r.chain_count and not r.game_over
                        and d.request.execution.reachable_mask[a]],
                }
            active = {
                'decision': d.request.identity.decision_id,
                'request_tick': d.request.execution.request_tick,
                'public_digest': d.request.public.digest,
                'reachable_mask': list(d.request.execution.reachable_mask),
                'candidates': [v.to_dict() for v in d.batch.candidates],
                'tactic_ranks': [v.to_dict() for v in d.batch.tactics],
                'selection': d.selection.to_dict(), 'receipt': d.receipt.to_dict(),
                'selected_action': attempt['record']['executed_action'],
                'survival': attempt['payload']['search']['survival'],
                'board_complete': complete, 'root_predictions': predictions,
                'incoming': [p.to_dict() for p in d.request.public.own.attack_packets],
            }
            rows.append(active)
        result = match.step({agent: TickInput.from_names(**value)
                             for agent, value in tick['inputs'].items()})
        if result.tick != tick['tick']:
            raise ValueError('replay tick differs')
        for agent, step in result.player_results.items():
            for event in step.events:
                if event.type not in ('lock', 'resolution_complete'):
                    continue
                value = {'player': agent, 'type': event.type, 'tick': event.tick, **event.data,
                         'attack': result.attack_diagnostics[agent]}
                events.append(value)
                if agent == 'player_0' and event.type == 'lock' and (
                        active is None or 'lock' in active):
                    unattributed_locks.append(value)
                    active = None
                if agent == 'player_0' and active is not None:
                    if event.type == 'lock':
                        expected = action_to_placement(active['selected_action'])
                        active['lock'] = value
                        active['root_matches'] = (event.data['axis_x'], event.data['rotation']) == (
                            expected.axis_x, expected.rotation.name)
                    else:
                        active['resolution'] = value
        for agent, count in result.dropped_ojama.items():
            if count:
                events.append({'type': 'drop', 'player': agent, 'tick': result.tick, 'amount': count})
    if verified != match.state_hash() or len(rows) != len(attempts):
        raise AssertionError('replay/decision audit mismatch')
    return {
        'schema': 'puyo.nextgen.realtime_audit.v1',
        'provenance': 'offline replay only; actual board never supplied to policy',
        'final_hash_verified': verified,
        'game_over': {a: s.simulator.game.game_over for a, s in match.player_states.items()},
        'lock_mismatch_decisions': [r['decision'] for r in rows if r.get('root_matches') is False],
        'unlocked_decisions': [r['decision'] for r in rows if 'lock' not in r],
        'unattributed_locks': unattributed_locks,
        'events': events, 'decisions': rows,
    }


def read_json(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == '.gz' else raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--replay', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    value = audit(read_json(args.report), read_json(args.replay))
    args.output.write_text(json.dumps(value, indent=2) + '\n')


if __name__ == '__main__':
    main()
