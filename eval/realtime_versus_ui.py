"""Graphical realtime versus viewer for placement policies."""

from __future__ import annotations

import argparse
import json
import sys
import threading
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pygame
except ImportError:  # pragma: no cover - dependency guard
    pygame = None

from eval.versus_ui import SPEED_CHOICES, VisualEvent
from puyo_env.actions import placement_to_action_index
from puyo_env.realtime_ai import (
    RealtimeControllerDiagnostics,
    RealtimeControllerStatus,
    RealtimeDecisionConfig,
    RealtimePolicyController,
    RealtimePuyoEnv,
)
from puyo_env.realtime_versus import REALTIME_AGENTS
from selfplay.policies import Policy, make_policy
from src.core.constants import Action, Direction
from src.core.headless import PlacementAction
from src.core.realtime import TickInput

if pygame is not None:
    from src.ui.keybindings import ACTION_ORDER, KeyBindings
    from src.ui.versus_renderer import SCREEN_HEIGHT, SCREEN_WIDTH, VersusRenderer
else:  # pragma: no cover - used only for dependency-light config imports
    ACTION_ORDER = ()
    KeyBindings = None
    SCREEN_WIDTH = 1120
    SCREEN_HEIGHT = 780
    VersusRenderer = None


REALTIME_POLICY_CHOICES = (
    "human", "first", "random", "greedy", "beam", "checkpoint", "manager", "manager_rule",
    "worker_large", "worker_quick", "worker_punish", "worker_counter",
    "worker_fire", "worker_fire_max", "worker_survival",
)
ASYNC_POLICY_TYPES = frozenset({"beam", "checkpoint", "manager", "manager_rule"})
HUMAN_SOFT_DROP_REPEAT_TICKS = 2


class DaemonDecisionExecutor:
    """Small daemon-backed executor so closing the UI never waits for inference."""

    def __init__(self):
        self._futures: set[Future] = set()
        self._closed = False

    def submit(self, function, *args, **kwargs) -> Future:
        if self._closed:
            raise RuntimeError("decision executor is closed")
        future = Future()
        self._futures.add(future)

        def run():
            if not future.set_running_or_notify_cancel():
                return
            try:
                future.set_result(function(*args, **kwargs))
            except BaseException as exc:  # pragma: no cover - surfaced through Future.result
                future.set_exception(exc)
            finally:
                self._futures.discard(future)

        threading.Thread(target=run, name="puyo-policy", daemon=True).start()
        return future

    def shutdown(self, wait: bool = False, cancel_futures: bool = True) -> None:
        self._closed = True
        if cancel_futures:
            for future in tuple(self._futures):
                future.cancel()


class RealtimeHumanController:
    """Translate held UI keys into deterministic realtime input edges."""

    def __init__(self, agent: str):
        self.agent = agent
        self.diagnostics = RealtimeControllerDiagnostics(last_event="human_ready")
        self._press: list[Action] = []
        self._release: list[Action] = []
        self._held: set[Action] = set()
        self._last_soft_drop_pulse_tick: int | None = None

    def status(self) -> RealtimeControllerStatus:
        return RealtimeControllerStatus(None, 0, 0, 0, (), None)

    def reset(self) -> None:
        self._press.clear()
        self._release.clear()
        self._held.clear()
        self._last_soft_drop_pulse_tick = None
        self.diagnostics = RealtimeControllerDiagnostics(last_event="human_ready")

    def key_down(self, action: Action) -> None:
        if action not in self._held:
            self._held.add(action)
            self._press.append(action)

    def key_up(self, action: Action) -> None:
        if action in self._held:
            self._held.remove(action)
            self._release.append(action)

    def next_input(self, match, *_args, **_kwargs) -> TickInput:
        press = list(self._press)
        release = list(self._release)
        if Action.DOWN in press:
            self._last_soft_drop_pulse_tick = match.tick
        elif Action.DOWN in self._held and (
            self._last_soft_drop_pulse_tick is None
            or match.tick - self._last_soft_drop_pulse_tick >= HUMAN_SOFT_DROP_REPEAT_TICKS
        ):
            # Re-arm the held input at the same cadence as placement planner pulses.
            release.append(Action.DOWN)
            press.append(Action.DOWN)
            self._last_soft_drop_pulse_tick = match.tick
        if Action.DOWN in release and Action.DOWN not in self._held:
            self._last_soft_drop_pulse_tick = None
        tick_input = TickInput(press=tuple(press), release=tuple(release))
        self._press.clear()
        self._release.clear()
        self.diagnostics.emitted_input_ticks += bool(tick_input.press or tick_input.release)
        self.diagnostics.last_event = "human_input" if tick_input.press or tick_input.release else "human_held"
        return tick_input


