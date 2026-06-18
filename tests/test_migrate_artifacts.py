import json
import tempfile
import unittest
from pathlib import Path

from train.artifacts import validate_artifact_manifest
from train.migrate_artifacts import discover_legacy_assets, migrate_legacy_artifacts


class TestMigrateArtifacts(unittest.TestCase):
    def test_migration_writes_metadata_without_touching_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "runs" / "versus_long" / "legacy-run"
            checkpoint = run_dir / "checkpoints" / "latest.pt"
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_bytes(b"legacy checkpoint")
            (run_dir / "config.yaml").write_text("seed: 1\n", encoding="utf-8")
            (run_dir / "metadata.json").write_text('{"git_commit": "abc123"}\n', encoding="utf-8")
            (run_dir / "summary.json").write_text(
                json.dumps({"run_id": "legacy-run", "global_step": 8, "mean_win_rate": 0.5}),
                encoding="utf-8",
            )

            assets = discover_legacy_assets([root / "runs"])
            self.assertEqual(len(assets), 1)
            self.assertEqual(assets[0].trainer_name, "versus_ppo")

            output_dir = root / "migration"
            summary = migrate_legacy_artifacts([root / "runs"], output_dir)

            self.assertEqual(summary["asset_count"], 1)
            self.assertFalse((run_dir / "artifact_manifest.json").exists())
            record = summary["records"][0]
            self.assertEqual(record["status"], "migrated")
            manifest_path = Path(record["manifest_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(validate_artifact_manifest(manifest, run_dir=manifest_path.parent), [])
            self.assertTrue((output_dir / "migration_records.csv").exists())
            self.assertTrue((output_dir / "migration_report.md").exists())


if __name__ == "__main__":
    unittest.main()
