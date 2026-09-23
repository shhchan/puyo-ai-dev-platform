import json
import multiprocessing
import os
import tempfile
import time
import unittest
from collections import deque
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

try:
    import gymnasium  # noqa: F401
    import numpy  # noqa: F401

    from eval.realtime_versus_ui import (
        ASYNC_POLICY_TYPES,
        RealtimeVersusMatchController,
        RealtimeVersusUiConfig,
        parse_config,
        run_ui,
    )
    from eval.versus_ui import VisualEvent
    from selfplay.policies import legal_indices
    from src.core.constants import Action, PUYO_SIZE, PuyoColor
    from src.ui.versus_renderer import (
        ACTIVE_GHOST_SCALE,
        VISIBLE_HEIGHT,
        VersusRenderer,
        animation_progress,
        garbage_animation_cells,
        live_active_pair_cells,
        plan_step_delta_cells,
        plan_step_placement_cells,
        settle_scale,
    )

    ENV_AVAILABLE = True
except (ImportError, OSError):
    ENV_AVAILABLE = False
    RealtimeVersusMatchController = None
    RealtimeVersusUiConfig = None
    parse_config = None
    run_ui = None
    legal_indices = None
    PuyoColor = None
    Action = None
    ACTIVE_GHOST_SCALE = None
    VersusRenderer = None
    animation_progress = None
    garbage_animation_cells = None
    live_active_pair_cells = None
    plan_step_delta_cells = None
    plan_step_placement_cells = None
    ASYNC_POLICY_TYPES = frozenset()


class BlockingTestPolicy:
    def __init__(self, release):
        self.release = release

    def select_action(self, observation, info):
        self.release.wait(1.0)
        return legal_indices(info)[0]


class FastTestPolicy:
    def select_action(self, observation, info):
        return legal_indices(info)[0]

try:
    import pygame

    PYGAME_AVAILABLE = ENV_AVAILABLE
except (ImportError, OSError):
    PYGAME_AVAILABLE = False


