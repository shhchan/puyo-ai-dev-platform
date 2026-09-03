import json
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from agents.deep_chain_builder import load_deep_chain_builder_config
from eval.deep_chain_builder_benchmark import (
    CANONICAL_BACKEND,
    MANIFEST_SCHEMA_VERSION,
    RUN_SCHEMA_VERSION,
    SUMMARY_SCHEMA_VERSION,
    _build_parser,
    audit_future_isolation,
    finalize_evidence,
    percentile,
    record_gui_qa,
    run_benchmark_run,
    verify_evidence,
)


class TestDeepChainBuilderBenchmark(unittest.TestCase):
    def test_locked_contract_includes_canonical_safe_build_horizon(self):
        config = load_deep_chain_builder_config()

        self.assertEqual(config.benchmark.seed_start, 123)
        self.assertEqual(config.benchmark.seed_count, 30)
        self.assertEqual(config.benchmark.repeats_per_seed, 2)
        self.assertEqual(config.benchmark.max_steps, 40)
        self.assertEqual(config.benchmark.to_dict()["run_count"], 60)

    def test_percentile_is_interpolated_and_empty_is_not_evaluable(self):
        self.assertEqual(percentile([], 0.95), None)
        self.assertEqual(percentile([1, 2, 3, 4], 0.5), 2.5)
        self.assertEqual(percentile([1, 2, 3, 4], 0.95), 3.8499999999999996)

    def test_smoke_run_records_authoritative_parity_and_trajectory_digests(self):
        payload = run_benchmark_run(
            seed=123,
            repeat=1,
            profile="smoke",
            max_steps=1,
        )

        self.assertEqual(payload["schema_version"], RUN_SCHEMA_VERSION)
        self.assertTrue(payload["fully_evaluated"])
        self.assertEqual(payload["termination_reason"], "turn_limit")
        self.assertEqual(payload["completed_turns"], 1)
        self.assertEqual(payload["backend"], "python")
        self.assertEqual(payload["records"][0]["search"]["backend"]["backend"], "python")
        self.assertEqual(payload["simulator_parity_mismatch_count"], 0)
        self.assertEqual(len(payload["action_digest"]), 64)
        self.assertEqual(len(payload["plan_digest"]), 64)
        self.assertEqual(len(payload["trajectory_digest"]), 64)
        self.assertTrue(payload["records"][0]["parity"]["passed"])

    def test_canonical_commands_require_explicit_native_backend(self):
        parser = _build_parser()
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["run"])
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["preflight"])

        run_args = parser.parse_args(["run", "--backend", "native"])
        preflight_args = parser.parse_args(
            ["preflight", "--backend", "native"]
        )

        self.assertEqual(CANONICAL_BACKEND, "native")
        self.assertEqual(run_args.backend, CANONICAL_BACKEND)
        self.assertEqual(preflight_args.backend, CANONICAL_BACKEND)

    def test_private_future_sentinels_do_not_cross_visible_boundary(self):
        payload = audit_future_isolation((123, 124), max_steps=40)

        self.assertTrue(payload["passed"])
        self.assertEqual(payload["private_future_leak_count"], 0)
        self.assertEqual(payload["seed_count"], 2)
        self.assertTrue(all(record["digests_match"] for record in payload["records"]))

    def test_incomplete_reference_evidence_is_a_verified_fail_not_a_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "preflight.json").write_text(
                json.dumps(
                    {
                        "schema_version": "puyo.deep_chain_builder.preflight.v1",
                        "timed_out": True,
                        "performance_gate_passed": False,
                        "decision_latency_lower_bound_seconds": 5.0,
                    }
                ),
                encoding="utf-8",
            )
            record_gui_qa(
                target,
                automated_passed=True,
                automated_command="python -m unittest tests.test_deep_chain_builder_benchmark",
                manual_status="pending",
                reviewer=None,
                notes="manual normal-window review pending",
            )

            summary = finalize_evidence(target)
            manifest = json.loads(
                (target / "benchmark_manifest.json").read_text(encoding="utf-8")
            )
            run_index = json.loads(
                (target / "run_index.json").read_text(encoding="utf-8")
            )

            self.assertEqual(summary["schema_version"], SUMMARY_SCHEMA_VERSION)
            self.assertEqual(manifest["schema_version"], MANIFEST_SCHEMA_VERSION)
            self.assertEqual(run_index["expected_run_count"], 60)
            self.assertEqual(len(run_index["records"]), 60)
            self.assertEqual(run_index["executed_run_count"], 0)
            self.assertFalse(summary["gates"]["coverage"]["passed"])
            self.assertFalse(summary["gates"]["performance"]["passed"])
            self.assertTrue(summary["gates"]["future_isolation"]["passed"])
            self.assertEqual(summary["baseline_decision"]["decision"], "FAIL")
            self.assertFalse(
                summary["baseline_decision"]["accepted_as_experimental_baseline"]
            )
            self.assertEqual(
                summary["failure_taxonomy"]["performance"]["status"],
                "confirmed_fail",
            )
            self.assertEqual(verify_evidence(target), [])

            summary_path = target / "benchmark_summary.json"
            summary_path.write_text(
                summary_path.read_text(encoding="utf-8") + " ",
                encoding="utf-8",
            )
            self.assertTrue(
                any("checksum mismatch" in issue for issue in verify_evidence(target))
            )


if __name__ == "__main__":
    unittest.main()
