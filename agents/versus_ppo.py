"""Flat PPO training against a self-play opponent policy."""

from __future__ import annotations

import csv
import json
import random
import subprocess
import time
from datetime import datetime, timezone
from dataclasses import asdict, dataclass
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

try:
    from torch.utils.tensorboard import SummaryWriter
except Exception:  # pragma: no cover - optional logger
    SummaryWriter = None

from puyo_env.selfplay_env import VersusSelfPlayEnv
from puyo_env.versus_env import VersusRewardConfig
from selfplay.opponent_pool import OpponentPool
from selfplay.policies import make_policy

from .networks import PuyoActorCritic, VECTOR_FEATURE_DIM


@dataclass
class VersusPPOConfig:
    seed: int = 1
    total_timesteps: int = 10_000
    num_envs: int = 4
    num_steps: int = 128
    learning_rate: float = 2.5e-4
    gamma: float = 0.99
    gae_lambda: float = 0.95
    update_epochs: int = 4
    minibatch_size: int = 128
    clip_coef: float = 0.2
    ent_coef: float = 0.01
    vf_coef: float = 0.5
    max_grad_norm: float = 0.5
    max_episode_steps: int = 500
    opponent_policy: str = "random"
    opponent_checkpoint_path: str = ""
    opponent_pool_path: str = ""
    log_dir: str = "runs/versus_ppo"
    checkpoint_path: str = ""
    device: str = "cpu"
    run_name: str = "versus_ppo"
    run_id: str = ""
    checkpoint_interval_updates: int = 0
    keep_best_checkpoint: bool = True
    best_checkpoint_metric: str = "mean_win_rate"
    rolling_window_episodes: int = 10
    reward_target_score_per_ojama: int = 70
    reward_score_reward: float = 0.25
    reward_attack_reward: float = 0.5
    reward_chain_bonus: float = 0.05
    reward_survival_bonus: float = 0.01
    reward_garbage_penalty: float = 0.02
    reward_invalid_action_penalty: float = 5.0
    reward_win_reward: float = 10.0
    reward_loss_penalty: float = 10.0
    reward_draw_penalty: float = 1.0


@dataclass(frozen=True)
class VersusArtifactPaths:
    run_id: str
    run_dir: Path
    metrics_path: Path
    config_path: Path
    metadata_path: Path
    summary_path: Path
    checkpoint_dir: Path
    checkpoint_path: Path
    best_checkpoint_path: Path


def _require_deps():
    if np is None or torch is None or optim is None:
        raise ImportError("versus PPO training requires numpy and torch. Install dependencies with requirements.txt.")


def _safe_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value.strip())
    return safe.strip("-") or "versus_ppo"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resolve_artifact_paths(cfg: VersusPPOConfig) -> VersusArtifactPaths:
    run_id = _safe_name(cfg.run_id) if cfg.run_id else f"{_safe_name(cfg.run_name)}-seed{cfg.seed}-{_utc_timestamp()}"
    run_dir = Path(cfg.log_dir) / run_id
    checkpoint_dir = run_dir / "checkpoints"
    checkpoint_path = Path(cfg.checkpoint_path) if cfg.checkpoint_path else checkpoint_dir / "latest.pt"
    return VersusArtifactPaths(
        run_id=run_id,
        run_dir=run_dir,
        metrics_path=run_dir / "metrics.csv",
        config_path=run_dir / "config.yaml",
        metadata_path=run_dir / "metadata.json",
        summary_path=run_dir / "summary.json",
        checkpoint_dir=checkpoint_dir,
        checkpoint_path=checkpoint_path,
        best_checkpoint_path=checkpoint_dir / "best.pt",
    )


def _git_commit() -> str:
    root = Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return "unknown"
    return result.stdout.strip() or "unknown"


