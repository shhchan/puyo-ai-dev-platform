import json
import unittest
from pathlib import Path

from eval.deep_chain_builder_smoke import (
    SMOKE_SCHEMA_VERSION,
    verify_smoke_artifact,
)

ARTIFACT_PATH = (
    Path(__file__).parents[1]
    / "docs"
    / "benchmarks"
    / "puyo-187-deep-chain-builder-smoke.json"
)


class TestDeepChainBuilderSmoke(unittest.TestCase):
    def test_committed_smoke_is_replayable_and_deterministic(self):
        payload = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))

        verify_smoke_artifact(payload)

        self.assertEqual(payload["schema_version"], SMOKE_SCHEMA_VERSION)
        self.assertEqual(len(payload["runs"]), 2)
        first, second = payload["runs"]
        self.assertEqual(first["actions"], second["actions"])
        self.assertEqual(first["plan_ids"], second["plan_ids"])
        self.assertEqual(first["final_board_digest"], second["final_board_digest"])
        for run in payload["runs"]:
            self.assertGreaterEqual(run["completed_turns"], 2)
            for record in run["records"]:
                plan = record["plan"]
                self.assertEqual(plan["schema_version"], "n-turn-plan-v1")
                self.assertEqual(plan["steps"][0]["action"], record["action"])
                self.assertTrue(
                    all("scenario_id" in step for step in plan["steps"])
                )
                self.assertTrue(
                    all(step["predicted_board"] for step in plan["steps"])
                )


if __name__ == "__main__":
    unittest.main()
