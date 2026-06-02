"""Actor-critic networks for placement-level Puyo policies."""

from __future__ import annotations

try:
    import torch
    from torch import nn
    from torch.distributions.categorical import Categorical
except ImportError:  # pragma: no cover - dependency guard
    torch = None
    nn = None
    Categorical = None

from puyo_env.actions import NUM_ACTIONS
from puyo_env.obs import BOARD_COLOR_CHANNELS, BOARD_ROWS, GRID_WIDTH, SCALAR_FEATURE_DIM, VISIBLE_PAIR_COUNT


VECTOR_FEATURE_DIM = VISIBLE_PAIR_COUNT * 2 * 5 + SCALAR_FEATURE_DIM


def _require_torch():
    if torch is None or nn is None or Categorical is None:
        raise ImportError("agents.networks requires torch. Install dependencies with `pip install -r requirements.txt`.")


if torch is not None:

    def _layer_init(layer, std=1.0, bias_const=0.0):
        torch.nn.init.orthogonal_(layer.weight, std)
        torch.nn.init.constant_(layer.bias, bias_const)
        return layer


    class PuyoActorCritic(nn.Module):
        """CNN board encoder plus vector features with policy/value heads."""

        def __init__(
            self,
            board_shape: tuple[int, int, int] = (len(BOARD_COLOR_CHANNELS), BOARD_ROWS, GRID_WIDTH),
            vector_dim: int = VECTOR_FEATURE_DIM,
            action_dim: int = NUM_ACTIONS,
            hidden_dim: int = 256,
        ):
            super().__init__()
            board_channels, board_rows, board_cols = board_shape
            self.board_shape = board_shape
            self.vector_dim = vector_dim
            self.action_dim = action_dim

            self.cnn = nn.Sequential(
                _layer_init(nn.Conv2d(board_channels, 32, kernel_size=3, padding=1)),
                nn.ReLU(),
                _layer_init(nn.Conv2d(32, 64, kernel_size=3, padding=1)),
                nn.ReLU(),
                nn.Flatten(),
            )
            with torch.no_grad():
                cnn_out_dim = self.cnn(torch.zeros(1, board_channels, board_rows, board_cols)).shape[1]

            self.trunk = nn.Sequential(
                _layer_init(nn.Linear(cnn_out_dim + vector_dim, hidden_dim)),
                nn.ReLU(),
                _layer_init(nn.Linear(hidden_dim, hidden_dim)),
                nn.ReLU(),
            )
            self.actor = _layer_init(nn.Linear(hidden_dim, action_dim), std=0.01)
            self.critic = _layer_init(nn.Linear(hidden_dim, 1), std=1.0)

        def forward(self, board, vector_features, action_mask=None):
            board_features = self.cnn(board.float())
            hidden = self.trunk(torch.cat([board_features, vector_features.float()], dim=1))
            logits = self.actor(hidden)
            if action_mask is not None:
                mask = action_mask.bool()
                logits = logits.masked_fill(~mask, -1.0e9)
            value = self.critic(hidden).squeeze(-1)
            return logits, value

        def get_action_and_value(self, observation, action=None, action_mask=None):
            vector_features = observation.get("vector_features")
            if vector_features is None:
                next_pairs = observation["next_pairs"].reshape(observation["next_pairs"].shape[0], -1)
                vector_features = torch.cat([next_pairs, observation["scalars"]], dim=1)
            logits, value = self.forward(observation["board"], vector_features, action_mask=action_mask)
            distribution = Categorical(logits=logits)
            if action is None:
                action = distribution.sample()
            return action, distribution.log_prob(action), distribution.entropy(), value


else:

    class PuyoActorCritic:  # pragma: no cover - dependency guard
        def __init__(self, *args, **kwargs):
            _ = (args, kwargs)
            _require_torch()


def obs_to_tensors(observation, device="cpu"):
    """Convert one observation dict to batched torch tensors."""

    _require_torch()
    import numpy as np

    board = torch.as_tensor(np.asarray(observation["board"])[None, ...], dtype=torch.float32, device=device)
    next_pairs = torch.as_tensor(np.asarray(observation["next_pairs"])[None, ...], dtype=torch.float32, device=device)
    scalars = torch.as_tensor(np.asarray(observation["scalars"])[None, ...], dtype=torch.float32, device=device)
    return {"board": board, "next_pairs": next_pairs, "scalars": scalars}
