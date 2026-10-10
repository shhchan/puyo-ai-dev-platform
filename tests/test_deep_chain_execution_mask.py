"""Public execution-mask regression for PUYO-266's reference selector."""

import json
import unittest
from pathlib import Path

from agents.decision_flow import DecisionContext
from agents.deep_chain_builder import (
    AGGREGATED_ROOT_SCORES_ARTIFACT,
    RUNTIME_INPUT_ARTIFACT,
    SELECTED_ACTION_ARTIFACT,
    SELECTION_EVIDENCE_ARTIFACT,
    DeepChainBuilderProfile,
    SelectPlacementStep,
    VisibleRuntimeInput,
)


class ExecutionMaskSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((Path(__file__).parent / "fixtures" /
                                  "nextgen_single_reference_2259_public.json").read_text())

    def context(self, mask):
        # Reproduces the native ranking at pattern2259, tick1789. Only 7/9
        # have a public real-time route; the higher ranked root19 does not.
        actions = tuple(self.fixture["ranked_roots"])
        values = tuple({
            "root_action": action, "ranking_key": (len(actions) - index,),
            "representative": {"actions": [action], "steps": [{"action": action}]},
        } for index, action in enumerate(actions))
        return DecisionContext(decision_id="mask-regression", profile=DeepChainBuilderProfile(
            name="reference", version="1.0", purpose="unit", depth=16, width=250,
            scenarios=6, max_expanded_nodes=600000), artifacts={
                RUNTIME_INPUT_ARTIFACT: VisibleRuntimeInput(board=None, next_pairs=None, action_mask=mask),
                AGGREGATED_ROOT_SCORES_ARTIFACT: values,
            })

    def test_uses_best_allowed_root_without_mutating_search_ranking(self):
        context = self.context(tuple(self.fixture["reachable_mask"]))
        step = SelectPlacementStep()
        before = context.require(AGGREGATED_ROOT_SCORES_ARTIFACT)
        result = step.run(context)
        self.assertEqual(result.outputs[SELECTED_ACTION_ARTIFACT], 9)
        self.assertEqual(result.candidate_count, 2)
        evidence = result.outputs[SELECTION_EVIDENCE_ARTIFACT]
        self.assertEqual(evidence["candidate_count"], 2)
        self.assertEqual([v["root_action"] for v in evidence["scenario_aggregation"]], [9, 7])
        self.assertEqual(context.require(AGGREGATED_ROOT_SCORES_ARTIFACT), before)
        self.assertEqual(step.summarize_inputs(context)["root_actions"], [19, 21, 16, 20, 9, 15, 7])
        self.assertIn(RUNTIME_INPUT_ARTIFACT, step.contract.requires)

    def test_missing_or_all_allowed_mask_keeps_original_rank(self):
        for mask in ((), (True,) * 22):
            with self.subTest(mask=mask):
                result = SelectPlacementStep().run(self.context(mask))
                self.assertEqual(result.outputs[SELECTED_ACTION_ARTIFACT], 19)
                self.assertEqual(result.candidate_count, 7)

    def test_all_disallowed_or_out_of_mask_roots_fail_closed(self):
        for mask in ((False,) * 22, (True,)):
            with self.subTest(mask=mask), self.assertRaisesRegex(ValueError, "public action mask"):
                SelectPlacementStep().run(self.context(mask))


if __name__ == "__main__":
    unittest.main()
