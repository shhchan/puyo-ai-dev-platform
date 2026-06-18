"""Exact restore helpers for training checkpoints."""

from __future__ import annotations

import hashlib
import pickle
import random
from pathlib import Path
from typing import Any, Mapping

try:
    import numpy as np
except ImportError:  # pragma: no cover - dependency guard
    np = None

try:
    import torch
except ImportError:  # pragma: no cover - dependency guard
    torch = None

from train.artifacts import validate_checkpoint_payload


class RestoreError(ValueError):
    """Raised when a checkpoint cannot satisfy the requested restore mode."""


def capture_rng_state() -> dict[str, Any]:
    state: dict[str, Any] = {"python_random": random.getstate()}
    if np is not None:
        state["numpy_random"] = np.random.get_state()
    if torch is not None:
        state["torch_random"] = torch.get_rng_state()
        if torch.cuda.is_available():
            state["torch_cuda_random_all"] = torch.cuda.get_rng_state_all()
    return state


def restore_rng_state(state: Mapping[str, Any]) -> None:
    if "python_random" not in state:
        raise RestoreError("checkpoint is missing python RNG state")
    random.setstate(state["python_random"])
    if np is not None:
        if "numpy_random" not in state:
            raise RestoreError("checkpoint is missing NumPy RNG state")
        np.random.set_state(state["numpy_random"])
    if torch is not None:
        if "torch_random" not in state:
            raise RestoreError("checkpoint is missing Torch RNG state")
        torch.set_rng_state(state["torch_random"])
        if torch.cuda.is_available():
            cuda_state = state.get("torch_cuda_random_all")
            if cuda_state is None:
                raise RestoreError("checkpoint is missing Torch CUDA RNG state")
            torch.cuda.set_rng_state_all(cuda_state)


def _hash_value(digest, value: Any) -> None:
    if torch is not None and torch.is_tensor(value):
        tensor = value.detach().cpu().contiguous()
        digest.update(str(tuple(tensor.shape)).encode("utf-8"))
        digest.update(str(tensor.dtype).encode("utf-8"))
        digest.update(tensor.numpy().tobytes())
        return
    if isinstance(value, Mapping):
        for key in sorted(value):
            digest.update(str(key).encode("utf-8"))
            _hash_value(digest, value[key])
        return
    if isinstance(value, (list, tuple)):
        digest.update(str(type(value)).encode("utf-8"))
        for item in value:
            _hash_value(digest, item)
        return
    digest.update(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL))


def state_hash(*values: Any) -> str:
    digest = hashlib.sha256()
    for value in values:
        _hash_value(digest, value)
    return digest.hexdigest()


def checkpoint_state_hash(checkpoint: Mapping[str, Any]) -> str:
    selected = {
        "model_state_dict": checkpoint.get("model_state_dict"),
        "optimizer_state_dict": checkpoint.get("optimizer_state_dict"),
        "rng_state": checkpoint.get("rng_state"),
        "trainer_state": checkpoint.get("trainer_state"),
        "global_step": checkpoint.get("global_step"),
    }
    return state_hash(selected)


def load_training_checkpoint(
    path: str | Path,
    *,
    map_location: str | Any = "cpu",
    expected_trainer_name: str | None = None,
    require_exact: bool = False,
) -> dict[str, Any]:
    if torch is None:
        raise ImportError("training checkpoint restore requires torch")
    checkpoint = torch.load(Path(path), map_location=map_location, weights_only=False)
    if not isinstance(checkpoint, dict):
        raise RestoreError("checkpoint must be a mapping")
    errors = validate_checkpoint_payload(checkpoint)
    if errors:
        raise RestoreError("; ".join(errors))
    schema = checkpoint["checkpoint_schema"]
    if expected_trainer_name is not None and schema.get("trainer_name") != expected_trainer_name:
        raise RestoreError(
            f"checkpoint trainer mismatch: {schema.get('trainer_name')} != {expected_trainer_name}"
        )
    resume_contract = schema.get("resume_contract", {})
    if require_exact:
        if not resume_contract.get("has_optimizer_state"):
            raise RestoreError("exact resume requires optimizer_state_dict")
        if not resume_contract.get("has_rng_state"):
            raise RestoreError("exact resume requires rng_state")
        if not resume_contract.get("has_trainer_state"):
            raise RestoreError("exact resume requires trainer_state")
    return checkpoint


def assert_resume_config_compatible(
    checkpoint: Mapping[str, Any],
    current_config: Mapping[str, Any],
    *,
    allowed_differences: set[str] | None = None,
) -> None:
    checkpoint_config = checkpoint.get("config")
    if not isinstance(checkpoint_config, Mapping):
        raise RestoreError("checkpoint is missing saved config")
    allowed = set(allowed_differences or set())
    errors = []
    for key, saved_value in checkpoint_config.items():
        if key in allowed:
            continue
        if key not in current_config:
            errors.append(f"{key}: missing in current config")
            continue
        if current_config[key] != saved_value:
            errors.append(f"{key}: {current_config[key]!r} != {saved_value!r}")
    if errors:
        raise RestoreError("resume config mismatch: " + "; ".join(errors))
