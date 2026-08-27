import importlib
import unittest
from dataclasses import replace

from agents.chain_structure import (
    ChainStructureAction,
    ChainStructureBudget,
    ChainStructureEvaluator,
    load_chain_structure_config,
)
from agents.compact_search import CompactSearchState, transition
from agents.deep_chain_native import InvalidNativeInputError
from agents.deep_chain_native_evaluator import (
    NATIVE_CHAIN_STRUCTURE_ABI_VERSION,
    NATIVE_CHAIN_STRUCTURE_BATCH_SCHEMA_VERSION,
    NATIVE_CHAIN_STRUCTURE_HOT_SCHEMA_VERSION,
    NATIVE_CHAIN_STRUCTURE_PROFILE_SCHEMA_VERSION,
    NativeChainStructureBatchClient,
    NativeChainStructureInput,
    encode_native_chain_structure_batch,
    materialize_native_chain_structure_result,
)
from src.core.constants import PuyoColor
from tests.test_chain_structure import _fixture_states


class TestNativeChainStructurePythonContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_chain_structure_config()

    def test_encoder_is_deterministic_and_uses_fixed_record_width(self):
        record = NativeChainStructureInput(CompactSearchState.empty())

        first = encode_native_chain_structure_batch([record], self.config)
        second = encode_native_chain_structure_batch([record], self.config)

        self.assertEqual(first, second)
        self.assertEqual(first[:4], b"NCSB")
        self.assertEqual(len(first), 240 + 184)

    def test_input_rejects_partial_action_context_and_invalid_profile_values(self):
        state = CompactSearchState.empty()

        with self.assertRaises(InvalidNativeInputError):
            NativeChainStructureInput(
                state,
                parent_state=state,
            )
        with self.assertRaises(InvalidNativeInputError):
            NativeChainStructureInput(state, target_chain_count=0)
        with self.assertRaises(InvalidNativeInputError):
            NativeChainStructureInput(
                state,
                pair=(PuyoColor.RED, PuyoColor.OJAMA),
            )

    def test_encoder_rejects_evidence_budget_outside_fixed_native_bound(self):
        config = replace(
            self.config,
            budget=replace(self.config.budget, max_resolution_nodes=97),
        )

        with self.assertRaises(InvalidNativeInputError):
            encode_native_chain_structure_batch(
                [NativeChainStructureInput(CompactSearchState.empty())],
                config,
            )


try:
    NATIVE_MODULE = importlib.import_module("_puyo_deep_chain_native")
except (ImportError, OSError):
    NATIVE_MODULE = None


@unittest.skipIf(NATIVE_MODULE is None, "release native extension is not installed")
class TestNativeChainStructureExtension(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_chain_structure_config()
        cls.python_evaluator = ChainStructureEvaluator(cls.config)
        cls.native_client = NativeChainStructureBatchClient(NATIVE_MODULE)

    def assert_result_parity(self, state, record, *, parent=None, action=None):
        expected = self.python_evaluator.evaluate(
            state,
            parent=parent,
            action=action,
        )
        actual = materialize_native_chain_structure_result(
            record,
            state=state,
            config=self.config,
        )
        self.assertEqual(actual.to_dict(), expected.to_dict())

    def test_capability_and_transition_abi_are_fixed(self):
        self.assertEqual(NATIVE_CHAIN_STRUCTURE_ABI_VERSION, 1)
        self.assertEqual(
            NATIVE_CHAIN_STRUCTURE_HOT_SCHEMA_VERSION,
            "puyo.native_chain_structure_hot.v1",
        )
        self.assertEqual(
            NATIVE_CHAIN_STRUCTURE_BATCH_SCHEMA_VERSION,
            "puyo.native_chain_structure_batch.v1",
        )
        self.assertEqual(
            NATIVE_CHAIN_STRUCTURE_PROFILE_SCHEMA_VERSION,
            "puyo.native_chain_structure_combined_profile.v1",
        )
        self.assertEqual(NATIVE_MODULE.COMPACT_HOT_CHILD_STATE_BYTES, 80)
        self.assertEqual(NATIVE_MODULE.COMPACT_HOT_RESULT_BYTES, 24)

    def test_all_chain_structure_fixtures_match_python_exactly(self):
        states = _fixture_states()
        inputs = [NativeChainStructureInput(state) for state in states.values()]

        first = self.native_client.evaluate_batch(inputs, self.config)
        second = self.native_client.evaluate_batch(inputs, self.config)

        self.assertEqual(first.response_bytes, second.response_bytes)
        for (case_id, state), record in zip(
            states.items(),
            first.records,
            strict=True,
        ):
            with self.subTest(case=case_id):
                self.assert_result_parity(state, record)

    def test_action_features_match_python_without_changing_state_features(self):
        parent_state = _fixture_states()["fixed-trigger-root"]
        pair = (PuyoColor.RED, PuyoColor.BLUE)
        transition_result = transition(parent_state, pair, 0)
        self.assertTrue(transition_result.valid)
        action = ChainStructureAction.from_result(transition_result)
        parent = self.python_evaluator.evaluate(parent_state)
        record = NativeChainStructureInput(
            transition_result.state,
            parent_state=parent_state,
            action=action,
            pair=pair,
            action_id=0,
        )

        native = self.native_client.evaluate_batch([record], self.config).records[0]
        state_only = self.native_client.evaluate_batch(
            [NativeChainStructureInput(transition_result.state)],
            self.config,
        ).records[0]

        self.assert_result_parity(
            transition_result.state,
            native,
            parent=parent,
            action=action,
        )
        self.assertEqual(native.features, state_only.features)
        self.assertTrue(native.action_features.evaluated)
        self.assertFalse(state_only.action_features.evaluated)

    def test_budget_exhaustion_matches_python_exactly(self):
        config = replace(
            self.config,
            budget=ChainStructureBudget(
                max_added_puyos=3,
                max_pattern_nodes=1,
                max_resolution_nodes=96,
                max_candidates=12,
            ),
        )
        state = _fixture_states()["tuning-connected-platform"]
        expected = ChainStructureEvaluator(config).evaluate(state)
        record = self.native_client.evaluate_batch(
            [NativeChainStructureInput(state)],
            config,
        ).records[0]
        actual = materialize_native_chain_structure_result(
            record,
            state=state,
            config=config,
        )

        self.assertEqual(actual.evaluation_status, "budget_exhausted")
        self.assertEqual(actual.to_dict(), expected.to_dict())

    def test_combined_profile_preserves_exact_operation_count(self):
        record = NativeChainStructureInput(
            CompactSearchState.empty(),
            pair=(PuyoColor.RED, PuyoColor.BLUE),
            action_id=0,
        )

        result = self.native_client.combined_profile(
            [record],
            self.config,
            operations=17,
        )

        self.assertEqual(result.operations, 17)
        self.assertEqual(result.record_count, 1)
        self.assertGreater(result.elapsed_ns, 0)
        self.assertEqual(result.evaluator_abi_version, 1)


if __name__ == "__main__":
    unittest.main()
