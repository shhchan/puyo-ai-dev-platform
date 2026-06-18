"""Realtime PPO smoke trainer backed by the fixed-tick environment."""

from __future__ import annotations

import copy
import csv
import json
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

try:
    import numpy as np
    import torch
    from torch import optim
except ImportError:  # pragma: no cover - dependency guard
    np = None
    torch = None
    optim = None

from agents.networks import PuyoActorCritic, VECTOR_FEATURE_DIM
from puyo_env.action_planner import plan_placement_action
from puyo_env.actions import NUM_ACTIONS, action_to_placement
from puyo_env.realtime_ai import (
    RealtimeDecisionConfig,
    RealtimePolicyController,
    RealtimePuyoEnv,
    RealtimeRewardConfig,
    realtime_checkpoint_metadata,
    validate_realtime_checkpoint_metadata,
)
from selfplay.policies import make_policy
from src.core.realtime import TickInput
from train.artifacts import attach_checkpoint_schema, git_commit, validate_artifact_manifest, write_artifact_manifest
from train.restore import (
    RestoreError,
    assert_resume_config_compatible,
    capture_rng_state,
    checkpoint_state_hash,
    load_training_checkpoint,
    restore_rng_state,
)


@dataclass
class RealtimePPOConfig:
    seed: int = 1
    total_timesteps: int = 256
    num_envs: int = 1
    num_steps: int = 16
    learning_rate: float = 2.5e-4
    gamma: float = 0.99
    gae_lambda: float = 0.95
    update_epochs: int = 2
    minibatch_size: int = 16
    clip_coef: float = 0.2
    ent_coef: float = 0.01
    vf_coef: float = 0.5
    max_grad_norm: float = 0.5
    max_ticks: int = 240
    opponent_policy: str = "random"
    opponent_checkpoint_path: str = ""
    log_dir: str = "runs/realtime_ppo"
    checkpoint_path: str = ""
    resume_checkpoint_path: str = ""
    device: str = "cpu"
    run_name: str = "realtime_ppo"
    run_id: str = ""
    checkpoint_interval_updates: int = 0
    keep_best_checkpoint: bool = True
    best_checkpoint_metric: str = "mean_win_rate"
    rolling_window_episodes: int = 10
    use_reachable_action_mask: bool = False
    opponent_inference_latency_ticks: int = 0
    opponent_timeout_ticks: int = 0
    learner_action_deadline_ticks: int = 48
    learner_fallback_action_index: int = 0
    decision_tick_limit: int = 240
    max_plan_expanded_states: int = 2_000
    eval_games: int = 1
    eval_max_ticks: int = 160
    reward_target_score_per_ojama: int = 70
    reward_score_reward: float = 0.25
    reward_attack_reward: float = 0.5
    reward_chain_bonus: float = 0.05
    reward_survival_bonus: float = 0.001
    reward_garbage_penalty: float = 0.02
    reward_deadline_miss_penalty: float = 0.25
    reward_input_failure_penalty: float = 1.0
    reward_win_reward: float = 10.0
    reward_loss_penalty: float = 10.0
    reward_draw_penalty: float = 1.0


@dataclass(frozen=True)
class RealtimeArtifactPaths:
    run_id: str
    run_dir: Path
    metrics_path: Path
    config_path: Path
    metadata_path: Path
    summary_path: Path
    manifest_path: Path
    checkpoint_dir: Path
    checkpoint_path: Path
    best_checkpoint_path: Path


@dataclass(frozen=True)
class RealtimeRolloutStep:
    observations: list[dict[str, Any]]
    infos: list[dict[str, Any]]
    rewards: list[float]
    dones: list[bool]
    episodes: list[dict[str, Any]]


def _require_deps() -> None:
    if np is None or torch is None or optim is None:
        raise ImportError("realtime PPO training requires numpy and torch. Install requirements.txt.")


def _safe_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value.strip())
    return safe.strip("-") or "realtime_ppo"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resolve_artifact_paths(cfg: RealtimePPOConfig) -> RealtimeArtifactPaths:
    run_id = _safe_name(cfg.run_id) if cfg.run_id else f"{_safe_name(cfg.run_name)}-seed{cfg.seed}-{_utc_timestamp()}"
    run_dir = Path(cfg.log_dir) / run_id
    checkpoint_dir = run_dir / "checkpoints"
    checkpoint_path = Path(cfg.checkpoint_path) if cfg.checkpoint_path else checkpoint_dir / "latest.pt"
    return RealtimeArtifactPaths(
        run_id=run_id,
        run_dir=run_dir,
        metrics_path=run_dir / "metrics.csv",
        config_path=run_dir / "config.yaml",
        metadata_path=run_dir / "metadata.json",
        summary_path=run_dir / "summary.json",
        manifest_path=run_dir / "artifact_manifest.json",
        checkpoint_dir=checkpoint_dir,
        checkpoint_path=checkpoint_path,
        best_checkpoint_path=checkpoint_dir / "best.pt",
    )


def _write_artifact_metadata(cfg: RealtimePPOConfig, paths: RealtimeArtifactPaths) -> None:
    resolved = {
        "run_id": paths.run_id,
        "run_dir": str(paths.run_dir),
        "metrics_path": str(paths.metrics_path),
        "manifest_path": str(paths.manifest_path),
        "checkpoint_path": str(paths.checkpoint_path),
        "best_checkpoint_path": str(paths.best_checkpoint_path),
    }
    config_dump = {"config": asdict(cfg), "resolved": resolved}
    paths.config_path.write_text(yaml.safe_dump(config_dump, sort_keys=True), encoding="utf-8")

    metadata = {
        "run_id": paths.run_id,
        "created_at_utc": _utc_timestamp(),
        "git_commit": git_commit(),
        "config_path": str(paths.config_path),
        "metrics_path": str(paths.metrics_path),
        "checkpoint_path": str(paths.checkpoint_path),
        "opponent_policy": cfg.opponent_policy,
        "opponent_checkpoint_path": cfg.opponent_checkpoint_path,
        "realtime_policy": realtime_checkpoint_metadata(native_realtime=True),
    }
    paths.metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")


