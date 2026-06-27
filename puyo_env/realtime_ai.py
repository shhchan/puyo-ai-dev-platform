"""Realtime AI observation, scheduling, and controller adapters."""

from __future__ import annotations

import copy
import math
import time
from concurrent.futures import Executor, Future
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

try:
    import numpy as np
except ImportError:  # pragma: no cover - dependency guard
    np = None

from puyo_env.action_planner import PlannedPlacement, plan_placement_action
from puyo_env.actions import NUM_ACTIONS, PLACEMENT_ACTIONS, action_to_placement
from puyo_env.obs import encode_board, encode_next_pairs, encode_scalars
from puyo_env.rewards import score_to_ojama
from puyo_env.realtime_versus import REALTIME_AGENTS, RealtimeMatchTickResult, RealtimeVersusMatch
from src.core.constants import Direction, GRID_HEIGHT, GRID_WIDTH
from src.core.headless import HeadlessPuyoSimulator
from src.core.realtime import DEFAULT_REALTIME_TIMING, RealtimeTimingConfig, TickInput


REALTIME_OBSERVATION_SCHEMA_VERSION = "realtime-placement-v1"
REALTIME_ACTION_CONTRACT_VERSION = "placement-index-to-tick-input-v1"
TURN_BASED_OBSERVATION_SCHEMA_VERSION = "placement-v1"
REALTIME_SCALAR_FEATURE_DIM = 8

_PHASE_CODES = {
    "control": 0.0,
    "animate": 0.25,
    "countdown": 0.5,
    "ready": 0.75,
    "gameover": 1.0,
}


def _require_numpy():
    if np is None:
        raise ImportError("realtime AI observation encoding requires numpy. Install requirements.txt.")
    return np


@dataclass(frozen=True)
class RealtimeDecisionConfig:
    """Deterministic controller timing and fallback contract."""

    inference_latency_ticks: int = 0
    timeout_ticks: int | None = None
    action_deadline_ticks: int | None = None
    fallback_action_index: int | None = None
    use_reachable_action_mask: bool = False
    abort_unreachable_active_plan: bool = True
    replan_check_interval_ticks: int = 8
    max_plan_expanded_states: int = 2_000

    def __post_init__(self) -> None:
        if self.inference_latency_ticks < 0:
            raise ValueError("inference_latency_ticks must be non-negative")
        if self.timeout_ticks is not None and self.timeout_ticks < 0:
            raise ValueError("timeout_ticks must be non-negative")
        if self.action_deadline_ticks is not None and self.action_deadline_ticks < 0:
            raise ValueError("action_deadline_ticks must be non-negative")
        if self.fallback_action_index is not None and not 0 <= self.fallback_action_index < NUM_ACTIONS:
            raise ValueError("fallback_action_index is outside the placement action range")
        if self.replan_check_interval_ticks < 1:
            raise ValueError("replan_check_interval_ticks must be positive")


@dataclass(frozen=True)
class RealtimeRewardConfig:
    """Reward components for fixed-tick realtime versus rollouts."""

    target_score_per_ojama: int = 70
    score_reward: float = 0.25
    attack_reward: float = 0.5
    chain_bonus: float = 0.05
    survival_bonus: float = 0.001
    garbage_penalty: float = 0.02
    deadline_miss_penalty: float = 0.25
    input_failure_penalty: float = 1.0
    win_reward: float = 10.0
    loss_penalty: float = 10.0
    draw_penalty: float = 1.0


@dataclass(frozen=True)
class RealtimeDecisionRecord:
    tick: int
    action_index: int | None
    axis_x: int | None
    rotation: str | None
    reachable: bool
    plan_ticks: int
    inference_latency_ticks: int
    timeout: bool
    deadline_miss: bool
    fallback: bool
    reason: str
    policy_elapsed_seconds: float

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RealtimeControllerDiagnostics:
    decision_requests: int = 0
    decisions_started: int = 0
    decisions_activated: int = 0
    timeouts: int = 0
    deadline_misses: int = 0
    unreachable_plans: int = 0
    masked_actions: int = 0
    fallback_actions: int = 0
    replans: int = 0
    emitted_input_ticks: int = 0
    idle_ticks: int = 0
    stale_decisions: int = 0
    policy_elapsed_seconds: float = 0.0
    inference_latency_ticks: int = 0
    planned_input_ticks: int = 0
    last_event: str = "idle"
    last_emitted_input: dict[str, list[str]] | None = None
    last_decision: RealtimeDecisionRecord | None = None

    @property
    def mean_policy_elapsed_ms(self) -> float:
        if self.decisions_started == 0:
            return 0.0
        return self.policy_elapsed_seconds * 1000.0 / self.decisions_started

    @property
    def mean_inference_latency_ticks(self) -> float:
        if self.decisions_started == 0:
            return 0.0
        return self.inference_latency_ticks / self.decisions_started

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["mean_policy_elapsed_ms"] = self.mean_policy_elapsed_ms
        payload["mean_inference_latency_ticks"] = self.mean_inference_latency_ticks
        if self.last_decision is not None:
            payload["last_decision"] = self.last_decision.to_json()
        return payload


