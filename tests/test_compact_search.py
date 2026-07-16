import copy
import json
import unittest
from pathlib import Path

from agents.compact_search import (
    COMPACT_SEARCH_SCHEMA_VERSION,
    CompactSearchSnapshot,
    CompactSearchState,
    CompactTranspositionKey,
    legal_action_indices,
    symmetry_reduced_action_indices,
    transition,
)
from puyo_env.actions import action_to_placement
from puyo_env.actions import legal_action_indices as authoritative_legal_actions
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, Direction, PuyoColor
from src.core.game import GameState
from src.core.headless import HeadlessPuyoSimulator
from src.core.puyo import Puyo


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "compact_search_kernel_cases.json"
CHAR_TO_COLOR = {
    ".": PuyoColor.EMPTY,
    "R": PuyoColor.RED,
    "B": PuyoColor.BLUE,
    "G": PuyoColor.GREEN,
    "Y": PuyoColor.YELLOW,
    "P": PuyoColor.PURPLE,
    "O": PuyoColor.OJAMA,
}
COLOR_TO_CHAR = {color: char for char, color in CHAR_TO_COLOR.items()}


def load_cases():
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if payload["schema_version"] != "puyo.compact_search_fixtures.v1":
        raise AssertionError("unexpected compact-search fixture schema")
    return payload["cases"]


def simulator_from_case(case):
    game = GameState(seed=0)
    game.spawn_puyo()
    if len(case["board"]) != GRID_HEIGHT:
        raise AssertionError("fixture board must contain all 14 rows")
    for y, row in enumerate(case["board"]):
        if len(row) != GRID_WIDTH:
            raise AssertionError("fixture row must contain six columns")
        for x, char in enumerate(row):
            color = CHAR_TO_COLOR[char]
            if color != PuyoColor.EMPTY:
                game.field.place_puyo(x, y, Puyo(color))
    pair = tuple(PuyoColor[name] for name in case["pair"])
    game.current_puyo_1 = Puyo(pair[0])
    game.current_puyo_2 = Puyo(pair[1])
    game.all_clear_bonus_pending = bool(case["all_clear_bonus_pending"])
    return HeadlessPuyoSimulator(game_state=game), pair


def board_strings(grid):
    return ["".join(COLOR_TO_CHAR[color] for color in row) for row in grid]


def compact_chain_payload(result):
    return [
        {
            "chain_index": step.chain_index,
            "vanished_count": step.vanished_count,
            "garbage_cleared_count": step.garbage_cleared_count,
            "score": step.score,
            "base": step.base,
            "bonus": step.bonus,
            "all_clear_bonus_score": step.all_clear_bonus_score,
        }
        for step in result.chains
    ]


def semantic_result_signature(result):
    return (
        result.state.to_bytes(),
        result.chain_count,
        result.score_delta,
        result.garbage_cleared_count,
        result.game_over,
    )


