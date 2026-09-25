"""Reproduce the paired PUYO-270 comparison from saved public ledgers.

Run from repository root: PYTHONPATH=. python docs/benchmarks/puyo-270-survival/analyze.py
The counterfactual bounded probe is applied equally to BEFORE and AFTER inputs.
"""
import gzip
import hashlib
import json
import os
import platform
import sys
from collections import Counter
from pathlib import Path

from agents import nextgen_contracts as c
from agents.compact_search import legal_action_indices
from agents.nextgen_shared_search import ResponseBudget, _public_state
from agents.nextgen_survival import probe
from eval.nextgen_gate_benchmark import SafeNoThreatMatch
from eval.nextgen_latency_benchmark import distribution
from puyo_env.nextgen_public_snapshot import TimingProfile, TickInterval

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
BEFORE = ROOT / 'docs/benchmarks/puyo-271-regression/before'
SEEDS = (55, 123, 124)


def read(path):
    return json.loads(gzip.decompress(path.read_bytes()) if path.suffix == '.gz' else path.read_text())


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_run(path):
    run = read(path)
    assert c.semantic_digest(run['semantic']) == run['semantic_digest']
    assert len(run['chains']) == run['placements']
    assert max(run['chains'], default=0) == run['max_chain']
    assert sum(0 < chain < 10 for chain in run['chains']) == run['premature']
    result = {key: run[key] for key in ('policy', 'seed', 'placements', 'max_chain', 'premature', 'game_over', 'semantic_digest', 'decision_seconds')}
    result['path'] = str(path.relative_to(ROOT))
    result['sha256'] = checksum(path)
    if run['policy'] != 'nextgen':
        result['survival_diagnostics'] = 'not_instrumented_deep_chain_reference'
        return result
    timing = TimingProfile.from_match(
        SafeNoThreatMatch(run['seed']), latency_mode='configured', inference_latency_ticks=0,
        timeout_ticks=2**31 - 1,
        operation_cadence=TickInterval(1, 120, 'public_estimate', 'smoke_operation_estimate'))
    rows = []
    for raw in run['ledger']:
        diagnostic = c.Diagnostics.from_dict(raw)
        req, batch, receipt = diagnostic.request, diagnostic.batch, diagnostic.receipt
        assert req.execution.timing_digest == timing.digest
        state, complete = _public_state(req)
        roots, sidecar = probe(req, state, legal_action_indices(state), ResponseBudget(128),
                               timing=timing, board_complete=complete)
        safe = {a for a, value in roots.items() if value.status == 'witness'}
        quiet = {a for a in safe if roots[a].root_chain == 0}
        candidates = {v.root_action for v in batch.candidates if v.root_legal and v.root_reachable}
        selected = roots.get(receipt.requested_action)
        actual = roots.get(receipt.executed_action)
        small = selected is not None and selected.root_chain is not None and 0 < selected.root_chain < 10
        classification = ('unnecessary_given_bounded_quiet_witness' if small and quiet else
                          'legitimate_survival_exception' if small and safe and not quiet and selected.status == 'witness' else
                          'unclassified_small_fire' if small else 'no_small_fire')
        rows.append({
            'decision_id': req.identity.decision_id,
            'probe_status': sidecar['status'], 'probe_nodes': sidecar['nodes'],
            'candidate_witness_gap': bool(safe - candidates),
            'selected_fatal_with_alternative_witness': bool(safe and selected and selected.status == 'fatal'),
            'executed_fatal_with_alternative_witness': bool(safe and actual and actual.status == 'fatal'),
            'witness_not_adopted': bool(selected and selected.status == 'witness' and receipt.outcome != 'activated'),
            'requested_action': receipt.requested_action, 'executed_action': receipt.executed_action,
            'receipt_outcome': receipt.outcome, 'selection_reason': diagnostic.selection.reason,
            'small_fire_classification': classification,
        })
    result['bounded_public_estimate'] = {
        'scope': 'known current/NEXT/NEXT2; geometric future actions; hidden rows estimated; no private-future guarantee',
        'candidate_witness_gap_count': sum(v['candidate_witness_gap'] for v in rows),
        'selected_fatal_with_alternative_witness_count': sum(v['selected_fatal_with_alternative_witness'] for v in rows),
        'executed_fatal_with_alternative_witness_count': sum(v['executed_fatal_with_alternative_witness'] for v in rows),
        'witness_not_adopted_count': sum(v['witness_not_adopted'] for v in rows),
        'classification_counts': dict(Counter(v['small_fire_classification'] for v in rows)),
        'probe_status_counts': dict(Counter(v['probe_status'] for v in rows)),
        'decision_rows': rows,
    }
    return result


