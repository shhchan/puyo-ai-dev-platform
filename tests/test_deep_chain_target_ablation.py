import copy
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from agents.deep_chain_builder import (
    DEEP_CHAIN_TARGET_CHAIN_CHOICES,
    DeepChainBuilderPolicy,
)
from eval import deep_chain_builder_benchmark as baseline
from eval import deep_chain_target_ablation as experiment
from eval.realtime_versus_ui import (
    RealtimeVersusMatchController,
    RealtimeVersusUiConfig,
    parse_config,
    validate_config,
)


class TestInteractiveTargets(unittest.TestCase):
    def test_cli_and_runtime_reject_outside_range_and_nonintegers(self):
        for value in ("0", "20", "7.5", "seven", "7.0"):
            with (
                self.subTest(value=value),
                redirect_stderr(StringIO()),
                self.assertRaises(SystemExit),
            ):
                parse_config(["--deep-chain-target-chain", value])
        for value in (0, 20, True, 7.0, 7.5, "7"):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, "integer in"),
            ):
                validate_config(RealtimeVersusUiConfig(deep_chain_target_chain=value))

    def test_python_and_native_preserve_targets_in_search_plan_and_diagnostics(self):
        observation, info = baseline._initial_observation_and_info(187, max_steps=40)
        for target in (1, 7, 19):
            for backend in ("python", "native"):
                with self.subTest(target=target, backend=backend):
                    policy = DeepChainBuilderPolicy(
                        profile="smoke", backend=backend, target_chain_count=target
                    )
                    action = policy.select_action(observation, info)
                    d = policy.tactical_diagnostics
                    self.assertEqual(d["target_chain_count"], target)
                    self.assertEqual(d["search"]["target_chain_count"], target)
                    self.assertEqual(
                        d["backend"]["configuration"]["minimum_chain_count"], target
                    )
                    self.assertEqual(
                        d["plan"]["objective"]["minimum_chain_count"], target
                    )
                    self.assertEqual(d["plan"]["steps"][0]["action"], action)
                    self.assertFalse(d["fallback"]["used"])

    def test_native_target_reaches_display_and_recorded_replay(self):
        for target in (1, 7, 19):
            with (
                self.subTest(target=target),
                patch("eval.realtime_versus_ui.ASYNC_POLICY_TYPES", frozenset()),
            ):
                config = parse_config(
                    [
                        "--policy-a",
                        "deep_chain_builder",
                        "--policy-b",
                        "first",
                        "--deep-chain-profile",
                        "smoke",
                        "--deep-chain-backend",
                        "native",
                        "--deep-chain-target-chain",
                        str(target),
                        "--replay",
                        "unused.json",
                    ]
                )
                controller = RealtimeVersusMatchController(config)
                try:
                    for _ in range(120):
                        controller.advance_tick()
                        if controller.controllers[
                            "player_0"
                        ].diagnostics.decisions_activated:
                            break
                    self.assertEqual(
                        controller.tactical_summary("player_0")["target_chain"], target
                    )
                    self.assertEqual(
                        controller.plan_overlay("player_0")["objective"][
                            "minimum_chain_count"
                        ],
                        target,
                    )
                    replay = controller.replay_payload()
                    records = [
                        tick["policy_diagnostics"]["player_0"]
                        for tick in replay["ticks"]
                        if "player_0" in tick["policy_diagnostics"]
                    ]
                    self.assertTrue(records)
                    for record in records:
                        self.assertEqual(record["target_chain_count"], target)
                        self.assertEqual(
                            record["plan"]["objective"]["minimum_chain_count"], target
                        )
                finally:
                    controller.shutdown()


