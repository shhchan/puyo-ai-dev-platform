import importlib
import json
import unittest
from pathlib import Path

from agents.compact_search import CompactSearchState, transition
from agents.deep_chain_native import InvalidNativeInputError
from agents.deep_chain_native_transition import (
    NATIVE_COMPACT_HOT_CHILD_STATE_BYTES,
    NATIVE_COMPACT_HOT_RESULT_ABI_VERSION,
    NATIVE_COMPACT_HOT_RESULT_BYTES,
    NATIVE_COMPACT_HOT_RESULT_SCHEMA_VERSION,
    NativeCompactArithmeticOverflowError,
    NativeCompactBatchClient,
    NativeCompactTransitionInput,
    decode_native_compact_batch_response,
    encode_native_compact_batch,
)
from eval.deep_chain_native_transition_benchmark import evaluate_native_parity
from src.core.constants import GRID_WIDTH, PuyoColor

ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "eval" / "deep_chain_native_transition_corpus.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "compact_search_kernel_cases.json"

PLANE_COLORS = (
    PuyoColor.RED,
    PuyoColor.BLUE,
    PuyoColor.GREEN,
    PuyoColor.YELLOW,
    PuyoColor.PURPLE,
    PuyoColor.OJAMA,
)
PLANE_INDEX = {color: index for index, color in enumerate(PLANE_COLORS)}


def _bit(x: int, y: int) -> int:
    return 1 << (y * GRID_WIDTH + x)


def _planes_from_grid(grid) -> tuple[int, ...]:
    planes = [0] * len(PLANE_COLORS)
    for y, row in enumerate(grid):
        for x, color in enumerate(row):
            if color in PLANE_INDEX:
                planes[PLANE_INDEX[color]] |= _bit(x, y)
    return tuple(planes)


def _mask_from_cells(cells) -> int:
    return sum(_bit(x, y) for x, y in cells)


def _state_from_fixture(case) -> CompactSearchState:
    char_to_color = {
        "R": PuyoColor.RED,
        "B": PuyoColor.BLUE,
        "G": PuyoColor.GREEN,
        "Y": PuyoColor.YELLOW,
        "P": PuyoColor.PURPLE,
        "O": PuyoColor.OJAMA,
    }
    planes = [0] * len(PLANE_COLORS)
    for y, row in enumerate(case["board"]):
        for x, char in enumerate(row):
            color = char_to_color.get(char)
            if color is not None:
                planes[PLANE_INDEX[color]] |= _bit(x, y)
    return CompactSearchState(
        planes=tuple(planes),
        all_clear_bonus_pending=bool(case["all_clear_bonus_pending"]),
    )


class TestNativeCompactPythonContract(unittest.TestCase):
    def test_input_rejects_invalid_action_and_non_normal_pair(self):
        state = CompactSearchState.empty()

        with self.assertRaises(InvalidNativeInputError):
            NativeCompactTransitionInput(
                state,
                (PuyoColor.RED, PuyoColor.OJAMA),
                0,
            )
        with self.assertRaises(InvalidNativeInputError):
            NativeCompactTransitionInput(
                state,
                (PuyoColor.RED, PuyoColor.BLUE),
                22,
            )

    def test_encoder_is_byte_deterministic_and_bounded(self):
        record = NativeCompactTransitionInput(
            CompactSearchState.empty(),
            (PuyoColor.RED, PuyoColor.BLUE),
            0,
        )

        first = encode_native_compact_batch([record], include_actions=True)
        second = encode_native_compact_batch([record], include_actions=True)

        self.assertEqual(first, second)
        self.assertEqual(first[:4], b"PCTB")


try:
    NATIVE_MODULE = importlib.import_module("_puyo_deep_chain_native")
except (ImportError, OSError):
    NATIVE_MODULE = None


