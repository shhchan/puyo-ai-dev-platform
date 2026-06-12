"""PPO training for the strategy-profile manager."""

from __future__ import annotations

import csv
import json
import random
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

try:
    import numpy as np
    import torch
    from torch import optim
except ImportError:  # pragma: no cover - dependency guard
    np = None
    torch = None
    optim = None

from agents.networks import PuyoActorCritic
from agents.strategy_manager import manager_checkpoint_metadata
from agents.tactical_scenarios import (
    generate_teacher_examples,
    load_teacher_dataset,
    write_teacher_dataset,
)
from agents.strategy_workers import default_worker_profiles, smoke_worker_profiles
from puyo_env.manager_env import ManagerSelfPlayEnv, manager_vector_dim, manager_vector_features
from selfplay.opponent_pool import OpponentPool, OpponentSnapshot
from selfplay.policies import make_policy


@dataclass
class ManagerPPOConfig:
    seed: int = 1
    total_timesteps: int = 2_048
    num_envs: int = 1
    num_steps: int = 32
    learning_rate: float = 2.5e-4
    gamma: float = 0.99
    gae_lambda: float = 0.95
    update_epochs: int = 4
    minibatch_size: int = 64
    clip_coef: float = 0.2
    ent_coef: float = 0.01
    vf_coef: float = 0.5
    max_grad_norm: float = 0.5
    max_episode_steps: int = 100
    opponent_policy: str = "greedy"
    opponent_checkpoint_path: str = ""
    opponent_pool_path: str = ""
    opponent_sampling: str = "balanced"
    switch_penalty: float = 0.02
    decision_time_penalty: float = 0.001
    auxiliary_reward_scale: float = 0.25
    curriculum_enabled: bool = True
    curriculum_boundaries: str = "0.20,0.45,0.70"
    behavior_cloning_epochs: int = 2
    teacher_dataset_path: str = ""
    generate_teacher_dataset: bool = True
    selfplay_snapshot_interval: int = 0
    use_smoke_profiles: bool = False
    log_dir: str = "runs/manager_ppo"
    checkpoint_path: str = ""
    device: str = "cpu"
    run_name: str = "manager_ppo"
    run_id: str = ""


def _require_deps() -> None:
    if np is None or torch is None or optim is None:
        raise ImportError("manager PPO training requires numpy and torch")


def _safe_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value.strip())
    return safe.strip("-") or "manager_ppo"


def _run_paths(cfg: ManagerPPOConfig) -> dict[str, Path | str]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = _safe_name(cfg.run_id) if cfg.run_id else f"{_safe_name(cfg.run_name)}-seed{cfg.seed}-{timestamp}"
    run_dir = Path(cfg.log_dir) / run_id
    checkpoint_dir = run_dir / "checkpoints"
    checkpoint_path = Path(cfg.checkpoint_path) if cfg.checkpoint_path else checkpoint_dir / "latest.pt"
    return {
        "run_id": run_id,
        "run_dir": run_dir,
        "checkpoint_dir": checkpoint_dir,
        "checkpoint_path": checkpoint_path,
        "best_checkpoint_path": checkpoint_dir / "best.pt",
        "metrics_path": run_dir / "metrics.csv",
        "config_path": run_dir / "config.yaml",
        "summary_path": run_dir / "summary.json",
        "teacher_dataset_path": run_dir / "teacher_dataset.json",
        "opponent_pool_path": run_dir / "opponent_pool.json",
        "snapshot_dir": run_dir / "opponents",
    }


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _curriculum_stage(cfg: ManagerPPOConfig, progress: float) -> tuple[str, float]:
    if not cfg.curriculum_enabled:
        return "full", 0.0
    values = [float(value.strip()) for value in cfg.curriculum_boundaries.split(",") if value.strip()]
    if len(values) != 3:
        raise ValueError("curriculum_boundaries must contain three comma-separated fractions")
    if progress < values[0]:
        return "safe_build", cfg.auxiliary_reward_scale
    if progress < values[1]:
        return "punish", cfg.auxiliary_reward_scale
    if progress < values[2]:
        return "counter", cfg.auxiliary_reward_scale
    decay = max(0.0, 1.0 - (progress - values[2]) / max(1e-9, 1.0 - values[2]))
    return "full", cfg.auxiliary_reward_scale * decay