@dataclass
class _PendingDecision:
    ready_tick: int
    record: RealtimeDecisionRecord
    plan: PlannedPlacement | None


@dataclass
class _AsyncDecision:
    future: Future
    requested_tick: int
    state_token: tuple[int, int]
    info: dict[str, Any]


@dataclass(frozen=True)
class RealtimeControllerStatus:
    active_action_index: int | None
    active_plan_ticks: int
    active_plan_remaining_ticks: int
    input_cursor: int
    active_plan_actions: tuple[str, ...]
    pending_ready_tick: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RealtimePolicyController:
    """Adapt placement-level policies to tick-level realtime inputs."""

    def __init__(
        self,
        policy: Any,
        *,
        config: RealtimeDecisionConfig | None = None,
        timing: RealtimeTimingConfig | None = None,
        decision_executor: Executor | None = None,
    ):
        self.policy = policy
        self.config = config or RealtimeDecisionConfig()
        self.timing = timing or DEFAULT_REALTIME_TIMING
        self.decision_executor = decision_executor
        self.diagnostics = RealtimeControllerDiagnostics()
        self._pending_decision: _PendingDecision | None = None
        self._async_decision: _AsyncDecision | None = None
        self._active_plan: PlannedPlacement | None = None
        self._active_action_index: int | None = None
        self._input_cursor = 0
        self._last_replan_check_tick = -1

    @property
    def active_plan_remaining_ticks(self) -> int:
        if self._active_plan is None:
            return 0
        return max(0, len(self._active_plan.inputs) - self._input_cursor)

    @property
    def active_action_index(self) -> int | None:
        return self._active_action_index

    def status(self) -> RealtimeControllerStatus:
        return RealtimeControllerStatus(
            active_action_index=self._active_action_index,
            active_plan_ticks=0 if self._active_plan is None else len(self._active_plan.inputs),
            active_plan_remaining_ticks=self.active_plan_remaining_ticks,
            input_cursor=self._input_cursor,
            active_plan_actions=(
                ()
                if self._active_plan is None
                else tuple(action.name for action in self._active_plan.high_level_actions)
            ),
            pending_ready_tick=None if self._pending_decision is None else self._pending_decision.ready_tick,
        )

    def reset(self) -> None:
        reset = getattr(self.policy, "reset", None)
        if callable(reset):
            reset()
        self.diagnostics = RealtimeControllerDiagnostics()
        self._pending_decision = None
        if self._async_decision is not None:
            self._async_decision.future.cancel()
        self._async_decision = None
        self._active_plan = None
        self._active_action_index = None
        self._input_cursor = 0
        self._last_replan_check_tick = -1

    def next_input(
        self,
        match: RealtimeVersusMatch,
        agent: str,
        observation: dict[str, Any] | None = None,
        info: dict[str, Any] | None = None,
    ) -> TickInput:
        """Return the input for the current match tick."""

        simulator = match.player_states[agent].simulator
        if self._active_plan is not None:
            if self._should_abort_active_plan(simulator):
                self._active_plan = None
                self._active_action_index = None
                self._input_cursor = 0
                self._pending_decision = None
                self.diagnostics.replans += 1
                self.diagnostics.last_event = "replan"
            else:
                return self._emit_active_plan_input()

        if simulator.game.state != "control" or simulator.game.game_over:
            self.diagnostics.idle_ticks += 1
            self.diagnostics.last_event = f"waiting_for_{simulator.game.state}"
            return TickInput()

        if self._pending_decision is None and self._async_decision is None:
            observation = observation or build_realtime_observation(match, agent)
            if info is None or (
                self.config.use_reachable_action_mask
                and info.get("action_mask_source") != "reachable_planner"
            ):
                info = build_realtime_info(
                    match,
                    agent,
                    use_reachable_action_mask=self.config.use_reachable_action_mask,
                )
            if self.decision_executor is None:
                self._pending_decision = self._start_decision(match, agent, observation, info)
            else:
                self._async_decision = self._submit_decision(match, agent, observation, info)

        if self._async_decision is not None:
            if not self._async_decision.future.done():
                self.diagnostics.idle_ticks += 1
                self.diagnostics.last_event = "thinking"
                return TickInput()
            pending_async = self._async_decision
            self._async_decision = None
            try:
                selected_action, elapsed = pending_async.future.result()
            except Exception:
                selected_action, elapsed = None, 0.0
            if pending_async.state_token != self._decision_state_token(match, agent):
                self.diagnostics.stale_decisions += 1
                self.diagnostics.last_event = "stale_decision_rejected"
                return TickInput()
            self._pending_decision = self._complete_decision(
                match,
                agent,
                selected_action,
                pending_async.info,
                elapsed,
                requested_tick=pending_async.requested_tick,
            )

        if match.tick < self._pending_decision.ready_tick:
            self.diagnostics.idle_ticks += 1
            self.diagnostics.last_event = "waiting_for_inference"
            return TickInput()

        pending = self._pending_decision
        self._pending_decision = None
        self._active_plan = pending.plan
        self._active_action_index = pending.record.action_index
        self._input_cursor = 0
        self.diagnostics.decisions_activated += 1
        self.diagnostics.last_decision = pending.record
        if self._active_plan is None or not self._active_plan.inputs:
            self.diagnostics.idle_ticks += 1
            self.diagnostics.last_event = pending.record.reason
            return TickInput()
        return self._emit_active_plan_input()

    def _start_decision(
        self,
        match: RealtimeVersusMatch,
        agent: str,
        observation: dict[str, Any],
        info: dict[str, Any],
    ) -> _PendingDecision:
        self.diagnostics.decision_requests += 1
        self.diagnostics.decisions_started += 1
        started = time.perf_counter()
        selected_action = int(self.policy.select_action(observation, info))
        elapsed = time.perf_counter() - started
        return self._complete_decision(match, agent, selected_action, info, elapsed)

    def _submit_decision(self, match, agent, observation, info) -> _AsyncDecision:
        self.diagnostics.decision_requests += 1
        self.diagnostics.decisions_started += 1

        def select():
            started = time.perf_counter()
            selected = int(self.policy.select_action(observation, info))
            return selected, time.perf_counter() - started

        return _AsyncDecision(
            future=self.decision_executor.submit(select),
            requested_tick=match.tick,
            state_token=self._decision_state_token(match, agent),
            info=dict(info),
        )

    @staticmethod
    def _decision_state_token(match, agent) -> tuple[int, int]:
        game = match.player_states[agent].simulator.game
        return (id(game.current_puyo_1), id(game.current_puyo_2))

    def _complete_decision(
        self,
        match,
        agent,
        selected_action,
        info,
        elapsed: float,
        *,
        requested_tick: int | None = None,
    ) -> _PendingDecision:
        self.diagnostics.policy_elapsed_seconds += elapsed

        mask = _bool_mask(info.get("action_mask"))
        timeout_limit = self.config.timeout_ticks
        configured_latency = int(self.config.inference_latency_ticks)
        timeout = timeout_limit is not None and configured_latency > timeout_limit
        effective_latency = min(configured_latency, timeout_limit) if timeout else configured_latency
        self.diagnostics.inference_latency_ticks += int(effective_latency or 0)

        action_index = None if selected_action is None else int(selected_action)
        reason = "policy"
        fallback = False
        if timeout:
            self.diagnostics.timeouts += 1
            action_index = self._fallback_action(mask, match=match, agent=agent)
            reason = "timeout_fallback"
            fallback = True

        if action_index is None or not 0 <= int(action_index) < NUM_ACTIONS:
            action_index = self._fallback_action(mask, match=match, agent=agent)
            reason = "invalid_action_fallback"
            fallback = True
        elif not mask[int(action_index)]:
            self.diagnostics.masked_actions += 1
            action_index = self._fallback_action(mask, match=match, agent=agent)
            reason = "masked_action_fallback"
            fallback = True

        plan = self._plan_action(match, agent, action_index)
        if plan is not None and not plan.reachable:
            self.diagnostics.unreachable_plans += 1
            action_index = self._fallback_action(mask, exclude=action_index, match=match, agent=agent)
            plan = self._plan_action(match, agent, action_index)
            reason = "unreachable_action_fallback"
            fallback = True

        deadline_miss = False
        if (
            plan is not None
            and plan.reachable
            and self.config.action_deadline_ticks is not None
            and plan.tick_count > self.config.action_deadline_ticks
        ):
            deadline_miss = True
            self.diagnostics.deadline_misses += 1
            action_index = self._fallback_action(mask, exclude=action_index, match=match, agent=agent)
            plan = self._plan_action(match, agent, action_index)
            reason = "deadline_fallback"
            fallback = True

        if fallback:
            self.diagnostics.fallback_actions += 1
        if plan is not None:
            self.diagnostics.planned_input_ticks += plan.tick_count

        placement = action_to_placement(action_index) if action_index is not None else None
        record = RealtimeDecisionRecord(
            tick=match.tick if requested_tick is None else requested_tick,
            action_index=action_index,
            axis_x=None if placement is None else placement.axis_x,
            rotation=None if placement is None else placement.rotation.name,
            reachable=bool(plan is not None and plan.reachable),
            plan_ticks=0 if plan is None else plan.tick_count,
            inference_latency_ticks=int(effective_latency or 0),
            timeout=timeout,
            deadline_miss=deadline_miss,
            fallback=fallback,
            reason=reason if plan is None or plan.reachable else (plan.reason or reason),
            policy_elapsed_seconds=elapsed,
        )
        self.diagnostics.last_decision = record
        ready_tick = match.tick + int(effective_latency or 0)
        if plan is not None and not plan.reachable:
            plan = None
        return _PendingDecision(ready_tick=ready_tick, record=record, plan=plan)

    def _plan_action(
        self,
        match: RealtimeVersusMatch,
        agent: str,
        action_index: int | None,
    ) -> PlannedPlacement | None:
        if action_index is None:
            return None
        return plan_placement_action(
            match.player_states[agent].simulator,
            action_to_placement(action_index),
            timing=self.timing,
            max_expanded_states=self.config.max_plan_expanded_states,
        )

    def _fallback_action(
        self,
        mask: Sequence[bool],
        *,
        exclude: int | None = None,
        match: RealtimeVersusMatch | None = None,
        agent: str | None = None,
    ) -> int | None:
        configured = self.config.fallback_action_index
        if configured is not None and configured != exclude and mask[configured]:
            return configured
        planned = self._fallback_action_by_plan(mask, exclude=exclude, match=match, agent=agent)
        if planned is not None:
            return planned
        for index, allowed in enumerate(mask):
            if allowed and index != exclude:
                return index
        for index, allowed in enumerate(mask):
            if allowed:
                return index
        return None

    def _fallback_action_by_plan(
        self,
        mask: Sequence[bool],
        *,
        exclude: int | None,
        match: RealtimeVersusMatch | None,
        agent: str | None,
    ) -> int | None:
        if match is None or agent is None:
            return None
        candidates = []
        center = (GRID_WIDTH - 1) / 2.0
        for index, allowed in enumerate(mask):
            if not allowed or index == exclude:
                continue
            plan = self._plan_action(match, agent, index)
            if plan is None or not plan.reachable:
                continue
            placement = action_to_placement(index)
            candidates.append(
                (
                    plan.tick_count,
                    abs(float(placement.axis_x) - center),
                    int(index),
                )
            )
        if not candidates:
            return None
        deadline = self.config.action_deadline_ticks
        if deadline is not None:
            within_deadline = [item for item in candidates if item[0] <= deadline]
            if within_deadline:
                candidates = within_deadline
        _, _, index = min(candidates)
        return index

    def _should_abort_active_plan(self, simulator) -> bool:
        if not self.config.abort_unreachable_active_plan:
            return False
        if self._active_plan is None or self._active_action_index is None:
            return False
        if simulator.game.state != "control" or simulator.game.game_over:
            return False
        if simulator.tick - self._last_replan_check_tick < self.config.replan_check_interval_ticks:
            return False
        self._last_replan_check_tick = simulator.tick
        probe = plan_placement_action(
            simulator,
            self._active_plan.action,
            timing=self.timing,
            max_expanded_states=self.config.max_plan_expanded_states,
        )
        return not probe.reachable

    def _emit_active_plan_input(self) -> TickInput:
        if self._active_plan is None or self._input_cursor >= len(self._active_plan.inputs):
            self._active_plan = None
            self._active_action_index = None
            self._input_cursor = 0
            self.diagnostics.idle_ticks += 1
            self.diagnostics.last_event = "plan_complete"
            return TickInput()
        tick_input = self._active_plan.inputs[self._input_cursor]
        self._input_cursor += 1
        self.diagnostics.emitted_input_ticks += 1
        self.diagnostics.last_emitted_input = tick_input.to_json()
        self.diagnostics.last_event = "executing_plan"
        return tick_input


