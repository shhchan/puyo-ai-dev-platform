"""Graphical versus viewer for AI and human placement policies."""

from __future__ import annotations

import argparse
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pygame
except ImportError:  # pragma: no cover - dependency guard
    pygame = None

from puyo_env.actions import PLACEMENT_ACTIONS, action_to_placement
from puyo_env.versus_env import AGENTS, VersusPuyoEnv
from selfplay.policies import Policy, legal_indices, make_policy
from src.core.constants import Direction

if pygame is not None:
    from src.ui.keybindings import ACTION_ORDER, KeyBindings
    from src.ui.versus_renderer import SCREEN_HEIGHT, SCREEN_WIDTH, VersusRenderer
else:  # pragma: no cover - used only for dependency-light config imports
    ACTION_ORDER = ()
    KeyBindings = None
    SCREEN_WIDTH = 1120
    SCREEN_HEIGHT = 720
    VersusRenderer = None


POLICY_CHOICES = ("human", "random", "greedy", "beam", "checkpoint")
SPEED_CHOICES = (0.25, 0.5, 1.0, 2.0, 4.0)
BASE_STEP_SECONDS = 0.7


@dataclass(frozen=True)
class VersusUiConfig:
    policy_a: str = "greedy"
    policy_b: str = "random"
    checkpoint_a: str | None = None
    checkpoint_b: str | None = None
    seed: int = 1
    max_steps: int = 100
    speed: float = 1.0
    start_paused: bool = False
    device: str = "cpu"
    deterministic: bool = True
    beam_depth: int = 5
    beam_width: int = 32
    beam_scenarios: int = 1
    keybindings_path: str | None = None


@dataclass(frozen=True)
class VisualEvent:
    kind: str
    agent: str
    label: str
    amount: int = 0
    action: int | None = None
    axis_y: int | None = None
    pair_colors: tuple | None = None
    coords: frozenset = frozenset()
    chain_index: int = 0


def validate_config(config: VersusUiConfig) -> None:
    policies = (config.policy_a, config.policy_b)
    if any(policy not in POLICY_CHOICES for policy in policies):
        raise ValueError(f"policy must be one of: {', '.join(POLICY_CHOICES)}")
    if policies.count("human") > 1:
        raise ValueError("only one human player is supported")
    if config.policy_a == "checkpoint" and not config.checkpoint_a:
        raise ValueError("--checkpoint-a is required when --policy-a=checkpoint")
    if config.policy_b == "checkpoint" and not config.checkpoint_b:
        raise ValueError("--checkpoint-b is required when --policy-b=checkpoint")
    if config.speed not in SPEED_CHOICES:
        raise ValueError(f"speed must be one of: {SPEED_CHOICES}")
    if config.max_steps <= 0:
        raise ValueError("max_steps must be positive")


def _player_pair(game) -> tuple | None:
    if game.current_puyo_1 is None or game.current_puyo_2 is None:
        return None
    return (game.current_puyo_1.color, game.current_puyo_2.color)


def build_visual_events(
    actions: dict[str, int],
    infos: dict,
    pair_colors: dict,
) -> list[VisualEvent]:
    """Build display-only events from the environment's authoritative step result."""

    events = []
    for agent in AGENTS:
        components = infos[agent].get("reward_components", {})
        received = int(components.get("garbage_received", 0))
        if received:
            events.append(VisualEvent("garbage", agent, f"OJAMA +{received}", amount=received))

    for agent in AGENTS:
        result = infos[agent].get("step_result")
        action = actions.get(agent)
        if result is None or action is None or not result.valid:
            continue
        events.append(
            VisualEvent(
                "placement",
                agent,
                "DROP",
                action=action,
                axis_y=result.axis_y,
                pair_colors=pair_colors.get(agent),
            )
        )

    max_chains = max(
        (len(getattr(infos[agent].get("step_result"), "chains", ())) for agent in AGENTS),
        default=0,
    )
    for chain_index in range(max_chains):
        for agent in AGENTS:
            result = infos[agent].get("step_result")
            chains = () if result is None else result.chains
            if chain_index >= len(chains):
                continue
            chain = chains[chain_index]
            events.append(
                VisualEvent(
                    "chain",
                    agent,
                    f"{chain.chain_index} CHAIN  +{chain.score}",
                    coords=chain.vanished,
                    chain_index=chain.chain_index,
                    amount=chain.score,
                )
            )
    return events


