"""PUYO-232 defaults, historical isolation and safe-build fire boundaries."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agents.deep_chain_builder import (
    DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
    SAFE_BUILD_CONTRACT_VERSION,
    DeepChainBuilderPolicy,
    load_deep_chain_builder_config,
)
from agents.long_horizon_search import classify_build_main_fire
from eval import deep_chain_target_ablation as engine
from eval.deep_chain_safe_build_benchmark import CONTRACT
from eval.realtime_versus_ui import parse_config
from src.ui.launcher_settings import LauncherSettings


class TestSafeBuildContract(unittest.TestCase):
    def test_defaults_and_explicit_targets_are_distinct_from_quality_floor(self):
        config = load_deep_chain_builder_config()
        self.assertEqual(config.default_target_chain_count, 10)
        self.assertEqual(config.quality_floor, 10)
        self.assertEqual(DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT, 10)
        self.assertEqual(config.quality_contract_version, SAFE_BUILD_CONTRACT_VERSION)
        self.assertEqual(LauncherSettings().deep_chain_target_chain, 10)
        self.assertEqual(parse_config([]).deep_chain_target_chain, 10)
        self.assertEqual(DeepChainBuilderPolicy(profile="smoke").target_chain_count, 10)
        custom = replace(config, default_target_chain_count=7)
        self.assertEqual(
            DeepChainBuilderPolicy(profile="smoke", config=custom).target_chain_count, 7
        )
        for target in range(1, 20):
            with self.subTest(target=target):
                self.assertEqual(
                    parse_config(
                        ["--deep-chain-target-chain", str(target)]
                    ).deep_chain_target_chain,
                    target,
                )
                policy = DeepChainBuilderPolicy(
                    profile="smoke", config=custom, target_chain_count=target
                )
                self.assertEqual(policy.target_chain_count, target)
                self.assertEqual(policy.tactical_diagnostics["quality_floor"], 10)
        with patch.object(
            engine.baseline, "load_deep_chain_builder_config", return_value=custom
        ), self.assertRaisesRegex(ValueError, "target and quality floor"):
            engine.configuration(contract=CONTRACT)

    def test_canonical_manifest_worker_and_historical_evidence_are_isolated(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(engine.baseline, "_native_build_provenance", return_value={}),
        ):
            root = Path(directory) / "canonical"
            manifest = engine.initialize(root, contract=CONTRACT)
            self.assertEqual(manifest["run_count"], 60)
            self.assertEqual(manifest["targets"], [10])
            self.assertEqual(manifest["common_configuration"]["target_chain_count"], 10)
            self.assertEqual(manifest["common_configuration"]["quality_floor"], 10)
            self.assertEqual(engine.load_manifest(root, contract=CONTRACT), manifest)
            with self.assertRaisesRegex(ValueError, "unsupported experiment"):
                engine.load_manifest(root)
            engine.finalize(root, contract=CONTRACT)
            self.assertEqual(engine.verify(root, contract=CONTRACT), [])
            summary = engine.baseline._read_json(root / "target-10/summary.json")
            self.assertFalse(summary["accepted"])
            self.assertEqual(len(summary["coverage"]), 60)
            with self.assertRaisesRegex(ValueError, "outside"):
                engine.diagnostic(root, 6, contract=CONTRACT)
            with (
                patch.object(engine, "initialize"),
                patch.object(engine.subprocess, "run") as worker,
            ):
                engine.run_pending(root, None, 1, contract=CONTRACT)
            self.assertEqual(
                worker.call_args.args[0][2], "eval.deep_chain_safe_build_benchmark"
            )
            for historical in (
                engine.baseline.DEFAULT_OUTPUT_DIR,
                engine.DEFAULT_OUTPUT_DIR,
            ):
                with self.assertRaisesRegex(ValueError, "read-only"):
                    engine.initialize(historical, contract=CONTRACT)
            legacy = Path(directory) / "legacy"
            engine.initialize(legacy)
            with self.assertRaisesRegex(ValueError, "unsupported experiment"):
                engine.finalize(legacy, contract=CONTRACT)
        self.assertEqual(engine.baseline.CANONICAL_TARGET_CHAIN_COUNT, 6)
        self.assertEqual(len(engine.identities()), 240)

    def test_historical_verifier_uses_the_recorded_config_blob(self):
        self.assertEqual(engine.baseline.verify_evidence(historical=True), [])
        self.assertIn("configuration checksum mismatch", engine.baseline.verify_evidence())

    def test_every_subten_chain_is_premature_including_safety_exceptions(self):
        for chain in range(20):
            for context in ("safe_build", "forced_safety"):
                expected = (
                    "quiet_continuation"
                    if chain == 0
                    else "target_fire"
                    if chain >= 10
                    else "forced_safety_fire"
                    if context == "forced_safety"
                    else "premature_fire"
                )
                with self.subTest(chain=chain, context=context):
                    self.assertEqual(
                        classify_build_main_fire(
                            chain_count=chain,
                            chain_score=100000,
                            target_chain_count=10,
                            fire_context=context,
                        ),
                        expected,
                    )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = engine.baseline.run_benchmark_run(
                seed=123, repeat=1, profile="smoke", max_steps=1, target_chain_count=10
            )
            for chain in range(1, 10):
                record = raw["records"][0]
                record["actual_result"]["chain_count"] = chain
                record["selection"]["selected_score"]["evidence"]["fire_class"] = (
                    "forced_safety_fire"
                )
                raw.update(
                    actual_fire_chain_counts=[chain],
                    maximum_actual_fire_chain_count=chain,
                    premature_fire_count=1,
                )
                summary = engine.summarize_target(
                    root, 10, {"manifest_sha256": "test"}, [raw], contract=CONTRACT
                )
                self.assertEqual(summary["premature_fire_count_all_repeats"], 1)
                self.assertEqual(len(summary["forced_safety_fires"]), 1)
                self.assertFalse(summary["gates"]["quality"])