def _write_artifact_metadata(cfg: VersusPPOConfig, paths: VersusArtifactPaths) -> None:
    resolved = {
        "run_id": paths.run_id,
        "run_dir": str(paths.run_dir),
        "metrics_path": str(paths.metrics_path),
        "checkpoint_path": str(paths.checkpoint_path),
        "best_checkpoint_path": str(paths.best_checkpoint_path),
    }
    config_dump = {"config": asdict(cfg), "resolved": resolved}
    paths.config_path.write_text(yaml.safe_dump(config_dump, sort_keys=True), encoding="utf-8")

    metadata = {
        "run_id": paths.run_id,
        "created_at_utc": _utc_timestamp(),
        "git_commit": _git_commit(),
        "config_path": str(paths.config_path),
        "metrics_path": str(paths.metrics_path),
        "checkpoint_path": str(paths.checkpoint_path),
        "opponent_policy": cfg.opponent_policy,
        "opponent_checkpoint_path": cfg.opponent_checkpoint_path,
        "opponent_pool_path": cfg.opponent_pool_path,
    }
    paths.metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")


def _make_reward_config(cfg: VersusPPOConfig) -> VersusRewardConfig:
    return VersusRewardConfig(
        target_score_per_ojama=cfg.reward_target_score_per_ojama,
        score_reward=cfg.reward_score_reward,
        attack_reward=cfg.reward_attack_reward,
        chain_bonus=cfg.reward_chain_bonus,
        survival_bonus=cfg.reward_survival_bonus,
        garbage_penalty=cfg.reward_garbage_penalty,
        invalid_action_penalty=cfg.reward_invalid_action_penalty,
        win_reward=cfg.reward_win_reward,
        loss_penalty=cfg.reward_loss_penalty,
        draw_penalty=cfg.reward_draw_penalty,
    )


def _stack_observations(observations: list[dict[str, Any]], device: str):
    boards = np.stack([obs["board"] for obs in observations])
    next_pairs = np.stack([obs["next_pairs"].reshape(-1) for obs in observations])
    scalars = np.stack([obs["scalars"] for obs in observations])
    vectors = np.concatenate([next_pairs, scalars], axis=1)
    return {
        "board": torch.as_tensor(boards, dtype=torch.float32, device=device),
        "vector_features": torch.as_tensor(vectors, dtype=torch.float32, device=device),
    }


def _stack_masks(infos: list[dict[str, Any]], device: str):
    masks = np.stack([info["action_mask"] for info in infos])
    return torch.as_tensor(masks, dtype=torch.bool, device=device)


def _make_writer(log_dir: Path):
    if SummaryWriter is None:
        return None
    return SummaryWriter(str(log_dir))


def _make_opponent_policy(cfg: VersusPPOConfig, env_index: int, opponent_pool: OpponentPool | None = None):
    if opponent_pool is not None:
        rng = random.Random(cfg.seed + 70_000 + env_index)
        snapshot = opponent_pool.sample(rng)
        return opponent_pool.make_policy(
            snapshot,
            seed=cfg.seed + 50_000 + env_index,
            device=cfg.device,
            deterministic=True,
        )

    checkpoint_path = cfg.opponent_checkpoint_path or None
    return make_policy(
        cfg.opponent_policy,
        seed=cfg.seed + 50_000 + env_index,
        checkpoint_path=checkpoint_path,
        device=cfg.device,
        deterministic=True,
    )


def _mean_last(values: list[float], window: int) -> float | None:
    if not values:
        return None
    return float(np.mean(values[-max(1, window):]))


def _current_metric_value(
    metric_name: str,
    *,
    window: int,
    episode_scores: list[float],
    episode_returns: list[float],
    episode_wins: list[float],
    episode_max_chains: list[float],
) -> float | None:
    metric_values = {
        "mean_episode_score": _mean_last(episode_scores, window),
        "mean_episode_return": _mean_last(episode_returns, window),
        "mean_win_rate": _mean_last(episode_wins, window),
        "mean_max_chain": _mean_last(episode_max_chains, window),
    }
    if metric_name not in metric_values:
        valid = ", ".join(sorted(metric_values))
        raise ValueError(f"unknown best_checkpoint_metric: {metric_name}; valid values: {valid}")
    return metric_values[metric_name]


