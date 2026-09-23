"""Reproduce PUYO-239 in a normal window and replay its fixed boundary fixture.

This is a boundary fixture, not a seed-only match replay. Use --replay to restore
its initial board before checking every recorded hash and attack diagnostic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from puyo_env.realtime_versus import RealtimeVersusMatch
from src.core.constants import PuyoColor
from src.core.puyo import Puyo
from src.core.realtime import RealtimeHeadlessSimulator


def prepare_boundary(match, *, survivor="player_0", chains=2, detected=True):
    """Set one live resolution opposite a top-out; no unspecified random inputs."""
    state = match.player_states[survivor]
    game = state.simulator.game
    red = ((0, 0), (0, 1), (0, 2), (1, 0))
    blue = ((1, 1), (2, 0), (3, 0), (4, 0)) if chains == 2 else ()
    for color, coords in ((PuyoColor.RED, red), (PuyoColor.BLUE, blue)):
        for x, y in coords:
            game.field.place_puyo(x, y, Puyo(color))
    game.current_puyo_1 = game.current_puyo_2 = None
    game.state = "animate"
    game._begin_chain_resolution()
    state.simulator = RealtimeHeadlessSimulator(game_state=game, timing=match.timing)
    state.score_carry = 69
    loser = "player_1" if survivor == "player_0" else "player_0"
    state = match.player_states[loser]
    game = state.simulator.game
    for y in range(12):
        game.field.place_puyo(2, y, Puyo(PuyoColor.RED if y % 2 else PuyoColor.BLUE))
    game.current_puyo_1 = game.current_puyo_2 = None
    game.game_over = detected
    game.state = "gameover" if detected else "animate"
    if not detected:
        game._begin_chain_resolution()
    state.simulator = RealtimeHeadlessSimulator(game_state=game, timing=match.timing)
    return loser


def replay_boundary(payload):
    from eval.realtime_arena import replay_realtime_match

    def make_match(**kwargs):
        match = RealtimeVersusMatch(**kwargs)
        prepare_boundary(match, **payload["fixture"])
        return match

    with patch("puyo_env.realtime_versus.RealtimeVersusMatch", side_effect=make_match):
        return replay_realtime_match(payload["replay"])


def capture(
    output, *, survivor="player_0", chains=2, frames_after=45, source_revision=None
):
    import pygame
    from eval.realtime_versus_ui import (
        RealtimeVersusMatchController,
        RealtimeVersusUiConfig,
    )
    from src.ui.versus_renderer import SCREEN_HEIGHT, SCREEN_WIDTH, VersusRenderer

    output.mkdir(parents=True, exist_ok=True)
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("PUYO-239 terminal chain boundary QA")
    if pygame.display.get_driver() == "dummy":
        raise RuntimeError("Normal window required: unset SDL_VIDEODRIVER=dummy")
    controller = RealtimeVersusMatchController(
        RealtimeVersusUiConfig(
            policy_a="first",
            policy_b="first",
            seed=239,
            max_ticks=None,
            replay_path=str(output / "boundary-replay.json"),
        )
    )
    fixture = dict(survivor=survivor, chains=chains, detected=True)
    prepare_boundary(controller.env.match, **fixture)
    controller.observations, controller.infos = controller.env._observations_and_infos()
    controller.initial_all_clear_diagnostics = (
        controller.env.match.all_clear_diagnostics()
    )
    controller._sync_display_boards()
    renderer = VersusRenderer(screen)
    clock = pygame.time.Clock()
    timeline = []
    finish_frames = 0
    saved = set()
    try:
        for frame in range(600):
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    raise RuntimeError("QA capture interrupted")
            if controller.env.agents:
                controller.advance_one()
                row = {
                    "tick": controller.env.match.tick,
                    "agents": list(controller.env.agents),
                    "winner": controller.winner,
                    "players": {
                        agent: {
                            "phase": state.simulator.game.state,
                            "score": state.simulator.game.score,
                            "chain": state.simulator.game.chain_count,
                            "pending": state.pending_ojama,
                            "generated": state.generated_ojama_total,
                            "received": state.received_ojama_total,
                            "active_pair": list(state.simulator.snapshot().active_pair),
                        }
                        for agent, state in controller.env.player_states.items()
                    },
                }
                timeline.append(row)
            else:
                # Exercise real update for draining terminal visual events.
                controller.update(controller.env.match.timing.tick_seconds)
                finish_frames += 1
            renderer.draw(controller)
            stage = None
            if frame == 0:
                stage = "first-tick"
            elif (
                controller.env.agents
                and controller.env.player_states[survivor].simulator.game.chain_count
                == 1
            ):
                stage = "chain-continues"
            elif not controller.env.agents and finish_frames == 0:
                stage = "authoritative-final"
            if stage and stage not in saved:
                pygame.image.save(screen, output / f"{stage}.png")
                saved.add(stage)
            if finish_frames >= frames_after:
                pygame.image.save(screen, output / "final.png")
                break
            clock.tick(60)
        replay = controller.replay_payload(interrupted=bool(controller.env.agents))
        payload = {
            "format": "puyo-terminal-boundary-qa-v1",
            "fixture": fixture,
            "replay": replay,
        }
        final_hash = replay_boundary(payload)
        (output / "boundary-replay.json").write_text(
            json.dumps(payload, indent=2) + "\n"
        )
        result = {
            "display_driver": pygame.display.get_driver(),
            "evidence": "automated normal-window run; agent image inspection recorded separately",
            "replay_verified_ticks": len(replay["ticks"]),
            "replay_final_hash": final_hash,
            "timeline": timeline,
            "qa_result": controller.qa_result(
                collection_manifest=None, interrupted=False
            ),
        }
        (output / "run.json").write_text(json.dumps(result, indent=2) + "\n")
        source_modules = (
            "src.core.game",
            "src.core.headless",
            "src.core.realtime",
            "puyo_env.realtime_ai",
            "puyo_env.realtime_versus",
            "eval.realtime_arena",
            "eval.realtime_versus_ui",
            "src.ui.versus_renderer",
        )
        source_root = Path(sys.modules["src.core.game"].__file__).resolve().parents[2]
        if source_revision is None:
            source_revision = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=source_root,
                text=True,
            ).strip()
        provenance = {
            "source_revision": source_revision,
            "source_root": str(source_root),
            "source_sha256": {
                name: hashlib.sha256(
                    Path(sys.modules[name].__file__).read_bytes()
                ).hexdigest()
                for name in source_modules
            },
            "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "display_driver": pygame.display.get_driver(),
            "pygame_version": pygame.version.ver,
            "python_version": sys.version,
        }
        (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        return result
    finally:
        controller.shutdown()
        pygame.quit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--replay", type=Path)
    parser.add_argument(
        "--source-revision", help="Revision of an extracted source archive"
    )
    parser.add_argument(
        "--survivor", choices=("player_0", "player_1"), default="player_0"
    )
    parser.add_argument("--chains", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    if args.replay:
        print(replay_boundary(json.loads(args.replay.read_text())))
    elif args.output:
        result = capture(
            args.output,
            survivor=args.survivor,
            chains=args.chains,
            source_revision=args.source_revision,
        )
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "display_driver",
                        "replay_verified_ticks",
                        "replay_final_hash",
                    )
                }
            )
        )
    else:
        parser.error("--output or --replay is required")


if __name__ == "__main__":
    main()
