"""Recompute the small desktop cohort, without policy search or gate promotion."""
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def summary():
    pairs = []
    for path in sorted((ROOT / 'before').glob('*.json.gz')):
        if '.locks.' in path.name:
            continue
        before = read(path)
        proposed = ROOT / 'rejected-d1060ba' / path.name
        if not proposed.exists():
            continue
        after = read(proposed)
        locks = [read(ROOT / variant / path.name.replace('.json.gz', '.locks.json.gz'))
                 for variant in ('before', 'rejected-d1060ba')]
        assert all(v['final_hash_matches'] and not v['lock_mismatch_decisions'] for v in locks)
        assert all(r['lock_matches_requested_root'] for v in locks for r in v['records'])
        assert all(r['resolution']['chain_count'] == r['offline_full_board_predicted_chain']
                   for v in locks for r in v['records'])
        assert not before['source_changed'] and not after['source_changed']
        assert before['build'] == after['build']
        assert before['profile'] == after['profile']
        assert before['search_config'] == after['search_config']
        assert before['driver_sha256'] == after['driver_sha256']
        assert before['ledger'][0]['request']['public'] == after['ledger'][0]['request']['public']
        common = list(zip(before['ledger'], after['ledger']))
        assert all(b['request']['public']['own']['known_pieces'] == a['request']['public']['own']['known_pieces']
                   for b, a in common)
        first_difference = next((i for i, (b, a) in enumerate(common)
                                 if b['receipt']['executed_action'] != a['receipt']['executed_action']), None)
        change = None
        if first_difference is not None:
            b, a = common[first_difference]
            change = {
                'decision': first_difference + 1,
                'same_public_input': b['request']['public'] == a['request']['public'],
                'same_reachable_mask': b['request']['execution']['reachable_mask'] == a['request']['execution']['reachable_mask'],
                'before_action': b['receipt']['executed_action'],
                'after_action': a['receipt']['executed_action'],
                'before_selection': b['selection'], 'after_selection': a['selection'],
                'after_receipt': a['receipt'],
                'after_actual_resolution': locks[1]['records'][first_difference].get('resolution'),
            }
        pairs.append({
            'identity': path.name.removesuffix('.json.gz'),
            'before': {k: before[k] for k in ('max_chain', 'premature', 'game_over', 'placements', 'decision_seconds')},
            'after': {k: after[k] for k in ('max_chain', 'premature', 'game_over', 'placements', 'decision_seconds')},
            'common_public_piece_windows': len(common), 'first_action_difference': change,
            'same_inputs_and_final_hash': before['semantic']['inputs'] == after['semantic']['inputs']
            and before['semantic']['final_hash'] == after['semantic']['final_hash'],
            'lock_mismatches': [v['lock_mismatch_decisions'] for v in locks],
            'survival_fire_receipts': [
                {'decision': i, 'action': d['receipt']['executed_action'],
                 'outcome': d['receipt']['outcome'], 'reason': d['receipt']['reason'],
                 'resolution': locks[1]['records'][i - 1].get('resolution')}
                for i, d in enumerate(after['ledger'], 1)
                if d['selection']['reason'] == 'legitimate_survival_exception'
            ],
        })
    return {
        'quality_status': 'observed quality FAIL / formal G2 BLOCKED retained',
        'proposal_status': 'rejected: GTR123 maximum chain 10 to 3, premature 0 to 2; runtime reverted',
        'unmeasured_proposal_identities': ['gtr-144', 'daa-55', 'persian-55', 'daa-random-59-step'],
        'pairs': pairs,
    }


if __name__ == '__main__':
    result = summary()
    expected = ROOT / 'comparison.json'
    if expected.exists():
        assert result == json.loads(expected.read_text())
    else:
        expected.write_text(json.dumps(result, indent=2) + '\n')
    manifest = ROOT / 'sha256.json'
    if manifest.exists():
        for name, digest in json.loads(manifest.read_text()).items():
            assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    print('Verified', len(result['pairs']), 'paired identities and actual locks')
