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

    def test_registry_recovers_legacy_run_without_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "legacy-run"
            checkpoint_dir = run_dir / "checkpoints"
            checkpoint_dir.mkdir(parents=True)
            step_1 = checkpoint_dir / "step_128.pt"
            step_2 = checkpoint_dir / "step_256.pt"
            latest = checkpoint_dir / "latest.pt"
            best = checkpoint_dir / "best.pt"
            for path in (step_1, step_2, latest, best):
                path.write_bytes(path.name.encode("utf-8"))
            (run_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "run_id": "legacy-run",
                        "global_step": 256,
                        "episodes": 4,
                        "mean_win_rate": 0.75,
                        "checkpoint_path": str(latest),
                        "best_checkpoint_path": str(best),
                        "periodic_checkpoints": [str(step_1), str(step_2)],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "metadata.json").write_text(
                json.dumps({"run_id": "legacy-run", "git_commit": "legacy123"}),
                encoding="utf-8",
            )

            registry = build_registry([root])
            run = registry.nodes["run:legacy-run"]
            checkpoints = [
                node for node in registry.nodes.values()
                if node.node_type == "checkpoint" and node.metadata.get("run_id") == "legacy-run"
            ]
            progress_edges = [edge for edge in registry.edges if edge.edge_type == "advances_to"]

            self.assertTrue(run.metadata["legacy"])
            self.assertEqual(run.metadata["metrics"]["mean_win_rate"], 0.75)
            self.assertEqual(len(checkpoints), 4)
            self.assertGreaterEqual(len(progress_edges), 2)
            self.assertEqual(validate_registry(registry), [])

    def test_registry_tracks_selfplay_snapshots_and_evaluations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "manager-run"
            checkpoint = run_dir / "checkpoints" / "latest.pt"
            best = run_dir / "checkpoints" / "best.pt"
            opponent = run_dir / "opponents" / "manager-step-64.pt"
            summary = run_dir / "summary.json"
            pool = run_dir / "opponent_pool.json"
            evaluations = run_dir / "selfplay_evaluations.json"
            opponent.parent.mkdir(parents=True)
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            for path in (checkpoint, best, opponent):
                path.write_bytes(path.name.encode("utf-8"))
            summary.write_text(
                json.dumps(
                    {
                        "run_id": "manager-run",
                        "global_step": 64,
                        "episodes": 2,
                        "mean_win_rate": 0.5,
                    }
                ),
                encoding="utf-8",
            )
            pool.write_text(
                json.dumps(
                    {
                        "snapshots": [
                            {
                                "name": "manager_step_64",
                                "policy_type": "manager",
                                "checkpoint_path": str(opponent),
                                "rating": 1012.0,
                                "games_played": 2,
                                "metadata": {
                                    "role": "selfplay_snapshot",
                                    "global_step": 64,
                                    "parent_checkpoint_path": str(best),
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            evaluations.write_text(
                json.dumps(
                    [
                        {
                            "global_step": 64,
                            "latest_name": "manager_step_64",
                            "opponent_name": "greedy_score",
                            "latest_rating": 1012.0,
                            "opponent_rating": 988.0,
                            "games": 2,
                            "win_rate": 0.5,
                            "mean_score": 1200.0,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            write_artifact_manifest(
                run_dir=run_dir,
                run_id="manager-run",
                trainer_name="manager_ppo",
                config={"seed": 1},
                git_commit="abc123",
                seed=1,
                artifacts={
                    "summary": summary,
                    "opponent_pool": pool,
                    "selfplay_evaluations": evaluations,
                },
                checkpoints={"latest": checkpoint, "best": best, "opponent_snapshot_1": opponent},
            )

            registry = build_registry([root])
            snapshot_nodes = [
                node for node in registry.nodes.values()
                if node.node_type == "opponent_snapshot"
            ]
            evaluation_nodes = [
                node for node in registry.nodes.values()
                if node.node_type == "selfplay_evaluation"
            ]

            self.assertEqual(len(snapshot_nodes), 1)
            self.assertEqual(len(evaluation_nodes), 1)
            self.assertTrue(
                any(edge.edge_type == "produces" and edge.target == snapshot_nodes[0].id for edge in registry.edges)
            )
            self.assertTrue(
                any(edge.edge_type == "evaluates" and edge.target == evaluation_nodes[0].id for edge in registry.edges)
            )
            self.assertEqual(validate_registry(registry), [])


if __name__ == "__main__":
    unittest.main()