@unittest.skipIf(NATIVE_MODULE is None, "release native extension is not installed")
class TestNativeCompactExtension(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = NativeCompactBatchClient(NATIVE_MODULE)

    def test_hot_result_contract_is_fixed_and_versioned(self):
        self.assertEqual(NATIVE_COMPACT_HOT_RESULT_ABI_VERSION, 1)
        self.assertEqual(
            NATIVE_COMPACT_HOT_RESULT_SCHEMA_VERSION,
            "puyo.native_compact_hot_result.v1",
        )
        self.assertEqual(NATIVE_COMPACT_HOT_CHILD_STATE_BYTES, 80)
        self.assertEqual(NATIVE_COMPACT_HOT_RESULT_BYTES, 24)
        self.assertEqual(NATIVE_MODULE.COMPACT_HOT_RESULT_ABI_VERSION, 1)
        self.assertEqual(NATIVE_MODULE.COMPACT_HOT_CHILD_STATE_BYTES, 80)
        self.assertEqual(NATIVE_MODULE.COMPACT_HOT_RESULT_BYTES, 24)

    def test_fixed_fixtures_match_summary_and_diagnostic_trace(self):
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        inputs = []
        python_results = []
        for case in fixture["cases"]:
            state = _state_from_fixture(case)
            pair = tuple(PuyoColor[name] for name in case["pair"])
            action = int(case["action"])
            inputs.append(NativeCompactTransitionInput(state, pair, action))
            python_results.append(
                transition(state, pair, action, capture_visuals=True)
            )

        native = self.client.transition_batch(
            inputs,
            capture_trace=True,
            include_actions=True,
        )

        for case, expected, actual in zip(
            fixture["cases"],
            python_results,
            native.records,
            strict=True,
        ):
            with self.subTest(case=case["id"]):
                self.assertEqual(actual.state, expected.state)
                self.assertEqual(actual.valid, expected.valid)
                self.assertEqual(actual.axis_y, expected.axis_y)
                self.assertEqual(actual.score_delta, expected.score_delta)
                self.assertEqual(actual.attack_score_delta, expected.attack_score_delta)
                self.assertEqual(actual.chain_count, expected.chain_count)
                self.assertEqual(actual.vanished_count, expected.vanished_count)
                self.assertEqual(
                    actual.garbage_cleared_count,
                    expected.garbage_cleared_count,
                )
                expected_placement = (
                    _planes_from_grid(expected.placement_board)
                    if expected.valid
                    else None
                )
                self.assertEqual(actual.placement_planes, expected_placement)
                self.assertEqual(len(actual.chains), len(expected.chains))
                for actual_step, expected_step in zip(
                    actual.chains,
                    expected.chains,
                    strict=True,
                ):
                    self.assertEqual(
                        actual_step.board_planes,
                        _planes_from_grid(expected_step.board),
                    )
                    self.assertEqual(
                        actual_step.vanished_mask,
                        _mask_from_cells(expected_step.vanished),
                    )
                    self.assertEqual(
                        actual_step.garbage_mask,
                        _mask_from_cells(expected_step.garbage_cleared),
                    )
                    self.assertEqual(actual_step.score, expected_step.score)
                    self.assertEqual(actual_step.base, expected_step.base)
                    self.assertEqual(actual_step.bonus, expected_step.bonus)

    def test_frozen_11264_transition_corpus_is_exact_and_deterministic(self):
        corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))

        result = evaluate_native_parity(self.client, corpus)

        self.assertEqual(result["transition_count"], 11_264)
        self.assertEqual(result["mismatch_count"], 0)
        self.assertEqual(result["action_mismatch_count"], 0)
        self.assertTrue(result["deterministic_response"])
        self.assertTrue(result["passed"])

    def test_hidden_row_and_ojama_change_exact_fingerprint(self):
        red_hidden = CompactSearchState(planes=(_bit(0, 13), 0, 0, 0, 0, 0))
        ojama_hidden = CompactSearchState(planes=(0, 0, 0, 0, 0, _bit(0, 13)))
        pair = (PuyoColor.BLUE, PuyoColor.GREEN)
        outputs = self.client.transition_batch(
            [
                NativeCompactTransitionInput(red_hidden, pair, 19),
                NativeCompactTransitionInput(ojama_hidden, pair, 19),
            ]
        ).records

        self.assertNotEqual(outputs[0].state, outputs[1].state)
        self.assertNotEqual(outputs[0].board_fingerprint, outputs[1].board_fingerprint)
        self.assertEqual(len(outputs[0].board_fingerprint), 16)

    def test_invalid_game_over_transition_preserves_state(self):
        state = CompactSearchState(
            planes=(0, 0, 0, 0, 0, 0),
            game_over=True,
            score=123,
            last_chain_end_score=100,
        )
        (result,) = self.client.transition_batch(
            [
                NativeCompactTransitionInput(
                    state,
                    (PuyoColor.RED, PuyoColor.BLUE),
                    0,
                )
            ]
        ).records

        self.assertFalse(result.valid)
        self.assertEqual(result.state, state)
        self.assertEqual(result.score_delta, 0)

    def test_native_errors_are_typed_indexed_and_do_not_poison_process(self):
        valid = NativeCompactTransitionInput(
            CompactSearchState.empty(),
            (PuyoColor.RED, PuyoColor.BLUE),
            0,
        )
        malformed = bytearray(encode_native_compact_batch([valid]))
        malformed[-1] = 0xFF

        with self.assertRaises(InvalidNativeInputError) as invalid_error:
            decode_native_compact_batch_response(
                NATIVE_MODULE._compact_transition_batch(bytes(malformed))
            )
        self.assertEqual(invalid_error.exception.provenance["record_index"], 0)

        planes = [0] * len(PLANE_COLORS)
        planes[0] = _bit(1, 0) | _bit(1, 1)
        overflow_state = CompactSearchState(
            planes=tuple(planes),
            score=(1 << 64) - 40,
        )
        overflow = NativeCompactTransitionInput(
            overflow_state,
            (PuyoColor.RED, PuyoColor.RED),
            7,
        )
        with self.assertRaises(NativeCompactArithmeticOverflowError) as captured:
            self.client.transition_batch([overflow])
        self.assertEqual(captured.exception.record_index, 0)

        recovery = self.client.transition_batch([valid])
        self.assertEqual(len(recovery.records), 1)
        self.assertTrue(recovery.records[0].valid)


if __name__ == "__main__":
    unittest.main()
