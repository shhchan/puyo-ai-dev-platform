"""Fixed DISPLAY cadence probe; synthetic input is not human acceptance QA."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import threading
import time

import pygame
import _puyo_deep_chain_native as native
from agents.deep_chain_native import NativeDeepChainBackend
import puyo_env.realtime_ai as ai
from eval.puyo_271_gui_probe import proc, stats
from eval.realtime_versus_ui import RealtimeVersusMatchController, RealtimeVersusUiConfig
from src.ui.versus_renderer import SCREEN_HEIGHT, SCREEN_WIDTH, VersusRenderer


def run(args):
    source_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    source_diff = hashlib.sha256(subprocess.check_output(["git", "diff", "--", "puyo_env"])).hexdigest()
    if args.reference_mask:
        # Baseline implementation at 595dbed, retained only in this evaluator.
        def reference(simulator, *, timing=None, max_expanded_states=2000):
            if simulator.game.state != "control" or simulator.game.game_over:
                return ai.np.zeros(ai.NUM_ACTIONS, dtype=ai.np.bool_)
            return ai.np.asarray([ai.plan_placement_action(simulator, action, timing=timing,
                max_expanded_states=max_expanded_states).reachable for action in ai.PLACEMENT_ACTIONS], dtype=ai.np.bool_)
        ai.realtime_reachable_action_mask = reference
    settings = dict(policy_a=args.policy, policy_b=args.opponent, seed=55, speed=1.0,
                    max_ticks=2400, plan_overlay=False, nextgen_profile="nextgen_safe_build",
                    nextgen_backend="native", keybindings_path="/tmp/puyo273-no-keybindings.json")
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    controller = RealtimeVersusMatchController(RealtimeVersusUiConfig(**settings))
    renderer, clock = VersusRenderer(screen), pygame.time.Clock()
    samples, functions = defaultdict(list), defaultdict(list)
    events, ticks, processes, cache = [], [], [], []
    pending, rendered = [], []
    workers = [e.process_pid for e in controller._decision_executors.values() if e.process_pid]
    frame = 0
    active_frames = []

    def wrap(obj, name, label):
        original = getattr(obj, name)
        def measured(*a, **kw):
            started = time.perf_counter_ns()
            try:
                return original(*a, **kw)
            finally:
                functions[label].append({"frame": frame, "ms": (time.perf_counter_ns()-started)/1e6})
        setattr(obj, name, measured)

    if not args.minimal:
        for name in ("_build_replay_tick", "_sync_display_boards", "tactical_diagnostics"):
            wrap(controller, name, name)
        wrap(controller.env, "step", "simulation")
        wrap(ai, "nextgen_authoritative_action_mask", "authoritative_mask")
        for agent, item in controller.controllers.items():
            wrap(item, "next_input", "next_input_" + agent)
            if getattr(item, "nextgen_scheduler", None):
                for name in ("prepare", "stale", "accept", "finish"):
                    wrap(item.nextgen_scheduler, name, "scheduler_" + name)
                for name in ("_complete_decision", "_activate_nextgen", "_plan_action", "_submit_decision"):
                    wrap(item, name, name)
                complete = item._complete_decision
                def completed(*a, _original=complete, _item=item, **kw):
                    result = _original(*a, **kw)
                    payload = _item.latest_policy_diagnostics
                    reuse = payload.get("search", {}).get("shared_reuse", {})
                    cache.append({"frame": frame, "hit": reuse.get("hit"), "worker_seconds": a[4] if len(a)>4 else None})
                    return result
                item._complete_decision = completed
        for executor in controller._decision_executors.values():
            wrap(executor, "submit_policy", "ipc_submit_enqueue")

    def human_state():
        game = controller.env.match.player_states["player_1"].simulator.game
        return [game.state, game.puyo_x, game.puyo_y, game.puyo_rot.name, game.blocked_rotate_input_count]

    advance = controller.advance_tick
    def advance_recorded():
        started = time.perf_counter_ns()
        before = human_state() if args.opponent == "human" else None
        old_tick = controller.env.match.tick
        result = advance()
        ended = time.perf_counter_ns()
        samples["tick_ms"].append((ended-started)/1e6)
        if args.opponent == "human":
            tick_input = controller.last_inputs.get("player_1")
            ticks.append({"frame": frame, "tick": controller.env.match.tick, "before": before,
                          "after": human_state(),
                          "fired": [a.name for a in controller.infos["player_1"]["match_result"].player_results["player_1"].fired_actions],
                          "held": [a.name for a in controller.env.match.player_states["player_1"].simulator.held_actions],
                          "ui_held": [a.name for a in controller.human._held], "press": [a.name for a in tick_input.press] if tick_input else [],
                          "release": [a.name for a in tick_input.release] if tick_input else []})
        # First simulation boundary after event dispatch; does not imply movement
        # succeeded (collision, animation and same-tick coalescing remain visible).
        for event in pending if controller.env.match.tick != old_tick else ():
            event["state_ns"] = ended
            event["tick"] = controller.env.match.tick
            samples["input_to_state_ms"].append((ended-event["posted_ns"])/1e6)
            rendered.append(event)
        if controller.env.match.tick != old_tick:
            pending.clear()
        return result
    controller.advance_tick = advance_recorded
    start = time.perf_counter_ns()
    previous = start
    stop = threading.Event()
    posted = []
    def feed():
        sequence = 0
        # Held DOWN plus alternating horizontal/rotation taps; 50 ms edges.
        keys = [(pygame.KEYDOWN, pygame.K_w), (pygame.KEYDOWN, pygame.K_a),
                (pygame.KEYUP, pygame.K_a), (pygame.KEYDOWN, pygame.K_e),
                (pygame.KEYUP, pygame.K_e), (pygame.KEYDOWN, pygame.K_d),
                (pygame.KEYUP, pygame.K_d), (pygame.KEYDOWN, pygame.K_q),
                (pygame.KEYUP, pygame.K_q), (pygame.KEYUP, pygame.K_w)]
        while not stop.is_set():
            expected = start + (sequence+1)*50_000_000
            if stop.wait(max(0, (expected-time.perf_counter_ns())/1e9)):
                return
            kind, key = keys[sequence % len(keys)] if args.opponent == "human" else (pygame.KEYDOWN, pygame.K_F12)
            event = {"id": sequence, "expected_ns": expected, "posted_ns": time.perf_counter_ns(), "type": kind, "key": key}
            try:
                pygame.event.post(pygame.event.Event(kind, **{k:v for k,v in event.items() if k != "type"}))
                posted.append(event)
            except pygame.error:
                return
            sequence += 1
    feeder = threading.Thread(target=feed, daemon=True)
    feeder.start()
    try:
        for frame in range(args.frames):
            delta = clock.tick(60)/1000
            t0 = time.perf_counter_ns()
            active_frames.append(bool(controller.env.agents))
            for event in pygame.event.get():
                if event.type in (pygame.KEYDOWN, pygame.KEYUP):
                    if hasattr(event, "posted_ns"):
                        row = {"active": active_frames[-1], "id":event.id,"frame":frame,"type":event.type,"key":event.key,
                               "expected_ns":event.expected_ns,"posted_ns":event.posted_ns,"handled_ns":time.perf_counter_ns()}
                        events.append(row)
                        pending.append(row)
                        samples["input_queue_ms"].append((row["handled_ns"]-event.posted_ns)/1e6)
                        samples["input_schedule_ms"].append((row["handled_ns"]-event.expected_ns)/1e6)
                    if event.type == pygame.KEYDOWN:
                        controller.handle_keydown(event.key)
                    else:
                        controller.handle_keyup(event.key)
            t1 = time.perf_counter_ns()
            old_tick = controller.env.match.tick
            controller.update(delta)
            t2 = time.perf_counter_ns()
            renderer.draw(controller)
            t3 = time.perf_counter_ns()
            for event in rendered:
                event["rendered_ns"] = t3
                samples["input_to_render_ms"].append((t3-event["posted_ns"])/1e6)
            rendered.clear()
            for key, value in (("frame_interval_ms",(t3-previous)/1e6),("event_ms",(t1-t0)/1e6),
                               ("update_ms",(t2-t1)/1e6),("render_ms",(t3-t2)/1e6),
                               ("tick_catchup",controller.env.match.tick-old_tick)):
                samples[key].append(value)
            previous = t3
            if not args.minimal and frame % 60 == 0:
                processes.append({"frame":frame,"processes":[p for pid in [os.getpid(),*workers] if (p:=proc(pid))]})
        result = {"settings":settings,"frames":args.frames,"elapsed_seconds":(previous-start)/1e9,
                  "ticks":controller.env.match.tick,"minimal":args.minimal,
                  "samples":{k:stats(v) for k,v in samples.items()},"raw_samples":dict(samples),
                  "functions_ms":{k:stats([r["ms"] for r in v]) for k,v in functions.items()},
                  "functions_raw":dict(functions),"cache_samples":cache,"input_events":events,
                  "human_ticks":ticks,"process_samples":processes,
                  "diagnostics":{a:i.diagnostics.to_dict() for a,i in controller.controllers.items()},
                  "scheduler_errors": {a:i.nextgen_scheduler.errors for a,i in controller.controllers.items()
                                       if getattr(i,"nextgen_scheduler",None)},
                  "native": {"capabilities":NativeDeepChainBackend().capabilities.to_dict(),
                             "module":native.__file__, "sha256":hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest()},
                  "source_sha":source_sha, "reference_mask":args.reference_mask,
                  "source_diff_sha256":source_diff,
                  "host":platform.uname()._asdict(),"display":os.environ.get("DISPLAY"),
                  "resolution":[SCREEN_WIDTH,SCREEN_HEIGHT],"clock_ticks":os.sysconf("SC_CLK_TCK")}
    finally:
        stop.set()
        feeder.join(timeout=1)
        controller.shutdown()
        pygame.quit()
    result["active_frames"] = active_frames
    result["active_samples"] = {k:stats([v for v, active in zip(samples[k],active_frames) if active])
                                for k in ("frame_interval_ms","event_ms","update_ms","render_ms","tick_catchup")}
    for label, field, origin in (("input_queue_ms","handled_ns","posted_ns"),
                                  ("input_schedule_ms","handled_ns","expected_ns"),
                                  ("input_to_state_ms","state_ns","posted_ns"),
                                  ("input_to_render_ms","rendered_ns","posted_ns")):
        result["active_samples"][label] = stats([(r[field]-r[origin])/1e6 for r in events if r["active"] and field in r])
    result["posted_events"] = posted
    result["worker_cleanup"] = {str(pid):not Path(f"/proc/{pid}").exists() for pid in workers}
    Path(args.output).write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({"output":args.output,"samples":result["samples"],"cleanup":result["worker_cleanup"]}))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy",default="nextgen_tactic_manager")
    parser.add_argument("--opponent",default="random")
    parser.add_argument("--frames",type=int,default=600)
    parser.add_argument("--minimal",action="store_true")
    parser.add_argument("--reference-mask",action="store_true")
    parser.add_argument("--output",required=True)
    run(parser.parse_args())

if __name__ == "__main__":
    main()
