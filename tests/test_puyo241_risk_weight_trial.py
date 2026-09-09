"""Trial-only checks: injected weights preserve the evaluator's strict parity."""

import unittest

from agents.chain_structure import ChainStructureEvaluator, load_chain_structure_config
from agents.deep_chain_native_evaluator import (
    NativeChainStructureBatchClient,
    NativeChainStructureInput,
    materialize_native_chain_structure_result,
)
from eval import puyo241_risk_weight_trial as trial
from tests.test_chain_structure import _fixture_states
from agents.deep_chain_search_backend import semantic_sha256

try:
    import _puyo_deep_chain_native as NATIVE_MODULE
except (ImportError, OSError):
    NATIVE_MODULE = None


class TestRiskWeightTrial(unittest.TestCase):
    def test_only_prefixed_weight_and_version_change(self):
        original = load_chain_structure_config().to_dict()
        candidate = trial.evaluator_config("danger2").to_dict()
        changed = {k for k, value in original["weights"].items()
                   if candidate["weights"][k] != value}
        self.assertEqual(changed, {"danger_ratio"})
        self.assertEqual(candidate["weights"]["danger_ratio"], -40000.0)
        candidate["weights"]["danger_ratio"] = original["weights"]["danger_ratio"]
        candidate["weight_version"] = original["weight_version"]
        self.assertEqual(candidate, original)
        self.assertEqual(load_chain_structure_config().to_dict(), original)
        with self.assertRaises(ValueError):
            trial.evaluator_config("unprefixed")

    @unittest.skipIf(NATIVE_MODULE is None, "release native extension is not installed")
    def test_adjusted_weight_full_evaluator_parity_and_fatal_floor(self):
        config = trial.evaluator_config("danger2")
        evaluator = ChainStructureEvaluator(config)
        states = _fixture_states()
        batch = NativeChainStructureBatchClient(NATIVE_MODULE).evaluate_batch(
            [NativeChainStructureInput(state, target_chain_count=10) for state in states.values()], config,
        )
        saw_fatal = False
        for (name, state), record in zip(states.items(), batch.records, strict=True):
            with self.subTest(name=name):
                expected = evaluator.evaluate(state, target_chain_count=10)
                actual = materialize_native_chain_structure_result(record, state=state, config=config)
                self.assertEqual(actual.to_dict(), expected.to_dict())
                if expected.score_breakdown.fatal:
                    saw_fatal = True
                    self.assertEqual(expected.score, config.fatal_score)
                    self.assertEqual(expected.score, ChainStructureEvaluator().evaluate(state).score)
        self.assertTrue(saw_fatal)

    @unittest.skipIf(NATIVE_MODULE is None, "release native extension is not installed")
    def test_real_native_request_receipt_and_diagnostics_use_injected_checksum(self):
        for condition in trial.CONDITIONS:
            with self.subTest(condition=condition):
                receipts = []
                policy = trial.policy_factory(condition, receipts)(123, "reference")
                obs, info = trial.baseline._initial_observation_and_info(123, max_steps=40)
                action = policy.select_action(obs, info)
                diagnostics = policy.tactical_diagnostics
                checksum = semantic_sha256(trial.evaluator_config(condition).to_dict())
                self.assertEqual(len(receipts), 1)
                self.assertEqual(receipts[0]["evaluator_config_sha256"], checksum)
                self.assertEqual(diagnostics["backend"]["configuration"]["evaluator_config_sha256"], checksum)
                self.assertEqual(receipts[0]["ranked_root_actions"][0], action)
                self.assertFalse(diagnostics["fallback"]["used"])


if __name__ == "__main__":
    unittest.main()
