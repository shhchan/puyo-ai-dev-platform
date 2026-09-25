"""Compare the GUI's real worker path with paused single-tick stepping.

No display is opened. This is a reproduction/receipt diagnostic, not G2 QA.
Example: python -m eval.nextgen_realtime_diagnostic --mode normal --output /tmp/run
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from agents import nextgen_contracts as c
from agents.template_catalog import match_templates
from agents.nextgen_profiles import NEXTGEN_PROFILE_CHOICES
from eval.realtime_versus_ui import RealtimeVersusMatchController, RealtimeVersusUiConfig


def source_identity():
    root = Path(__file__).resolve().parents[1]
    paths = subprocess.check_output(
        ["git", "ls-files", "agents", "puyo_env", "src/core", "src/ui", "eval", "train/config"],
        text=True, cwd=root,
    ).splitlines()
    paths.append(str(Path(__file__).relative_to(root)))
    return {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=root).strip(),
        "files_sha256": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in sorted(set(paths))},
    }


def template_observation(catalog, snapshot, key):
    """Observed required-cell progress, never the candidate's future score."""
    if key is None:
        return None
    result = match_templates(
        catalog, snapshot.own.visible_board, (), node_budget=0, binding_budget=1,
        preferred_key=key,
    )
    candidate = next((v for v in result.candidates if v.key == key), None)
    return None if candidate is None else {
        "key": key, "progress": candidate.progress,
        "complete": candidate.complete, "compatible": candidate.compatible,
    }


def summarize(attempts):
    adopted = [r for r in attempts if r["record"]["outcome"] == "activated"]
    return {
        "outcomes": dict(Counter(r["record"]["outcome"] for r in attempts)),
        "fallbacks": sum(r["record"]["fallback"] for r in attempts),
        "adopted_actions": [r["record"]["executed_action"] for r in adopted],
        "adopted_piece_ids": [r["piece_id"] for r in adopted],
        "adopted_tactics": [r["payload"]["nextgen"]["selection"]["selected_tactic_id"] for r in adopted],
        "shared_cache_hits": sum(r["payload"].get("search", {}).get("shared_reuse", {}).get("hit", False) for r in attempts),
    }


