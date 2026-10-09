"""Read saved public inputs and old G2 raw; never run native policy or games.

The optimistic enumeration stops at the first clear. Only the root uses the
saved reachable mask; subsequent legal placements deliberately ignore movement.
Its maximum is an upper bound for reachable first clears in the known window,
not a control certificate, production search budget, or long-term quality bound.
"""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from agents import nextgen_contracts as c
from agents.compact_search import legal_action_indices, transition
from agents.nextgen_shared_search import _pairs, _public_state
from agents.nextgen_survival import inferred_state, value

DIRECTORY = Path(__file__).resolve().parent


def read(path, sources):
    data = path.read_bytes()
    sources[str(path.relative_to(DIRECTORY.parent))] = hashlib.sha256(data).hexdigest()
    return json.loads(gzip.decompress(data) if path.suffix == '.gz' else data)


def first_clear_upper_bound(request, state):
    pairs = _pairs(request.public.own.known_pieces)
    count, maximum, example = 0, 0, []

    def visit(current, path):
        nonlocal count, maximum, example
        if len(path) == len(pairs):
            return
        for action in legal_action_indices(current):
            if not path and not request.execution.reachable_mask[action]:
                continue
            result = transition(current, pairs[len(path)], action)
            count += 1
            if not result.valid or result.game_over:
                continue
            if result.chain_count:
                if result.chain_count > maximum:
                    maximum, example = result.chain_count, [*path, action]
            else:
                visit(result.state, [*path, action])

    visit(state, [])
    return {'maximum_first_clear': maximum, 'example_optimistic_path': example,
            'offline_transition_calls': count, 'known_depth': len(pairs)}


def inspect_fixed(sources):
    raw = read(DIRECTORY / 'bounded-fixed/gtr-128.json.gz', sources)
    rows = []
    for decision, (wire, row) in enumerate(zip(raw['ledger'], raw['rows'], strict=True), 1):
        request = c.NextgenRequest.from_dict(wire['request'])
        batch = c.CandidateBatch.from_dict(wire['batch'])
        state = inferred_state(request, _public_state(request)[0])
        assert state is not None
        pair = _pairs(request.public.own.known_pieces)[0]
        immediate = []
        for action in legal_action_indices(state):
            result = transition(state, pair, action)
            if result.valid and not result.game_over and result.chain_count:
                immediate.append({'root': action, 'chain': result.chain_count,
                                  'reachable': request.execution.reachable_mask[action]})
        survival = row['search']['survival']
        backend = row['search']['backend']
        assert survival['nodes'] <= 128
        assert row['counters']['response_nodes'] <= 256
        assert row['counters']['shared_nodes'] <= 600000
        rows.append({
            'decision': decision, 'action': row['action'], 'actual_chain': raw['chains'][decision - 1],
            'reason': wire['selection']['reason'], 'tactic': wire['selection']['selected_tactic_id'],
            'public_heights': state.column_heights,
            'template_exit': row['phase'].get('exit_reason'),
            'immediate_nonfatal_clears': immediate,
            'known_first_clear_upper_bound': first_clear_upper_bound(request, state),
            'candidate_chain_max': max((value(v, 'chain_count') or 0 for v in batch.candidates), default=0),
            'sampled_chain_max': max((v['chain_count']['maximum'] for v in row['root_evidence']), default=0),
            'survival': survival,
            'shared_nodes': row['counters']['shared_nodes'],
            'shared_budget_exhausted': backend['budget_exhausted'],
            'response_nodes': row['counters']['response_nodes'],
        })
    return {'source': raw['source']['commit'], 'quality': {
        k: raw[k] for k in ('max_chain', 'premature', 'game_over', 'placements')}, 'rows': rows}


def inspect_formal(sources):
    root = DIRECTORY.parent / 'integrated-g2-20260927/native-g2'
    identities, small_clears, equal = [], [], []
    for seed in range(123, 153):
        digests = []
        for repeat in (1, 2):
            raw = read(root / f'seed-{seed}-repeat-{repeat}.json.gz', sources)
            assert raw['max_chain'] == max(raw['chains'], default=0)
            assert raw['premature'] == sum(0 < n < 10 for n in raw['chains'])
            digests.append(raw['semantic_digest'])
            identities.append({'seed': seed, 'repeat': repeat, **{
                k: raw[k] for k in ('max_chain', 'premature', 'game_over', 'placements')}})
            if repeat != 1:
                continue
            for decision, chain in enumerate(raw['chains'], 1):
                if not 0 < chain < 10:
                    continue
                wire, row = raw['ledger'][decision - 1], raw['rows'][decision - 1]
                survival = row['search']['survival']
                small_clears.append({
                    'seed': seed, 'decision': decision, 'chain': chain, 'root': row['action'],
                    'reason': wire['selection']['reason'], 'tactic': wire['selection']['selected_tactic_id'],
                    'template_exit': row['phase'].get('exit_reason'),
                    'survival_active': survival['active'], 'survival_nodes': survival['nodes'],
                    'quiet_witness_roots': [v['action'] for v in survival['roots']
                                            if v['status'] == 'witness' and not v['root_chain']],
                })
        equal.append({'seed': seed, 'repeat_semantic_equal': digests[0] == digests[1]})
    first = [v for v in identities if v['repeat'] == 1]
    return {'scope': 'old source only; not a current-source formal G2 rerun',
            'summary': {'identities': len(identities),
                        'mean_max_chain': sum(v['max_chain'] for v in first) / len(first),
                        'premature': sum(v['premature'] for v in identities),
                        'game_over': sum(v['game_over'] for v in identities),
                        'repeat_equal': sum(v['repeat_semantic_equal'] for v in equal)},
            'identities': identities, 'small_clears_repeat_1': small_clears, 'repeats': equal}


def audit():
    sources = {}
    fixed = inspect_fixed(sources)
    formal = inspect_formal(sources)
    bounds = [r['known_first_clear_upper_bound']['maximum_first_clear'] for r in fixed['rows']]
    assert len(bounds) == 40 and max(bounds) < 10
    assert not any(r['shared_budget_exhausted'] for r in fixed['rows'])
    assert fixed['rows'][-1]['reason'] == 'legitimate_survival_exception'
    return {'scope': 'saved public known inputs; optimistic first-clear enumeration; no policy/game rerun',
            'private_board_used': False, 'quota_or_runtime_changed': False,
            'maximum_known_first_clear_upper_bound': max(bounds),
            'fixed_128': fixed, 'old_formal_g2': formal, 'input_sha256': sources}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = audit()
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'known_first_clear_upper_bound': result['maximum_known_first_clear_upper_bound'],
                      'old_g2': result['old_formal_g2']['summary']}))
