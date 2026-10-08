"""Recheck saved PUYO-268 evidence without running policy search or native code."""
import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from agents import deep_chain_builder as reference
from agents import nextgen_contracts as c
from agents.nextgen_profiles import nextgen_search_settings
from agents.nextgen_shared_search import _pairs, _public_state, _sequences
from eval.puyo_268_public_reference import estimated_state


def load(path):
    return json.loads(gzip.decompress(path.read_bytes()) if path.suffix == '.gz' else path.read_bytes())


def verify(run_dir):
    summary = load(run_dir / 'summary.json')
    if summary['source_changed']:
        raise ValueError('measurement source changed')
    for name, expected in summary['sha256'].items():
        if hashlib.sha256((run_dir / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f'artifact checksum mismatch: {name}')
    decisions = 0
    rejected_shadows = 0
    incomplete_references = 0
    for row in summary['rows']:
        seed = row['seed']
        nextgen = load(run_dir / f'nextgen-{seed}.json.gz')
        for name, file in [('nextgen', f'nextgen-{seed}.json.gz'), ('public_reference', f'reference-{seed}.json.gz')]:
            raw = load(run_dir / file)
            metrics = row['metrics'][name]
            for field, value in [('max_chain', max(raw['chains'], default=0)),
                                 ('premature', sum(0 < chain < 10 for chain in raw['chains'])),
                                 ('placements', len(raw['chains']))]:
                if raw[field] != value or metrics[field] != value:
                    raise ValueError(f'raw metric mismatch: {seed}/{name}/{field}')
            if name == 'public_reference' and not raw['completed_requested_placements']:
                incomplete_references += 1
                if raw['completion_status'] != 'rejected_unreachable_action' or not raw['errors']:
                    raise ValueError('incomplete reference lacks failure evidence')
            if len(raw['rows']) != len(raw['chains']):
                raise ValueError('decision/placement alignment needs explicit audit')
        pairs = load(run_dir / f'exact-input-{seed}.json.gz')
        if len(pairs) != row['paired_decisions'] or len(pairs) != len(nextgen['ledger']):
            raise ValueError('missing paired decision')
        for pair, ledger in zip(pairs, nextgen['ledger'], strict=True):
            if pair['request'] != ledger['request'] or pair['receipt'] != ledger['receipt']:
                raise ValueError('pair detached from actual request/receipt')
            request = c.NextgenRequest.from_dict(pair['request'])
            public = _public_state(request)[0]
            record = pair['reference_input']
            estimate = estimated_state(reference.build_visible_runtime_input(record['observation'], record['info']))
            search = pair['reference_search']
            if public != estimate or hashlib.sha256(public.to_bytes()).hexdigest() != search['state_sha256']:
                raise ValueError('public state mismatch')
            if record['observation']['ghost_row'] is not None or search['public_board_complete']:
                raise ValueError('unknown rows claimed observed')
            if search['known_pairs'] != [[v.name for v in p] for p in _pairs(request.public.own.known_pieces)]:
                raise ValueError('known pieces mismatch')
            if search['action_mask'] != list(request.execution.reachable_mask):
                raise ValueError('reachability mismatch')
            config = nextgen_search_settings('nextgen_safe_build', seed=seed)[1]
            if [v['sequence_digest'] for v in search['scenario_sequences']] != [v.sequence_digest for v in _sequences(request.public.own.known_pieces, config)]:
                raise ValueError('sampled scenario mismatch')
            if request.control.search_profile.shared_quota != search['search_config']['max_expanded_nodes']:
                raise ValueError('shared budget mismatch')
            executable = request.execution.reachable_mask[pair['reference_action']]
            if pair['reference_action_executable'] != executable:
                raise ValueError('reference rejection differs from input mask')
            if executable == bool(pair['reference_selection_error']):
                raise ValueError('reference rejection reason missing or spurious')
            rejected_shadows += not executable
            decisions += 1
    print(json.dumps({'artifact_hashes': len(summary['sha256']), 'paired_decisions': decisions,
                      'seeds': [v['seed'] for v in summary['rows']], 'evidence_verified': True,
                      'rejected_shadow_decisions': rejected_shadows,
                      'incomplete_reference_runs': incomplete_references,
                      'quality_pass': False, 'formal_g2': 'unchanged: observed FAIL / BLOCKED'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, default=Path(__file__).resolve().parent)
    verify(parser.parse_args().run_dir)