def main():
    if sys.argv[1:] == ['--verify']:
        manifest = read(HERE / 'manifest.json')
        for relative, expected in manifest['artifacts_sha256'].items():
            assert checksum(ROOT / relative) == expected, relative
        print('PUYO-270 saved artifacts verified')
        return
    before, after = [], []
    latency = {'before': {}, 'after': {}}
    for phase, directory, result in (('before', BEFORE, before), ('after', HERE / 'paired', after)):
        declaration, summary = read(directory / 'declaration.json'), read(directory / 'summary.json')
        assert declaration == summary['declaration'] and not summary['source_changed_during_run']
        assert declaration['seeds'] == list(SEEDS) and declaration['repeats'] == 1
        for policy in ('nextgen', 'deep_chain'):
            times = []
            for seed in SEEDS:
                path = directory / f'{policy}-{seed}-1.json.gz'
                result.append(inspect_run(path))
                times.extend(v['seconds'] for v in read(path)['rows'])
            latency[phase][policy] = distribution(times)
    action_matches = {}
    for policy in ('nextgen', 'deep_chain'):
        for seed in SEEDS:
            name = f'{policy}-{seed}-1.json.gz'
            old, new = read(BEFORE / name), read(HERE / 'paired' / name)
            action_matches[f'{policy}/{seed}'] = [r['action'] for r in old['rows']] == [r['action'] for r in new['rows']]
    rejected = [inspect_run(path) for path in sorted((HERE / 'rejected-9284bf6').glob('*.json.gz'))]
    report = {
        'schema': 'puyo.270.survival_comparison.v1',
        'before_source': read(BEFORE / 'declaration.json')['source']['commit'],
        'after_source': read(HERE / 'paired/declaration.json')['source']['commit'],
        'conditions': '3 prespecified seeds x 1 repeat; max40 placements; configured0; inactive opponent; native scenario-6; unchanged quotas',
        'native': read(BEFORE / 'regression_manifest.json')['input_conditions']['native'],
        'native_reuse': 'same read-only c0c77d9 binary; native source diff is empty; Python source differs as declared',
        'host': {'platform': platform.platform(), 'python': platform.python_version(),
                 'logical_cpus': os.cpu_count(), 'affinity': sorted(os.sched_getaffinity(0))},
        'before': before, 'after': after, 'decision_seconds': latency,
        'before_after_actions_identical': action_matches,
        'intermediate_run': {
            'source': '1b7acdfd43143af5c3746091344dd1bc3f1f63f7',
            'status': 'superseded by single common selector boundary; six runs complete',
            'summary': read(HERE / 'intermediate-1b7acdf/summary.json'),
        },
        'rejected_run': {'source': '9284bf604ef292f21d745385ed96e3ce57a9ace7',
                         'status': 'interrupted after nextgen3; no completed deep_chain run; no complete summary',
                         'reason': 'safe 12-chain fire_main was incorrectly vetoed at seed55 decision26, causing later suffocation',
                         'results': rejected},
        'formal_G2': 'not_run; PUYO-266 owns acceptance',
        'GUI_QA': 'not_run; PUYO-269/273 owns cadence',
    }
    (HERE / 'comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    import _puyo_deep_chain_native as native
    from agents.deep_chain_native import NativeDeepChainBackend
    binary = next(Path(native.__file__).parent.glob('*.so'))
    artifacts = [HERE / 'comparison.json', Path(__file__), ROOT / 'tests/test_nextgen_survival.py']
    for directory in ('paired', 'intermediate-1b7acdf', 'rejected-9284bf6'):
        artifacts.extend(path for path in (HERE / directory).iterdir() if path.is_file())
    artifacts.extend(BEFORE / f'{policy}-{seed}-1.json.gz' for policy in ('nextgen', 'deep_chain') for seed in SEEDS)
    manifest = {
        'schema': 'puyo.270.survival_manifest.v1',
        'adopted_source': report['after_source'],
        'base': 'c511598fccc76cbfda6663f08d40d0144d3ead85',
        'command': 'python -m eval.nextgen_safe_build_diagnostic --output <new-dir> --profile nextgen_safe_build --repeats 1',
        'python': sys.executable,
        'native': {'path': str(binary), 'sha256': checksum(binary),
                   'capabilities': NativeDeepChainBackend(canonical=True).capabilities.to_dict()},
        'artifacts_sha256': {str(path.relative_to(ROOT)): checksum(path) for path in sorted(set(artifacts))},
        'limits': ['diagnostic3x1, not formal G2', 'public hidden rows and future movement are estimates',
                   'new requests revalidate authoritative root reachability'],
    }
    (HERE / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'quality': [{k: row[k] for k in ('policy', 'seed', 'max_chain', 'premature', 'game_over')} for row in after],
                      'decision_seconds': latency}, indent=2))


if __name__ == '__main__':
    main()
