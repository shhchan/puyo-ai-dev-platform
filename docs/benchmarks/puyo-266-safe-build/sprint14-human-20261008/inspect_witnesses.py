"""Offline fresh-spawn geometry comparison; never runtime/private policy input."""
import argparse
import importlib.util
import json
from pathlib import Path

from agents import nextgen_contracts as c
from agents.compact_search import CompactSearchState, transition
from agents.nextgen_shared_search import _pairs, _public_state
from eval.nextgen_realtime_audit import read_json
from eval.realtime_arena import match_from_replay
from src.core.realtime import TickInput

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    'prior_geometry_diagnostic', ROOT.parent / 'desktop-survival-20260928/inspect_witnesses.py')
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)


def inspect(directory):
    def read(name):
        path = directory / (name + '.json')
        return read_json(path if path.exists() else path.with_suffix('.json.gz'))
    report, replay = read('report'), read('replay')
    match = match_from_replay(replay)
    activated = {a['record']['activation_tick']: a for a in report['attempts']
                 if a['record']['outcome'] == 'activated'}
    rows = []
    for tick in replay['ticks']:
        if match.tick in activated:
            attempt = activated[match.tick]
            diagnostic = c.Diagnostics.from_dict(attempt['payload']['nextgen'])
            public, _ = _public_state(diagnostic.request)
            full = CompactSearchState.from_game(match.player_states['player_0'].simulator.game)
            root = next((v for v in attempt['payload']['search']['survival'].get('roots', ())
                         if v['action'] == attempt['record']['executed_action']), {})
            if root.get('witness'):
                record = {'decision': diagnostic.request.identity.decision_id,
                    'witness': root['witness'],
                    'hidden_difference': [[x, y] for y in (12, 13) for x in range(6)
                                          if public.color_at(x, y) != full.color_at(x, y)],
                    'steps': []}
                valid = True
                for depth, action in enumerate(root['witness']):
                    pair = _pairs(diagnostic.request.public.own.known_pieces)[depth]
                    if depth:
                        pg, fg = prior.geometry(public, pair), prior.geometry(full, pair)
                        record['steps'].append({'depth': depth, 'action': action,
                            'public': action in pg['reachable_actions'],
                            'full': action in fg['reachable_actions'], 'prefix_valid': valid,
                            'public_geometry': pg, 'full_geometry': fg})
                        valid &= action in fg['reachable_actions']
                    public = transition(public, pair, action).state
                    full = transition(full, pair, action).state
                rows.append(record)
        match.step({key: TickInput.from_names(**value) for key, value in tick['inputs'].items()})
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(inspect(args.directory), indent=2) + '\n')