@dataclass(frozen=True)
class RealtimeVersusUiConfig:
    policy_a: str = "first"
    policy_b: str = "random"
    checkpoint_a: str | None = None
    checkpoint_b: str | None = None
    seed: int = 1
    seed_a: int | None = None
    seed_b: int | None = None
    max_ticks: int = 10_000
    speed: float = 1.0
    start_paused: bool = False
    device: str = "cpu"
    deterministic: bool = True
    beam_depth: int = 10
    beam_width: int = 48
    beam_scenarios: int = 1
    beam_minimum_chain: int = 6
    beam_depth_a: int | None = None
    beam_depth_b: int | None = None
    beam_width_a: int | None = None
    beam_width_b: int | None = None
    beam_scenarios_a: int | None = None
    beam_scenarios_b: int | None = None
    beam_minimum_chain_a: int | None = None
    beam_minimum_chain_b: int | None = None
    device_a: str | None = None
    device_b: str | None = None
    deterministic_a: bool | None = None
    deterministic_b: bool | None = None
    inference_latency_ticks: int = 0
    timeout_ticks: int | None = None
    action_deadline_ticks: int | None = None
    use_reachable_action_mask: bool = False
    keybindings_path: str | None = None
    result_json: str | None = None
    max_frames: int | None = None
    plan_overlay: bool = True

    @property
    def max_steps(self) -> int:
        return self.max_ticks


def validate_config(config: RealtimeVersusUiConfig) -> None:
    policies = (config.policy_a, config.policy_b)
    if any(policy not in REALTIME_POLICY_CHOICES for policy in policies):
        raise ValueError(f"policy must be one of: {', '.join(REALTIME_POLICY_CHOICES)}")
    if policies.count("human") > 1:
        raise ValueError("only one human player is supported")
    if config.policy_a in {"checkpoint", "manager"} and not config.checkpoint_a:
        raise ValueError(f"--checkpoint-a is required when --policy-a={config.policy_a}")
    if config.policy_b in {"checkpoint", "manager"} and not config.checkpoint_b:
        raise ValueError(f"--checkpoint-b is required when --policy-b={config.policy_b}")
    if config.speed not in SPEED_CHOICES:
        raise ValueError(f"speed must be one of: {SPEED_CHOICES}")
    if config.max_ticks <= 0:
        raise ValueError("max_ticks must be positive")
    if config.inference_latency_ticks < 0:
        raise ValueError("inference_latency_ticks must be non-negative")
    if config.timeout_ticks is not None and config.timeout_ticks < 0:
        raise ValueError("timeout_ticks must be non-negative")
    if config.action_deadline_ticks is not None and config.action_deadline_ticks < 0:
        raise ValueError("action_deadline_ticks must be non-negative")
    if config.max_frames is not None and config.max_frames <= 0:
        raise ValueError("max_frames must be positive")
    for side in ("a", "b"):
        depth = getattr(config, f"beam_depth_{side}")
        width = getattr(config, f"beam_width_{side}")
        scenarios = getattr(config, f"beam_scenarios_{side}")
        minimum_chain = getattr(config, f"beam_minimum_chain_{side}")
        if depth is not None and depth < 1:
            raise ValueError(f"beam depth {side} must be at least 1")
        if width is not None and width < 1:
            raise ValueError(f"beam width {side} must be at least 1")
        if scenarios is not None and not 1 <= scenarios <= 6:
            raise ValueError(f"beam scenarios {side} must be in [1, 6]")
        if minimum_chain is not None and minimum_chain < 1:
            raise ValueError(f"beam minimum chain {side} must be at least 1")


