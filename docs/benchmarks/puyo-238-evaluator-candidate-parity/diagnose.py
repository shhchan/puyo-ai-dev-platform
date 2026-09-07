"""Read-only evaluator diagnosis; run from the source/build being measured.

Use --reproduce on PUYO-231's clean checkout to capture the unchanged exception
and original decision request. Use --corpus on the fixed clean checkout.
All output is diagnostic and excluded from trajectory quality/performance.
"""

import argparse
import hashlib
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from agents import chain_structure
from agents import deep_chain_search_backend as backend
from agents.compact_search import CompactSearchState
from agents.deep_chain_native import InvalidNativeInputError, encode_request
from agents.deep_chain_native_evaluator import (
    NativeChainStructureBatchClient,
    NativeChainStructureInput,
    _materialize_candidate,
    materialize_native_chain_structure_result,
)
from eval import deep_chain_builder_benchmark as baseline
from eval import deep_chain_native_evaluator_benchmark as evaluator_benchmark
from eval import deep_chain_target_ablation as ablation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--reproduce", action="store_true")
    parser.add_argument("--corpus", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve()
    if root.exists():
        raise ValueError("refusing to overwrite diagnostic output")
    provenance = baseline._native_build_provenance(strict=True)
    fixture = baseline._read_json(
        Path(__file__).resolve().parents[3]
        / "tests/fixtures/evaluator_candidate_alias.json"
    )
    fields = dict(fixture["state"])
    fields["planes"] = tuple(fields["planes"])
    state = CompactSearchState(**fields)
    config = chain_structure.load_chain_structure_config()
    client = NativeChainStructureBatchClient()
    inputs = [NativeChainStructureInput(state, target_chain_count=12)]
    first = client.evaluate_batch(inputs, config)
    second = client.evaluate_batch(inputs, config)
    record = first.records[0]
    components = chain_structure.extract_components(state)
    by_cell = {cell: component for component in components for cell in component.cells}

    def candidate(value):
        materialized = _materialize_candidate(
            value, state=state, component_by_cell=by_cell
        )
        return {
            "candidate": materialized.to_dict(),
            "signature": materialized.canonical_signature,
            "key": chain_structure._candidate_rank_key(materialized),
            "fixed_tie_break": value.fixed_tie_break,
        }

    python_result = chain_structure.ChainStructureEvaluator(config).evaluate(state)
    try:
        materialized = materialize_native_chain_structure_result(
            record, state=state, config=config
        )
        error = None
        parity = materialized.to_dict() == python_result.to_dict()
    except InvalidNativeInputError as exc:
        error = {"type": type(exc).__name__, "detail": str(exc)}
        parity = False
    root.mkdir(parents=True)
    payload = {
        "ticket": "PUYO-238",
        "kind": "diagnostic_excluded_from_quality_and_performance",
        "build_provenance": provenance,
        "fixture": fixture,
        "native_record": asdict(record),
        "native_best": candidate(record.best),
        "exported_candidates": [
            dict(index=i, **candidate(c)) for i, c in enumerate(record.candidates)
        ],
        "python_result": python_result.to_dict(),
        "materialization_error": error,
        "full_python_native_parity": parity,
        "byte_identical_repeats": first.response_bytes == second.response_bytes,
        "response_sha256": hashlib.sha256(first.response_bytes).hexdigest(),
    }
    if args.reproduce:
        original = backend.materialize_native_long_horizon_result
        captures = []

        def materialize(result, request):
            try:
                return original(result, request)
            except Exception as exc:
                wire = encode_request(request)
                (root / "request.hex").write_text(wire.hex() + "\n")
                captures.append(
                    {
                        "error": {"type": type(exc).__name__, "detail": str(exc)},
                        "state": asdict(request.state),
                        "search_config": asdict(request.search_config),
                        "request_sha256": hashlib.sha256(wire).hexdigest(),
                    }
                )
                raise

        with patch.object(
            backend, "materialize_native_long_horizon_result", materialize
        ):
            run = baseline.run_benchmark_run(
                seed=146,
                repeat=1,
                profile="reference",
                max_steps=40,
                backend="native",
                target_chain_count=12,
            )
        ablation.write_run(root / "reproduction.json.gz", run)
        payload["closed_loop"] = {
            "termination": run["termination_reason"],
            "completed_turns": run["completed_turns"],
            "captures": captures,
        }
    if args.corpus:
        import _puyo_deep_chain_native as native

        semantic, _ = evaluator_benchmark._semantic_verification(
            corpus=baseline._read_json(evaluator_benchmark.DEFAULT_CORPUS_PATH),
            fixture_path=evaluator_benchmark.DEFAULT_FIXTURE_PATH,
            module=native,
            ticket="PUYO-238",
        )
        payload["semantic_corpus"] = semantic
        observation, info = baseline._initial_observation_and_info(146, max_steps=40)
        policy = baseline._policy_factory(146, "reference", "native", 12)
        samples = []
        for label in ("original", "private_counterfactual"):
            obs, details = dict(observation), dict(info)
            if label == "private_counterfactual":
                obs["private_future_queue"] = "private-future-counterfactual"
                details.update(
                    simulator="private-simulator-counterfactual",
                    future_queue="private-future-counterfactual",
                )
                policy.reset()
            action = int(policy.select_action(obs, details))
            diagnostics = policy.tactical_diagnostics
            evidence = {
                "action": action,
                "plan": baseline._plan_summary(diagnostics["plan"]),
                "search_digest": diagnostics["search"]["deterministic_digest"],
            }
            samples.append(
                {
                    "label": label,
                    "evidence": evidence,
                    "digest": ablation.digest(evidence),
                    "fallback": diagnostics["fallback"],
                    "scenario_accounting": baseline._scenario_accounting(diagnostics),
                }
            )
        payload["private_counterfactual"] = {
            "seed": 146,
            "target": 12,
            "samples": samples,
            "matches": samples[0]["digest"] == samples[1]["digest"],
            "boundary_audit": baseline.audit_future_isolation((146,), max_steps=40),
        }
    # The same hot transition/evaluator work is timed on both builds, outside
    # the end-to-end trajectory denominator; one warmup then five raw samples.
    client.combined_profile(inputs, config, operations=10000)
    payload["hot_profile"] = [
        asdict(client.combined_profile(inputs, config, operations=600000))
        for _ in range(5)
    ]
    baseline._write_json(root / "diagnostics.json", baseline._json_ready(payload))
    print(root, "parity", parity, "error", error, flush=True)


if __name__ == "__main__":
    main()