def _make_reward_config(cfg: RealtimePPOConfig) -> RealtimeRewardConfig:
    return RealtimeRewardConfig(
        target_score_per_ojama=cfg.reward_target_score_per_ojama,
        score_reward=cfg.reward_score_reward,
        attack_reward=cfg.reward_attack_reward,
        chain_bonus=cfg.reward_chain_bonus,
        survival_bonus=cfg.reward_survival_bonus,
        garbage_penalty=cfg.reward_garbage_penalty,
        deadline_miss_penalty=cfg.reward_deadline_miss_penalty,
        input_failure_penalty=cfg.reward_input_failure_penalty,
        win_reward=cfg.reward_win_reward,
        loss_penalty=cfg.reward_loss_penalty,
        draw_penalty=cfg.reward_draw_penalty,
    )


def stack_realtime_observations(observations: Sequence[Mapping[str, Any]], device: str):
    """Stack realtime placement observations for the shared actor-critic network."""

    _require_deps()
    boards = np.stack([obs["board"] for obs in observations])
    next_pairs = np.stack([obs["next_pairs"].reshape(-1) for obs in observations])
    scalars = np.stack([obs["scalars"] for obs in observations])
    vectors = np.concatenate([next_pairs, scalars], axis=1)
    return {
        "board": torch.as_tensor(boards, dtype=torch.float32, device=device),
        "vector_features": torch.as_tensor(vectors, dtype=torch.float32, device=device),
    }


def stack_realtime_masks(infos: Sequence[Mapping[str, Any]], device: str):
    _require_deps()
    masks = np.stack([info["action_mask"] for info in infos])
    return torch.as_tensor(masks, dtype=torch.bool, device=device)


def compute_gae(
    rewards,
    dones,
    values,
    next_done,
    next_value,
    *,
    gamma: float,
    gae_lambda: float,
):
    """Compute generalized advantage estimates for rollout tensors."""

    advantages = torch.zeros_like(rewards, device=rewards.device)
    lastgaelam = 0.0
    for t in reversed(range(rewards.shape[0])):
        if t == rewards.shape[0] - 1:
            nextnonterminal = 1.0 - next_done
            nextvalues = next_value
        else:
            nextnonterminal = 1.0 - dones[t + 1]
            nextvalues = values[t + 1]
        delta = rewards[t] + gamma * nextvalues * nextnonterminal - values[t]
        lastgaelam = delta + gamma * gae_lambda * nextnonterminal * lastgaelam
        advantages[t] = lastgaelam
    return advantages, advantages + values