class TestCompactSearchKernel(unittest.TestCase):
    def test_schema_is_explicit_and_production_path_is_pure_python(self):
        self.assertEqual(COMPACT_SEARCH_SCHEMA_VERSION, "puyo.compact_search_state.v1")
        state = CompactSearchState.empty()

        self.assertIsInstance(state.planes, tuple)
        self.assertTrue(all(isinstance(plane, int) for plane in state.planes))

    def test_golden_fixtures_match_authoritative_simulator(self):
        for case in load_cases():
            with self.subTest(case=case["id"]):
                simulator, pair = simulator_from_case(case)
                snapshot = CompactSearchSnapshot.from_simulator(simulator)
                expected = case["expected"]

                authoritative_legal = authoritative_legal_actions(simulator)
                compact_legal = list(legal_action_indices(snapshot.state))
                reduced = list(symmetry_reduced_action_indices(snapshot.state, pair))
                self.assertEqual(authoritative_legal, expected["legal_actions"])
                self.assertEqual(compact_legal, expected["legal_actions"])
                self.assertEqual(reduced, expected["symmetry_reduced_actions"])

                authoritative = copy.deepcopy(simulator)
                authoritative_result = authoritative.step(
                    action_to_placement(case["action"]),
                    capture_visuals=True,
                )
                compact_result = transition(
                    snapshot.state,
                    pair,
                    case["action"],
                    capture_visuals=True,
                )

                self.assertEqual(compact_result.axis_y, expected["axis_y"])
                self.assertEqual(compact_result.score_delta, expected["score_delta"])
                self.assertEqual(
                    compact_result.attack_score_delta,
                    expected["attack_score_delta"],
                )
                self.assertEqual(compact_result.chain_count, expected["chain_count"])
                self.assertEqual(
                    compact_result.vanished_count,
                    expected["vanished_count"],
                )
                self.assertEqual(
                    compact_result.garbage_cleared_count,
                    expected["garbage_cleared_count"],
                )
                self.assertEqual(compact_result.game_over, expected["game_over"])
                self.assertEqual(
                    compact_result.all_clear_achieved,
                    expected["all_clear_achieved"],
                )
                self.assertEqual(
                    compact_result.all_clear_bonus_pending,
                    expected["all_clear_bonus_pending"],
                )
                self.assertEqual(
                    compact_result.all_clear_bonus_consumed,
                    expected["all_clear_bonus_consumed"],
                )
                self.assertEqual(
                    compact_result.all_clear_bonus_score,
                    expected["all_clear_bonus_score"],
                )
                self.assertEqual(
                    board_strings(compact_result.state.to_color_grid()),
                    expected["final_board"],
                )
                self.assertEqual(
                    compact_chain_payload(compact_result), expected["chains"]
                )

                self.assertEqual(
                    compact_result.state,
                    CompactSearchState.from_simulator(authoritative),
                )
                self.assertEqual(compact_result.valid, authoritative_result.valid)
                self.assertEqual(compact_result.axis_y, authoritative_result.axis_y)
                self.assertEqual(
                    compact_result.score_delta,
                    authoritative_result.score_delta,
                )
                self.assertEqual(
                    compact_result.attack_score_delta,
                    authoritative_result.attack_score_delta,
                )
                self.assertEqual(
                    compact_result.chain_count,
                    authoritative_result.chain_count,
                )
                self.assertEqual(
                    compact_result.game_over, authoritative_result.game_over
                )
                self.assertEqual(
                    compact_result.placement_board,
                    authoritative_result.placement_board,
                )
                self.assertEqual(
                    tuple(
                        (
                            step.chain_index,
                            step.vanished_count,
                            step.score,
                            step.base,
                            step.bonus,
                            step.groups,
                            step.vanished,
                            step.board,
                            step.all_clear_bonus_score,
                        )
                        for step in compact_result.chains
                    ),
                    tuple(
                        (
                            step.chain_index,
                            step.vanished_count,
                            step.score,
                            step.base,
                            step.bonus,
                            step.groups,
                            step.vanished,
                            step.board,
                            step.all_clear_bonus_score,
                        )
                        for step in authoritative_result.chains
                    ),
                )

    def test_transition_is_immutable_and_byte_deterministic(self):
        case = next(item for item in load_cases() if item["id"] == "two_chain")
        simulator, pair = simulator_from_case(case)
        state = CompactSearchState.from_simulator(simulator)
        before = state.to_bytes()

        first = transition(state, pair, case["action"], capture_visuals=True)
        second = transition(state, pair, case["action"], capture_visuals=True)

        self.assertEqual(state.to_bytes(), before)
        self.assertEqual(first, second)
        self.assertEqual(first.state.to_bytes(), second.state.to_bytes())

    def test_equal_pair_reduction_preserves_exact_outcome_set_and_action_ids(self):
        case = next(
            item for item in load_cases() if item["id"] == "equal_pair_symmetry"
        )
        simulator, pair = simulator_from_case(case)
        state = CompactSearchState.from_simulator(simulator)
        full = legal_action_indices(state)
        reduced = symmetry_reduced_action_indices(state, pair)

        self.assertEqual(len(full), 22)
        self.assertEqual(len(reduced), 11)
        self.assertTrue(set(reduced).issubset(full))
        self.assertEqual(
            {
                semantic_result_signature(transition(state, pair, action))
                for action in full
            },
            {
                semantic_result_signature(transition(state, pair, action))
                for action in reduced
            },
        )

    def test_hash_and_equality_include_hidden_rows_ojama_and_lifecycle(self):
        base = HeadlessPuyoSimulator(seed=1)
        hidden = copy.deepcopy(base)
        ojama = copy.deepcopy(base)
        pending = copy.deepcopy(base)
        hidden.game.field.place_puyo(0, 13, Puyo(PuyoColor.RED))
        ojama.game.field.place_puyo(0, 13, Puyo(PuyoColor.OJAMA))
        pending.game.all_clear_bonus_pending = True

        states = [
            CompactSearchState.from_simulator(item)
            for item in (base, hidden, ojama, pending)
        ]

        self.assertEqual(len(set(states)), 4)
        self.assertEqual(len({hash(state) for state in states}), 4)
        self.assertEqual(len({state.to_bytes() for state in states}), 4)
        self.assertEqual(states[1].column_heights[0], 14)

    def test_transposition_key_requires_external_scenario_cursor_and_depth(self):
        state = CompactSearchState.empty()
        keys = {
            CompactTranspositionKey(state, scenario_id=0, pair_cursor=0, depth=0),
            CompactTranspositionKey(state, scenario_id=1, pair_cursor=0, depth=0),
            CompactTranspositionKey(state, scenario_id=0, pair_cursor=1, depth=0),
            CompactTranspositionKey(state, scenario_id=0, pair_cursor=0, depth=1),
        }

        self.assertEqual(len(keys), 4)
        with self.assertRaises(TypeError):
            CompactTranspositionKey(state)  # type: ignore[call-arg]

    def test_snapshot_excludes_current_pair_from_state_identity(self):
        first = HeadlessPuyoSimulator(seed=1)
        second = copy.deepcopy(first)
        second.game.current_puyo_1 = Puyo(PuyoColor.PURPLE)
        second.game.current_puyo_2 = Puyo(PuyoColor.RED)

        first_snapshot = CompactSearchSnapshot.from_simulator(first)
        second_snapshot = CompactSearchSnapshot.from_simulator(second)

        self.assertEqual(first_snapshot.state, second_snapshot.state)
        self.assertEqual(
            first_snapshot.state,
            CompactSearchState.from_game(first.game),
        )
        self.assertNotEqual(first_snapshot.current_pair, second_snapshot.current_pair)

    def test_invalid_placement_returns_original_state(self):
        state = CompactSearchState.empty()

        result = transition(
            state,
            (PuyoColor.RED, PuyoColor.BLUE),
            (99, Direction.UP),
        )

        self.assertFalse(result.valid)
        self.assertIs(result.state, state)
        self.assertEqual(result.score_delta, 0)

    def test_attack_score_lifecycle_matches_authoritative_accumulation(self):
        case = next(
            item for item in load_cases() if item["id"] == "one_chain_all_clear"
        )
        simulator, pair = simulator_from_case(case)
        simulator.game.score = 29
        simulator.game.last_chain_end_score = 0
        state = CompactSearchState.from_simulator(simulator)

        authoritative = copy.deepcopy(simulator).step(
            action_to_placement(case["action"])
        )
        compact = transition(state, pair, case["action"])

        self.assertEqual(compact.score_delta, 40)
        self.assertEqual(compact.attack_score_delta, 69)
        self.assertEqual(compact.attack_score_delta, authoritative.attack_score_delta)


if __name__ == "__main__":
    unittest.main()