class RealtimeVersusMatchController:
    progress_unit = "tick"

    def __init__(
        self,
        config: RealtimeVersusUiConfig,
        policy_factory: Callable[..., Policy] = make_policy,
    ):
        validate_config(config)
        self.config = config
        self.policy_factory = policy_factory
        self._decision_executor = DaemonDecisionExecutor()
        self.env = RealtimePuyoEnv(
            seed=config.seed,
            max_ticks=config.max_ticks,
            use_reachable_action_mask=config.use_reachable_action_mask,
        )
        self.speed = config.speed
        self.paused = config.start_paused
        self.event_queue: deque[VisualEvent] = deque()
        self.current_event: VisualEvent | None = None
        self.event_elapsed = 0.0
        self.event_queues = {agent: deque() for agent in REALTIME_AGENTS}
        self.current_events: dict[str, VisualEvent | None] = {agent: None for agent in REALTIME_AGENTS}
        self.event_elapsed_by_agent = {agent: 0.0 for agent in REALTIME_AGENTS}
        self.tick_elapsed = 0.0
        self.last_inputs: dict[str, TickInput] = {}
        self.display_boards: dict[str, tuple] = {}
        if KeyBindings is None:
            raise ImportError("realtime versus UI requires pygame; install requirements.txt")
        self.keybindings = KeyBindings(config.keybindings_path)
        self.settings_open = False
        self.settings_index = 0
        self.settings_capture = False
        self.settings_message = ""
        self._settings_previous_paused = self.paused
        self.plan_overlay_enabled = {
            "player_0": config.plan_overlay,
            "player_1": config.plan_overlay,
        }
        self.policies: dict[str, Policy | None] = {}
        self.controllers: dict[str, RealtimePolicyController | RealtimeHumanController] = {}
        self.human: RealtimeHumanController | None = None
        self.human_agent: str | None = None
        self.observations = {}
        self.infos = {}
        self.reset()

    @property
    def progress_value(self) -> int:
        return self.env.match.tick

    @property
    def policy_names(self) -> dict[str, str]:
        return {"player_0": self.config.policy_a, "player_1": self.config.policy_b}

    @property
    def winner(self) -> str | None:
        return self.infos.get("player_0", {}).get("winner")

    def uses_live_active_pair(self) -> bool:
        return True

    def policy_display_name(self, agent: str) -> str:
        policy = self.policies.get(agent)
        profile_name = getattr(policy, "current_profile_name", None)
        base = self.policy_names[agent]
        return f"{base}: {profile_name}" if profile_name else base

    def tactical_diagnostics(self, agent: str) -> dict:
        policy = self.policies.get(agent)
        diagnostics = getattr(policy, "tactical_diagnostics", None)
        if isinstance(diagnostics, dict):
            return diagnostics
        proposal = getattr(policy, "last_proposal", None)
        plan = getattr(policy, "last_plan", None)
        if proposal is not None:
            return {
                "incoming_attack": proposal.incoming_attack,
                "target_attack": proposal.target_attack,
                "deadline": proposal.deadline,
                "reason": proposal.reason,
                "objective": getattr(proposal, "objective_dict", {}),
                "objective_result": getattr(proposal, "objective_result_dict", {}),
                "plan": {} if plan is None else plan.to_dict(),
                "plan_id": "" if plan is None else plan.plan_id,
                "plan_update_reason": "" if plan is None else plan.update_reason,
            }
        decision = self.controllers[agent].diagnostics.last_decision
        if decision is None:
            return {}
        target = "-" if decision.action_index is None else str(decision.action_index)
        return {
            "target_attack": target,
            "deadline": self.config.action_deadline_ticks or 0,
            "reason": decision.reason,
        }

    def plan_overlay(self, agent: str) -> dict:
        if not self.plan_overlay_enabled.get(agent, False):
            return {}
        diagnostics = self.tactical_diagnostics(agent)
        plan = diagnostics.get("plan", {})
        return plan if isinstance(plan, dict) else {}

    def realtime_diagnostics(self, agent: str) -> dict[str, str]:
        controller = self.controllers[agent]
        status = controller.status()
        info = self.infos.get(agent, {})
        tick_input = self.last_inputs.get(agent, TickInput())
        input_label = self._format_input_label(tick_input, info.get("held_actions", ()))
        if status.pending_ready_tick is not None:
            plan_label = f"waiting {status.pending_ready_tick - self.env.match.tick}"
        else:
            plan_label = f"plan {status.input_cursor}/{status.active_plan_ticks}"
        incoming = info.get("incoming_ticks", 0)
        arrival = info.get("incoming_arrival_tick")
        deadline_label = "deadline -"
        if incoming or arrival is not None:
            deadline_label = f"deadline t-{incoming} at {arrival}"
        return {
            "input": input_label,
            "plan": plan_label,
            "event": controller.diagnostics.last_event,
            "deadline": deadline_label,
        }

    def target_action(self, agent: str) -> int | None:
        status = self.controllers[agent].status()
        if status.active_action_index is not None:
            return status.active_action_index
        decision = self.controllers[agent].diagnostics.last_decision
        if decision is None:
            return None
        return decision.action_index

    def active_action(self, agent: str) -> int:
        target = self.target_action(agent)
        if target is not None:
            return target
        return placement_to_action_index(PlacementAction(2, Direction.UP))

    def reset(self) -> None:
        self.policies = {
            "player_0": None if self.config.policy_a == "human" else self._make_policy("a"),
            "player_1": None if self.config.policy_b == "human" else self._make_policy("b"),
        }
        decision_config = RealtimeDecisionConfig(
            inference_latency_ticks=self.config.inference_latency_ticks,
            timeout_ticks=self.config.timeout_ticks,
            action_deadline_ticks=self.config.action_deadline_ticks,
            use_reachable_action_mask=self.config.use_reachable_action_mask,
        )
        self.controllers = {}
        self.human = None
        self.human_agent = None
        for agent, policy in self.policies.items():
            if policy is None:
                self.human = RealtimeHumanController(agent)
                self.human_agent = agent
                self.controllers[agent] = self.human
                continue
            policy_name = self.policy_names[agent]
            executor = self._decision_executor if policy_name in ASYNC_POLICY_TYPES else None
            self.controllers[agent] = RealtimePolicyController(
                policy,
                config=decision_config,
                decision_executor=executor,
            )
        self.observations, self.infos = self.env.reset(seed=self.config.seed)
        self.event_queue.clear()
        self.current_event = None
        self.event_elapsed = 0.0
        for agent in REALTIME_AGENTS:
            self.event_queues[agent].clear()
            self.current_events[agent] = None
            self.event_elapsed_by_agent[agent] = 0.0
        self.tick_elapsed = 0.0
        self.last_inputs = {}
        self._sync_display_boards()

    def advance_one(self, include_human: bool = False) -> bool:
        _ = include_human
        return self.advance_tick()

    def advance_tick(self) -> bool:
        if not self.env.agents:
            return False
        inputs = {}
        for agent in self.env.agents:
            inputs[agent] = self.controllers[agent].next_input(
                self.env.match,
                agent,
                self.observations[agent],
                self.infos[agent],
            )
        self.last_inputs = inputs
        self.observations, _, _, _, self.infos = self.env.step(inputs)
        self._sync_display_boards()
        match_result = self.infos["player_0"].get("match_result")
        if match_result is not None:
            for event in self._visual_events_from_tick(match_result):
                self.event_queues[event.agent].append(event)
            for agent in REALTIME_AGENTS:
                self._start_next_event(agent)
        return True

    def update(self, delta_time: float) -> None:
        if self.paused:
            return
        self._advance_visual_events(delta_time)
        tick_seconds = self.env.match.timing.tick_seconds
        self.tick_elapsed += delta_time * self.speed
        ticks_to_run = min(int(self.tick_elapsed / tick_seconds), 12)
        for _ in range(ticks_to_run):
            if not self.advance_tick():
                self.tick_elapsed = 0.0
                return
            self.tick_elapsed -= tick_seconds

    def change_speed(self, direction: int) -> None:
        index = SPEED_CHOICES.index(self.speed)
        self.speed = SPEED_CHOICES[max(0, min(len(SPEED_CHOICES) - 1, index + direction))]

    def _make_policy(self, side: str) -> Policy:
        policy_type = self.config.policy_a if side == "a" else self.config.policy_b
        policy_seed = getattr(self.config, f"seed_{side}")
        if policy_seed is None:
            policy_seed = self.config.seed + (0 if side == "a" else 10_000)

        def side_value(name: str):
            value = getattr(self.config, f"{name}_{side}")
            return getattr(self.config, name) if value is None else value

        return self.policy_factory(
            policy_type,
            seed=policy_seed,
            checkpoint_path=self.config.checkpoint_a if side == "a" else self.config.checkpoint_b,
            device=side_value("device"),
            deterministic=side_value("deterministic"),
            beam_depth=side_value("beam_depth"),
            beam_width=side_value("beam_width"),
            beam_scenarios=side_value("beam_scenarios"),
            beam_minimum_chain=side_value("beam_minimum_chain"),
        )

    def _current_board(self, agent: str) -> tuple:
        grid = self.env.player_states[agent].simulator.game.field.to_color_grid()
        return tuple(tuple(row) for row in grid)

    def _sync_display_boards(self) -> None:
        self.display_boards = {agent: self._current_board(agent) for agent in REALTIME_AGENTS}

    def _visual_events_from_tick(self, match_result) -> list[VisualEvent]:
        events = []
        for agent in REALTIME_AGENTS:
            for event in match_result.player_results[agent].events:
                if event.type == "lock":
                    placement = PlacementAction(
                        int(event.data.get("axis_x", 2)),
                        Direction[str(event.data.get("rotation", "UP"))],
                    )
                    events.append(
                        VisualEvent(
                            "placement",
                            agent,
                            "LOCK",
                            action=placement_to_action_index(placement),
                            axis_y=int(event.data.get("axis_y", 0)),
                        )
                    )
                elif event.type == "resolution_complete":
                    chain_count = int(event.data.get("chain_count", 0))
                    score_delta = int(event.data.get("score_delta", 0))
                    if chain_count or score_delta:
                        events.append(
                            VisualEvent(
                                "chain",
                                agent,
                                f"{chain_count} CHAIN  +{score_delta}",
                                amount=score_delta,
                                chain_index=chain_count,
                            )
                        )
        for agent, amount in match_result.dropped_ojama.items():
            if amount:
                events.append(VisualEvent("garbage", agent, f"OJAMA +{amount}", amount=int(amount)))
        return events

    def _start_next_event(self, agent: str) -> None:
        if self.current_events[agent] is None and self.event_queues[agent]:
            self.current_events[agent] = self.event_queues[agent].popleft()
            self.event_elapsed_by_agent[agent] = 0.0

    def _advance_visual_events(self, delta_time: float) -> None:
        for agent in REALTIME_AGENTS:
            event = self.current_events[agent]
            if event is None:
                self._start_next_event(agent)
                continue
            self.event_elapsed_by_agent[agent] += delta_time
            if self.event_elapsed_by_agent[agent] >= self._event_duration(event):
                self.current_events[agent] = None
                self._start_next_event(agent)

    def _event_duration(self, event: VisualEvent | None) -> float:
        if event is None:
            return 0.0
        durations = {"garbage": 0.35, "placement": 0.20, "chain": 0.55}
        return durations.get(event.kind, 0.25) / self.speed

    def visual_event(self, agent: str) -> VisualEvent | None:
        return self.current_events[agent]

    def visual_event_elapsed(self, agent: str) -> float:
        return self.event_elapsed_by_agent[agent]

    def _open_settings(self) -> None:
        self._settings_previous_paused = self.paused
        self.paused = True
        self.settings_open = True
        self.settings_capture = False
        self.settings_message = "Select an action and press Enter."

    def _close_settings(self) -> None:
        self.settings_open = False
        self.settings_capture = False
        self.paused = self._settings_previous_paused

    def _handle_settings_keydown(self, key: int) -> None:
        if self.settings_capture:
            if key == pygame.K_ESCAPE:
                self.settings_capture = False
                self.settings_message = "Key change canceled."
                return
            action = ACTION_ORDER[self.settings_index]
            try:
                self.keybindings.rebind(action, key)
            except OSError as exc:
                self.settings_message = f"Could not save key settings: {exc}"
            else:
                self.settings_message = f"Saved {self.keybindings.display_names(action)}."
            self.settings_capture = False
            return

        if key == pygame.K_ESCAPE:
            self._close_settings()
        elif key == pygame.K_UP:
            self.settings_index = (self.settings_index - 1) % len(ACTION_ORDER)
        elif key == pygame.K_DOWN:
            self.settings_index = (self.settings_index + 1) % len(ACTION_ORDER)
        elif key in (pygame.K_RETURN, pygame.K_SPACE):
            self.settings_capture = True
            self.settings_message = "Press the new key. Esc cancels."
        elif key == pygame.K_BACKSPACE:
            try:
                self.keybindings.reset_defaults()
            except OSError as exc:
                self.settings_message = f"Could not save key settings: {exc}"
            else:
                self.settings_message = "Restored and saved default keys."

    def handle_keydown(self, key: int) -> bool:
        if pygame is None:
            return True
        if self.settings_open:
            self._handle_settings_keydown(key)
            return True
        if self.keybindings.matches("open_settings", key):
            self._open_settings()
        elif self.keybindings.matches("quit", key):
            return False
        elif self.keybindings.matches("pause", key):
            self.paused = not self.paused
        elif self.keybindings.matches("reset", key):
            self.reset()
        elif self.keybindings.matches("speed_up", key):
            self.change_speed(1)
        elif self.keybindings.matches("speed_down", key):
            self.change_speed(-1)
        elif self.keybindings.matches("step", key):
            self.advance_tick()
        elif key == pygame.K_o:
            enabled = not all(self.plan_overlay_enabled.values())
            self.plan_overlay_enabled = {agent: enabled for agent in REALTIME_AGENTS}
        elif self.human is not None:
            action = self._human_action_for_key(key)
            if action is not None:
                self.human.key_down(action)
        return True

    def handle_keyup(self, key: int) -> None:
        if self.human is None:
            return
        action = self._human_action_for_key(key)
        if action is not None:
            self.human.key_up(action)

    def _human_action_for_key(self, key: int) -> Action | None:
        bindings = (
            ("human_left", Action.LEFT),
            ("human_right", Action.RIGHT),
            ("rotate_left", Action.ROTATE_LEFT),
            ("rotate_right", Action.ROTATE_RIGHT),
            ("drop", Action.DOWN),
        )
        for binding, action in bindings:
            if self.keybindings.matches(binding, key):
                return action
        return None

    def shutdown(self) -> None:
        self._decision_executor.shutdown(wait=False, cancel_futures=True)

    def _format_input_label(self, tick_input: TickInput, held_actions) -> str:
        edges = [f"+{action.name}" for action in tick_input.press]
        edges.extend(f"-{action.name}" for action in tick_input.release)
        if edges:
            return " ".join(edges)
        held = tuple(held_actions or ())
        if held:
            return "held " + "/".join(held)
        return "idle"


