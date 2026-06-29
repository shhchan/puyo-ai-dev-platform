import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from eval.promotion_gate import (
    GateConfig,
    PromotionCriteria,
    apply_evaluation,
    build_evaluation,
    evaluate_criteria,
    evaluate_and_apply,
    initialize_registry,
    load_registry,
    rollback,
)


class TestPromotionGate(unittest.TestCase):
    def _checkpoint(self, root: Path, name: str) -> Path:
        path = root / f"{name}.pt"
        path.write_bytes(f"checkpoint-{name}".encode("ascii"))
        return path

    def _metrics(self, *, win_rate: float = 0.75, failure_rate: float = 0.0) -> dict:
        return {
            "arena": {"challenger_score_rate": win_rate},
            "tactical_scenarios": {
                "challenger_score_rate": 0.60,
                "champion_score_rate": 0.55,
            },
            "chain_benchmark": {
                "challenger_mean_max_chain": 3.0,
                "champion_mean_max_chain": 3.0,
            },
            "operation_guard": {
                "failure_rate": failure_rate,
                "deadline_miss_rate": 0.0,
            },
            "latency_guard": {"mean_policy_elapsed_ms": 10.0},
        }

    def test_criteria_rejects_any_failed_guard(self):
        criteria = PromotionCriteria()
        accepted = evaluate_criteria(self._metrics(), criteria)
        rejected = evaluate_criteria(self._metrics(failure_rate=0.10), criteria)

        self.assertEqual(accepted["decision"], "promote")
        self.assertEqual(rejected["decision"], "reject")
        self.assertEqual(rejected["failed_checks"], ["operation_failure_rate"])

    def test_promotion_is_idempotent_and_rollback_swaps_stable_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            champion_path = self._checkpoint(root, "champion")
            challenger_path = self._checkpoint(root, "challenger")
            registry_path = root / "registry.json"
            artifact_path = root / "evaluation" / "evaluation.json"
            initial = initialize_registry(registry_path, champion_path=champion_path)
            champion = initial["roles"]["champion"]
            challenger = {
                "path": str(challenger_path.resolve()),
                "sha256": hashlib.sha256(challenger_path.read_bytes()).hexdigest(),
            }
            evaluation = build_evaluation(champion, challenger, GateConfig(), self._metrics())

            promoted = apply_evaluation(
                registry_path,
                evaluation,
                artifact_path=artifact_path,
                opponent_pool_limit=8,
            )
            repeated = apply_evaluation(
                registry_path,
                evaluation,
                artifact_path=artifact_path,
                opponent_pool_limit=8,
            )

            self.assertEqual(promoted["roles"]["champion"], challenger)
            self.assertEqual(promoted["roles"]["previous_stable"], champion)
            self.assertIsNone(promoted["roles"]["challenger"])
            self.assertEqual(repeated["revision"], promoted["revision"])
            self.assertEqual(len(repeated["evaluations"]), 1)
            self.assertEqual(repeated["opponent_pool"][0]["sha256"], champion["sha256"])
            self.assertEqual(json.loads(artifact_path.read_text())["verdict"]["decision"], "promote")

            rolled_back = rollback(registry_path, reason="production deadline misses")
            self.assertEqual(rolled_back["roles"]["champion"], champion)
            self.assertEqual(rolled_back["roles"]["previous_stable"], challenger)
            self.assertEqual(rolled_back["transitions"][-1]["kind"], "rollback")

    def test_rejected_challenger_never_becomes_champion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            champion_path = self._checkpoint(root, "champion")
            challenger_path = self._checkpoint(root, "challenger")
            registry_path = root / "registry.json"
            initial = initialize_registry(registry_path, champion_path=champion_path)
            challenger = {
                "path": str(challenger_path.resolve()),
                "sha256": hashlib.sha256(challenger_path.read_bytes()).hexdigest(),
            }
            evaluation = build_evaluation(
                initial["roles"]["champion"],
                challenger,
                GateConfig(),
                self._metrics(win_rate=0.25),
            )

            registry = apply_evaluation(
                registry_path,
                evaluation,
                artifact_path=root / "rejected.json",
                opponent_pool_limit=8,
            )

            self.assertEqual(registry["roles"]["champion"], initial["roles"]["champion"])
            self.assertEqual(registry["roles"]["challenger"], challenger)
            self.assertEqual(registry["transitions"][-1]["kind"], "rejection")

    def test_stale_evaluation_cannot_overwrite_new_champion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._checkpoint(root, "first")
            second = self._checkpoint(root, "second")
            third = self._checkpoint(root, "third")
            registry_path = root / "registry.json"
            initial = initialize_registry(registry_path, champion_path=first)
            second_record = {
                "path": str(second.resolve()),
                "sha256": hashlib.sha256(second.read_bytes()).hexdigest(),
            }
            third_record = {
                "path": str(third.resolve()),
                "sha256": hashlib.sha256(third.read_bytes()).hexdigest(),
            }
            stale = build_evaluation(initial["roles"]["champion"], third_record, GateConfig(), self._metrics())
            current = build_evaluation(initial["roles"]["champion"], second_record, GateConfig(), self._metrics())
            apply_evaluation(
                registry_path,
                current,
                artifact_path=root / "current.json",
                opponent_pool_limit=8,
            )

            with self.assertRaisesRegex(RuntimeError, "champion changed"):
                apply_evaluation(
                    registry_path,
                    stale,
                    artifact_path=root / "stale.json",
                    opponent_pool_limit=8,
                )
            self.assertEqual(load_registry(registry_path)["roles"]["champion"], second_record)

    def test_high_level_evaluate_reuses_promoted_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            champion_path = self._checkpoint(root, "champion")
            challenger_path = self._checkpoint(root, "challenger")
            registry_path = root / "registry.json"
            initialize_registry(registry_path, champion_path=champion_path)
            config = GateConfig()

            from unittest import mock

            with mock.patch("eval.promotion_gate.collect_metrics", return_value=self._metrics()) as collect:
                first = evaluate_and_apply(
                    registry_path,
                    challenger_path,
                    config=config,
                    output_dir=root / "evaluations",
                )
                second = evaluate_and_apply(
                    registry_path,
                    challenger_path,
                    config=config,
                    output_dir=root / "evaluations",
                )

            registry = load_registry(registry_path)
            self.assertEqual(second["evaluation_id"], first["evaluation_id"])
            self.assertEqual(registry["revision"], 2)
            self.assertEqual(len(registry["evaluations"]), 1)
            collect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