@unittest.skipUnless(ENV_AVAILABLE, "gymnasium/numpy are not installed")
class TestRealtimeVersusUiConfig(unittest.TestCase):
    def test_tick_limit_is_disabled_by_default(self):
        config = parse_config([])

        self.assertIsNone(config.max_ticks)
        self.assertIsNone(config.max_steps)

    def test_realtime_policy_options_are_parsed(self):
        config = parse_config(
            [
                "--policy-a",
                "first",
                "--policy-b",
                "beam",
                "--seed",
                "54",
                "--max-ticks",
                "120",
                "--inference-latency-ticks",
                "2",
                "--latency-mode",
                "configured",
                "--qa-profile",
                "deterministic",
                "--timeout-ticks",
                "4",
                "--use-reachable-action-mask",
            ]
        )

        self.assertEqual(config.policy_a, "first")
        self.assertEqual(config.policy_b, "beam")
        self.assertEqual(config.max_ticks, 120)
        self.assertEqual(config.max_steps, 120)
        self.assertEqual(config.inference_latency_ticks, 2)
        self.assertEqual(config.latency_mode, "configured")
        self.assertEqual(config.qa_profile, "deterministic")
        self.assertTrue(config.use_reachable_action_mask)
        self.assertTrue(config.plan_overlay)

    def test_v1_7_policy_and_qa_artifact_options_are_parsed(self):
        config = parse_config(
            [
                "--policy-a",
                "v1_7_analyzer_manager",
                "--policy-b",
                "manager_rule",
                "--result-json",
                "/tmp/result.json",
                "--replay",
                "/tmp/replay.json",
                "--qa-notes",
                "reviewed",
            ]
        )

        self.assertEqual(config.policy_a, "v1_7_analyzer_manager")
        self.assertIsNone(config.checkpoint_a)
        self.assertEqual(config.replay_path, "/tmp/replay.json")
        self.assertEqual(config.qa_notes, "reviewed")

    def test_deep_chain_builder_is_a_realtime_policy_and_async(self):
        config = parse_config(
            [
                "--policy-a",
                "deep_chain_builder",
                "--deep-chain-profile",
                "smoke",
                "--deep-chain-backend",
                "native",
                "--deep-chain-target-chain",
                "10",
            ]
        )

        self.assertEqual(config.policy_a, "deep_chain_builder")
        self.assertEqual(config.deep_chain_profile, "smoke")
        self.assertEqual(config.deep_chain_backend, "native")
        self.assertEqual(config.deep_chain_target_chain, 10)
        self.assertIn("deep_chain_builder", ASYNC_POLICY_TYPES)

    def test_terminal_frame_auto_exit_is_parsed_and_validated(self):
        config = parse_config(["--exit-after-finish-frames", "30"])

        self.assertEqual(config.exit_after_finish_frames, 30)
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parse_config(["--exit-after-finish-frames", "0"])

    def test_v1_7_bootstrap_checkpoint_policy_is_async_and_requires_a_path(self):
        config = parse_config(
            [
                "--policy-a",
                "v1_7_bootstrap_manager",
                "--checkpoint-a",
                "bootstrap.pt",
            ]
        )

        self.assertEqual(config.policy_a, "v1_7_bootstrap_manager")
        self.assertEqual(config.checkpoint_a, "bootstrap.pt")
        self.assertIn("v1_7_bootstrap_manager", ASYNC_POLICY_TYPES)
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parse_config(["--policy-a", "v1_7_bootstrap_manager"])

    def test_plan_overlay_can_be_disabled_from_cli(self):
        config = parse_config(["--no-plan-overlay"])

        self.assertFalse(config.plan_overlay)

    def test_collection_requires_a_human_policy(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parse_config(["--policy-a", "first", "--policy-b", "random", "--collect-human-data"])

    def test_checkpoint_path_is_required(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parse_config(["--policy-a", "checkpoint"])


@unittest.skipUnless(PYGAME_AVAILABLE, "pygame is not installed")
class TestRealtimeVersusMatchController(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def test_controller_advances_realtime_ticks_and_exposes_diagnostics(self):
        class StubPolicy:
            def select_action(self, observation, info):
                return legal_indices(info)[0]

        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(
                policy_a="first",
                policy_b="first",
                seed=54,
                max_ticks=80,
                start_paused=True,
            ),
            policy_factory=lambda policy_type, **kwargs: StubPolicy(),
        )

        for _ in range(16):
            controller.advance_tick()

        self.assertEqual(controller.env.match.tick, 16)
        self.assertGreater(controller.controllers["player_0"].diagnostics.decisions_started, 0)
        self.assertGreater(controller.controllers["player_0"].diagnostics.emitted_input_ticks, 0)
        self.assertTrue(live_active_pair_cells(controller.env.player_states["player_0"].simulator.game))
        diagnostics = controller.realtime_diagnostics("player_0")
        self.assertIn("input", diagnostics)
        self.assertIn("plan", diagnostics)

    def test_deep_chain_placements_replace_ghosts_and_overlay_toggle_preserves_decision(self):
        from agents.deep_chain_builder import DeepChainBuilderPolicy

        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(
                policy_a="first", policy_b="random", seed=187,
                max_ticks=600, replay_path="unused-replay.json",
            ),
            policy_factory=lambda policy_type, **kwargs: (
                DeepChainBuilderPolicy(profile="smoke", backend="python")
                if policy_type == "first" else FastTestPolicy()
            ),
        )
        self.addCleanup(controller.shutdown)
        applied_plans = []
        for _ in range(600):
            controller.advance_tick()
            diagnostics = controller.controllers["player_0"].diagnostics
            if diagnostics.decisions_activated <= len(applied_plans):
                continue
            policy = controller.tactical_diagnostics("player_0")
            decision = diagnostics.last_decision
            self.assertEqual(decision.decision_input, policy["decision_input"])
            self.assertEqual(decision.policy_decision_id, policy["decision_trace"]["decision_id"])
            self.assertEqual(decision.action_index, policy["plan"]["steps"][0]["action"])
            self.assertNotIn(policy["plan_id"], applied_plans)
            applied_plans.append(policy["plan_id"])
            self.assertEqual(controller.plan_overlay("player_0"), policy["plan"])
            counters = diagnostics.to_dict()
            controller.handle_keydown(pygame.K_o)
            self.assertEqual(controller.plan_overlay("player_0"), {})
            self.assertEqual(controller.tactical_diagnostics("player_0"), policy)
            controller.handle_keydown(pygame.K_o)
            self.assertEqual(controller.plan_overlay("player_0"), policy["plan"])
            self.assertEqual(controller.tactical_diagnostics("player_0"), policy)
            self.assertEqual(diagnostics.to_dict(), counters)
            if len(applied_plans) == 3:
                break
        self.assertEqual(len(applied_plans), 3)
        self.assertEqual(diagnostics.replans, 0)
        replay = controller.replay_payload()
        self.assertEqual(replay["policy_decision_schema_version"], "puyo.realtime_policy_decisions.v1")
        recorded_ids = {
            tick["policy_diagnostics"]["player_0"].get("plan_id")
            for tick in replay["ticks"] if "player_0" in tick["policy_diagnostics"]
        }
        self.assertTrue(set(applied_plans).issubset(recorded_ids))

    def test_controller_exposes_policy_plan_overlay_when_enabled(self):
        plan = {
            "schema_version": "n-turn-plan-v1",
            "plan_id": "plan-123",
            "update_reason": "policy_decision",
            "objective": {"reason": "attack"},
            "steps": [],
        }

        class StubPolicy:
            tactical_diagnostics = {"plan": plan, "plan_id": "plan-123"}

            def select_action(self, observation, info):
                return legal_indices(info)[0]

        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(
                policy_a="first",
                policy_b="first",
                seed=54,
                max_ticks=80,
                start_paused=True,
            ),
            policy_factory=lambda policy_type, **kwargs: StubPolicy(),
        )

        self.assertEqual(controller.plan_overlay("player_0")["plan_id"], "plan-123")
        controller.plan_overlay_enabled["player_0"] = False
        self.assertEqual(controller.plan_overlay("player_0"), {})

    def test_deep_chain_summary_uses_selected_search_evidence_and_trace_steps(self):
        class StubPolicy:
            tactical_diagnostics = {
                "policy_id": "deep_chain_builder",
                "target_chain_count": 10,
                "profile": {"name": "smoke", "depth": 4, "width": 8},
                "selected_action": 7,
                "candidate_count": 22,
                "selection_reason": "highest_aggregated_root_ranking",
                "scenario_aggregation": [
                    {
                        "root_action": 3,
                        "evidence": {"chain_count": {"maximum": 12}},
                    },
                    {
                        "root_action": 7,
                        "evidence": {"chain_count": {"maximum": 9}},
                    },
                ],
                "search": {
                    "scenario_ids": ["scenario-0", "scenario-1"],
                    "counters": {"expanded_nodes": 1020},
                },
                "backend": {
                    "requested_backend": "native",
                    "backend": "native",
                    "fallback": {"used": False},
                },
                "decision_trace": {
                    "step_count": 2,
                    "elapsed_seconds": 1.25,
                    "steps": [
                        {
                            "step_id": "normalize_observation",
                            "elapsed_seconds": 0.01,
                        },
                        {
                            "step_id": "run_long_range_search",
                            "elapsed_seconds": 1.24,
                        },
                    ],
                },
                "plan_id": "plan-12345678",
                "replan_reason": "new_observation",
                "plan": {
                    "prediction_summary": {"maximum_chain_count": 3},
                    "steps": [{"predicted_chain_count": 3}],
                },
            }

            def select_action(self, observation, info):
                return legal_indices(info)[0]

        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(policy_a="first", policy_b="first"),
            policy_factory=lambda policy_type, **kwargs: StubPolicy(),
        )

        summary = controller.tactical_summary("player_0")

        self.assertEqual(summary["profile_name"], "smoke")
        self.assertEqual(summary["scenario_count"], 2)
        self.assertEqual(summary["max_chain"], 9)
        self.assertEqual(summary["target_chain"], 10)
        self.assertEqual(summary["expanded_nodes"], 1020)
        self.assertEqual(summary["backend_id"], "native")
        self.assertEqual(summary["backend_requested"], "native")
        self.assertFalse(summary["backend_fallback"])
        self.assertEqual(summary["flow_step_count"], 2)
        self.assertEqual(summary["flow_steps"][1]["step_id"], "run_long_range_search")
        self.assertEqual(summary["flow_elapsed_seconds"], 1.25)
        controller.shutdown()

    def test_replay_and_qa_result_share_runtime_lifecycle_and_attack_diagnostics(self):
        class StubPolicy:
            tactical_diagnostics = {
                "model_metadata": {
                    "model_family": "Adaptive Chain Manager",
                    "model_version": "v1.7.0",
                    "lineage_node_id": "model_version:v1.7.0",
                },
                "selected_tactic": {
                    "tactic_id": "build_main",
                    "reason_code": "safe_build",
                },
                "analyzer": {
                    "diagnostics": {
                        "own": {"danger": 0.1, "forecast": {"short_attack": 2}},
                        "opponent": {"danger": 0.2, "forecast": {"short_attack": 3}},
                        "incoming": {"amount": 4, "can_cancel": True},
                    }
                },
                "planner_request": {
                    "objective": {"kind": "build", "target_chain": 6}
                },
                "worker": {
                    "result": {
                        "predicted_chain_count": 2,
                        "predicted_attack": 3,
                        "danger": 0.1,
                    }
                },
                "target_attack": 3,
                "reason": "safe build",
                "reason_code": "safe_build",
                "plan_id": "plan-1",
                "plan": {},
            }

            def select_action(self, observation, info):
                return legal_indices(info)[0]

        with tempfile.TemporaryDirectory() as directory:
            controller = RealtimeVersusMatchController(
                RealtimeVersusUiConfig(
                    policy_a="first",
                    policy_b="first",
                    max_ticks=80,
                    replay_path=str(Path(directory) / "replay.json"),
                    qa_notes="reviewed",
                ),
                policy_factory=lambda policy_type, **kwargs: StubPolicy(),
            )
            controller.advance_tick()
            player_0 = controller.env.player_states["player_0"]
            player_1 = controller.env.player_states["player_1"]
            player_0.simulator.game.all_clear_achieved = True
            player_0.simulator.game.all_clear_bonus_pending = True
            player_0.score_carry = 69
            player_1.simulator.game.all_clear_bonus_consumed = True
            controller.advance_tick()

            tick = controller.replay_ticks[-1]
            all_clear = tick["all_clear_diagnostics"]["players"]
            self.assertTrue(all_clear["player_0"]["all_clear_achieved"])
            self.assertTrue(all_clear["player_0"]["all_clear_bonus_pending"])
            self.assertTrue(all_clear["player_1"]["all_clear_bonus_consumed"])
            self.assertEqual(tick["attack_diagnostics"]["player_0"]["score_carry"], 69)
            self.assertIn("controller_diagnostics", tick)
            diagnostic_tick = next(
                item
                for item in controller.replay_ticks
                if "player_0" in item["policy_diagnostics"]
            )
            self.assertEqual(
                diagnostic_tick["policy_diagnostics"]["player_0"]["selected_tactic"][
                    "tactic_id"
                ],
                "build_main",
            )

            summary = controller.tactical_summary("player_0")
            self.assertEqual(summary["tactic_id"], "build_main")
            self.assertEqual(summary["own_short_attack"], 2)
            qa = controller.qa_result(collection_manifest=None, interrupted=True)
            self.assertEqual(qa["schema_version"], "puyo.gui_qa.v1")
            self.assertEqual(qa["notes"], "reviewed")
            self.assertEqual(
                qa["models"]["player_0"]["model_family"],
                "Adaptive Chain Manager",
            )
            self.assertTrue(
                qa["diagnostics"]["lifecycle_coverage"]["asymmetric_achieved"]
            )
            self.assertTrue(
                qa["diagnostics"]["lifecycle_coverage"]["players"]["player_1"][
                    "consumed"
                ]
            )
            controller.shutdown()

    def test_gui_qa_gate_does_not_pass_on_tick_limit_and_zero_attack(self):
        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(
                policy_a="first",
                policy_b="random",
                max_ticks=1,
                qa_profile="attack",
            )
        )
        controller.advance_tick()

        qa = controller.qa_result(collection_manifest=None, interrupted=False)

        self.assertTrue(qa["result"]["execution_completed"])
        self.assertFalse(qa["result"]["completed"])
        self.assertFalse(qa["quality_gate"]["passed"])
        codes = {
            failure["code"]
            for failure in qa["quality_gate"]["failure_reasons"]
        }
        self.assertIn("attack_not_generated", codes)
        self.assertIn("minimum_placements_not_met", codes)
        controller.shutdown()

    def test_ojama_animation_uses_pre_board_and_actual_landing_cells(self):
        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(
                policy_a="first",
                policy_b="random",
                max_ticks=100,
            )
        )
        controller.env.match.schedule_attack("player_0", 3, delay_ticks=0)

        controller.advance_tick()

        self.assertIsNone(controller.current_events["player_1"])
        self.assertEqual(controller.env.player_states["player_1"].pending_ojama, 3)
        for _ in range(99):
            controller._advance_visual_events(1.0)
            controller.advance_tick()
            event = controller.current_events["player_1"]
            if event is not None and event.kind == "garbage":
                break
        else:
            self.fail("ojama animation did not start at a placement boundary")

        self.assertEqual(event.kind, "garbage")
        self.assertEqual(len(event.coords), 3)
        self.assertEqual(event.amount, len(event.coords))
        for x, y in event.coords:
            self.assertNotEqual(controller.display_boards["player_1"][y][x], PuyoColor.OJAMA)
            self.assertEqual(controller._current_board("player_1")[y][x], PuyoColor.OJAMA)

        pre_cells = garbage_animation_cells(event, 0.0)
        mid_cells = garbage_animation_cells(event, 0.5)
        post_cells = garbage_animation_cells(event, 1.0)
        targets = tuple(sorted(event.coords, key=lambda coord: (coord[1], coord[0])))
        self.assertEqual(tuple((x, int(y)) for x, y in post_cells), targets)
        for (_, pre_y), (_, mid_y), (_, post_y) in zip(pre_cells, mid_cells, post_cells):
            self.assertGreater(pre_y, mid_y)
            self.assertGreater(mid_y, post_y)

        state_hash = controller.env.match.state_hash()
        controller._advance_visual_events(0.175)
        self.assertEqual(controller.env.match.state_hash(), state_hash)
        controller._advance_visual_events(0.2)
        self.assertEqual(controller.env.match.state_hash(), state_hash)
        for x, y in event.coords:
            self.assertEqual(controller.display_boards["player_1"][y][x], PuyoColor.OJAMA)
        controller.shutdown()

    def test_queued_ojama_stays_hidden_until_one_fall_then_survives_next_placement(self):
        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(policy_a="first", policy_b="random", max_ticks=200)
        )
        renderer = VersusRenderer(pygame.Surface((6 * PUYO_SIZE, VISIBLE_HEIGHT * PUYO_SIZE)))
        field = pygame.Rect(0, 0, 6 * PUYO_SIZE, VISIBLE_HEIGHT * PUYO_SIZE)
        controller.env.match.schedule_attack("player_0", 3, delay_ticks=0)
        agent = "player_1"
        queued_garbage = None
        snapshots = {}
        garbage_starts = 0
        previous_event = None
        try:
            for _ in range(350):
                controller.update(1 / 60)
                event = controller.visual_event(agent)
                elapsed = controller.visual_event_elapsed(agent)
                board = controller.display_boards[agent]
                if queued_garbage is None:
                    queued_garbage = next(
                        (item for item in controller.event_queues[agent] if item.kind == "garbage"),
                        None,
                    )
                    if queued_garbage is not None:
                        snapshots["pre"] = (event, board, elapsed)
                        self.assertEqual(
                            sum(cell == PuyoColor.OJAMA for row in controller._current_board(agent) for cell in row),
                            3,
                        )
                        self.assertTrue(all(board[y][x] != PuyoColor.OJAMA for x, y in queued_garbage.coords))
                if event is queued_garbage and event is not None:
                    if previous_event is not event:
                        garbage_starts += 1
                        snapshots["fall_pre"] = (event, board, elapsed)
                    if 0.15 <= elapsed <= 0.20 and "fall_mid" not in snapshots:
                        snapshots["fall_mid"] = (event, board, elapsed)
                elif "fall_pre" in snapshots and "post" not in snapshots:
                    snapshots["post"] = (event, board, elapsed)
                if "post" in snapshots and event is not None and event.kind == "placement":
                    if "next_pre" not in snapshots:
                        snapshots["next_pre"] = (event, board, elapsed)
                    elif 0.08 <= elapsed <= 0.12 and "next_mid" not in snapshots:
                        snapshots["next_mid"] = (event, board, elapsed)
                elif "next_pre" in snapshots and "next_post" not in snapshots:
                    snapshots["next_post"] = (event, board, elapsed)
                previous_event = event
                if "next_post" in snapshots:
                    break

            self.assertEqual(garbage_starts, 1)
            self.assertEqual(
                set(snapshots),
                {"pre", "fall_pre", "fall_mid", "post", "next_pre", "next_mid", "next_post"},
            )
            x, y = min(queued_garbage.coords)
            pixel = (x * PUYO_SIZE + PUYO_SIZE // 2, (VISIBLE_HEIGHT - y - 1) * PUYO_SIZE + PUYO_SIZE // 2)
            for phase, (event, board, elapsed) in snapshots.items():
                with self.subTest(phase=phase):
                    renderer._draw_board(field, event, board, event_elapsed=elapsed)
                    color = renderer.screen.get_at(pixel)[:3]
                    if phase in {"pre", "fall_pre", "fall_mid"}:
                        self.assertNotEqual(color, renderer.colors[PuyoColor.OJAMA])
                        self.assertNotEqual(board[y][x], PuyoColor.OJAMA)
                    else:
                        self.assertEqual(color, renderer.colors[PuyoColor.OJAMA])
                        self.assertEqual(board[y][x], PuyoColor.OJAMA)
            self.assertFalse(any(item is queued_garbage for item in controller.event_queues[agent]))
        finally:
            controller.shutdown()

    def test_pending_ojama_masks_only_its_own_cells_and_handles_multiple_drops(self):
        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(policy_a="first", policy_b="random", max_ticks=10)
        )
        agent = "player_1"
        original = controller._current_board(agent)
        rows = [list(row) for row in original]
        rows[0][0] = PuyoColor.OJAMA
        rows[0][1] = PuyoColor.OJAMA
        rows[0][2] = PuyoColor.RED
        authoritative = tuple(tuple(row) for row in rows)
        first_pre_rows = [[PuyoColor.EMPTY for _ in row] for row in original]
        first_pre_rows[0][0] = PuyoColor.RED
        first_pre = tuple(tuple(row) for row in first_pre_rows)
        second_pre = [list(row) for row in first_pre]
        second_pre[0][0] = PuyoColor.OJAMA
        second_pre[0][1] = PuyoColor.BLUE
        first = VisualEvent("garbage", agent, "OJAMA +1", amount=1, coords=frozenset({(0, 0)}), board=first_pre)
        second = VisualEvent("garbage", agent, "OJAMA +1", amount=1, coords=frozenset({(1, 0)}), board=tuple(tuple(row) for row in second_pre))
        try:
            controller.current_events[agent] = first
            controller.event_queues[agent] = deque([second])
            with patch.object(controller, "_current_board", return_value=authoritative):
                controller._sync_display_boards()
            self.assertEqual(controller.display_boards[agent][0][:3], (PuyoColor.EMPTY, PuyoColor.EMPTY, PuyoColor.RED))

            controller.current_events[agent] = second
            controller.event_queues[agent].clear()
            with patch.object(controller, "_current_board", return_value=authoritative):
                controller._sync_display_boards()
            self.assertEqual(controller.display_boards[agent][0][:3], (PuyoColor.OJAMA, PuyoColor.EMPTY, PuyoColor.RED))

            rows[0][1] = PuyoColor.BLUE
            with patch.object(controller, "_current_board", return_value=tuple(tuple(row) for row in rows)):
                controller._sync_display_boards()
            self.assertEqual(controller.display_boards[agent][0][:3], (PuyoColor.OJAMA, PuyoColor.BLUE, PuyoColor.RED))
        finally:
            controller.shutdown()

    def test_ojama_lifecycle_is_stable_across_speed_pause_step_and_replay(self):
        config = RealtimeVersusUiConfig(
            policy_a="first", policy_b="random", max_ticks=160, replay_path="unused.json"
        )
        reference = RealtimeVersusMatchController(config)
        try:
            reference.env.match.schedule_attack("player_0", 3, delay_ticks=0)
            for _ in range(160):
                reference.advance_tick()
            expected_hash = reference.env.match.state_hash()
            expected_replay = [
                (tick["tick"], tick["inputs"], tick["snapshot_hash"])
                for tick in reference.replay_ticks
            ]
        finally:
            reference.shutdown()

        for speed in (0.25, 0.5, 1.0, 2.0, 4.0):
            with self.subTest(speed=speed):
                controller = RealtimeVersusMatchController(
                    RealtimeVersusUiConfig(
                        policy_a="first", policy_b="random", max_ticks=160,
                        replay_path="unused.json", speed=speed,
                    )
                )
                controller.env.match.schedule_attack("player_0", 3, delay_ticks=0)
                started = set()
                completed = set()
                paused_once = False
                previous_event = None
                try:
                    for _ in range(700):
                        controller.update(1 / 60)
                        event = controller.visual_event("player_1")
                        pending_garbage = [
                            item for item in controller.event_queues["player_1"]
                            if item.kind == "garbage"
                        ]
                        for pending in pending_garbage:
                            self.assertTrue(all(
                                controller.display_boards["player_1"][y][x] != PuyoColor.OJAMA
                                for x, y in pending.coords
                            ))
                        if event is not None and event.kind == "garbage":
                            started.add(id(event))
                            self.assertTrue(all(
                                controller.display_boards["player_1"][y][x] != PuyoColor.OJAMA
                                for x, y in event.coords
                            ))
                            if not paused_once:
                                paused_once = True
                                controller.paused = True
                                tick = controller.env.match.tick
                                elapsed = controller.visual_event_elapsed("player_1")
                                state_hash = controller.env.match.state_hash()
                                for _ in range(8):
                                    controller.update(1 / 60)
                                self.assertEqual(controller.env.match.tick, tick)
                                self.assertEqual(controller.visual_event_elapsed("player_1"), elapsed)
                                self.assertEqual(controller.env.match.state_hash(), state_hash)
                                self.assertTrue(controller.handle_keydown(pygame.K_n))
                                self.assertEqual(controller.env.match.tick, tick + 1)
                                self.assertIs(controller.visual_event("player_1"), event)
                                self.assertAlmostEqual(
                                    controller.visual_event_elapsed("player_1"),
                                    elapsed + controller.env.match.timing.tick_seconds / speed,
                                )
                                controller.paused = False
                        if previous_event is not None and previous_event.kind == "garbage" and event is not previous_event:
                            completed.add(id(previous_event))
                            self.assertTrue(all(
                                controller.display_boards["player_1"][y][x] == PuyoColor.OJAMA
                                for x, y in previous_event.coords
                            ))
                        previous_event = event
                        if controller.env.match.tick >= 160:
                            break
                    self.assertEqual(controller.env.match.tick, 160)
                    self.assertTrue(paused_once)
                    self.assertEqual(len(started), 1)
                    self.assertEqual(started, completed)
                    self.assertEqual(controller.env.match.state_hash(), expected_hash)
                    self.assertEqual(
                        [(tick["tick"], tick["inputs"], tick["snapshot_hash"]) for tick in controller.replay_ticks],
                        expected_replay,
                    )
                    self.assertEqual(controller.replay_payload()["expected_final_hash"], expected_hash)
                finally:
                    controller.shutdown()

        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(
                policy_a="first", policy_b="random", max_ticks=160,
                replay_path="unused.json", start_paused=True,
            )
        )
        controller.env.match.schedule_attack("player_0", 3, delay_ticks=0)
        step_started = set()
        step_completed = set()
        previous_event = None
        try:
            for _ in range(160):
                self.assertTrue(controller.handle_keydown(pygame.K_n))
                event = controller.visual_event("player_1")
                if event is not None and event.kind == "garbage":
                    step_started.add(id(event))
                if previous_event is not None and previous_event.kind == "garbage" and event is not previous_event:
                    step_completed.add(id(previous_event))
                previous_event = event
            self.assertTrue(controller.paused)
            self.assertEqual(step_started, step_completed)
            self.assertEqual(len(step_started), 1)
            self.assertEqual(controller.env.match.state_hash(), expected_hash)
            self.assertEqual(
                [(tick["tick"], tick["inputs"], tick["snapshot_hash"]) for tick in controller.replay_ticks],
                expected_replay,
            )
        finally:
            controller.shutdown()

    def test_plan_step_delta_cells_excludes_existing_board_cells(self):
        base_board = [[PuyoColor.EMPTY for _ in range(6)] for _ in range(12)]
        base_board[0][0] = PuyoColor.RED
        step = {
            "predicted_board": [
                ["RED", "BLUE", "EMPTY", "EMPTY", "EMPTY", "EMPTY"],
                ["EMPTY", "EMPTY", "GREEN", "EMPTY", "EMPTY", "EMPTY"],
            ]
        }

        self.assertEqual(
            plan_step_delta_cells(base_board, step),
            ((1, 0, "BLUE"), (2, 1, "GREEN")),
        )

    def test_explicit_plan_placement_cells_override_cumulative_board_delta(self):
        base_board = [[PuyoColor.EMPTY for _ in range(6)] for _ in range(12)]
        step = {
            "placement_cells": [
                {"x": 5, "y": 0, "color": "RED"},
                {"x": 5, "y": 1, "color": "YELLOW"},
            ],
            "predicted_board": [["BLUE", "GREEN"]],
        }

        self.assertEqual(
            plan_step_placement_cells(base_board, step),
            ((5, 0, "RED"), (5, 1, "YELLOW")),
        )

    def test_slow_policy_does_not_block_other_player_or_render_tick(self):
        release = multiprocessing.get_context("spawn").Event()

        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(policy_a="manager_rule", policy_b="first", max_ticks=500),
            policy_factory=lambda policy_type, **kwargs: (
                BlockingTestPolicy(release) if policy_type == "manager_rule" else FastTestPolicy()
            ),
            decision_process_start_method="spawn",
        )
        started = time.perf_counter()
        controller.advance_tick()
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.2)
        self.assertEqual(controller.env.match.tick, 1)
        self.assertEqual(controller.controllers["player_0"].diagnostics.last_event, "thinking")
        self.assertGreater(controller.controllers["player_1"].diagnostics.emitted_input_ticks, 0)
        self.assertNotEqual(
            controller._decision_executors["player_0"].process_pid,
            os.getpid(),
        )

        release.set()
        for _ in range(300):
            controller.advance_tick()
            if controller.controllers["player_0"].diagnostics.decisions_activated:
                break
            time.sleep(0.01)
        self.assertGreater(controller.controllers["player_0"].diagnostics.decisions_activated, 0)
        controller.shutdown()

    def test_stale_async_decision_is_rejected_after_active_pair_changes(self):
        release = multiprocessing.get_context("spawn").Event()

        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(policy_a="beam", policy_b="first", max_ticks=500),
            policy_factory=lambda policy_type, **kwargs: (
                BlockingTestPolicy(release) if policy_type == "beam" else FastTestPolicy()
            ),
            decision_process_start_method="spawn",
        )
        controller.advance_tick()
        controller.env.player_states["player_0"].simulator.game.spawn_puyo()
        release.set()
        for _ in range(300):
            controller.advance_tick()
            if controller.controllers["player_0"].diagnostics.stale_decisions:
                break
            time.sleep(0.01)
        self.assertEqual(controller.controllers["player_0"].diagnostics.stale_decisions, 1)
        controller.shutdown()

    def test_human_soft_drop_edges_do_not_pause_ai(self):
        class StubPolicy:
            def select_action(self, observation, info):
                return legal_indices(info)[0]

        controller = RealtimeVersusMatchController(
            RealtimeVersusUiConfig(policy_a="human", policy_b="first", max_ticks=80),
            policy_factory=lambda policy_type, **kwargs: StubPolicy(),
        )
        controller.handle_keydown(pygame.K_w)
        controller.advance_tick()
        self.assertIn(Action.DOWN, controller.last_inputs["player_0"].press)
        self.assertGreater(controller.controllers["player_1"].diagnostics.decisions_started, 0)

        controller.advance_tick()
        controller.advance_tick()
        self.assertIn(Action.DOWN, controller.last_inputs["player_0"].press)
        self.assertIn(Action.DOWN, controller.last_inputs["player_0"].release)

        controller.handle_keyup(pygame.K_w)
        controller.advance_tick()
        self.assertIn(Action.DOWN, controller.last_inputs["player_0"].release)
        controller.shutdown()

    def test_collection_off_writes_audit_without_dataset_session(self):
        class StubPolicy:
            def select_action(self, observation, info):
                return legal_indices(info)[0]

        with tempfile.TemporaryDirectory() as directory:
            controller = RealtimeVersusMatchController(
                RealtimeVersusUiConfig(
                    policy_a="human",
                    policy_b="first",
                    max_ticks=80,
                    dataset_root=directory,
                ),
                policy_factory=lambda policy_type, **kwargs: StubPolicy(),
            )
            for _ in range(4):
                controller.advance_tick()

            self.assertIsNone(controller.finalize_collection(interrupted=True))
            self.assertFalse((Path(directory) / "sessions").exists())
            audit = (Path(directory) / "collection_audit.jsonl").read_text(encoding="utf-8")
            self.assertIn('"enabled": false', audit)
            self.assertNotIn("snapshot_hash", audit)
            controller.shutdown()

    def test_collection_on_persists_replayable_session_and_feedback(self):
        class StubPolicy:
            def select_action(self, observation, info):
                return legal_indices(info)[0]

        with tempfile.TemporaryDirectory() as directory:
            controller = RealtimeVersusMatchController(
                RealtimeVersusUiConfig(
                    policy_a="human",
                    policy_b="first",
                    max_ticks=80,
                    collection_enabled=True,
                    dataset_root=directory,
                    collection_feedback="useful match",
                ),
                policy_factory=lambda policy_type, **kwargs: StubPolicy(),
            )
            for _ in range(8):
                controller.advance_tick()
            self.assertEqual(
                controller.collection_replay_ticks[0]["all_clear_diagnostics"]["schema_version"],
                "puyo.all_clear_diagnostics.v1",
            )
            manifest = controller.finalize_collection(interrupted=True)

            self.assertIsNotNone(manifest)
            session_dir = Path(directory) / "sessions" / manifest["session_id"]
            stored = json.loads((session_dir / "human_session_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["outcome"]["feedback"], "useful match")
            self.assertEqual(stored["trajectory"]["ticks"], 8)
            audit = (Path(directory) / "collection_audit.jsonl").read_text(encoding="utf-8")
            self.assertIn('"event": "session_saved"', audit)
            self.assertNotIn("useful match", audit)
            controller.shutdown()

    def test_stopping_collection_discards_buffered_trajectory(self):
        class StubPolicy:
            def select_action(self, observation, info):
                return legal_indices(info)[0]

        with tempfile.TemporaryDirectory() as directory:
            controller = RealtimeVersusMatchController(
                RealtimeVersusUiConfig(
                    policy_a="human",
                    policy_b="first",
                    max_ticks=80,
                    collection_enabled=True,
                    dataset_root=directory,
                ),
                policy_factory=lambda policy_type, **kwargs: StubPolicy(),
            )
            controller.advance_tick()
            controller.toggle_collection()

            self.assertFalse(controller.collection_enabled)
            self.assertFalse(controller.collection_replay_ticks)
            self.assertIsNone(controller.finalize_collection(interrupted=True))
            self.assertFalse((Path(directory) / "sessions").exists())
            controller.shutdown()

    def test_plan_ghost_is_full_size_color_outline_without_center_label(self):
        surface = pygame.Surface((160, 160))
        surface.fill((1, 2, 3))
        renderer = VersusRenderer(surface)
        field = pygame.Rect(32, 32, 6 * 32, 12 * 32)
        renderer._draw_plan_cell(field, 0, 11, "RED", alpha=255)
        sx, sy = renderer._grid_position(field, 0, 11)
        center = (int(sx + 16), int(sy + 16))
        radius = int(32 * 0.38)
        outline_colors = {
            surface.get_at((int(sx + 16 + offset), int(sy + 16)))[:3]
            for offset in range(radius - 2, radius + 2)
        }

        self.assertEqual(surface.get_at(center)[:3], (1, 2, 3))
        self.assertIn(renderer.colors[PuyoColor.RED], outline_colors)

    def test_plan_overlay_keeps_diagnostics_out_of_the_board(self):
        surface = pygame.Surface((320, 480))
        renderer = VersusRenderer(surface)
        rendered_labels = []
        renderer._draw_plan_cell = lambda *args, **kwargs: None
        renderer._draw_text = lambda text, *args, **kwargs: rendered_labels.append(text)
        empty_board = [["EMPTY"] * 6 for _ in range(12)]
        plan = {
            "schema_version": "n-turn-plan-v1",
            "plan_id": "plan-12345678",
            "update_reason": "new_observation",
            "steps": [
                {
                    "step_index": 0,
                    "known_tsumo": True,
                    "placement_cells": [
                        {"x": 0, "y": 11, "color": "RED"},
                        {"x": 0, "y": 10, "color": "BLUE"},
                    ],
                },
                {
                    "step_index": 1,
                    "known_tsumo": False,
                    "placement_cells": [
                        {"x": 1, "y": 11, "color": "GREEN"},
                        {"x": 1, "y": 10, "color": "YELLOW"},
                    ],
                },
            ],
        }

        renderer._draw_plan_overlay(
            pygame.Rect(32, 32, 6 * 32, 12 * 32),
            empty_board,
            plan,
        )

        self.assertEqual(rendered_labels, ["1", "2?"])

    def test_active_ghost_has_no_white_outline(self):
        surface = pygame.Surface((64, 64))
        surface.fill((1, 2, 3))
        renderer = VersusRenderer(surface)
        renderer._draw_puyo(16, 16, renderer.colors[PuyoColor.BLUE], alpha=150, scale=ACTIVE_GHOST_SCALE)
        radius = int(32 * 0.38 * ACTIVE_GHOST_SCALE)
        outside = surface.get_at((32 + radius + 2, 32))[:3]

        self.assertEqual(outside, (1, 2, 3))

    def test_visual_timeline_is_elapsed_time_based_and_active_ghost_is_half_size(self):
        self.assertEqual(animation_progress(0.2, 0.4), animation_progress(0.1 + 0.1, 0.4))
        self.assertEqual(settle_scale(1.0), (1.0, 1.0))
        self.assertEqual(ACTIVE_GHOST_SCALE, 0.5)


@unittest.skipUnless(PYGAME_AVAILABLE, "pygame is not installed")
class TestRealtimeVersusUiSmoke(unittest.TestCase):
    def test_initial_paused_frame_renders_before_first_tick(self):
        result = run_ui(
            RealtimeVersusUiConfig(
                policy_a="manager_rule",
                policy_b="beam",
                seed=54,
                max_ticks=80,
                beam_depth=2,
                beam_width=8,
                start_paused=True,
            ),
            max_frames=1,
        )

        self.assertEqual(result["ticks"], 0)
        self.assertEqual(result["decisions_player_0"], 0)

    def test_dummy_video_driver_smoke_advances_match_ticks(self):
        result = run_ui(
            RealtimeVersusUiConfig(
                policy_a="first",
                policy_b="random",
                seed=54,
                max_ticks=80,
                speed=4.0,
            ),
            max_frames=6,
        )

        self.assertGreater(result["ticks"], 0)
        self.assertGreater(result["decisions_player_0"], 0)

    def test_dummy_video_driver_writes_versioned_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            replay_path = Path(directory) / "replay.json"
            result = run_ui(
                RealtimeVersusUiConfig(
                    policy_a="first",
                    policy_b="random",
                    seed=54,
                    max_ticks=1,
                    speed=4.0,
                    replay_path=str(replay_path),
                    qa_notes="dummy smoke",
                ),
                max_frames=4,
            )

            replay = json.loads(replay_path.read_text(encoding="utf-8"))
            self.assertEqual(result["schema_version"], "puyo.gui_qa.v1")
            self.assertEqual(replay["format"], "puyo-realtime-match-v1")
            self.assertEqual(replay["outcome"]["notes"], "dummy smoke")
            self.assertEqual(
                replay["ticks"][0]["all_clear_diagnostics"]["schema_version"],
                "puyo.all_clear_diagnostics.v1",
            )
            self.assertIn("attack_diagnostics", replay["ticks"][0])

    def test_terminal_frame_auto_exit_and_frame_callback(self):
        rendered_frames = []
        result = run_ui(
            RealtimeVersusUiConfig(
                policy_a="first",
                policy_b="random",
                seed=54,
                max_ticks=1,
                speed=4.0,
                exit_after_finish_frames=2,
            ),
            frame_callback=lambda _screen, frame_index: rendered_frames.append(
                frame_index
            ),
        )

        self.assertFalse(result["result"]["interrupted"])
        self.assertEqual(result["result"]["termination_reason"], "tick_limit")
        self.assertGreaterEqual(len(rendered_frames), 2)


if __name__ == "__main__":
    unittest.main()