def parse_config(argv=None) -> RealtimeVersusUiConfig:
    parser = argparse.ArgumentParser(description="Watch a realtime Puyo AI versus match.")
    parser.add_argument("--policy-a", choices=REALTIME_POLICY_CHOICES, default="first")
    parser.add_argument("--policy-b", choices=REALTIME_POLICY_CHOICES, default="random")
    parser.add_argument("--checkpoint-a")
    parser.add_argument("--checkpoint-b")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--seed-a", "--policy-seed-a", dest="seed_a", type=int)
    parser.add_argument("--seed-b", "--policy-seed-b", dest="seed_b", type=int)
    parser.add_argument("--max-ticks", type=int, default=10_000)
    parser.add_argument("--speed", type=float, choices=SPEED_CHOICES, default=1.0)
    parser.add_argument("--start-paused", action="store_true")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--stochastic", action="store_true")
    parser.add_argument("--beam-depth", type=int, default=10)
    parser.add_argument("--beam-width", type=int, default=48)
    parser.add_argument("--beam-scenarios", type=int, default=1)
    parser.add_argument("--beam-minimum-chain", type=int, default=6)
    for side in ("a", "b"):
        parser.add_argument(f"--beam-depth-{side}", type=int)
        parser.add_argument(f"--beam-width-{side}", type=int)
        parser.add_argument(f"--beam-scenarios-{side}", type=int)
        parser.add_argument(f"--beam-minimum-chain-{side}", type=int)
        parser.add_argument(f"--device-{side}")
        deterministic_group = parser.add_mutually_exclusive_group()
        deterministic_group.add_argument(
            f"--deterministic-{side}",
            dest=f"deterministic_{side}",
            action="store_true",
        )
        deterministic_group.add_argument(
            f"--stochastic-{side}",
            dest=f"deterministic_{side}",
            action="store_false",
        )
        parser.set_defaults(**{f"deterministic_{side}": None})
    parser.add_argument("--inference-latency-ticks", type=int, default=0)
    parser.add_argument("--timeout-ticks", type=int)
    parser.add_argument("--action-deadline-ticks", type=int)
    parser.add_argument("--use-reachable-action-mask", action="store_true")
    parser.add_argument("--result-json", help="Write the final UI smoke result as JSON.")
    parser.add_argument("--max-frames", type=int, help="Stop after this many rendered frames.")
    parser.add_argument("--no-plan-overlay", dest="plan_overlay", action="store_false")
    parser.set_defaults(plan_overlay=True)
    parser.add_argument(
        "--keybindings",
        dest="keybindings_path",
        help="Override the persistent keybindings JSON path.",
    )
    args = parser.parse_args(argv)
    config = RealtimeVersusUiConfig(
        policy_a=args.policy_a,
        policy_b=args.policy_b,
        checkpoint_a=args.checkpoint_a,
        checkpoint_b=args.checkpoint_b,
        seed=args.seed,
        seed_a=args.seed_a,
        seed_b=args.seed_b,
        max_ticks=args.max_ticks,
        speed=args.speed,
        start_paused=args.start_paused,
        device=args.device,
        deterministic=not args.stochastic,
        beam_depth=args.beam_depth,
        beam_width=args.beam_width,
        beam_scenarios=args.beam_scenarios,
        beam_minimum_chain=args.beam_minimum_chain,
        beam_depth_a=args.beam_depth_a,
        beam_depth_b=args.beam_depth_b,
        beam_width_a=args.beam_width_a,
        beam_width_b=args.beam_width_b,
        beam_scenarios_a=args.beam_scenarios_a,
        beam_scenarios_b=args.beam_scenarios_b,
        beam_minimum_chain_a=args.beam_minimum_chain_a,
        beam_minimum_chain_b=args.beam_minimum_chain_b,
        device_a=args.device_a,
        device_b=args.device_b,
        deterministic_a=args.deterministic_a,
        deterministic_b=args.deterministic_b,
        inference_latency_ticks=args.inference_latency_ticks,
        timeout_ticks=args.timeout_ticks,
        action_deadline_ticks=args.action_deadline_ticks,
        use_reachable_action_mask=args.use_reachable_action_mask,
        keybindings_path=args.keybindings_path,
        result_json=args.result_json,
        max_frames=args.max_frames,
        plan_overlay=args.plan_overlay,
    )
    try:
        validate_config(config)
    except ValueError as exc:
        parser.error(str(exc))
    return config


