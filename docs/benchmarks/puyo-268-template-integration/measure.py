"""Exact-public-input backend comparison plus a short real scheduler rollout.

Run from the repository root using its dedicated release-native environment.
This diagnostic does not replace PUYO-271 paired quality evaluation or G2.
"""
import argparse
import gzip
import hashlib
import json
import os
import platform
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from agents import nextgen_contracts as c
from agents.compact_search import transition
from agents.deep_chain_search_backend import NativeLongHorizonSearchBackend
from agents.nextgen_profiles import nextgen_search_settings
from agents.nextgen_shared_search import SharedSearchBatchBuilder, _pairs, _public_state
from eval.nextgen_latency_benchmark import distribution
from eval.nextgen_realtime_diagnostic import source_identity
from eval.nextgen_safe_build_diagnostic import measure
from tests.test_template_preserving_integration import CORPUS, key, make_request, selected_catalog


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    import _puyo_deep_chain_native as native
    source = source_identity()
    profile, cfg = nextgen_search_settings(seed=55)
    native_backend = NativeLongHorizonSearchBackend()
    cat = selected_catalog('persian')
    cases = [*CORPUS['persian_counterexamples'],
             {'id': 'persian_other_color_support', 'rows_bottom_up': ['110000', '400000']},
             {'id': 'persian_public_completion', 'rows_bottom_up': ['111333', '023200', '002000']}]
    results = []
    for case in cases:
        known = ((2, 3), (3, 2), (2, 1)) if case['id'].endswith('completion') else ((1, 3), (2, 3), (2, 2))
        req = make_request(cat, cfg, case['rows_bottom_up'], known=known,
                           quota=profile.shared_quota, unknown=True)
        req = replace(req, control=replace(req.control, search_profile=profile))
        public_state, _ = _public_state(req)
        for constrained in (False, True):
            current = req if constrained else replace(req, control=replace(req.control, phase=replace(req.control.phase, active=False)))
            builder = SharedSearchBatchBuilder(native_backend, cfg, template_catalog=cat)
            builder.build(current, template_key=key(cat))  # warmup; no cache
            repeats, times = [], []
            for _ in range(3):
                started = time.perf_counter()
                ex = builder.build(current, template_key=key(cat))
                times.append(time.perf_counter()-started)
                tactic = 'build_template' if constrained and ex.batch.tactics[1].available else 'build_main'
                chosen = ex.select(tactic)
                resolved = transition(public_state, _pairs(known)[0], chosen.root_action)
                repeats.append({
                    'batch': ex.batch.to_dict(), 'search': ex.diagnostics,
                    'chosen_tactic': tactic, 'chosen_action': chosen.root_action,
                    'current_chain': resolved.chain_count,
                    'current_game_over': resolved.game_over,
                    'long_horizon_digest': ex.shared_result.deterministic_digest(),
                    'maximum_search_chain': max((v.chain_count for r in ex.shared_result.ranked_roots
                        for scenario in r.scenario_values for v in (scenario.best_fire,) if v), default=0),
                })
            results.append({'case': case['id'], 'constrained': constrained,
                            'request': current.to_dict(), 'decision_seconds': distribution(times), 'repeats': repeats})
    (args.output/'fixtures.json.gz').write_bytes(gzip.compress(json.dumps(results).encode(), mtime=0))
    # A real public-only nextgen scheduler run. The exact-input reference above
    # is a shadow decision comparison, not an executed reference trajectory.
    solo = measure('nextgen', 55, 'nextgen_safe_build', placements=20)
    (args.output/'nextgen-55-20.json.gz').write_bytes(gzip.compress(json.dumps(solo).encode(), mtime=0))
    summary = {
        'source': source, 'source_changed': source_identity() != source,
        'script_sha256': sha(__file__), 'fixture_sha256': sha(ROOT/'tests/fixtures/puyo_271_regression_cases.json'),
        'native': native_backend.describe(), 'native_binary_sha256': sha(native.__file__),
        'python': sys.executable, 'platform': platform.platform(),
        'affinity': sorted(os.sched_getaffinity(0)), 'profile': profile.to_dict(), 'search_config': asdict(cfg),
        'comparisons': [{k:v for k,v in row.items() if k not in ('request','repeats')} |
                        {'actions': [r['chosen_action'] for r in row['repeats']],
                         'tactics': [r['chosen_tactic'] for r in row['repeats']],
                         'current_chains': [r['current_chain'] for r in row['repeats']],
                         'deterministic': len({r['long_horizon_digest'] for r in row['repeats']}) == 1}
                        for row in results],
        'solo': {k:solo[k] for k in ('placements','max_chain','premature','game_over','decision_seconds','errors')},
        'solo_phase_exits': [r.get('phase') for r in solo['rows']],
        'limits': ['Fixtures compare exact public board/pairs, identical native binary and fixed shared quota.',
                   'Reference is the unconstrained deep-chain backend using the same public estimator/config/scenarios, not a full reference-policy trajectory.',
                   'Solo is one nextgen seed and 20 placements; actual chains and receipts must not be attributed to shadow reference.',
                   'Three-seed full quality comparison belongs to PUYO-271; human GUI QA and formal G2 remain unverified.'],
    }
    summary['artifacts_sha256'] = {p.name:sha(p) for p in args.output.glob('*.gz')}
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')


if __name__ == '__main__':
    main()
