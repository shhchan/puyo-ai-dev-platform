import os
import unittest
from concurrent.futures import Future

from puyo_env.action_planner import plan_placement_action
from puyo_env.actions import NUM_ACTIONS
from puyo_env.realtime_ai import (
    REALTIME_OBSERVATION_SCHEMA_VERSION,
    PolicyProcessExecutor,
    RealtimeDecisionConfig,
    RealtimePolicyController,
    RealtimePuyoEnv,
    build_realtime_info,
    build_realtime_observation,
    realtime_checkpoint_metadata,
    realtime_reachable_action_mask,
    validate_realtime_checkpoint_metadata,
)
from puyo_env.realtime_versus import RealtimeVersusMatch
from selfplay.policies import FirstLegalPolicy
from src.core.constants import PuyoColor
from src.core.puyo import Puyo


class ProcessTestPolicy:
    def select_action(self, observation, info):
        return int(info["selected_action"])

    @property
    def tactical_diagnostics(self):
        return {"schema_version": "process-test-v1", "worker_pid": os.getpid()}


class TestRealtimeAI(unittest.TestCase):
    def test_policy_process_executor_uses_spawned_process_and_returns_diagnostics(self):
        executor = PolicyProcessExecutor(ProcessTestPolicy())
        try:
            future = executor.submit_policy({}, {"selected_action": 4})
            selected, elapsed, diagnostics = future.result(timeout=5.0)
        finally:
            executor.shutdown(wait=True)

        self.assertEqual(selected, 4)
        self.assertGreaterEqual(elapsed, 0.0)
        self.assertEqual(diagnostics["schema_version"], "process-test-v1")
        self.assertNotEqual(diagnostics["worker_pid"], os.getpid())

    def test_realtime_observation_contains_deadline_and_reachable_mask(self):
        match = RealtimeVersusMatch(seed=123, attack_delay_ticks=12)
        match.schedule_attack("player_0", 4, delay_ticks=7)

        observation = build_realtime_observation(match, "player_1", include_action_mask=True)
        info = build_realtime_info(match, "player_1")

        self.assertEqual(observation["schema_version"], REALTIME_OBSERVATION_SCHEMA_VERSION)
        self.assertEqual(observation["action_mask"].shape, (NUM_ACTIONS,))
        self.assertEqual(info["incoming_ticks"], 7)
        self.assertEqual(info["incoming_arrival_tick"], 7)
        self.assertEqual(info["own_phase"], "control")
        self.assertIn("active_position", info)

    def test_runtime_info_distinguishes_initial_empty_and_opponent_state(self):
        match = RealtimeVersusMatch(seed=123)
        own_game = match.player_states["player_0"].simulator.game
        opponent_game = match.player_states["player_1"].simulator.game
        own_game.all_clear_achieved = True
        own_game.all_clear_bonus_pending = True
        opponent_game.field.place_puyo(0, 0, Puyo(PuyoColor.RED))
        opponent_game.all_clear_bonus_consumed = True

        info = build_realtime_info(match, "player_0")

        self.assertEqual(
            info["all_clear_diagnostics_schema_version"],
            "puyo.all_clear_diagnostics.v1",
        )
        self.assertTrue(info["board_empty"])
        self.assertTrue(info["all_clear_achieved"])
        self.assertTrue(info["all_clear_bonus_pending"])
        self.assertFalse(info["all_clear_bonus_consumed"])
        self.assertFalse(info["opponent_board_empty"])
        self.assertTrue(info["opponent_all_clear_bonus_consumed"])

    def test_reachable_action_mask_matches_planner(self):
        match = RealtimeVersusMatch(seed=123)

        mask = realtime_reachable_action_mask(match.player_states["player_0"].simulator)

        for index, allowed in enumerate(mask):
            with self.subTest(index=index):
                plan = plan_placement_action(
                    match.player_states["player_0"].simulator,
                    action=index_to_placement(index),
                )
                self.assertEqual(bool(allowed), plan.reachable)

    def test_controller_injects_latency_before_executing_plan(self):
        match = RealtimeVersusMatch(seed=123)
        controller = RealtimePolicyController(
            FirstLegalPolicy(),
            config=RealtimeDecisionConfig(inference_latency_ticks=2),
        )

        first = controller.next_input(match, "player_0")
        match.step({"player_0": first})
        second = controller.next_input(match, "player_0")
        match.step({"player_0": second})
        third = controller.next_input(match, "player_0")

        self.assertEqual(first.press, ())
        self.assertEqual(second.press, ())
        self.assertNotEqual(third, first)
        self.assertEqual(controller.diagnostics.decisions_started, 1)
        self.assertEqual(controller.diagnostics.idle_ticks, 2)
        self.assertEqual(controller.diagnostics.last_decision.inference_latency_ticks, 2)
        self.assertEqual(controller.diagnostics.last_decision.latency_mode, "configured")
        self.assertEqual(controller.diagnostics.last_decision.request_tick, 0)
        self.assertEqual(controller.diagnostics.last_decision.completion_tick, 0)
        self.assertEqual(controller.diagnostics.last_decision.scheduled_activation_tick, 2)
        self.assertEqual(controller.diagnostics.last_decision.activation_tick, 2)
        self.assertEqual(controller.diagnostics.last_decision.outcome, "activated")

    def test_controller_timeout_uses_fallback_deadline(self):
        match = RealtimeVersusMatch(seed=123)
        controller = RealtimePolicyController(
            FirstLegalPolicy(),
            config=RealtimeDecisionConfig(inference_latency_ticks=5, timeout_ticks=1),
        )

        controller.next_input(match, "player_0")
        match.step()
        tick_input = controller.next_input(match, "player_0")

        self.assertEqual(controller.diagnostics.timeouts, 1)
        self.assertTrue(controller.diagnostics.last_decision.timeout)
        self.assertTrue(controller.diagnostics.last_decision.fallback)
        self.assertEqual(controller.diagnostics.last_decision.timeout_tick, 1)
        self.assertEqual(controller.diagnostics.last_decision.activation_tick, 1)
        self.assertEqual(controller.diagnostics.last_decision.outcome, "fallback")
        self.assertNotEqual(tick_input, type(tick_input)())

    def test_measured_async_latency_uses_completion_match_tick(self):
        class ManualExecutor:
            def __init__(self):
                self.future = Future()

            def submit(self, function):
                return self.future

        match = RealtimeVersusMatch(seed=123)
        executor = ManualExecutor()
        controller = RealtimePolicyController(
            FirstLegalPolicy(),
            config=RealtimeDecisionConfig(latency_mode="measured"),
            decision_executor=executor,
        )

        first = controller.next_input(match, "player_0")
        match.step({"player_0": first})
        executor.future.set_result((0, 0.125))
        second = controller.next_input(match, "player_0")

        decision = controller.diagnostics.last_decision
        self.assertNotEqual(second, type(second)())
        self.assertEqual(decision.request_tick, 0)
        self.assertEqual(decision.completion_tick, 1)
        self.assertEqual(decision.activation_tick, 1)
        self.assertEqual(decision.inference_latency_ticks, 1)
        self.assertEqual(decision.latency_mode, "measured")

    def test_configured_latency_activates_on_same_tick_for_sync_and_async(self):
        class ManualExecutor:
            def __init__(self):
                self.future = Future()

            def submit(self, function):
                return self.future

        sync_match = RealtimeVersusMatch(seed=123)
        async_match = RealtimeVersusMatch(seed=123)
        config = RealtimeDecisionConfig(inference_latency_ticks=2)
        sync_controller = RealtimePolicyController(FirstLegalPolicy(), config=config)
        executor = ManualExecutor()
        async_controller = RealtimePolicyController(
            FirstLegalPolicy(),
            config=config,
            decision_executor=executor,
        )

        sync_inputs = [sync_controller.next_input(sync_match, "player_0")]
        async_inputs = [async_controller.next_input(async_match, "player_0")]
        executor.future.set_result((0, 0.25))
        for _ in range(2):
            sync_match.step({"player_0": sync_inputs[-1]})
            async_match.step({"player_0": async_inputs[-1]})
            sync_inputs.append(sync_controller.next_input(sync_match, "player_0"))
            async_inputs.append(async_controller.next_input(async_match, "player_0"))

        self.assertEqual(sync_inputs, async_inputs)
        self.assertEqual(sync_controller.diagnostics.last_decision.activation_tick, 2)
        self.assertEqual(async_controller.diagnostics.last_decision.activation_tick, 2)
        self.assertEqual(async_controller.diagnostics.last_decision.completion_tick, 1)

    def test_async_timeout_activates_fallback_and_discards_late_result(self):
        class ManualExecutor:
            def __init__(self):
                self.future = Future()

            def submit(self, function):
                return self.future

        match = RealtimeVersusMatch(seed=123)
        executor = ManualExecutor()
        controller = RealtimePolicyController(
            FirstLegalPolicy(),
            config=RealtimeDecisionConfig(latency_mode="measured", timeout_ticks=1),
            decision_executor=executor,
        )

        controller.next_input(match, "player_0")
        match.step()
        tick_input = controller.next_input(match, "player_0")

        decision = controller.diagnostics.last_decision
        self.assertTrue(executor.future.cancelled())
        self.assertNotEqual(tick_input, type(tick_input)())
        self.assertTrue(decision.timeout)
        self.assertTrue(decision.fallback)
        self.assertEqual(decision.request_tick, 0)
        self.assertEqual(decision.completion_tick, 1)
        self.assertEqual(decision.activation_tick, 1)
        self.assertEqual(decision.fallback_reason, "timeout_fallback")

    def test_measured_inference_latency_counts_toward_action_deadline(self):
        class ManualExecutor:
            def __init__(self):
                self.future = Future()

            def submit(self, function):
                return self.future

        match = RealtimeVersusMatch(seed=123)
        executor = ManualExecutor()
        controller = RealtimePolicyController(
            FirstLegalPolicy(),
            config=RealtimeDecisionConfig(
                latency_mode="measured",
                action_deadline_ticks=62,
            ),
            decision_executor=executor,
        )

        controller.next_input(match, "player_0")
        match.step()
        executor.future.set_result((0, 0.125))
        controller.next_input(match, "player_0")

        decision = controller.diagnostics.last_decision
        self.assertTrue(decision.deadline_miss)
        self.assertTrue(decision.fallback)
        self.assertEqual(decision.reason, "deadline_fallback")
        self.assertEqual(decision.inference_latency_ticks, 1)

    def test_deadline_fallback_prefers_short_reachable_plan(self):
        match = RealtimeVersusMatch(seed=123)
        controller = RealtimePolicyController(
            FirstLegalPolicy(),
            config=RealtimeDecisionConfig(
                action_deadline_ticks=48,
                use_reachable_action_mask=True,
            ),
        )

        controller.next_input(
            match,
            "player_0",
            build_realtime_observation(match, "player_0", include_action_mask=True),
            build_realtime_info(match, "player_0", use_reachable_action_mask=True),
        )

        decision = controller.diagnostics.last_decision
        self.assertTrue(decision.deadline_miss)
        self.assertTrue(decision.fallback)
        self.assertEqual(decision.reason, "deadline_fallback")
        self.assertEqual(decision.action_index, 7)
        self.assertLess(decision.plan_ticks, 62)

    def test_realtime_env_returns_reward_components_and_episode_info(self):
        env = RealtimePuyoEnv(seed=123, max_ticks=2)
        observations, infos = env.reset(seed=123)

        self.assertIn("realtime_scalars", observations["player_0"])
        self.assertEqual(len(infos["player_0"]["action_mask"]), NUM_ACTIONS)

        _, rewards, _, truncations, infos = env.step()
        _, rewards, _, truncations, infos = env.step()

        self.assertTrue(truncations["player_0"])
        self.assertIn("reward_components", infos["player_0"])
        self.assertIn("episode", infos["player_0"])
        self.assertIn("player_0", rewards)

    def test_realtime_env_does_not_truncate_when_tick_limit_is_disabled(self):
        env = RealtimePuyoEnv(seed=123, max_ticks=None)
        _, infos = env.reset(seed=123)

        self.assertIsNone(infos["player_0"]["max_ticks"])
        for _ in range(3):
            _, _, _, truncations, _ = env.step()
            self.assertFalse(truncations["player_0"])
        self.assertTrue(env.agents)

    def test_checkpoint_metadata_accepts_native_and_turn_based_adapter(self):
        native = {"realtime_policy": realtime_checkpoint_metadata(native_realtime=True)}
        turn_based = {"model_state_dict": {"cnn.0.weight": object()}}

        self.assertEqual(validate_realtime_checkpoint_metadata(native)["mode"], "realtime_native")
        self.assertEqual(
            validate_realtime_checkpoint_metadata(turn_based)["mode"],
            "turn_based_placement_adapter",
        )
        with self.assertRaises(ValueError):
            validate_realtime_checkpoint_metadata({}, allow_turn_based_adapter=False)


def index_to_placement(index):
    from puyo_env.actions import action_to_placement

    return action_to_placement(index)


if __name__ == "__main__":
    unittest.main()
