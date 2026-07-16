import inspect
import json
import random
import unittest
from pathlib import Path

import agents.chain_structure as chain_structure
from agents.chain_structure import (
    CHAIN_STRUCTURE_FEATURE_VERSION,
    CHAIN_STRUCTURE_RESULT_SCHEMA_VERSION,
    ChainStructureAction,
    ChainStructureBudget,
    ChainStructureConfig,
    ChainStructureEvaluator,
    ChainStructureResult,
    bounded_quiescence,
    connection_candidates,
    extract_components,
    load_chain_structure_config,
    mirror_state,
)
from agents.compact_search import CompactSearchState, transition
from src.core.constants import PuyoColor


FIXTURE_PATH = Path("tests/fixtures/chain_structure_cases.json")
CHAR_TO_PLANE = {
    "R": 0,
    "B": 1,
    "G": 2,
    "Y": 3,
    "P": 4,
    "O": 5,
}


def _state(rows, *, game_over=False):
    if len(rows) != 14 or any(len(row) != 6 for row in rows):
        raise ValueError("fixture board must contain 14 bottom-up rows of width 6")
    planes = [0] * 6
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            if char == ".":
                continue
            planes[CHAR_TO_PLANE[char]] |= 1 << (y * 6 + x)
    return CompactSearchState(tuple(planes), game_over=game_over)


def _fixture_states():
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if payload["schema_version"] != "puyo.chain_structure_fixtures.v1":
        raise ValueError("unsupported chain-structure fixture schema")
    return {case["id"]: _state(case["rows_bottom_up"]) for case in payload["cases"]}