def run(*, mode, seed=55, placements=15, max_ticks=6000, opponent="random", backend="native", profile="nextgen_smoke", write_replay=True, output):
    if mode not in ("normal", "step"):
        raise ValueError("mode must be normal or step")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    source = source_identity()
    config = RealtimeVersusUiConfig(
        policy_a="nextgen_tactic_manager", policy_b=opponent,
        seed=seed, seed_a=seed, seed_b=seed + 10_000,
        nextgen_seed=seed, nextgen_templates="gtr",
        nextgen_selection_mode="argmax", nextgen_commit_turns=14,
        nextgen_profile=profile, latency_mode="measured",
        nextgen_backend=backend,
        max_ticks=max_ticks, replay_path=str(output / "replay.json") if write_replay else None,
        dataset_root=str(output / "dataset"),
    )
    game = RealtimeVersusMatchController(config)
    controller = game.controllers["player_0"]
    runtime = controller.nextgen_scheduler
    attempts, requests, boards = [], [], []
    seen = set()
    last_request = None
    placement_count = 0
    started = time.monotonic()
    try:
        while game.env.agents and placement_count < placements:
            tick_started = time.monotonic()
            if mode == "step" and controller._async_decision is not None:
                # The worker keeps running while n is not pressed. Advance one
                # match tick only after it completes, without changing latency mode.
                controller._async_decision.future.result(timeout=120)
            before = game.env.match.public_snapshot()
            game.advance_tick()
            if runtime.data is not None and runtime.data["identity"] != last_request:
                last_request = runtime.data["identity"]
                requests.append({
                    "identity": last_request.to_dict(),
                    "public": runtime.data["public"].to_dict(),
                    "execution": runtime.data["execution"].to_dict(),
                })
            record = controller.diagnostics.last_decision
            if record is not None and record.outcome != "scheduled":
                identity = (record.request_tick, record.completion_tick, record.outcome)
                if identity not in seen:
                    seen.add(identity)
                    payload = copy.deepcopy(controller.latest_policy_diagnostics)
                    diagnostics = payload.get("nextgen")
                    changed = []
                    if diagnostics:
                        c.Diagnostics.from_dict(diagnostics)
                        requested = diagnostics["request"]["public"]
                        changed = [k for k, v in before.to_dict().items() if requested[k] != v]
                    attempts.append({
                        "record": asdict(record), "payload": payload,
                        "piece_id": None if runtime.data is None else runtime.data["piece_id"],
                        "pre_execution_public": before.to_dict(),
                        "changed_public_fields": changed,
                    })
                    print(f"{mode} tick={game.env.match.tick} outcome={record.outcome} "
                          f"requested={record.requested_action} executed={record.executed_action} "
                          f"seconds={record.policy_elapsed_seconds:.3f}", flush=True)
            public = game.env.match.public_snapshot()
            count = sum(e.kind == "placement" and e.player_id == 0 for e in public.events)
            if count != placement_count:
                placement_count = count
                boards.append((public, None if runtime.phase.candidate is None else runtime.phase.candidate.key, "lock"))
            if boards and boards[-1][2] == "lock" and public.own.phase == "control":
                # Split pairs can still fall after the lock event. Capture the
                # first settled control board rather than label that animation
                # intermediate as broken/completed template geometry.
                boards[-1] = (public, boards[-1][1], "settled_control")
            if mode == "normal":
                # Bound the simulation to 60 Hz; slow host work is not caught up
                # in a burst. Actual wall duration is included in the report.
                time.sleep(max(0, 1 / 60 - (time.monotonic() - tick_started)))
        elapsed = time.monotonic() - started
        ledger = game.nextgen_ledger_payload()
        report = {
            "schema": "puyo.nextgen.realtime_diagnostic.v1",
            "mode": mode, "config": asdict(config),
            "source": source,
            "source_changed_during_run": source != source_identity(),
            "execution": "GUI RealtimeVersusMatchController / PolicyProcessExecutor spawn; no display",
            "clock": "60 Hz maximum; no catch-up" if mode == "normal" else "wait worker between single ticks",
            "elapsed_seconds": elapsed, "ticks": game.env.match.tick,
            "target_placements": placements, "observed_placements": placement_count,
            "replay_saved": write_replay,
            "termination": "placement_limit" if placement_count >= placements else "match_or_tick_limit",
            "requests": requests, "attempts": attempts,
            "placements": [{"public": p.to_dict(), "stage": stage, "template": template_observation(game.nextgen_catalog, p, key)} for p, key, stage in boards],
            "summary": summarize(attempts),
            "controller": controller.diagnostics.to_dict(),
            "quality_status": "diagnostic_only; human GUI QA and G2 not established",
        }
        outputs = [("report", report), ("ledger", ledger)]
        if write_replay:
            outputs.append(("replay", game.replay_payload(interrupted=bool(game.env.agents))))
        for name, value in outputs:
            (output / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n")
        print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
        return report
    finally:
        game.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("normal", "step"), required=True)
    parser.add_argument("--seed", type=int, default=55)
    parser.add_argument("--placements", type=int, default=15)
    parser.add_argument("--max-ticks", type=int, default=6000)
    parser.add_argument("--opponent", choices=("random", "human"), default="random")
    parser.add_argument("--backend", choices=("native", "python"), default="native")
    parser.add_argument("--profile", choices=NEXTGEN_PROFILE_CHOICES, default="nextgen_smoke")
    parser.add_argument("--omit-replay", dest="write_replay", action="store_false",
                        help="Save report/ledger without retaining the large per-tick GUI replay")
    parser.add_argument("--output", type=Path, required=True)
    args = vars(parser.parse_args())
    if args["placements"] < 1 or args["max_ticks"] < 1:
        parser.error("placements and max-ticks must be positive")
    run(**args)


if __name__ == "__main__":
    main()
