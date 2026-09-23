"""PUYO-239: terminal resolution across the match, environment and consumers."""

import copy
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from eval.realtime_arena import run_realtime_match
from eval.realtime_terminal_qa import prepare_boundary, replay_boundary
from puyo_env.realtime_ai import (
    RealtimePolicyController,
    RealtimePuyoEnv,
    build_realtime_info,
    build_realtime_observation,
)
from puyo_env.realtime_versus import REALTIME_AGENTS
from selfplay.policies import FirstLegalPolicy
from src.core.constants import Action
from src.core.realtime import TickInput


class TestTerminalResolution(unittest.TestCase):
    def make_env(self, **fixture):
        env = RealtimePuyoEnv(seed=239)
        env.reset()
        prepare_boundary(env.match, **fixture)
        return env

    def finish(self, env):
        results = []
        for _ in range(200):
            _, _, terminals, truncations, infos = env.step(
                {
                    agent: TickInput(press=(Action.START, Action.DOWN))
                    for agent in REALTIME_AGENTS
                }
            )
            results.append(infos)
            self.assertFalse(any(truncations.values()))
            if not env.agents:
                self.assertTrue(all(terminals.values()))
                return results
            self.assertFalse(any(terminals.values()))
            self.assertIsNone(infos["player_0"]["winner"])
        self.fail("terminal chain did not finish")

    def test_one_and_two_chain_finish_symmetrically_without_spawn_or_duplicate_reward(
        self,
    ):
        for survivor in REALTIME_AGENTS:
            for chains, score, units, carry in ((1, 40, 1, 39), (2, 360, 6, 9)):
                with self.subTest(survivor=survivor, chains=chains):
                    env = self.make_env(survivor=survivor, chains=chains)
                    loser = env.match._opponent(survivor)
                    queues = {
                        a: list(s.simulator.game.next_puyo_queue)
                        for a, s in env.player_states.items()
                    }
                    dead_before = env.player_states[
                        loser
                    ].simulator.game.field.to_color_grid()
                    with patch.object(
                        env.player_states[survivor].simulator.game,
                        "spawn_puyo",
                        side_effect=AssertionError("new pair"),
                    ):
                        rows = self.finish(env)
                    self.assertGreater(len(rows), 1)
                    state = env.player_states[survivor]
                    self.assertEqual(state.simulator.game.score, score)
                    self.assertEqual(state.simulator.game.chain_count, chains)
                    self.assertEqual(state.simulator.game.last_chain_score_delta, score)
                    self.assertEqual(
                        (state.generated_ojama_total, state.score_carry), (units, carry)
                    )
                    self.assertEqual(env.player_states[loser].pending_ojama, units)
                    self.assertEqual(env.player_states[loser].received_ojama_total, 0)
                    self.assertEqual(
                        env.player_states[loser].simulator.game.field.to_color_grid(),
                        dead_before,
                    )
                    self.assertEqual(rows[-1][survivor]["winner"], survivor)
                    self.assertEqual(rows[-1][survivor]["episode"]["max_chain"], chains)
                    components = [r[survivor]["reward_components"] for r in rows]
                    self.assertEqual(sum(c["score_delta"] for c in components), score)
                    self.assertAlmostEqual(
                        sum(c["score_reward"] for c in components), score / 70 * 0.25
                    )
                    self.assertEqual(
                        sum(c["attack_outgoing"] for c in components), units
                    )
                    self.assertEqual(sum(c["terminal_reward"] for c in components), 10)
                    self.assertEqual(
                        sum(
                            r[loser]["reward_components"]["terminal_reward"]
                            for r in rows
                        ),
                        -10,
                    )
                    self.assertEqual(
                        sum(
                            e.type == "resolution_complete"
                            for r in rows
                            for e in r[survivor]["match_result"]
                            .player_results[survivor]
                            .events
                        ),
                        1,
                    )
                    for agent, player in env.player_states.items():
                        self.assertEqual(
                            list(player.simulator.game.next_puyo_queue), queues[agent]
                        )
                        self.assertIsNone(player.simulator.game.current_puyo_1)
                    self.assertIsNone(
                        rows[-1][survivor]["simulator"].game.current_puyo_1
                    )
                    self.assertIsNone(
                        rows[-1][loser]["opponent_simulator"].game.current_puyo_1
                    )
                    self.assertTrue(env.match.finished)
                    self.assertFalse(env.match.resolution_pending)
                    with self.assertRaises(RuntimeError):
                        env.step()
                    repeat = env.match.step()
                    self.assertEqual(repeat.generated_attacks[survivor], 0)
                    self.assertEqual(state.generated_ojama_total, units)

    def test_pending_packets_cancel_then_notice_without_any_terminal_drop(self):
        for incoming, generated, outgoing, retained in ((2, 6, 4, 0), (9, 6, 0, 3)):
            with self.subTest(incoming=incoming):
                env = self.make_env(chains=2)
                env.match.schedule_attack("player_1", incoming, delay_ticks=0)
                env.match.schedule_attack("player_0", 3, delay_ticks=1000)
                rows = self.finish(env)
                state = env.player_states["player_0"]
                self.assertEqual(state.generated_ojama_total, generated)
                self.assertEqual(state.canceled_ojama_total, min(incoming, generated))
                self.assertEqual(state.sent_ojama_total, outgoing)
                self.assertEqual(state.pending_ojama, retained)
                self.assertEqual(
                    env.player_states["player_1"].pending_ojama, 3 + outgoing
                )
                self.assertTrue(
                    all(
                        not any(r["player_0"]["match_result"].dropped_ojama.values())
                        for r in rows
                    )
                )

    def test_all_clear_bonus_and_soft_drop_score_are_counted_in_final_attack(self):
        env = self.make_env(chains=1)
        game = env.player_states["player_0"].simulator.game
        game.score = 13
        game.all_clear_bonus_pending = True
        rows = self.finish(env)
        attack = rows[-1]["player_0"]["match_result"].attack_diagnostics["player_0"]
        self.assertEqual(game.score, 2153)
        self.assertEqual(attack["attack_score_delta"], 2153)
        self.assertEqual(attack["all_clear_bonus_score"], 2100)
        self.assertEqual(attack["generated"], 31)
        self.assertEqual(env.player_states["player_0"].score_carry, 52)

    def test_no_chain_freezes_control_immediately(self):
        env = RealtimePuyoEnv(seed=239)
        env.reset()
        dead = env.player_states["player_1"].simulator.game
        dead.game_over, dead.state = True, "gameover"
        survivor = env.player_states["player_0"].simulator
        before = survivor.snapshot()
        rows = self.finish(env)
        after = survivor.snapshot()
        self.assertEqual(len(rows), 1)
        self.assertEqual(before.active_pair, after.active_pair)
        self.assertEqual(before.active_position, after.active_position)
        self.assertEqual(before.score, after.score)
        self.assertEqual(after.held_actions, ())

    def test_first_clear_after_gravity_is_still_pending(self):
        from src.core.constants import PuyoColor
        from src.core.field import Field
        from src.core.puyo import Puyo

        env = self.make_env(chains=1)
        game = env.player_states["player_0"].simulator.game
        game.field = Field()
        for x, y in ((0, 0), (0, 4), (1, 0), (1, 6)):
            game.field.place_puyo(x, y, Puyo(PuyoColor.RED))
        self.assertFalse(game.field.get_vanish_groups())
        self.assertTrue(env.match.resolution_pending)
        self.finish(env)
        self.assertEqual(game.score, 40)
        self.assertEqual(env.player_states["player_1"].pending_ojama, 1)

    def test_actual_garbage_topout_during_opponent_chain_waits_for_notice(self):
        from src.core.puyo import Puyo
        from src.core.constants import PuyoColor

        for survivor in REALTIME_AGENTS:
            with self.subTest(survivor=survivor):
                env = self.make_env(survivor=survivor, detected=False)
                loser = env.match._opponent(survivor)
                env.player_states[loser].simulator.game.field.place_puyo(
                    2, 11, Puyo(PuyoColor.EMPTY)
                )
                env.match.schedule_attack(survivor, 6, delay_ticks=0)
                env.step()
                self.assertTrue(env.match.resolution_pending)
                self.assertTrue(env.player_states[loser].simulator.game.game_over)
                self.assertEqual(env.player_states[loser].received_ojama_total, 6)
                self.finish(env)
                self.assertEqual(env.player_states[loser].received_ojama_total, 6)
                self.assertEqual(env.player_states[loser].pending_ojama, 6)

    def test_zero_chain_drop_does_not_delay_topout(self):
        from src.core.constants import PuyoColor
        from src.core.puyo import Puyo

        env = self.make_env(detected=False)
        game = env.player_states["player_0"].simulator.game
        from src.core.field import Field

        game.field = Field()
        game.field.place_puyo(0, 5, Puyo(PuyoColor.RED))
        _, _, terminated, _, _ = env.step()
        self.assertTrue(all(terminated.values()))
        self.assertEqual(game.animation_state, "drop_tween")
        self.assertTrue(env.match.finished)
        self.assertEqual(game.score, 0)

    def test_simultaneous_topout_retains_score_tiebreak_and_draw(self):
        for scores, winner in (
            ((0, 0), None),
            ((40, 0), "player_0"),
            ((0, 40), "player_1"),
        ):
            with self.subTest(scores=scores):
                env = RealtimePuyoEnv(seed=239)
                env.reset()
                for agent, score in zip(REALTIME_AGENTS, scores):
                    game = env.player_states[agent].simulator.game
                    game.game_over, game.state, game.score = True, "gameover", score
                rows = self.finish(env)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[-1]["player_0"]["winner"], winner)

    def test_same_tick_topout_defers_other_spawn_and_keeps_running_chain(self):
        for survivor in REALTIME_AGENTS:
            with self.subTest(survivor=survivor):
                env = self.make_env(survivor=survivor, detected=False)
                env.step()
                self.assertTrue(env.match.resolution_pending)
                self.assertTrue(env.agents)
                self.assertEqual(
                    env.player_states[survivor].simulator.game.state, "animate"
                )
                self.finish(env)
                self.assertIsNone(
                    env.player_states[survivor].simulator.game.current_puyo_1
                )

    def test_same_tick_resolution_and_garbage_topout_never_spawn_next_pair(self):
        for loser in REALTIME_AGENTS:
            with self.subTest(loser=loser):
                env = RealtimePuyoEnv(seed=239)
                env.reset()
                for state in env.player_states.values():
                    game = state.simulator.game
                    game.current_puyo_1 = game.current_puyo_2 = None
                    game.state = "animate"
                    game._begin_chain_resolution()
                # Filling all 12 visible rows deterministically top-outs at drop boundary.
                env.match.max_ojama_drop = 72
                env.match.schedule_attack(env.match._opponent(loser), 72, delay_ticks=0)
                _, _, terminal, _, _ = env.step()
                self.assertTrue(all(terminal.values()))
                self.assertEqual(env.player_states[loser].received_ojama_total, 72)
                for state in env.player_states.values():
                    self.assertIsNone(state.simulator.game.current_puyo_1)

    def test_both_actual_garbage_topouts_share_one_draw_boundary(self):
        env = RealtimePuyoEnv(seed=239)
        env.reset()
        env.match.max_ojama_drop = 72
        for agent, state in env.player_states.items():
            game = state.simulator.game
            game.current_puyo_1 = game.current_puyo_2 = None
            game.state = "animate"
            game._begin_chain_resolution()
            env.match.schedule_attack(agent, 72, delay_ticks=0)
        _, _, terminated, _, infos = env.step()
        self.assertTrue(all(terminated.values()))
        self.assertIsNone(infos["player_0"]["winner"])
        for state in env.player_states.values():
            self.assertTrue(state.simulator.game.game_over)
            self.assertEqual(state.received_ojama_total, 72)
            self.assertIsNone(state.simulator.game.current_puyo_1)

    def test_controller_starts_no_policy_or_active_plan_input_after_topout(self):
        env = self.make_env()
        controller = RealtimePolicyController(FirstLegalPolicy())
        controller._active_plan = object()  # A stale plan must not emit another input.
        with patch.object(
            controller.policy, "select_action", side_effect=AssertionError("new search")
        ):
            self.assertEqual(controller.next_input(env.match, "player_0"), TickInput())
        self.assertEqual(controller.diagnostics.decisions_started, 0)
        self.assertTrue(
            build_realtime_info(env.match, "player_0")["resolution_pending"]
        )

    def test_terminal_masks_do_not_run_reachability_search(self):
        env = self.make_env()
        with patch(
            "puyo_env.realtime_ai.realtime_reachable_action_mask",
            side_effect=AssertionError("new search"),
        ):
            for agent in REALTIME_AGENTS:
                info = build_realtime_info(
                    env.match, agent, use_reachable_action_mask=True
                )
                obs = build_realtime_observation(
                    env.match, agent, include_action_mask=True
                )
                self.assertFalse(any(info["action_mask"]))
                self.assertFalse(any(obs["action_mask"]))

    def test_headless_default_spawns_but_snapshot_can_preserve_ready(self):
        from src.core.headless import HeadlessPuyoSimulator

        self.assertIsNotNone(HeadlessPuyoSimulator(seed=239).game.current_puyo_1)
        self.assertIsNone(
            HeadlessPuyoSimulator(seed=239, auto_spawn=False).game.current_puyo_1
        )

    def test_clone_resume_during_pending_chain_matches_uninterrupted_hash(self):
        env = self.make_env()
        env.step()
        clone = copy.deepcopy(env)
        self.finish(env)
        self.finish(clone)
        self.assertEqual(env.match.state_hash(), clone.match.state_hash())

    def test_timeout_remains_explicit_truncation_during_pending_resolution(self):
        env = self.make_env()
        env.max_ticks = 1
        _, _, terminated, truncated, infos = env.step()
        self.assertFalse(any(terminated.values()))
        self.assertTrue(all(truncated.values()))
        self.assertTrue(env.match.resolution_pending)
        self.assertFalse(env.agents)
        self.assertIn("episode", infos["player_0"])

    def test_arena_and_replay_share_terminal_score_attack_and_hash(self):
        fixture = dict(survivor="player_0", chains=2, detected=True)
        original_reset = RealtimePuyoEnv.reset

        def reset(env, **kwargs):
            original_reset(env, **kwargs)
            prepare_boundary(env.match, **fixture)
            return env._observations_and_infos()

        with (
            patch.object(RealtimePuyoEnv, "reset", reset),
            patch.object(
                RealtimePolicyController,
                "next_input",
                side_effect=AssertionError("controller called"),
            ),
        ):
            result = run_realtime_match(
                FirstLegalPolicy(), FirstLegalPolicy(), seed=239, record_replay=True
            )
        self.assertEqual(result.score_player_0, 360)
        self.assertEqual(result.sent_ojama_player_0, 6)
        self.assertEqual(result.max_chain_player_0, 2)
        self.assertEqual(result.winner, "player_0")
        payload = {"fixture": fixture, "replay": result.replay}
        self.assertEqual(replay_boundary(payload), result.final_hash)
        changed = copy.deepcopy(payload)
        changed["replay"]["ticks"][-1]["attack_diagnostics"]["player_0"][
            "outgoing"
        ] += 1
        with self.assertRaisesRegex(AssertionError, "attack diagnostics mismatch"):
            replay_boundary(changed)


class TestTerminalPresentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pygame

        pygame.init()

    @classmethod
    def tearDownClass(cls):
        import pygame

        pygame.quit()

    def make_controller(self):
        from eval.realtime_versus_ui import (
            RealtimeVersusMatchController,
            RealtimeVersusUiConfig,
        )

        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(
                policy_a="first",
                policy_b="first",
                seed=239,
            )
        )
        self.addCleanup(controller.shutdown)
        prepare_boundary(controller.env.match)
        controller.observations, controller.infos = (
            controller.env._observations_and_infos()
        )
        controller._sync_display_boards()
        return controller

    def test_pause_step_speed_drain_terminal_queue_without_new_ticks_or_controllers(
        self,
    ):
        import pygame
        from src.ui.versus_renderer import SCREEN_HEIGHT, SCREEN_WIDTH, VersusRenderer

        for speed in (0.5, 1.0, 4.0):
            with self.subTest(speed=speed):
                controller = self.make_controller()
                controller.speed = speed
                controller.paused = True
                first_hash = controller.env.match.state_hash()
                controller.update(1)
                self.assertEqual(controller.env.match.state_hash(), first_hash)
                renderer = VersusRenderer(
                    pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
                )
                with patch.object(
                    renderer, "_draw_text", wraps=renderer._draw_text
                ) as draw_text:
                    renderer.draw(controller)
                    self.assertNotIn(
                        "GAME OVER", [call.args[0] for call in draw_text.call_args_list]
                    )
                with patch.object(
                    RealtimePolicyController,
                    "next_input",
                    side_effect=AssertionError("new controller input"),
                ):
                    while controller.env.agents:
                        controller.handle_keydown(pygame.K_n)
                tick = controller.env.match.tick
                final_hash = controller.env.match.state_hash()
                self.assertFalse(controller.presentation_finished)
                self.assertEqual(controller.infos["player_1"]["pending_ojama"], 6)
                with patch.object(
                    renderer, "_draw_text", wraps=renderer._draw_text
                ) as draw_text:
                    renderer.draw(controller)
                    self.assertNotIn(
                        "GAME OVER", [call.args[0] for call in draw_text.call_args_list]
                    )
                for _ in range(100):
                    if controller.presentation_finished:
                        break
                    controller.handle_keydown(pygame.K_n)
                self.assertTrue(controller.presentation_finished)
                self.assertEqual(controller.env.match.tick, tick)
                self.assertEqual(controller.env.match.state_hash(), final_hash)
                self.assertFalse(controller.advance_one())
                with patch.object(
                    renderer, "_draw_text", wraps=renderer._draw_text
                ) as draw_text:
                    renderer.draw(controller)
                    labels = [call.args[0] for call in draw_text.call_args_list]
                    self.assertIn("GAME OVER", labels)
                    self.assertIn("PLAYER 1 WINS", labels)
                qa = controller.qa_result(collection_manifest=None, interrupted=False)
                self.assertEqual(qa["result"]["termination_reason"], "game_over")
                self.assertEqual(qa["result"]["scores"]["player_0"], 360)

    def test_manual_exit_during_pending_chain_is_interrupted(self):
        controller = self.make_controller()
        controller.advance_one()
        qa = controller.qa_result(collection_manifest=None, interrupted=True)
        self.assertEqual(qa["result"]["termination_reason"], "interrupted")
        self.assertFalse(qa["result"]["completed"])
        self.assertIsNone(qa["result"]["winner"])


if __name__ == "__main__":
    unittest.main()
