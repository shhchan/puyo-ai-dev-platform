import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch

from agents.state_analyzer import AnalyzerConfig, StateAnalyzer
from agents.strategy_workers import smoke_worker_profiles
from agents.v1_7_strategy_manager import (
    BOOTSTRAP_TRAINER_NAME,
    FEATURE_SCHEMA_VERSION,
    LIFECYCLE_CARRY_FEATURES,
    MODEL_FAMILY,
    MODEL_VERSION,
    POLICY_TYPE,
    StrategyFeatureContract,
    V17StrategyManagerNetwork,
    V17StrategyManagerPolicy,
    build_v1_7_checkpoint_metadata,
    migrate_v1_7_1_checkpoint_payload,
    validate_v1_7_strategy_manager_checkpoint_payload,
)
from agents.v1_7_tactics import load_tactic_registry
from eval.arena import parse_args as parse_arena_args
from eval.realtime_arena import parse_args as parse_realtime_arena_args
from eval.spectate import parse_args as parse_spectate_args
from puyo_env.versus_env import VersusPuyoEnv
from selfplay.policies import make_policy
from src.ui.launcher_settings import LauncherPresetStore, LauncherSettingsManager
from train.artifacts import attach_checkpoint_schema
from train.restore import checkpoint_state_hash


def build_checkpoint_payload() -> dict:
    registry = load_tactic_registry()
    contract = StrategyFeatureContract.from_registry(registry)
    model = V17StrategyManagerNetwork(contract, hidden_dim=16)
    run_id = "v1-7-checkpoint-test"
    config = {"hidden_dim": 16, "run_id": run_id}
    metadata = build_v1_7_checkpoint_metadata(registry, run_id=run_id)
    schemas = metadata["schemas"]
    checkpoint = attach_checkpoint_schema(
        {
            "policy_type": POLICY_TYPE,
            "model_family": MODEL_FAMILY,
            "model_version": MODEL_VERSION,
            "model_state_dict": model.state_dict(),
            "config": config,
            "run_id": run_id,
            "global_step": 3,
            "hidden_dim": 16,
            "feature_contract": contract.to_metadata(),
            "checkpoint_metadata": metadata,
            "dataset": {
                "path": "test-dataset",
                "dataset_id": "dataset-test-id",
                "manifest_sha256": "a" * 64,
                "schemas": {
                    "analyzer_input": schemas["analyzer_input"],
                    "analyzer_diagnostics": schemas["analyzer_diagnostics"],
                    "build_potential": schemas["build_potential"],
                    "chain_style": schemas["chain_style"],
                    "feature": schemas["strategy_features"],
                    "preview_feature": schemas["planner_preview_features"],
                    "tactic_registry": schemas["tactic_registry"],
                    "tactic_registry_version": schemas["tactic_registry_version"],
                },
                "counts": {"train": 3, "validation": 26},
                "compatibility": {},
                "chain_style": dict(metadata["chain_style"]),
            },
            "lifecycle_carry_contract": {
                "analyzer_input_schema_version": schemas["analyzer_input"],
                "strategy_feature_schema_version": FEATURE_SCHEMA_VERSION,
                "required_features": list(LIFECYCLE_CARRY_FEATURES),
                "legacy_implicit_defaults_allowed": False,
            },
        },
        trainer_name=BOOTSTRAP_TRAINER_NAME,
        run_id=run_id,
        checkpoint_kind="bootstrap",
        global_step=3,
        config=config,
        git_commit="test-commit",
        seed=127,
    )
    checkpoint["state_hash"] = checkpoint_state_hash(checkpoint)
    return checkpoint


