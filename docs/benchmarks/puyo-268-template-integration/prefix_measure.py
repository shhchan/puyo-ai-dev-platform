"""Fixed-budget public-prefix diagnostic; separate from human GUI QA and G2."""
import argparse
import gzip
import hashlib
import json
import os
import platform
import sys
import time
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from agents import nextgen_contracts as c
from agents.nextgen_shared_search import SharedSearchBatchBuilder, PreparedTemplateSearch
from agents.template_catalog import match_templates
from eval.nextgen_safe_build_diagnostic import make_policy, measure
from eval.nextgen_realtime_diagnostic import source_identity
from puyo_env.actions import PLACEMENT_ACTIONS


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.write_bytes(gzip.compress(json.dumps(value, allow_nan=False).encode(), mtime=0))


def summarize(run):
    first = [r for r in run['rows'] if r['phase']['phase_id'] == 'template-phase-1']
    completion = next((i for i, r in enumerate(first) if r['phase']['exit_reason'] == 'completed'), None)
    return {k: run[k] for k in ('seed', 'max_chain', 'premature', 'game_over', 'placements', 'decision_seconds', 'errors')} | {
        'initial_template_completed_at_placements': completion,
        'initial_phase_exit': first[-1]['phase']['exit_reason'],
        'tactics': dict(Counter(r['selection']['selected_tactic_id'] for r in run['rows'])),
        'receipts': dict(Counter(r['receipt']['outcome'] for r in run['ledger'])),
        'actions': [r['action'] for r in run['rows']],
        'placements_geometry': [{'column': PLACEMENT_ACTIONS[r['action']].axis_x + 1,
                                'rotation': PLACEMENT_ACTIONS[r['action']].rotation.name} for r in run['rows']],
        'maximum_visible_height': max((14 - y for l in run['ledger'] for y, row in enumerate(l['request']['public']['own']['visible_board']) if any(v for v in row if v is not None)), default=0),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    import _puyo_deep_chain_native as native
    source = source_identity()
    baseline = {seed: json.load(gzip.open(args.baseline_dir / f'nextgen-{seed}-1.json.gz')) for seed in (55, 123, 124)}
    policy = make_policy('nextgen', 123, 'nextgen_safe_build')
    request = c.NextgenRequest.from_dict(baseline[123]['ledger'][5]['request'])
    raw_key = baseline[123]['rows'][5]['phase']['selected_key']
    key = (*raw_key[:3], tuple(tuple(v) for v in raw_key[3]))
    result = match_templates(policy.catalog, request.public.own.visible_board, request.public.own.known_pieces,
                            node_budget=128, binding_budget=4096, preferred_key=key,
                            reachable_mask=request.execution.reachable_mask, prioritize_static_binding=True,
                            evaluate_prefix_progress=True)
    exact = []
    for mode in ('immediate_only_ablation', 'public_prefix', 'unconstrained_reference'):
        current = request if mode != 'unconstrained_reference' else replace(request, control=replace(request.control, phase=replace(request.control.phase, active=False)))
        matched = result if mode != 'immediate_only_ablation' else replace(result, candidates=tuple(replace(v, root_prefix_progress=()) for v in result.candidates))
        builder = SharedSearchBatchBuilder(policy.backend, policy.search_config, template_catalog=policy.catalog)
        kwargs = {'template_key': key}
        if mode != 'unconstrained_reference':
            kwargs['prepared_template'] = PreparedTemplateSearch(c.semantic_digest(current), key, matched, 0.)
        for repeat in range(4):
            started = time.perf_counter()
            ex = builder.build(current, **kwargs)
            elapsed = time.perf_counter() - started
            if repeat == 0:
                continue
            tactic = 'build_main' if mode == 'unconstrained_reference' else 'build_template'
            exact.append({'mode': mode, 'repeat': repeat, 'seconds_excluding_matcher': elapsed,
                          'request': current.to_dict(), 'batch': ex.batch.to_dict(), 'search': ex.diagnostics,
                          'chosen_tactic': tactic, 'chosen_action': ex.select(tactic).root_action,
                          'backend_digest': ex.shared_result.deterministic_digest})
    write(args.output / 'exact-request-6.json.gz', {'baseline_source_sha256': sha(args.baseline_dir/'nextgen-123-1.json.gz'),
                                                   'baseline_row': baseline[123]['rows'][5],
                                                   'baseline_ledger': baseline[123]['ledger'][5], 'comparisons': exact})
    summaries = []
    for seed in (55, 123, 124):
        run = measure('nextgen', seed, 'nextgen_safe_build', placements=40)
        write(args.output / f'nextgen-{seed}-40.json.gz', run)
        summaries.append(summarize(run))
    summary = {'source': source, 'source_changed': source_identity() != source,
               'native_binary_sha256': sha(native.__file__), 'native': policy.backend.describe(),
               'script_sha256': sha(__file__), 'python': sys.executable, 'platform': platform.platform(),
               'affinity': sorted(os.sched_getaffinity(0)), 'profile': policy.profile.to_dict(),
               'search_config': asdict(policy.search_config), 'baseline': [summarize(baseline[s]) for s in (55,123,124)],
               'baseline_artifacts_sha256': {p.name: sha(p) for p in args.baseline_dir.glob('nextgen-*-1.json.gz')},
               'after': summaries, 'exact': [{k: r[k] for k in ('mode','repeat','seconds_excluding_matcher','chosen_action','backend_digest')} for r in exact],
               'limits': ['Original human GUI replay was not saved; these are new machine runs.',
                          'Configured zero inference ticks, inactive opponent; no human 0.25x or random GUI result is mixed in.',
                          'Baseline trajectories are saved PUYO-271 after evidence, not fresh paired timing measurements.',
                          'Exact-input ablation uses the same matcher quota and removes only prefix evidence from ranking.',
                          'Unconstrained reference is a shadow backend decision, not an executed reference-policy trajectory.',
                          'Three seeds are diagnostic; formal G2 belongs to PUYO-266.']}
    summary['artifacts_sha256'] = {p.name: sha(p) for p in args.output.glob('*.gz')}
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')


if __name__ == '__main__':
    main()
