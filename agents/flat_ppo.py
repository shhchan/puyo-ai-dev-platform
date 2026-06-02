"""CleanRL-style flat PPO training loop for the Phase 1 single-player env."""

from __future__ import annotations

import csv
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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

from puyo_env.single_env import SinglePuyoEnv

from .networks import PuyoActorCritic, VECTOR_FEATURE_DIM


@dataclass
class FlatPPOConfig:
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
    log_dir: str = "runs/flat_ppo"
    checkpoint_path: str = "runs/flat_ppo/puyo_flat_ppo.pt"
    device: str = "cpu"


def _require_deps():
    if np is None or torch is None or optim is None:
        raise ImportError("flat PPO training requires numpy and torch. Install dependencies with requirements.txt.")


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


def train_flat_ppo(config: FlatPPOConfig | None = None) -> dict[str, Any]:
    """Train a flat masked PPO agent and write scalar logs/checkpoint."""

    _require_deps()
    cfg = config or FlatPPOConfig()
    if cfg.num_envs <= 0 or cfg.num_steps <= 0:
        raise ValueError("num_envs and num_steps must be positive")

    run_dir = Path(cfg.log_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = run_dir / "metrics.csv"
    checkpoint_path = Path(cfg.checkpoint_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    random.seed(cfg.seed)
    np.random.seed(cfg.seed)
    torch.manual_seed(cfg.seed)
    torch.backends.cudnn.deterministic = True

    device = torch.device(cfg.device)
    envs = [
        SinglePuyoEnv(seed=cfg.seed + env_index * 10_000, max_steps=cfg.max_episode_steps)
        for env_index in range(cfg.num_envs)
    ]
    observations = []
    infos = []
    for env_index, env in enumerate(envs):
        obs, info = env.reset(seed=cfg.seed + env_index)
        observations.append(obs)
        infos.append(info)

    agent = PuyoActorCritic(vector_dim=VECTOR_FEATURE_DIM).to(device)
    optimizer = optim.Adam(agent.parameters(), lr=cfg.learning_rate, eps=1e-5)
    writer = _make_writer(run_dir)
    if writer is not None:
        writer.add_text("hyperparameters", "\n".join(f"{key}: {value}" for key, value in asdict(cfg).items()))

    batch_size = cfg.num_envs * cfg.num_steps
    minibatch_size = min(cfg.minibatch_size, batch_size)
    global_step = 0
    start_time = time.time()
    episode_scores: list[float] = []
    episode_returns: list[float] = []

    with metrics_path.open("w", newline="", encoding="utf-8") as metrics_file:
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
                        episode_returns.append(episode_return)
                        episode_scores.append(episode_score)
                        csv_writer.writerow({"global_step": global_step, "metric": "episodic_return", "value": episode_return})
                        csv_writer.writerow({"global_step": global_step, "metric": "episodic_score", "value": episode_score})
                        if writer is not None:
                            writer.add_scalar("charts/episodic_return", episode_return, global_step)
                            writer.add_scalar("charts/episodic_score", episode_score, global_step)
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
            metrics_file.flush()
            if writer is not None:
                writer.add_scalar("losses/policy_loss", pg_loss.item(), global_step)
                writer.add_scalar("losses/value_loss", v_loss.item(), global_step)
                writer.add_scalar("losses/explained_variance", explained_var, global_step)
                writer.add_scalar("charts/SPS", int(global_step / (time.time() - start_time)), global_step)

        torch.save(
            {
                "model_state_dict": agent.state_dict(),
                "config": asdict(cfg),
                "global_step": global_step,
                "episode_scores": episode_scores,
                "episode_returns": episode_returns,
            },
            checkpoint_path,
        )
        if writer is not None:
            writer.close()

    for env in envs:
        env.close()

    return {
        "global_step": global_step,
        "checkpoint_path": str(checkpoint_path),
        "metrics_path": str(metrics_path),
        "mean_episode_score": float(np.mean(episode_scores[-10:])) if episode_scores else None,
        "episodes": len(episode_scores),
    }
