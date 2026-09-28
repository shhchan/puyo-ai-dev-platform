"""Verify one new GUI-worker replay and separate candidates from actual locks."""
import argparse
import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from agents import nextgen_contracts as c  # noqa: E402
from agents.compact_search import legal_action_indices, transition  # noqa: E402
from agents.nextgen_shared_search import _pairs, _public_state  # noqa: E402
from eval.realtime_arena import match_from_replay, replay_realtime_match  # noqa: E402
from puyo_env.actions import action_to_placement  # noqa: E402
from src.core.realtime import TickInput  # noqa: E402


def audit(directory):
    def read(name):
        plain = directory / (name + '.json')
        return json.loads(plain.read_bytes() if plain.exists() else
                          gzip.decompress((directory / (name + '.json.gz')).read_bytes()))
    report = read('report')
    replay = read('replay' if (directory / 'replay.json').exists() else 'replay-inputs')
    verified = replay_realtime_match(replay)
    match = match_from_replay(replay)
    attempts = [a for a in report['attempts'] if a['record']['outcome'] == 'activated']
    activated = {a['record']['activation_tick']: a for a in attempts}
    rows, current = [], None
    for tick in replay['ticks']:
        if match.tick in activated:
            a = activated[match.tick]
            d = c.Diagnostics.from_dict(a['payload']['nextgen'])
            state, complete = _public_state(d.request)
            pair = _pairs(d.request.public.own.known_pieces)[0]
            predictions = [(action, transition(state, pair, action)) for action in legal_action_indices(state)]
            current = {
                'decision': d.request.identity.decision_id,
                'request': d.request.to_dict(), 'selection': d.selection.to_dict(),
                'receipt': d.receipt.to_dict(),
                'selected_action': a['record']['executed_action'],
                'survival': a['payload']['search']['survival'],
                'board_complete': complete,
                'legal_immediate_clears': [action for action, r in predictions if r.chain_count],
                'reachable_immediate_clears': [action for action, r in predictions
                                             if r.chain_count and d.request.execution.reachable_mask[action]],
            }
            rows.append(current)
        result = match.step({agent: TickInput.from_names(**value) for agent, value in tick['inputs'].items()})
        for event in result.player_results['player_0'].events:
            if event.type == 'lock':
                assert current is not None
                expected = action_to_placement(current['selected_action'])
                current['lock'] = {'tick': event.tick, **event.data}
                current['root_matches'] = (event.data['axis_x'], event.data['rotation']) == (
                    expected.axis_x, expected.rotation.name)
            if event.type == 'resolution_complete':
                current['resolution'] = {'tick': event.tick, **event.data}
    assert verified == match.state_hash()
    assert len(rows) == len(attempts)
    return {
        'condition': 'new desktop step/measured GUI worker run; original human speed/raw unknown',
        'ticks': report['ticks'], 'elapsed_seconds': report['elapsed_seconds'],
        'source_changed': report['source_changed_during_run'],
        'config': report['config'], 'final_hash_verified': verified,
        'game_over': match.player_states['player_0'].simulator.game.game_over,
        'lock_mismatch_decisions': [r['decision'] for r in rows if not r.get('root_matches')],
        'decisions': rows,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.directory)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print('Verified', result['ticks'], 'ticks; lock mismatches', result['lock_mismatch_decisions'])
