import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from eval.promotion_gate import GateConfig, apply_evaluation, build_evaluation, initialize_registry, rollback
from human_data.audit import (
    append_audit_event,
    build_audit_report,
    execute_deletion,
    plan_session_deletion,
    report_markdown,
)
from train.artifacts import file_sha256


class TestHumanDataAudit(unittest.TestCase):
    SESSION_ID = "89abcdef0123456789abcdef01234567"

    def _session(self, dataset: Path) -> Path:
        path = dataset / "sessions" / self.SESSION_ID
        path.mkdir(parents=True)
        (path / "human_session_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": "puyo.human_session_manifest.v1",
                    "session_id": self.SESSION_ID,
                    "created_at_utc": "2026-06-29T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        return path

    def _derived_run(self, training: Path, *, parent: Path) -> tuple[Path, Path]:
        run = training / "derived-89"
        checkpoint = run / "checkpoints" / "challenger.pt"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(b"derived")
        (run / "summary.json").write_text(
            json.dumps(
                {
                    "schema_version": "puyo.human_training_summary.v1",
                    "run_id": "derived-89",
                    "created_at_utc": "2026-06-29T00:01:00Z",
                    "session_ids": [self.SESSION_ID],
                    "checkpoint_path": str(checkpoint),
                    "parent_checkpoint_path": str(parent),
                }
            ),
            encoding="utf-8",
        )
        return run, checkpoint

    def _registry(self, path: Path, champion: Path) -> Path:
        initialize_registry(path, champion_path=champion)
        return path

    def _metrics(self) -> dict:
        return {
            "arena": {"challenger_score_rate": 0.75},
            "tactical_scenarios": {"challenger_score_rate": 0.6, "champion_score_rate": 0.5},
            "chain_benchmark": {"challenger_mean_max_chain": 3.0, "champion_mean_max_chain": 3.0},
            "operation_guard": {"failure_rate": 0.0, "deadline_miss_rate": 0.0},
            "latency_guard": {"mean_policy_elapsed_ms": 5.0},
        }

    def test_end_to_end_report_traces_collection_training_promotion_and_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            training = root / "training"
            session = self._session(dataset)
            champion = root / "models" / "champion.pt"
            champion.parent.mkdir()
            champion.write_bytes(b"champion")
            _, challenger = self._derived_run(training, parent=champion)
            registry_path = self._registry(root / "registry" / "model_registry.json", champion)

            append_audit_event(dataset / "audit_events.jsonl", event="collection.saved", resource_type="human_session", resource_id=self.SESSION_ID)
            append_audit_event(training / "audit_events.jsonl", event="training.completed", resource_type="derived_run", resource_id="derived-89", details={"session_ids": [self.SESSION_ID]})
            initial = json.loads(registry_path.read_text(encoding="utf-8"))
            challenger_record = {"path": str(challenger.resolve()), "sha256": file_sha256(challenger)}
            evaluation = build_evaluation(initial["roles"]["champion"], challenger_record, GateConfig(), self._metrics())
            apply_evaluation(registry_path, evaluation, artifact_path=root / "evaluation.json", opponent_pool_limit=8)
            rollback(registry_path, reason="audit e2e")

            report = build_audit_report(dataset_root=dataset, training_root=training, registry_path=registry_path)
            markdown = report_markdown(report)

            self.assertEqual(report["sessions"][0]["session_id"], self.SESSION_ID)
            self.assertEqual(report["derived_runs"][0]["session_ids"], [self.SESSION_ID])
            self.assertEqual([item["kind"] for item in report["model_registry"]["transitions"]], ["promotion", "rollback"])
            self.assertIn("derived-89", markdown)
            self.assertIn("gate.promotion.authorized", [event["event"] for event in report["events"]])
            self.assertTrue(session.exists())

    def test_deletion_preview_blocks_registry_referenced_derived_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            training = root / "training"
            self._session(dataset)
            parent = root / "parent.pt"
            parent.write_bytes(b"parent")
            _, challenger = self._derived_run(training, parent=parent)
            registry = self._registry(root / "registry.json", challenger)

            plan = plan_session_deletion(dataset_root=dataset, training_root=training, registry_path=registry, session_id=self.SESSION_ID)

            self.assertTrue(plan["blocked"])
            self.assertEqual(plan["protected_references"][0]["kind"], "role:champion")
            with self.assertRaisesRegex(RuntimeError, "blocked"):
                execute_deletion(plan, confirmation_token=plan["confirmation_token"])
            self.assertTrue(challenger.exists())

    def test_confirmed_deletion_moves_session_and_derived_run_but_preserves_parent_and_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            training = root / "training"
            session = self._session(dataset)
            parent = root / "parent.pt"
            parent.write_bytes(b"parent")
            run, _ = self._derived_run(training, parent=parent)
            active = root / "active.pt"
            active.write_bytes(b"active")
            registry = self._registry(root / "registry.json", active)
            before = file_sha256(registry)
            plan = plan_session_deletion(dataset_root=dataset, training_root=training, registry_path=registry, session_id=self.SESSION_ID)

            result = execute_deletion(plan, confirmation_token=plan["confirmation_token"])

            self.assertTrue(result["deleted"])
            self.assertFalse(session.exists())
            self.assertFalse(run.exists())
            self.assertTrue(parent.exists())
            self.assertTrue(active.exists())
            self.assertEqual(file_sha256(registry), before)

    def test_disk_error_rolls_back_partial_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            training = root / "training"
            session = self._session(dataset)
            parent = root / "parent.pt"
            parent.write_bytes(b"parent")
            run, _ = self._derived_run(training, parent=parent)
            registry = self._registry(root / "registry.json", parent)
            plan = plan_session_deletion(dataset_root=dataset, training_root=training, registry_path=registry, session_id=self.SESSION_ID)
            real_move = shutil.move
            calls = 0

            def fail_second_move(source, target):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected disk error")
                return real_move(source, target)

            with mock.patch("human_data.audit.shutil.move", side_effect=fail_second_move):
                with self.assertRaisesRegex(OSError, "injected disk error"):
                    execute_deletion(plan, confirmation_token=plan["confirmation_token"])

            self.assertTrue(session.exists())
            self.assertTrue(run.exists())
            self.assertTrue(parent.exists())


if __name__ == "__main__":
    unittest.main()
