"""Settings, registry discovery, and preset persistence for the launcher."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from eval.realtime_versus_ui import REALTIME_POLICY_CHOICES
from eval.versus_ui import POLICY_CHOICES, SPEED_CHOICES
from train.artifacts import CHECKPOINT_SCHEMA_VERSION, validate_checkpoint_payload

try:
    import torch
except (ImportError, OSError):  # pragma: no cover - optional checkpoint inspection
    torch = None

try:
    import yaml
except ImportError:  # pragma: no cover - optional config validation
    yaml = None


SETTINGS_SCHEMA_VERSION = "puyo.launcher_settings.v1"
PRESET_SCHEMA_VERSION = "puyo.launcher_presets.v1"


@dataclass(frozen=True)
class RegistryEntry:
    path: str
    label: str
    source: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class LauncherSettings:
    policy_a: str = "first"
    policy_b: str = "random"
    checkpoint_a: str | None = None
    checkpoint_b: str | None = None
    seed: int = 57
    seed_a: int | None = None
    seed_b: int | None = None
    speed: float = 1.0
    start_paused: bool = True
    device: str = "cpu"
    deterministic: bool = True
    deterministic_a: bool | None = None
    deterministic_b: bool | None = None
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
    max_steps: int = 100
    max_ticks: int = 600
    games: int = 1
    config_path: str = "train/config/realtime_smoke.yaml"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = SETTINGS_SCHEMA_VERSION
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LauncherSettings":
        allowed = {field for field in cls.__dataclass_fields__}
        values = {key: value for key, value in data.items() if key in allowed}
        return cls(**values)


def default_settings(action_key: str) -> LauncherSettings:
    if action_key == "play":
        return LauncherSettings(policy_a="human", policy_b="greedy", seed=57, max_steps=100, start_paused=True)
    if action_key == "spectate":
        return LauncherSettings(policy_a="first", policy_b="random", seed=57, max_ticks=600, start_paused=True)
    if action_key == "arena":
        return LauncherSettings(policy_a="first", policy_b="random", seed=57, max_ticks=180, games=1)
    if action_key == "training":
        return LauncherSettings(seed=57, config_path="train/config/realtime_smoke.yaml")
    return LauncherSettings()


class ArtifactRegistryClient:
    """Discovers local configs and checkpoint artifacts without requiring a DB."""

    def __init__(self, repo_root: str | Path, roots: tuple[str, ...] = ("runs", "docs/benchmarks")):
        self.repo_root = Path(repo_root)
        self.roots = roots

    def config_entries(self) -> tuple[RegistryEntry, ...]:
        config_root = self.repo_root / "train" / "config"
        entries = []
        for pattern in ("*.yaml", "*.yml", "*.json"):
            for path in sorted(config_root.glob(pattern)):
                entries.append(
                    RegistryEntry(
                        path=_display_repo_path(path, self.repo_root),
                        label=path.name,
                        source="train/config",
                        metadata={},
                    )
                )
        return tuple(entries)

    def checkpoint_entries(self) -> tuple[RegistryEntry, ...]:
        discovered: dict[str, RegistryEntry] = {}
        for root_name in self.roots:
            root = self.repo_root / root_name
            if not root.exists():
                continue
            for manifest_path in sorted(root.rglob("artifact_manifest.json")):
                for entry in self._entries_from_manifest(manifest_path):
                    discovered.setdefault(entry.path, entry)
            for checkpoint_path in sorted(root.rglob("*.pt")):
                display_path = _display_repo_path(checkpoint_path, self.repo_root)
                discovered.setdefault(
                    display_path,
                    RegistryEntry(
                        path=display_path,
                        label=checkpoint_path.name,
                        source="manual-scan",
                        metadata={},
                    ),
                )
        return tuple(sorted(discovered.values(), key=lambda entry: entry.path))

    def _entries_from_manifest(self, manifest_path: Path) -> list[RegistryEntry]:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(manifest, Mapping):
            return []
        run = manifest.get("run", {})
        run_id = str(run.get("run_id") or manifest_path.parent.name) if isinstance(run, Mapping) else manifest_path.parent.name
        entries = []
        for record in manifest.get("checkpoints", []):
            if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
                continue
            path = Path(record["path"])
            if not path.is_absolute():
                path = manifest_path.parent / path
            entries.append(
                RegistryEntry(
                    path=_display_repo_path(path, self.repo_root),
                    label=f"{run_id}:{record.get('role', path.name)}",
                    source=_display_repo_path(manifest_path, self.repo_root),
                    metadata={
                        "run_id": run_id,
                        "role": record.get("role"),
                        "sha256": record.get("sha256"),
                        "schema_version": manifest.get("schema_version"),
                    },
                )
            )
        return entries


class LauncherPresetStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else Path.home() / ".config" / "puyo_ai_dev_platform" / "launcher_presets.json"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": PRESET_SCHEMA_VERSION, "recent": {}, "presets": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": PRESET_SCHEMA_VERSION, "recent": {}, "presets": {}}
        if not isinstance(data, dict):
            return {"schema_version": PRESET_SCHEMA_VERSION, "recent": {}, "presets": {}}
        data.setdefault("schema_version", PRESET_SCHEMA_VERSION)
        data.setdefault("recent", {})
        data.setdefault("presets", {})
        return data

    def save(self, data: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(data)
        payload["schema_version"] = PRESET_SCHEMA_VERSION
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def recent(self, action_key: str) -> LauncherSettings | None:
        data = self.load().get("recent", {})
        if not isinstance(data, Mapping) or not isinstance(data.get(action_key), Mapping):
            return None
        return LauncherSettings.from_dict(data[action_key])

    def save_recent(self, action_key: str, settings: LauncherSettings) -> None:
        data = self.load()
        recent = data.setdefault("recent", {})
        recent[action_key] = settings.to_dict()
        self.save(data)

    def preset_names(self, action_key: str) -> tuple[str, ...]:
        presets = self.load().get("presets", {})
        action_presets = presets.get(action_key, {}) if isinstance(presets, Mapping) else {}
        if not isinstance(action_presets, Mapping):
            return ()
        return tuple(sorted(str(name) for name in action_presets))

    def save_preset(self, action_key: str, name: str, settings: LauncherSettings) -> None:
        data = self.load()
        presets = data.setdefault("presets", {})
        action_presets = presets.setdefault(action_key, {})
        action_presets[name] = settings.to_dict()
        self.save(data)

    def load_preset(self, action_key: str, name: str) -> LauncherSettings | None:
        presets = self.load().get("presets", {})
        action_presets = presets.get(action_key, {}) if isinstance(presets, Mapping) else {}
        preset = action_presets.get(name) if isinstance(action_presets, Mapping) else None
        if not isinstance(preset, Mapping):
            return None
        return LauncherSettings.from_dict(preset)


class LauncherSettingsManager:
    def __init__(
        self,
        *,
        repo_root: str | Path,
        store: LauncherPresetStore | None = None,
        registry: ArtifactRegistryClient | None = None,
    ):
        self.repo_root = Path(repo_root)
        self.store = store or LauncherPresetStore()
        self.registry = registry or ArtifactRegistryClient(self.repo_root)
        self._settings = {
            action: self.store.recent(action) or default_settings(action)
            for action in ("play", "spectate", "arena", "training", "models")
        }
        self._preset_indices: dict[str, int] = {}

    def for_action(self, action_key: str) -> LauncherSettings:
        return self._settings.get(action_key, default_settings(action_key))

    def update(self, action_key: str, field: str, value: Any) -> LauncherSettings:
        settings = self.for_action(action_key)
        if field not in LauncherSettings.__dataclass_fields__:
            raise KeyError(f"unknown launcher setting: {field}")
        updated = replace(settings, **{field: value})
        self._settings[action_key] = updated
        return updated

    def editable_fields(self, action_key: str) -> tuple[str, ...]:
        if action_key == "training":
            return ("config_path", "seed")
        if action_key in {"play", "spectate", "arena"}:
            return (
                "policy_a",
                "policy_b",
                "checkpoint_a",
                "checkpoint_b",
                "seed",
                "seed_a",
                "seed_b",
                "speed",
                "beam_depth_a",
                "beam_depth_b",
                "beam_width_a",
                "beam_width_b",
                "deterministic_a",
                "deterministic_b",
            )
        return ()

    def field_label(self, action_key: str, field: str) -> str:
        value = getattr(self.for_action(action_key), field)
        return f"{field}: {_display_value(value)}"

    def cycle(self, action_key: str, field: str, delta: int = 1) -> LauncherSettings:
        settings = self.for_action(action_key)
        value = getattr(settings, field)
        if field in {"policy_a", "policy_b"}:
            return self.update(action_key, field, _cycle_value(value, self.policy_choices(action_key), delta))
        if field in {"checkpoint_a", "checkpoint_b"}:
            choices = (None,) + tuple(entry.path for entry in self.registry.checkpoint_entries())
            return self.update(action_key, field, _cycle_value(value, choices, delta))
        if field == "config_path":
            choices = tuple(entry.path for entry in self.registry.config_entries())
            return self.update(action_key, field, _cycle_value(value, choices or (value,), delta))
        if field == "speed":
            return self.update(action_key, field, _cycle_value(value, SPEED_CHOICES, delta))
        if field in {"deterministic_a", "deterministic_b"}:
            return self.update(action_key, field, _cycle_value(value, (None, True, False), delta))
        if field in {"seed", "seed_a", "seed_b"}:
            current = int(value) if value is not None else self.for_action(action_key).seed
            return self.update(action_key, field, current + delta)
        if field.startswith("beam_depth") or field.startswith("beam_width"):
            current = int(value) if value is not None else getattr(settings, field.rsplit("_", 1)[0], 1)
            return self.update(action_key, field, max(1, current + delta))
        return settings

    def policy_choices(self, action_key: str) -> tuple[str, ...]:
        if action_key == "play":
            return POLICY_CHOICES
        return REALTIME_POLICY_CHOICES

    def save_recent(self, action_key: str) -> None:
        self.store.save_recent(action_key, self.for_action(action_key))

    def save_preset(self, action_key: str, name: str | None = None) -> str:
        preset_name = name or f"last-{action_key}"
        self.store.save_preset(action_key, preset_name, self.for_action(action_key))
        return preset_name

    def load_next_preset(self, action_key: str) -> str | None:
        names = self.store.preset_names(action_key)
        if not names:
            return None
        index = (self._preset_indices.get(action_key, -1) + 1) % len(names)
        self._preset_indices[action_key] = index
        preset = self.store.load_preset(action_key, names[index])
        if preset is not None:
            self._settings[action_key] = preset
        return names[index]

    def validate(self, action_key: str) -> list[str]:
        settings = self.for_action(action_key)
        errors: list[str] = []
        if action_key in {"play", "spectate", "arena"}:
            choices = self.policy_choices(action_key)
            for side in ("a", "b"):
                policy = getattr(settings, f"policy_{side}")
                if policy not in choices:
                    errors.append(f"policy_{side} must be one of: {', '.join(choices)}")
                checkpoint = getattr(settings, f"checkpoint_{side}")
                if policy in {"checkpoint", "manager"}:
                    errors.extend(self._validate_checkpoint(side, policy, checkpoint))
            if settings.speed not in SPEED_CHOICES and action_key != "arena":
                errors.append(f"speed must be one of: {SPEED_CHOICES}")
        if action_key == "training":
            errors.extend(self._validate_config_path(settings.config_path))
        return errors

    def _validate_checkpoint(self, side: str, policy: str, checkpoint: str | None) -> list[str]:
        if not checkpoint:
            return [f"checkpoint_{side} is required when policy_{side}={policy}"]
        path = resolve_repo_path(checkpoint, self.repo_root)
        if not path.exists():
            return [f"checkpoint_{side} does not exist: {checkpoint}"]
        if path.suffix != ".pt" or torch is None:
            return []
        try:
            payload = torch.load(path, map_location="cpu", weights_only=False)
        except Exception as exc:
            return [f"checkpoint_{side} could not be loaded: {exc}"]
        if not isinstance(payload, Mapping):
            return [f"checkpoint_{side} must contain a mapping payload"]
        errors = []
        if "checkpoint_schema" in payload:
            errors.extend(f"checkpoint_{side}: {error}" for error in validate_checkpoint_payload(payload))
            schema = payload.get("checkpoint_schema", {})
            if isinstance(schema, Mapping) and schema.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
                errors.append(f"checkpoint_{side}: unsupported checkpoint schema")
        if policy == "manager" and payload.get("policy_type") not in {None, "strategy_manager"}:
            errors.append(f"checkpoint_{side} is not a strategy manager checkpoint")
        if policy == "checkpoint" and payload.get("policy_type") == "strategy_manager":
            errors.append(f"checkpoint_{side} is a manager checkpoint; use policy_{side}=manager")
        if "model_state_dict" not in payload and not any(str(key).endswith("weight") for key in payload):
            errors.append(f"checkpoint_{side} is missing model_state_dict")
        return errors

    def _validate_config_path(self, config_path: str) -> list[str]:
        path = resolve_repo_path(config_path, self.repo_root)
        if not path.exists():
            return [f"config_path does not exist: {config_path}"]
        if path.suffix in {".yaml", ".yml"} and yaml is not None:
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception as exc:
                return [f"config_path could not be parsed: {exc}"]
            if not isinstance(data, Mapping):
                return [f"config_path must contain a mapping: {config_path}"]
        return []


def resolve_repo_path(path: str | Path, repo_root: Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else repo_root / candidate


def _display_repo_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path)


def _display_value(value: Any) -> str:
    if value is None:
        return "auto"
    return str(value)


def _cycle_value(value: Any, choices: tuple[Any, ...], delta: int) -> Any:
    if not choices:
        return value
    try:
        index = choices.index(value)
    except ValueError:
        index = -1 if delta > 0 else 0
    return choices[(index + delta) % len(choices)]