class TestChainStructureEvaluator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_chain_structure_config()
        cls.evaluator = ChainStructureEvaluator(cls.config)
        cls.states = _fixture_states()

    def test_config_and_result_contract_are_versioned_and_compact_only(self):
        source = inspect.getsource(chain_structure)
        result = self.evaluator.evaluate(self.states["fixed-empty"])

        self.assertNotIn("HeadlessPuyoSimulator", source)
        self.assertNotIn("clone_simulator", source)
        self.assertEqual(self.config.feature_version, CHAIN_STRUCTURE_FEATURE_VERSION)
        self.assertEqual(result.schema_version, CHAIN_STRUCTURE_RESULT_SCHEMA_VERSION)
        self.assertEqual(result.feature_version, CHAIN_STRUCTURE_FEATURE_VERSION)
        self.assertEqual(result.weight_version, self.config.weight_version)
        self.assertTrue(result.evaluated)
        self.assertEqual(result.evaluation_status, "not_found")
        self.assertEqual(result.score, 0.0)
        self.assertIsNone(result.truncation_reason)
        self.assertEqual(
            result.to_dict()["metric_namespace"], "generic_chain_structure"
        )
        self.assertNotIn("gtr", source.lower())

        invalid_config = self.config.to_dict()
        invalid_config["weights"]["tear"] = 1.0
        with self.assertRaisesRegex(ValueError, "reward/cost weight sign"):
            ChainStructureConfig.from_dict(invalid_config)

    def test_components_connections_and_ignition_relations_are_lossless(self):
        state = _state(
            [
                "RR.RR.",
                "......",
                "......",
                "......",
                "......",
                "......",
                "......",
                "......",
                "......",
                "......",
                "......",
                "......",
                "......",
                "......",
            ]
        )
        components = extract_components(state)
        connections = connection_candidates(state, components)
        result = self.evaluator.evaluate(state)

        self.assertEqual([component.size for component in components], [2, 2])
        self.assertEqual(len({component.component_id for component in components}), 2)
        self.assertEqual(len(connections), 1)
        self.assertEqual(connections[0].bridge_cell, (2, 0))
        self.assertEqual(len(connections[0].source_component_ids), 2)
        self.assertIsNotNone(result.quiescence.best)
        relation = result.quiescence.best.relations[0]
        self.assertEqual(relation.chain_index, 1)
        self.assertEqual(len(relation.source_component_ids), 2)
        self.assertGreaterEqual(result.features.connectivity_edges, 2)

    def test_simultaneous_color_groups_count_as_one_chain_step(self):
        planes = [0] * 6
        for x in range(4):
            planes[0] |= 1 << x
            planes[1] |= 1 << (6 + x)

        resolved = chain_structure._resolve_virtual(
            tuple(planes),
            component_by_cell={},
        )

        self.assertEqual(resolved.chain_count, 1)
        self.assertEqual(resolved.score, 240)
        self.assertEqual(len(resolved.relations), 2)
        self.assertEqual({relation.chain_index for relation in resolved.relations}, {1})

    def test_later_chain_relations_preserve_component_provenance_through_gravity(
        self,
    ):
        state = _state(
            [
                "BBBOOO",
                "..RRR.",
                "..B...",
                "......",
                "......",
                "......",
                "......",
                "......",
                "......",
                "......",
                "......",
                "......",
                "......",
                "......",
            ]
        )
        components = extract_components(state)
        component_by_cell = {
            cell: component for component in components for cell in component.cells
        }
        planes = list(state.planes)
        planes[0] |= 1 << (6 + 5)

        resolved = chain_structure._resolve_virtual(
            tuple(planes),
            component_by_cell=component_by_cell,
        )

        blue_ids = {
            component.component_id
            for component in components
            if component.color == PuyoColor.BLUE
        }
        second_relation = next(
            relation for relation in resolved.relations if relation.chain_index == 2
        )
        self.assertEqual(resolved.chain_count, 2)
        self.assertEqual(resolved.score, 360)
        self.assertEqual(set(second_relation.source_component_ids), blue_ids)

    def test_evaluation_and_tie_break_are_deterministic_and_mirror_symmetric(self):
        for case_id, state in self.states.items():
            with self.subTest(case_id=case_id):
                first = self.evaluator.evaluate(state)
                second = self.evaluator.evaluate(state)
                reflected = self.evaluator.evaluate(mirror_state(state))

                self.assertEqual(first.score, second.score)
                self.assertEqual(first.features, second.features)
                self.assertEqual(first.tie_break_digest, second.tie_break_digest)
                self.assertEqual(first.score, reflected.score)
                self.assertEqual(
                    first.features.to_dict(),
                    reflected.features.to_dict(),
                )
                self.assertEqual(
                    first.tie_break_digest,
                    reflected.tie_break_digest,
                )
                self.assertEqual(
                    sorted(component.component_id for component in first.components),
                    sorted(
                        component.component_id for component in reflected.components
                    ),
                )

    def test_random_gravity_valid_states_keep_mirror_normalized_features(self):
        randomizer = random.Random(173)
        for case_index in range(32):
            planes = [0] * 6
            for x in range(6):
                for y in range(randomizer.randrange(0, 11)):
                    plane = 5 if randomizer.random() < 0.12 else randomizer.randrange(5)
                    planes[plane] |= 1 << (y * 6 + x)
            state = CompactSearchState(tuple(planes))
            direct = self.evaluator.evaluate(state)
            reflected = self.evaluator.evaluate(mirror_state(state))
            with self.subTest(case_index=case_index):
                self.assertEqual(direct.score, reflected.score)
                self.assertEqual(
                    direct.features.to_dict(),
                    reflected.features.to_dict(),
                )
                self.assertEqual(
                    direct.tie_break_digest,
                    reflected.tie_break_digest,
                )

    def test_extendable_structure_outranks_same_height_unreachable_structure(self):
        extendable = self.evaluator.evaluate(self.states["fixed-extendable-high"])
        unreachable = self.evaluator.evaluate(self.states["fixed-unreachable-high"])

        self.assertEqual(
            extendable.features.canonical_column_heights,
            unreachable.features.canonical_column_heights,
        )
        self.assertTrue(extendable.features.trigger_reachable)
        self.assertFalse(unreachable.features.trigger_reachable)
        self.assertTrue(unreachable.features.unreachable_trigger)
        self.assertEqual(unreachable.score, self.config.fatal_score)
        self.assertGreater(extendable.score, unreachable.score)

    def test_quiet_placement_preserves_trigger_and_outranks_premature_fire(self):
        root = self.states["fixed-trigger-root"]
        parent = self.evaluator.evaluate(root)
        pair = (PuyoColor.RED, PuyoColor.BLUE)
        premature_transition = transition(root, pair, 0)
        quiet_transition = transition(root, pair, 16)
        premature = self.evaluator.evaluate(
            premature_transition.state,
            parent=parent,
            action=ChainStructureAction.from_result(premature_transition),
            target_chain_count=6,
        )
        quiet = self.evaluator.evaluate(
            quiet_transition.state,
            parent=parent,
            action=ChainStructureAction.from_result(quiet_transition),
            target_chain_count=6,
        )
        target_fire = self.evaluator.evaluate(
            premature_transition.state,
            parent=parent,
            action=ChainStructureAction.from_result(premature_transition),
            target_chain_count=1,
        )

        self.assertEqual(premature_transition.chain_count, 1)
        self.assertEqual(quiet_transition.chain_count, 0)
        self.assertTrue(premature.action_features.premature_fire)
        self.assertGreater(premature.action_features.trigger_damage, 0)
        self.assertGreater(premature.action_features.tear_count, 0)
        self.assertGreater(premature.action_features.waste_count, 0)
        self.assertFalse(quiet.action_features.premature_fire)
        self.assertEqual(quiet.action_features.trigger_damage, 0)
        self.assertEqual(
            quiet.features.required_key_count, parent.features.required_key_count
        )
        self.assertGreater(quiet.score, premature.score)
        self.assertFalse(target_fire.action_features.premature_fire)
        self.assertEqual(target_fire.action_features.trigger_damage, 0)
        self.assertEqual(target_fire.action_features.tear_count, 0)
        self.assertEqual(target_fire.action_features.waste_count, 0)

    def test_hidden_row_growth_is_recorded_as_waste(self):
        parent_state = _state(
            ["O....."] * 12 + ["......", "......"]
        )
        child_planes = list(parent_state.planes)
        child_planes[0] |= 1 << (12 * 6)
        parent = self.evaluator.evaluate(parent_state)
        child = self.evaluator.evaluate(
            CompactSearchState(tuple(child_planes)),
            parent=parent,
            action=ChainStructureAction(),
        )

        self.assertEqual(child.features.hidden_row_count, 1)
        self.assertEqual(child.action_features.waste_count, 1)

    def test_fatal_conditions_cannot_be_offset_by_positive_features(self):
        positive = self.states["fixed-trigger-root"]
        death = CompactSearchState(
            planes=positive.planes,
            game_over=True,
        )
        death_result = self.evaluator.evaluate(death)
        unreachable = self.evaluator.evaluate(self.states["fixed-unreachable-high"])
        blocked = _state(
            [
                "RBGYPR",
                "RBGYPR",
                "RBGYPR",
                "RBGYPR",
                "RBGYPR",
                "RBGYPR",
                "RBGYPR",
                "RBGYPR",
                "RBGYPR",
                "RBGYPR",
                "RBGYPR",
                "RBGYPR",
                "......",
                "......",
            ]
        )
        blocked_result = self.evaluator.evaluate(blocked)

        self.assertGreater(death_result.score_breakdown.quiescence_chain, 0.0)
        self.assertTrue(death_result.features.death)
        self.assertEqual(death_result.score, self.config.fatal_score)
        self.assertTrue(unreachable.features.unreachable_trigger)
        self.assertEqual(unreachable.score, self.config.fatal_score)
        self.assertTrue(blocked_result.features.structural_dead_end)
        self.assertEqual(blocked_result.score, self.config.fatal_score)

    def test_status_distinguishes_unevaluated_exhausted_and_evaluated_zero(self):
        unevaluated = ChainStructureResult.not_evaluated(
            weight_version=self.config.weight_version,
            reason="node_budget",
        )
        limited_config = ChainStructureConfig(
            weight_version=self.config.weight_version,
            budget=ChainStructureBudget(
                max_added_puyos=3,
                max_pattern_nodes=1,
                max_resolution_nodes=1,
                max_candidates=1,
            ),
            weights=self.config.weights,
            fatal_score=self.config.fatal_score,
        )
        trigger_root = self.states["fixed-trigger-root"]
        exhausted = ChainStructureEvaluator(limited_config).evaluate(
            trigger_root,
            parent=self.evaluator.evaluate(trigger_root),
            action=ChainStructureAction(),
        )
        zero = self.evaluator.evaluate(self.states["fixed-empty"])

        self.assertFalse(unevaluated.evaluated)
        self.assertIsNone(unevaluated.score)
        self.assertEqual(unevaluated.truncation_reason, "node_budget")
        self.assertTrue(exhausted.evaluated)
        self.assertEqual(exhausted.evaluation_status, "budget_exhausted")
        self.assertEqual(exhausted.truncation_reason, "pattern_nodes")
        self.assertEqual(exhausted.action_features.trigger_damage, 0)
        self.assertTrue(zero.evaluated)
        self.assertEqual(zero.evaluation_status, "not_found")
        self.assertEqual(zero.score, 0.0)

    def test_quiescence_budget_and_result_fields_are_bounded(self):
        state = self.states["tuning-connected-platform"]
        summary = bounded_quiescence(
            state,
            budget=self.config.budget,
        )

        self.assertLessEqual(
            summary.pattern_nodes,
            self.config.budget.max_pattern_nodes,
        )
        self.assertLessEqual(
            summary.resolution_nodes,
            self.config.budget.max_resolution_nodes,
        )
        self.assertLessEqual(
            len(summary.candidates),
            self.config.budget.max_candidates,
        )
        self.assertIsNotNone(summary.best)
        self.assertGreater(summary.best.chain_count, 0)
        self.assertGreater(summary.best.chain_score, 0)
        self.assertIn(summary.best.required_key_count, (1, 2, 3))
        self.assertGreaterEqual(summary.best.trigger_height, 0)
        self.assertGreaterEqual(summary.best.remaining_link_2, 0)
        self.assertGreaterEqual(summary.best.remaining_link_3, 0)


if __name__ == "__main__":
    unittest.main()
