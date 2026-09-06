import json
import unittest
from pathlib import Path

from agents.decision_flow import (
    DECISION_TRACE_SCHEMA_VERSION,
    DecisionContext,
    DecisionFlow,
    DecisionStep,
    DecisionStepContract,
    StepResult,
)
from agents.deep_chain_builder import (
    AGGREGATED_ROOT_SCORES_ARTIFACT,
    DEEP_CHAIN_BUILDER_POLICY_ID,
    DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
    N_TURN_PLAN_SCHEMA_VERSION,
    NORMALIZED_OBSERVATION_ARTIFACT,
    RUNTIME_INPUT_ARTIFACT,
    SELECTED_ACTION_ARTIFACT,
    VISIBLE_INFO_FIELDS,
    VISIBLE_OBSERVATION_FIELDS,
    AggregateScenarioScoresStep,
    CompleteVisibleQueueScenariosStep,
    DeepChainBuilderConfig,
    DeepChainBuilderPolicy,
    DeepChainBuilderProfile,
    DeepChainBuildFlow,
    EmitDecisionTraceStep,
    EnumerateRootPlacementsStep,
    NormalizeObservationStep,
    RunLongRangeSearchStep,
    SelectPlacementStep,
    VisibleRuntimeInput,
    build_visible_runtime_input,
    load_deep_chain_builder_config,
)
from puyo_env.actions import legal_action_mask
from puyo_env.obs import encode_observation
from selfplay.policies import make_policy
from src.core.headless import HeadlessPuyoSimulator

BOUNDARY_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "deep_chain_observation_boundary.json"
)


class _ArtifactStep(DecisionStep):
    def __init__(
        self,
        step_id,
        *,
        requires,
        output_key,
        output_value,
        candidate_count=None,
        selection_reason=None,
    ):
        self.contract = DecisionStepContract(
            step_id=step_id,
            requires=tuple(requires),
            provides=(output_key,),
            purpose=f"test step {step_id}",
        )
        self.output_key = output_key
        self.output_value = output_value
        self.candidate_count = candidate_count
        self.selection_reason = selection_reason

    def summarize_inputs(self, context):
        return {
            "step": self.step_id,
            "artifact_count": len(context.artifacts),
        }

    def run(self, context):
        _ = context
        return StepResult(
            outputs={self.output_key: self.output_value},
            candidate_count=self.candidate_count,
            selection_reason=self.selection_reason,
        )


class _SelectFirstLegalStep(DecisionStep):
    contract = DecisionStepContract(
        step_id="select_first_legal_fixture",
        requires=(NORMALIZED_OBSERVATION_ARTIFACT,),
        provides=(SELECTED_ACTION_ARTIFACT,),
        purpose="test-only policy adapter selection",
    )

    def run(self, context):
        visible = context.require(NORMALIZED_OBSERVATION_ARTIFACT)
        action = next(
            index for index, allowed in enumerate(visible.action_mask) if allowed
        )
        return StepResult(
            outputs={SELECTED_ACTION_ARTIFACT: action},
            candidate_count=visible.legal_action_count,
            selection_reason="first_legal_fixture",
        )


class _TimeoutStep(DecisionStep):
    contract = DecisionStepContract(
        step_id="timeout_fixture",
        requires=(NORMALIZED_OBSERVATION_ARTIFACT,),
        provides=(SELECTED_ACTION_ARTIFACT,),
        purpose="test-only timeout",
    )

    def run(self, context):
        _ = context
        raise TimeoutError("fixture deadline")


class _IllegalSelectionStep(DecisionStep):
    contract = DecisionStepContract(
        step_id="illegal_selection_fixture",
        requires=(NORMALIZED_OBSERVATION_ARTIFACT,),
        provides=(SELECTED_ACTION_ARTIFACT,),
        purpose="test-only invalid result",
    )

    def run(self, context):
        _ = context
        return StepResult(
            outputs={SELECTED_ACTION_ARTIFACT: 999},
            candidate_count=1,
            selection_reason="illegal_fixture",
        )


