"""Persistent key bindings for the graphical versus UI."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pygame


BINDING_DEFINITIONS = (
    ("open_settings", "Open key settings", ("f1",)),
    ("pause", "Pause / resume", ("p",)),
    ("step", "Advance one turn", ("n",)),
    ("speed_down", "Decrease speed", ("[", "-")),
    ("speed_up", "Increase speed", ("]", "=")),
    ("reset", "Reset match", ("r",)),
    ("quit", "Quit", ("escape", "x")),
    ("human_left", "Human: move left", ("a", "left")),
    ("human_right", "Human: move right", ("d", "right")),
    ("rotate_left", "Human: rotate left", ("q", "up")),
    ("rotate_right", "Human: rotate right", ("e", "down")),
    ("drop", "Human: place pair", ("s", "return", "space")),
)

ACTION_ORDER = tuple(action for action, _, _ in BINDING_DEFINITIONS)
ACTION_LABELS = {action: label for action, label, _ in BINDING_DEFINITIONS}
DEFAULT_BINDINGS = {
    action: list(key_names)
    for action, _, key_names in BINDING_DEFINITIONS
}


def default_keybindings_path() -> Path:
    config_root = os.environ.get("XDG_CONFIG_HOME")
    if config_root:
        return Path(config_root) / "puyo_ai_dev_platform" / "versus_ui_keybindings.json"
    return Path.home() / ".config" / "puyo_ai_dev_platform" / "versus_ui_keybindings.json"


class KeyBindings:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path).expanduser() if path else default_keybindings_path()
        self.bindings = self._defaults()
        self.load()

    def _defaults(self) -> dict[str, list[str]]:
        return {action: list(names) for action, names in DEFAULT_BINDINGS.items()}

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(stored, dict):
            return
        for action in ACTION_ORDER:
            names = stored.get(action)
            if not isinstance(names, list) or not names:
                continue
            valid_names = []
            for name in names:
                if not isinstance(name, str):
                    continue
                try:
                    pygame.key.key_code(name)
                except ValueError:
                    continue
                valid_names.append(name)
            if valid_names:
                self.bindings[action] = valid_names

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.bindings, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def matches(self, action: str, key: int) -> bool:
        return any(pygame.key.key_code(name) == key for name in self.bindings[action])

    def display_names(self, action: str) -> str:
        return " / ".join(self._display_name(name) for name in self.bindings[action])

    def _display_name(self, name: str) -> str:
        display = name.upper()
        replacements = {
            "RETURN": "ENTER",
            "LEFT BRACKET": "[",
            "RIGHT BRACKET": "]",
        }
        return replacements.get(display, display)

    def rebind(self, action: str, key: int) -> None:
        previous = {name: list(keys) for name, keys in self.bindings.items()}
        new_name = pygame.key.name(key)
        old_primary = self.bindings[action][0]
        for other_action in ACTION_ORDER:
            if other_action == action or new_name not in self.bindings[other_action]:
                continue
            remaining = [name for name in self.bindings[other_action] if name != new_name]
            if not remaining and old_primary != new_name:
                remaining = [old_primary]
            if not remaining:
                remaining = [DEFAULT_BINDINGS[other_action][0]]
            self.bindings[other_action] = remaining
        self.bindings[action] = [new_name]
        try:
            self.save()
        except OSError:
            self.bindings = previous
            raise

    def reset_defaults(self) -> None:
        previous = self.bindings
        self.bindings = self._defaults()
        try:
            self.save()
        except OSError:
            self.bindings = previous
            raise
