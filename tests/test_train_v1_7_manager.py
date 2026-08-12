import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch

from agents.beam_search import BUILD_POTENTIAL_SCHEMA_VERSION
from agents.state_analyzer import ANALYZER_DIAGNOSTICS_SCHEMA_VERSION
from agents.v1_7_strategy_manager import (
    BUILD_POTENTIAL_CHECKPOINT_MIGRATION_SCHEMA_VERSION,
    CHECKPOINT_METADATA_SCHEMA_VERSION,
    PARENT_LINEAGE_NODE_ID,
    POLICY_TYPE,
    V17StrategyFeatureEncoder,
    migrate_build_potential_v2_checkpoint_payload,
    validate_v1_7_strategy_manager_checkpoint_payload,
)
from agents.v1_7_tactics import load_tactic_registry
from eval.analyzer_scenarios import evaluate_scenarios, load_scenarios
from train.artifacts import validate_artifact_manifest, validate_checkpoint_payload
from train.restore import checkpoint_state_hash
from train.train_v1_7_manager import (
    TRAINER_NAME,
    V17ManagerBootstrapConfig,
    _load_and_validate_dataset,
    _migrate_v1_7_1_dataset_metadata,
    load_config,
    train_v1_7_manager,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "docs/benchmarks/puyo-v1-7-1-bootstrap-dataset-smoke"


class TestTrainV17Manager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario_results = evaluate_scenarios(load_scenarios())

    def config(self, root: Path, run_id: str) -> V17ManagerBootstrapConfig:
        return V17ManagerBootstrapConfig(
            seed=126,
            run_id=run_id,
            log_dir=str(root),
            dataset_dir=str(DATASET),
            scenario_dataset=str(ROOT / "eval/scenarios/v1_7_analyzer.json"),
            epochs=1,
            batch_size=8,
            hidden_dim=16,
        )

    def run_training(self, config):
        with (
            mock.patch(
                "train.train_v1_7_manager.validate_bootstrap_dataset",
                return_value=[],
            ),
            mock.patch(
                "train.train_v1_7_manager.evaluate_scenarios",
                return_value=self.scenario_results,
            ),
        ):
            return train_v1_7_manager(config)

    def test_config_loads_and_coerces_overrides(self):
        config = load_config(
            ROOT / "train/config/v1_7_manager_bootstrap.yaml",
            ["epochs=2", "deterministic=false", "hidden_dim=32"],
        )

        self.assertEqual(config.epochs, 2)
        self.assertFalse(config.deterministic)
        self.assertEqual(config.hidden_dim, 32)
        with self.assertRaisesRegex(ValueError, "unknown config field"):
            load_config(
                ROOT / "train/config/v1_7_manager_bootstrap.yaml",
                ["unknown=1"],
            )

    def test_real_legacy_dataset_loads_and_migrates_metadata_only(self):
        config = self.config(Path("unused"), "legacy-dataset-load")
        loaded = _load_and_validate_dataset(config)
        stored_features = tuple(
            copy.deepcopy(sample["features"])
            for sample in (*loaded.train, *loaded.validation)
        )
        encoder = V17StrategyFeatureEncoder(load_tactic_registry())

        migrated = _migrate_v1_7_1_dataset_metadata(loaded, encoder)

        self.assertEqual(
            migrated.manifest["schemas"]["analyzer_diagnostics"],
            ANALYZER_DIAGNOSTICS_SCHEMA_VERSION,
        )
        self.assertEqual(
            migrated.manifest["schemas"]["build_potential"],
            BUILD_POTENTIAL_SCHEMA_VERSION,
        )
        self.assertEqual(migrated.manifest["feature_contract"]["context_dim"], 77)
        self.assertEqual(
            tuple(
                sample["features"]
                for sample in (*migrated.train, *migrated.validation)
            ),
            stored_features,
        )

    def test_training_writes_reproducible_checkpoint_metrics_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.run_training(self.config(root, "first"))
            second = self.run_training(self.config(root, "second"))

            self.assertEqual(first["state_hash"], second["state_hash"])
            checkpoint = torch.load(first["checkpoint_path"], map_location="cpu", weights_only=False)
            self.assertEqual(validate_checkpoint_payload(checkpoint), [])
            self.assertEqual(
                validate_v1_7_strategy_manager_checkpoint_payload(checkpoint),
                [],
            )
            self.assertEqual(checkpoint["checkpoint_schema"]["trainer_name"], TRAINER_NAME)
            self.assertEqual(checkpoint["policy_type"], POLICY_TYPE)
            self.assertEqual(
                checkpoint["checkpoint_metadata"]["schema_version"],
                CHECKPOINT_METADATA_SCHEMA_VERSION,
            )
            self.assertEqual(
                checkpoint["checkpoint_metadata"]["lineage"]["parent_node_id"],
                PARENT_LINEAGE_NODE_ID,
            )
            self.assertEqual(
                checkpoint["checkpoint_metadata"]["lineage"]["training_run_id"],
                "first",
            )
            self.assertEqual(checkpoint["feature_contract"]["context_dim"], 77)
            self.assertEqual(checkpoint["feature_contract"]["preview_dim"], 23)
            self.assertEqual(
                checkpoint["checkpoint_metadata"]["schemas"]["build_potential"],
                BUILD_POTENTIAL_SCHEMA_VERSION,
            )
            self.assertEqual(
                checkpoint["dataset"]["schemas"]["build_potential"],
                BUILD_POTENTIAL_SCHEMA_VERSION,
            )
            self.assertEqual(checkpoint["scenario_validation"]["passed"], 24)
            self.assertEqual(checkpoint["scenario_validation"]["failed"], 0)
            self.assertFalse(
                checkpoint["lifecycle_carry_contract"]["legacy_implicit_defaults_allowed"]
            )
            self.assertIn(
                "own.score_carry",
                checkpoint["lifecycle_carry_contract"]["required_features"],
            )

            metrics = json.loads(Path(first["metrics_path"]).read_text(encoding="utf-8"))
            self.assertIn("parameter_loss", metrics["final"]["validation"])
            self.assertIn("arbitration_tactic_accuracy", metrics["final"]["validation"])
            confusion = json.loads(
                Path(first["confusion_report_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(len(confusion["splits"]["validation"]["matrix"]), 8)
            parameters = json.loads(
                Path(first["parameter_report_path"]).read_text(encoding="utf-8")
            )
            self.assertIn("mean_normalized_error", parameters["splits"]["validation"]["overall"])
            scenarios = json.loads(
                Path(first["scenario_report_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(scenarios["analyzer"]["summary"]["passed"], 24)
            self.assertEqual(scenarios["model_validation"]["samples"], 24)
            manifest_path = Path(first["manifest_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                manifest["extra"]["checkpoint_metadata"],
                checkpoint["checkpoint_metadata"],
            )
            self.assertEqual(
                validate_artifact_manifest(manifest, run_dir=manifest_path.parent),
                [],
            )

            previous = copy.deepcopy(checkpoint)
            previous_metadata = previous["checkpoint_metadata"]
            previous_metadata["schema_version"] = (
                "puyo.v1_7_strategy_manager.checkpoint_metadata.v1"
            )
            previous_metadata["schemas"].update(
                {
                    "analyzer_diagnostics": "puyo.state_analyzer.diagnostics.v1",
                    "planner_request": "planner-schema-v2",
                    "strategy_diagnostics": (
                        "puyo.v1_7_strategy_manager.diagnostics.v1"
                    ),
                }
            )
            previous_metadata["schemas"].pop("build_potential")
            previous["dataset"]["schemas"]["analyzer_diagnostics"] = (
                "puyo.state_analyzer.diagnostics.v1"
            )
            previous["dataset"]["schemas"].pop("build_potential")
            previous["state_hash"] = checkpoint_state_hash(previous)
            source_state_hash = previous["state_hash"]
            migrated = migrate_build_potential_v2_checkpoint_payload(previous)

            self.assertEqual(
                migrated["schema_migration"]["schema_version"],
                BUILD_POTENTIAL_CHECKPOINT_MIGRATION_SCHEMA_VERSION,
            )
            self.assertEqual(
                migrated["schema_migration"]["source_state_hash"],
                source_state_hash,
            )
            self.assertFalse(migrated["schema_migration"]["weights_changed"])
            self.assertFalse(
                migrated["schema_migration"]["feature_shape_changed"]
            )
            self.assertEqual(migrated["feature_contract"]["context_dim"], 77)
            self.assertTrue(
                all(
                    torch.equal(value, migrated["model_state_dict"][name])
                    for name, value in previous["model_state_dict"].items()
                )
            )
            self.assertEqual(
                validate_v1_7_strategy_manager_checkpoint_payload(migrated),
                [],
            )

    def test_invalid_or_legacy_dataset_fails_before_creating_a_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.config(root / "runs", "invalid")
            with mock.patch(
                "train.train_v1_7_manager.validate_bootstrap_dataset",
                return_value=["strategy feature contract mismatch"],
            ):
                with self.assertRaisesRegex(ValueError, "strategy feature contract mismatch"):
                    train_v1_7_manager(config)
            self.assertFalse((root / "runs" / "invalid").exists())

            mixed = root / "mixed"
            mixed.mkdir()
            manifest = json.loads((DATASET / "dataset_manifest.json").read_text(encoding="utf-8"))
            manifest["counts"]["legacy"] = 1
            (mixed / "dataset_manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            for name in ("train.jsonl", "validation.jsonl", "legacy.jsonl", "rejected.jsonl"):
                shutil.copyfile(DATASET / name, mixed / name)
            legacy_config = self.config(root / "runs", "legacy")
            legacy_config.dataset_dir = str(mixed)
            with mock.patch(
                "train.train_v1_7_manager.validate_bootstrap_dataset",
                return_value=[],
            ):
                with self.assertRaisesRegex(ValueError, "audited nontraining records"):
                    train_v1_7_manager(legacy_config)
            self.assertFalse((root / "runs" / "legacy").exists())


if __name__ == "__main__":
    unittest.main()
