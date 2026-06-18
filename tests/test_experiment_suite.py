import json
import tempfile
import unittest
from pathlib import Path

try:
    from train.experiment_suite import build_run_matrix, load_suite_definition, run_suite

    AVAILABLE = True
except ImportError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, "experiment suite dependencies are not installed")
class TestExperimentSuite(unittest.TestCase):
    def _write_suite(self, root: Path, *, seeds=(1,), scenarios=None) -> Path:
        scenarios = scenarios or [{"name": "random", "overrides": {"opponent_policy": "random"}}]
        suite_path = root / "suite.yaml"
        suite_path.write_text(
            "\n".join(
                [
                    "name: unit-suite",
                    "trainer: versus_ppo",
                    "config: train/config/versus_long_smoke.yaml",
                    f"output_dir: {root / 'suite-output'}",
                    f"seeds: {list(seeds)}",
                    "replicates: 1",
                    "max_parallel: 1",
                    "metrics: [global_step, episodes, mean_win_rate, mean_episode_score]",
                    "overrides:",
                    "  total_timesteps: 4",
                    "  num_envs: 1",
                    "  num_steps: 2",
                    "  minibatch_size: 2",
                    "  max_episode_steps: 2",
                    "  checkpoint_interval_updates: 0",
                    "  keep_best_checkpoint: false",
                    "scenarios:",
                    *[
                        f"  - name: {scenario['name']}\n    overrides: {scenario.get('overrides', {})}"
                        for scenario in scenarios
                    ],
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return suite_path

    def test_run_matrix_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite_path = self._write_suite(
                root,
                seeds=(2, 1),
                scenarios=[
                    {"name": "random", "overrides": {"opponent_policy": "random"}},
                    {"name": "greedy", "overrides": {"opponent_policy": "greedy"}},
                ],
            )

            suite = load_suite_definition(suite_path)
            matrix = build_run_matrix(suite)

            self.assertEqual(
                [spec.run_id for spec in matrix],
                [
                    "unit-suite-random-seed2-rep1",
                    "unit-suite-random-seed1-rep1",
                    "unit-suite-greedy-seed2-rep1",
                    "unit-suite-greedy-seed1-rep1",
                ],
            )

    def test_suite_smoke_run_and_resume_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite_path = self._write_suite(root)

            manifest = run_suite(suite_path)
            self.assertEqual(manifest["records"][0]["status"], "completed")
            self.assertEqual(manifest["aggregate"]["status_counts"]["completed"], 1)
            self.assertTrue((root / "suite-output" / "summary.json").exists())
            self.assertTrue((root / "suite-output" / "suite_manifest.json").exists())

            skipped = run_suite(suite_path)
            self.assertEqual(skipped["records"][0]["status"], "skipped")

            summary = json.loads((root / "suite-output" / "summary.json").read_text(encoding="utf-8"))
            self.assertIn("mean_win_rate", summary["overall"])


if __name__ == "__main__":
    unittest.main()
