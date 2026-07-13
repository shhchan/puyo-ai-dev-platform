import unittest


try:
    import gymnasium  # noqa: F401
    import numpy

    from puyo_env.actions import placement_to_action_index
    from puyo_env.realtime_versus import RealtimeVersusMatch
    from puyo_env.selfplay_env import VersusSelfPlayEnv
    from puyo_env.versus_env import VersusPuyoEnv
    from selfplay.policies import FirstLegalPolicy
    from src.core.constants import Direction, PuyoColor, VISIBLE_HEIGHT
    from src.core.headless import PlacementAction
    from src.core.puyo import Puyo

    VERSUS_ENV_AVAILABLE = True
except Exception:
    VERSUS_ENV_AVAILABLE = False
    numpy = None
    VersusPuyoEnv = None
    RealtimeVersusMatch = None
    VersusSelfPlayEnv = None
    FirstLegalPolicy = None
    Direction = None
    PlacementAction = None
    PuyoColor = None
    VISIBLE_HEIGHT = None
    Puyo = None


@unittest.skipUnless(VERSUS_ENV_AVAILABLE, "gymnasium/numpy are not installed")
class TestVersusPuyoEnv(unittest.TestCase):
    def test_reset_exposes_both_players_with_same_tsumo_seed(self):
        env = VersusPuyoEnv(seed=123, max_steps=10)

        observations, infos = env.reset(seed=123)

        self.assertEqual(set(observations), {"player_0", "player_1"})
        self.assertEqual(observations["player_0"]["board"].shape, (12, 13, 6))
        self.assertEqual(observations["player_0"]["own_board"].shape, (6, 13, 6))
        self.assertEqual(int(infos["player_0"]["action_mask"].sum()), 22)
        numpy.testing.assert_array_equal(
            observations["player_0"]["next_pairs"],
            observations["player_1"]["next_pairs"],
        )
        self.assertEqual(
            infos["player_0"]["all_clear_diagnostics_schema_version"],
            "puyo.all_clear_diagnostics.v1",
        )
        self.assertTrue(infos["player_0"]["board_empty"])
        self.assertFalse(infos["player_0"]["all_clear_achieved"])
        self.assertFalse(infos["player_0"]["all_clear_bonus_pending"])
        self.assertFalse(infos["player_0"]["all_clear_bonus_consumed"])
        self.assertIsNone(infos["player_0"]["termination_reason"])

    def test_runtime_info_keeps_own_and_opponent_all_clear_state_independent(self):
        env = VersusPuyoEnv(seed=123, max_steps=10)
        env.reset(seed=123)
        game_0 = env.player_states["player_0"].simulator.game
        game_1 = env.player_states["player_1"].simulator.game
        game_0.all_clear_achieved = True
        game_0.all_clear_bonus_pending = True
        game_1.field.place_puyo(0, 0, Puyo(PuyoColor.RED))
        game_1.all_clear_bonus_consumed = True

        info = env._info("player_0")

        self.assertTrue(info["board_empty"])
        self.assertTrue(info["all_clear_achieved"])
        self.assertTrue(info["all_clear_bonus_pending"])
        self.assertFalse(info["all_clear_bonus_consumed"])
        self.assertFalse(info["opponent_board_empty"])
        self.assertFalse(info["opponent_all_clear_achieved"])
        self.assertFalse(info["opponent_all_clear_bonus_pending"])
        self.assertTrue(info["opponent_all_clear_bonus_consumed"])

    def test_first_legal_policies_run_until_truncated(self):
        env = VersusPuyoEnv(seed=123, max_steps=3)
        observations, infos = env.reset(seed=123)
        policy = FirstLegalPolicy()

        done = False
        steps = 0
        while not done:
            actions = {
                agent: policy.select_action(observations[agent], infos[agent])
                for agent in env.agents
            }
            observations, _, terminations, truncations, infos = env.step(actions)
            done = all(terminations.values()) or all(truncations.values())
            steps += 1

        self.assertEqual(steps, 3)
        self.assertIn("episode", infos["player_0"])
        self.assertIn("max_chain", infos["player_0"]["episode"])
        self.assertIn("max_chain_count", infos["player_0"])
        self.assertEqual(infos["player_0"]["termination_reason"], "step_limit")
        self.assertEqual(infos["player_1"]["termination_reason"], "step_limit")

    def test_invalid_action_has_explicit_termination_reason(self):
        env = VersusPuyoEnv(seed=123, max_steps=3)
        _, _ = env.reset(seed=123)

        _, _, terminations, _, infos = env.step(
            {"player_0": -1, "player_1": 0}
        )

        self.assertTrue(terminations["player_0"])
        self.assertEqual(infos["player_0"]["termination_reason"], "invalid_action")
        self.assertIsNone(infos["player_1"]["termination_reason"])

    def test_due_garbage_top_out_has_explicit_termination_reason(self):
        env = VersusPuyoEnv(seed=123, max_steps=3)
        observations, infos = env.reset(seed=123)
        state = env.player_states["player_0"]
        for y in range(VISIBLE_HEIGHT - 1):
            color = PuyoColor.BLUE if y % 2 == 0 else PuyoColor.RED
            state.simulator.game.field.place_puyo(2, y, Puyo(color))
        state.pending_ojama = 6
        policy = FirstLegalPolicy()
        actions = {
            agent: policy.select_action(observations[agent], infos[agent])
            for agent in env.agents
        }

        _, _, terminations, _, infos = env.step(actions)

        self.assertTrue(terminations["player_0"])
        self.assertEqual(infos["player_0"]["termination_reason"], "garbage_top_out")

    def test_scheduled_ojama_allows_one_response_action_before_drop(self):
        env = VersusPuyoEnv(seed=123, max_steps=5)
        observations, infos = env.reset(seed=123)
        env._schedule_attack("player_0", 3)
        policy = FirstLegalPolicy()

        actions = {
            agent: policy.select_action(observations[agent], infos[agent])
            for agent in env.agents
        }
        observations, _, _, _, infos = env.step(actions)

        self.assertEqual(infos["player_1"]["pending_ojama"], 3)
        self.assertEqual(infos["player_1"]["incoming_turns"], 1)
        self.assertEqual(infos["player_1"]["received_ojama_total"], 0)

        actions = {
            agent: policy.select_action(observations[agent], infos[agent])
            for agent in env.agents
        }
        _, _, _, _, infos = env.step(actions)

        self.assertEqual(infos["player_1"]["pending_ojama"], 0)
        self.assertEqual(infos["player_1"]["received_ojama_total"], 3)

    def test_attack_resolution_handles_full_partial_and_excess_cancel(self):
        env = VersusPuyoEnv(seed=123, max_steps=5)
        env.reset(seed=123)
        env.player_states["player_0"].pending_ojama = 10

        partial = env._resolve_attacks({"player_0": 6, "player_1": 0})

        self.assertEqual(partial["player_0"], {"generated": 6, "canceled": 6, "outgoing": 0})
        self.assertEqual(env.player_states["player_0"].pending_ojama, 4)

        excess = env._resolve_attacks({"player_0": 7, "player_1": 0})

        self.assertEqual(excess["player_0"], {"generated": 7, "canceled": 4, "outgoing": 3})
        self.assertEqual(env.player_states["player_0"].pending_ojama, 0)
        self.assertEqual(env.player_states["player_1"].pending_ojama, 3)

    def test_simultaneous_attacks_cancel_without_player_order_bias(self):
        env = VersusPuyoEnv(seed=123, max_steps=5)
        env.reset(seed=123)

        attacks = env._resolve_attacks({"player_0": 8, "player_1": 3})

        self.assertEqual(attacks["player_0"], {"generated": 8, "canceled": 3, "outgoing": 5})
        self.assertEqual(attacks["player_1"], {"generated": 3, "canceled": 3, "outgoing": 0})
        self.assertEqual(env.player_states["player_0"].pending_ojama, 0)
        self.assertEqual(env.player_states["player_1"].pending_ojama, 5)

    def test_score_carry_matches_chain_end_boundary_conversion(self):
        env = VersusPuyoEnv(seed=123, max_steps=5)
        env.reset(seed=123)

        generated = [
            env._attack_units_from_score("player_0", score_delta)
            for score_delta in (40, 29, 1, 71)
        ]

        self.assertEqual(generated, [0, 0, 1, 1])
        self.assertEqual(env.player_states["player_0"].score_carry, 1)

    def test_all_clear_bonus_uses_attack_cancellation_order_with_realtime_parity(self):
        env = VersusPuyoEnv(seed=123, max_steps=10, attack_delay_steps=100)
        env.reset(seed=123)
        attack_action = placement_to_action_index(PlacementAction(2, Direction.UP))
        idle_action = placement_to_action_index(PlacementAction(4, Direction.UP))
        game_0 = env.player_states["player_0"].simulator.game
        game_1 = env.player_states["player_1"].simulator.game

        game_0.current_puyo_1 = Puyo(PuyoColor.RED)
        game_0.current_puyo_2 = Puyo(PuyoColor.RED)
        for y in (0, 1):
            game_0.field.place_puyo(1, y, Puyo(PuyoColor.RED))
        game_1.current_puyo_1 = Puyo(PuyoColor.PURPLE)
        game_1.current_puyo_2 = Puyo(PuyoColor.YELLOW)

        _, _, _, _, infos = env.step(
            {"player_0": attack_action, "player_1": idle_action}
        )

        first_attack = infos["player_0"]["reward_components"]
        self.assertEqual(first_attack["attack_generated"], 0)
        self.assertFalse(first_attack["all_clear_bonus_consumed"])
        self.assertTrue(game_0.all_clear_bonus_pending)
        self.assertEqual(env.player_states["player_0"].score_carry, 40)

        game_0.current_puyo_1 = Puyo(PuyoColor.BLUE)
        game_0.current_puyo_2 = Puyo(PuyoColor.BLUE)
        for y in (0, 1):
            game_0.field.place_puyo(1, y, Puyo(PuyoColor.BLUE))
        game_0.field.place_puyo(5, 0, Puyo(PuyoColor.RED))
        game_1.current_puyo_1 = Puyo(PuyoColor.PURPLE)
        game_1.current_puyo_2 = Puyo(PuyoColor.YELLOW)
        for x, y in ((0, 0), (0, 1), (1, 0), (1, 1)):
            game_1.field.place_puyo(x, y, Puyo(PuyoColor.RED))
        for x, y in ((3, 0), (3, 1), (4, 0), (4, 1)):
            game_1.field.place_puyo(x, y, Puyo(PuyoColor.BLUE))
        env.player_states["player_0"].pending_ojama = 5

        _, _, _, _, infos = env.step(
            {"player_0": attack_action, "player_1": idle_action}
        )

        attack = infos["player_0"]["reward_components"]
        turn_resolution = {
            "generated": attack["attack_generated"],
            "canceled": attack["attack_canceled"],
            "outgoing": attack["attack_outgoing"],
        }
        self.assertEqual(
            turn_resolution,
            {"generated": 31, "canceled": 8, "outgoing": 23},
        )
        self.assertEqual(attack["attack_score_delta"], 2140)
        self.assertTrue(attack["all_clear_bonus_consumed"])
        self.assertEqual(attack["all_clear_bonus_score"], 2100)
        self.assertEqual(env.player_states["player_0"].score_carry, 10)

        realtime = RealtimeVersusMatch(seed=123, attack_delay_ticks=100)
        realtime.player_states["player_0"].score_carry = 40
        realtime.schedule_attack("player_1", 5, delay_ticks=1000)
        realtime_generated = {
            "player_0": realtime._attack_units_from_score("player_0", 2140),
            "player_1": realtime._attack_units_from_score("player_1", 240),
        }
        realtime_resolution = realtime.resolve_generated_attacks(realtime_generated)

        self.assertEqual(realtime_resolution["player_0"], turn_resolution)
        self.assertEqual(realtime.player_states["player_0"].score_carry, 10)

        game_0.current_puyo_1 = Puyo(PuyoColor.GREEN)
        game_0.current_puyo_2 = Puyo(PuyoColor.GREEN)
        for y in (0, 1):
            game_0.field.place_puyo(1, y, Puyo(PuyoColor.GREEN))
        game_1.current_puyo_1 = Puyo(PuyoColor.PURPLE)
        game_1.current_puyo_2 = Puyo(PuyoColor.YELLOW)

        _, _, _, _, infos = env.step(
            {"player_0": attack_action, "player_1": idle_action}
        )

        third_attack = infos["player_0"]["reward_components"]
        self.assertEqual(third_attack["attack_generated"], 0)
        self.assertFalse(third_attack["all_clear_bonus_consumed"])
        self.assertEqual(third_attack["all_clear_bonus_score"], 0)

    def test_unplaced_ojama_stays_pending_without_false_game_over(self):
        env = VersusPuyoEnv(seed=123, max_steps=5)
        env.reset(seed=123)
        state = env.player_states["player_0"]
        for x in (0, 1, 3, 4, 5):
            for y in range(VISIBLE_HEIGHT):
                state.simulator.game.field.place_puyo(x, y, Puyo(PuyoColor.RED))
        state.pending_ojama = 30

        placed = env._apply_pending_ojama("player_0")

        self.assertEqual(placed, 5)
        self.assertEqual(state.pending_ojama, 25)
        self.assertFalse(state.simulator.game.game_over)
        self.assertTrue(
            state.simulator.game.field.get_puyo(2, VISIBLE_HEIGHT - 1).is_empty()
        )

    def test_ojama_at_choke_point_causes_game_over(self):
        env = VersusPuyoEnv(seed=123, max_steps=5)
        env.reset(seed=123)
        state = env.player_states["player_0"]
        for y in range(VISIBLE_HEIGHT - 1):
            state.simulator.game.field.place_puyo(2, y, Puyo(PuyoColor.BLUE))
        state.pending_ojama = 6

        env._apply_pending_ojama("player_0")

        self.assertTrue(state.simulator.game.game_over)

    def test_selfplay_wrapper_returns_single_agent_step(self):
        env = VersusSelfPlayEnv(seed=123, max_steps=2, opponent_policy=FirstLegalPolicy())
        _, info = env.reset(seed=123)

        action = int(numpy.flatnonzero(info["action_mask"])[0])
        _, _, terminated, truncated, next_info = env.step(action)

        self.assertFalse(terminated)
        self.assertFalse(truncated)
        self.assertIn("opponent_action", next_info)


if __name__ == "__main__":
    unittest.main()
