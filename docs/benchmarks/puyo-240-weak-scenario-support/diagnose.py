"""PUYO-240 全root診断。計測・品質比較には算入しない。"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from agents import deep_chain_search_backend as search_backend
from agents.deep_chain_native import encode_request
from eval import deep_chain_builder_benchmark as baseline
from eval import deep_chain_target_ablation as ablation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("既存の証跡を上書きしません")
    provenance = baseline._native_build_provenance(strict=True)
    cases = []
    original = search_backend.materialize_native_long_horizon_result

    def capture(native, request):
        result = original(native, request)
        cases.append(
            {
                "turn": len(cases),
                "request_hex": encode_request(request).hex(),
                "native_ranking": list(native.ranked_root_actions),
                "native_selected_action": native.selected_action,
                "native_digest": native.deterministic_digest,
                "python_digest": result.deterministic_digest,
                "strict_all_root_parity_passed": True,
                "counters": result.counters.to_dict(),
                "roots": [
                    {
                        **e.to_dict(),
                        "ranking_key": [
                            str(v) if isinstance(v, float) and not math.isfinite(v) else v
                            for v in e.ranking_key
                        ],
                        "representative": (
                            {
                                "scenario_id": result.representatives[e.root_action].scenario_id,
                                "depth": len(result.representatives[e.root_action].path),
                                "evaluator_score": result.representatives[e.root_action].evaluator_score,
                            }
                            if e.root_action in result.representatives
                            else None
                        ),
                    }
                    for e in result.ranked_roots
                ],
            }
        )
        return result

    search_backend.materialize_native_long_horizon_result = capture
    run = baseline.run_benchmark_run(
        seed=args.seed,
        repeat=1,
        profile="reference",
        max_steps=40,
        backend="native",
        target_chain_count=10,
    )
    payload = {
        "ticket": "PUYO-240",
        "kind": "全root診断（計測・品質比較から除外）",
        "prefixed_thresholds": [2, 3],
        "build_provenance": provenance,
        "configuration": ablation.configuration(),
        "script_sha256": baseline.file_sha256(Path(__file__)),
        "seed": args.seed,
        "cases": cases,
        "trajectory": run,
    }
    ablation.write_run(args.output, payload)
    print({"seed": args.seed, "decisions": len(cases), "maximum_actual_chain": run["maximum_actual_fire_chain_count"], "termination": run["termination_reason"], "path": str(args.output)}, flush=True)


if __name__ == "__main__":
    main()
