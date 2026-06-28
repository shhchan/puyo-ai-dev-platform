import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from human_data.dataset import (
    create_session,
    delete_session,
    quarantine_invalid_sessions,
    rebuild_index,
    replay_session,
    validate_session,
)
from puyo_env.realtime_versus import RealtimeVersusMatch
from train.lineage import ancestors, build_registry


class TestHumanDataset(unittest.TestCase):
    SESSION_ID = "0123456789abcdef0123456789abcdef"

    def _replay(self):
        match = RealtimeVersusMatch(seed=86)
        ticks = []
        for _ in range(80):
            result = match.step()
            ticks.append(
                {
                    "tick": result.tick,
                    "inputs": {
                        "player_0": {"press": [], "release": []},
                        "player_1": {"press": [], "release": []},
                    },
                    "snapshot_hash": result.snapshot_hash,
                }
            )
        replay = {
            "format": "puyo-realtime-match-v1",
            "seed": 86,
            "max_ticks": 80,
            "ticks": ticks,
            "expected_final_hash": match.state_hash(),
        }
        return SimpleNamespace(final_hash=match.state_hash(), winner=None), replay

    def _create(self, root: Path):
        checkpoint = root / "models" / "opponent.pt"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(b"checkpoint")
        result, replay = self._replay()
        manifest = create_session(
            root,
            replay,
            session_id=self.SESSION_ID,
            models={
                "player_0": {"policy": "human"},
                "player_1": {
                    "policy": "checkpoint",
                    "model_id": "opponent-v1",
                    "checkpoint_path": str(checkpoint),
                },
            },
            config={"max_ticks": 80, "collection_enabled": True},
            environment={"git_commit": "abc123"},
            outcome={"winner": result.winner},
        )
        return root / "sessions" / self.SESSION_ID, manifest, result

    def test_session_validates_replays_and_tracks_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session_dir, manifest, result = self._create(root)

            self.assertEqual(validate_session(session_dir), [])
            self.assertEqual(replay_session(session_dir), result.final_hash)
            self.assertEqual(manifest["models"]["player_1"]["model_id"], "opponent-v1")
            index = json.loads((root / "dataset_index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["session_count"], 1)

            registry = build_registry([root])
            session_node = f"human_session:{self.SESSION_ID}"
            model_node = next(
                node.id for node in registry.nodes.values()
                if node.node_type == "dataset_model" and node.label == "opponent-v1"
            )
            self.assertIn(model_node, ancestors(registry, session_node))
            self.assertTrue(any(node.node_type == "environment" for node in registry.nodes.values()))

    def test_checksum_drift_is_quarantined_and_index_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session_dir, _, _ = self._create(root)
            trajectory_path = session_dir / "trajectory.json"
            trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
            trajectory["ticks"][0]["rewards"]["player_0"] = 1.0
            trajectory_path.write_text(json.dumps(trajectory), encoding="utf-8")

            self.assertTrue(any("sha256 mismatch" in error for error in validate_session(session_dir)))
            records = quarantine_invalid_sessions(root)
            self.assertEqual(len(records), 1)
            self.assertTrue((root / "quarantine" / self.SESSION_ID / "quarantine_reason.json").exists())
            self.assertEqual(rebuild_index(root)["session_count"], 0)

    def test_individual_session_can_be_deleted_and_reindexed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._create(root)

            self.assertTrue(delete_session(root, self.SESSION_ID))
            self.assertFalse(delete_session(root, self.SESSION_ID))
            self.assertEqual(rebuild_index(root)["sessions"], [])


if __name__ == "__main__":
    unittest.main()