class RealtimePuyoEnv:
    """PettingZoo-like fixed-tick realtime environment for controller rollouts."""

    metadata = {"name": "puyo_realtime_v0", "render_modes": []}
    possible_agents = REALTIME_AGENTS

    def __init__(
        self,
        seed: int | None = None,
        max_ticks: int = 10_000,
        timing: RealtimeTimingConfig | None = None,
        reward_config: RealtimeRewardConfig | None = None,
        include_action_mask_in_observation: bool = False,
        use_reachable_action_mask: bool = False,
    ):
        self.base_seed = seed
        self.max_ticks = int(max_ticks)
        self.timing = timing or DEFAULT_REALTIME_TIMING
        self.reward_config = reward_config or RealtimeRewardConfig()
        self.include_action_mask_in_observation = include_action_mask_in_observation
        self.use_reachable_action_mask = use_reachable_action_mask
        self.match = RealtimeVersusMatch(seed=seed, timing=self.timing)
        self.agents: list[str] = []
        self._episode_index = 0
        self._episode_returns = {agent: 0.0 for agent in self.possible_agents}
        self._max_chain_counts = {agent: 0 for agent in self.possible_agents}
        self._last_infos: dict[str, dict[str, Any]] = {}

    @property
    def player_states(self):
        return self.match.player_states

    @property
    def step_count(self) -> int:
        return self.match.tick

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        _ = options
        effective_seed = self._effective_seed(seed)
        self.match.reset(seed=effective_seed)
        self.agents = list(self.possible_agents)
        self._episode_returns = {agent: 0.0 for agent in self.possible_agents}
        self._max_chain_counts = {agent: 0 for agent in self.possible_agents}
        self._episode_index += 1
        observations, infos = self._observations_and_infos()
        self._last_infos = infos
        return observations, infos

    def step(self, inputs: Mapping[str, TickInput] | None = None):
        if not self.agents:
            raise RuntimeError("step() was called after episode termination")
        result = self.match.step(inputs)
        rewards: dict[str, float] = {}
        components: dict[str, dict[str, float]] = {}
        for agent in self.possible_agents:
            component = realtime_reward_components(result, agent, self.reward_config)
            self._max_chain_counts[agent] = max(
                self._max_chain_counts[agent],
                int(component["chain_count"]),
            )
            reward = component["total_reward"]
            rewards[agent] = reward
            components[agent] = component

        terminal = any(
            self.match.player_states[agent].simulator.game.game_over
            for agent in self.possible_agents
        )
        truncated = self.match.tick >= self.max_ticks and not terminal
        winner = result.winner if terminal else None
        if truncated:
            winner = _winner_from_scores(self.match)

        episode_done = terminal or truncated
        if episode_done:
            for agent in self.possible_agents:
                terminal_reward = _terminal_reward(agent, winner, self.reward_config)
                rewards[agent] += terminal_reward
                components[agent]["terminal_reward"] = terminal_reward
                components[agent]["total_reward"] = rewards[agent]

        for agent in self.possible_agents:
            self._episode_returns[agent] += rewards[agent]

        observations, infos = self._observations_and_infos()
        terminations = {agent: bool(terminal) for agent in self.possible_agents}
        truncations = {agent: bool(truncated) for agent in self.possible_agents}
        for agent in self.possible_agents:
            infos[agent].update(
                {
                    "reward_components": components[agent],
                    "match_result": result,
                    "winner": winner,
                    "tick_count": self.match.tick,
                    "max_chain_count": self._max_chain_counts[agent],
                }
            )
            if episode_done:
                opponent = _opponent(agent)
                infos[agent]["episode"] = {
                    "r": self._episode_returns[agent],
                    "l": self.match.tick,
                    "score": self.match.player_states[agent].simulator.game.score,
                    "opponent_score": self.match.player_states[opponent].simulator.game.score,
                    "winner": winner,
                    "win": 0.5 if winner is None else float(winner == agent),
                    "sent_ojama": self.match.player_states[agent].sent_ojama_total,
                    "generated_ojama": self.match.player_states[agent].generated_ojama_total,
                    "canceled_ojama": self.match.player_states[agent].canceled_ojama_total,
                    "received_ojama": self.match.player_states[agent].received_ojama_total,
                    "max_chain": self._max_chain_counts[agent],
                }
        if episode_done:
            self.agents = []
        self._last_infos = infos
        return observations, rewards, terminations, truncations, infos

    def _effective_seed(self, seed: int | None) -> int | None:
        if seed is not None:
            return seed
        if self.base_seed is None:
            return None
        return self.base_seed + self._episode_index

    def _observations_and_infos(self):
        observations = {
            agent: build_realtime_observation(
                self.match,
                agent,
                max_ticks=self.max_ticks,
                include_action_mask=self.include_action_mask_in_observation,
            )
            for agent in self.possible_agents
        }
        infos = {
            agent: build_realtime_info(
                self.match,
                agent,
                max_ticks=self.max_ticks,
                use_reachable_action_mask=self.use_reachable_action_mask,
            )
            for agent in self.possible_agents
        }
        for agent in self.possible_agents:
            infos[agent]["max_chain_count"] = self._max_chain_counts[agent]
        return observations, infos

    def close(self):
        return None


