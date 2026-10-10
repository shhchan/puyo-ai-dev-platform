"""Measure a long realtime QA replay with the PUYO-273 input schedule clock."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import threading
import time
from dataclasses import asdict
from pathlib import Path

import pygame

from eval.puyo_271_gui_probe import stats
from eval.qa_session import validate_qa_session
from eval.realtime_versus_ui import (
    RealtimeVersusMatchController,
    RealtimeVersusUiConfig,
    run_ui,
)

KEYS = (
    (pygame.KEYDOWN, pygame.K_w), (pygame.KEYDOWN, pygame.K_a),
    (pygame.KEYUP, pygame.K_a), (pygame.KEYDOWN, pygame.K_e),
    (pygame.KEYUP, pygame.K_e), (pygame.KEYDOWN, pygame.K_d),
    (pygame.KEYUP, pygame.K_d), (pygame.KEYDOWN, pygame.K_q),
    (pygame.KEYUP, pygame.K_q), (pygame.KEYUP, pygame.K_w),
)


def _rss_kb(pid: int) -> int | None:
    try:
        lines = Path(f"/proc/{pid}/status").read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    return None


def run(mode: str, output: Path, frames: int) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    config = RealtimeVersusUiConfig(
        policy_a="nextgen_tactic_manager", policy_b="human", seed=127,
        speed=1.0, max_ticks=1000, nextgen_backend="native",
        qa_auto_save=mode == "on", qa_save_root=str(output / "sessions"),
        exit_after_finish_frames=2,
    )
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"]))
    if dirty:
        raise RuntimeError("benchmark requires a clean committed source")
    import _puyo_deep_chain_native as native

    module = Path(getattr(native, "_puyo_deep_chain_native", native).__file__)
    stop = threading.Event()
    feeder = None
    start_ns = None
    events = []
    pending = []
    frame_ns = []
    memory = []
    workers = []
    controller_ref = None
    last_frame_ns = None
    original_get = pygame.event.get
    original_advance = RealtimeVersusMatchController.advance_tick
    original_init = RealtimeVersusMatchController.__init__

    def init_recorded(self, *args, **kwargs):
        nonlocal controller_ref, workers
        original_init(self, *args, **kwargs)
        controller_ref = self
        workers = [executor.process_pid for executor in self._decision_executors.values() if executor.process_pid]

    def get_recorded(*args, **kwargs):
        retrieved = original_get(*args, **kwargs)
        now = time.perf_counter_ns()
        for event in retrieved:
            if hasattr(event, "expected_ns"):
                row = {
                    "id": event.id, "key": event.key, "type": event.type,
                    "expected_ns": event.expected_ns, "posted_ns": event.posted_ns,
                    "handled_ns": now,
                    "active": bool(controller_ref.env.agents) if controller_ref else False,
                }
                events.append(row)
                pending.append(row)
        return retrieved

    def advance_recorded(self):
        before = self.env.match.tick
        result = original_advance(self)
        if self.env.match.tick != before:
            now = time.perf_counter_ns()
            for event in pending:
                event["state_ns"] = now
            pending.clear()
        return result

    def feed():
        sequence = 0
        while not stop.is_set():
            expected = start_ns + (sequence + 1) * 50_000_000
            if stop.wait(max(0, (expected - time.perf_counter_ns()) / 1e9)):
                return
            kind, key = KEYS[sequence % len(KEYS)]
            posted = time.perf_counter_ns()
            try:
                pygame.event.post(pygame.event.Event(kind, key=key, id=sequence,
                                                     expected_ns=expected, posted_ns=posted))
            except pygame.error:
                return
            sequence += 1

    def frame_callback(_screen, index):
        nonlocal feeder, start_ns, last_frame_ns
        now = time.perf_counter_ns()
        if last_frame_ns is not None:
            frame_ns.append(now - last_frame_ns)
        last_frame_ns = now
        for event in events:
            if "state_ns" in event and "rendered_ns" not in event:
                event["rendered_ns"] = now
        if index % 60 == 0:
            memory.append({"frame": index, "parent_rss_kb": _rss_kb(os.getpid()),
                           "worker_rss_kb": [_rss_kb(pid) for pid in workers]})
        if feeder is None:
            start_ns = now
            feeder = threading.Thread(target=feed, daemon=True)
            feeder.start()

    RealtimeVersusMatchController.__init__ = init_recorded
    RealtimeVersusMatchController.advance_tick = advance_recorded
    pygame.event.get = get_recorded
    started = time.perf_counter()
    try:
        result = run_ui(config, max_frames=frames, frame_callback=frame_callback)
    finally:
        stop.set()
        if feeder is not None:
            feeder.join(timeout=2)
        pygame.event.get = original_get
        RealtimeVersusMatchController.advance_tick = original_advance
        RealtimeVersusMatchController.__init__ = original_init
    elapsed = time.perf_counter() - started
    session = result["artifacts"].get("qa_session")
    validation = validate_qa_session(session) if session else None
    paths = {name: (Path(session) / name).stat().st_size for name in ("replay.json", "result.json", "manifest.json")} if session else {}
    active = [row for row in events if row["active"]]
    samples = {}
    for name, end, begin in (
        ("input_schedule_ms", "handled_ns", "expected_ns"),
        ("input_queue_ms", "handled_ns", "posted_ns"),
        ("input_to_state_ms", "state_ns", "posted_ns"),
        ("input_to_render_ms", "rendered_ns", "posted_ns"),
    ):
        samples[name] = stats([(row[end] - row[begin]) / 1e6 for row in active if end in row])
    report = {
        "mode": mode, "config": asdict(config), "source_commit": source,
        "native_module": str(module), "native_sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
        "python": os.sys.executable, "frames": result["runtime"]["frames"],
        "ticks": result["ticks"], "interrupted": result["result"]["interrupted"],
        "elapsed_seconds": elapsed, "runtime": result["runtime"],
        "qa_save_elapsed_seconds": result["artifacts"].get("qa_save_elapsed_seconds"),
        "qa_session": session, "qa_validation_errors": validation,
        "file_bytes": paths, "input_samples": samples,
        "frame_interval_ms": stats([n / 1e6 for n in frame_ns]),
        "events": active, "memory_samples": memory,
        "max_parent_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "workers_stopped": {str(pid): not Path(f"/proc/{pid}").exists() for pid in workers},
    }
    (output / "measurement.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in (
        "mode", "frames", "ticks", "interrupted", "elapsed_seconds", "runtime",
        "qa_save_elapsed_seconds", "qa_validation_errors", "file_bytes",
        "input_samples", "max_parent_rss_kb", "workers_stopped",
    )}))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("off", "on"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=1100)
    args = parser.parse_args()
    run(args.mode, args.output, args.frames)


if __name__ == "__main__":
    main()
