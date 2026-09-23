import tempfile
import unittest
from pathlib import Path

from eval.deep_chain_native_search_benchmark import (
    ABLATION_SCHEMA_VERSION,
    END_TO_END_P95_MAX_MS,
    NATIVE_TOTAL_P95_MAX_MS,
    QUALITY_SEEDS,
    RAW_SCHEMA_VERSION,
    SUMMARY_SCHEMA_VERSION,
    derive_decision,
    nearest_rank,
    parse_args,
    verify_artifacts,
    write_artifacts,
)


def _summary(*, native_ms=300.0, end_to_end_ms=400.0):
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "ticket": "PUYO-202",
        "contract": {
            "depth": 16,
            "width": 250,
            "scenarios": 6,
            "max_expanded_nodes": 600_000,
        },
        "performance": {
            "sample_count": 5,
            "percentile": "nearest-rank-p95",
            "outlier_removal": "none",
            "native_total_p95_ms": native_ms,
            "end_to_end_p95_ms": end_to_end_ms,
            "native_total_limit_ms": NATIVE_TOTAL_P95_MAX_MS,
            "end_to_end_limit_ms": END_TO_END_P95_MAX_MS,
        },
        "semantic": {
            "python_differential_mismatches": 0,
            "oracle_parallel_mismatches": 0,
            "repeat_mismatches": 0,
            "isolated_seed_count": 30,
            "budget_contract_passed": True,
            "gil_counter_delta": 10,
        },
        "memory": {
            "arena_capacity_nodes": 6_000,
            "tt_capacity_slots": 16_384,
            "peak_live_nodes": 5_750,
            "max_rss_kib": 100_000,
        },
        "ablation": {
            "tie_break_version": "puyo.long_horizon_survivor_tie_break.v2",
            "decision_ranking_mismatches": 0,
            "root_evidence_mismatches": 0,
            "representative_path_changes": 4,
        },
        "provenance": {"git_commit": "a" * 40},
    }
    summary["decision"] = derive_decision(summary)
    return summary


def _details(native_ms=300.0, end_to_end_ms=400.0):
    samples = [
        {
            "native_total_ns": int(native_ms * 1_000_000),
            "end_to_end_ns": int(end_to_end_ms * 1_000_000),
        }
        for _ in QUALITY_SEEDS
    ]
    return {
        "raw": {
            "schema_version": RAW_SCHEMA_VERSION,
            "ticket": "PUYO-202",
            "samples": samples,
        },
        "ablation": {
            "schema_version": ABLATION_SCHEMA_VERSION,
            "ticket": "PUYO-202",
        },
    }


class DeepChainNativeSearchBenchmarkTest(unittest.TestCase):
    def test_contract_uses_nearest_rank_without_outlier_removal(self):
        self.assertEqual(nearest_rank([1, 2, 3, 4, 99]), 99)
        self.assertEqual(parse_args(["run"]).command, "run")
        self.assertEqual(NATIVE_TOTAL_P95_MAX_MS, 900.0)
        self.assertEqual(END_TO_END_P95_MAX_MS, 1_000.0)

    def test_decision_requires_every_semantic_and_performance_gate(self):
        passing = _summary()
        self.assertTrue(passing["decision"]["passed"])
        self.assertEqual(passing["decision"]["decision"], "GO")
        self.assertFalse(passing["decision"]["production_backend_promoted"])

        failing = _summary(native_ms=900.001, end_to_end_ms=1_000.001)
        self.assertFalse(failing["decision"]["passed"])
        self.assertEqual(
            failing["decision"]["failed_checks"],
            ["native_total_p95", "end_to_end_p95"],
        )

    def test_manifest_verifier_recomputes_percentiles_and_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_artifacts(output, _summary(), _details())
            self.assertEqual(verify_artifacts(output), [])

            raw = output / "raw_samples.json"
            raw.write_text(raw.read_text(encoding="utf-8") + " ", encoding="utf-8")
            self.assertIn(
                "artifact digest mismatch: raw_samples.json",
                verify_artifacts(output),
            )


if __name__ == "__main__":
    unittest.main()