def _behavior_clone(
    agent,
    optimizer,
    examples,
    *,
    device,
    epochs: int,
) -> float | None:
    if epochs <= 0 or not examples:
        return None
    boards = torch.as_tensor(np.asarray([item.board for item in examples]), dtype=torch.float32, device=device)
    next_pairs = np.asarray([item.next_pairs for item in examples], dtype=np.float32).reshape(len(examples), -1)
    manager_features = np.asarray([item.manager_features for item in examples], dtype=np.float32)
    vectors = torch.as_tensor(
        np.concatenate([next_pairs, manager_features], axis=1),
        dtype=torch.float32,
        device=device,
    )
    labels = torch.as_tensor(
        [item.selected_profile_id for item in examples],
        dtype=torch.long,
        device=device,
    )
    loss_value = None
    for _ in range(epochs):
        logits, _ = agent.forward(boards, vectors)
        loss = torch.nn.functional.cross_entropy(logits, labels)
        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(agent.parameters(), 0.5)
        optimizer.step()
        loss_value = float(loss.item())
    return loss_value


def _teacher_accuracy(agent, examples, *, device) -> float | None:
    if not examples:
        return None
    boards = torch.as_tensor(np.asarray([item.board for item in examples]), dtype=torch.float32, device=device)
    next_pairs = np.asarray([item.next_pairs for item in examples], dtype=np.float32).reshape(len(examples), -1)
    manager_features = np.asarray([item.manager_features for item in examples], dtype=np.float32)
    vectors = torch.as_tensor(
        np.concatenate([next_pairs, manager_features], axis=1),
        dtype=torch.float32,
        device=device,
    )
    labels = torch.as_tensor(
        [item.selected_profile_id for item in examples],
        dtype=torch.long,
        device=device,
    )
    with torch.no_grad():
        logits, _ = agent.forward(boards, vectors)
    return float((torch.argmax(logits, dim=1) == labels).float().mean().item())


def _stack(observations: list[dict[str, Any]], infos: list[dict[str, Any]], device):
    boards = np.stack([observation["board"] for observation in observations])
    vectors = np.stack([manager_vector_features(observation) for observation in observations])
    masks = np.stack([info["action_mask"] for info in infos])
    return (
        torch.as_tensor(boards, dtype=torch.float32, device=device),
        torch.as_tensor(vectors, dtype=torch.float32, device=device),
        torch.as_tensor(masks, dtype=torch.bool, device=device),
    )


def _checkpoint_payload(cfg, agent, optimizer, profiles, global_step, episodes, kind):
    return {
        **manager_checkpoint_metadata(profiles),
        "model_state_dict": agent.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "config": asdict(cfg),
        "git_commit": _git_commit(),
        "board_shape": agent.board_shape,
        "global_step": global_step,
        "episodes": episodes,
        "checkpoint_kind": kind,
    }