def realtime_reachable_action_mask(
    simulator,
    *,
    timing: RealtimeTimingConfig | None = None,
    max_expanded_states: int = 2_000,
):
    """Return the placement actions reachable from the active realtime state."""

    numpy = _require_numpy()
    if simulator.game.state != "control" or simulator.game.game_over:
        return numpy.zeros(NUM_ACTIONS, dtype=numpy.bool_)
    return numpy.asarray(
        [
            plan_placement_action(
                simulator,
                action,
                timing=timing,
                max_expanded_states=max_expanded_states,
            ).reachable
            for action in PLACEMENT_ACTIONS
        ],
        dtype=numpy.bool_,
    )


def build_realtime_observation(
    match: RealtimeVersusMatch,
    agent: str,
    *,
    max_ticks: int = 10_000,
    include_action_mask: bool = False,
) -> dict[str, Any]:
    """Build a placement-policy compatible realtime observation."""

    numpy = _require_numpy()
    state = match.player_states[agent]
    opponent_state = match.player_states[_opponent(agent)]
    own_board = encode_board(state.simulator.game)
    opponent_board = encode_board(opponent_state.simulator.game)
    observation = {
        "board": numpy.concatenate([own_board, opponent_board], axis=0).astype(numpy.float32, copy=False),
        "own_board": own_board,
        "opponent_board": opponent_board,
        "next_pairs": encode_next_pairs(state.simulator.game),
        "scalars": encode_scalars(
            state.simulator.game,
            step_count=match.tick,
            max_steps=max_ticks,
            pending_ojama=state.pending_ojama,
            sent_ojama=state.sent_ojama_total,
        ),
        "realtime_scalars": encode_realtime_scalars(match, agent, max_ticks=max_ticks),
        "schema_version": REALTIME_OBSERVATION_SCHEMA_VERSION,
    }
    if include_action_mask:
        observation["action_mask"] = realtime_reachable_action_mask(state.simulator).astype(numpy.int8)
    return observation


