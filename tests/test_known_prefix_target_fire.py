"""The one PUYO-242 preference, its public boundary, and tracker retention."""

import unittest
from dataclasses import replace
from types import SimpleNamespace

from agents.compact_search import CompactSearchState
from agents.decision_flow import DecisionContext
from agents.deep_chain_builder import (
    CompleteVisibleQueueScenariosStep,
    _decode_visible_pairs,
    build_visible_runtime_input,
    load_deep_chain_builder_config,
)
from agents.long_horizon_search import (
    FIRE_CLASS_TARGET,
    ChainFireEvidence,
    _ScenarioTracker,
    aggregate_expected_chain_evidence,
    classify_build_main_fire,
)
from puyo_env.obs import encode_observation
from src.core.headless import HeadlessPuyoSimulator
from tests.test_long_horizon_search import _scenario_value


def fire(depth, score, *, known=3, kind=FIRE_CLASS_TARGET):
    return ChainFireEvidence(
        root_action=3, scenario_id=0, chain_count=10, chain_score=1000,
        depth=depth, trigger_action=3, state_fingerprint="test-state",
        path=(3,) * depth, terminal=True, terminal_reason="chain_count_gte_1",
        fire_class=kind, target_chain_count=10, allowed=True,
        terminal_score=score, known_pair_count=known,
    )


class TestKnownPrefixTargetFire(unittest.TestCase):
    def test_public_current_next_next2_and_short_queue_define_prefix(self):
        simulator = HeadlessPuyoSimulator(seed=123)
        observation = encode_observation(simulator, step_count=0, max_steps=40)
        expected = ((simulator.game.current_puyo_1.color, simulator.game.current_puyo_2.color),) + tuple(
            (a.color, b.color) for a, b in tuple(simulator.game.next_puyo_queue)[:2]
        )
        self.assertEqual(_decode_visible_pairs(observation["next_pairs"]), expected)
        for known in (1, 2, 3):
            visible = build_visible_runtime_input(
                {**observation, "next_pairs": observation["next_pairs"][:known], "private_future": object()},
                {"simulator": object(), "future_queue": object()},
            )
            context = DecisionContext(
                decision_id="public-prefix", profile=load_deep_chain_builder_config().profile("smoke"),
                artifacts={"normalized_observation": visible},
            )
            result = CompleteVisibleQueueScenariosStep().run(context)
            for sequence in result.outputs["scenario_sequences"]:
                self.assertEqual(sequence.known_pairs, expected[:known])
                self.assertEqual(sequence.known_pair_count, known)
                self.assertEqual(sequence.to_dict()["pairs"][known]["source"], "unknown")

    def test_depth_is_one_based_and_uses_actual_short_queue_length(self):
        for known in (1, 2, 3):
            with self.subTest(known=known):
                near, far = fire(known, 1, known=known), fire(known + 1, 100000, known=known)
                self.assertTrue(near.known_prefix_target)
                self.assertFalse(far.known_prefix_target)
                self.assertGreater(near.rank_key, far.rank_key)
                # Being closer within the prefix adds no extra preference.
                self.assertGreater(fire(known, 2, known=known).rank_key,
                                   fire(1, 1, known=known).rank_key)
        self.assertFalse(fire(1, 10, known=0).known_prefix_target)

    def test_preference_is_target_only_and_existing_class_order_is_preserved(self):
        for kind in ("winning_fire", "forced_safety_fire", "premature_fire"):
            near, far = fire(1, 1, kind=kind), fire(4, 100, kind=kind)
            self.assertFalse(near.known_prefix_target)
            self.assertGreater(far.rank_key, near.rank_key)
        self.assertGreater(fire(4, 1, kind="winning_fire").rank_key, fire(1, 100).rank_key)
        self.assertGreater(fire(4, 1).rank_key, fire(1, 100, kind="forced_safety_fire").rank_key)
        self.assertEqual(classify_build_main_fire(chain_count=9, chain_score=1000,
                         target_chain_count=10, fire_context="safe_build"), "premature_fire")

    def test_tracker_keeps_near_target_and_its_terminal_but_official_max_remains(self):
        for depths in ((4, 3), (3, 4)):
            tracker = _ScenarioTracker(3, 0, "record_and_stop", 1, 1, known_pair_count=3)
            nodes = {}
            for depth in depths:
                node = SimpleNamespace(path=(3,) * depth)
                nodes[depth] = node
                evidence = tracker.record_fire(
                    result=SimpleNamespace(chain_count=10, score_delta=depth * 1000,
                                           state=CompactSearchState(planes=(0,) * 6)),
                    evaluation=None, fire_class=FIRE_CLASS_TARGET,
                    terminal_score=depth * 1000, terminal_score_breakdown={},
                    target_chain_count=10, path=node.path,
                    terminal=True, terminal_reason="chain_count_gte_1",
                )
                tracker.record_terminal(node, evidence)
            self.assertEqual(tracker.selected_fire.depth, 3)
            self.assertEqual(tracker.best_fire.depth, 4)
            self.assertIs(tracker.representative, nodes[3])
            self.assertEqual(tracker.fire_count, 2)

    def test_root_and_representative_prefer_near_target_after_coverage_and_support(self):
        def root(fires, requested=2):
            return aggregate_expected_chain_evidence(3, [
                replace(_scenario_value(i, 10, 1000), selected_fire=f,
                        selected_fire_class=FIRE_CLASS_TARGET)
                for i, f in enumerate(fires)
            ], requested_scenarios=requested)
        near = root([fire(3, 1), fire(4, 1000)])
        far = root([fire(4, 100000), fire(4, 100000)])
        self.assertEqual(near.best_fire.depth, 3)
        self.assertGreater(near.ranking_key, far.ranking_key)
        self.assertGreater(far.ranking_key, root([fire(1, 1)]).ranking_key)
        self.assertGreater(root([fire(3, 2)]).ranking_key, root([fire(1, 1)]).ranking_key)


if __name__ == "__main__":
    unittest.main()
