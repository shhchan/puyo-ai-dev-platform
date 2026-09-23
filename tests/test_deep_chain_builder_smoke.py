import json
import unittest
from pathlib import Path

from eval.deep_chain_builder_smoke import (
    SMOKE_SCHEMA_VERSION,
    run_headless_smoke,
    verify_smoke_artifact,
)

ARTIFACT_PATH = (
    Path(__file__).parents[1]
    / "docs"
    / "benchmarks"
    / "puyo-187-deep-chain-builder-smoke.json"
)


class TestDeepChainBuilderSmoke(unittest.TestCase):
    def test_experimental_target_is_recorded_separately(self):
        payload = run_headless_smoke(
            seed=187,
            turns=1,
            target_chain_count=10,
        )

        self.assertEqual(payload["target_chain_count"], 10)
        record = payload["records"][0]
        self.assertEqual(
            record["plan"]["objective"]["minimum_chain_count"], 10
        )
        self.assertEqual(record["search"]["target_chain_count"], 10)

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