def encode_realtime_scalars(
    match: RealtimeVersusMatch,
    agent: str,
    *,
    max_ticks: int = 10_000,
    dtype: Any | None = None,
):
    numpy = _require_numpy()
    state = match.player_states[agent]
    opponent_state = match.player_states[_opponent(agent)]
    game = state.simulator.game
    active_x = 0.0 if game.current_puyo_1 is None else game.puyo_x / float(max(1, GRID_WIDTH - 1))
    active_y = 0.0 if game.current_puyo_1 is None else game.puyo_y / float(max(1, GRID_HEIGHT - 1))
    rotation = 0.0 if game.current_puyo_1 is None else _rotation_scalar(game.puyo_rot)
    incoming_ticks = _incoming_ticks(match, agent)
    return numpy.asarray(
        [
            min(float(match.tick) / float(max(1, max_ticks)), 1.0),
            active_x,
            active_y,
            rotation,
            min(float(game.ground_frame_count) / float(max(1, match.timing.lock_frame_limit)), 1.0),
            min(float(state.pending_ojama) / 30.0, 1.0),
            1.0 if incoming_ticks is None else min(float(incoming_ticks) / float(max(1, match.timing.attack_delay_ticks)), 1.0),
            _PHASE_CODES.get(opponent_state.simulator.game.state, 1.0),
        ],
        dtype=dtype or numpy.float32,
    )