class _AggregatedRootFixtureStep(DecisionStep):
    contract = DecisionStepContract(
        step_id="aggregated_root_fixture",
        requires=(NORMALIZED_OBSERVATION_ARTIFACT,),
        provides=(AGGREGATED_ROOT_SCORES_ARTIFACT,),
        purpose="test-only deterministic representative",
    )

    def run(self, context):
        visible = context.require(NORMALIZED_OBSERVATION_ARTIFACT)
        action = next(
            index for index, allowed in enumerate(visible.action_mask) if allowed
        )
        state_id = f"fixture-state-{visible.step_count}"
        step = {
            "step_index": 0,
            "action": action,
            "axis_x": 0,
            "rotation": "UP",
            "known_tsumo": True,
            "scenario": "visible",
            "scenario_id": 0,
            "tsumo": ["RED", "BLUE"],
            "valid": True,
            "predicted_chain_count": 0,
            "predicted_score": 0,
            "predicted_attack": 0,
            "cumulative_score": 0,
            "cumulative_attack": 0,
            "danger": 0.0,
            "predicted_board": [],
            "placement_cells": [],
            "state_fingerprint": state_id,
            "chains": [],
            "reason": "fixture",
        }
        aggregates = (
            {
                "root_action": action,
                "ranking_key": (1.0, -action),
                "score_breakdown": {"total": 1.0},
                "evidence": {"scenario_values": [{"scenario_id": 0}]},
                "representative": {
                    "scenario_id": 0,
                    "sample_id": "fixture",
                    "actions": [action],
                    "queue_digest": state_id,
                    "root_state_fingerprint": state_id,
                    "final_state_fingerprint": state_id,
                    "trajectory_source": "fixture",
                    "steps": [step],
                },
                "search_diagnostics": {},
            },
        )
        return StepResult(
            outputs={AGGREGATED_ROOT_SCORES_ARTIFACT: aggregates},
            candidate_count=1,
            selection_reason="fixture_aggregation",
        )


class _InheritedFlow(DecisionFlow):
    def default_steps(self):
        return (
            _ArtifactStep(
                "inherited_a",
                requires=("seed",),
                output_key="a",
                output_value=1,
            ),
            _ArtifactStep(
                "inherited_b",
                requires=("a",),
                output_key="b",
                output_value=2,
            ),
        )


class _GuardedMapping(dict):
    def __init__(self, values, forbidden):
        super().__init__(values)
        self.forbidden = set(forbidden)
        self.accessed = []

    def get(self, key, default=None):
        if key in self.forbidden:
            raise AssertionError(f"private runtime key was accessed: {key}")
        self.accessed.append(key)
        return super().get(key, default)


