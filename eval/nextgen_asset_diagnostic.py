"""Offline provenance audit of completed template cells and public fire plans.

This adds no policy search, ranking, or quota. Missing public witnesses are
unknown, not proof that a surviving template is useless. Coordinates are bottom
up. Cell identities follow gravity: equal colors at equal coordinates do not
prove that an original template cell survived.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from agents import nextgen_contracts as c
from agents.compact_search import legal_action_indices, transition
from agents.nextgen_shared_search import _pairs, _public_state
from agents.template_catalog import compile_selected_template, load_template_catalog
from src.core.constants import Direction


def _gravity(tokens):
    result = {}
    for x in range(6):
        column = sorted((y, token) for (cx, y), token in tokens.items() if cx == x and y < 13)
        result.update({(x, y): token for y, (_, token) in enumerate(column)})
        if (x, 13) in tokens:
            result[x, 13] = tokens[x, 13]
    return result


def trace_placement(state, pair, action, assets):
    """Return transition, surviving origin->position map and consumed origins.

    Asset keys are arbitrary stable IDs. Every original cell must be occupied.
    Permanent row 14 follows compact_search's explicit gravity convention.
    """
    if len(set(assets.values())) != len(assets):
        raise ValueError("asset cells overlap")
    if any(not state.occupied_mask & (1 << (y * 6 + x)) for x, y in assets.values()):
        raise ValueError("asset cell is absent")
    result = transition(state, pair, action, capture_visuals=True)
    if not result.valid:
        raise ValueError("cannot trace invalid placement")
    at = {position: origin for origin, position in assets.items()}
    tokens = {(x, y): at.get((x, y)) for y in range(14) for x in range(6)
              if state.occupied_mask & (1 << (y * 6 + x))}
    x, y = result.action.axis_x, result.axis_y
    dx, dy = {Direction.UP: (0, 1), Direction.RIGHT: (1, 0),
              Direction.DOWN: (0, -1), Direction.LEFT: (-1, 0)}[result.action.rotation]
    tokens[x, y] = None
    tokens[x + dx, y + dy] = None
    tokens = _gravity(tokens)
    consumed = {}
    for step in result.chains:
        for position in step.vanished | step.garbage_cleared:
            origin = tokens.pop(position)
            if origin is not None:
                consumed[origin] = step.chain_index
        tokens = _gravity(tokens)
    occupied = sum(1 << (y * 6 + x) for x, y in tokens)
    if occupied != result.state.occupied_mask:
        raise ValueError("cell trace differs from compact transition")
    remaining = {origin: position for position, origin in tokens.items() if origin is not None}
    return result, remaining, consumed


def public_plan(state, pairs, path, assets):
    """Replay only the supplied public prefix; never substitute sampled pairs."""
    if not path or len(path) > len(pairs):
        raise ValueError("plan exceeds public pieces")
    remaining, consumed, chains = dict(assets), {}, []
    score = 0
    for depth, action in enumerate(path, 1):
        result, remaining, removed = trace_placement(state, pairs[depth - 1], action, remaining)
        consumed.update({key: [depth, chain] for key, chain in removed.items()})
        chains.append(result.chain_count)
        score += result.score_delta
        state = result.state
        if result.game_over:
            break
    return {
        "path": list(path), "chains": chains, "max_chain": max(chains),
        "score": score, "game_over": state.game_over,
        "used_cells": sorted(consumed), "used_count": len(consumed),
        "consumed_at": consumed,
        "remaining_cells": {key: list(value) for key, value in sorted(remaining.items())},
        "remaining_count": len(remaining), "remaining_board_cells": state.cell_count,
        "remaining_heights": list(state.column_heights),
        "board_planes": list(state.planes),
    }


def audit_run(raw, catalog, *, alternative_quota=2048):
    """Audit receipt-backed solo diagnostics; refuse ambiguous row alignment.

    All reachable roots and existing batch public plans are compared at actual
    fires. The extra transitions belong to this offline diagnostic budget.
    """
    rows, ledger, chains = raw["rows"], raw["ledger"], raw["chains"]
    if not len(rows) == len(ledger) == len(chains):
        raise ValueError("requires one settled receipt per diagnostic row")
    assets, phases, fires, gaps = {}, [], [], []
    previous_state = None
    known_phases = set()
    for index, (row, wire, actual_chain) in enumerate(zip(rows, ledger, chains), 1):
        diagnostic = c.Diagnostics.from_dict(wire)
        request, receipt = diagnostic.request, diagnostic.receipt
        if receipt.outcome != "activated" or receipt.executed_action != row["action"]:
            raise ValueError("receipt did not execute the diagnosed action")
        state, complete = _public_state(request)
        pairs = _pairs(request.public.own.known_pieces)
        # Unknown hidden cells stay unknown. Compare only the visible mask.
        visible_mask = sum(1 << (y * 6 + x) for y, line in enumerate(reversed(request.public.own.visible_board))
                           for x, cell in enumerate(line) if cell is not None)
        if previous_state is not None and any((a ^ b) & visible_mask for a, b in zip(state.planes, previous_state.planes)):
            gaps.append({"placement": index, "reason": "public_board_discontinuity"})
            assets = {}
        phase = row["phase"]
        phase_id = phase["phase_id"]
        if phase["exit_reason"] == "completed" and phase_id not in known_phases:
            key = list(phase["selected_key"])
            key[3] = tuple(tuple(v) for v in key[3])
            template = compile_selected_template(catalog, tuple(key))
            _, matches = template.evaluate(state, state)
            if not matches:
                raise ValueError("completed template does not match public board")
            positions = set(assets.values())
            added = {}
            for x, y, _ in template.required_cells:
                if (x, y) not in positions:
                    added[f"{phase_id}:{x}:{y}"] = (x, y)
            assets.update(added)
            phases.append({"phase_id": phase_id, "placement": index,
                           "template_key": key, "cells": added,
                           "previous_asset_overlap": len(template.required_cells) - len(added)})
            known_phases.add(phase_id)
        result, remaining, consumed = trace_placement(state, pairs[0], row["action"], assets)
        if result.chain_count != actual_chain:
            raise ValueError("public predicted chain differs from actual resolution")
        if actual_chain:
            selected = public_plan(state, pairs, (row["action"],), assets)
            candidates = {candidate.candidate_id: candidate for candidate in diagnostic.batch.candidates}
            tactic_ranks = {t.tactic_id: {cid: rank for rank, cid in enumerate(t.candidate_ids)}
                            for t in diagnostic.batch.tactics}
            paths = {(action,): [] for action in legal_action_indices(state)
                     if request.execution.reachable_mask[action]}
            for candidate in candidates.values():
                if all(step.provenance == "public_known" for step in candidate.plan) and candidate.root_reachable:
                    paths.setdefault(tuple(step.action for step in candidate.plan), []).append(candidate.candidate_id)
            alternatives, used = [], 0
            for path, ids in sorted(paths.items(), key=lambda item: (len(item[0]), item[0])):
                if used + len(path) > alternative_quota:
                    break
                used += len(path)
                value = public_plan(state, pairs, path, assets)
                value["candidate_ids"] = ids
                value["tactic_ranks"] = {t: min(r[cid] for cid in ids if cid in r)
                                        for t, r in tactic_ranks.items() if any(cid in r for cid in ids)}
                alternatives.append(value)
            using = [a for a in alternatives if a["max_chain"] >= 10 and not a["game_over"]
                     and a["used_count"] > selected["used_count"]]
            fires.append({
                "placement": index, "public_digest": request.public.digest,
                "public_board_complete": complete, "phase": phase,
                "selection": diagnostic.selection.to_dict(), "receipt": receipt.to_dict(),
                "selected": selected, "asset_status": "no_tracked_asset" if not assets
                else "unused" if not consumed else "fully_used" if not remaining else "partly_used",
                "public_asset_using_alternatives": len(using),
                "alternative_status": "public_estimate" if not complete else "visible_exact",
                "alternatives": alternatives, "offline_nodes": used,
                "offline_quota": alternative_quota, "offline_cutoff": len(alternatives) != len(paths),
                "future_mainline": "unknown beyond recorded public plans; sampled evidence is not a promise",
                "shared_rank": row["shared_rank"], "root_evidence": row["root_evidence"],
                "policy_counters": row["counters"], "decision_seconds": row["seconds"],
            })
        assets, previous_state = remaining, result.state
    return {"schema": "puyo.nextgen.asset_audit.v1", "seed": raw["seed"],
            "source_semantic_digest": raw["semantic_digest"], "phases": phases,
            "fires": fires, "gaps": gaps, "remaining_assets": assets,
            "quality_status": "diagnostic only; unused is not evidence of harmful residue"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    catalog = load_template_catalog(Path(__file__).resolve().parents[1] / "train/config/nextgen_templates.yaml")
    for path in args.inputs:
        content = path.read_bytes()
        raw = json.loads(gzip.decompress(content) if path.suffix == ".gz" else content)
        result = audit_run(raw, catalog)
        result["input"] = {"path": str(path), "sha256": hashlib.sha256(content).hexdigest()}
        destination = args.output / (path.name.removesuffix(".json.gz") + ".asset.json.gz")
        destination.write_bytes(gzip.compress(json.dumps(result, allow_nan=False).encode(), mtime=0))
        print(path.name, [(f["placement"], f["selected"]["max_chain"], f["asset_status"],
                           f["selected"]["used_count"], f["selected"]["remaining_count"])
                          for f in result["fires"]], flush=True)


if __name__ == "__main__":
    main()