def build_realtime_info(
    match: RealtimeVersusMatch,
    agent: str,
    *,
    max_ticks: int = 10_000,
    use_reachable_action_mask: bool = True,
) -> dict[str, Any]:
    state = match.player_states[agent]
    opponent = _opponent(agent)
    opponent_state = match.player_states[opponent]
    action_mask = (
        realtime_reachable_action_mask(state.simulator)
        if use_reachable_action_mask
        else _turn_based_action_mask(state.simulator)
    )
    incoming_ticks = _incoming_ticks(match, agent)
    opponent_incoming_ticks = _incoming_ticks(match, opponent)
    return {
        "action_mask": action_mask,
        "action_mask_source": "reachable_planner" if use_reachable_action_mask else "placement_legal",
        "schema_version": REALTIME_OBSERVATION_SCHEMA_VERSION,
        "action_contract_version": REALTIME_ACTION_CONTRACT_VERSION,
        "score": state.simulator.game.score,
        "opponent_score": opponent_state.simulator.game.score,
        "pending_ojama": state.pending_ojama,
        "incoming_ojama": state.pending_ojama,
        "incoming_ticks": 0 if incoming_ticks is None else incoming_ticks,
        "incoming_turns": _ticks_to_turns(match, incoming_ticks),
        "incoming_arrival_tick": _next_arrival_tick(match, agent),
        "incoming_attack_packets": _attack_packets(match, agent),
        "sent_ojama_total": state.sent_ojama_total,
        "generated_ojama_total": state.generated_ojama_total,
        "canceled_ojama_total": state.canceled_ojama_total,
        "received_ojama_total": state.received_ojama_total,
        "simulator": _placement_simulator_snapshot(state.simulator.game),
        "realtime_simulator": state.simulator,
        "opponent_pending_ojama": opponent_state.pending_ojama,
        "opponent_incoming_ticks": 0 if opponent_incoming_ticks is None else opponent_incoming_ticks,
        "opponent_incoming_turns": _ticks_to_turns(match, opponent_incoming_ticks),
        "opponent_sent_ojama_total": opponent_state.sent_ojama_total,
        "opponent_received_ojama_total": opponent_state.received_ojama_total,
        "opponent_simulator": _placement_simulator_snapshot(opponent_state.simulator.game),
        "opponent_realtime_simulator": opponent_state.simulator,
        "own_phase": state.simulator.game.state,
        "opponent_phase": opponent_state.simulator.game.state,
        "active_pair": state.simulator.snapshot().active_pair,
        "active_position": state.simulator.snapshot().active_position,
        "held_actions": state.simulator.snapshot().held_actions,
        "step_count": match.tick,
        "tick_count": match.tick,
        "max_steps": max_ticks,
        "max_ticks": max_ticks,
    }