def _checkpoint_payload(
    *,
    cfg: VersusPPOConfig,
    paths: VersusArtifactPaths,
    agent,
    optimizer,
    global_step: int,
    episode_scores: list[float],
    episode_returns: list[float],
    episode_wins: list[float],
    episode_lengths: list[float],
    episode_max_chains: list[float],
    episode_sent_ojama: list[float],
    episode_received_ojama: list[float],
    checkpoint_kind: str,
    best_metric_value: float | None,
) -> dict[str, Any]:
    return {
        "model_state_dict": agent.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "config": asdict(cfg),
        "run_id": paths.run_id,
        "global_step": global_step,
        "board_shape": agent.board_shape,
        "episode_scores": episode_scores,
        "episode_returns": episode_returns,
        "episode_wins": episode_wins,
        "episode_lengths": episode_lengths,
        "episode_max_chains": episode_max_chains,
        "episode_sent_ojama": episode_sent_ojama,
        "episode_received_ojama": episode_received_ojama,
        "checkpoint_kind": checkpoint_kind,
        "best_checkpoint_metric": cfg.best_checkpoint_metric,
        "best_metric_value": best_metric_value,
    }


def train_versus_ppo(config: VersusPPOConfig | None = None) -> dict[str, Any]:
    """Train player_0 with PPO against a fixed opponent policy."""

    _require_deps()
    cfg = config or VersusPPOConfig()
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
        episode_max_chains=[],
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
    reward_config = _make_reward_config(cfg)
    opponent_pool = OpponentPool.load(cfg.opponent_pool_path) if cfg.opponent_pool_path else None
    envs = [
        VersusSelfPlayEnv(
            seed=cfg.seed + env_index * 10_000,
            max_steps=cfg.max_episode_steps,
            opponent_policy=_make_opponent_policy(cfg, env_index, opponent_pool),
            reward_config=reward_config,
        )
        for env_index in range(cfg.num_envs)
    ]
    observations = []
    infos = []
    for env_index, env in enumerate(envs):
        obs, info = env.reset(seed=cfg.seed + env_index)
        observations.append(obs)
        infos.append(info)

    board_shape = tuple(int(dim) for dim in observations[0]["board"].shape)
    agent = PuyoActorCritic(board_shape=board_shape, vector_dim=VECTOR_FEATURE_DIM).to(device)
    optimizer = optim.Adam(agent.parameters(), lr=cfg.learning_rate, eps=1e-5)
    writer = _make_writer(paths.run_dir)
    if writer is not None:
        writer.add_text("hyperparameters", "\n".join(f"{key}: {value}" for key, value in asdict(cfg).items()))

    batch_size = cfg.num_envs * cfg.num_steps
    minibatch_size = min(cfg.minibatch_size, batch_size)
    global_step = 0
    start_time = time.time()
    episode_scores: list[float] = []
    episode_returns: list[float] = []
    episode_wins: list[float] = []
    episode_lengths: list[float] = []
    episode_max_chains: list[float] = []
    episode_sent_ojama: list[float] = []
    episode_received_ojama: list[float] = []
    periodic_checkpoints: list[str] = []
    best_metric_value: float | None = None
    best_checkpoint_written = False

    with paths.metrics_path.open("w", newline="", encoding="utf-8") as metrics_file:
        csv_writer = csv.DictWriter(
            metrics_file,
            fieldnames=["global_step", "metric", "value"],
        )
        csv_writer.writeheader()

        num_updates = max(1, cfg.total_timesteps // batch_size)
        next_done = torch.zeros(cfg.num_envs, dtype=torch.float32, device=device)

        for update in range(1, num_updates + 1):
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
                batch_obs = _stack_observations(observations, str(device))
                batch_masks = _stack_masks(infos, str(device))

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

                next_observations = []
                next_infos = []
                next_done_values = []
                for env_index, env in enumerate(envs):
                    obs, reward, terminated, truncated, info = env.step(int(action[env_index].item()))
                    rewards[step, env_index] = float(reward)
                    done = terminated or truncated
                    if "episode" in info:
                        episode_return = float(info["episode"]["r"])
                        episode_score = float(info["episode"]["score"])
                        episode_win = float(info["episode"]["win"])
                        episode_length = float(info["episode"]["l"])
                        episode_max_chain = float(info["episode"]["max_chain"])
                        sent_ojama = float(info["episode"]["sent_ojama"])
                        received_ojama = float(info["episode"]["received_ojama"])
                        opponent_score = float(info["episode"]["opponent_score"])
                        episode_returns.append(episode_return)
                        episode_scores.append(episode_score)
                        episode_wins.append(episode_win)
                        episode_lengths.append(episode_length)
                        episode_max_chains.append(episode_max_chain)
                        episode_sent_ojama.append(sent_ojama)
                        episode_received_ojama.append(received_ojama)
                        csv_writer.writerow({"global_step": global_step, "metric": "episodic_return", "value": episode_return})
                        csv_writer.writerow({"global_step": global_step, "metric": "episodic_score", "value": episode_score})
                        csv_writer.writerow({"global_step": global_step, "metric": "episodic_opponent_score", "value": opponent_score})
                        csv_writer.writerow({"global_step": global_step, "metric": "episodic_win", "value": episode_win})
                        csv_writer.writerow({"global_step": global_step, "metric": "episodic_length", "value": episode_length})
                        csv_writer.writerow({"global_step": global_step, "metric": "episodic_max_chain", "value": episode_max_chain})
                        csv_writer.writerow({"global_step": global_step, "metric": "episodic_sent_ojama", "value": sent_ojama})
                        csv_writer.writerow({"global_step": global_step, "metric": "episodic_received_ojama", "value": received_ojama})
                        if writer is not None:
                            writer.add_scalar("charts/episodic_return", episode_return, global_step)
                            writer.add_scalar("charts/episodic_score", episode_score, global_step)
                            writer.add_scalar("charts/episodic_opponent_score", opponent_score, global_step)
                            writer.add_scalar("charts/episodic_win", episode_win, global_step)
                            writer.add_scalar("charts/episodic_length", episode_length, global_step)
                            writer.add_scalar("charts/episodic_max_chain", episode_max_chain, global_step)
                            writer.add_scalar("charts/episodic_sent_ojama", sent_ojama, global_step)
                            writer.add_scalar("charts/episodic_received_ojama", received_ojama, global_step)
                    if done:
                        obs, info = env.reset()
                    next_observations.append(obs)
                    next_infos.append(info)
                    next_done_values.append(float(done))
                observations = next_observations
                infos = next_infos
                next_done = torch.as_tensor(next_done_values, dtype=torch.float32, device=device)

            with torch.no_grad():
                next_batch_obs = _stack_observations(observations, str(device))
                next_batch_masks = _stack_masks(infos, str(device))
                next_value = agent.get_action_and_value(next_batch_obs, action_mask=next_batch_masks)[3]
                advantages = torch.zeros_like(rewards, device=device)
                lastgaelam = 0.0
                for t in reversed(range(cfg.num_steps)):
                    if t == cfg.num_steps - 1:
                        nextnonterminal = 1.0 - next_done
                        nextvalues = next_value
                    else:
                        nextnonterminal = 1.0 - dones[t + 1]
                        nextvalues = values[t + 1]
                    delta = rewards[t] + cfg.gamma * nextvalues * nextnonterminal - values[t]
                    lastgaelam = delta + cfg.gamma * cfg.gae_lambda * nextnonterminal * lastgaelam
                    advantages[t] = lastgaelam
                returns = advantages + values

            b_boards = boards.reshape((-1, *agent.board_shape))
            b_vectors = vectors.reshape((-1, VECTOR_FEATURE_DIM))
            b_masks = masks.reshape((-1, agent.action_dim))
            b_actions = actions.reshape(-1)
            b_logprobs = logprobs.reshape(-1)
            b_advantages = advantages.reshape(-1)
            b_returns = returns.reshape(-1)
            b_values = values.reshape(-1)

            indices = np.arange(batch_size)
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
            csv_writer.writerow({"global_step": global_step, "metric": "explained_variance", "value": explained_var})
            elapsed = max(time.time() - start_time, 1e-9)
            sps = int(global_step / elapsed)
            csv_writer.writerow({"global_step": global_step, "metric": "SPS", "value": float(sps)})
            metrics_file.flush()
            if writer is not None:
                writer.add_scalar("losses/policy_loss", pg_loss.item(), global_step)
                writer.add_scalar("losses/value_loss", v_loss.item(), global_step)
                writer.add_scalar("losses/explained_variance", explained_var, global_step)
                writer.add_scalar("charts/SPS", sps, global_step)

            current_best_candidate = _current_metric_value(
                cfg.best_checkpoint_metric,
                window=cfg.rolling_window_episodes,
                episode_scores=episode_scores,
                episode_returns=episode_returns,
                episode_wins=episode_wins,
                episode_max_chains=episode_max_chains,
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
                        episode_max_chains=episode_max_chains,
                        episode_sent_ojama=episode_sent_ojama,
                        episode_received_ojama=episode_received_ojama,
                        checkpoint_kind="best",
                        best_metric_value=best_metric_value,
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
                        episode_max_chains=episode_max_chains,
                        episode_sent_ojama=episode_sent_ojama,
                        episode_received_ojama=episode_received_ojama,
                        checkpoint_kind="periodic",
                        best_metric_value=best_metric_value,
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
                episode_max_chains=episode_max_chains,
                episode_sent_ojama=episode_sent_ojama,
                episode_received_ojama=episode_received_ojama,
                checkpoint_kind="latest",
                best_metric_value=best_metric_value,
            ),
            paths.checkpoint_path,
        )
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
            "mean_max_chain": _mean_last(episode_max_chains, cfg.rolling_window_episodes),
            "mean_sent_ojama": _mean_last(episode_sent_ojama, cfg.rolling_window_episodes),
            "mean_received_ojama": _mean_last(episode_received_ojama, cfg.rolling_window_episodes),
            "best_checkpoint_metric": cfg.best_checkpoint_metric,
            "best_metric_value": best_metric_value,
            "checkpoint_path": str(paths.checkpoint_path),
            "best_checkpoint_path": str(paths.best_checkpoint_path) if best_checkpoint_written else None,
            "periodic_checkpoints": periodic_checkpoints,
            "metrics_path": str(paths.metrics_path),
            "config_path": str(paths.config_path),
            "metadata_path": str(paths.metadata_path),
        }
        paths.summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        if writer is not None:
            writer.close()

    for env in envs:
        env.close()

    return {
        "run_id": paths.run_id,
        "run_dir": str(paths.run_dir),
        "global_step": global_step,
        "checkpoint_path": str(paths.checkpoint_path),
        "best_checkpoint_path": str(paths.best_checkpoint_path) if best_checkpoint_written else None,
        "metrics_path": str(paths.metrics_path),
        "config_path": str(paths.config_path),
        "metadata_path": str(paths.metadata_path),
        "summary_path": str(paths.summary_path),
        "mean_episode_score": _mean_last(episode_scores, cfg.rolling_window_episodes),
        "mean_win_rate": _mean_last(episode_wins, cfg.rolling_window_episodes),
        "mean_max_chain": _mean_last(episode_max_chains, cfg.rolling_window_episodes),
        "episodes": len(episode_scores),
    }
