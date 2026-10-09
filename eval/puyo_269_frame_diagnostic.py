"""One fixed dual-nextgen run with scoped wall/thread CPU diagnostics.

This is an instrumented cause-analysis run, not a minimal-probe gate result.
Product code, GC policy, game ticks, and the existing 25/50 ms gate are unchanged.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import functools
import gc
import hashlib
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch


class Recorder:
    def __init__(self):
        self.frame = -1
        self.rows = []
        self.local = threading.local()
        self.gc_started = {}

    def start(self, label):
        return (label, self.frame, time.perf_counter_ns(), time.thread_time_ns(),
                threading.current_thread().name)

    def end(self, token, **extra):
        ended, cpu = time.perf_counter_ns(), time.thread_time_ns()
        label, frame, started, started_cpu, thread = token
        self.rows.append({"label": label, "frame_start": frame, "frame_end": self.frame,
                          "thread": thread, "started_ns": started, "ended_ns": ended,
                          "wall_ms": (ended-started)/1e6,
                          "thread_cpu_ms": (cpu-started_cpu)/1e6, **extra})

    def wrap(self, label, function):
        @functools.wraps(function)
        def measured(*args, **kwargs):
            stack = getattr(self.local, "stack", ())
            self.local.stack = (*stack, label)
            token = self.start(label)
            try:
                return function(*args, **kwargs)
            finally:
                self.end(token, parent=stack[-1] if stack else None)
                self.local.stack = stack
        return measured

    def scoped(self, label, function):
        measured = self.wrap(label, function)
        @functools.wraps(function)
        def in_scope(*args, **kwargs):
            if getattr(self.local, "stack", ()):
                return measured(*args, **kwargs)
            return function(*args, **kwargs)
        return in_scope

    def gc_callback(self, phase, info):
        key = (threading.get_ident(), info["generation"])
        if phase == "start":
            self.gc_started[key] = self.start("gc_collect")
        else:
            token = self.gc_started.pop(key, None)
            if token is not None:
                self.end(token, generation=info["generation"],
                         collected=info["collected"], uncollectable=info["uncollectable"])


def run(output, diagnostic_output):
    import copy
    import pygame
    from agents import nextgen_contracts as contracts
    from eval import puyo_273_gui_probe as probe
    import puyo_env.nextgen_scheduler as scheduler
    import puyo_env.realtime_ai as ai

    recorder = Recorder()
    original_clock = pygame.time.Clock

    class Clock:
        def __init__(self):
            self.clock = original_clock()

        def tick(self, fps):
            recorder.frame += 1
            return recorder.wrap("clock_tick", self.clock.tick)(fps)

    args = SimpleNamespace(policy="nextgen_tactic_manager", opponent="nextgen_tactic_manager",
                           frames=360, minimal=True, overlay=True, reference_mask=False,
                           geometric_reference=False, output=str(output))
    with ExitStack() as stack:
        stack.enter_context(patch.object(pygame.time, "Clock", Clock))
        for obj, name, label in (
            (probe.RealtimeVersusMatchController, "update", "update"),
            (probe.RealtimeVersusMatchController, "advance_tick", "tick"),
            (probe.VersusRenderer, "draw", "render"),
            (scheduler.NextgenScheduler, "prepare", "prepare"),
            (scheduler.NextgenScheduler, "accept", "accept"),
            (scheduler.NextgenScheduler, "finish", "finish"),
            (ai.RealtimePolicyController, "_activate_nextgen", "activation"),
            (ai.RealtimePolicyController, "_complete_decision", "completion"),
            (scheduler, "decode_nextgen_payload", "reader_decode"),
        ):
            stack.enter_context(patch.object(obj, name, recorder.wrap(label, getattr(obj, name))))
        stack.enter_context(patch.object(contracts.Diagnostics, "to_dict",
                                        recorder.scoped("diagnostics_to_dict", contracts.Diagnostics.to_dict)))
        # Replace only the two module references, not copy.deepcopy globally:
        # recursive copies must not acquire a new timer for every object.
        copy_proxy = SimpleNamespace(copy=copy.copy, deepcopy=recorder.scoped("deepcopy", copy.deepcopy))
        for module in (scheduler, ai):
            stack.enter_context(patch.object(module, "copy", copy_proxy))
        gc.callbacks.append(recorder.gc_callback)
        try:
            probe.run(args)
        finally:
            gc.callbacks.remove(recorder.gc_callback)
    raw = json.loads(output.read_text())
    samples = raw["raw_samples"]
    residual = [frame-event-update-render for frame,event,update,render in zip(
        samples["frame_interval_ms"], samples["event_ms"], samples["update_ms"], samples["render_ms"])]
    diagnostic_output.write_text(json.dumps({
        "purpose": "cause_analysis_only_not_minimal_gate", "source_sha": raw["source_sha"],
        "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "raw_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "clock": "perf_counter_ns wall / thread_time_ns current-thread CPU",
        "caveats": ["Nested intervals overlap and must not be summed.",
                    "Wall minus thread CPU includes scheduling, GIL and native waits; it is not OS-only.",
                    "Residual includes clock.tick and bookkeeping between measured frames.",
                    "Instrumentation changes allocation and GC timing; compare against saved minimal runs cautiously.",
                    "Reader intervals use explicit start/end frame IDs; completions can cross frames."],
        "wait_residual_ms": residual, "intervals": recorder.rows,
    }, indent=2)+"\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diagnostic-output", type=Path, required=True)
    args = parser.parse_args()
    run(args.output, args.diagnostic_output)


if __name__ == "__main__":
    main()
