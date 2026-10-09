"""Audit the saved five-seed bounded-prefix cohort; no policy rerun."""
import argparse
import gzip
import json
from pathlib import Path

from agents import nextgen_contracts as c
from agents.nextgen_shared_search import _public_state
from agents.nextgen_survival import inferred_state
from eval.nextgen_adoption_replay import replay_adoptions


def read(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def audit(root):
    rows = []
    for seed in (55, 123, 124, 126, 128):
        raw = read(root / f'gtr-{seed}.json.gz')
        locks = read(root / f'gtr-{seed}.locks.json.gz')
        previous = 'inference-v1-fixed' if seed == 128 else 'fire-eligible-fixed'
        old = read(root.parent / previous / f'gtr-{seed}.json.gz')
        first_changed = next((i for i, (a, b) in enumerate(zip(old['rows'], raw['rows']), 1)
                              if a['action'] != b['action']), None)
        prefix_count = first_changed or min(len(old['rows']), len(raw['rows']))
        # Runtime paths contain coordinate tuples; JSON persists them as arrays.
        assert json.loads(json.dumps(replay_adoptions(raw))) == locks
        hidden, quota, alternate, clears = [], [], [], []
        maxima = dict(survival=0, response=0, shared=0, template=0)
        for index, (wire, row, lock) in enumerate(zip(raw['ledger'], raw['rows'], locks['records'], strict=True), 1):
            request = c.NextgenRequest.from_dict(wire['request'])
            inferred = inferred_state(request, _public_state(request)[0])
            hidden.append({'decision': index, 'known': inferred is not None,
                           'offline_planes_match': inferred is not None and inferred.planes == tuple(lock['offline_full_planes'])})
            counters = wire['batch']['counters']
            survival = row['search']['survival']
            for key, value in (('survival', survival['nodes']), ('response', counters['response_nodes']),
                               ('shared', counters['shared_nodes']), ('template', counters['template_nodes'])):
                maxima[key] = max(maxima[key], value)
            if (survival['nodes'] > 128 or counters['response_nodes'] > 256
                    or counters['shared_nodes'] > 600000 or counters['template_nodes'] > 128):
                quota.append(index)
            if 'alternate_prefix' in survival.get('control_proof', {}):
                proof = survival['control_proof']['alternate_prefix']
                assert proof['actual_nodes'] <= proof['logical_nodes'] <= 128
                assert sum(proof['charged'].values()) <= survival['reused_transition_nodes']
                alternate.append({'decision': index, 'action': row['action'],
                                  'proof': survival['control_proof'], 'nodes': survival['nodes']})
            if raw['chains'][index - 1]:
                clears.append({'decision': index, 'chain': raw['chains'][index - 1],
                               'remaining_locks': raw['placements'] - index})
        rows.append({'seed': seed, 'source': raw['source']['commit'], 'source_changed': raw['source_changed'],
                     **{k: raw[k] for k in ('max_chain', 'premature', 'game_over', 'placements', 'decision_seconds')},
                     'inference': hidden, 'quota_violations': quota, 'max_nodes': maxima,
                     'alternate': alternate, 'clears': clears,
                     'comparison': {'baseline': previous, 'source': old['source']['commit'],
                                    'same_build': raw['build'] == old['build'],
                                    'same_profile': raw['profile'] == old['profile'],
                                    'same_search_config': raw['search_config'] == old['search_config'],
                                    'first_action_change': first_changed,
                                    'prefix_decisions': prefix_count,
                                    'public_equal_through_first_change': all(
                                        a['request']['public'] == b['request']['public']
                                        for a, b in zip(old['ledger'][:prefix_count], raw['ledger'][:prefix_count])),
                                    'same_inputs': raw['semantic']['inputs'] == old['semantic']['inputs'],
                                    'same_final_hash': raw['semantic']['final_hash'] == old['semantic']['final_hash']},
                     'lock_count': len(locks['records']), 'lock_mismatches': locks['lock_mismatch_decisions'],
                     'unlocked': [r['decision'] for r in locks['records'] if not r.get('lock')],
                     'final_hash_replayed': locks['final_hash_matches'],
                     'final_hash': raw['semantic']['final_hash']})
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cohort', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    rows = audit(args.cohort)
    args.output.write_text(json.dumps(rows, indent=2) + '\n')
    print([{k: row[k] for k in ('seed', 'max_chain', 'premature', 'game_over', 'placements', 'quota_violations', 'lock_mismatches')} for row in rows])