def train_manager_ppo(config: ManagerPPOConfig | None = None) -> dict[str, Any]:
    """Train a profile selector against a fixed placement policy."""

    _require_deps()
    cfg = config or ManagerPPOConfig()
    if cfg.num_envs <= 0 or cfg.num_steps <= 0:
        raise ValueError("num_envs and num_steps must be positive")
    paths = _run_paths(cfg)
    run_dir = paths["run_dir"]
    checkpoint_dir = paths["checkpoint_dir"]
    checkpoint_path = paths["checkpoint_path"]
    run_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    paths["snapshot_dir"].mkdir(parents=True, exist_ok=True)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    paths["config_path"].write_text(
        yaml.safe_dump(
            {"config": asdict(cfg), "run_id": paths["run_id"], "git_commit": _git_commit()},
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    random.seed(cfg.seed)
    np.random.seed(cfg.seed)
    torch.manual_seed(cfg.seed)
    device = torch.device(cfg.device)
    profiles = smoke_worker_profiles() if cfg.use_smoke_profiles else default_worker_profiles()
    vector_dim = manager_vector_dim(len(profiles))
    opponent_pool = OpponentPool.load(cfg.opponent_pool_path) if cfg.opponent_pool_path else None
    opponent_rng = random.Random(cfg.seed + 91_337)
    opponent_counts: dict[str, int] = {}

    def make_training_opponent(env_index: int):
        if opponent_pool is None:
            return (
                cfg.opponent_policy,
                make_policy(
                    cfg.opponent_policy,
                    seed=cfg.seed + 50_000 + env_index,
                    checkpoint_path=cfg.opponent_checkpoint_path or None,
                    device=cfg.device,
                    deterministic=True,
                ),
            )
        snapshot = opponent_pool.sample(
            opponent_rng,
            strategy=cfg.opponent_sampling,
            target_rating=opponent_pool.elo_config.default_rating,
        )
        opponent_counts[snapshot.name] = opponent_counts.get(snapshot.name, 0) + 1
        snapshot.games_played += 1
        return (
            snapshot.name,
            opponent_pool.make_policy(
                snapshot,
                seed=cfg.seed + 50_000 + env_index + sum(opponent_counts.values()),
                device=cfg.device,
                deterministic=True,
            ),
        )

    envs = []
    for env_index in range(cfg.num_envs):
        opponent_name, opponent = make_training_opponent(env_index)
        env = ManagerSelfPlayEnv(
            seed=cfg.seed + env_index * 10_000,
            max_steps=cfg.max_episode_steps,
            opponent_policy=opponent,
            profiles=profiles,
            switch_penalty=cfg.switch_penalty,
            decision_time_penalty=cfg.decision_time_penalty,
            auxiliary_reward_scale=cfg.auxiliary_reward_scale,
        )
        env.opponent_name = opponent_name
        envs.append(env)

    observations = []
    infos = []
    for env_index, env in enumerate(envs):
        observation, info = env.reset(seed=cfg.seed + env_index)
        observations.append(observation)
        infos.append(info)
    board_shape = tuple(int(value) for value in observations[0]["board"].shape)
    agent = PuyoActorCritic(
        board_shape=board_shape,
        vector_dim=vector_dim,
        action_dim=len(profiles),
    ).to(device)
    optimizer = optim.Adam(agent.parameters(), lr=cfg.learning_rate, eps=1e-5)

    teacher_path = Path(cfg.teacher_dataset_path) if cfg.teacher_dataset_path else paths["teacher_dataset_path"]
    teacher_examples = []
    if cfg.teacher_dataset_path:
        teacher_examples = load_teacher_dataset(teacher_path)
    elif cfg.generate_teacher_dataset:
        teacher_examples = generate_teacher_examples(profiles=profiles)
        write_teacher_dataset(teacher_path, teacher_examples)
    behavior_cloning_loss = _behavior_clone(
        agent,
        optimizer,
        teacher_examples,
        device=device,
        epochs=cfg.behavior_cloning_epochs,
    )
    behavior_cloning_accuracy = _teacher_accuracy(agent, teacher_examples, device=device)
    behavior_checkpoint_path = paths["checkpoint_dir"] / "behavior_cloned.pt"
    if teacher_examples:
        payload = _checkpoint_payload(cfg, agent, optimizer, profiles, 0, [], "behavior_cloned")
        torch.save(payload, behavior_checkpoint_path)
        torch.save(payload, paths["best_checkpoint_path"])

    batch_size = cfg.num_envs * cfg.num_steps
    minibatch_size = min(batch_size, cfg.minibatch_size)
    num_updates = max(1, cfg.total_timesteps // batch_size)
    global_step = 0
    next_done = torch.zeros(cfg.num_envs, dtype=torch.float32, device=device)
    episodes: list[dict[str, Any]] = []
    best_win_rate = float("-inf")
    best_written = bool(teacher_examples)
    best_tactical_accuracy = behavior_cloning_accuracy
    started = time.time()

    with paths["metrics_path"].open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["global_step", "metric", "value"])
        writer.writeheader()
        for _update in range(num_updates):
            progress = min(1.0, global_step / max(1, cfg.total_timesteps))
            stage, auxiliary_scale = _curriculum_stage(cfg, progress)
            for env_index, env in enumerate(envs):
                env.set_curriculum_stage(stage, auxiliary_scale)
                infos[env_index]["action_mask"] = env._manager_action_mask()
            boards = torch.zeros((cfg.num_steps, cfg.num_envs, *board_shape), device=device)
            vectors = torch.zeros((cfg.num_steps, cfg.num_envs, vector_dim), device=device)
            masks = torch.zeros((cfg.num_steps, cfg.num_envs, len(profiles)), dtype=torch.bool, device=device)
            actions = torch.zeros((cfg.num_steps, cfg.num_envs), dtype=torch.long, device=device)
            logprobs = torch.zeros((cfg.num_steps, cfg.num_envs), device=device)
            rewards = torch.zeros((cfg.num_steps, cfg.num_envs), device=device)
            dones = torch.zeros((cfg.num_steps, cfg.num_envs), device=device)
            values = torch.zeros((cfg.num_steps, cfg.num_envs), device=device)

            for step in range(cfg.num_steps):
                global_step += cfg.num_envs
                batch_boards, batch_vectors, batch_masks = _stack(observations, infos, device)
                boards[step] = batch_boards
                vectors[step] = batch_vectors
                masks[step] = batch_masks
                dones[step] = next_done
                with torch.no_grad():
                    action, logprob, _, value = agent.get_action_and_value(
                        {"board": batch_boards, "vector_features": batch_vectors},
                        action_mask=batch_masks,
                    )
                actions[step] = action
                logprobs[step] = logprob
                values[step] = value

                new_observations = []
                new_infos = []
                done_values = []
                for env_index, env in enumerate(envs):
                    observation, reward, terminated, truncated, info = env.step(int(action[env_index].item()))
                    rewards[step, env_index] = float(reward)
                    done = terminated or truncated
                    if "manager_episode" in info:
                        episode = dict(info["manager_episode"])
                        episodes.append(episode)
                        for metric in (
                            "r",
                            "score",
                            "win",
                            "max_chain",
                            "switches",
                            "mean_decision_ms",
                            "mean_expanded_nodes",
                            "missed_lethal",
                            "failed_counter",
                            "tactical_success_rate",
                        ):
                            writer.writerow({"global_step": global_step, "metric": f"episodic_{metric}", "value": episode[metric]})
                        for profile_id, count in enumerate(episode["profile_counts"]):
                            writer.writerow({"global_step": global_step, "metric": f"profile_{profile_id}_count", "value": count})
                    if done:
                        if opponent_pool is not None:
                            opponent_name, opponent = make_training_opponent(env_index)
                            env.opponent_name = opponent_name
                            env.opponent_policy = opponent
                        observation, info = env.reset()
                    new_observations.append(observation)
                    new_infos.append(info)
                    done_values.append(float(done))
                observations = new_observations
                infos = new_infos
                next_done = torch.as_tensor(done_values, dtype=torch.float32, device=device)

            with torch.no_grad():
                next_boards, next_vectors, next_masks = _stack(observations, infos, device)
                next_value = agent.get_action_and_value(
                    {"board": next_boards, "vector_features": next_vectors}, action_mask=next_masks
                )[3]
                advantages = torch.zeros_like(rewards)
                last_gae = 0.0
                for step in reversed(range(cfg.num_steps)):
                    if step == cfg.num_steps - 1:
                        next_nonterminal = 1.0 - next_done
                        next_values = next_value
                    else:
                        next_nonterminal = 1.0 - dones[step + 1]
                        next_values = values[step + 1]
                    delta = rewards[step] + cfg.gamma * next_values * next_nonterminal - values[step]
                    last_gae = delta + cfg.gamma * cfg.gae_lambda * next_nonterminal * last_gae
                    advantages[step] = last_gae
                returns = advantages + values

            flat_boards = boards.reshape((-1, *board_shape))
            flat_vectors = vectors.reshape((-1, vector_dim))
            flat_masks = masks.reshape((-1, len(profiles)))
            flat_actions = actions.reshape(-1)
            flat_logprobs = logprobs.reshape(-1)
            flat_advantages = advantages.reshape(-1)
            flat_returns = returns.reshape(-1)
            indices = np.arange(batch_size)
            for _ in range(cfg.update_epochs):
                np.random.shuffle(indices)
                for start in range(0, batch_size, minibatch_size):
                    selected = indices[start : start + minibatch_size]
                    _, new_logprob, entropy, new_value = agent.get_action_and_value(
                        {"board": flat_boards[selected], "vector_features": flat_vectors[selected]},
                        action=flat_actions[selected],
                        action_mask=flat_masks[selected],
                    )
                    ratio = (new_logprob - flat_logprobs[selected]).exp()
                    normalized = flat_advantages[selected]
                    normalized = (normalized - normalized.mean()) / (normalized.std() + 1e-8)
                    policy_loss = torch.max(
                        -normalized * ratio,
                        -normalized * torch.clamp(ratio, 1.0 - cfg.clip_coef, 1.0 + cfg.clip_coef),
                    ).mean()
                    value_loss = 0.5 * ((new_value.view(-1) - flat_returns[selected]) ** 2).mean()
                    loss = policy_loss - cfg.ent_coef * entropy.mean() + cfg.vf_coef * value_loss
                    optimizer.zero_grad()
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(agent.parameters(), cfg.max_grad_norm)
                    optimizer.step()

            writer.writerow({"global_step": global_step, "metric": "loss_policy", "value": float(policy_loss.item())})
            writer.writerow({"global_step": global_step, "metric": "loss_value", "value": float(value_loss.item())})
            tactical_accuracy = _teacher_accuracy(agent, teacher_examples, device=device)
            if tactical_accuracy is not None:
                writer.writerow({"global_step": global_step, "metric": "tactical_teacher_accuracy", "value": tactical_accuracy})
            writer.writerow({"global_step": global_step, "metric": "curriculum_stage", "value": stage})
            writer.writerow({"global_step": global_step, "metric": "auxiliary_reward_scale", "value": auxiliary_scale})
            writer.writerow({"global_step": global_step, "metric": "SPS", "value": global_step / max(time.time() - started, 1e-9)})
            handle.flush()
            recent = episodes[-10:]
            win_rate = float(np.mean([episode["win"] for episode in recent])) if recent else None
            if (
                win_rate is not None
                and (tactical_accuracy is None or tactical_accuracy >= 0.8)
                and win_rate > best_win_rate
            ):
                best_win_rate = win_rate
                best_tactical_accuracy = tactical_accuracy
                torch.save(
                    _checkpoint_payload(cfg, agent, optimizer, profiles, global_step, episodes, "best"),
                    paths["best_checkpoint_path"],
                )
                best_written = True

            if (
                cfg.selfplay_snapshot_interval > 0
                and global_step % cfg.selfplay_snapshot_interval == 0
            ):
                snapshot_path = paths["snapshot_dir"] / f"manager-step-{global_step}.pt"
                torch.save(
                    _checkpoint_payload(cfg, agent, optimizer, profiles, global_step, episodes, "opponent"),
                    snapshot_path,
                )
                if opponent_pool is None:
                    opponent_pool = OpponentPool()
                name = f"manager_step_{global_step}"
                if opponent_pool.get(name) is None:
                    opponent_pool.add(
                        OpponentSnapshot(
                            name=name,
                            policy_type="manager",
                            checkpoint_path=str(snapshot_path),
                            metadata={"global_step": global_step, "role": "selfplay_snapshot"},
                        )
                    )
                opponent_pool.save(paths["opponent_pool_path"])

    torch.save(
        _checkpoint_payload(cfg, agent, optimizer, profiles, global_step, episodes, "latest"),
        checkpoint_path,
    )
    if opponent_pool is not None:
        opponent_pool.save(paths["opponent_pool_path"])
    recent = episodes[-10:]
    summary = {
        "run_id": paths["run_id"],
        "git_commit": _git_commit(),
        "global_step": global_step,
        "episodes": len(episodes),
        "mean_win_rate": float(np.mean([episode["win"] for episode in recent])) if recent else None,
        "mean_score": float(np.mean([episode["score"] for episode in recent])) if recent else None,
        "mean_switches": float(np.mean([episode["switches"] for episode in recent])) if recent else None,
        "mean_decision_ms": float(np.mean([episode["mean_decision_ms"] for episode in recent])) if recent else None,
        "mean_tactical_success_rate": (
            float(np.mean([episode["tactical_success_rate"] for episode in recent])) if recent else None
        ),
        "behavior_cloning_loss": behavior_cloning_loss,
        "behavior_cloning_accuracy": behavior_cloning_accuracy,
        "final_tactical_accuracy": _teacher_accuracy(agent, teacher_examples, device=device),
        "best_tactical_accuracy": best_tactical_accuracy,
        "behavior_checkpoint_path": str(behavior_checkpoint_path) if teacher_examples else None,
        "teacher_examples": len(teacher_examples),
        "teacher_dataset_path": str(teacher_path) if teacher_examples else None,
        "worker_profiles": [asdict(profile) for profile in profiles],
        "opponent_counts": opponent_counts,
        "opponent_pool_path": str(paths["opponent_pool_path"]) if opponent_pool is not None else None,
        "checkpoint_path": str(checkpoint_path),
        "best_checkpoint_path": str(paths["best_checkpoint_path"]) if best_written else None,
        "metrics_path": str(paths["metrics_path"]),
    }
    paths["summary_path"].write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    for env in envs:
        env.close()
    return {
        **summary,
        "run_dir": str(run_dir),
        "config_path": str(paths["config_path"]),
        "summary_path": str(paths["summary_path"]),
    }
