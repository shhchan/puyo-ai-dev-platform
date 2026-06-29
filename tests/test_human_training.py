import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch

from agents.networks import PuyoActorCritic, VECTOR_FEATURE_DIM
from human_data.dataset import create_session
from human_data.sampler import sample_human_dataset
from puyo_env.realtime_ai import build_realtime_observation, realtime_checkpoint_metadata
from puyo_env.realtime_versus import RealtimeVersusMatch
from src.core.realtime import TickInput
from train.artifacts import attach_checkpoint_schema, file_sha256
from train.human_training import (
    JOB_SCHEMA_VERSION,
    HumanTrainingConfig,
    control_job,
    train_human_derived,
)
from train.lineage import ancestors, build_registry


class TestHumanTraining(unittest.TestCase):
    SESSION_ID = "abcdef0123456789abcdef0123456789"

    def _dataset(self, root: Path) -> Path:
        match = RealtimeVersusMatch(seed=87)
        ticks = []
        for tick in range(180):
            inputs = {
                "player_0": TickInput.from_names(press=("DOWN",)) if tick == 0 else TickInput(),
                "player_1": TickInput.from_names(press=("DOWN",)) if tick == 0 else TickInput(),
            }
            result = match.step(inputs)
            ticks.append(
                {
                    "tick": result.tick,
                    "inputs": {agent: value.to_json() for agent, value in inputs.items()},
                    "snapshot_hash": result.snapshot_hash,
                }
            )
        replay = {
            "format": "puyo-realtime-match-v1",
            "seed": 87,
            "max_ticks": len(ticks),
            "ticks": ticks,
            "expected_final_hash": match.state_hash(),
        }
        create_session(
            root,
            replay,
            session_id=self.SESSION_ID,
            models={"player_0": {"policy": "human"}, "player_1": {"policy": "random"}},
            config={"collection_enabled": True},
            outcome={"winner": "player_0"},
        )
        return root

    def _checkpoint(self, path: Path) -> Path:
        match = RealtimeVersusMatch(seed=87)
        observation = build_realtime_observation(match, "player_0")
        agent = PuyoActorCritic(board_shape=tuple(observation["board"].shape), vector_dim=VECTOR_FEATURE_DIM)
        config = {"seed": 87}
        payload = attach_checkpoint_schema(
            {
                "model_state_dict": agent.state_dict(),
                "config": config,
                "run_id": "parent",
                "global_step": 0,
                "board_shape": agent.board_shape,
                "vector_dim": agent.vector_dim,
                "realtime_policy": realtime_checkpoint_metadata(native_realtime=True),
            },
            trainer_name="realtime_ppo",
            run_id="parent",
            checkpoint_kind="latest",
            global_step=0,
            config=config,
            git_commit="test",
            seed=87,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(payload, path)
        return path

    def test_sampler_reconstructs_human_placements_deterministically(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset_root = self._dataset(Path(directory) / "dataset")
            first = sample_human_dataset(dataset_root, seed=87)
            second = sample_human_dataset(dataset_root, seed=87)
            mixed = sample_human_dataset(dataset_root, method="mixed_replay", self_play_ratio=0.5, seed=87)
            advantaged = sample_human_dataset(
                dataset_root,
                method="advantage_weighted",
                minimum_advantage=1.0,
                seed=87,
            )

            self.assertGreater(first.selection["human_sample_count"], 0)
            self.assertEqual(first.selection, second.selection)
            self.assertEqual(
                [(sample.tick, sample.action_index) for sample in first.samples],
                [(sample.tick, sample.action_index) for sample in second.samples],
            )
            self.assertGreater(mixed.selection["self_play_sample_count"], 0)
            self.assertTrue(all(sample.weight == 1.0 for sample in advantaged.samples))

    def test_training_writes_challenger_lineage_and_preserves_active_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_root = self._dataset(root / "dataset")
            parent = self._checkpoint(root / "active" / "current.pt")
            before = file_sha256(parent)
            result = train_human_derived(
                HumanTrainingConfig(
                    seed=87,
                    run_id="derived-test",
                    log_dir=str(root / "runs"),
                    dataset_root=str(dataset_root),
                    parent_checkpoint_path=str(parent),
                    active_checkpoint_path=str(parent),
                    epochs=1,
                    batch_size=4,
                    small_dataset_threshold=100,
                )
            )

            self.assertEqual(file_sha256(parent), before)
            self.assertTrue(Path(result["checkpoint_path"]).is_file())
            self.assertTrue(result["active_checkpoint_unchanged"])
            self.assertIn("small_dataset_bias", result["warnings"])
            selection = json.loads(Path(result["dataset_selection_path"]).read_text(encoding="utf-8"))
            self.assertEqual(selection["sessions"][0]["session_id"], self.SESSION_ID)
            registry = build_registry([dataset_root, result["run_dir"]])
            self.assertIn(f"human_session:{self.SESSION_ID}", ancestors(registry, "run:derived-test"))

    def test_job_pause_resume_and_cancel_are_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "job.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": JOB_SCHEMA_VERSION,
                        "job_id": "job",
                        "state": "running",
                        "pid": 123,
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch("train.human_training.os.kill") as kill:
                paused = control_job("job", "pause", job_root=root)
                resumed = control_job("job", "resume", job_root=root)
                cancelled = control_job("job", "cancel", job_root=root)

            self.assertEqual((paused["state"], resumed["state"], cancelled["state"]), ("paused", "running", "cancelled"))
            self.assertEqual(kill.call_count, 3)


if __name__ == "__main__":
    unittest.main()
