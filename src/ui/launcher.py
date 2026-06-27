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
from src.ui.launcher_settings import LauncherPresetStore, LauncherSettingsManager


SCREEN_WIDTH = 980
SCREEN_HEIGHT = 640
FPS = 60
UI_ASSET_FONT = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "MPLUS1-Regular.ttf"
BACKGROUND = (20, 24, 32)
PANEL = (33, 39, 52)
PANEL_ACTIVE = (48, 57, 74)
TEXT = (236, 240, 246)
MUTED = (154, 164, 181)
ACCENT = (74, 196, 158)
WARNING = (238, 181, 94)
ERROR = (238, 111, 111)
SETTINGS_ROWS_PER_PAGE = 12


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
            return "実行中"
        return "完了" if code == 0 else f"失敗 ({code})"

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
        preset_store_path: str | Path | None = None,
    ):
        self.python_executable = python_executable or sys.executable
        self.repo_root = Path(repo_root or Path.cwd())
        self.popen_factory = popen_factory
        self.settings = LauncherSettingsManager(
            repo_root=self.repo_root,
            store=LauncherPresetStore(preset_store_path),
        )
        self.actions = self._build_actions()
        self.current_job: LauncherJob | None = None
        self.message = "準備完了。"

    def _build_actions(self) -> dict[str, LauncherAction]:
        return {
            "play": LauncherAction(
                "play",
                "対戦",
                "play",
                "Human vs greedy",
                "人間と AI の対戦を開始します。現状は既存 versus UI 経路です。",
            ),
            "spectate": LauncherAction(
                "spectate",
                "観戦",
                "spectate",
                "Realtime AI 対戦",
                "2 つの realtime policy の対戦を観戦します。",
            ),
            "arena": LauncherAction(
                "arena",
                "評価",
                "arena",
                "Realtime arena",
                "固定 seed の realtime arena 評価を実行します。",
            ),
            "training": LauncherAction(
                "training",
                "学習",
                "training",
                "Realtime smoke 学習",
                "既存の smoke training entry point を起動します。",
            ),
            "models": LauncherAction(
                "models",
                "モデル",
                "models",
                "Model viewer",
                "replay diagnostics と lineage を確認する viewer を起動します。",
            ),
        }

    def navigation_actions(self) -> tuple[LauncherAction, ...]:
        return tuple(self.actions[key] for key in ("play", "spectate", "arena", "training", "models"))

    def action_for_screen(self, screen: str) -> LauncherAction | None:
        return self.actions.get(screen)

    def play_config(self) -> VersusUiConfig:
        settings = self.settings.for_action("play")
        return VersusUiConfig(
            policy_a=settings.policy_a,
            policy_b=settings.policy_b,
            checkpoint_a=settings.checkpoint_a,
            checkpoint_b=settings.checkpoint_b,
            seed=settings.seed,
            seed_a=settings.seed_a,
            seed_b=settings.seed_b,
            max_steps=settings.max_steps,
            speed=settings.speed,
            start_paused=settings.start_paused,
            device=settings.device,
            deterministic=settings.deterministic,
            beam_depth=settings.beam_depth,
            beam_width=settings.beam_width,
            beam_scenarios=settings.beam_scenarios,
            beam_minimum_chain=settings.beam_minimum_chain,
            beam_depth_a=settings.beam_depth_a,
            beam_depth_b=settings.beam_depth_b,
            beam_width_a=settings.beam_width_a,
            beam_width_b=settings.beam_width_b,
            beam_scenarios_a=settings.beam_scenarios_a,
            beam_scenarios_b=settings.beam_scenarios_b,
            beam_minimum_chain_a=settings.beam_minimum_chain_a,
            beam_minimum_chain_b=settings.beam_minimum_chain_b,
            device_a=settings.device_a,
            device_b=settings.device_b,
            deterministic_a=settings.deterministic_a,
            deterministic_b=settings.deterministic_b,
            keybindings_path=settings.keybindings_path,
        )

    def spectate_config(self) -> RealtimeVersusUiConfig:
        settings = self.settings.for_action("spectate")
        return RealtimeVersusUiConfig(
            policy_a=settings.policy_a,
            policy_b=settings.policy_b,
            checkpoint_a=settings.checkpoint_a,
            checkpoint_b=settings.checkpoint_b,
            seed=settings.seed,
            seed_a=settings.seed_a,
            seed_b=settings.seed_b,
            max_ticks=settings.max_ticks,
            speed=settings.speed,
            start_paused=settings.start_paused,
            device=settings.device,
            deterministic=settings.deterministic,
            beam_depth=settings.beam_depth,
            beam_width=settings.beam_width,
            beam_scenarios=settings.beam_scenarios,
            beam_minimum_chain=settings.beam_minimum_chain,
            beam_depth_a=settings.beam_depth_a,
            beam_depth_b=settings.beam_depth_b,
            beam_width_a=settings.beam_width_a,
            beam_width_b=settings.beam_width_b,
            beam_scenarios_a=settings.beam_scenarios_a,
            beam_scenarios_b=settings.beam_scenarios_b,
            beam_minimum_chain_a=settings.beam_minimum_chain_a,
            beam_minimum_chain_b=settings.beam_minimum_chain_b,
            device_a=settings.device_a,
            device_b=settings.device_b,
            deterministic_a=settings.deterministic_a,
            deterministic_b=settings.deterministic_b,
            inference_latency_ticks=settings.inference_latency_ticks,
            timeout_ticks=settings.timeout_ticks,
            action_deadline_ticks=settings.action_deadline_ticks,
            use_reachable_action_mask=settings.use_reachable_action_mask,
            keybindings_path=settings.keybindings_path,
            result_json=settings.result_json,
            max_frames=settings.max_frames,
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
            settings = self.settings.for_action("arena")
            args = [
                self.python_executable,
                "-m",
                "eval.realtime_arena",
                "--policy-a",
                settings.policy_a,
                "--policy-b",
                settings.policy_b,
                "--games",
                str(settings.games),
                "--seed",
                str(settings.seed),
                "--max-ticks",
                str(settings.max_ticks),
                "--device",
                settings.device,
                "--beam-depth",
                str(settings.beam_depth),
                "--beam-width",
                str(settings.beam_width),
                "--beam-scenarios",
                str(settings.beam_scenarios),
                "--beam-minimum-chain",
                str(settings.beam_minimum_chain),
                *checkpoint_argv(settings),
                "--inference-latency-ticks",
                str(settings.inference_latency_ticks),
            ]
            if settings.timeout_ticks is not None:
                args.extend(["--timeout-ticks", str(settings.timeout_ticks)])
            if settings.action_deadline_ticks is not None:
                args.extend(["--action-deadline-ticks", str(settings.action_deadline_ticks)])
            if settings.paired_sides:
                args.append("--paired-sides")
            if settings.replay_path:
                args.extend(["--replay", settings.replay_path])
            return tuple(args)
        if action_key == "training":
            settings = self.settings.for_action("training")
            return (
                self.python_executable,
                "-m",
                "train.train_realtime",
                "--config",
                settings.config_path,
                "--set",
                f"run_id={settings.run_id}",
                "--set",
                f"seed={settings.seed}",
            )
        if action_key == "models":
            return (
                self.python_executable,
                "-m",
                "eval.model_viewer",
                "--lineage-root",
                "runs",
                "--lineage-root",
                "docs/benchmarks",
                "--report-json",
                "/tmp/puyo-model-viewer-report.json",
                "--report-markdown",
                "/tmp/puyo-model-viewer-report.md",
            )
        raise KeyError(f"unknown launcher action: {action_key}")

    def command_label(self, action_key: str) -> str:
        return " ".join(self.command_for(action_key))

    def update_setting(self, action_key: str, field: str, value) -> None:
        self.settings.update(action_key, field, value)
        self.message = f"{field} を更新しました。"

    def cycle_setting(self, action_key: str, field: str, delta: int = 1) -> None:
        self.settings.cycle(action_key, field, delta)
        self.message = f"{field} を更新しました。"

    def setting_rows(self, action_key: str) -> tuple[str, ...]:
        return tuple(
            self.settings.field_label(action_key, field)
            for field in self.settings.editable_fields(action_key)
        )

    def save_preset(self, action_key: str) -> str:
        name = self.settings.save_preset(action_key)
        self.message = f"preset {name} を保存しました。"
        return name

    def load_next_preset(self, action_key: str) -> str | None:
        name = self.settings.load_next_preset(action_key)
        if name is None:
            self.message = "この workflow の preset はまだありません。"
            return None
        self.message = f"preset {name} を読み込みました。"
        return name

    def validate_action(self, action_key: str) -> list[str]:
        return self.settings.validate(action_key)

    def start(self, action_key: str) -> bool:
        action = self.actions[action_key]
        if self.current_job and self.current_job.is_running:
            self.message = f"{action.label} を始める前に {self.current_job.action.label} を停止してください。"
            return False
        errors = self.validate_action(action_key)
        if errors:
            self.message = f"{action.label} を開始できません: {errors[0]}"
            return False
        command = self.command_for(action_key)
        try:
            process = self.popen_factory(command, cwd=str(self.repo_root))
        except OSError as exc:
            self.message = f"{action.label} を開始できません: {exc}。依存関係と path を確認してください。"
            return False
        self.current_job = LauncherJob(action=action, command=command, process=process)
        self.settings.save_recent(action_key)
        self.message = f"{action.label} を開始しました。"
        return True

    def stop(self) -> bool:
        if self.current_job is None or not self.current_job.is_running:
            self.message = "実行中の job はありません。"
            return False
        self.current_job.process.terminate()
        self.message = f"{self.current_job.action.label} を停止しています。"
        return True

    def refresh_status(self) -> str:
        if self.current_job is None:
            return "job なし"
        status = self.current_job.status_label()
        if status.startswith("失敗"):
            self.message = f"{self.current_job.action.label} は失敗しました ({status})。terminal 出力と path を確認してください。"
        elif status == "完了":
            self.message = f"{self.current_job.action.label} は完了しました。"
        return f"{self.current_job.action.label}: {status}"


class LauncherController:
    def __init__(self, service: LauncherService | None = None):
        self.service = service or LauncherService()
        self.screen = "home"
        self.selection = 0
        self.settings_mode = False
        self.settings_page = 0

    @property
    def current_options(self) -> tuple[str, ...]:
        if self.screen == "home":
            return tuple(action.screen for action in self.service.navigation_actions())
        if self.settings_mode:
            return (*self.visible_setting_fields(), "prev_page", "next_page", "back")
        if self.service.settings.editable_fields(self.screen):
            return ("settings", "run", "preset", "save", "stop", "back")
        return ("run", "stop", "back")

    def visible_setting_fields(self) -> tuple[str, ...]:
        fields = self.service.settings.editable_fields(self.screen)
        start = self.settings_page * SETTINGS_ROWS_PER_PAGE
        return fields[start : start + SETTINGS_ROWS_PER_PAGE]

    def settings_page_count(self) -> int:
        fields = self.service.settings.editable_fields(self.screen)
        if not fields:
            return 1
        return max(1, (len(fields) + SETTINGS_ROWS_PER_PAGE - 1) // SETTINGS_ROWS_PER_PAGE)

    def _set_settings_page(self, page: int) -> None:
        self.settings_page = page % self.settings_page_count()
        self.selection = 0

    def _move(self, delta: int) -> None:
        options = self.current_options
        self.selection = (self.selection + delta) % len(options)

    def _activate(self) -> None:
        selected = self.current_options[self.selection]
        if self.screen == "home":
            self.screen = selected
            self.selection = 0
            self.settings_page = 0
        elif self.settings_mode:
            if selected == "back":
                self.settings_mode = False
                self.selection = 0
            elif selected == "prev_page":
                self._set_settings_page(self.settings_page - 1)
            elif selected == "next_page":
                self._set_settings_page(self.settings_page + 1)
            else:
                self.service.cycle_setting(self.screen, selected, 1)
        elif selected == "run":
            self.service.start(self.screen)
        elif selected == "settings":
            self.settings_mode = True
            self.selection = 0
            self.settings_page = 0
        elif selected == "preset":
            self.service.load_next_preset(self.screen)
        elif selected == "save":
            self.service.save_preset(self.screen)
        elif selected == "stop":
            self.service.stop()
        elif selected == "back":
            self.screen = "home"
            self.selection = 0
            self.settings_page = 0

    def handle_keydown(self, key: int) -> bool:
        if pygame is None:
            return True
        if key in (pygame.K_ESCAPE, pygame.K_q):
            if self.settings_mode:
                self.settings_mode = False
                self.selection = 0
                return True
            elif self.screen == "home":
                return False
            self.screen = "home"
            self.selection = 0
        elif key == pygame.K_LEFT and self.settings_mode:
            selected = self.current_options[self.selection]
            if selected != "back":
                self.service.cycle_setting(self.screen, selected, -1)
        elif key == pygame.K_RIGHT and self.settings_mode:
            selected = self.current_options[self.selection]
            if selected != "back":
                self.service.cycle_setting(self.screen, selected, 1)
        elif key in (pygame.K_UP, pygame.K_LEFT):
            self._move(-1)
        elif key in (pygame.K_DOWN, pygame.K_RIGHT, pygame.K_TAB):
            self._move(1)
        elif key in (pygame.K_RETURN, pygame.K_SPACE):
            self._activate()
        return True

    def handle_mouse_down(self, pos: tuple[int, int], button: int) -> bool:
        if button in (4, 5):
            self._move(-1 if button == 4 else 1)
            return True
        options = self.current_options
        for index, option in enumerate(options):
            if self._option_rect(index, len(options)).collidepoint(pos):
                self.selection = index
                if button == 1:
                    self._activate()
                elif button == 3 and self.settings_mode and option not in {"back", "prev_page", "next_page"}:
                    self.service.cycle_setting(self.screen, option, -1)
                return True
        return True

    def _option_rect(self, index: int, option_count: int) -> "pygame.Rect":
        if pygame is None:
            raise RuntimeError("pygame is required for launcher layout")
        if self.screen == "home":
            return pygame.Rect(52, 118 + index * 84, SCREEN_WIDTH - 104, 66)
        if self.settings_mode:
            if index < len(self.visible_setting_fields()):
                column = index // 6
                row = index % 6
                column_width = 420
                return pygame.Rect(70 + column * 440, 214 + row * 42, column_width, 34)
            action_index = index - len(self.visible_setting_fields())
            return pygame.Rect(70 + action_index * 132, 492, 120, 42)
        gap = 12
        width = min(146, (SCREEN_WIDTH - 104 - gap * (option_count - 1)) // option_count)
        return pygame.Rect(52 + index * (width + gap), 506, width, 48)


class LauncherRenderer:
    def __init__(self, screen):
        self.screen = screen
        self.title_font = _font(34, bold=True)
        self.heading_font = _font(24, bold=True)
        self.font = _font(18)
        self.small_font = _font(15, monospace=True)

    def _draw_text(self, text: str, font, color, rect: pygame.Rect, *, center=False) -> None:
        surface = font.render(self._fit_text(text, font, rect.width), True, color)
        target = surface.get_rect()
        if center:
            target.center = rect.center
        else:
            target.topleft = rect.topleft
        self.screen.blit(surface, target)

    def _fit_text(self, text: str, font, width: int) -> str:
        if font.size(text)[0] <= width:
            return text
        ellipsis = "..."
        available = max(0, width - font.size(ellipsis)[0])
        trimmed = ""
        for character in text:
            candidate = trimmed + character
            if font.size(candidate)[0] > available:
                break
            trimmed = candidate
        return trimmed.rstrip() + ellipsis

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
                    if font.size(word)[0] <= width:
                        line = word
                    else:
                        chunk = ""
                        for character in word:
                            candidate_chunk = chunk + character
                            if font.size(candidate_chunk)[0] <= width:
                                chunk = candidate_chunk
                            else:
                                if chunk:
                                    lines.append(chunk)
                                chunk = character
                        line = chunk
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
        self._draw_text("ぷよ AI 開発プラットフォーム", self.title_font, TEXT, header)
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
        for index, action in enumerate(actions):
            rect = controller._option_rect(index, len(actions))
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
        if controller.settings_mode:
            self._draw_settings(controller, action)
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
        recovery = "起動に失敗した場合は terminal 出力、依存関係、path を確認してください。"
        self._draw_wrapped(recovery, self.font, ERROR, content.x + 18, y, content.width - 36)

        options = controller.current_options
        for index, option in enumerate(options):
            rect = controller._option_rect(index, len(options))
            selected = index == controller.selection
            pygame.draw.rect(self.screen, PANEL_ACTIVE if selected else PANEL, rect, border_radius=6)
            pygame.draw.rect(self.screen, ACCENT if selected else (82, 92, 112), rect, 2)
            self._draw_text(_option_label(option), self.font, TEXT, rect, center=True)

    def _draw_settings(self, controller: LauncherController, action: LauncherAction) -> None:
        content = pygame.Rect(52, 104, SCREEN_WIDTH - 104, 430)
        pygame.draw.rect(self.screen, PANEL, content, border_radius=6)
        pygame.draw.rect(self.screen, (82, 92, 112), content, 2)
        self._draw_text(
            f"{action.label} 設定",
            self.heading_font,
            TEXT,
            pygame.Rect(content.x + 18, content.y + 18, 360, 30),
        )
        errors = controller.service.validate_action(action.key)
        page = f"{controller.settings_page + 1}/{controller.settings_page_count()}"
        status = errors[0] if errors else f"設定は有効です。ページ {page}"
        self._draw_wrapped(
            status,
            self.font,
            ERROR if errors else ACCENT,
            content.x + 18,
            content.y + 54,
            content.width - 36,
        )

        options = controller.current_options
        selected_option = options[controller.selection] if options else "back"
        for index, option in enumerate(options):
            rect = controller._option_rect(index, len(options))
            selected = index == controller.selection
            pygame.draw.rect(self.screen, PANEL_ACTIVE if selected else BACKGROUND, rect, border_radius=5)
            pygame.draw.rect(self.screen, ACCENT if selected else (82, 92, 112), rect, 1)
            if option in {"back", "prev_page", "next_page"}:
                label = _option_label(option)
            else:
                label = controller.service.settings.field_label(action.key, option)
            self._draw_text(label, self.small_font, TEXT, pygame.Rect(rect.x + 8, rect.y + 7, rect.width - 16, 18))

        help_rect = pygame.Rect(510, 476, 410, 44)
        if selected_option in {"back", "prev_page", "next_page"}:
            help_text = "左クリックで実行します。設定項目は左クリックで次の値、右クリックで前の値に変更できます。"
        else:
            help_text = controller.service.settings.field_help(action.key, selected_option)
        self._draw_wrapped(help_text, self.small_font, MUTED, help_rect.x, help_rect.y, help_rect.width)


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
    if config.result_json:
        args.extend(["--result-json", config.result_json])
    if config.max_frames is not None:
        args.extend(["--max-frames", str(config.max_frames)])
    if config.keybindings_path:
        args.extend(["--keybindings", config.keybindings_path])
    return tuple(args)


def checkpoint_argv(settings) -> tuple[str, ...]:
    args: list[str] = []
    if settings.checkpoint_a:
        args.extend(["--checkpoint-a", settings.checkpoint_a])
    if settings.checkpoint_b:
        args.extend(["--checkpoint-b", settings.checkpoint_b])
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


def _font(size: int, *, bold: bool = False, monospace: bool = False):
    if not pygame.font.get_init():
        pygame.font.init()
    if UI_ASSET_FONT.exists():
        font = pygame.font.Font(str(UI_ASSET_FONT), size)
        font.set_bold(bold)
        return font
    candidates = (
        ["Noto Sans Mono CJK JP", "Noto Sans Mono", "DejaVu Sans Mono"]
        if monospace
        else ["Noto Sans CJK JP", "Noto Sans JP", "TakaoGothic", "IPAGothic", "DejaVu Sans"]
    )
    for name in candidates:
        if pygame.font.match_font(name):
            return pygame.font.SysFont(name, size, bold=bold)
    return pygame.font.SysFont(None, size, bold=bold)


def _option_label(option: str) -> str:
    return {
        "settings": "設定",
        "run": "開始",
        "preset": "読込",
        "save": "保存",
        "stop": "停止",
        "back": "戻る",
        "prev_page": "前へ",
        "next_page": "次へ",
    }.get(option, option)


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
            elif event.type == pygame.MOUSEBUTTONDOWN:
                running = controller.handle_mouse_down(event.pos, event.button)
        renderer.draw(controller)
        frames += 1
    result = {
        "screen": controller.screen,
        "job": controller.service.refresh_status(),
        "message": controller.service.message,
    }
    pygame.quit()
    return result
