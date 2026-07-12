import json
import os
import tempfile
import unittest
from pathlib import Path


os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from train.artifacts import write_artifact_manifest
from src.ui.model_viewer import (
    ModelViewerController,
    build_model_viewer_data,
    load_replay_timeline,
    run_model_viewer,
)

try:
    import pygame  # noqa: F401

    PYGAME_AVAILABLE = True
except (ImportError, OSError):
    PYGAME_AVAILABLE = False


class TestModelViewerData(unittest.TestCase):
    def _write_run(
        self,
        root: Path,
        run_id: str = "viewer-run",
        *,
        parent_checkpoint_path: str | None = None,
    ) -> Path:
        run_dir = root / run_id
        checkpoint = run_dir / "checkpoints" / "latest.pt"
        summary = run_dir / "summary.json"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(f"{run_id}-checkpoint".encode("utf-8"))
        summary.write_text(
            json.dumps({"run_id": run_id, "mean_win_rate": 0.5}),
            encoding="utf-8",
        )
        write_artifact_manifest(
            run_dir=run_dir,
            run_id=run_id,
            trainer_name="viewer_test",
            config={"seed": 83},
            git_commit="abc123",
            seed=83,
            artifacts={"summary": summary},
            checkpoints={"latest": checkpoint},
            parent_checkpoint_path=parent_checkpoint_path,
        )
        return checkpoint

    def _write_match_replay(self, root: Path, *, checkpoint_path: Path | None = None) -> Path:
        replay = root / "replay.json"
        player_0_policy = {"policy_type": "manager_rule"}
        if checkpoint_path is not None:
            player_0_policy["checkpoint_path"] = str(checkpoint_path)
        replay.write_text(
            json.dumps(
                {
                    "format": "puyo-realtime-match-v1",
                    "seed": 83,
                    "expected_final_hash": "final",
                    "policies": {
                        "player_0": player_0_policy,
                        "player_1": {"policy_type": "beam"},
                    },
                    "ticks": [
                        {
                            "tick": 1,
                            "snapshot_hash": "hash-1",
                            "inputs": {"player_0": {"press": ["LEFT"]}},
                            "policy_diagnostics": {
                                "player_0": {
                                    "profile_name": "build_large",
                                    "expanded_nodes": 42,
                                    "search_objective": {"kind": "build", "target_chain": 6},
                                    "plan_id": "plan-1",
                                    "plan": {
                                        "schema_version": "n-turn-plan-v1",
                                        "profile_name": "build_large",
                                        "steps": [
                                            {"action": 8, "axis_x": 2, "rotation": "RIGHT"}
                                        ],
                                    },
                                }
                            },
                            "controller_diagnostics": {
                                "player_0": {
                                    "last_decision": {
                                        "action_index": 8,
                                        "axis_x": 2,
                                        "rotation": "RIGHT",
                                        "reason": "policy",
                                        "reachable": True,
                                        "plan_ticks": 31,
                                        "policy_elapsed_seconds": 0.012,
                                    }
                                }
                            },
                            "all_clear_diagnostics": {
                                "schema_version": "puyo.all_clear_diagnostics.v1",
                                "players": {
                                    "player_0": {
                                        "board_empty": True,
                                        "all_clear_achieved": True,
                                        "all_clear_bonus_pending": True,
                                        "all_clear_bonus_consumed": False,
                                    },
                                    "player_1": {
                                        "board_empty": False,
                                        "all_clear_achieved": False,
                                        "all_clear_bonus_pending": False,
                                        "all_clear_bonus_consumed": False,
                                    },
                                },
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return replay

    def test_replay_timeline_reads_policy_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            replay = self._write_match_replay(Path(directory))

            _, replay_format, seed, final_hash, policy_metadata, timeline = load_replay_timeline(replay)

            self.assertEqual(replay_format, "puyo-realtime-match-v1")
            self.assertEqual(seed, 83)
            self.assertEqual(final_hash, "final")
            self.assertEqual(policy_metadata["player_0"]["policy_type"], "manager_rule")
            self.assertEqual(timeline[0].plan_ids, ("plan-1",))
            self.assertTrue(
                timeline[0].all_clear_diagnostics["players"]["player_0"]["all_clear_bonus_pending"]
            )

    def test_model_viewer_report_summarizes_replay_and_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = self._write_run(root)
            replay = self._write_match_replay(root, checkpoint_path=checkpoint)

            data = build_model_viewer_data(replay_path=replay, lineage_roots=(str(root),))
            controller = ModelViewerController(data)
            controller.toggle_bookmark()
            controller.change_speed(1)
            report = controller.report()

            self.assertEqual(report["replay"]["plan_ids"], ["plan-1"])
            self.assertEqual(report["replay"]["bookmarks"], [1])
            self.assertEqual(report["replay"]["playback_stride"], 2)
            self.assertEqual(report["replay"]["mode"], "timeline")
            self.assertEqual(report["replay"]["selected_entry"]["agents"]["player_0"]["policy_type"], "manager_rule")
            self.assertEqual(
                report["replay"]["selected_entry"]["agents"]["player_0"]["decision"]["action_index"],
                8,
            )
            self.assertEqual(
                report["replay"]["selected_entry"]["agents"]["player_0"]["diagnostics"]["profile_name"],
                "build_large",
            )
            self.assertEqual(
                report["replay"]["selected_entry"]["agents"]["player_0"]["all_clear"],
                {
                    "board_empty": True,
                    "all_clear_achieved": True,
                    "all_clear_bonus_pending": True,
                    "all_clear_bonus_consumed": False,
                },
            )
            self.assertEqual(
                report["replay"]["selected_entry"]["agents"]["player_0"]["lineage_node_id"],
                report["lineage"]["selected_node"]["id"],
            )
            self.assertIn(
                "run:viewer-run",
                report["replay"]["selected_entry"]["agents"]["player_0"]["lineage_ancestors"],
            )
            self.assertEqual(report["lineage"]["runs"], 1)
            self.assertEqual(report["lineage"]["checkpoints"], 1)
            self.assertEqual(report["lineage"]["selected_node"]["node_type"], "checkpoint")
            self.assertEqual(report["lineage"]["selected_node"]["parents"], ["run:viewer-run"])

    def test_lineage_summary_represents_branching_model_evolution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent_checkpoint = self._write_run(root, "manager_rule-v1")
            self._write_run(
                root,
                "manager_rule-a-v1.1",
                parent_checkpoint_path=str(parent_checkpoint),
            )
            self._write_run(
                root,
                "manager_rule-b-v1.1",
                parent_checkpoint_path=str(parent_checkpoint),
            )

            data = build_model_viewer_data(lineage_roots=(str(root),))
            parent_node = next(
                node for node in data.lineage.checkpoints
                if node.get("path") == str(parent_checkpoint)
            )
            children = data.lineage.child_nodes(parent_node["id"])

            self.assertEqual(
                {node["id"] for node in children},
                {"run:manager_rule-a-v1.1", "run:manager_rule-b-v1.1"},
            )

    def test_lineage_only_mode_does_not_report_playing_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_run(root)

            data = build_model_viewer_data(lineage_roots=(str(root),))
            controller = ModelViewerController(data)
            controller.toggle_pause()
            report = controller.report()

            self.assertEqual(controller.message, "lineage only")
            self.assertEqual(report["replay"]["mode"], "lineage_only")

    def test_report_includes_model_roles_and_last_gate_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_run(root)
            registry_path = root / "model_registry.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "schema_version": "puyo.model_role_registry.v1",
                        "revision": 3,
                        "roles": {
                            "champion": {"path": "/models/champion.pt", "sha256": "a"},
                            "challenger": None,
                            "previous_stable": {"path": "/models/previous.pt", "sha256": "b"},
                        },
                        "evaluations": [{"artifact_path": "/runs/evaluation.json", "decision": "promote"}],
                        "transitions": [{"kind": "promotion"}],
                        "opponent_pool": [{"sha256": "b"}],
                    }
                ),
                encoding="utf-8",
            )

            data = build_model_viewer_data(lineage_roots=(str(root),), model_registry_path=registry_path)
            report = ModelViewerController(data).report()

            self.assertEqual(report["model_registry"]["revision"], 3)
            self.assertEqual(report["model_registry"]["roles"]["champion"]["path"], "/models/champion.pt")
            self.assertEqual(report["model_registry"]["last_transition"]["kind"], "promotion")
            self.assertEqual(report["model_registry"]["opponent_pool_size"], 1)
            self.assertIsNone(report["replay"]["selected_tick"])
            self.assertEqual(report["lineage"]["selected_node"]["node_type"], "checkpoint")

    def test_lineage_selection_moves_between_registry_nodes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_run(root)
            data = build_model_viewer_data(lineage_roots=(str(root),))
            controller = ModelViewerController(data)
            first = controller.selected_lineage_id

            controller.seek_lineage(1)

            self.assertNotEqual(controller.selected_lineage_id, first)
            self.assertIsNotNone(controller.selected_lineage_node)


@unittest.skipUnless(PYGAME_AVAILABLE, "pygame is not installed")
class TestModelViewerSmoke(unittest.TestCase):
    def test_dummy_video_driver_writes_report_artifacts(self):
        fixture = Path(__file__).parent / "fixtures" / "realtime_replay_seed123.json"
        with tempfile.TemporaryDirectory() as directory:
            report_json = Path(directory) / "viewer.json"
            report_md = Path(directory) / "viewer.md"
            data = build_model_viewer_data(replay_path=fixture, lineage_roots=("docs/benchmarks",))

            result = run_model_viewer(
                data,
                max_frames=1,
                report_json=report_json,
                report_markdown=report_md,
            )

            self.assertEqual(result["schema_version"], "puyo.model_viewer_report.v1")
            self.assertTrue(report_json.exists())
            self.assertIn("Puyo Model Viewer Report", report_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
