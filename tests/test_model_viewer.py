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
    def _write_run(self, root: Path) -> None:
        run_dir = root / "viewer-run"
        checkpoint = run_dir / "checkpoints" / "latest.pt"
        summary = run_dir / "summary.json"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(b"checkpoint")
        summary.write_text(
            json.dumps({"run_id": "viewer-run", "mean_win_rate": 0.5}),
            encoding="utf-8",
        )
        write_artifact_manifest(
            run_dir=run_dir,
            run_id="viewer-run",
            trainer_name="viewer_test",
            config={"seed": 83},
            git_commit="abc123",
            seed=83,
            artifacts={"summary": summary},
            checkpoints={"latest": checkpoint},
        )

    def _write_match_replay(self, root: Path) -> Path:
        replay = root / "replay.json"
        replay.write_text(
            json.dumps(
                {
                    "format": "puyo-realtime-match-v1",
                    "seed": 83,
                    "expected_final_hash": "final",
                    "ticks": [
                        {
                            "tick": 1,
                            "snapshot_hash": "hash-1",
                            "inputs": {"player_0": {"press": ["LEFT"]}},
                            "policy_diagnostics": {
                                "player_0": {
                                    "plan_id": "plan-1",
                                    "plan": {"schema_version": "n-turn-plan-v1"},
                                }
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

            _, replay_format, seed, final_hash, timeline = load_replay_timeline(replay)

            self.assertEqual(replay_format, "puyo-realtime-match-v1")
            self.assertEqual(seed, 83)
            self.assertEqual(final_hash, "final")
            self.assertEqual(timeline[0].plan_ids, ("plan-1",))

    def test_model_viewer_report_summarizes_replay_and_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_run(root)
            replay = self._write_match_replay(root)

            data = build_model_viewer_data(replay_path=replay, lineage_roots=(str(root),))
            controller = ModelViewerController(data)
            controller.toggle_bookmark()
            controller.change_speed(1)
            report = controller.report()

            self.assertEqual(report["replay"]["plan_ids"], ["plan-1"])
            self.assertEqual(report["replay"]["bookmarks"], [1])
            self.assertEqual(report["replay"]["playback_stride"], 2)
            self.assertEqual(report["replay"]["mode"], "timeline")
            self.assertEqual(report["lineage"]["runs"], 1)
            self.assertEqual(report["lineage"]["checkpoints"], 1)
            self.assertEqual(report["lineage"]["selected_node"]["node_type"], "checkpoint")
            self.assertEqual(report["lineage"]["selected_node"]["parents"], ["run:viewer-run"])

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
