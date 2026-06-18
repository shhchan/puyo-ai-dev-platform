import json
import tempfile
import unittest
from pathlib import Path

from train.artifacts import (
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    CHECKPOINT_SCHEMA_VERSION,
    attach_checkpoint_schema,
    file_sha256,
    validate_artifact_manifest,
    validate_checkpoint_payload,
    write_artifact_manifest,
)


class TestTrainingArtifacts(unittest.TestCase):
    def test_checkpoint_schema_is_attached_and_validated(self):
        config = {"seed": 7, "total_timesteps": 128}
        payload = attach_checkpoint_schema(
            {
                "model_state_dict": {"weight": [1, 2, 3]},
                "optimizer_state_dict": {"state": {}},
                "config": config,
                "global_step": 64,
            },
            trainer_name="unit_trainer",
            run_id="run-seed7",
            checkpoint_kind="latest",
            global_step=64,
            config=config,
            git_commit="abc123",
            seed=7,
            parent_checkpoint_path="runs/parent/checkpoints/best.pt",
            environment_progress={"episodes": 3},
        )

        self.assertEqual(payload["artifact_schema_version"], CHECKPOINT_SCHEMA_VERSION)
        self.assertEqual(payload["checkpoint_schema"]["trainer_name"], "unit_trainer")
        self.assertEqual(payload["checkpoint_schema"]["run_id"], "run-seed7")
        self.assertEqual(payload["checkpoint_schema"]["parent_checkpoint_path"], "runs/parent/checkpoints/best.pt")
        self.assertTrue(payload["checkpoint_schema"]["resume_contract"]["has_optimizer_state"])
        self.assertEqual(validate_checkpoint_payload(payload), [])

    def test_manifest_records_file_hashes_and_detects_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            metrics_path = run_dir / "metrics.csv"
            checkpoint_path = run_dir / "checkpoints" / "latest.pt"
            metrics_path.write_text("global_step,metric,value\n1,loss,0.5\n", encoding="utf-8")
            checkpoint_path.parent.mkdir()
            checkpoint_path.write_bytes(b"checkpoint")

            manifest = write_artifact_manifest(
                run_dir=run_dir,
                run_id="unit-run",
                trainer_name="unit_trainer",
                config={"seed": 1},
                git_commit="abc123",
                seed=1,
                artifacts={"metrics": metrics_path},
                checkpoints={"latest": checkpoint_path},
            )

            self.assertEqual(manifest["schema_version"], ARTIFACT_MANIFEST_SCHEMA_VERSION)
            self.assertEqual(manifest["artifacts"][0]["sha256"], file_sha256(metrics_path))
            self.assertEqual(manifest["checkpoints"][0]["path"], "checkpoints/latest.pt")
            self.assertEqual(validate_artifact_manifest(manifest, run_dir=run_dir), [])

            loaded = json.loads((run_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(loaded["run"]["run_id"], "unit-run")

            metrics_path.write_text("changed\n", encoding="utf-8")
            errors = validate_artifact_manifest(manifest, run_dir=run_dir)
            self.assertTrue(any("sha256 mismatch" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