class RealtimeRolloutAdapter:
    """Convert placement actions into fixed-tick realtime PPO transitions."""

    def __init__(self, cfg: RealtimePPOConfig):
        _require_deps()
        self.cfg = copy.deepcopy(cfg)
        self.reward_config = _make_reward_config(cfg)
        self.envs = [
            RealtimePuyoEnv(
                seed=cfg.seed + env_index * 10_000,
                max_ticks=cfg.max_ticks,
                reward_config=self.reward_config,
                use_reachable_action_mask=cfg.use_reachable_action_mask,
            )
            for env_index in range(cfg.num_envs)
        ]
        self.opponent_controllers = [
            RealtimePolicyController(
                make_policy(
                    cfg.opponent_policy,
                    seed=cfg.seed + 50_000 + env_index,
                    checkpoint_path=cfg.opponent_checkpoint_path or None,
                    deterministic=True,
                ),
                config=RealtimeDecisionConfig(
                    inference_latency_ticks=cfg.opponent_inference_latency_ticks,
                    timeout_ticks=cfg.opponent_timeout_ticks or None,
                    use_reachable_action_mask=cfg.use_reachable_action_mask,
                    max_plan_expanded_states=cfg.max_plan_expanded_states,
                ),
            )
            for env_index in range(cfg.num_envs)
        ]
        self.observations: list[dict[str, Any]] = []
        self.infos: list[dict[str, Any]] = []
        self._player_observations: list[dict[str, dict[str, Any]]] = []
        self._player_infos: list[dict[str, dict[str, Any]]] = []
        self._episode_returns = [0.0 for _ in range(cfg.num_envs)]
        self._episode_decisions = [0 for _ in range(cfg.num_envs)]
        self._episode_deadline_misses = [0 for _ in range(cfg.num_envs)]
        self._episode_input_failures = [0 for _ in range(cfg.num_envs)]
        self._episode_latency_ticks = [0 for _ in range(cfg.num_envs)]
        self.reset()

    def reset(self) -> None:
        self.observations = []
        self.infos = []
        self._player_observations = []
        self._player_infos = []
        for env_index, env in enumerate(self.envs):
            observations, infos = env.reset(seed=self.cfg.seed + env_index)
            self.opponent_controllers[env_index].reset()
            self._player_observations.append(observations)
            self._player_infos.append(infos)
            self.observations.append(copy.deepcopy(observations["player_0"]))
            self.infos.append(copy.deepcopy(infos["player_0"]))
        self._episode_returns = [0.0 for _ in range(self.cfg.num_envs)]
        self._episode_decisions = [0 for _ in range(self.cfg.num_envs)]
        self._episode_deadline_misses = [0 for _ in range(self.cfg.num_envs)]
        self._episode_input_failures = [0 for _ in range(self.cfg.num_envs)]
        self._episode_latency_ticks = [0 for _ in range(self.cfg.num_envs)]

    def state_dict(self) -> dict[str, Any]:
        return {
            "envs": copy.deepcopy(self.envs),
            "opponent_controllers": copy.deepcopy(self.opponent_controllers),
            "observations": copy.deepcopy(self.observations),
            "infos": copy.deepcopy(self.infos),
            "player_observations": copy.deepcopy(self._player_observations),
            "player_infos": copy.deepcopy(self._player_infos),
            "episode_returns": list(self._episode_returns),
            "episode_decisions": list(self._episode_decisions),
            "episode_deadline_misses": list(self._episode_deadline_misses),
            "episode_input_failures": list(self._episode_input_failures),
            "episode_latency_ticks": list(self._episode_latency_ticks),
        }

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        self.envs = copy.deepcopy(state["envs"])
        self.opponent_controllers = copy.deepcopy(state["opponent_controllers"])
        self.observations = copy.deepcopy(state["observations"])
        self.infos = copy.deepcopy(state["infos"])
        self._player_observations = copy.deepcopy(state["player_observations"])
        self._player_infos = copy.deepcopy(state["player_infos"])
        self._episode_returns = list(state["episode_returns"])
        self._episode_decisions = list(state["episode_decisions"])
        self._episode_deadline_misses = list(state["episode_deadline_misses"])
        self._episode_input_failures = list(state["episode_input_failures"])
        self._episode_latency_ticks = list(state["episode_latency_ticks"])

    def close(self) -> None:
        for env in self.envs:
            env.close()

    def step(self, actions: Sequence[int]) -> RealtimeRolloutStep:
        rewards: list[float] = []
        dones: list[bool] = []
        episodes: list[dict[str, Any]] = []
        for env_index, action_index in enumerate(actions):
            reward, done, info, episode = self._step_one(env_index, int(action_index))
            rewards.append(float(reward))
            dones.append(bool(done))
            if episode is not None:
                episodes.append(episode)
            metrics = info.get("trainer_step_metrics")
            if metrics is not None:
                self._player_infos[env_index]["player_0"]["trainer_step_metrics"] = metrics
            self._player_infos[env_index]["player_0"]["trainer_done"] = done
            self.observations[env_index] = copy.deepcopy(self._player_observations[env_index]["player_0"])
            self.infos[env_index] = copy.deepcopy(self._player_infos[env_index]["player_0"])
        return RealtimeRolloutStep(
            observations=copy.deepcopy(self.observations),
            infos=copy.deepcopy(self.infos),
            rewards=rewards,
            dones=dones,
            episodes=episodes,
        )

    def _step_one(self, env_index: int, action_index: int) -> tuple[float, bool, dict[str, Any], dict[str, Any] | None]:
        env = self.envs[env_index]
        controller = self.opponent_controllers[env_index]
        current_info = self._player_infos[env_index]["player_0"]
        mask = [bool(value) for value in list(current_info.get("action_mask", []))]
        selected, plan, input_failure, deadline_miss = self._plan_or_fallback(
            env,
            action_index,
            mask,
        )
        plan_inputs = plan.inputs if plan is not None and plan.reachable else ()
        plan_ticks = len(plan_inputs)
        self._episode_decisions[env_index] += 1
        self._episode_deadline_misses[env_index] += int(deadline_miss)
        self._episode_input_failures[env_index] += int(input_failure)
        self._episode_latency_ticks[env_index] += plan_ticks

        total_reward = 0.0
        done = False
        last_infos = self._player_infos[env_index]
        cursor = 0
        ticks_elapsed = 0
        for _ in range(max(1, self.cfg.decision_tick_limit)):
            learner_input = plan_inputs[cursor] if cursor < len(plan_inputs) else TickInput()
            if cursor < len(plan_inputs):
                cursor += 1
            opponent_input = controller.next_input(
                env.match,
                "player_1",
                self._player_observations[env_index]["player_1"],
                self._player_infos[env_index]["player_1"],
            )
            observations, rewards, terminations, truncations, infos = env.step(
                {
                    "player_0": learner_input,
                    "player_1": opponent_input,
                }
            )
            ticks_elapsed += 1
            total_reward += float(rewards["player_0"])
            self._player_observations[env_index] = observations
            self._player_infos[env_index] = infos
            last_infos = infos
            done = bool(terminations["player_0"] or truncations["player_0"])
            if done:
                break
            player_state = env.match.player_states["player_0"].simulator.game.state
            if cursor >= len(plan_inputs) and player_state == "control":
                break

        penalty = 0.0
        if input_failure:
            penalty -= self.cfg.reward_input_failure_penalty
        if deadline_miss:
            penalty -= self.cfg.reward_deadline_miss_penalty
        total_reward += penalty
        self._episode_returns[env_index] += total_reward

        info = last_infos["player_0"]
        info["trainer_step_metrics"] = {
            "selected_action": selected,
            "requested_action": action_index,
            "plan_ticks": plan_ticks,
            "ticks_elapsed": ticks_elapsed,
            "input_failure": input_failure,
            "deadline_miss": deadline_miss,
            "penalty": penalty,
            "opponent_timeouts": controller.diagnostics.timeouts,
            "opponent_deadline_misses": controller.diagnostics.deadline_misses,
            "opponent_unreachable_plans": controller.diagnostics.unreachable_plans,
        }

        episode = None
        if done:
            episode = dict(info.get("episode", {}))
            episode["trainer_return"] = self._episode_returns[env_index]
            episode["decision_count"] = self._episode_decisions[env_index]
            episode["deadline_misses"] = self._episode_deadline_misses[env_index]
            episode["input_failures"] = self._episode_input_failures[env_index]
            episode["latency_ticks"] = self._episode_latency_ticks[env_index]
            episode["opponent_timeouts"] = controller.diagnostics.timeouts
            episode["opponent_deadline_misses"] = controller.diagnostics.deadline_misses
            episode["opponent_unreachable_plans"] = controller.diagnostics.unreachable_plans
            observations, infos = env.reset()
            controller.reset()
            self._player_observations[env_index] = observations
            self._player_infos[env_index] = infos
            self._episode_returns[env_index] = 0.0
            self._episode_decisions[env_index] = 0
            self._episode_deadline_misses[env_index] = 0
            self._episode_input_failures[env_index] = 0
            self._episode_latency_ticks[env_index] = 0

        return total_reward, done, info, episode

    def _plan_or_fallback(self, env, action_index: int, mask: Sequence[bool]):
        input_failure = False
        deadline_miss = False
        selected = action_index
        if not 0 <= selected < NUM_ACTIONS or not (mask and mask[selected]):
            input_failure = True
            selected = self._fallback_action(mask)
        plan = self._plan(env, selected)
        if plan is not None and not plan.reachable:
            input_failure = True
            selected = self._fallback_action(mask, exclude=selected)
            plan = self._plan(env, selected)
        if (
            plan is not None
            and plan.reachable
            and self.cfg.learner_action_deadline_ticks >= 0
            and plan.tick_count > self.cfg.learner_action_deadline_ticks
        ):
            deadline_miss = True
            selected = self._fallback_action(mask, exclude=selected)
            fallback_plan = self._plan(env, selected)
            if fallback_plan is not None:
                plan = fallback_plan
        return selected, plan, input_failure, deadline_miss

    def _plan(self, env, action_index: int | None):
        if action_index is None:
            return None
        return plan_placement_action(
            env.match.player_states["player_0"].simulator,
            action_to_placement(action_index),
            timing=env.timing,
            max_expanded_states=self.cfg.max_plan_expanded_states,
        )

    def _fallback_action(self, mask: Sequence[bool], *, exclude: int | None = None) -> int | None:
        configured = self.cfg.learner_fallback_action_index
        if 0 <= configured < len(mask) and configured != exclude and mask[configured]:
            return configured
        for index, allowed in enumerate(mask):
            if allowed and index != exclude:
                return index
        for index, allowed in enumerate(mask):
            if allowed:
                return index
        return None