def build_legacy_checkpoint_payload() -> dict:
    checkpoint = build_checkpoint_payload()
    checkpoint["model_version"] = "v1.7.1"
    metadata = checkpoint["checkpoint_metadata"]
    metadata["model_version"] = "v1.7.1"
    metadata["lineage"] = {
        "node_id": "model_version:v1.7.1",
        "parent_node_id": "model_version:v1.7.0",
        "training_run_id": checkpoint["run_id"],
    }
    metadata["schemas"].update(
        {
            "tactic_registry": "tactic-schema-v1",
            "tactic_registry_version": "v1.7.0",
            "planner_request": "planner-schema-v1",
        }
    )
    checkpoint["feature_contract"]["registry_version"] = "v1.7.0"
    checkpoint["dataset"]["schemas"].update(
        {
            "tactic_registry": "tactic-schema-v1",
            "tactic_registry_version": "v1.7.0",
        }
    )
    return checkpoint


class TestV17CheckpointLoading(unittest.TestCase):
    def save_checkpoint(self, root: str, payload: dict) -> Path:
        path = Path(root) / "bootstrap.pt"
        torch.save(payload, path)
        return path

    def test_valid_checkpoint_loads_and_selects_a_legal_action(self):
        payload = build_checkpoint_payload()
        self.assertEqual(validate_v1_7_strategy_manager_checkpoint_payload(payload), [])

        with tempfile.TemporaryDirectory() as directory:
            path = self.save_checkpoint(directory, payload)
            policy = V17StrategyManagerPolicy.from_checkpoint(
                path,
                analyzer=StateAnalyzer(
                    AnalyzerConfig(max_depth=1, beam_width=6, max_attack_options=4)
                ),
                profiles=smoke_worker_profiles(),
            )
            env = VersusPuyoEnv(seed=127, max_steps=2)
            observations, infos = env.reset(seed=127)

            action = policy.select_action(
                observations["player_0"],
                infos["player_0"],
            )

            self.assertTrue(bool(infos["player_0"]["action_mask"][action]))
            self.assertEqual(policy.checkpoint_path, str(path))
            self.assertEqual(
                policy.tactical_diagnostics["model_metadata"]["checkpoint_run_id"],
                payload["run_id"],
            )
            self.assertEqual(
                policy.tactical_diagnostics["lineage"]["parent_node_id"],
                "model_version:v1.7.1",
            )
            env.close()

    def test_policy_factory_requires_and_loads_a_checkpoint(self):
        with self.assertRaisesRegex(ValueError, "checkpoint_path is required"):
            make_policy(POLICY_TYPE)

        with tempfile.TemporaryDirectory() as directory:
            path = self.save_checkpoint(directory, build_checkpoint_payload())
            policy = make_policy(POLICY_TYPE, checkpoint_path=path)

        self.assertIsInstance(policy, V17StrategyManagerPolicy)

    def test_v1_7_1_checkpoint_migration_is_explicit_recorded_and_loadable(self):
        legacy = build_legacy_checkpoint_payload()
        original = copy.deepcopy(legacy)

        migrated = migrate_v1_7_1_checkpoint_payload(legacy)

        self.assertEqual(legacy["model_version"], original["model_version"])
        self.assertEqual(migrated["model_version"], MODEL_VERSION)
        self.assertEqual(
            migrated["schema_migration"]["source_model_version"],
            "v1.7.1",
        )
        self.assertFalse(migrated["schema_migration"]["weights_changed"])
        self.assertFalse(migrated["schema_migration"]["feature_shape_changed"])
        self.assertEqual(validate_v1_7_strategy_manager_checkpoint_payload(migrated), [])

        with tempfile.TemporaryDirectory() as directory:
            path = self.save_checkpoint(directory, migrated)
            policy = V17StrategyManagerPolicy.from_checkpoint(path)

        self.assertIsInstance(policy, V17StrategyManagerPolicy)

    def test_v1_7_1_checkpoint_is_not_implicitly_reinterpreted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.save_checkpoint(directory, build_legacy_checkpoint_payload())
            with self.assertRaisesRegex(ValueError, "incompatible v1.7 bootstrap"):
                V17StrategyManagerPolicy.from_checkpoint(path)

    def test_incompatible_metadata_fails_before_weights_are_applied(self):
        payload = build_checkpoint_payload()
        payload["feature_contract"]["context_dim"] += 1
        errors = validate_v1_7_strategy_manager_checkpoint_payload(payload)
        self.assertTrue(
            any(
                "strategy feature contract mismatch for context_dim" in error
                and "expected 77" in error
                and "got 78" in error
                for error in errors
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            path = self.save_checkpoint(directory, payload)
            with mock.patch.object(
                V17StrategyManagerNetwork,
                "load_state_dict",
            ) as load_state_dict:
                with self.assertRaisesRegex(ValueError, "incompatible v1.7 bootstrap"):
                    V17StrategyManagerPolicy.from_checkpoint(path)
                load_state_dict.assert_not_called()

    def test_tensor_shape_and_state_hash_mismatches_are_explicit(self):
        shape_payload = build_checkpoint_payload()
        key = "shared_encoder.0.weight"
        shape_payload["model_state_dict"][key] = shape_payload["model_state_dict"][key][:-1]
        shape_payload["state_hash"] = checkpoint_state_hash(shape_payload)

        shape_errors = validate_v1_7_strategy_manager_checkpoint_payload(shape_payload)
        self.assertTrue(
            any(
                "model_state_dict shape mismatch for shared_encoder.0.weight" in error
                and "expected (16, 77)" in error
                and "got (15, 77)" in error
                for error in shape_errors
            )
        )

        hash_payload = copy.deepcopy(build_checkpoint_payload())
        hash_payload["model_state_dict"][key][0, 0] += 1
        hash_errors = validate_v1_7_strategy_manager_checkpoint_payload(hash_payload)
        self.assertTrue(
            any(
                error.startswith("state_hash: expected ") and ", got " in error
                for error in hash_errors
            )
        )

    def test_loader_uses_explicit_non_weights_only_mode_and_reports_missing_path(self):
        missing = Path("missing-v1-7-bootstrap.pt")
        with self.assertRaisesRegex(FileNotFoundError, str(missing)):
            V17StrategyManagerPolicy.from_checkpoint(missing)

        with tempfile.TemporaryDirectory() as directory:
            path = self.save_checkpoint(directory, build_checkpoint_payload())
            original_load = torch.load
            with mock.patch(
                "agents.v1_7_strategy_manager.torch.load",
                wraps=original_load,
            ) as load:
                V17StrategyManagerPolicy.from_checkpoint(path)

        self.assertEqual(load.call_args.kwargs["map_location"], "cpu")
        self.assertFalse(load.call_args.kwargs["weights_only"])

    def test_headless_clis_accept_checkpoint_backed_policy(self):
        arguments = [
            "--policy-a",
            POLICY_TYPE,
            "--checkpoint-a",
            "bootstrap.pt",
        ]
        self.assertEqual(parse_arena_args(arguments).policy_a, POLICY_TYPE)
        self.assertEqual(parse_realtime_arena_args(arguments).policy_a, POLICY_TYPE)
        self.assertEqual(parse_spectate_args(arguments).policy_a, POLICY_TYPE)

    def test_launcher_accepts_only_compatible_bootstrap_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.save_checkpoint(directory, build_checkpoint_payload())
            manager = LauncherSettingsManager(
                repo_root=root,
                store=LauncherPresetStore(root / "presets.json"),
            )
            manager.update("spectate", "policy_a", POLICY_TYPE)
            manager.update("spectate", "checkpoint_a", str(path))

            self.assertEqual(manager.validate("spectate"), [])

            incompatible = build_checkpoint_payload()
            incompatible["checkpoint_metadata"]["model_version"] = "v9.9.9"
            bad_path = root / "incompatible.pt"
            torch.save(incompatible, bad_path)
            manager.update("spectate", "checkpoint_a", str(bad_path))

            errors = manager.validate("spectate")
            self.assertTrue(
                any(
                    "checkpoint_a: checkpoint_metadata.model_version: "
                    "expected 'v1.7.2', got 'v9.9.9'" in error
                    for error in errors
                )
            )


if __name__ == "__main__":
    unittest.main()