class HumanPlacement:
    def __init__(self, agent: str):
        self.agent = agent
        self.action = 0

    def reset(self, info: dict) -> None:
        choices = legal_indices(info)
        self.action = choices[0] if choices else 0

    def _legal(self, info: dict) -> list[int]:
        return legal_indices(info)

    def move(self, dx: int, info: dict) -> None:
        current = action_to_placement(self.action)
        choices = [
            index
            for index in self._legal(info)
            if action_to_placement(index).rotation == current.rotation
            and (action_to_placement(index).axis_x - current.axis_x) * dx > 0
        ]
        if not choices:
            return
        self.action = min(
            choices,
            key=lambda index: abs(action_to_placement(index).axis_x - current.axis_x),
        )

    def rotate(self, delta: int, info: dict) -> None:
        current = action_to_placement(self.action)
        directions = list(Direction)
        start = directions.index(current.rotation)
        legal = set(self._legal(info))
        for offset in range(1, len(directions) + 1):
            rotation = directions[(start + delta * offset) % len(directions)]
            for index, placement in enumerate(PLACEMENT_ACTIONS):
                if index in legal and placement.axis_x == current.axis_x and placement.rotation == rotation:
                    self.action = index
                    return


class VersusMatchController:
    def __init__(
        self,
        config: VersusUiConfig,
        policy_factory: Callable[..., Policy] = make_policy,
    ):
        validate_config(config)
        self.config = config
        self.policy_factory = policy_factory
        self.env = VersusPuyoEnv(seed=config.seed, max_steps=config.max_steps)
        self.speed = config.speed
        self.paused = config.start_paused
        self.event_queue: deque[VisualEvent] = deque()
        self.current_event: VisualEvent | None = None
        self.event_elapsed = 0.0
        self.step_elapsed = 0.0
        self.last_actions: dict[str, int] = {}
        if KeyBindings is None:
            raise ImportError("versus UI requires pygame; install requirements.txt")
        self.keybindings = KeyBindings(config.keybindings_path)
        self.settings_open = False
        self.settings_index = 0
        self.settings_capture = False
        self.settings_message = ""
        self._settings_previous_paused = self.paused
        self.policies: dict[str, Policy | None] = {}
        self.human: HumanPlacement | None = None
        self.observations = {}
        self.infos = {}
        self.reset()

    @property
    def policy_names(self) -> dict[str, str]:
        return {"player_0": self.config.policy_a, "player_1": self.config.policy_b}

    @property
    def winner(self) -> str | None:
        return self.infos.get("player_0", {}).get("winner")

    def _make_policy(self, side: str) -> Policy | None:
        policy_type = self.config.policy_a if side == "a" else self.config.policy_b
        if policy_type == "human":
            return None
        return self.policy_factory(
            policy_type,
            seed=self.config.seed + (0 if side == "a" else 10_000),
            checkpoint_path=self.config.checkpoint_a if side == "a" else self.config.checkpoint_b,
            device=self.config.device,
            deterministic=self.config.deterministic,
            beam_depth=self.config.beam_depth,
            beam_width=self.config.beam_width,
            beam_scenarios=self.config.beam_scenarios,
        )

    def reset(self) -> None:
        self.policies = {
            "player_0": self._make_policy("a"),
            "player_1": self._make_policy("b"),
        }
        self.observations, self.infos = self.env.reset(seed=self.config.seed)
        human_agents = [agent for agent, policy in self.policies.items() if policy is None]
        self.human = HumanPlacement(human_agents[0]) if human_agents else None
        if self.human:
            self.human.reset(self.infos[self.human.agent])
        self.event_queue.clear()
        self.current_event = None
        self.event_elapsed = 0.0
        self.step_elapsed = 0.0
        self.last_actions = {}

    def _selected_actions(self, include_human: bool) -> dict[str, int] | None:
        if not self.env.agents:
            return None
        actions = {}
        for agent in AGENTS:
            policy = self.policies[agent]
            if policy is None:
                if not include_human or self.human is None:
                    return None
                actions[agent] = self.human.action
            else:
                actions[agent] = policy.select_action(self.observations[agent], self.infos[agent])
        return actions

    def step_with_actions(self, actions: dict[str, int]) -> bool:
        if not self.env.agents:
            return False
        pair_colors = {
            agent: _player_pair(self.env.player_states[agent].simulator.game)
            for agent in AGENTS
        }
        self.observations, _, _, _, self.infos = self.env.step(actions)
        self.last_actions = dict(actions)
        self.event_queue.extend(build_visual_events(actions, self.infos, pair_colors))
        self._start_next_event()
        if self.human and self.env.agents:
            self.human.reset(self.infos[self.human.agent])
        return True

    def advance_one(self, include_human: bool = False) -> bool:
        if self.current_event or self.event_queue:
            if not self.paused:
                return False
            self.current_event = None
            self.event_queue.clear()
            self.event_elapsed = 0.0
        actions = self._selected_actions(include_human=include_human)
        return False if actions is None else self.step_with_actions(actions)

    def _start_next_event(self) -> None:
        if self.current_event is None and self.event_queue:
            self.current_event = self.event_queue.popleft()
            self.event_elapsed = 0.0

    def _event_duration(self) -> float:
        if self.current_event is None:
            return 0.0
        durations = {"garbage": 0.35, "placement": 0.25, "chain": 0.55}
        return durations.get(self.current_event.kind, 0.3) / self.speed

    def update(self, delta_time: float) -> None:
        if self.paused:
            return
        if self.current_event is not None:
            self.event_elapsed += delta_time
            if self.event_elapsed >= self._event_duration():
                self.current_event = None
                self._start_next_event()
            return
        if self.event_queue:
            self._start_next_event()
            return
        if self.human is not None or not self.env.agents:
            return
        self.step_elapsed += delta_time
        if self.step_elapsed >= BASE_STEP_SECONDS / self.speed:
            self.step_elapsed = 0.0
            self.advance_one()

    def change_speed(self, direction: int) -> None:
        index = SPEED_CHOICES.index(self.speed)
        self.speed = SPEED_CHOICES[max(0, min(len(SPEED_CHOICES) - 1, index + direction))]

    def _open_settings(self) -> None:
        self._settings_previous_paused = self.paused
        self.paused = True
        self.settings_open = True
        self.settings_capture = False
        self.settings_message = "Select an action and press Enter."

    def _close_settings(self) -> None:
        self.settings_open = False
        self.settings_capture = False
        self.paused = self._settings_previous_paused

    def _handle_settings_keydown(self, key: int) -> None:
        if self.settings_capture:
            if key == pygame.K_ESCAPE:
                self.settings_capture = False
                self.settings_message = "Key change canceled."
                return
            action = ACTION_ORDER[self.settings_index]
            try:
                self.keybindings.rebind(action, key)
            except OSError as exc:
                self.settings_message = f"Could not save key settings: {exc}"
            else:
                self.settings_message = f"Saved {self.keybindings.display_names(action)}."
            self.settings_capture = False
            return

        if key == pygame.K_ESCAPE:
            self._close_settings()
        elif key == pygame.K_UP:
            self.settings_index = (self.settings_index - 1) % len(ACTION_ORDER)
        elif key == pygame.K_DOWN:
            self.settings_index = (self.settings_index + 1) % len(ACTION_ORDER)
        elif key in (pygame.K_RETURN, pygame.K_SPACE):
            self.settings_capture = True
            self.settings_message = "Press the new key. Esc cancels."
        elif key == pygame.K_BACKSPACE:
            try:
                self.keybindings.reset_defaults()
            except OSError as exc:
                self.settings_message = f"Could not save key settings: {exc}"
            else:
                self.settings_message = "Restored and saved default keys."

    def handle_keydown(self, key: int) -> bool:
        if pygame is None:
            return True
        if self.settings_open:
            self._handle_settings_keydown(key)
            return True
        if self.keybindings.matches("open_settings", key):
            self._open_settings()
        elif self.keybindings.matches("quit", key):
            return False
        elif self.keybindings.matches("pause", key):
            self.paused = not self.paused
        elif self.keybindings.matches("reset", key):
            self.reset()
        elif self.keybindings.matches("speed_up", key):
            self.change_speed(1)
        elif self.keybindings.matches("speed_down", key):
            self.change_speed(-1)
        elif self.keybindings.matches("step", key):
            self.advance_one(include_human=True)
        elif self.human is not None and self.env.agents:
            info = self.infos[self.human.agent]
            if self.keybindings.matches("human_left", key):
                self.human.move(-1, info)
            elif self.keybindings.matches("human_right", key):
                self.human.move(1, info)
            elif self.keybindings.matches("rotate_left", key):
                self.human.rotate(-1, info)
            elif self.keybindings.matches("rotate_right", key):
                self.human.rotate(1, info)
            elif self.keybindings.matches("drop", key):
                self.advance_one(include_human=True)
        return True


