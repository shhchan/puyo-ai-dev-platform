import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from eval.qa_session import (
    QASessionSaveError,
    main,
    save_qa_session,
    validate_qa_session,
)
from puyo_env.realtime_versus import RealtimeVersusMatch


def fixture(*, interrupted=False):
    match = RealtimeVersusMatch(seed=127)
    ticks = []
    for _ in range(4):
        tick = match.step()
        ticks.append({
            "tick": tick.tick,
            "inputs": {agent: {"press": [], "release": []} for agent in ("player_0", "player_1")},
            "snapshot_hash": tick.snapshot_hash,
        })
    replay = {
        "format": "puyo-realtime-match-v1", "seed": 127,
        "policies": {"player_0": {"policy_seed": 57}, "player_1": {}},
        "ticks": ticks, "expected_final_hash": match.state_hash(),
        "outcome": {"interrupted": interrupted},
    }
    result = {
        "schema_version": "puyo.gui_qa.v1",
        "match": {"seed": 127, "speed": 1.0},
        "result": {"ticks": len(ticks), "interrupted": interrupted},
    }
    return replay, result


class TestQASession(unittest.TestCase):
    def test_publishes_immutable_validated_sessions_for_normal_and_interrupted_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            for interrupted in (False, True):
                replay, result = fixture(interrupted=interrupted)
                session, manifest = save_qa_session(
                    directory, replay=replay, result=result,
                    config={"seed": 127, "speed": 1.0, "tsumo_pattern_id": None},
                    source={"git_commit": "abc", "dirty": False},
                    tsumo={"mode": "random", "pattern_id": None, "data_version": None},
                )
                self.assertEqual(validate_qa_session(session), [])
                self.assertEqual(manifest["save"]["interrupted"], interrupted)
                self.assertTrue(manifest["save"]["replay_validated"])
                self.assertIsNone(manifest["identity"]["native"])
                self.assertEqual(manifest["identity"]["tsumo"]["mode"], "random")
                self.assertEqual(manifest["match"]["policy_seeds"], {"player_0": 57, "player_1": None})
                self.assertEqual({item.name for item in session.iterdir()}, {"replay.json", "result.json", "manifest.json"})
                saved_result = json.loads((session / "result.json").read_text(encoding="utf-8"))
                self.assertEqual(saved_result["artifacts"]["replay"], str(session / "replay.json"))
            self.assertEqual(len(list(Path(directory).iterdir())), 2)

    def test_detects_tampering_without_mutating_saved_session(self):
        with tempfile.TemporaryDirectory() as directory:
            replay, result = fixture()
            session, _ = save_qa_session(directory, replay=replay, result=result, config={})
            path = session / "replay.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["ticks"][0]["inputs"]["player_0"]["press"] = ["left"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertIn("replay checksum or size mismatch", validate_qa_session(session))

    def test_copied_bundle_validates_without_original_location(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replay, result = fixture()
            original, _ = save_qa_session(root / "source", replay=replay, result=result, config={})
            copied = root / "other-computer" / original.name
            copied.parent.mkdir()
            shutil.copytree(original, copied)
            shutil.rmtree(original)
            self.assertEqual(validate_qa_session(copied), [])
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(main([str(copied)]), 0)
            self.assertIn("QA session valid", output.getvalue())

    def test_validator_passes_tsumo_source_override_to_replay_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            replay, result = fixture()
            session, _ = save_qa_session(directory, replay=replay, result=result, config={})
            from eval import qa_session
            with patch.object(qa_session, "replay_realtime_match", return_value="ok") as reader:
                self.assertEqual(validate_qa_session(session, tsumo_source_override="/other/haipuyo.txt"), [])
            reader.assert_called_once_with(replay, tsumo_source_override="/other/haipuyo.txt")

    def test_write_failure_keeps_hidden_recovery_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            replay, result = fixture()
            from eval import qa_session
            real_write = qa_session._write_json_atomic

            def fail_result(path, value):
                if path.name == "result.json":
                    raise OSError("disk full")
                real_write(path, value)

            with (
                patch.object(qa_session, "_write_json_atomic", side_effect=fail_result),
                self.assertRaises(QASessionSaveError) as caught,
            ):
                save_qa_session(directory, replay=replay, result=result, config={})
            self.assertTrue(caught.exception.pending_path.name.endswith(".pending"))
            self.assertTrue((caught.exception.pending_path / "replay.json").is_file())
            self.assertEqual(json.loads((caught.exception.pending_path / "save_failure.json").read_text(encoding="utf-8"))["status"], "failed")
            self.assertFalse((Path(directory) / caught.exception.session_id).exists())

    def test_bad_replay_never_creates_session(self):
        with tempfile.TemporaryDirectory() as directory:
            replay, result = fixture()
            replay["ticks"][0]["snapshot_hash"] = "incorrect"
            with self.assertRaises(AssertionError):
                save_qa_session(directory, replay=replay, result=result, config={})
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_publish_refuses_to_replace_an_existing_session(self):
        with tempfile.TemporaryDirectory() as directory:
            from eval.qa_session import _publish_session
            root = Path(directory)
            pending = root / ".reserved.pending"
            published = root / "reserved"
            pending.mkdir()
            published.mkdir()
            (published / "sentinel").write_text("existing", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                _publish_session(pending, published)
            self.assertTrue(pending.exists())
            self.assertEqual((published / "sentinel").read_text(encoding="utf-8"), "existing")


if __name__ == "__main__":
    unittest.main()