def realtime_reward_components(
    match_result: RealtimeMatchTickResult,
    agent: str,
    config: RealtimeRewardConfig | None = None,
) -> dict[str, float]:
    reward_config = config or RealtimeRewardConfig()
    step_result = match_result.player_results[agent]
    score_delta = 0
    chain_count = 0
    for event in step_result.events:
        if event.type == "resolution_complete":
            score_delta += int(event.data.get("score_delta", 0))
            chain_count = max(chain_count, int(event.data.get("chain_count", 0)))
    if score_delta == 0:
        score_delta = max(0, int(step_result.score_delta))
    attack = match_result.attack_diagnostics.get(agent, {})
    dropped = int(match_result.dropped_ojama.get(agent, 0))
    score_reward = reward_config.score_reward * score_to_ojama(
        score_delta,
        reward_config.target_score_per_ojama,
    )
    attack_reward = reward_config.attack_reward * float(attack.get("outgoing", 0))
    chain_reward = reward_config.chain_bonus * float(chain_count)
    survival_reward = reward_config.survival_bonus
    garbage_penalty = -reward_config.garbage_penalty * float(dropped)
    total = score_reward + attack_reward + chain_reward + survival_reward + garbage_penalty
    return {
        "score_delta": float(score_delta),
        "chain_count": float(chain_count),
        "attack_generated": float(attack.get("generated", 0)),
        "attack_canceled": float(attack.get("canceled", 0)),
        "attack_outgoing": float(attack.get("outgoing", 0)),
        "garbage_received": float(dropped),
        "score_reward": score_reward,
        "attack_reward": attack_reward,
        "chain_reward": chain_reward,
        "survival_reward": survival_reward,
        "garbage_penalty": garbage_penalty,
        "deadline_penalty": 0.0,
        "input_failure_penalty": 0.0,
        "terminal_reward": 0.0,
        "total_reward": total,
    }


def realtime_checkpoint_metadata(
    *,
    native_realtime: bool = False,
    observation_schema_version: str = REALTIME_OBSERVATION_SCHEMA_VERSION,
    action_contract_version: str = REALTIME_ACTION_CONTRACT_VERSION,
) -> dict[str, Any]:
    """Return metadata stored with realtime-compatible checkpoints."""

    return {
        "policy_contract": "realtime_native" if native_realtime else "turn_based_placement_adapter",
        "observation_schema_version": observation_schema_version,
        "action_contract_version": action_contract_version,
        "turn_based_adapter_supported": not native_realtime,
    }