def _mean_last(values: Sequence[float], window: int) -> float | None:
    if not values:
        return None
    return float(np.mean(list(values)[-max(1, window):]))


def _current_metric_value(
    metric_name: str,
    *,
    window: int,
    episode_scores: Sequence[float],
    episode_returns: Sequence[float],
    episode_wins: Sequence[float],
    episode_lengths: Sequence[float],
    episode_deadline_misses: Sequence[float],
    episode_input_failures: Sequence[float],
) -> float | None:
    metric_values = {
        "mean_episode_score": _mean_last(episode_scores, window),
        "mean_episode_return": _mean_last(episode_returns, window),
        "mean_win_rate": _mean_last(episode_wins, window),
        "mean_episode_length": _mean_last(episode_lengths, window),
        "mean_deadline_misses": _mean_last(episode_deadline_misses, window),
        "mean_input_failures": _mean_last(episode_input_failures, window),
    }
    if metric_name not in metric_values:
        valid = ", ".join(sorted(metric_values))
        raise ValueError(f"unknown best_checkpoint_metric: {metric_name}; valid values: {valid}")
    return metric_values[metric_name]


def _trainer_state(
    *,
    adapter: RealtimeRolloutAdapter,
    next_done,
    episode_scores: list[float],
    episode_returns: list[float],
    episode_wins: list[float],
    episode_lengths: list[float],
    episode_sent_ojama: list[float],
    episode_received_ojama: list[float],
    episode_deadline_misses: list[float],
    episode_input_failures: list[float],
    episode_latency_ticks: list[float],
    periodic_checkpoints: list[str],
    best_metric_value: float | None,
    best_checkpoint_written: bool,
) -> dict[str, Any]:
    return {
        "adapter": adapter.state_dict(),
        "next_done": next_done.detach().cpu(),
        "episode_scores": list(episode_scores),
        "episode_returns": list(episode_returns),
        "episode_wins": list(episode_wins),
        "episode_lengths": list(episode_lengths),
        "episode_sent_ojama": list(episode_sent_ojama),
        "episode_received_ojama": list(episode_received_ojama),
        "episode_deadline_misses": list(episode_deadline_misses),
        "episode_input_failures": list(episode_input_failures),
        "episode_latency_ticks": list(episode_latency_ticks),
        "periodic_checkpoints": list(periodic_checkpoints),
        "best_metric_value": best_metric_value,
        "best_checkpoint_written": bool(best_checkpoint_written),
    }


def _checkpoint_payload(
    *,
    cfg: RealtimePPOConfig,
    paths: RealtimeArtifactPaths,
    agent,
    optimizer,
    global_step: int,
    episode_scores: list[float],
    episode_returns: list[float],
    episode_wins: list[float],
    episode_lengths: list[float],
    episode_sent_ojama: list[float],
    episode_received_ojama: list[float],
    episode_deadline_misses: list[float],
    episode_input_failures: list[float],
    episode_latency_ticks: list[float],
    checkpoint_kind: str,
    best_metric_value: float | None,
    rng_state: dict[str, Any],
    trainer_state: dict[str, Any],
) -> dict[str, Any]:
    config = asdict(cfg)
    payload = {
        "model_state_dict": agent.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "config": config,
        "run_id": paths.run_id,
        "global_step": global_step,
        "board_shape": agent.board_shape,
        "vector_dim": agent.vector_dim,
        "episode_scores": episode_scores,
        "episode_returns": episode_returns,
        "episode_wins": episode_wins,
        "episode_lengths": episode_lengths,
        "episode_sent_ojama": episode_sent_ojama,
        "episode_received_ojama": episode_received_ojama,
        "episode_deadline_misses": episode_deadline_misses,
        "episode_input_failures": episode_input_failures,
        "episode_latency_ticks": episode_latency_ticks,
        "checkpoint_kind": checkpoint_kind,
        "best_checkpoint_metric": cfg.best_checkpoint_metric,
        "best_metric_value": best_metric_value,
        "realtime_policy": realtime_checkpoint_metadata(native_realtime=True),
        "rng_state": rng_state,
        "trainer_state": trainer_state,
    }
    payload = attach_checkpoint_schema(
        payload,
        trainer_name="realtime_ppo",
        run_id=paths.run_id,
        checkpoint_kind=checkpoint_kind,
        global_step=global_step,
        config=config,
        git_commit=git_commit(),
        seed=cfg.seed,
        parent_checkpoint_path=cfg.resume_checkpoint_path or None,
        environment_progress={
            "episodes": len(episode_scores),
            "rolling_window_episodes": cfg.rolling_window_episodes,
            "max_ticks": cfg.max_ticks,
        },
    )
    payload["state_hash"] = checkpoint_state_hash(payload)
    return payload


def validate_realtime_training_checkpoint(
    checkpoint_path: str | Path,
    *,
    manifest_path: str | Path | None = None,
    map_location: str | Any = "cpu",
    require_exact: bool = False,
) -> dict[str, Any]:
    checkpoint = load_training_checkpoint(
        checkpoint_path,
        map_location=map_location,
        expected_trainer_name="realtime_ppo",
        require_exact=require_exact,
    )
    try:
        validate_realtime_checkpoint_metadata(checkpoint, allow_turn_based_adapter=False)
    except ValueError as exc:
        raise RestoreError(str(exc)) from exc
    if manifest_path is not None:
        manifest_target = Path(manifest_path)
        manifest = json.loads(manifest_target.read_text(encoding="utf-8"))
        run = manifest.get("run", {})
        if run.get("trainer_name") != "realtime_ppo":
            raise RestoreError(f"manifest trainer mismatch: {run.get('trainer_name')} != realtime_ppo")
        manifest_errors = validate_artifact_manifest(manifest, run_dir=manifest_target.parent)
        if manifest_errors:
            raise RestoreError("; ".join(manifest_errors))
    return checkpoint


