import json
import os
import tempfile
import unittest
from pathlib import Path


os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

try:
    import gymnasium  # noqa: F401
    import numpy  # noqa: F401
    import pygame  # noqa: F401

    from eval.ui_regression_smoke import run_smoke

    ENV_AVAILABLE = True
except (ImportError, OSError):
    ENV_AVAILABLE = False
    run_smoke = None


@unittest.skipUnless(ENV_AVAILABLE, "pygame/gymnasium/numpy are not installed")
class TestUiRegressionSmoke(unittest.TestCase):
    def test_integrated_dummy_video_smoke_writes_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result_json = root / "ui-smoke.json"
            viewer_json = root / "viewer.json"
            viewer_markdown = root / "viewer.md"

            report = run_smoke(
                result_json=result_json,
                viewer_json=viewer_json,
                viewer_markdown=viewer_markdown,
                launcher_frames=1,
                match_frames=3,
                viewer_frames=1,
                max_frame_ms=250.0,
            )

            saved = json.loads(result_json.read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertTrue(saved["passed"])
            self.assertEqual(saved["schema_version"], "puyo.ui_regression_smoke.v1")
            self.assertTrue(viewer_json.exists())
            self.assertIn("Puyo Model Viewer Report", viewer_markdown.read_text(encoding="utf-8"))
            self.assertEqual(
                [step["name"] for step in saved["steps"]],
                [
                    "launcher_navigation",
                    "realtime_match_plan_overlay",
                    "model_viewer_replay_lineage",
                ],
            )
            match_step = next(step for step in saved["steps"] if step["name"] == "realtime_match_plan_overlay")
            self.assertGreater(match_step["result"]["ticks"], 0)
            self.assertLessEqual(match_step["average_frame_ms"], match_step["max_frame_ms"])


if __name__ == "__main__":
    unittest.main()
