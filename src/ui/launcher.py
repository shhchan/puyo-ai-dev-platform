"""Shared Pygame launcher for the main Puyo AI workflows."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

try:
    import pygame
except ImportError:  # pragma: no cover - dependency guard
    pygame = None

from eval.realtime_versus_ui import RealtimeVersusUiConfig
from eval.versus_ui import VersusUiConfig


SCREEN_WIDTH = 980
SCREEN_HEIGHT = 640
FPS = 60
BACKGROUND = (20, 24, 32)
PANEL = (33, 39, 52)
PANEL_ACTIVE = (48, 57, 74)
TEXT = (236, 240, 246)
MUTED = (154, 164, 181)
ACCENT = (74, 196, 158)
WARNING = (238, 181, 94)
ERROR = (238, 111, 111)


@dataclass(frozen=True)
class LauncherAction:
    key: str
    label: str
    screen: str
    command_label: str
    description: str


@dataclass(frozen=True)
class LauncherJob:
    action: LauncherAction
    command: tuple[str, ...]
    process: subprocess.Popen

    def status_label(self) -> str:
        code = self.process.poll()
        if code is None:
            return "running"
        return "complete" if code == 0 else f"failed ({code})"

    @property
    def is_running(self) -> bool:
        return self.process.poll() is None


class LauncherService:
    """Builds workflow commands and owns the one background launcher job."""

    def __init__(
        self,
        *,
        python_executable: str | None = None,
        repo_root: str | Path | None = None,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    ):
        self.python_executable = python_executable or sys.executable
        self.repo_root = Path(repo_root or Path.cwd())
        self.popen_factory = popen_factory
        self.actions = self._build_actions()
        self.current_job: LauncherJob | None = None
        self.message = "Ready."

    def _build_actions(self) -> dict[str, LauncherAction]:
        return {
            "play": LauncherAction(
                "play",
                "Play",
                "play",
                "Human vs greedy",
                "Start a placement-level match with one human side.",
            ),
            "spectate": LauncherAction(
                "spectate",
                "Spectate",
                "spectate",
                "Realtime AI match",
                "Watch two realtime policies from the existing versus UI.",
            ),
            "arena": LauncherAction(
                "arena",
                "Arena",
                "arena",
                "One paired realtime game",
                "Run a short fixed-seed evaluation job.",
            ),
            "training": LauncherAction(
                "training",
                "Training",
                "training",
                "Realtime smoke training",
                "Start the existing smoke training entry point.",
            ),
            "models": LauncherAction(
                "models",
                "Models",
                "models",
                "Lineage registry",
                "Build model and benchmark lineage artifacts.",
            ),
        }

    def navigation_actions(self) -> tuple[LauncherAction, ...]:
        return tuple(self.actions[key] for key in ("play", "spectate", "arena", "training", "models"))

    def action_for_screen(self, screen: str) -> LauncherAction | None:
        return self.actions.get(screen)

    def play_config(self) -> VersusUiConfig:
        return VersusUiConfig(policy_a="human", policy_b="greedy", seed=57, start_paused=True)

    def spectate_config(self) -> RealtimeVersusUiConfig:
        return RealtimeVersusUiConfig(
            policy_a="first",
            policy_b="random",
            seed=57,
            max_ticks=600,
            start_paused=True,
        )

    def command_for(self, action_key: str) -> tuple[str, ...]:
        if action_key == "play":
            return (
                self.python_executable,
                "-m",
                "eval.versus_ui",
                *versus_config_to_argv(self.play_config()),
            )
        if action_key == "spectate":
            return (
                self.python_executable,
                "-m",
                "eval.realtime_versus_ui",
                *realtime_config_to_argv(self.spectate_config()),
            )
        if action_key == "arena":
            return (
                self.python_executable,
                "-m",
                "eval.realtime_arena",
                "--policy-a",
                "first",
                "--policy-b",
                "random",
                "--games",
                "1",
                "--seed",
                "57",
                "--max-ticks",
                "180",
                "--paired-sides",
            )
        if action_key == "training":
            return (
                self.python_executable,
                "-m",
                "train.train_realtime",
                "--config",
                "train/config/realtime_smoke.yaml",
                "--set",
                "run_id=launcher-smoke",
            )
        if action_key == "models":
            return (
                self.python_executable,
                "-m",
                "train.lineage",
                "--root",
                "runs",
                "--root",
                "docs/benchmarks",
                "--output",
                "/tmp/puyo-launcher-lineage.json",
                "--markdown",
                "/tmp/puyo-launcher-lineage.md",
            )
        raise KeyError(f"unknown launcher action: {action_key}")

    def command_label(self, action_key: str) -> str:
        return " ".join(self.command_for(action_key))

    def start(self, action_key: str) -> bool:
        action = self.actions[action_key]
        if self.current_job and self.current_job.is_running:
            self.message = f"Stop {self.current_job.action.label} before starting {action.label}."
            return False
        command = self.command_for(action_key)
        try:
            process = self.popen_factory(command, cwd=str(self.repo_root))
        except OSError as exc:
            self.message = f"Could not start {action.label}: {exc}. Check dependencies and paths."
            return False
        self.current_job = LauncherJob(action=action, command=command, process=process)
        self.message = f"Started {action.label}."
        return True

    def stop(self) -> bool:
        if self.current_job is None or not self.current_job.is_running:
            self.message = "No running job."
            return False
        self.current_job.process.terminate()
        self.message = f"Stopping {self.current_job.action.label}."
        return True

    def refresh_status(self) -> str:
        if self.current_job is None:
            return "No job"
        status = self.current_job.status_label()
        if status.startswith("failed"):
            self.message = f"{self.current_job.action.label} {status}. Check terminal output and command paths."
        elif status == "complete":
            self.message = f"{self.current_job.action.label} complete."
        return f"{self.current_job.action.label}: {status}"


class LauncherController:
    def __init__(self, service: LauncherService | None = None):
        self.service = service or LauncherService()
        self.screen = "home"
        self.selection = 0

    @property
    def current_options(self) -> tuple[str, ...]:
        if self.screen == "home":
            return tuple(action.screen for action in self.service.navigation_actions())
        return ("run", "stop", "back")

    def _move(self, delta: int) -> None:
        options = self.current_options
        self.selection = (self.selection + delta) % len(options)

    def _activate(self) -> None:
        selected = self.current_options[self.selection]
        if self.screen == "home":
            self.screen = selected
            self.selection = 0
        elif selected == "run":
            self.service.start(self.screen)
        elif selected == "stop":
            self.service.stop()
        elif selected == "back":
            self.screen = "home"
            self.selection = 0

    def handle_keydown(self, key: int) -> bool:
        if pygame is None:
            return True
        if key in (pygame.K_ESCAPE, pygame.K_q):
            if self.screen == "home":
                return False
            self.screen = "home"
            self.selection = 0
        elif key in (pygame.K_UP, pygame.K_LEFT):
            self._move(-1)
        elif key in (pygame.K_DOWN, pygame.K_RIGHT, pygame.K_TAB):
            self._move(1)
        elif key in (pygame.K_RETURN, pygame.K_SPACE):
            self._activate()
        return True


class LauncherRenderer:
    def __init__(self, screen):
        self.screen = screen
        self.title_font = pygame.font.SysFont("Arial", 34, bold=True)
        self.heading_font = pygame.font.SysFont("Arial", 24, bold=True)
        self.font = pygame.font.SysFont("Arial", 18)
        self.small_font = pygame.font.SysFont("Consolas", 15)

    def _draw_text(self, text: str, font, color, rect: pygame.Rect, *, center=False) -> None:
        surface = font.render(text, True, color)
        target = surface.get_rect()
        if center:
            target.center = rect.center
        else:
            target.topleft = rect.topleft
        self.screen.blit(surface, target)

    def _wrapped_lines(self, text: str, font, width: int) -> list[str]:
        lines = []
        for raw_line in text.splitlines() or [""]:
            words = raw_line.split(" ")
            line = ""
            for word in words:
                candidate = word if not line else f"{line} {word}"
                if font.size(candidate)[0] <= width:
                    line = candidate
                else:
                    if line:
                        lines.append(line)
                    line = word
            lines.append(line)
        return lines

    def _draw_wrapped(self, text: str, font, color, x: int, y: int, width: int) -> int:
        for line in self._wrapped_lines(text, font, width):
            surface = font.render(line, True, color)
            self.screen.blit(surface, (x, y))
            y += surface.get_height() + 4
        return y

    def draw(self, controller: LauncherController) -> None:
        self.screen.fill(BACKGROUND)
        controller.service.refresh_status()
        header = pygame.Rect(32, 28, SCREEN_WIDTH - 64, 58)
        self._draw_text("Puyo AI Dev Platform", self.title_font, TEXT, header)
        if controller.screen == "home":
            self._draw_home(controller)
        else:
            self._draw_workflow(controller)

        status_rect = pygame.Rect(32, SCREEN_HEIGHT - 78, SCREEN_WIDTH - 64, 24)
        message_rect = pygame.Rect(32, SCREEN_HEIGHT - 48, SCREEN_WIDTH - 64, 24)
        self._draw_text(controller.service.refresh_status(), self.font, ACCENT, status_rect)
        self._draw_text(controller.service.message, self.font, MUTED, message_rect)
        pygame.display.flip()

    def _draw_home(self, controller: LauncherController) -> None:
        actions = controller.service.navigation_actions()
        top = 118
        for index, action in enumerate(actions):
            rect = pygame.Rect(52, top + index * 84, SCREEN_WIDTH - 104, 66)
            color = PANEL_ACTIVE if index == controller.selection else PANEL
            pygame.draw.rect(self.screen, color, rect, border_radius=6)
            pygame.draw.rect(self.screen, ACCENT if index == controller.selection else (82, 92, 112), rect, 2)
            self._draw_text(action.label, self.heading_font, TEXT, pygame.Rect(rect.x + 18, rect.y + 11, 170, 26))
            self._draw_text(
                action.command_label,
                self.font,
                WARNING,
                pygame.Rect(rect.x + 190, rect.y + 13, 260, 24),
            )
            self._draw_text(
                action.description,
                self.font,
                MUTED,
                pygame.Rect(rect.x + 190, rect.y + 39, rect.width - 210, 20),
            )

    def _draw_workflow(self, controller: LauncherController) -> None:
        action = controller.service.action_for_screen(controller.screen)
        if action is None:
            return
        content = pygame.Rect(52, 118, SCREEN_WIDTH - 104, 360)
        pygame.draw.rect(self.screen, PANEL, content, border_radius=6)
        pygame.draw.rect(self.screen, (82, 92, 112), content, 2)
        self._draw_text(action.label, self.heading_font, TEXT, pygame.Rect(content.x + 18, content.y + 18, 300, 30))
        y = self._draw_wrapped(action.description, self.font, MUTED, content.x + 18, content.y + 62, content.width - 36)
        y += 16
        self._draw_text("CLI equivalent", self.font, WARNING, pygame.Rect(content.x + 18, y, 260, 22))
        y += 28
        y = self._draw_wrapped(
            controller.service.command_label(action.key),
            self.small_font,
            TEXT,
            content.x + 18,
            y,
            content.width - 36,
        )
        y += 18
        recovery = "If startup fails, check the terminal output, installed requirements, and file paths."
        self._draw_wrapped(recovery, self.font, ERROR, content.x + 18, y, content.width - 36)

        options = controller.current_options
        for index, option in enumerate(options):
            rect = pygame.Rect(52 + index * 168, 506, 146, 48)
            selected = index == controller.selection
            pygame.draw.rect(self.screen, PANEL_ACTIVE if selected else PANEL, rect, border_radius=6)
            pygame.draw.rect(self.screen, ACCENT if selected else (82, 92, 112), rect, 2)
            self._draw_text(option.upper(), self.font, TEXT, rect, center=True)


def versus_config_to_argv(config: VersusUiConfig) -> tuple[str, ...]:
    args = [
        "--policy-a",
        config.policy_a,
        "--policy-b",
        config.policy_b,
        "--seed",
        str(config.seed),
        "--max-steps",
        str(config.max_steps),
        "--speed",
        str(config.speed),
        "--device",
        config.device,
        "--beam-depth",
        str(config.beam_depth),
        "--beam-width",
        str(config.beam_width),
        "--beam-scenarios",
        str(config.beam_scenarios),
        "--beam-minimum-chain",
        str(config.beam_minimum_chain),
    ]
    if config.checkpoint_a:
        args.extend(["--checkpoint-a", config.checkpoint_a])
    if config.checkpoint_b:
        args.extend(["--checkpoint-b", config.checkpoint_b])
    if config.seed_a is not None:
        args.extend(["--seed-a", str(config.seed_a)])
    if config.seed_b is not None:
        args.extend(["--seed-b", str(config.seed_b)])
    if config.start_paused:
        args.append("--start-paused")
    if not config.deterministic:
        args.append("--stochastic")
    args.extend(_side_specific_argv(config, ("device", "deterministic", "beam_depth", "beam_width", "beam_scenarios", "beam_minimum_chain")))
    if config.keybindings_path:
        args.extend(["--keybindings", config.keybindings_path])
    return tuple(args)


def realtime_config_to_argv(config: RealtimeVersusUiConfig) -> tuple[str, ...]:
    args = [
        "--policy-a",
        config.policy_a,
        "--policy-b",
        config.policy_b,
        "--seed",
        str(config.seed),
        "--max-ticks",
        str(config.max_ticks),
        "--speed",
        str(config.speed),
        "--device",
        config.device,
        "--beam-depth",
        str(config.beam_depth),
        "--beam-width",
        str(config.beam_width),
        "--beam-scenarios",
        str(config.beam_scenarios),
        "--beam-minimum-chain",
        str(config.beam_minimum_chain),
        "--inference-latency-ticks",
        str(config.inference_latency_ticks),
    ]
    if config.checkpoint_a:
        args.extend(["--checkpoint-a", config.checkpoint_a])
    if config.checkpoint_b:
        args.extend(["--checkpoint-b", config.checkpoint_b])
    if config.seed_a is not None:
        args.extend(["--seed-a", str(config.seed_a)])
    if config.seed_b is not None:
        args.extend(["--seed-b", str(config.seed_b)])
    if config.start_paused:
        args.append("--start-paused")
    if not config.deterministic:
        args.append("--stochastic")
    if config.timeout_ticks is not None:
        args.extend(["--timeout-ticks", str(config.timeout_ticks)])
    if config.action_deadline_ticks is not None:
        args.extend(["--action-deadline-ticks", str(config.action_deadline_ticks)])
    if config.use_reachable_action_mask:
        args.append("--use-reachable-action-mask")
    args.extend(_side_specific_argv(config, ("device", "deterministic", "beam_depth", "beam_width", "beam_scenarios", "beam_minimum_chain")))
    if config.keybindings_path:
        args.extend(["--keybindings", config.keybindings_path])
    return tuple(args)


def _side_specific_argv(config, names: Iterable[str]) -> list[str]:
    args: list[str] = []
    for side in ("a", "b"):
        for name in names:
            value = getattr(config, f"{name}_{side}")
            if value is None:
                continue
            option = "--" + name.replace("_", "-") + f"-{side}"
            if isinstance(value, bool):
                args.append(option if value else f"--stochastic-{side}")
            else:
                args.extend([option, str(value)])
    return args


def run_launcher(*, service: LauncherService | None = None, max_frames: int | None = None) -> dict[str, str]:
    if pygame is None:
        raise ImportError("launcher requires pygame; install requirements.txt")
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("Puyo AI Dev Platform")
    clock = pygame.time.Clock()
    controller = LauncherController(service)
    renderer = LauncherRenderer(screen)
    running = True
    frames = 0
    while running and (max_frames is None or frames < max_frames):
        clock.tick(FPS)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                running = controller.handle_keydown(event.key)
        renderer.draw(controller)
        frames += 1
    result = {
        "screen": controller.screen,
        "job": controller.service.refresh_status(),
        "message": controller.service.message,
    }
    pygame.quit()
    return result
