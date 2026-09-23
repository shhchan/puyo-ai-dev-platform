"""Canonical target10/quality10 evaluation established by PUYO-232.

PUYO-236 owns the full 60-run measurement and baseline decision. The shared
runner preserves PUYO-231's immutable manifests, process isolation and gates;
the legacy PUYO-204 command and its target6 constant remain unchanged.
"""

from pathlib import Path

from eval.deep_chain_target_ablation import ExperimentContract, main

CANONICAL_TARGET_CHAIN_COUNT = 10
CONTRACT = ExperimentContract(
    ticket="PUYO-236",
    schema="puyo.deep_chain_safe_build_benchmark.v2",
    targets=(CANONICAL_TARGET_CHAIN_COUNT,),
    output_dir=Path("docs/benchmarks/puyo-236-safe-build-baseline"),
    module="eval.deep_chain_safe_build_benchmark",
    title="PUYO-236 safe-build target10 品質・性能評価",
    canonical_safe_build=True,
)


if __name__ == "__main__":
    raise SystemExit(main(contract=CONTRACT))
