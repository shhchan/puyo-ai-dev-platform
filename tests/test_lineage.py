import json
import tempfile
import unittest
from pathlib import Path

from train.artifacts import write_artifact_manifest
from train.lineage import (
    ancestors,
    build_registry,
    descendants,
    validate_registry,
    write_markdown_report,
    write_registry,
)


class TestLineageRegistry(unittest.TestCase):
    def _write_run(self, root: Path, run_id: str, *, parent_checkpoint_path: str | None = None) -> Path:
        run_dir = root / run_id
        checkpoint = run_dir / "checkpoints" / "latest.pt"
        summary = run_dir / "summary.json"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(f"{run_id}-checkpoint".encode("utf-8"))
        summary.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "global_step": 8,
                    "episodes": 2,
                    "mean_win_rate": 0.5,
                    "checkpoint_path": str(checkpoint),
                }
            ),
            encoding="utf-8",
        )
        write_artifact_manifest(
            run_dir=run_dir,
            run_id=run_id,
            trainer_name="versus_ppo",
            config={"seed": 1},
            git_commit="abc123",
            seed=1,
            artifacts={"summary": summary},
            checkpoints={"latest": checkpoint},
            parent_checkpoint_path=parent_checkpoint_path,
        )
        return checkpoint

    def test_registry_tracks_checkpoint_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent_checkpoint = self._write_run(root, "parent")
            child_checkpoint = self._write_run(root, "child", parent_checkpoint_path=str(parent_checkpoint))

            registry = build_registry([root])
            parent_node = next(
                node for node in registry.nodes.values()
                if node.path == str(parent_checkpoint)
            )
            child_node = next(
                node for node in registry.nodes.values()
                if node.path == str(child_checkpoint)
            )

            self.assertIn("run:child", descendants(registry, parent_node.id))
            self.assertIn(parent_node.id, ancestors(registry, child_node.id))
            self.assertEqual(validate_registry(registry), [])

            output = root / "lineage.json"
            report = root / "lineage.md"
            write_registry(registry, output)
            write_markdown_report(registry, report)

            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema_version"], "puyo.lineage_registry.v1")
            self.assertIn("Model Lineage Report", report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
