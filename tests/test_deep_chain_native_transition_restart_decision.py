import json
import tempfile
import unittest
from pathlib import Path

from eval.deep_chain_native_transition_restart_decision import (
    DEFAULT_DECISION_PATH,
    verify_decision,
)


class TestDeepChainNativeTransitionRestartDecision(unittest.TestCase):
    def _mutated_decision(self, mutate) -> dict:
        payload = json.loads(DEFAULT_DECISION_PATH.read_text(encoding="utf-8"))
        mutate(payload)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "final_decision.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return verify_decision(path)

    def test_checked_in_decision_is_integral(self):
        result = verify_decision()

        self.assertTrue(result["passed"], result["issues"])

    def test_human_approval_removal_is_rejected(self):
        result = self._mutated_decision(
            lambda payload: payload["risk_acceptance"].update(
                {"human_reviewer_approval_recorded": False}
            )
        )

        self.assertFalse(result["passed"])
        self.assertIn("risk acceptance differs from its authority", result["issues"])

    def test_stop_decision_substitution_is_rejected(self):
        result = self._mutated_decision(
            lambda payload: payload.update({"decision": "NO_GO_STOP"})
        )

        self.assertFalse(result["passed"])
        self.assertIn(
            "PUYO-213 must record the selected GO_WITH_RISK_ACCEPTANCE outcome",
            result["issues"],
        )

    def test_combined_gate_drift_is_rejected(self):
        result = self._mutated_decision(
            lambda payload: payload["prototype_gate"].update(
                {"combined_transition_evaluator_p95_ms_max": 820.626}
            )
        )

        self.assertFalse(result["passed"])
        self.assertIn(
            "bounded prototype gate differs from its authority", result["issues"]
        )

    def test_authority_digest_drift_is_rejected(self):
        def mutate(payload):
            payload["authority_artifacts"][0]["sha256"] = "0" * 64

        result = self._mutated_decision(mutate)

        self.assertFalse(result["passed"])
        self.assertIn("authority artifact inventory changed", result["issues"])

    def test_candidate_commit_is_rejected(self):
        result = self._mutated_decision(
            lambda payload: payload["repository_audit"].update(
                {"candidate_commit": "candidate"}
            )
        )

        self.assertFalse(result["passed"])
        self.assertIn(
            "repository audit records a PUYO-212 candidate commit", result["issues"]
        )


if __name__ == "__main__":
    unittest.main()