def validate_realtime_checkpoint_metadata(
    checkpoint: Mapping[str, Any],
    *,
    allow_turn_based_adapter: bool = True,
) -> dict[str, Any]:
    """Validate a checkpoint contract and report how realtime should load it."""

    metadata = checkpoint.get("realtime_policy")
    if metadata is None and isinstance(checkpoint.get("metadata"), Mapping):
        metadata = checkpoint["metadata"].get("realtime_policy")
    if isinstance(metadata, Mapping):
        schema = metadata.get("observation_schema_version")
        action_contract = metadata.get("action_contract_version")
        if schema != REALTIME_OBSERVATION_SCHEMA_VERSION:
            raise ValueError(f"unsupported realtime observation schema: {schema!r}")
        if action_contract != REALTIME_ACTION_CONTRACT_VERSION:
            raise ValueError(f"unsupported realtime action contract: {action_contract!r}")
        return {"mode": "realtime_native", "metadata": dict(metadata)}

    if allow_turn_based_adapter and _looks_like_turn_based_checkpoint(checkpoint):
        return {
            "mode": "turn_based_placement_adapter",
            "metadata": realtime_checkpoint_metadata(native_realtime=False),
        }

    raise ValueError("checkpoint has no realtime contract and cannot be adapted")


def _bool_mask(mask: Any) -> list[bool]:
    if mask is None:
        return [False] * NUM_ACTIONS
    return [bool(value) for value in list(mask)]


def _turn_based_action_mask(simulator):
    numpy = _require_numpy()
    placement_simulator = _placement_simulator_snapshot(simulator.game)
    legal = set(placement_simulator.legal_actions())
    return numpy.asarray([action in legal for action in PLACEMENT_ACTIONS], dtype=numpy.bool_)


def _placement_simulator_snapshot(game) -> HeadlessPuyoSimulator:
    return HeadlessPuyoSimulator(game_state=copy.deepcopy(game))


def _incoming_ticks(match: RealtimeVersusMatch, agent: str) -> int | None:
    arrival = _next_arrival_tick(match, agent)
    if arrival is None:
        return None
    return max(0, int(arrival) - int(match.tick))


def _next_arrival_tick(match: RealtimeVersusMatch, agent: str) -> int | None:
    attacks = match.player_states[agent].incoming_attacks
    if not attacks:
        return None
    return min(packet.arrival_tick for packet in attacks)


def _ticks_to_turns(match: RealtimeVersusMatch, ticks: int | None) -> int:
    if ticks is None:
        return 0
    return int(math.ceil(max(0, ticks) / float(max(1, match.timing.lock_frame_limit))))


def _attack_packets(match: RealtimeVersusMatch, agent: str) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "amount": packet.amount,
            "arrival_tick": packet.arrival_tick,
            "ticks_to_arrival": max(0, packet.arrival_tick - match.tick),
            "source_agent": packet.source_agent,
            "created_tick": packet.created_tick,
        }
        for packet in sorted(match.player_states[agent].incoming_attacks, key=lambda item: item.arrival_tick)
    )


def _rotation_scalar(rotation: Direction) -> float:
    order = (Direction.UP, Direction.RIGHT, Direction.DOWN, Direction.LEFT)
    return order.index(rotation) / float(len(order) - 1)


def _opponent(agent: str) -> str:
    if agent == "player_0":
        return "player_1"
    if agent == "player_1":
        return "player_0"
    raise KeyError(f"unknown agent: {agent}")


def _winner_from_scores(match: RealtimeVersusMatch) -> str | None:
    score_0 = match.player_states["player_0"].simulator.game.score
    score_1 = match.player_states["player_1"].simulator.game.score
    if score_0 > score_1:
        return "player_0"
    if score_1 > score_0:
        return "player_1"
    return None


def _terminal_reward(agent: str, winner: str | None, config: RealtimeRewardConfig) -> float:
    if winner is None:
        return -config.draw_penalty
    if winner == agent:
        return config.win_reward
    return -config.loss_penalty


def _looks_like_turn_based_checkpoint(checkpoint: Mapping[str, Any]) -> bool:
    if "model_state_dict" in checkpoint:
        return True
    return any(str(key).endswith("cnn.0.weight") for key in checkpoint.keys())
