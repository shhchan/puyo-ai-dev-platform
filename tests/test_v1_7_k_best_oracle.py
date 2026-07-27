import json
import tempfile
import unittest
from pathlib import Path

from agents.beam_search import BuildPotentialBudget, clone_simulator
from eval.v1_7_k_best_oracle import (
    ORACLE_DECISION_SCHEMA_VERSION,
    ORACLE_SUITE_SCHEMA_VERSION,
    ORACLE_TRAJECTORY_SCHEMA_VERSION,
    OracleSearchConfiguration,
    authoritative_future_view,
    classify_build_premature_fire,
    run_oracle_artifacts,
    select_offline_oracle_candidate,
    verify_oracle_artifacts,
)
from src.core.headless import HeadlessPuyoSimulator


def _fast_configuration(**overrides):
    values = {
        "depth": 1,
        "width": 22,
        "scenarios": 1,
        "max_expanded_nodes": 100,
        "build_steps": 1,
        "fire_steps": 1,
        "preview_steps": 1,
        "build_potential_budget": BuildPotentialBudget(
            max_added_puyos=1,
            max_pattern_nodes=1,
            max_resolution_nodes=1,
            max_alternatives=1,
            max_continuation_actions=1,
            max_recovery_puyos=0,
        ),
    }
    values.update(overrides)
    return OracleSearchConfiguration.for_profile("runtime", **values)


def _evaluation(
    candidate_id,
    index,
    *,
    maximum_chain=0,
    immediate_chain=0,
    structural_potential=0,
    structural_score=0.0,
):
    return {
        "candidate_id": candidate_id,
        "candidate_index": index,
        "root_action": index,
        "candidate_value": float(index),
        "search_predicted_chain_count": maximum_chain,
        "structural_prediction": {
            "score": structural_score,
            "potential_chain_count": structural_potential,
            "trigger_reachable": structural_potential > 0,
            "trigger_id": f"trigger-{index}",
        },
        "immediate_outcome": {
            "valid": True,
            "game_over": False,
            "actual_chain_count": immediate_chain,
        },
        "authoritative_future_rollout": {
            "maximum_chain_count": maximum_chain,
            "target_fire_depth": 2 if maximum_chain >= 10 else None,
            "final_board": {"danger": 0.1, "value": float(index)},
        },
        "danger": 0.1,
        "continuation_flexibility": 0.5,
    }


class TestV17KBestOracle(unittest.TestCase):
    def test_authoritative_future_snapshot_is_reproducible_and_non_mutating(self):
        simulator = HeadlessPuyoSimulator(seed=180)
        before = clone_simulator(simulator)

        first = authoritative_future_view(simulator, pair_count=6)
        second = authoritative_future_view(simulator, pair_count=6)

        self.assertEqual(first.digest, second.digest)
        self.assertEqual(first.pairs, second.pairs)
        self.assertEqual(first.known_pair_count, 3)
        self.assertEqual(
            authoritative_future_view(before, pair_count=6).digest,
            first.digest,
        )
        self.assertEqual(
            simulator.game.current_puyo_1.color,
            before.game.current_puyo_1.color,
        )
        self.assertEqual(
            simulator.game.current_puyo_2.color,
            before.game.current_puyo_2.color,
        )

    def test_oracle_selects_only_from_k_best_and_uses_actual_future_fire(self):
        evaluations = [
            _evaluation("quiet", 0, structural_potential=9, structural_score=100.0),
            _evaluation(
                "target",
                1,
                maximum_chain=10,
                structural_potential=10,
                structural_score=50.0,
            ),
        ]

        selected = select_offline_oracle_candidate(
            evaluations,
            phase="fire",
            previous_trigger=None,
            target_chain_count=10,
        )

        self.assertEqual(selected, 1)
        self.assertLess(selected, len(evaluations))

    def test_build_premature_fire_classification_is_explicit(self):
        quiet = {
            "valid": True,
            "game_over": False,
            "chain_count": 0,
        }
        premature = {
            "valid": True,
            "game_over": False,
            "chain_count": 3,
        }

        self.assertEqual(
            classify_build_premature_fire(
                selected_chain_count=3,
                target_chain_count=10,
                candidate_root_outcomes=[premature, quiet],
                legal_root_outcomes=[premature, quiet],
            ),
            "oracle_error",
        )
        self.assertEqual(
            classify_build_premature_fire(
                selected_chain_count=3,
                target_chain_count=10,
                candidate_root_outcomes=[premature],
                legal_root_outcomes=[premature, quiet],
            ),
            "candidate_limited",
        )
        self.assertEqual(
            classify_build_premature_fire(
                selected_chain_count=3,
                target_chain_count=10,
                candidate_root_outcomes=[premature],
                legal_root_outcomes=[premature],
            ),
            "unavoidable",
        )
        self.assertEqual(
            classify_build_premature_fire(
                selected_chain_count=0,
                target_chain_count=10,
                candidate_root_outcomes=[quiet],
                legal_root_outcomes=[quiet],
            ),
            "none",
        )

    def test_artifact_tracks_phase_boundary_selectors_and_determinism(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = run_oracle_artifacts(
                directory,
                seeds=(123,),
                configuration=_fast_configuration(),
                repetitions=2,
            )
            verified = verify_oracle_artifacts(directory)
            records = json.loads(
                (Path(directory) / "trajectory_records.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(summary["schema_version"], ORACLE_SUITE_SCHEMA_VERSION)
        self.assertTrue(summary["determinism"]["passed"])
        self.assertTrue(summary["future_isolation_passed"])
        self.assertTrue(summary["phase_limits_respected"])
        self.assertTrue(summary["executed_roots_in_k_best"])
        self.assertNotIn("seed_results", summary)
        self.assertEqual(verified["status"], "passed")
        trajectories = records["seed_results"][0]["trajectories"]
        self.assertEqual(
            {trajectory["summary"]["selector"] for trajectory in trajectories},
            {
                "offline_oracle",
                "compatibility_rank_0",
                "legacy_capability_selector",
            },
        )
        for trajectory in trajectories:
            self.assertEqual(
                trajectory["schema_version"],
                ORACLE_TRAJECTORY_SCHEMA_VERSION,
            )
            self.assertLessEqual(trajectory["summary"]["build_decisions"], 1)
            self.assertLessEqual(trajectory["summary"]["fire_decisions"], 1)
            lifecycle = trajectory["summary"]["phase_lifecycle"]
            self.assertTrue(lifecycle["build"]["entered"])
            self.assertEqual(
                lifecycle["build"]["decision_count"],
                trajectory["summary"]["build_decisions"],
            )
            self.assertEqual(
                lifecycle["fire"]["decision_count"],
                trajectory["summary"]["fire_decisions"],
            )
            self.assertEqual(
                lifecycle["fire"]["entered"],
                trajectory["summary"]["phase_boundary"] is not None,
            )
            self.assertIsNotNone(lifecycle["build"]["end_reason"])
            self.assertIsNotNone(lifecycle["fire"]["end_reason"])
            for decision in trajectory["decisions"]:
                self.assertEqual(
                    decision["schema_version"],
                    ORACLE_DECISION_SCHEMA_VERSION,
                )
                self.assertEqual(
                    decision["oracle_private_future"]["scope"],
                    "offline_oracle_evaluator_only",
                )
                self.assertFalse(
                    decision["selector_comparison"]["learned_selector"][
                        "input_contract"
                    ]["oracle_future_fields_present"]
                )


if __name__ == "__main__":
    unittest.main()
