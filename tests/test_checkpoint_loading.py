import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch

from agents.networks import PuyoActorCritic
from agents.strategy_manager import StrategyManagerPolicy, manager_checkpoint_metadata
from puyo_env.manager_env import MANAGER_VECTOR_DIM
from selfplay.policies import CheckpointPolicy


class TestCheckpointLoading(unittest.TestCase):
    def _write_placement_checkpoint(self, directory: Path) -> Path:
        model = PuyoActorCritic()
        path = directory / "placement.pt"
        torch.save({"model_state_dict": model.state_dict()}, path)
        return path

    def _write_manager_checkpoint(self, directory: Path) -> Path:
        from agents.strategy_workers import smoke_worker_profiles

        profiles = smoke_worker_profiles()
        model = PuyoActorCritic(
            board_shape=(6, 12, 6),
            vector_dim=MANAGER_VECTOR_DIM,
            action_dim=len(profiles),
        )
        payload = {
            **manager_checkpoint_metadata(profiles),
            "model_state_dict": model.state_dict(),
            "board_shape": (6, 12, 6),
        }
        path = directory / "manager.pt"
        torch.save(payload, path)
        return path

    def test_checkpoint_policy_passes_weights_only_false(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_placement_checkpoint(Path(directory))
            original_load = torch.load
            captured: dict[str, object] = {}

            def wrapped_load(*args, **kwargs):
                captured["kwargs"] = dict(kwargs)
                return original_load(*args, **kwargs)

            with mock.patch("selfplay.policies.torch.load", side_effect=wrapped_load):
                policy = CheckpointPolicy(path)

            self.assertIsNotNone(policy)
            self.assertIn("weights_only", captured["kwargs"])
            self.assertFalse(captured["kwargs"]["weights_only"])

    def test_strategy_manager_policy_passes_weights_only_false(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_manager_checkpoint(Path(directory))
            original_load = torch.load
            captured: dict[str, object] = {}

            def wrapped_load(*args, **kwargs):
                captured["kwargs"] = dict(kwargs)
                return original_load(*args, **kwargs)

            with mock.patch("agents.strategy_manager.torch.load", side_effect=wrapped_load):
                policy = StrategyManagerPolicy(path)

            self.assertIsNotNone(policy)
            self.assertIn("weights_only", captured["kwargs"])
            self.assertFalse(captured["kwargs"]["weights_only"])


if __name__ == "__main__":
    unittest.main()