class TestDeepChainBuilder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_deep_chain_builder_config()
        cls.fixture = json.loads(BOUNDARY_FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_external_profiles_and_benchmark_contract_are_locked(self):
        reference = self.config.profile("reference")
        smoke = self.config.profile("smoke")
        benchmark = self.config.benchmark

        self.assertEqual(self.config.policy_id, DEEP_CHAIN_BUILDER_POLICY_ID)
        self.assertEqual(
            (reference.depth, reference.width, reference.scenarios),
            (16, 250, 6),
        )
        self.assertEqual(reference.max_expanded_nodes, 600_000)
        self.assertEqual((smoke.depth, smoke.width, smoke.scenarios), (4, 8, 2))
        self.assertEqual(benchmark.seed_count * benchmark.repeats_per_seed, 60)
        self.assertEqual(benchmark.to_dict()["seeds"], list(range(123, 153)))
        self.assertEqual(benchmark.minimum_mean_actual_fire_chain_count, 10.0)
        self.assertEqual(benchmark.maximum_private_future_leaks, 0)
        self.assertTrue(benchmark.require_repeat_digest_match)
        self.assertEqual(benchmark.maximum_decision_p95_seconds, 1.0)

        payload = self.config.to_dict()
        payload["profiles"]["reference"]["depth"] = 9
        payload["profiles"]["reference"]["width"] = 32
        payload["profiles"]["reference"]["scenarios"] = 3
        custom = DeepChainBuilderConfig.from_dict(payload)
        self.assertEqual(
            (
                custom.profile("reference").depth,
                custom.profile("reference").width,
                custom.profile("reference").scenarios,
            ),
            (9, 32, 3),
        )

    def test_default_flow_exposes_reviewable_step_contracts(self):
        flow = DeepChainBuildFlow()

        self.assertEqual(
            flow.step_ids,
            (
                "normalize_observation",
                "complete_visible_queue_scenarios",
                "enumerate_root_placements",
                "run_long_range_search",
                "aggregate_scenario_scores",
                "select_placement",
                "emit_decision_trace",
            ),
        )
        self.assertTrue(all(contract.purpose for contract in flow.contracts()))
        self.assertEqual(
            flow.contracts()[0].provides,
            (NORMALIZED_OBSERVATION_ARTIFACT,),
        )
        self.assertEqual(
            flow.contracts()[-1].requires,
            ("selected_action", "selected_plan", "selection_evidence"),
        )

    def test_flow_supports_reorder_insertion_and_replacement(self):
        steps = (
            _ArtifactStep(
                "alpha",
                requires=("seed",),
                output_key="alpha_value",
                output_value=1,
                candidate_count=4,
                selection_reason="alpha_fixture",
            ),
            _ArtifactStep(
                "beta",
                requires=("seed",),
                output_key="beta_value",
                output_value=2,
            ),
        )
        base = DecisionFlow(steps)
        inserted = base.insert_after(
            "alpha",
            _ArtifactStep(
                "inserted",
                requires=("seed",),
                output_key="inserted_value",
                output_value=3,
            ),
        )
        replaced = inserted.replace_step(
            "beta",
            _ArtifactStep(
                "replacement",
                requires=("seed",),
                output_key="replacement_value",
                output_value=4,
            ),
        )
        reordered = replaced.reorder(("replacement", "alpha", "inserted"))
        context = DecisionContext(
            decision_id="composition-fixture",
            profile=self.config.profile("smoke"),
            artifacts={"seed": 1},
        )

        result = reordered.execute(context)

        self.assertEqual(
            tuple(entry.step_id for entry in result.trace_entries),
            ("replacement", "alpha", "inserted"),
        )
        self.assertEqual(result.artifacts["replacement_value"], 4)
        alpha_trace = result.trace_entries[1]
        self.assertEqual(alpha_trace.candidate_count, 4)
        self.assertEqual(alpha_trace.selection_reason, "alpha_fixture")
        self.assertGreaterEqual(alpha_trace.elapsed_seconds, 0.0)
        self.assertEqual(
            result.trace.to_dict()["schema_version"],
            DECISION_TRACE_SCHEMA_VERSION,
        )

    def test_flow_subclass_can_inherit_and_override_default_steps(self):
        flow = _InheritedFlow()
        context = DecisionContext(
            decision_id="inheritance-fixture",
            profile=self.config.profile("smoke"),
            artifacts={"seed": 1},
        )

        result = flow.execute(context)

        self.assertEqual(flow.step_ids, ("inherited_a", "inherited_b"))
        self.assertEqual(result.artifacts["b"], 2)

    def test_private_authoritative_future_never_crosses_visible_boundary(self):
        fixture = self.fixture
        sentinel = fixture["private_authoritative_state"]["sentinel"]
        forbidden = fixture["forbidden_info_keys"]
        observation_values = {
            **fixture["observation"],
            "private_future_queue": fixture["private_authoritative_state"],
        }
        info_values = {
            **fixture["info"],
            "simulator": fixture["private_authoritative_state"],
            "realtime_simulator": fixture["private_authoritative_state"],
            "private_future_queue": fixture["private_authoritative_state"],
            "puyo_sequence": fixture["private_authoritative_state"],
        }
        observation = _GuardedMapping(
            observation_values,
            {"private_future_queue"},
        )
        info = _GuardedMapping(info_values, forbidden)

        visible = build_visible_runtime_input(observation, info)
        context = DecisionContext(
            decision_id="boundary-fixture",
            profile=self.config.profile("smoke"),
            artifacts={RUNTIME_INPUT_ARTIFACT: visible},
        )
        result = DecisionFlow((NormalizeObservationStep(),)).execute(context)

        expected = fixture["expected"]
        self.assertIsInstance(visible, VisibleRuntimeInput)
        self.assertEqual(visible.visible_pair_count, expected["visible_pair_count"])
        self.assertEqual(visible.legal_action_count, expected["legal_action_count"])
        self.assertEqual(visible.score, expected["score"])
        self.assertEqual(visible.step_count, expected["step_count"])
        self.assertNotIn(sentinel, repr(result.artifacts))
        self.assertTrue(set(info.accessed).isdisjoint(forbidden))
        self.assertTrue(set(info.accessed).issubset(VISIBLE_INFO_FIELDS))
        self.assertTrue(set(observation.accessed).issubset(VISIBLE_OBSERVATION_FIELDS))
        self.assertEqual(
            result.trace_entries[0].selection_reason,
            "visible_runtime_boundary_validated",
        )

    def test_visible_boundary_accepts_production_numpy_observation(self):
        simulator = HeadlessPuyoSimulator(seed=185)
        observation = encode_observation(
            simulator,
            step_count=0,
            max_steps=40,
        )
        info = {
            "action_mask": legal_action_mask(simulator),
            "simulator": simulator,
            "score": simulator.game.score,
            "step_count": 0,
        }

        visible = build_visible_runtime_input(observation, info)

        self.assertEqual(visible.board.shape, observation["board"].shape)
        self.assertEqual(visible.next_pairs.shape, observation["next_pairs"].shape)
        self.assertIsNot(visible.board, observation["board"])
        self.assertEqual(visible.visible_pair_count, 3)
        self.assertGreater(visible.legal_action_count, 0)

    def test_visible_queue_completion_preserves_known_pairs(self):
        visible = build_visible_runtime_input(
            self.fixture["observation"], self.fixture["info"]
        )
        context = DecisionContext(
            decision_id="scenario-fixture",
            profile=self.config.profile("smoke"),
            artifacts={"normalized_observation": visible},
        )
        result = CompleteVisibleQueueScenariosStep().run(context)
        scenarios = result.outputs["scenario_sequences"]

        self.assertEqual(len(scenarios), 2)
        self.assertEqual(
            scenarios[0].to_dict()["pairs"][:3],
            [
                {"cursor": 0, "source": "known", "colors": ["RED", "BLUE"]},
                {"cursor": 1, "source": "known", "colors": ["GREEN", "YELLOW"]},
                {"cursor": 2, "source": "known", "colors": ["BLUE", "GREEN"]},
            ],
        )

    def test_search_core_enumerates_and_aggregates_deterministically(self):
        simulator = HeadlessPuyoSimulator(seed=186)
        observation = encode_observation(simulator, step_count=0, max_steps=40)
        visible = build_visible_runtime_input(
            observation,
            {
                "action_mask": legal_action_mask(simulator),
                "score": simulator.game.score,
                "step_count": 0,
            },
        )
        profile = DeepChainBuilderProfile(
            name="test",
            version="1",
            purpose="unit test",
            depth=2,
            width=2,
            scenarios=2,
            max_expanded_nodes=128,
        )
        context = DecisionContext(
            decision_id="search-core-fixture",
            profile=profile,
            artifacts={"normalized_observation": visible},
        )
        result = DecisionFlow(
            (
                CompleteVisibleQueueScenariosStep(),
                EnumerateRootPlacementsStep(),
                RunLongRangeSearchStep(),
                AggregateScenarioScoresStep(),
            )
        ).execute(context)
        values = result.require("aggregated_root_scores")

        self.assertEqual(len(values), len(result.require("root_placements")))
        self.assertEqual(
            [item["root_action"] for item in values],
            [item["root_action"] for item in values],
        )
        self.assertEqual(
            result.require("scenario_search_results")["result"].counters.expanded_nodes,
            128,
        )

    def test_policy_adapter_accepts_an_injected_complete_flow(self):
        flow = DecisionFlow((NormalizeObservationStep(), _SelectFirstLegalStep()))
        policy = DeepChainBuilderPolicy(profile="smoke", flow=flow, config=self.config)
        fixture = self.fixture

        action = policy.select_action(fixture["observation"], fixture["info"])

        self.assertEqual(action, 0)
        diagnostics = policy.tactical_diagnostics
        self.assertEqual(diagnostics["policy_id"], DEEP_CHAIN_BUILDER_POLICY_ID)
        self.assertEqual(diagnostics["selected_action"], 0)
        self.assertEqual(diagnostics["decision_trace"]["step_count"], 2)
        self.assertEqual(
            diagnostics["decision_trace"]["steps"][1]["selection_reason"],
            "first_legal_fixture",
        )

    def test_default_policy_emits_replayable_plan_and_complete_trace(self):
        simulator = HeadlessPuyoSimulator(seed=187)
        observation = encode_observation(simulator, step_count=0, max_steps=4)
        info = {
            "action_mask": legal_action_mask(simulator),
            "score": simulator.game.score,
            "step_count": 0,
            "max_steps": 4,
            "last_chain_end_score": simulator.game.last_chain_end_score,
        }
        policy = DeepChainBuilderPolicy(profile="smoke", config=self.config)

        action = policy.select_action(observation, info)
        diagnostics = policy.tactical_diagnostics
        plan = diagnostics["plan"]

        self.assertTrue(info["action_mask"][action])
        self.assertEqual(plan["schema_version"], N_TURN_PLAN_SCHEMA_VERSION)
        self.assertIsInstance(plan["profile_id"], int)
        self.assertEqual(plan["steps"][0]["action"], action)
        self.assertEqual(
            [step["known_tsumo"] for step in plan["steps"]],
            [True, True, True, False],
        )
        self.assertEqual(plan["steps"][-1]["scenario"], "unknown_scenario")
        self.assertTrue(all("scenario_id" in step for step in plan["steps"]))
        self.assertTrue(all(step["predicted_board"] for step in plan["steps"]))
        self.assertTrue(all("predicted_chain_count" in step for step in plan["steps"]))
        self.assertEqual(diagnostics["candidate_count"], 22)
        self.assertEqual(len(diagnostics["scenario_aggregation"]), 22)
        self.assertEqual(diagnostics["decision_trace"]["step_count"], 7)
        self.assertTrue(
            all(
                step["candidate_count"] is not None
                and step["selection_reason"]
                and step["elapsed_seconds"] >= 0.0
                and step["input_summary"]
                for step in diagnostics["decision_trace"]["steps"]
            )
        )
        self.assertEqual(
            diagnostics["decision_output"]["decision_trace"]["step_count"],
            7,
        )
        self.assertFalse(diagnostics["fallback"]["used"])
        json.dumps(diagnostics, allow_nan=False)

    def test_runtime_target_chain_reaches_search_plan_and_diagnostics(self):
        simulator = HeadlessPuyoSimulator(seed=187)
        observation = encode_observation(simulator, step_count=0, max_steps=4)
        info = {
            "action_mask": legal_action_mask(simulator),
            "score": simulator.game.score,
            "step_count": 0,
            "max_steps": 4,
        }
        default_policy = DeepChainBuilderPolicy(profile="smoke", config=self.config)
        explicit_default_policy = DeepChainBuilderPolicy(
            profile="smoke",
            config=self.config,
            target_chain_count=DEFAULT_DEEP_CHAIN_TARGET_CHAIN_COUNT,
        )
        experimental_policy = DeepChainBuilderPolicy(
            profile="smoke",
            config=self.config,
            target_chain_count=10,
        )

        default_action = default_policy.select_action(observation, info)
        explicit_action = explicit_default_policy.select_action(observation, info)
        experimental_policy.select_action(observation, info)
        default_diagnostics = default_policy.tactical_diagnostics
        explicit_diagnostics = explicit_default_policy.tactical_diagnostics
        experimental_diagnostics = experimental_policy.tactical_diagnostics

        self.assertEqual(default_action, explicit_action)
        self.assertEqual(
            default_diagnostics["search"]["deterministic_digest"],
            explicit_diagnostics["search"]["deterministic_digest"],
        )
        self.assertEqual(
            default_diagnostics["plan_id"], explicit_diagnostics["plan_id"]
        )
        self.assertEqual(experimental_diagnostics["target_chain_count"], 10)
        self.assertEqual(experimental_diagnostics["search"]["target_chain_count"], 10)
        self.assertEqual(
            experimental_diagnostics["backend"]["configuration"][
                "minimum_chain_count"
            ],
            10,
        )
        self.assertEqual(
            experimental_diagnostics["plan"]["objective"]["minimum_chain_count"],
            10,
        )

    def test_runtime_target_chain_rejects_values_outside_native_contract(self):
        for value in (0, 256, True):
            with self.subTest(value=value), self.assertRaises(
                (TypeError, ValueError)
            ):
                DeepChainBuilderPolicy(
                    profile="smoke",
                    config=self.config,
                    target_chain_count=value,
                )

    def test_policy_researches_and_reports_plan_change_reason(self):
        flow = DecisionFlow(
            (
                NormalizeObservationStep(),
                _AggregatedRootFixtureStep(),
                SelectPlacementStep(),
                EmitDecisionTraceStep(),
            )
        )
        policy = DeepChainBuilderPolicy(profile="smoke", flow=flow, config=self.config)
        fixture = self.fixture

        policy.select_action(fixture["observation"], fixture["info"])
        initial = policy.tactical_diagnostics
        policy.select_action(fixture["observation"], fixture["info"])
        unchanged = policy.tactical_diagnostics
        changed_info = {
            **fixture["info"],
            "step_count": fixture["info"]["step_count"] + 1,
        }
        policy.select_action(fixture["observation"], changed_info)
        changed = policy.tactical_diagnostics

        self.assertEqual(initial["replan_reason"], "initial_plan")
        self.assertEqual(initial["plan_id"], unchanged["plan_id"])
        self.assertEqual(unchanged["replan_reason"], "plan_unchanged")
        self.assertNotEqual(initial["plan_id"], changed["plan_id"])
        self.assertEqual(changed["replan_reason"], "new_observation")

    def test_timeout_and_invalid_results_use_legal_deterministic_fallback(self):
        fixture = self.fixture
        first_legal = next(
            index
            for index, allowed in enumerate(fixture["info"]["action_mask"])
            if allowed
        )
        cases = (
            (_TimeoutStep(), "search_timeout"),
            (_IllegalSelectionStep(), "invalid_search_result"),
        )
        for failing_step, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                policy = DeepChainBuilderPolicy(
                    profile="smoke",
                    flow=DecisionFlow((NormalizeObservationStep(), failing_step)),
                    config=self.config,
                )

                action = policy.select_action(fixture["observation"], fixture["info"])
                diagnostics = policy.tactical_diagnostics

                self.assertEqual(action, first_legal)
                self.assertEqual(diagnostics["plan"]["steps"][0]["action"], action)
                self.assertTrue(diagnostics["fallback"]["used"])
                self.assertEqual(diagnostics["fallback"]["reason"], expected_reason)
                self.assertTrue(diagnostics["fallback"]["detail"])
                self.assertEqual(
                    diagnostics["decision_trace"]["steps"][0]["step_id"],
                    "deterministic_fallback",
                )

    def test_factory_builds_deep_chain_policy_without_checkpoint(self):
        policy = make_policy("deep_chain_builder")
        smoke_policy = make_policy(
            "deep_chain_builder",
            deep_chain_profile="smoke",
            deep_chain_target_chain=10,
        )

        self.assertIsInstance(policy, DeepChainBuilderPolicy)
        self.assertEqual(policy.profile.name, "reference")
        self.assertEqual(policy.backend_mode, "python")
        self.assertEqual(smoke_policy.profile.name, "smoke")
        self.assertEqual(smoke_policy.target_chain_count, 10)


if __name__ == "__main__":
    unittest.main()
