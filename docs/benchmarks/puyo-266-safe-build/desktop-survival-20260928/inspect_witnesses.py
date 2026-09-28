"""Read-only bounded geometry comparison; full boards never enter the policy."""
import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from agents import nextgen_contracts as c  # noqa: E402
from agents.compact_search import CompactSearchState, legal_action_indices, transition  # noqa: E402
from agents.nextgen_shared_search import _pairs, _public_state  # noqa: E402
from puyo_env.action_planner import _geometric_paths  # noqa: E402
from puyo_env.actions import PLACEMENT_ACTIONS  # noqa: E402
from src.core.game import GameState  # noqa: E402
from src.core.puyo import Puyo  # noqa: E402

ROOT = Path(__file__).resolve().parent
CASES = ((123, 28), (126, 34), (126, 35), (128, 38), (132, 32), (144, 37))


def read(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def geometry(state, pair):
    # Fresh spawn, zero interpolation/counters, public known colors. The seeded
    # placeholder queue is never read by this geometry-only query.
    game = GameState(seed=0)
    game.state = 'control'
    game.current_puyo_1, game.current_puyo_2 = (Puyo(color) for color in pair)
    for y, row in enumerate(state.to_color_grid()):
        for x, color in enumerate(row):
            game.field.grid[y][x] = Puyo(color)
    legal = legal_action_indices(state)
    budget = [2000]
    paths = _geometric_paths(game, tuple(PLACEMENT_ACTIONS[a] for a in legal), 2000, budget)
    return {
        'reachable_actions': [a for a in legal if paths[PLACEMENT_ACTIONS[a]][1] is not None],
        'expanded_control_states': 2000 - budget[0],
        'control_state_budget': 2000,
    }


def inspect():
    rows = []
    for seed, decision in CASES:
        raw = read(ROOT / 'before' / f'gtr-{seed}.json.gz')
        locks = read(ROOT / 'before' / f'gtr-{seed}.locks.json.gz')
        wire, row, lock = raw['ledger'][decision - 1], raw['rows'][decision - 1], locks['records'][decision - 1]
        req = c.NextgenRequest.from_dict(wire['request'])
        public, _ = _public_state(req)
        full = CompactSearchState(planes=tuple(lock['offline_full_planes']))
        witness = next(v['witness'] for v in row['search']['survival']['roots'] if v['action'] == row['action'])
        record = {
            'seed': seed, 'decision': decision, 'witness': witness,
            'actual_root_matches': lock['lock_matches_requested_root'],
            'source': 'public request compared with offline authoritative replay; never runtime input',
            'hidden_cells_absent_from_public_model': [
                [x, y, full.color_at(x, y).name] for y in (12, 13) for x in range(6)
                if full.color_at(x, y) != public.color_at(x, y)
            ],
            'continuations': [],
        }
        public_prefix, full_prefix = True, True
        for depth, action in enumerate(witness):
            pair = _pairs(req.public.own.known_pieces)[depth]
            if depth:
                p, f = geometry(public, pair), geometry(full, pair)
                record['continuations'].append({
                    'depth': depth, 'action': action,
                    'public_estimate': p, 'offline_full_board': f,
                    'found_in_public': action in p['reachable_actions'],
                    'found_in_offline_full': action in f['reachable_actions'],
                    'preceding_prefix_reachable_in_public': public_prefix,
                    'preceding_prefix_reachable_in_offline_full': full_prefix,
                })
                public_prefix &= action in p['reachable_actions']
                full_prefix &= action in f['reachable_actions']
            public = transition(public, pair, action).state
            full = transition(full, pair, action).state
        rows.append(record)
    return {'scope': 'fresh-spawn geometry only; no live clock or execution guarantee', 'cases': rows}


if __name__ == '__main__':
    value = inspect()
    output = ROOT / 'witness-comparison.json'
    if output.exists():
        assert value == json.loads(output.read_text())
    else:
        output.write_text(json.dumps(value, indent=2) + '\n')
    print('Verified', len(value['cases']), 'public/offline witness comparisons')
