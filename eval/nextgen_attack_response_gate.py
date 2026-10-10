"""PUYO-277 public challenge gate, separate from solo chain quality.

Artificial initial boards and attack scripts are replay inputs. All simulation
uses the existing realtime engine; the policy receives only its scheduler's
public request. Offline public-root audits never influence a selected action.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import signal
import subprocess
import time
from dataclasses import asdict, replace
from pathlib import Path

from agents import nextgen_contracts as c
from agents.compact_search import CompactSearchState, legal_action_indices, transition
from agents.long_horizon_search import LongHorizonSearchConfig
from agents.nextgen_response_search import drop_distributions, drop_public_garbage
from agents.nextgen_shared_search import _pairs, _public_state
from agents.nextgen_tactic_manager import NextgenTacticManagerPolicy
from eval.nextgen_safe_build_gate import build_identity
from puyo_env.actions import action_to_placement
from puyo_env.realtime_ai import RealtimeDecisionConfig, RealtimePolicyController
from puyo_env.realtime_versus import RealtimeVersusMatch
from src.core.constants import PuyoColor
from src.core.puyo import Puyo
from src.core.realtime import RealtimeTimingConfig, TickInput
from src.core.tsumo import EsportsTsuSource

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/puyo_277_attack_response_cases.json"
REGISTRATION_COMMIT = "00a8fe1"
SOURCE_DIRS = ("agents", "puyo_env", "src/core", "eval", "train/config", "tests")


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def read(path):
    path = Path(path)
    data = path.read_bytes()
    return json.loads(gzip.decompress(data) if path.suffix == ".gz" else data)


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()
    path.write_bytes(gzip.compress(data, mtime=0) if path.suffix == ".gz" else data)


def source_identity():
    paths = subprocess.check_output(
        ["git", "ls-files", *SOURCE_DIRS], cwd=ROOT, text=True
    ).splitlines()
    return {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}


def registered_cases():
    fixtures = read(FIXTURES)
    frozen = subprocess.check_output(
        ["git", "show", f"{REGISTRATION_COMMIT}:{FIXTURES.relative_to(ROOT)}"], cwd=ROOT
    )
    if json.loads(frozen) != fixtures:
        raise ValueError(
            "preregistered fixtures changed; retain failures, do not replace the cohort"
        )
    return fixtures


def initialize(output, source):
    output = Path(output)
    if (output / "manifest.json").exists():
        raise ValueError("manifest already exists")
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all", *SOURCE_DIRS],
        cwd=ROOT,
        text=True,
    )
    if dirty.strip():
        raise ValueError("commit implementation and tests before formal registration")
    cases = registered_cases()
    corpus = EsportsTsuSource(source)
    if corpus.checksum != cases["source_sha256"]:
        raise ValueError("corpus differs from registration")
    policy = make_policy(cases)
    manifest = {
        "schema": "puyo.nextgen.attack_response_manifest.v1",
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "registration_commit": REGISTRATION_COMMIT,
        "source_files": source_identity(),
        "build": build_identity(),
        "fixtures": cases,
        "fixtures_sha256": digest(cases),
        "source_path": str(Path(source).resolve()),
        "actual_policy": policy_identity(policy),
        "claim": "bounded configured-time public challenge gate; not solo G2 or measured GUI SLA",
    }
    write(output / "manifest.json", manifest)
    return manifest


def make_policy(config, *, backend=None):
    settings = config["policy"]
    return NextgenTacticManagerPolicy(
        backend=backend or settings["backend"],
        seed=settings["seed"],
        profile=c.SearchProfile(**settings["profile"]),
        search_config=LongHorizonSearchConfig(**settings["search"]),
        template_binding_budget=settings["template_binding_budget"],
    )


def policy_identity(policy):
    return {
        "backend": policy.backend.backend_id,
        "profile": policy.profile.to_dict(),
        "search": asdict(policy.search_config),
        "catalog": policy.catalog.semantic_digest,
        "template_binding_budget": policy.template_binding_budget,
    }


def setup_match(config, case, source):
    match = RealtimeVersusMatch(
        seed=277,
        timing=RealtimeTimingConfig(**config["timing"]),
        tsumo_mode="esports_tsu",
        tsumo_source=str(source),
        tsumo_pattern_id=case["pattern_id"],
        tsumo_player_1_pattern_id=case["pattern_id"],
    )
    game = match.player_states["player_0"].simulator.game
    for y, row in enumerate(case["board_bottom_up"]):
        for x, color in enumerate(row):
            game.field.place_puyo(x, y, Puyo(PuyoColor[color]))
    match.player_states["player_0"].score_carry = case["score_carry"]
    # An artificial board must never inherit an empty-reset hidden-row proof.
    match._public_inference_reset_available = False
    opponent = match.player_states["player_1"].simulator.game
    opponent.state = "countdown"
    opponent.countdown_time_left = 1e12
    public = match.public_snapshot()
    observed = [
        [c.PUBLIC_CELL_TO_COLOR[v].name for v in pair]
        for pair in public.own.known_pieces
    ]
    if observed != case["public_prefix"]:
        raise ValueError("provider prefix differs from the frozen fixture")
    return match


def inject_attacks(match, case, condition):
    if condition not in ("attack", "no_attack"):
        raise ValueError("unknown paired condition")
    applied = []
    for packet in case["attack_script"]:
        if condition == "attack" and packet["tick"] == match.tick:
            match.schedule_attack(
                "player_1", packet["units"], delay_ticks=packet["delay_ticks"]
            )
            applied.append(packet)
    return applied


def board_names(state):
    return [[v.name for v in row] for row in state.to_color_grid()]


def cells_present(board, cells):
    return bool(cells) and all(board[y][x] == color for x, y, color in cells)


def with_key(state, key):
    x, y, color = key
    bit = 1 << (y * 6 + x)
    if state.occupied_mask & bit:
        return None
    planes = list(state.planes)
    planes[
        (
            PuyoColor.RED,
            PuyoColor.BLUE,
            PuyoColor.GREEN,
            PuyoColor.YELLOW,
            PuyoColor.PURPLE,
            PuyoColor.OJAMA,
        ).index(PuyoColor[color])
    ] |= bit
    return replace(state, planes=tuple(planes))


def original_cell_flow(state, pair, result):
    """Follow original cell IDs through the kernel's complete clear sequence.

    This is label transport, not a second chain resolver: vanish sets come from
    the existing kernel. Color boards are checked against its final board.
    """
    tokens = {
        (x, y): ((x, y), color)
        for y, row in enumerate(state.to_color_grid())
        for x, color in enumerate(row)
        if color != PuyoColor.EMPTY
    }
    if not result.valid:
        return {"cleared": [], "remaining": [], "consistent": False}
    x, y = result.action.axis_x, result.axis_y
    dx, dy = {"UP": (0, 1), "RIGHT": (1, 0), "DOWN": (0, -1), "LEFT": (-1, 0)}[
        result.action.rotation.name
    ]
    tokens[(x, y)] = (None, pair[0])
    tokens[(x + dx, y + dy)] = (None, pair[1])

    def settle(values):
        settled = {pos: token for pos, token in values.items() if pos[1] == 13}
        for column in range(6):
            ordered = [
                token
                for (cx, cy), token in sorted(values.items(), key=lambda v: v[0][1])
                if cx == column and cy < 13
            ]
            settled.update({(column, row): token for row, token in enumerate(ordered)})
        return settled

    tokens = settle(tokens)
    cleared = []
    for step in result.chains:
        for pos in step.vanished | step.garbage_cleared:
            origin, color = tokens.pop(pos)
            if origin is not None:
                cleared.append(
                    {
                        "origin": list(origin),
                        "at": list(pos),
                        "color": color.name,
                        "chain": step.chain_index,
                    }
                )
        tokens = settle(tokens)
    colors = [[PuyoColor.EMPTY] * 6 for _ in range(14)]
    for (x, y), (_, color) in tokens.items():
        colors[y][x] = color
    return {
        "cleared": cleared,
        "remaining": [
            {"origin": list(origin), "at": list(pos), "color": color.name}
            for pos, (origin, color) in sorted(tokens.items())
            if origin is not None
        ],
        "consistent": tuple(tuple(row) for row in colors)
        == result.state.to_color_grid(),
    }


def shape_preserved(cells, flow):
    """All annotated cell IDs survive with unchanged relative geometry."""
    if not cells:
        return None
    remaining = {tuple(v["origin"]): v for v in flow["remaining"]}
    offsets = set()
    for x, y, color in cells:
        token = remaining.get((x, y))
        if token is None or token["color"] != color:
            return False
        offsets.add((token["at"][0] - x, token["at"][1] - y))
    return len(offsets) == 1


def public_trace(request, case, search):
    """Bounded offline audit: only current public pair, board, and root mask.

    Annotation cells describe the frozen challenge, not a learned chain label.
    Geometric fireability with unknown hidden rows remains conditional.
    """
    state, complete = _public_state(request)
    board = board_names(state)
    sub = case["secondary_cells"]
    prepared = cells_present(board, sub)
    current = _pairs(request.public.own.known_pieces)[0]
    sub_positions = {(x, y) for x, y, _ in sub}
    main_positions = {(x, y) for x, y, _ in case["mainline_cells"]}
    rows = []
    keyed = with_key(state, case["missing_key"]) if case["missing_key"] else None
    for action in legal_action_indices(state):
        result = transition(state, current, action)
        flow = original_cell_flow(state, current, result)
        if result.valid and not flow["consistent"]:
            raise AssertionError("cell transport differs from existing transition")
        cleared = {tuple(v["origin"]) for v in flow["cleared"]}
        uses_sub = prepared and sub_positions <= cleared
        keyed_result = transition(keyed, current, action) if keyed is not None else None
        rows.append(
            {
                "action": action,
                "reachable": request.execution.reachable_mask[action],
                "valid": result.valid,
                "fatal_before_garbage": result.game_over,
                "chain_count": result.chain_count,
                "uses_secondary": uses_sub,
                "uses_mainline": bool(main_positions & cleared),
                "mainline_shape_preserved": shape_preserved(
                    case["mainline_cells"], flow
                ),
                "original_cell_flow": flow,
                "remaining_board_bottom_up": board_names(result.state),
                "key_counterfactual_chain": keyed_result.chain_count
                if keyed_result and keyed_result.valid
                else None,
            }
        )
    fireable = [
        r["action"]
        for r in rows
        if r["uses_secondary"]
        and r["reachable"]
        and r["valid"]
        and not r["fatal_before_garbage"]
    ]
    response = search.response_result
    status = (
        "absent"
        if not sub
        else "consumed_or_changed"
        if not prepared
        else "fireable"
        if fireable
        else "prepared"
    )
    return {
        "kind": case["secondary_kind"],
        "preparation_status": status,
        "prepared": prepared,
        "fireable_current_actions": fireable,
        "certainty": "visible_exact" if complete else "conditional_hidden_rows",
        "mainline_cells": case["mainline_cells"],
        "missing_key": case["missing_key"],
        "public_roots": rows,
        "offline_audit_transitions": len(rows) * (2 if keyed else 1),
        "offline_audit_not_policy_search": True,
        "response_status": response.status,
        "response_cutoff": response.cutoff_reason,
        "response_traces": [asdict(t) for t in response.traces],
        "survival": search.diagnostics["survival"],
        "scope": "current-pair geometric witness; activation mask and actual lock remain authoritative",
        "shape_scope": "registered cell identities and relative geometry, not general chain potential; re-annotating a changed shape is not inferred",
    }


def unavoidable_oracle(case):
    """Fixed public-board challenge oracle for the first resolution boundary.

    No hidden future, search outcome, or opponent state enters this proof.
    Only a due full-row attack is certified here; other cases stay unknown.
    """
    planes = [0] * 6
    for y, row in enumerate(case["board_bottom_up"]):
        for x, color in enumerate(row):
            if color != "EMPTY":
                planes[
                    ("RED", "BLUE", "GREEN", "YELLOW", "PURPLE", "OJAMA").index(color)
                ] |= 1 << (6 * y + x)
    state = CompactSearchState(tuple(planes))
    due = sum(
        p["units"] for p in case["attack_script"] if p["tick"] == p["delay_ticks"] == 0
    )
    rows = []
    pair = tuple(PuyoColor[v] for v in case["public_prefix"][0])
    for action in legal_action_indices(state):
        result = transition(state, pair, action)
        generated = (result.attack_score_delta + case["score_carry"]) // 70
        count = min(30, max(0, due - generated))
        outcomes = [
            drop_public_garbage(result.state, columns)[0].game_over
            for columns in drop_distributions(count)
        ]
        rows.append(
            {
                "action": action,
                "fatal": not result.valid or result.game_over or all(outcomes),
                "chain_count": result.chain_count,
                "drop_after_cancel": count,
            }
        )
    return {
        "status": "unavoidable"
        if rows and all(r["fatal"] for r in rows)
        else "not_proven",
        "scope": "frozen complete public challenge board, all geometric current roots, first boundary only",
        "roots": rows,
    }


def event_record(result):
    return json.loads(
        json.dumps(
            {
                "tick": result.tick,
                "snapshot_hash": result.snapshot_hash,
                "attack": {a: dict(v) for a, v in result.attack_diagnostics.items()},
                "dropped": dict(result.dropped_ojama),
                "events": {
                    a: [
                        {"type": e.type, "tick": e.tick, "data": dict(e.data)}
                        for e in r.events
                        if e.type in ("lock", "resolution_complete")
                    ]
                    for a, r in result.player_results.items()
                },
            }
        )
    )


def replay_run(replay, source):
    """Reapply declared setup/script and tick inputs through the same engine."""
    if replay.get("schema") != "puyo.nextgen.attack_response_replay.v1":
        raise ValueError("unsupported attack replay schema")
    match = setup_match(replay["config"], replay["case"], source)
    if match.replay_rules()["tsumo"] != {
        **replay["rules"]["tsumo"],
        "source_path": str(source),
    }:
        raise ValueError("replay provider identity mismatch")
    if match.state_hash() != replay["initial_hash"]:
        raise ValueError("initial hash mismatch")
    for entry in replay["ticks"]:
        if match.tick != entry["input_tick"]:
            raise ValueError("non-contiguous replay input tick")
        applied = inject_attacks(match, replay["case"], replay["condition"])
        if applied != entry["injected_attacks"]:
            raise ValueError("replay attack script mismatch")
        result = match.step(
            {a: TickInput.from_names(**v) for a, v in entry["inputs"].items()}
        )
        if event_record(result) != entry["result"]:
            raise ValueError(f"replay event/hash mismatch at tick {match.tick}")
    if match.state_hash() != replay["final_hash"]:
        raise ValueError("final hash mismatch")
    return match.state_hash()


def assess(case, condition, decisions, ticks, game_over, oracle):
    issues = []
    resolutions = [
        e
        for t in ticks
        for e in t["result"]["events"]["player_0"]
        if e["type"] == "resolution_complete"
    ]
    drops = [
        t["result"]["tick"] for t in ticks if t["result"]["dropped"].get("player_0", 0)
    ]
    cancels = sum(t["result"]["attack"]["player_0"]["canceled"] for t in ticks)
    fires = [e for e in resolutions if e["data"].get("chain_count", 0)]
    for row in decisions:
        receipt = row["diagnostics"]["receipt"]
        if receipt["outcome"] != "activated":
            issues.append("decision_" + receipt["outcome"])
        if row.get("lock") is None:
            issues.append("adoption_without_lock")
        elif not row["lock_matches"]:
            issues.append("selected_action_lock_mismatch")
        if not row["within_deadline"]:
            issues.append("receipt_or_lock_deadline_missed")
        if not row["quota_ok"]:
            issues.append("quota_exceeded")
        if row.get("resolution") is None:
            issues.append("adoption_without_resolution")
        elif not row.get("observed_trace_matches", False):
            issues.append("public_prediction_actual_resolution_mismatch")
        if not row.get("public_root_confirmed", False):
            issues.append("selected_public_root_not_confirmed")
        # Search completeness is separate from a positively observed witness.
        # A quota cutoff neither invalidates a confirmed response nor proves
        # an absent response impossible. Missing observations above still fail.
        if (
            condition == "no_attack"
            and row.get("resolution", {}).get("data", {}).get("chain_count", 0)
            in range(1, 10)
            and "survival" not in row["diagnostics"]["selection"]["reason"]
        ):
            issues.append("control_small_clear_without_survival_reason")
    expected = case["expected"]
    excluded = condition == "attack" and expected["unavoidable_excluded_from_success"]
    if not decisions:
        issues.append("no_decision")
    else:
        first = decisions[0]["trace"]
        if expected["classification"] == "prepared" and not first["prepared"]:
            issues.append("expected_prepared_structure_missing")
        if (
            expected["classification"] == "absent"
            and first["preparation_status"] != "absent"
        ):
            issues.append("unexpected_secondary_structure")
        if case["missing_key"] and not any(
            r["uses_secondary"]
            and r["key_counterfactual_chain"] is not None
            and r["key_counterfactual_chain"] > r["chain_count"]
            for r in first["public_roots"]
        ):
            issues.append("missing_key_connection_not_proven")
    if len(resolutions) < case["max_resolutions"] and not game_over:
        issues.append("resolution_window_incomplete")
    if excluded:
        if oracle["status"] != "unavoidable":
            issues.append("unavoidable_not_proven")
        if not game_over:
            issues.append("unavoidable_actual_loss_not_observed")
    elif game_over:
        issues.append("non_unavoidable_topout")
    if condition == "attack":
        applied = [p for t in ticks for p in t["injected_attacks"]]
        if applied != case["attack_script"]:
            issues.append("attack_script_not_fully_applied")
        received = sum(t["result"]["dropped"].get("player_0", 0) for t in ticks)
        if received + cancels < sum(p["units"] for p in case["attack_script"]):
            issues.append("attack_boundary_outcome_unobserved")
        if expected["require_cancel"] and cancels < sum(
            p["units"] for p in case["attack_script"]
        ):
            issues.append("required_cancel_missing")
        if expected["require_post_drop_fire"] and not any(
            e["tick"] > d for e in fires for d in drops
        ):
            issues.append("required_post_drop_fire_missing")
        if expected["require_secondary_fire"] and not any(
            r.get("used_secondary") for r in decisions
        ):
            issues.append("required_secondary_fire_missing")
        if expected["preserve_mainline"] and any(
            r.get("mainline_consumed") or r.get("mainline_shape_preserved") is not True
            for r in decisions
        ):
            issues.append("mainline_preservation_failed")
    return {
        "status": "excluded_unavoidable"
        if excluded and not issues
        else "fail"
        if issues
        else "pass",
        "issues": sorted(set(issues)),
        "canceled": cancels,
        "drop_ticks": drops,
        "fire_ticks": [e["tick"] for e in fires],
        "game_over": game_over,
        "maximum_chain_observed_not_threshold": max(
            (e["data"].get("chain_count", 0) for e in resolutions), default=0
        ),
        "search_completeness": [
            {
                "response_status": r["trace"]["response_status"],
                "cutoff": r["trace"]["response_cutoff"],
                "public_geometry": r["trace"]["certainty"],
                "actual_positive_witness": r.get("public_root_confirmed", False),
            }
            for r in decisions
        ],
        "proof_scope": "positive public root plus authoritative receipt/lock/resolution; no exhaustive-search or unknown-hidden-state guarantee",
    }


def run_case(
    config, case, condition, source, *, backend=None, max_ticks=None, evidence_dir=None
):
    policy = make_policy(config, backend=backend)
    controller = RealtimePolicyController(
        policy, config=RealtimeDecisionConfig(**config["controller"])
    )
    match = setup_match(config, case, source)
    initial_hash = match.state_hash()
    ticks, decisions = [], []
    active, resolved = None, 0
    started = time.perf_counter()
    try:
        for _ in range(max_ticks or case["max_ticks"]):
            injected = inject_attacks(match, case, condition)
            ledger = controller.nextgen_scheduler.ledger
            count = len(ledger)
            before = time.perf_counter()
            value = controller.next_input(match, "player_0")
            elapsed = time.perf_counter() - before
            for diagnostic in ledger[count:]:
                request = diagnostic.request
                search = policy.last_context.require("search")
                trace = public_trace(request, case, search)
                receipt = diagnostic.receipt
                counters, quota = (
                    diagnostic.batch.counters,
                    request.control.search_profile,
                )
                active = {
                    "diagnostics": diagnostic.to_dict(),
                    "trace": trace,
                    "decision_seconds": elapsed,
                    "lock": None,
                    "quota_ok": counters.shared_nodes <= quota.shared_quota
                    and counters.template_nodes <= quota.template_quota
                    and counters.response_nodes <= quota.response_quota,
                    "within_deadline": receipt.activation_tick is not None
                    and receipt.activation_tick <= request.execution.timeout_tick,
                    "mainline_consumption_reason": diagnostic.selection.reason,
                }
                decisions.append(active)
            input_tick = match.tick
            result = match.step({"player_0": value})
            record = event_record(result)
            ticks.append(
                {
                    "input_tick": input_tick,
                    "inputs": {"player_0": value.to_json()},
                    "injected_attacks": injected,
                    "result": record,
                }
            )
            for event in record["events"]["player_0"]:
                if event["type"] == "lock" and active is not None:
                    receipt = active["diagnostics"]["receipt"]
                    action = receipt["executed_action"]
                    placement = (
                        action_to_placement(action) if action is not None else None
                    )
                    active["lock"] = event
                    active["lock_matches"] = placement is not None and (
                        event["data"]["axis_x"],
                        event["data"]["rotation"],
                    ) == (placement.axis_x, placement.rotation.name)
                    active["within_deadline"] &= (
                        event["tick"]
                        <= active["diagnostics"]["request"]["execution"]["request_tick"]
                        + config["controller"]["action_deadline_ticks"]
                    )
                    root = next(
                        (
                            r
                            for r in active["trace"]["public_roots"]
                            if r["action"] == action
                        ),
                        {},
                    )
                    active["used_secondary"] = active["lock_matches"] and root.get(
                        "uses_secondary", False
                    )
                    active["mainline_consumed"] = active["lock_matches"] and root.get(
                        "uses_mainline", False
                    )
                elif event["type"] == "resolution_complete":
                    resolved += 1
                    if active is not None:
                        active["resolution"] = event
                        public = match.public_snapshot().own
                        active["remaining_public_board"] = public.to_dict()[
                            "visible_board"
                        ]
                        actual = [
                            [
                                c.PUBLIC_CELL_TO_COLOR[v].name
                                if v is not None
                                else None
                                for v in row
                            ]
                            for row in reversed(public.visible_board)
                        ]
                        root = next(
                            (
                                r
                                for r in active["trace"]["public_roots"]
                                if r["action"]
                                == active["diagnostics"]["receipt"]["executed_action"]
                            ),
                            {},
                        )
                        predicted = root.get("remaining_board_bottom_up", [])
                        drop_here = record["dropped"].get("player_0", 0) > 0
                        active["observed_trace_matches"] = (
                            active.get("lock_matches", False)
                            and root.get("chain_count")
                            == event["data"].get("chain_count", 0)
                            and bool(predicted)
                            and all(
                                actual[y][x] == predicted[y][x]
                                or (
                                    drop_here
                                    and predicted[y][x] == "EMPTY"
                                    and actual[y][x] == "OJAMA"
                                )
                                for y in range(12)
                                for x in range(6)
                            )
                        )
                        active["public_root_confirmed"] = (
                            active["observed_trace_matches"]
                            and root.get("reachable", False)
                            and root.get("valid", False)
                            and root.get("original_cell_flow", {}).get(
                                "consistent", False
                            )
                        )
                        # Credit requires actual lock, chain count, and final colored cells.
                        active["used_secondary"] = (
                            active.get("used_secondary", False)
                            and active["observed_trace_matches"]
                            and event["data"].get("chain_count", 0) > 0
                        )
                        active["mainline_consumed"] = (
                            active.get("mainline_consumed", False)
                            and active["observed_trace_matches"]
                        )
                        active["mainline_shape_preserved"] = (
                            root.get("mainline_shape_preserved")
                            if active["observed_trace_matches"]
                            else None
                        )
            if resolved >= case["max_resolutions"] or match.finished:
                break
        replay = {
            "schema": "puyo.nextgen.attack_response_replay.v1",
            "config": config,
            "case": case,
            "condition": condition,
            "rules": match.replay_rules(),
            "initial_hash": initial_hash,
            "ticks": ticks,
            "final_hash": match.state_hash(),
        }
        verified = replay_run(replay, source)
        oracle = unavoidable_oracle(case)
        result = {
            "schema": "puyo.nextgen.attack_response_run.v1",
            "case_id": case["id"],
            "condition": condition,
            "pattern_id": case["pattern_id"],
            "initial_hash": initial_hash,
            "provider": match.replay_rules()["tsumo"],
            "fixture_sha256": digest(case),
            "attack_script_sha256": digest(case["attack_script"]),
            "policy": policy_identity(policy),
            "configured_timing": config["controller"],
            "elapsed_seconds": time.perf_counter() - started,
            "decisions": decisions,
            "public_placement_history": match.public_placement_history().to_dict(),
            "public_events": match.public_snapshot().to_dict()["events"],
            "replay_verified_hash": verified,
            "replay_sha256": digest(replay),
            "oracle": oracle,
            "scheduler_errors": controller.nextgen_scheduler.errors,
            "assessment": assess(
                case,
                condition,
                decisions,
                ticks,
                match.player_states["player_0"].simulator.game.game_over,
                oracle,
            ),
        }
        return result, replay
    except Exception:
        if evidence_dir is not None:
            write(
                Path(evidence_dir) / "partial.json.gz",
                {
                    "case": case,
                    "condition": condition,
                    "decisions": decisions,
                    "ticks": ticks,
                    "initial_hash": initial_hash,
                    "final_hash": match.state_hash(),
                    "config": config,
                    "rules": match.replay_rules(),
                    "schema": "puyo.nextgen.attack_response_replay.v1",
                },
            )
        raise
    finally:
        # The synchronous controller owns no pool or external mutable workspace.
        policy.reset()


def execute(output, case_id, condition, *, source=None):
    output = Path(output)
    manifest = read(output / "manifest.json")
    if (
        source_identity() != manifest["source_files"]
        or build_identity() != manifest["build"]
    ):
        raise ValueError("source/native/environment drift since manifest freeze")
    if registered_cases() != manifest["fixtures"]:
        raise ValueError("fixture drift")
    case = next(c for c in manifest["fixtures"]["cases"] if c["id"] == case_id)
    path = output / f"{case_id}-{condition}"
    if path.exists():
        raise ValueError("run already exists; never overwrite a failed run")
    path.mkdir()
    try:
        result, replay = run_case(
            manifest["fixtures"],
            case,
            condition,
            source or manifest["source_path"],
            evidence_dir=path,
        )
        if result["policy"] != manifest["actual_policy"]:
            raise ValueError("actual policy differs from manifest")
        result["manifest_sha256"] = digest(manifest)
        write(path / "replay.json.gz", replay)
        write(path / "report.json.gz", result)
        if source_identity() != manifest["source_files"]:
            raise ValueError(
                "source changed during run; evidence retained but not eligible"
            )
        return result
    except Exception as error:
        write(
            path / "error.json", {"type": type(error).__name__, "message": str(error)}
        )
        raise


def summarize(output):
    output = Path(output)
    manifest = read(output / "manifest.json")
    rows, missing = [], []
    for case in manifest["fixtures"]["cases"]:
        pair, reports = {}, {}
        for condition in manifest["fixtures"]["conditions"]:
            path = output / f"{case['id']}-{condition}" / "report.json.gz"
            if not path.exists():
                missing.append(str(path.relative_to(output)))
                continue
            report = read(path)
            if report["manifest_sha256"] != digest(manifest):
                raise ValueError("foreign run in aggregate")
            if (path.parent / "error.json").exists():
                raise ValueError("run with retained error cannot qualify")
            pair[condition] = report["assessment"]
            reports[condition] = report
        if len(reports) == 2 and any(
            reports["attack"][k] != reports["no_attack"][k]
            for k in (
                "initial_hash",
                "fixture_sha256",
                "attack_script_sha256",
                "policy",
                "provider",
            )
        ):
            raise ValueError("paired setup/provider/config mismatch")
        metrics = {
            condition: {
                "replay_verified_hash": report["replay_verified_hash"],
                "locks": [d["lock"] for d in report["decisions"] if d.get("lock")],
                "decisions": len(report["decisions"]),
                "max_decision_seconds": max(
                    (d["decision_seconds"] for d in report["decisions"]), default=None
                ),
                "report": f"{case['id']}-{condition}/report.json.gz",
            }
            for condition, report in reports.items()
        }
        rows.append({"case_id": case["id"], "conditions": pair, "metrics": metrics})
    failures = sum(
        v["status"] == "fail" for row in rows for v in row["conditions"].values()
    )
    result = {
        "schema": "puyo.nextgen.attack_response_summary.v1",
        "status": "incomplete" if missing else "fail" if failures else "pass",
        "failed_conditions": failures,
        "missing": missing,
        "paired": rows,
        "solo_quality_gate": "separate; no battle maximum-chain threshold",
        "latency_scope": "configured ticks and observed CPU time; no measured GUI SLA",
    }
    write(output / "summary.json", result)
    return result


def main():
    def interrupted(signum, frame):
        raise InterruptedError(
            f"gate interrupted by signal {signum}; partial evidence retained"
        )

    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--source", required=True)
    run = commands.add_parser("run")
    run.add_argument("--case", required=True)
    run.add_argument("--condition", choices=("attack", "no_attack"), required=True)
    run.add_argument("--source")
    commands.add_parser("summarize")
    args = parser.parse_args()
    if args.command == "init":
        result = initialize(args.output, args.source)
        print(
            json.dumps(
                {
                    "manifest": str(args.output / "manifest.json"),
                    "sha256": digest(result),
                }
            )
        )
    elif args.command == "run":
        result = execute(args.output, args.case, args.condition, source=args.source)
        print(json.dumps(result["assessment"], indent=2))
    else:
        result = summarize(args.output)
        print(json.dumps(result, indent=2))
        raise SystemExit(result["status"] != "pass")


if __name__ == "__main__":
    main()
