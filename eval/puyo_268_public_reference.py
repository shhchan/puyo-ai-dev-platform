"""PUYO-268 public-estimate reference comparison; never a formal G2 gate.

The benchmark-only adapter preserves the deep_chain_builder reference flow,
except for its observation-to-state and scenario-seed boundaries. Unknown top
rows remain explicitly unknown in evidence; both searches use the same empty
estimate for those cells. Run sequentially: scoped patches are not thread safe.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import os
import platform
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from agents import deep_chain_builder as reference
from agents import nextgen_contracts as c
from agents.nextgen_shared_search import _pairs, _public_state, _sequences, scenario_provenance
from agents.nextgen_profiles import nextgen_search_settings
from agents.template_catalog import compile_selected_template
from eval import nextgen_safe_build_diagnostic as diagnostic
from eval.nextgen_realtime_diagnostic import source_identity

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_STATE = reference._compact_state_from_observation
ORIGINAL_POLICY = diagnostic.make_policy


def public_observation(observation, info):
    """Drop hidden rows and non-public score bookkeeping before reference use."""
    board = reference._plain_nested(observation.get('own_board', observation.get('board')))
    board = copy.deepcopy(board)
    if reference._shape(board) != (6, 13, 6):
        raise ValueError('reference observation must have 6 x 13 x 6 board')
    for channel in board:
        channel[0] = [0] * 6  # row 13 is also outside nextgen's 12 visible rows
    return {
        'own_board': board, 'ghost_row': None,
        'next_pairs': copy.deepcopy(observation['next_pairs']),
    }, {
        'action_mask': list(info['action_mask']),
        'all_clear_bonus_pending': bool(info.get('all_clear_bonus_pending', False)),
    }


def estimated_state(observation):
    # Zero is an estimate, not evidence that unknown cells are empty.
    return ORIGINAL_STATE(replace(observation, ghost_row=[[0] * 6 for _ in range(6)]))


def observation_from_request(request):
    state, _ = _public_state(request)
    grid = state.to_color_grid()
    colors = reference.VISIBLE_PAIR_COLORS + (reference.PuyoColor.OJAMA,)
    board = [[[int(grid[y][x] == color) for x in range(6)]
              for y in range(12, -1, -1)] for color in colors]
    return {'own_board': board, 'next_pairs': [[v.name for v in pair]
                                             for pair in _pairs(request.public.own.known_pieces)]}, {
        'action_mask': request.execution.reachable_mask,
        'all_clear_bonus_pending': request.public.own.all_clear_bonus_pending,
    }


class PublicReferencePolicy(reference.DeepChainBuilderPolicy):
    """Evaluation adapter; no production policy or ABI changes."""

    def __init__(self, seed, *, backend='native'):
        super().__init__(profile='reference', backend=backend)
        self.public_config = nextgen_search_settings('nextgen_safe_build', seed=seed)[1]
        self.public_inputs = []
        self.search_evidence = []

    @contextmanager
    def boundary(self):
        with patch.object(reference, '_compact_state_from_observation', estimated_state), \
             patch.object(reference, '_visible_decision_seed', return_value=self.public_config.resolved_decision_seed):
            yield

    def decision_input_identity(self, observation, info):
        observation, info = public_observation(observation, info)
        with self.boundary():
            return super().decision_input_identity(observation, info)

    def decide(self, observation, info):
        observation, info = public_observation(observation, info)
        with self.boundary():
            context = super().decide(observation, info)
        visible = context.require(reference.NORMALIZED_OBSERVATION_ARTIFACT)
        payload = context.require(reference.SCENARIO_SEARCH_RESULTS_ARTIFACT)
        result = payload['result']
        self.public_inputs.append({'observation': observation, 'info': info})
        self.search_evidence.append({
            'state_sha256': hashlib.sha256(payload['root_state'].to_bytes()).hexdigest(),
            'known_pairs': [[v.name for v in p] for p in reference._decode_visible_pairs(visible.next_pairs)],
            'action_mask': list(visible.action_mask),
            'scenario_sequences': [s.to_dict() for s in result.scenario_sequences],
            'backend_digest': result.deterministic_digest,
            'backend': payload['backend'], 'public_board_complete': False,
            'source': 'public_estimate', 'search_config': asdict(self.public_config),
        })
        return context


def write_json(path, value):
    data = (json.dumps(value, indent=2, allow_nan=False) + '\n').encode()
    path.write_bytes(gzip.compress(data, mtime=0) if path.suffix == '.gz' else data)


def fixed_key(run):
    key = run['rows'][0]['phase']['selected_key']
    return (*key[:3], tuple(tuple(v) for v in key[3]))


def completion_at_public_input(states, constraint):
    for index, state in enumerate(states[:15]):
        compatible, complete = constraint.evaluate(state, state)
        if compatible and complete:
            return index
    return None


def compare_request(request, policy):
    if request.control.scenario_provenance != scenario_provenance(request.public.own.known_pieces, policy.public_config):
        raise ValueError('nextgen scenario provenance mismatch')
    for field in ('depth', 'width', 'scenarios', 'max_expanded_nodes'):
        if getattr(policy.profile, field) != getattr(policy.public_config, field):
            raise ValueError(f'reference search budget mismatch: {field}')
    observation, info = observation_from_request(request)
    context = policy.decide(observation, info)
    evidence = policy.search_evidence[-1]
    state, complete = _public_state(request)
    expected = {
        'state_sha256': hashlib.sha256(state.to_bytes()).hexdigest(),
        'known_pairs': [[v.name for v in pair] for pair in _pairs(request.public.own.known_pieces)],
        'action_mask': list(request.execution.reachable_mask),
    }
    for name, value in expected.items():
        if evidence[name] != value:
            raise ValueError(f'exact public input mismatch: {name}')
    # Source labels describe adapters and legitimately differ; sequence values
    # and scenario identities must match, including every sampled unknown pair.
    expected_sequences = _sequences(request.public.own.known_pieces, policy.public_config)
    actual_sequences = context.require(reference.SCENARIO_SEQUENCES_ARTIFACT)
    if [s.sequence_digest for s in actual_sequences] != [s.sequence_digest for s in expected_sequences]:
        raise ValueError('scenario sequence mismatch')
    profile = request.control.search_profile
    if profile.shared_quota != policy.profile.max_expanded_nodes:
        raise ValueError('shared node budget differs from reference')
    return {
        'request': request.to_dict(), 'reference_input': policy.public_inputs[-1],
        'reference_search': evidence, 'input_equal': True, 'scenario_equal': True,
        'shared_budget_equal': True, 'public_board_complete': complete,
        'reference_action': context.require(reference.SELECTED_ACTION_ARTIFACT),
        'reference_seconds': context.trace.elapsed_seconds,
    }


def run(output, seeds, placements):
    output.mkdir(parents=True, exist_ok=False)
    import _puyo_deep_chain_native as native
    source = source_identity()
    declaration = {
        'source': source, 'seeds': seeds, 'placements': placements,
        'native_binary_sha256': hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'platform': platform.platform(), 'affinity': sorted(os.sched_getaffinity(0)),
        'threads': {k: os.environ.get(k) for k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'RAYON_NUM_THREADS')},
        'scope': 'fixed three-seed diagnostic; not G2 or quality PASS',
        'comparison': 'same public estimate, known pairs, reachable mask, scenario sequences, shared budget at every nextgen decision',
        'reference': 'deep_chain_builder reference flow with evaluation-only public-state/seed normalization',
        'limits': ['Unknown top two rows are estimated empty in both searches, never proven empty.',
                   'Live rollouts diverge after different actions; exact paired decisions are reference shadows on each nextgen input.',
                   'Shadow reference actions are not executed and do not supply actual chain outcomes.',
                   'Template/response budgets 128/256 are nextgen-only and reported separately; shared search is 600000 nodes for both.',
                   'Reference has no template phase; its template metric is a passive observer of the initial nextgen binding.',
                   'Existing G2 observed quality FAIL and G2 BLOCKED remain unchanged.'],
    }
    write_json(output / 'declaration.json', declaration)
    summaries = []
    for seed in seeds:
        nextgen = diagnostic.measure('nextgen', seed, 'nextgen_safe_build', placements=placements)
        write_json(output / f'nextgen-{seed}.json.gz', nextgen)
        shadow = PublicReferencePolicy(seed)
        pairs = []
        for row, ledger in zip(nextgen['rows'], nextgen['ledger'], strict=True):
            request = c.NextgenRequest.from_dict(ledger['request'])
            pair = compare_request(request, shadow)
            pair.update(nextgen_action=row['action'], nextgen_seconds=row['seconds'],
                        nextgen_search=row['search'], nextgen_counters=row['counters'],
                        receipt=ledger['receipt'])
            pairs.append(pair)
        write_json(output / f'exact-input-{seed}.json.gz', pairs)
        live = PublicReferencePolicy(seed)
        def factory(kind, current_seed, profile):
            if kind != 'deep_chain' or current_seed != seed:
                raise ValueError('unexpected policy request')
            return live
        with patch.object(diagnostic, 'make_policy', side_effect=factory):
            actual_reference = diagnostic.measure('deep_chain', seed, 'nextgen_safe_build', placements=placements)
        actual_reference.update(public_inputs=live.public_inputs, public_search=live.search_evidence)
        write_json(output / f'reference-{seed}.json.gz', actual_reference)
        cat = ORIGINAL_POLICY('nextgen', seed, 'nextgen_safe_build').catalog
        constraint = compile_selected_template(cat, fixed_key(nextgen))
        nextgen_states = [_public_state(c.NextgenRequest.from_dict(v['request']))[0] for v in nextgen['ledger']]
        reference_states = [estimated_state(reference.build_visible_runtime_input(v['observation'], v['info']))
                            for v in live.public_inputs]
        metrics = {}
        for name, raw, states in [('nextgen', nextgen, nextgen_states), ('public_reference', actual_reference, reference_states)]:
            metrics[name] = {k: raw[k] for k in ('max_chain', 'premature', 'game_over', 'placements', 'decision_seconds', 'errors', 'semantic_digest')}
            metrics[name]['initial_binding_completed_within_14_at'] = completion_at_public_input(states, constraint)
        summaries.append({'seed': seed, 'metrics': metrics, 'paired_decisions': len(pairs), 'selected_key': fixed_key(nextgen)})
        print(json.dumps(summaries[-1]), flush=True)
    if source_identity() != source:
        raise ValueError('source changed during comparison')
    result = {'declaration': declaration, 'rows': summaries, 'source_changed': False,
              'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob('*.gz'))}}
    write_json(output / 'summary.json', result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seeds', type=int, nargs='+', default=[55, 123, 124])
    parser.add_argument('--placements', type=int, default=40)
    args = parser.parse_args()
    if not 15 <= args.placements <= 40 or len(args.seeds) != len(set(args.seeds)):
        parser.error('require 15..40 placements and unique seeds')
    run(args.output, args.seeds, args.placements)


if __name__ == '__main__':
    main()
