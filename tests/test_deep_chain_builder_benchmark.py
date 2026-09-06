import json
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from agents.deep_chain_builder import load_deep_chain_builder_config
from eval.deep_chain_builder_benchmark import (
    CANONICAL_BACKEND,
    CANONICAL_TARGET_CHAIN_COUNT,
    MANIFEST_SCHEMA_VERSION,
    RUN_SCHEMA_VERSION,
    SUMMARY_SCHEMA_VERSION,
    TICKET,
    _build_parser,
    _load_completed_runs,
    _scenario_accounting,
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
        self.assertEqual(CANONICAL_TARGET_CHAIN_COUNT, 6)

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
        self.assertEqual(payload["ticket"], TICKET)
        self.assertTrue(payload["fully_evaluated"])
        self.assertEqual(payload["termination_reason"], "turn_limit")
        self.assertEqual(payload["completed_turns"], 1)
        self.assertEqual(payload["backend"], "python")
        self.assertEqual(payload["target_chain_count"], CANONICAL_TARGET_CHAIN_COUNT)
        self.assertEqual(payload["records"][0]["search"]["backend"]["backend"], "python")
        self.assertEqual(payload["simulator_parity_mismatch_count"], 0)
        self.assertEqual(len(payload["action_digest"]), 64)
        self.assertEqual(len(payload["plan_digest"]), 64)
        self.assertEqual(len(payload["trajectory_digest"]), 64)
        self.assertTrue(payload["records"][0]["parity"]["passed"])
        self.assertTrue(payload["records"][0]["scenario_accounting"]["passed"])

    def test_scenario_accounting_rejects_missing_and_duplicate_ids(self):
        payload = _scenario_accounting(
            {
                "search": {"scenario_ids": [0, 1]},
                "scenario_aggregation": [
                    {
                        "root_action": 3,
                        "evidence": {
                            "requested_scenarios": 2,
                            "scenario_values": [
                                {"scenario_id": 0},
                                {"scenario_id": 0},
                            ],
                        },
                    }
                ],
            }
        )

        self.assertFalse(payload["passed"])
        self.assertEqual(payload["failure_count"], 1)
        self.assertEqual(payload["failures"][0]["missing_scenario_ids"], [1])
        self.assertEqual(payload["failures"][0]["duplicate_scenario_ids"], [0])

    def test_canonical_commands_require_explicit_native_backend(self):
        parser = _build_parser()
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["run"])
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["preflight"])
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "run",
                    "--backend",
                    "native",
                    "--target-chain",
                    "10",
                ]
            )

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

    def test_canonical_loader_rejects_an_experimental_target(self):
        config = load_deep_chain_builder_config()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            run_dir = target / "runs"
            run_dir.mkdir(parents=True)
            (run_dir / "seed-123-repeat-01.json").write_text(
                json.dumps(
                    {
                        "schema_version": RUN_SCHEMA_VERSION,
                        "ticket": TICKET,
                        "run_id": "seed-123-repeat-01",
                        "target_chain_count": 10,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError, "canonical target chain count mismatch"
            ):
                _load_completed_runs(target, config.benchmark)

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

    def test_gui_qa_keeps_dummy_and_manual_results_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            result_path = target / "dummy-result.json"
            replay_path = target / "dummy-replay.json"
            result_path.write_text(
                json.dumps(
                    {
                        "schema_version": "puyo.gui_qa.v1",
                        "models": {
                            "player_0": {
                                "deep_chain_profile": "reference",
                                "deep_chain_backend": "native",
                                "deep_chain_target_chain": 6,
                            }
                        },
                        "diagnostics": {
                            "policy": {
                                "player_0": {
                                    "selected_action": 4,
                                    "plan": {"steps": [{"action": 4}]},
                                    "backend": {"backend": "native"},
                                    "fallback": {"used": False},
                                }
                            },
                            "controller": {
                                "player_0": {"decisions_started": 3, "replans": 2}
                            },
                        },
                        "plan_overlay_player_0": True,
                    }
                ),
                encoding="utf-8",
            )
            replay_path.write_text(
                json.dumps({"format": "puyo-realtime-match-v1"}),
                encoding="utf-8",
            )

            payload = record_gui_qa(
                target,
                automated_passed=True,
                automated_command="python -m unittest",
                manual_status="pending",
                reviewer=None,
                notes="visual review remains pending",
                dummy_result_path=result_path,
                dummy_replay_path=replay_path,
            )

            self.assertTrue(payload["automated"]["passed"])
            self.assertTrue(payload["dummy_replay"]["passed"])
            self.assertEqual(payload["manual"]["status"], "pending")
            self.assertFalse(payload["passed"])


if __name__ == "__main__":
    unittest.main()
