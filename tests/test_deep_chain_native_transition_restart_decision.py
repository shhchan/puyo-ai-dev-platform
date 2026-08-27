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

    def test_risk_acceptance_cannot_be_recorded_without_a_new_decision(self):
        result = self._mutated_decision(
            lambda payload: payload["risk_acceptance"].update({"granted": True})
        )

        self.assertFalse(result["passed"])
        self.assertIn(
            "quiet component risk acceptance must not be granted", result["issues"]
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
