import argparse
import ast
import json
import os
import statistics
import subprocess
import threading
import time
from collections import defaultdict

import pygame
import puyo_env.realtime_ai as realtime_ai

import eval.realtime_versus_ui as ui
from eval.realtime_versus_ui import RealtimeVersusMatchController, RealtimeVersusUiConfig
from src.ui.versus_renderer import SCREEN_HEIGHT, SCREEN_WIDTH, VersusRenderer


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * q
    lo = int(index)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (index - lo)


def stats(values):
    return {"n": len(values), "p50": percentile(values, 0.5), "p95": percentile(values, 0.95), "p99": percentile(values, 0.99), "max": max(values) if values else None}


def proc(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            fields = f.read().rsplit(") ", 1)[1].split()
        with open(f"/proc/{pid}/status") as f:
            lines = f.readlines()
        values = {line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in lines if ":" in line}
        return {"pid": pid, "cpu_ticks": int(fields[11]) + int(fields[12]), "rss_kb": int(values.get("VmRSS", "0 kB").split()[0]), "threads": int(values.get("Threads", "0"))}
    except (FileNotFoundError, ProcessLookupError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", required=True)
    ap.add_argument("--both", action="store_true")
    ap.add_argument("--frames", type=int, default=600)
    ap.add_argument("--output", required=True)
    ap.add_argument("--overlay", action="store_true")
    ap.add_argument("--legacy", action="store_true")
    args = ap.parse_args()
    if args.legacy:
        source = subprocess.check_output(["git", "show", "HEAD:eval/realtime_versus_ui.py"], text=True)
        tree = ast.parse(source)
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RealtimeVersusMatchController")
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_build_replay_tick")
        namespace = dict(vars(ui))
        exec(compile(ast.Module(body=[method], type_ignores=[]), "legacy_replay_tick", "exec"), namespace)
        RealtimeVersusMatchController._build_replay_tick = namespace["_build_replay_tick"]
    settings = dict(policy_a=args.policy, policy_b=args.policy if args.both else "random", seed=55, speed=1.0, max_ticks=2400, plan_overlay=args.overlay, nextgen_profile="nextgen_safe_build", nextgen_backend="native", deep_chain_profile="reference", deep_chain_backend="native")
    config = RealtimeVersusUiConfig(**settings)
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    clock = pygame.time.Clock()
    controller = RealtimeVersusMatchController(config)
    renderer = VersusRenderer(screen)
    samples = defaultdict(list)
    input_events = []
    process_samples = []
    tick_durations = []
    functions = defaultdict(list)
    cache_samples = []
    def wrap(obj, name, label=None):
        original = getattr(obj, name)
        def timed(*a, **kw):
            t = time.perf_counter_ns()
            if name == "_complete_decision":
                payload = getattr(obj, "latest_policy_diagnostics", {})
                reuse = payload.get("search", {}).get("shared_reuse", {}) if isinstance(payload, dict) else {}
                cache_samples.append({"hit": reuse.get("hit"), "worker_seconds": a[4] if len(a) > 4 else None})
            try:
                return original(*a, **kw)
            finally:
                functions[label or name].append((time.perf_counter_ns() - t) / 1e6)
        setattr(obj, name, timed)
    for name in ("_build_replay_tick", "_sync_display_boards", "tactical_diagnostics"):
        wrap(controller, name)
    wrap(controller.env, "step", "env_step")
    for agent, item in controller.controllers.items():
        wrap(item, "next_input", "next_input_" + agent)
        if getattr(item, "nextgen_scheduler", None):
            for name in ("prepare", "stale", "finish", "accept"):
                wrap(item.nextgen_scheduler, name, "scheduler_" + name)
            for name in ("_complete_decision", "_activate_nextgen", "_record_stale_decision", "_submit_decision"):
                wrap(item, name)
            wrap(item, "_plan_action")
    wrap(realtime_ai, "nextgen_authoritative_action_mask", "authoritative_mask")
    original_advance = controller.advance_tick
    def timed_advance():
        start = time.perf_counter_ns()
        try:
            return original_advance()
        finally:
            tick_durations.append((time.perf_counter_ns() - start) / 1e6)
    controller.advance_tick = timed_advance
    previous = time.perf_counter_ns()
    start_wall = previous
    stop_inputs = threading.Event()
    def input_feeder():
        sequence = 1
        while not stop_inputs.is_set():
            expected_ns = start_wall + sequence * 50_000_000
            delay = (expected_ns - time.perf_counter_ns()) / 1e9
            if delay > 0 and stop_inputs.wait(delay):
                return
            try:
                pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F12, posted_ns=expected_ns))
            except pygame.error:
                return
            sequence += 1
    feeder = threading.Thread(target=input_feeder, daemon=True)
    feeder.start()
    try:
        for i in range(args.frames):
            before_tick = time.perf_counter_ns()
            delta = clock.tick(60) / 1000.0
            event_start = time.perf_counter_ns()
            for event in pygame.event.get():
                if event.type == pygame.KEYDOWN:
                    if hasattr(event, "posted_ns"):
                        samples["input_age_ms"].append((time.perf_counter_ns() - event.posted_ns) / 1e6)
                        input_events.append({"frame": i, "posted_ns": event.posted_ns, "handled_ns": time.perf_counter_ns(), "key": event.key})
                    controller.handle_keydown(event.key)
                elif event.type == pygame.KEYUP:
                    controller.handle_keyup(event.key)
            event_end = time.perf_counter_ns()
            controller.update(delta)
            update_end = time.perf_counter_ns()
            renderer.draw(controller)
            draw_end = time.perf_counter_ns()
            samples["frame_interval_ms"].append((draw_end - previous) / 1e6)
            samples["clock_wait_ms"].append((event_start - before_tick) / 1e6)
            samples["events_ms"].append((event_end - event_start) / 1e6)
            samples["update_ms"].append((update_end - event_end) / 1e6)
            samples["render_ms"].append((draw_end - update_end) / 1e6)
            samples["tick_count"].append(controller.env.match.tick)
            previous = draw_end
            if i % 60 == 0:
                pids = [os.getpid()] + [e.process_pid for e in controller._decision_executors.values() if e.process_pid]
                process_samples.append({"frame": i, "processes": [p for pid in pids if (p := proc(pid))]})
        diagnostics = {agent: item.diagnostics.to_dict() for agent, item in controller.controllers.items()}
        ledger = {agent: [x.to_dict() for x in item.nextgen_scheduler.ledger] for agent, item in controller.controllers.items() if getattr(item, "nextgen_scheduler", None)}
        result = {"settings": settings, "frames": args.frames, "elapsed_seconds": (previous - start_wall) / 1e9, "ticks": controller.env.match.tick, "samples": {k: stats(v) for k, v in samples.items() if k != "tick_count"}, "tick_duration_ms": stats(tick_durations), "tick_catchup": stats([b-a for a,b in zip(samples["tick_count"],samples["tick_count"][1:])]), "functions_ms": {k: stats(v) for k,v in functions.items()}, "functions_total_ms": {k: sum(v) for k,v in functions.items()}, "cache_samples": cache_samples, "raw_samples": dict(samples), "input_events": input_events, "process_samples": process_samples, "diagnostics": diagnostics, "ledger": ledger}
    finally:
        stop_inputs.set()
        feeder.join(timeout=1)
        controller.shutdown()
        pygame.quit()
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k not in ("ledger", "process_samples", "diagnostics")}, indent=2), flush=True)


if __name__ == "__main__":
    main()