def parse_config(argv=None) -> VersusUiConfig:
    parser = argparse.ArgumentParser(description="Watch or play a graphical Puyo versus match.")
    parser.add_argument("--policy-a", choices=POLICY_CHOICES, default="greedy")
    parser.add_argument("--policy-b", choices=POLICY_CHOICES, default="random")
    parser.add_argument("--checkpoint-a")
    parser.add_argument("--checkpoint-b")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument("--speed", type=float, choices=SPEED_CHOICES, default=1.0)
    parser.add_argument("--start-paused", action="store_true")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--stochastic", action="store_true")
    parser.add_argument("--beam-depth", type=int, default=5)
    parser.add_argument("--beam-width", type=int, default=32)
    parser.add_argument("--beam-scenarios", type=int, default=1)
    parser.add_argument(
        "--keybindings",
        dest="keybindings_path",
        help="Override the persistent keybindings JSON path.",
    )
    args = parser.parse_args(argv)
    config = VersusUiConfig(
        policy_a=args.policy_a,
        policy_b=args.policy_b,
        checkpoint_a=args.checkpoint_a,
        checkpoint_b=args.checkpoint_b,
        seed=args.seed,
        max_steps=args.max_steps,
        speed=args.speed,
        start_paused=args.start_paused,
        device=args.device,
        deterministic=not args.stochastic,
        beam_depth=args.beam_depth,
        beam_width=args.beam_width,
        beam_scenarios=args.beam_scenarios,
        keybindings_path=args.keybindings_path,
    )
    try:
        validate_config(config)
    except ValueError as exc:
        parser.error(str(exc))
    return config


def run_ui(config: VersusUiConfig, *, max_frames: int | None = None) -> dict:
    if pygame is None:
        raise ImportError("versus UI requires pygame; install requirements.txt")
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("Puyo AI Versus")
    clock = pygame.time.Clock()
    controller = VersusMatchController(config)
    renderer = VersusRenderer(screen)
    running = True
    frames = 0
    while running and (max_frames is None or frames < max_frames):
        delta_time = clock.tick(60) / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                running = controller.handle_keydown(event.key)
        controller.update(delta_time)
        renderer.draw(controller)
        frames += 1
    result = {
        "winner": controller.winner,
        "score_player_0": controller.infos["player_0"]["score"],
        "score_player_1": controller.infos["player_1"]["score"],
        "steps": controller.env.step_count,
    }
    pygame.quit()
    return result


def main(argv=None) -> None:
    result = run_ui(parse_config(argv))
    print(
        f"result: winner={result['winner']} score_player_0={result['score_player_0']} "
        f"score_player_1={result['score_player_1']} steps={result['steps']}"
    )


if __name__ == "__main__":
    main()
