import tempfile
import unittest
from pathlib import Path

from agents.beam_search import evaluate_build_potential_v1
from eval.v1_7_build_potential_benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    DEFAULT_SOURCE,
    FEATURE_NAMES,
    TARGET_NAMES,
    _candidate_board,
    audit_v1_projection,
    build_metrics,
    kendall_tau_b,
    load_frozen_decisions,
    parse_args,
    run_benchmark,
    spearman_correlation,
    verify_benchmark,
)
from src.core.headless import HeadlessPuyoSimulator


ROOT = Path(__file__).resolve().parents[1]


class TestV17BuildPotentialBenchmark(unittest.TestCase):
    def test_loads_stable_prefix_from_frozen_puyo_165_v1_artifact(self):
        records, evidence = load_frozen_decisions(
            ROOT / DEFAULT_SOURCE,
            decision_limit=2,
        )

        self.assertEqual(len(records), 2)
        self.assertEqual(evidence["available_decisions"], 1200)
        self.assertEqual(evidence["selected_decisions"], 2)
        self.assertEqual(
            evidence["schema_version"],
            "puyo.v1_7_search_diagnostics_decision.v1",
        )
        self.assertEqual(len(evidence["sha256"]), 64)
        self.assertTrue(evidence["immutable_source"])

    def test_v1_projection_preserves_positive_and_marks_zero_unknown(self):
        records, _ = load_frozen_decisions(
            ROOT / DEFAULT_SOURCE,
            decision_limit=1,
        )

        audit = audit_v1_projection(records)

        self.assertTrue(audit["passed"])
        self.assertEqual(audit["error_count"], 0)
        self.assertEqual(
            audit["candidate_records"],
            audit["legacy_positive_records"] + audit["legacy_zero_records"],
        )
        self.assertEqual(
            audit["status_counts"].get("legacy_partial", 0),
            audit["legacy_positive_records"],
        )
        self.assertEqual(
            audit["status_counts"].get("unknown", 0),
            audit["legacy_zero_records"],
        )

    def test_candidate_replay_uses_the_frozen_hidden_future_scenario(self):
        records, _ = load_frozen_decisions(
            ROOT / DEFAULT_SOURCE,
            decision_limit=1,
        )
        source_candidate = next(
            candidate
            for candidate in records[0]["current"]["candidates"]
            if candidate["action"] == 5
        )

        candidate, issue = _candidate_board(
            HeadlessPuyoSimulator(seed=records[0]["seed"]),
            source_candidate["best_path"],
        )

        self.assertIsNone(issue)
        self.assertIsNotNone(candidate)
        self.assertEqual(
            evaluate_build_potential_v1(candidate).to_dict(),
            source_candidate["potential"],
        )

    def test_rank_correlations_support_ties_without_scipy(self):
        self.assertAlmostEqual(
            spearman_correlation([1.0, 2.0, 2.0, 4.0], [1.0, 3.0, 3.0, 5.0]),
            1.0,
        )
        self.assertAlmostEqual(
            kendall_tau_b([1.0, 2.0, 2.0, 4.0], [1.0, 3.0, 3.0, 5.0]),
            1.0,
        )
        self.assertAlmostEqual(
            spearman_correlation([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]),
            -1.0,
        )
        self.assertAlmostEqual(
            kendall_tau_b([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]),
            -1.0,
        )
        self.assertIsNone(kendall_tau_b([1.0, 1.0], [2.0, 3.0]))

    def test_metrics_expose_deterministic_and_tie_aware_top1(self):
        rows = []
        values = (
            (0, 0.8, 10.0),
            (1, 0.8, 20.0),
            (2, 0.2, 20.0),
        )
        for action, feature, target in values:
            features = {name: feature for name in FEATURE_NAMES}
            targets = {name: target for name in TARGET_NAMES}
            rows.append(
                {
                    "seed": 123,
                    "step": 0,
                    "action": action,
                    "features": features,
                    "targets": targets,
                }
            )

        ranking = build_metrics(rows)["reference_candidate_value"]["v2_composite"][
            "ranking"
        ]

        self.assertEqual(ranking["eligible_decisions"], 1)
        self.assertEqual(ranking["informative_decisions"], 1)
        self.assertEqual(ranking["feature_tie_decisions"], 1)
        self.assertEqual(ranking["target_tie_decisions"], 1)
        self.assertEqual(ranking["deterministic_top1_hits"], 0)
        self.assertEqual(ranking["tie_aware_top_set_overlap_hits"], 1)

    def test_smoke_artifact_replays_cache_on_and_off_and_verifies(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "artifact"
            args = parse_args(
                [
                    "run",
                    "--source",
                    str(ROOT / DEFAULT_SOURCE),
                    "--output-dir",
                    str(output),
                    "--workers",
                    "1",
                    "--repetitions",
                    "2",
                    "--decision-limit",
                    "1",
                ]
            )

            summary = run_benchmark(args)
            verified = verify_benchmark(
                parse_args(["verify", "--artifact-dir", str(output)])
            )

        self.assertEqual(summary["schema_version"], BENCHMARK_SCHEMA_VERSION)
        self.assertTrue(summary["evaluation_completed"])
        self.assertIsNone(summary["quality_gate"])
        self.assertEqual(
            summary["determinism"]["cache_modes"],
            ["enabled", "disabled"],
        )
        self.assertTrue(summary["determinism"]["passed"])
        self.assertEqual(summary["budget"]["violations"], 0)
        self.assertEqual(
            summary["compatibility"]["learned_analyzer_feature_count"],
            77,
        )
        self.assertEqual(set(summary["metrics"]), set(TARGET_NAMES))
        self.assertTrue(verified["passed"])

    def test_run_rejects_a_different_frozen_source_hash_before_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            args = parse_args(
                [
                    "run",
                    "--source",
                    str(ROOT / DEFAULT_SOURCE),
                    "--output-dir",
                    directory,
                    "--decision-limit",
                    "1",
                    "--expected-source-sha256",
                    "0" * 64,
                ]
            )

            with self.assertRaisesRegex(ValueError, "source hash differs"):
                run_benchmark(args)


if __name__ == "__main__":
    unittest.main()
