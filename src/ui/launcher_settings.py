"""Settings, registry discovery, and preset persistence for the launcher."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from train.artifacts import CHECKPOINT_SCHEMA_VERSION, validate_checkpoint_payload

try:
    import yaml
except ImportError:  # pragma: no cover - optional config validation
    yaml = None


SETTINGS_SCHEMA_VERSION = "puyo.launcher_settings.v1"
PRESET_SCHEMA_VERSION = "puyo.launcher_presets.v1"
POLICY_CHOICES = (
    "human", "first", "random", "greedy", "beam", "checkpoint", "manager", "manager_rule",
    "worker_large", "worker_quick", "worker_punish", "worker_counter", "worker_fire",
    "worker_fire_max", "worker_survival",
)
REALTIME_POLICY_CHOICES = POLICY_CHOICES
SPEED_CHOICES = (0.25, 0.5, 1.0, 2.0, 4.0)


@dataclass(frozen=True)
class RegistryEntry:
    path: str
    label: str
    source: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class LauncherFieldSpec:
    name: str
    label: str
    cli_option: str
    description: str


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
    device_a: str | None = None
    device_b: str | None = None
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
    inference_latency_ticks: int = 0
    timeout_ticks: int | None = None
    action_deadline_ticks: int | None = None
    use_reachable_action_mask: bool = False
    keybindings_path: str | None = None
    result_json: str | None = None
    max_frames: int | None = None
    paired_sides: bool = True
    replay_path: str | None = None
    config_path: str = "train/config/realtime_smoke.yaml"
    run_id: str = "launcher-smoke"

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
        return LauncherSettings(seed=57, config_path="train/config/realtime_smoke.yaml", run_id="launcher-smoke")
    return LauncherSettings()


FIELD_SPECS: dict[str, LauncherFieldSpec] = {
    "policy_a": LauncherFieldSpec("policy_a", "1P 方策", "--policy-a", "1P 側で使う policy を選びます。human、beam、manager_rule などは CLI 名のまま表示します。"),
    "policy_b": LauncherFieldSpec("policy_b", "2P 方策", "--policy-b", "2P 側で使う policy を選びます。checkpoint / manager を選ぶ場合は対応する checkpoint が必要です。"),
    "checkpoint_a": LauncherFieldSpec("checkpoint_a", "1P checkpoint", "--checkpoint-a", "1P 側 policy が checkpoint または manager のときに読み込むモデル path です。"),
    "checkpoint_b": LauncherFieldSpec("checkpoint_b", "2P checkpoint", "--checkpoint-b", "2P 側 policy が checkpoint または manager のときに読み込むモデル path です。"),
    "seed": LauncherFieldSpec("seed", "対戦 seed", "--seed", "環境・つも系列を再現するための共通 seed です。"),
    "seed_a": LauncherFieldSpec("seed_a", "1P policy seed", "--seed-a", "1P 側 policy 専用の乱数 seed です。auto の場合は共通 seed から決まります。"),
    "seed_b": LauncherFieldSpec("seed_b", "2P policy seed", "--seed-b", "2P 側 policy 専用の乱数 seed です。auto の場合は共通 seed から決まります。"),
    "max_steps": LauncherFieldSpec("max_steps", "最大 step", "--max-steps", "ターン制 versus UI の最大 step 数です。"),
    "max_ticks": LauncherFieldSpec("max_ticks", "最大 tick", "--max-ticks", "realtime UI / arena を何 tick まで進めるかを指定します。"),
    "speed": LauncherFieldSpec("speed", "再生速度", "--speed", "画面上の進行速度倍率です。0.25 から 4.0 まで選べます。"),
    "start_paused": LauncherFieldSpec("start_paused", "一時停止開始", "--start-paused", "ON の場合、画面を開いた直後に pause した状態で開始します。"),
    "device": LauncherFieldSpec("device", "共通 device", "--device", "checkpoint / manager 推論で使う device です。通常は cpu を使います。"),
    "device_a": LauncherFieldSpec("device_a", "1P device", "--device-a", "1P 側だけ device を上書きします。auto の場合は共通 device を使います。"),
    "device_b": LauncherFieldSpec("device_b", "2P device", "--device-b", "2P 側だけ device を上書きします。auto の場合は共通 device を使います。"),
    "deterministic": LauncherFieldSpec("deterministic", "決定的推論", "--stochastic", "ON の場合は決定的に行動します。OFF の場合は stochastic sampling を使います。"),
    "deterministic_a": LauncherFieldSpec("deterministic_a", "1P 決定的推論", "--deterministic-a / --stochastic-a", "1P 側だけ決定的推論を上書きします。auto の場合は共通設定を使います。"),
    "deterministic_b": LauncherFieldSpec("deterministic_b", "2P 決定的推論", "--deterministic-b / --stochastic-b", "2P 側だけ決定的推論を上書きします。auto の場合は共通設定を使います。"),
    "beam_depth": LauncherFieldSpec("beam_depth", "beam 深さ", "--beam-depth", "beam policy の共通探索深さです。"),
    "beam_width": LauncherFieldSpec("beam_width", "beam 幅", "--beam-width", "beam policy の共通探索候補数です。"),
    "beam_scenarios": LauncherFieldSpec("beam_scenarios", "beam scenarios", "--beam-scenarios", "beam policy が見る未来 scenario 数です。"),
    "beam_minimum_chain": LauncherFieldSpec("beam_minimum_chain", "beam 最小連鎖", "--beam-minimum-chain", "beam policy が優先する最小 chain 数です。"),
    "beam_depth_a": LauncherFieldSpec("beam_depth_a", "1P beam 深さ", "--beam-depth-a", "1P 側だけ beam 探索深さを上書きします。auto の場合は共通値を使います。"),
    "beam_depth_b": LauncherFieldSpec("beam_depth_b", "2P beam 深さ", "--beam-depth-b", "2P 側だけ beam 探索深さを上書きします。auto の場合は共通値を使います。"),
    "beam_width_a": LauncherFieldSpec("beam_width_a", "1P beam 幅", "--beam-width-a", "1P 側だけ beam 探索候補数を上書きします。auto の場合は共通値を使います。"),
    "beam_width_b": LauncherFieldSpec("beam_width_b", "2P beam 幅", "--beam-width-b", "2P 側だけ beam 探索候補数を上書きします。auto の場合は共通値を使います。"),
    "beam_scenarios_a": LauncherFieldSpec("beam_scenarios_a", "1P scenarios", "--beam-scenarios-a", "1P 側だけ beam scenario 数を上書きします。auto の場合は共通値を使います。"),
    "beam_scenarios_b": LauncherFieldSpec("beam_scenarios_b", "2P scenarios", "--beam-scenarios-b", "2P 側だけ beam scenario 数を上書きします。auto の場合は共通値を使います。"),
    "beam_minimum_chain_a": LauncherFieldSpec("beam_minimum_chain_a", "1P 最小連鎖", "--beam-minimum-chain-a", "1P 側だけ beam 最小 chain 数を上書きします。auto の場合は共通値を使います。"),
    "beam_minimum_chain_b": LauncherFieldSpec("beam_minimum_chain_b", "2P 最小連鎖", "--beam-minimum-chain-b", "2P 側だけ beam 最小 chain 数を上書きします。auto の場合は共通値を使います。"),
    "inference_latency_ticks": LauncherFieldSpec("inference_latency_ticks", "推論 latency", "--inference-latency-ticks", "AI の決定が反映されるまでの遅延 tick 数です。"),
    "timeout_ticks": LauncherFieldSpec("timeout_ticks", "timeout tick", "--timeout-ticks", "AI decision の timeout tick です。auto の場合は timeout なしです。"),
    "action_deadline_ticks": LauncherFieldSpec("action_deadline_ticks", "deadline tick", "--action-deadline-ticks", "AI action を deadline miss と扱う tick 数です。auto の場合は無効です。"),
    "use_reachable_action_mask": LauncherFieldSpec("use_reachable_action_mask", "到達可能 mask", "--use-reachable-action-mask", "ON の場合、realtime 環境で到達可能な action だけを policy に渡します。"),
    "keybindings_path": LauncherFieldSpec("keybindings_path", "キー設定 path", "--keybindings", "keybindings JSON の path です。auto の場合は既定 path を使います。"),
    "result_json": LauncherFieldSpec("result_json", "結果 JSON", "--result-json", "realtime UI smoke 結果を書き出す JSON path です。auto の場合は書き出しません。"),
    "max_frames": LauncherFieldSpec("max_frames", "最大 frame", "--max-frames", "UI smoke 用に描画 frame 数で停止する設定です。auto の場合は停止しません。"),
    "games": LauncherFieldSpec("games", "試合数", "--games", "arena で実行する game 数です。"),
    "paired_sides": LauncherFieldSpec("paired_sides", "左右入替評価", "--paired-sides", "ON の場合、arena で 1P/2P を入れ替えた paired evaluation を行います。"),
    "replay_path": LauncherFieldSpec("replay_path", "replay path", "--replay", "arena の replay 出力 path です。auto の場合は書き出しません。"),
    "config_path": LauncherFieldSpec("config_path", "学習 config", "--config", "train.train_realtime に渡す YAML/JSON config path です。"),
    "run_id": LauncherFieldSpec("run_id", "run_id", "--set run_id=...", "training smoke の run_id です。"),
}


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
            return ("config_path", "run_id", "seed")
        if action_key == "play":
            return (
                "policy_a",
                "policy_b",
                "checkpoint_a",
                "checkpoint_b",
                "seed",
                "seed_a",
                "seed_b",
                "max_steps",
                "speed",
                "start_paused",
                "device",
                "device_a",
                "device_b",
                "deterministic",
                "deterministic_a",
                "deterministic_b",
                "beam_depth",
                "beam_width",
                "beam_scenarios",
                "beam_minimum_chain",
                "beam_depth_a",
                "beam_depth_b",
                "beam_width_a",
                "beam_width_b",
                "beam_scenarios_a",
                "beam_scenarios_b",
                "beam_minimum_chain_a",
                "beam_minimum_chain_b",
                "keybindings_path",
            )
        if action_key == "spectate":
            return (
                "policy_a",
                "policy_b",
                "checkpoint_a",
                "checkpoint_b",
                "seed",
                "seed_a",
                "seed_b",
                "max_ticks",
                "speed",
                "start_paused",
                "device",
                "device_a",
                "device_b",
                "deterministic",
                "deterministic_a",
                "deterministic_b",
                "beam_depth",
                "beam_width",
                "beam_scenarios",
                "beam_minimum_chain",
                "beam_depth_a",
                "beam_depth_b",
                "beam_width_a",
                "beam_width_b",
                "beam_scenarios_a",
                "beam_scenarios_b",
                "beam_minimum_chain_a",
                "beam_minimum_chain_b",
                "inference_latency_ticks",
                "timeout_ticks",
                "action_deadline_ticks",
                "use_reachable_action_mask",
                "keybindings_path",
                "result_json",
                "max_frames",
            )
        if action_key == "arena":
            return (
                "policy_a",
                "policy_b",
                "checkpoint_a",
                "checkpoint_b",
                "games",
                "seed",
                "max_ticks",
                "device",
                "beam_depth",
                "beam_width",
                "beam_scenarios",
                "beam_minimum_chain",
                "inference_latency_ticks",
                "timeout_ticks",
                "action_deadline_ticks",
                "paired_sides",
                "replay_path",
            )
        return ()

    def field_label(self, action_key: str, field: str) -> str:
        _ = action_key
        spec = FIELD_SPECS.get(field)
        value = getattr(self.for_action(action_key), field)
        label = spec.label if spec is not None else field
        return f"{label}: {_display_value(value)}"

    def field_help(self, action_key: str, field: str) -> str:
        _ = action_key
        spec = FIELD_SPECS.get(field)
        if spec is None:
            return field
        return f"{spec.cli_option} / {spec.description}"

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
        if field == "run_id":
            return self.update(action_key, field, _cycle_value(value, ("launcher-smoke", "launcher-check", "manual-gui"), delta))
        if field in {"device", "device_a", "device_b"}:
            choices = ("cpu", "cuda") if field == "device" else (None, "cpu", "cuda")
            return self.update(action_key, field, _cycle_value(value, choices, delta))
        if field in {"keybindings_path", "result_json", "replay_path"}:
            choices_by_field = {
                "keybindings_path": (None, "/tmp/puyo-keybindings.json"),
                "result_json": (None, "/tmp/puyo-realtime-ui-result.json"),
                "replay_path": (None, "/tmp/puyo-arena-replay.json"),
            }
            return self.update(action_key, field, _cycle_value(value, choices_by_field[field], delta))
        if field == "speed":
            return self.update(action_key, field, _cycle_value(value, SPEED_CHOICES, delta))
        if field in {"deterministic", "start_paused", "use_reachable_action_mask", "paired_sides"}:
            return self.update(action_key, field, not bool(value))
        if field in {"deterministic_a", "deterministic_b"}:
            return self.update(action_key, field, _cycle_value(value, (None, True, False), delta))
        if field in {"seed", "seed_a", "seed_b", "max_steps", "max_ticks", "games", "inference_latency_ticks"}:
            current = int(value) if value is not None else self.for_action(action_key).seed
            step = 10 if field in {"max_steps", "max_ticks"} else 1
            return self.update(action_key, field, max(1, current + delta * step))
        if field in {"timeout_ticks", "action_deadline_ticks", "max_frames"}:
            if value is None:
                return self.update(action_key, field, 1 if delta > 0 else None)
            updated = int(value) + delta
            return self.update(action_key, field, updated if updated > 0 else None)
        if field.startswith("beam_"):
            if field.endswith("_a") or field.endswith("_b"):
                base_name = field.rsplit("_", 1)[0]
                fallback = getattr(settings, base_name, 1)
            else:
                fallback = 1
            current = int(value) if value is not None else int(fallback)
            updated = current + delta
            if "scenarios" in field:
                updated = min(6, max(1, updated))
            else:
                updated = max(1, updated)
            return self.update(action_key, field, updated)
        return settings

    def field_kind(self, action_key: str, field: str) -> str:
        value = getattr(self.for_action(action_key), field)
        if field in {"checkpoint_a", "checkpoint_b", "config_path", "run_id", "device", "device_a", "device_b", "keybindings_path", "result_json", "replay_path"}:
            return "string"
        if isinstance(value, bool) or field in {"deterministic_a", "deterministic_b"}:
            return "choice"
        if isinstance(value, (int, float)) or field in {"seed_a", "seed_b", "timeout_ticks", "action_deadline_ticks", "max_frames"}:
            return "number"
        return "choice"

    def field_choices(self, action_key: str, field: str) -> tuple[Any, ...]:
        settings = self.for_action(action_key)
        if field in {"policy_a", "policy_b"}:
            return self.policy_choices(action_key)
        if field in {"checkpoint_a", "checkpoint_b"}:
            return (None,) + tuple(entry.path for entry in self.registry.checkpoint_entries())
        if field == "config_path":
            return tuple(entry.path for entry in self.registry.config_entries())
        if field == "run_id":
            return ("launcher-smoke", "launcher-check", "manual-gui")
        if field in {"device", "device_a", "device_b"}:
            return ("cpu", "cuda") if field == "device" else (None, "cpu", "cuda")
        if field in {"keybindings_path", "result_json", "replay_path"}:
            return {
                "keybindings_path": (None, "/tmp/puyo-keybindings.json"),
                "result_json": (None, "/tmp/puyo-realtime-ui-result.json"),
                "replay_path": (None, "/tmp/puyo-arena-replay.json"),
            }[field]
        if field == "speed":
            return SPEED_CHOICES
        return (getattr(settings, field),)

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
            if settings.max_steps <= 0:
                errors.append("max_steps must be positive")
            if settings.max_ticks <= 0:
                errors.append("max_ticks must be positive")
            if settings.games <= 0:
                errors.append("games must be positive")
            if settings.inference_latency_ticks < 0:
                errors.append("inference_latency_ticks must be non-negative")
            if settings.timeout_ticks is not None and settings.timeout_ticks < 0:
                errors.append("timeout_ticks must be non-negative")
            if settings.action_deadline_ticks is not None and settings.action_deadline_ticks < 0:
                errors.append("action_deadline_ticks must be non-negative")
            if settings.max_frames is not None and settings.max_frames <= 0:
                errors.append("max_frames must be positive")
        if action_key == "training":
            errors.extend(self._validate_config_path(settings.config_path))
        return errors

    def _validate_checkpoint(self, side: str, policy: str, checkpoint: str | None) -> list[str]:
        if not checkpoint:
            return [f"checkpoint_{side} is required when policy_{side}={policy}"]
        path = resolve_repo_path(checkpoint, self.repo_root)
        if not path.exists():
            return [f"checkpoint_{side} does not exist: {checkpoint}"]
        if path.suffix != ".pt":
            return []
        try:
            import torch
        except (ImportError, OSError):  # pragma: no cover - optional checkpoint inspection
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
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    return str(value)


def _cycle_value(value: Any, choices: tuple[Any, ...], delta: int) -> Any:
    if not choices:
        return value
    try:
        index = choices.index(value)
    except ValueError:
        index = -1 if delta > 0 else 0
    return choices[(index + delta) % len(choices)]