def run_ui(config: RealtimeVersusUiConfig, *, max_frames: int | None = None) -> dict:
    if pygame is None:
        raise ImportError("realtime versus UI requires pygame; install requirements.txt")
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("Puyo AI Realtime Versus")
    clock = pygame.time.Clock()
    controller = RealtimeVersusMatchController(config)
    renderer = VersusRenderer(screen)
    running = True
    frames = 0
    try:
        while running and (max_frames is None or frames < max_frames):
            delta_time = clock.tick(60) / 1000.0
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    running = controller.handle_keydown(event.key)
                elif event.type == pygame.KEYUP:
                    controller.handle_keyup(event.key)
            controller.update(delta_time)
            renderer.draw(controller)
            frames += 1
    except KeyboardInterrupt:
        pass
    finally:
        result = {
            "winner": controller.winner,
            "score_player_0": controller.infos["player_0"]["score"],
            "score_player_1": controller.infos["player_1"]["score"],
            "ticks": controller.env.match.tick,
            "decisions_player_0": controller.controllers["player_0"].diagnostics.decisions_started,
            "decisions_player_1": controller.controllers["player_1"].diagnostics.decisions_started,
            "emitted_input_ticks_player_0": controller.controllers["player_0"].diagnostics.emitted_input_ticks,
            "emitted_input_ticks_player_1": controller.controllers["player_1"].diagnostics.emitted_input_ticks,
            "plan_overlay_player_0": controller.plan_overlay_enabled["player_0"],
            "plan_overlay_player_1": controller.plan_overlay_enabled["player_1"],
        }
        controller.shutdown()
        pygame.quit()
    return result


def main(argv=None) -> None:
    config = parse_config(argv)
    result = run_ui(config, max_frames=config.max_frames)
    if config.result_json:
        path = Path(config.result_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"result: winner={result['winner']} score_player_0={result['score_player_0']} "
        f"score_player_1={result['score_player_1']} ticks={result['ticks']}"
    )


if __name__ == "__main__":
    main()