class TestTargetAblation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "evidence"
        self.provenance = {
            "evaluated_commit": "a" * 40,
            "host": {"cpu": "test"},
            "capabilities": {"build_profile": "release"},
            "wheels": [{"sha256": "test"}],
            "configuration": {"thread_mode": "scenario-6"},
        }
        self.build = patch.object(
            baseline, "_native_build_provenance", return_value=self.provenance
        )
        self.build.start()
        self.addCleanup(self.build.stop)
        self.manifest = experiment.initialize(self.root)

    def test_experiment_is_independent_of_gui_range_and_historical_target(self):
        self.assertEqual(DEEP_CHAIN_TARGET_CHAIN_CHOICES, tuple(range(1, 20)))
        self.assertEqual(experiment.TARGETS, (6, 8, 10, 12))
        self.assertEqual(len(experiment.identities()), 240)
        self.assertEqual(len({i["run_id"] for i in experiment.identities()}), 240)
        self.assertEqual(baseline.CANONICAL_TARGET_CHAIN_COUNT, 6)
        self.assertEqual(self.manifest["common_configuration"]["quality_floor"], 10)

    def test_resume_rejects_host_source_build_config_and_manifest_changes(self):
        self.assertEqual(experiment.initialize(self.root), self.manifest)
        for key in (
            "host",
            "evaluated_commit",
            "wheels",
            "capabilities",
            "configuration",
        ):
            changed = {**self.provenance, key: "changed"}
            with (
                self.subTest(key=key),
                patch.object(
                    baseline, "_native_build_provenance", return_value=changed
                ),
                self.assertRaisesRegex(ValueError, "provenance"),
            ):
                experiment.initialize(self.root)
        with (
            patch.object(experiment, "configuration", return_value={}),
            self.assertRaisesRegex(ValueError, "configuration"),
        ):
            experiment.initialize(self.root)
        damaged = copy.deepcopy(self.manifest)
        damaged["targets"].append(7)
        baseline._write_json(self.root / "experiment_manifest.json", damaged)
        with self.assertRaisesRegex(ValueError, "checksum"):
            experiment.load_manifest(self.root)

    def test_missing_runs_are_pending_and_never_pass_or_enter_denominator(self):
        result = experiment.finalize(self.root)
        self.assertFalse(result["10"]["gates"]["quality"])
        payload = baseline._read_json(self.root / "target-10/summary.json")
        self.assertEqual(len(payload["coverage"]), 60)
        self.assertTrue(all(r["status"] == "pending" for r in payload["coverage"]))
        self.assertIsNone(payload["unique_seed_estimate"]["mean_maximum_actual_chain"])
        self.assertIsNone(payload["latency"]["p95_seconds"])
        self.assertEqual(experiment.verify(self.root), [])
        payload["gates"]["quality"] = True
        baseline._write_json(self.root / "target-10/summary.json", payload)
        self.assertTrue(experiment.verify(self.root))

    def test_per_condition_finalization_and_verification(self):
        experiment.finalize(self.root, 8)
        self.assertEqual(experiment.verify(self.root, 8), [])
        self.assertFalse((self.root / "target-10/summary.json").exists())
        self.assertFalse((self.root / "paired_comparison.json").exists())

    def test_repeat_two_is_not_a_new_seed_and_target_success_is_not_quality_success(
        self,
    ):
        raw = baseline.run_benchmark_run(
            seed=123, repeat=1, profile="smoke", max_steps=1, target_chain_count=6
        )
        record = raw["records"][0]
        record["actual_result"]["chain_count"] = 8
        raw.update(
            maximum_actual_fire_chain_count=8,
            actual_fire_chain_counts=[8],
            premature_fire_count=1,
        )
        second = {**raw, "repeat": 2}
        summary = experiment.summarize_target(
            self.root, 6, self.manifest, [raw, second]
        )
        self.assertEqual(summary["unique_seed_estimate"]["denominator"], 1)
        self.assertEqual(
            summary["unique_seed_estimate"]["internal_target_reached_count"], 1
        )
        self.assertEqual(
            summary["unique_seed_estimate"]["quality_floor_reached_count"], 0
        )
        self.assertEqual(summary["premature_fire_count_all_repeats"], 2)
        self.assertFalse(summary["gates"]["coverage"])

    def test_historical_output_is_protected(self):
        with self.assertRaisesRegex(ValueError, "read-only"):
            experiment.initialize(baseline.DEFAULT_OUTPUT_DIR)

    def test_raw_validation_rejects_target_identity_and_premature_tampering(self):
        identity = experiment.identities()[0]
        raw = baseline.run_benchmark_run(
            seed=123, repeat=1, profile="smoke", max_steps=1, target_chain_count=6
        )
        raw.update(
            schema_version=experiment.SCHEMA,
            ticket=experiment.TICKET,
            run_id=identity["run_id"],
            quality_floor=10,
            manifest_sha256=self.manifest["manifest_sha256"],
            evaluated_commit=self.provenance["evaluated_commit"],
            profile="reference",
            backend="native",
            max_steps=40,
            termination_reason="policy_error",
            fully_evaluated=False,
        )
        experiment.validate_run(raw, identity, self.manifest)
        for key, value in (
            ("target_chain_count", 8),
            ("run_id", "wrong"),
            ("premature_fire_count", 99),
            ("fully_evaluated", True),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                experiment.validate_run({**raw, key: value}, identity, self.manifest)
        raw["records"][0]["plan"]["objective"]["minimum_chain_count"] = 10
        with self.assertRaisesRegex(ValueError, "propagation"):
            experiment.validate_run(raw, identity, self.manifest)


if __name__ == "__main__":
    unittest.main()