def train_realtime_ppo(config: RealtimePPOConfig | None = None) -> dict[str, Any]:
    """Train a placement policy through fixed-tick realtime smoke rollouts."""

    _require_deps()
    cfg = config or RealtimePPOConfig()
    if cfg.num_envs <= 0 or cfg.num_steps <= 0:
        raise ValueError("num_envs and num_steps must be positive")
    if cfg.rolling_window_episodes <= 0:
        raise ValueError("rolling_window_episodes must be positive")
    _current_metric_value(
        cfg.best_checkpoint_metric,
        window=cfg.rolling_window_episodes,
        episode_scores=[],
        episode_returns=[],
        episode_wins=[],
        episode_lengths=[],
        episode_deadline_misses=[],
        episode_input_failures=[],
    )

    paths = _resolve_artifact_paths(cfg)
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    paths.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    paths.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    _write_artifact_metadata(cfg, paths)

    random.seed(cfg.seed)
    np.random.seed(cfg.seed)
    torch.manual_seed(cfg.seed)
    torch.backends.cudnn.deterministic = True

    device = torch.device(cfg.device)
    adapter = RealtimeRolloutAdapter(cfg)
    board_shape = tuple(int(dim) for dim in adapter.observations[0]["board"].shape)
    agent = PuyoActorCritic(board_shape=board_shape, vector_dim=VECTOR_FEATURE_DIM).to(device)
    optimizer = optim.Adam(agent.parameters(), lr=cfg.learning_rate, eps=1e-5)

    batch_size = cfg.num_envs * cfg.num_steps
    minibatch_size = min(cfg.minibatch_size, batch_size)
    global_step = 0
    start_time = time.time()
    episode_scores: list[float] = []
    episode_returns: list[float] = []
    episode_wins: list[float] = []
    episode_lengths: list[float] = []
    episode_sent_ojama: list[float] = []
    episode_received_ojama: list[float] = []
    episode_deadline_misses: list[float] = []
    episode_input_failures: list[float] = []
    episode_latency_ticks: list[float] = []
    periodic_checkpoints: list[str] = []
    best_metric_value: float | None = None
    best_checkpoint_written = False
    next_done = torch.zeros(cfg.num_envs, dtype=torch.float32, device=device)
    resume_checkpoint = None
    if cfg.resume_checkpoint_path:
        resume_checkpoint = validate_realtime_training_checkpoint(
            cfg.resume_checkpoint_path,
            map_location=device,
            require_exact=True,
        )
        assert_resume_config_compatible(
            resume_checkpoint,
            asdict(cfg),
            allowed_differences={
                "total_timesteps",
                "log_dir",
                "checkpoint_path",
                "resume_checkpoint_path",
                "run_name",
                "run_id",
            },
        )
        agent.load_state_dict(resume_checkpoint["model_state_dict"])
        optimizer.load_state_dict(resume_checkpoint["optimizer_state_dict"])
        trainer_state = resume_checkpoint["trainer_state"]
        adapter.load_state_dict(trainer_state["adapter"])
        next_done = trainer_state["next_done"].to(device)
        global_step = int(resume_checkpoint["global_step"])
        episode_scores = list(trainer_state["episode_scores"])
        episode_returns = list(trainer_state["episode_returns"])
        episode_wins = list(trainer_state["episode_wins"])
        episode_lengths = list(trainer_state["episode_lengths"])
        episode_sent_ojama = list(trainer_state["episode_sent_ojama"])
        episode_received_ojama = list(trainer_state["episode_received_ojama"])
        episode_deadline_misses = list(trainer_state["episode_deadline_misses"])
        episode_input_failures = list(trainer_state["episode_input_failures"])
        episode_latency_ticks = list(trainer_state["episode_latency_ticks"])
        periodic_checkpoints = list(trainer_state["periodic_checkpoints"])
        best_metric_value = trainer_state["best_metric_value"]
        best_checkpoint_written = bool(trainer_state["best_checkpoint_written"])
        restore_rng_state(resume_checkpoint["rng_state"])

    with paths.metrics_path.open("w", newline="", encoding="utf-8") as metrics_file:
        csv_writer = csv.DictWriter(
            metrics_file,
            fieldnames=["global_step", "metric", "value"],
        )
        csv_writer.writeheader()

        num_updates = max(1, cfg.total_timesteps // batch_size)
        start_update = global_step // batch_size

        for update in range(start_update + 1, num_updates + 1):
            boards = torch.zeros((cfg.num_steps, cfg.num_envs, *agent.board_shape), dtype=torch.float32, device=device)
            vectors = torch.zeros((cfg.num_steps, cfg.num_envs, VECTOR_FEATURE_DIM), dtype=torch.float32, device=device)
            masks = torch.zeros((cfg.num_steps, cfg.num_envs, agent.action_dim), dtype=torch.bool, device=device)
            actions = torch.zeros((cfg.num_steps, cfg.num_envs), dtype=torch.long, device=device)
            logprobs = torch.zeros((cfg.num_steps, cfg.num_envs), dtype=torch.float32, device=device)
            rewards = torch.zeros((cfg.num_steps, cfg.num_envs), dtype=torch.float32, device=device)
            dones = torch.zeros((cfg.num_steps, cfg.num_envs), dtype=torch.float32, device=device)
            values = torch.zeros((cfg.num_steps, cfg.num_envs), dtype=torch.float32, device=device)

            for step in range(cfg.num_steps):
                global_step += cfg.num_envs
                batch_obs = stack_realtime_observations(adapter.observations, str(device))
                batch_masks = stack_realtime_masks(adapter.infos, str(device))

                boards[step] = batch_obs["board"]
                vectors[step] = batch_obs["vector_features"]
                masks[step] = batch_masks
                dones[step] = next_done

                with torch.no_grad():
                    action, logprob, _, value = agent.get_action_and_value(
                        batch_obs,
                        action_mask=batch_masks,
                    )
                actions[step] = action
                logprobs[step] = logprob
                values[step] = value

                rollout_step = adapter.step(action.detach().cpu().tolist())
                rewards[step] = torch.as_tensor(rollout_step.rewards, dtype=torch.float32, device=device)
                next_done = torch.as_tensor(rollout_step.dones, dtype=torch.float32, device=device)
                for episode in rollout_step.episodes:
                    episode_return = float(episode.get("trainer_return", episode.get("r", 0.0)))
                    episode_score = float(episode.get("score", 0.0))
                    episode_win = float(episode.get("win", 0.0))
                    episode_length = float(episode.get("l", 0.0))
                    sent_ojama = float(episode.get("sent_ojama", 0.0))
                    received_ojama = float(episode.get("received_ojama", 0.0))
                    deadline_misses = float(episode.get("deadline_misses", 0.0))
                    input_failures = float(episode.get("input_failures", 0.0))
                    latency_ticks = float(episode.get("latency_ticks", 0.0))
                    episode_returns.append(episode_return)
                    episode_scores.append(episode_score)
                    episode_wins.append(episode_win)
                    episode_lengths.append(episode_length)
                    episode_sent_ojama.append(sent_ojama)
                    episode_received_ojama.append(received_ojama)
                    episode_deadline_misses.append(deadline_misses)
                    episode_input_failures.append(input_failures)
                    episode_latency_ticks.append(latency_ticks)
                    csv_writer.writerow({"global_step": global_step, "metric": "episodic_return", "value": episode_return})
                    csv_writer.writerow({"global_step": global_step, "metric": "episodic_score", "value": episode_score})
                    csv_writer.writerow({"global_step": global_step, "metric": "episodic_win", "value": episode_win})
                    csv_writer.writerow({"global_step": global_step, "metric": "episodic_length", "value": episode_length})
                    csv_writer.writerow({"global_step": global_step, "metric": "episodic_sent_ojama", "value": sent_ojama})
                    csv_writer.writerow({"global_step": global_step, "metric": "episodic_received_ojama", "value": received_ojama})
                    csv_writer.writerow({"global_step": global_step, "metric": "episodic_deadline_misses", "value": deadline_misses})
                    csv_writer.writerow({"global_step": global_step, "metric": "episodic_input_failures", "value": input_failures})
                    csv_writer.writerow({"global_step": global_step, "metric": "episodic_latency_ticks", "value": latency_ticks})

            with torch.no_grad():
                next_batch_obs = stack_realtime_observations(adapter.observations, str(device))
                next_batch_masks = stack_realtime_masks(adapter.infos, str(device))
                next_value = agent.get_action_and_value(next_batch_obs, action_mask=next_batch_masks)[3]
                advantages, returns = compute_gae(
                    rewards,
                    dones,
                    values,
                    next_done,
                    next_value,
                    gamma=cfg.gamma,
                    gae_lambda=cfg.gae_lambda,
                )

            b_boards = boards.reshape((-1, *agent.board_shape))
            b_vectors = vectors.reshape((-1, VECTOR_FEATURE_DIM))
            b_masks = masks.reshape((-1, agent.action_dim))
            b_actions = actions.reshape(-1)
            b_logprobs = logprobs.reshape(-1)
            b_advantages = advantages.reshape(-1)
            b_returns = returns.reshape(-1)
            b_values = values.reshape(-1)

            indices = np.arange(batch_size)
            pg_loss = torch.tensor(0.0, device=device)
            v_loss = torch.tensor(0.0, device=device)
            entropy_loss = torch.tensor(0.0, device=device)
            for _ in range(cfg.update_epochs):
                np.random.shuffle(indices)
                for start in range(0, batch_size, minibatch_size):
                    end = start + minibatch_size
                    mb_indices = indices[start:end]
                    mb_obs = {
                        "board": b_boards[mb_indices],
                        "vector_features": b_vectors[mb_indices],
                    }
                    _, newlogprob, entropy, newvalue = agent.get_action_and_value(
                        mb_obs,
                        action=b_actions[mb_indices],
                        action_mask=b_masks[mb_indices],
                    )
                    logratio = newlogprob - b_logprobs[mb_indices]
                    ratio = logratio.exp()

                    mb_advantages = b_advantages[mb_indices]
                    mb_advantages = (mb_advantages - mb_advantages.mean()) / (mb_advantages.std() + 1e-8)
                    pg_loss_unclipped = -mb_advantages * ratio
                    pg_loss_clipped = -mb_advantages * torch.clamp(
                        ratio,
                        1.0 - cfg.clip_coef,
                        1.0 + cfg.clip_coef,
                    )
                    pg_loss = torch.max(pg_loss_unclipped, pg_loss_clipped).mean()
                    newvalue = newvalue.view(-1)
                    v_loss = 0.5 * ((newvalue - b_returns[mb_indices]) ** 2).mean()
                    entropy_loss = entropy.mean()
                    loss = pg_loss - cfg.ent_coef * entropy_loss + cfg.vf_coef * v_loss

                    optimizer.zero_grad()
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(agent.parameters(), cfg.max_grad_norm)
                    optimizer.step()

            explained_var = float("nan")
            y_pred = b_values.detach().cpu().numpy()
            y_true = b_returns.detach().cpu().numpy()
            var_y = np.var(y_true)
            if var_y > 0:
                explained_var = float(1 - np.var(y_true - y_pred) / var_y)

            csv_writer.writerow({"global_step": global_step, "metric": "loss_policy", "value": float(pg_loss.item())})
            csv_writer.writerow({"global_step": global_step, "metric": "loss_value", "value": float(v_loss.item())})
            csv_writer.writerow({"global_step": global_step, "metric": "entropy", "value": float(entropy_loss.item())})
            csv_writer.writerow({"global_step": global_step, "metric": "explained_variance", "value": explained_var})
            elapsed = max(time.time() - start_time, 1e-9)
            csv_writer.writerow({"global_step": global_step, "metric": "SPS", "value": float(int(global_step / elapsed))})
            metrics_file.flush()

            current_best_candidate = _current_metric_value(
                cfg.best_checkpoint_metric,
                window=cfg.rolling_window_episodes,
                episode_scores=episode_scores,
                episode_returns=episode_returns,
                episode_wins=episode_wins,
                episode_lengths=episode_lengths,
                episode_deadline_misses=episode_deadline_misses,
                episode_input_failures=episode_input_failures,
            )
            if (
                cfg.keep_best_checkpoint
                and current_best_candidate is not None
                and (best_metric_value is None or current_best_candidate > best_metric_value)
            ):
                best_metric_value = current_best_candidate
                torch.save(
                    _checkpoint_payload(
                        cfg=cfg,
                        paths=paths,
                        agent=agent,
                        optimizer=optimizer,
                        global_step=global_step,
                        episode_scores=episode_scores,
                        episode_returns=episode_returns,
                        episode_wins=episode_wins,
                        episode_lengths=episode_lengths,
                        episode_sent_ojama=episode_sent_ojama,
                        episode_received_ojama=episode_received_ojama,
                        episode_deadline_misses=episode_deadline_misses,
                        episode_input_failures=episode_input_failures,
                        episode_latency_ticks=episode_latency_ticks,
                        checkpoint_kind="best",
                        best_metric_value=best_metric_value,
                        rng_state=capture_rng_state(),
                        trainer_state=_trainer_state(
                            adapter=adapter,
                            next_done=next_done,
                            episode_scores=episode_scores,
                            episode_returns=episode_returns,
                            episode_wins=episode_wins,
                            episode_lengths=episode_lengths,
                            episode_sent_ojama=episode_sent_ojama,
                            episode_received_ojama=episode_received_ojama,
                            episode_deadline_misses=episode_deadline_misses,
                            episode_input_failures=episode_input_failures,
                            episode_latency_ticks=episode_latency_ticks,
                            periodic_checkpoints=periodic_checkpoints,
                            best_metric_value=best_metric_value,
                            best_checkpoint_written=True,
                        ),
                    ),
                    paths.best_checkpoint_path,
                )
                best_checkpoint_written = True

            if cfg.checkpoint_interval_updates > 0 and update % cfg.checkpoint_interval_updates == 0:
                periodic_path = paths.checkpoint_dir / f"step_{global_step}.pt"
                torch.save(
                    _checkpoint_payload(
                        cfg=cfg,
                        paths=paths,
                        agent=agent,
                        optimizer=optimizer,
                        global_step=global_step,
                        episode_scores=episode_scores,
                        episode_returns=episode_returns,
                        episode_wins=episode_wins,
                        episode_lengths=episode_lengths,
                        episode_sent_ojama=episode_sent_ojama,
                        episode_received_ojama=episode_received_ojama,
                        episode_deadline_misses=episode_deadline_misses,
                        episode_input_failures=episode_input_failures,
                        episode_latency_ticks=episode_latency_ticks,
                        checkpoint_kind="periodic",
                        best_metric_value=best_metric_value,
                        rng_state=capture_rng_state(),
                        trainer_state=_trainer_state(
                            adapter=adapter,
                            next_done=next_done,
                            episode_scores=episode_scores,
                            episode_returns=episode_returns,
                            episode_wins=episode_wins,
                            episode_lengths=episode_lengths,
                            episode_sent_ojama=episode_sent_ojama,
                            episode_received_ojama=episode_received_ojama,
                            episode_deadline_misses=episode_deadline_misses,
                            episode_input_failures=episode_input_failures,
                            episode_latency_ticks=episode_latency_ticks,
                            periodic_checkpoints=[*periodic_checkpoints, str(periodic_path)],
                            best_metric_value=best_metric_value,
                            best_checkpoint_written=best_checkpoint_written,
                        ),
                    ),
                    periodic_path,
                )
                periodic_checkpoints.append(str(periodic_path))

        torch.save(
            _checkpoint_payload(
                cfg=cfg,
                paths=paths,
                agent=agent,
                optimizer=optimizer,
                global_step=global_step,
                episode_scores=episode_scores,
                episode_returns=episode_returns,
                episode_wins=episode_wins,
                episode_lengths=episode_lengths,
                episode_sent_ojama=episode_sent_ojama,
                episode_received_ojama=episode_received_ojama,
                episode_deadline_misses=episode_deadline_misses,
                episode_input_failures=episode_input_failures,
                episode_latency_ticks=episode_latency_ticks,
                checkpoint_kind="latest",
                best_metric_value=best_metric_value,
                rng_state=capture_rng_state(),
                trainer_state=_trainer_state(
                    adapter=adapter,
                    next_done=next_done,
                    episode_scores=episode_scores,
                    episode_returns=episode_returns,
                    episode_wins=episode_wins,
                    episode_lengths=episode_lengths,
                    episode_sent_ojama=episode_sent_ojama,
                    episode_received_ojama=episode_received_ojama,
                    episode_deadline_misses=episode_deadline_misses,
                    episode_input_failures=episode_input_failures,
                    episode_latency_ticks=episode_latency_ticks,
                    periodic_checkpoints=periodic_checkpoints,
                    best_metric_value=best_metric_value,
                    best_checkpoint_written=best_checkpoint_written,
                ),
            ),
            paths.checkpoint_path,
        )

        evaluation = _run_eval_smoke(cfg, paths.checkpoint_path)
        summary = {
            "run_id": paths.run_id,
            "run_dir": str(paths.run_dir),
            "global_step": global_step,
            "episodes": len(episode_scores),
            "rolling_window_episodes": cfg.rolling_window_episodes,
            "mean_episode_score": _mean_last(episode_scores, cfg.rolling_window_episodes),
            "mean_episode_return": _mean_last(episode_returns, cfg.rolling_window_episodes),
            "mean_win_rate": _mean_last(episode_wins, cfg.rolling_window_episodes),
            "mean_episode_length": _mean_last(episode_lengths, cfg.rolling_window_episodes),
            "mean_sent_ojama": _mean_last(episode_sent_ojama, cfg.rolling_window_episodes),
            "mean_received_ojama": _mean_last(episode_received_ojama, cfg.rolling_window_episodes),
            "mean_deadline_misses": _mean_last(episode_deadline_misses, cfg.rolling_window_episodes),
            "mean_input_failures": _mean_last(episode_input_failures, cfg.rolling_window_episodes),
            "mean_latency_ticks": _mean_last(episode_latency_ticks, cfg.rolling_window_episodes),
            "best_checkpoint_metric": cfg.best_checkpoint_metric,
            "best_metric_value": best_metric_value,
            "checkpoint_path": str(paths.checkpoint_path),
            "best_checkpoint_path": str(paths.best_checkpoint_path) if best_checkpoint_written else None,
            "periodic_checkpoints": periodic_checkpoints,
            "metrics_path": str(paths.metrics_path),
            "config_path": str(paths.config_path),
            "metadata_path": str(paths.metadata_path),
            "manifest_path": str(paths.manifest_path),
            "resume_checkpoint_path": cfg.resume_checkpoint_path or None,
            "evaluation": evaluation,
        }
        paths.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        checkpoint_artifacts: dict[str, str | Path | None] = {
            "latest": paths.checkpoint_path,
            "best": paths.best_checkpoint_path if best_checkpoint_written else None,
        }
        checkpoint_artifacts.update(
            {f"periodic_{index + 1}": path for index, path in enumerate(periodic_checkpoints)}
        )
        write_artifact_manifest(
            run_dir=paths.run_dir,
            run_id=paths.run_id,
            trainer_name="realtime_ppo",
            config=asdict(cfg),
            git_commit=git_commit(),
            seed=cfg.seed,
            artifacts={
                "config": paths.config_path,
                "metadata": paths.metadata_path,
                "metrics": paths.metrics_path,
                "summary": paths.summary_path,
            },
            checkpoints=checkpoint_artifacts,
            manifest_path=paths.manifest_path,
            parent_checkpoint_path=cfg.resume_checkpoint_path or None,
            extra={
                "best_checkpoint_metric": cfg.best_checkpoint_metric,
                "rolling_window_episodes": cfg.rolling_window_episodes,
                "realtime_policy": realtime_checkpoint_metadata(native_realtime=True),
                "evaluation": evaluation,
            },
        )

    validate_realtime_training_checkpoint(
        paths.checkpoint_path,
        manifest_path=paths.manifest_path,
        map_location=device,
        require_exact=True,
    )
    adapter.close()

    return {
        "run_id": paths.run_id,
        "run_dir": str(paths.run_dir),
        "global_step": global_step,
        "checkpoint_path": str(paths.checkpoint_path),
        "best_checkpoint_path": str(paths.best_checkpoint_path) if best_checkpoint_written else None,
        "metrics_path": str(paths.metrics_path),
        "config_path": str(paths.config_path),
        "metadata_path": str(paths.metadata_path),
        "manifest_path": str(paths.manifest_path),
        "summary_path": str(paths.summary_path),
        "mean_episode_score": _mean_last(episode_scores, cfg.rolling_window_episodes),
        "mean_win_rate": _mean_last(episode_wins, cfg.rolling_window_episodes),
        "mean_deadline_misses": _mean_last(episode_deadline_misses, cfg.rolling_window_episodes),
        "mean_input_failures": _mean_last(episode_input_failures, cfg.rolling_window_episodes),
        "episodes": len(episode_scores),
        "evaluation": evaluation,
    }


def _run_eval_smoke(cfg: RealtimePPOConfig, checkpoint_path: Path) -> dict[str, Any]:
    if cfg.eval_games <= 0:
        return {"enabled": False}
    from eval.realtime_arena import run_realtime_series, summarize_realtime_result

    policy = RealtimeCheckpointPolicy(checkpoint_path, device=cfg.device, deterministic=True)
    opponent = make_policy(
        cfg.opponent_policy,
        seed=cfg.seed + 90_000,
        checkpoint_path=cfg.opponent_checkpoint_path or None,
        deterministic=True,
    )
    result = run_realtime_series(
        policy,
        opponent,
        games=cfg.eval_games,
        seed=cfg.seed + 80_000,
        max_ticks=cfg.eval_max_ticks,
        decision_config=RealtimeDecisionConfig(
            use_reachable_action_mask=cfg.use_reachable_action_mask,
            max_plan_expanded_states=cfg.max_plan_expanded_states,
        ),
    )
    summary = summarize_realtime_result(
        result,
        label="realtime_ppo_smoke",
        policy_a="checkpoint",
        policy_b=cfg.opponent_policy,
        games=len(result.matches),
        seed=cfg.seed + 80_000,
        max_ticks=cfg.eval_max_ticks,
    )
    return {
        "enabled": True,
        "games": cfg.eval_games,
        "max_ticks": cfg.eval_max_ticks,
        "win_rate_policy_a": summary["win_rate_policy_a"],
        "mean_ticks": summary["mean_ticks"],
        "mean_deadline_misses_policy_a": summary["mean_deadline_misses_policy_a"],
        "mean_unreachable_plans_policy_a": summary["mean_unreachable_plans_policy_a"],
    }


class RealtimeCheckpointPolicy:
    """Inference policy loaded through the realtime checkpoint validator."""

    def __init__(self, checkpoint_path: str | Path, *, device: str = "cpu", deterministic: bool = True):
        _require_deps()
        self.device = torch.device(device)
        self.deterministic = deterministic
        checkpoint = validate_realtime_training_checkpoint(
            checkpoint_path,
            map_location=self.device,
            require_exact=False,
        )
        self.agent = PuyoActorCritic(
            board_shape=tuple(checkpoint["board_shape"]),
            vector_dim=int(checkpoint.get("vector_dim", VECTOR_FEATURE_DIM)),
        ).to(self.device)
        self.agent.load_state_dict(checkpoint["model_state_dict"])
        self.agent.eval()

    def select_action(self, observation: dict[str, Any], info: dict[str, Any]) -> int:
        mask = info.get("action_mask")
        if mask is None:
            mask = [True] * NUM_ACTIONS
        with torch.no_grad():
            obs = stack_realtime_observations([observation], str(self.device))
            action_mask = torch.as_tensor(np.asarray(mask)[None, ...], dtype=torch.bool, device=self.device)
            if self.deterministic:
                logits, _ = self.agent.forward(
                    obs["board"],
                    obs["vector_features"],
                    action_mask=action_mask,
                )
                return int(torch.argmax(logits, dim=1).item())
            action = self.agent.get_action_and_value(obs, action_mask=action_mask)[0]
            return int(action.item())
