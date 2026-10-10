"""Evaluation-only solo runtime and public reference boundary for PUYO-266.

No provider, match, simulator or private board is accepted by reference_input.
Settled hidden rows are usable only when the shared public lifecycle observer
has deduced them. Unknown deductions never become empty cells.
"""

from __future__ import annotations

import numpy as np

from agents import nextgen_contracts as c
from agents.compact_search import CompactSearchState, legal_action_indices, transition
from agents.deep_chain_builder import DeepChainBuilderPolicy
from agents.nextgen_survival import _ControlProof, _ProofCutoff
from eval.nextgen_safe_build_diagnostic import make_policy as legacy_make_policy
from puyo_env.actions import action_to_placement
from puyo_env.obs import BOARD_COLOR_CHANNELS, NORMAL_COLOR_CHANNELS
from puyo_env.realtime_ai import (
    REALTIME_ACTION_CONTRACT_VERSION,
    REALTIME_OBSERVATION_SCHEMA_VERSION,
    RealtimeDecisionConfig,
    RealtimePolicyController,
    realtime_reachable_action_mask,
)
from puyo_env.realtime_versus import RealtimeVersusMatch
from src.core.realtime import TickInput

POLICIES = ("nextgen_tactic_manager", "deep_chain_builder")
POLICY_SEED = 55  # Independent of pattern ID; repeats differ only in process identity.
PROOF_NODE_LIMIT = 100_000  # Offline audit only; never a policy search allowance.


class SingleQualityMatch(RealtimeVersusMatch):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        opponent = self.player_states["player_1"].simulator.game
        opponent.state = "countdown"
        opponent.countdown_time_left = 1e9
        self.public_snapshot()
        self.public_board_inference()

    def schedule_attack(self, *args, **kwargs):
        return None


def make_policy(kind, *, backend="native", smoke=False):
    if kind == "deep_chain_builder":
        return DeepChainBuilderPolicy(profile="smoke" if smoke else "reference", backend=backend)
    if kind != "nextgen_tactic_manager":
        raise ValueError("unknown policy")
    if backend != "native":
        # Test-only injection, retaining the production catalog/selector defaults.
        from pathlib import Path

        from agents.nextgen_profiles import nextgen_search_settings
        from agents.nextgen_tactic_manager import NextgenTacticManagerPolicy
        from src.ui.launcher_settings import resolve_nextgen_catalog

        catalog, _ = resolve_nextgen_catalog(
            catalog_path="train/config/nextgen_templates.yaml", templates="gtr",
            mode="argmax", temperature=.1, commit_turns=14,
            repo_root=Path(__file__).resolve().parents[1],
        )
        profile, search = nextgen_search_settings(
            "nextgen_smoke" if smoke else "nextgen_safe_build", seed=POLICY_SEED)
        return NextgenTacticManagerPolicy(catalog=catalog, seed=POLICY_SEED,
                                         profile=profile, search_config=search, backend=backend)
    return legacy_make_policy("nextgen", POLICY_SEED,
                              "nextgen_smoke" if smoke else "nextgen_safe_build")


def public_board(public, inference):
    if (inference.status != "known" or inference.player_id != 0
            or inference.visible_digest != c.semantic_digest(public.visible_board)):
        raise ValueError("public board deduction unavailable or mismatched")
    visible = tuple(reversed(public.visible_board))[:12]
    if len(visible) != 12 or any(v is None for row in visible for v in row):
        raise ValueError("unobserved public visible cells")
    return (*visible, *inference.hidden_rows)


def public_state(public, inference):
    board = public_board(public, inference)
    planes = [0] * 6
    for y, row in enumerate(board):
        for x, value in enumerate(row):
            if value:
                planes[BOARD_COLOR_CHANNELS.index(c.PUBLIC_CELL_TO_COLOR[value])] |= 1 << (y * 6 + x)
    return CompactSearchState(tuple(planes), all_clear_bonus_pending=public.all_clear_bonus_pending)


def reference_input(public, inference, mask, *, tick, score, last_chain_end_score,
                    last_chain_score_delta):
    """Build an allowlisted reference observation solely from public values."""
    if len(public.known_pieces) != 3 or inference.observed_tick != tick:
        raise ValueError("public queue/tick boundary mismatch")
    board = public_board(public, inference)
    encoded = np.zeros((6, 13, 6), dtype=np.float32)
    ghost = np.zeros((6, 6), dtype=np.float32)
    for y, row in enumerate(board):
        for x, value in enumerate(row):
            if value:
                channel = BOARD_COLOR_CHANNELS.index(c.PUBLIC_CELL_TO_COLOR[value])
                if y == 13:
                    ghost[channel, x] = 1
                else:
                    encoded[channel, 12 - y, x] = 1
    pairs = np.zeros((3, 2, 5), dtype=np.float32)
    for i, pair in enumerate(public.known_pieces):
        for j, value in enumerate(pair):
            pairs[i, j, NORMAL_COLOR_CHANNELS.index(c.PUBLIC_CELL_TO_COLOR[value])] = 1
    observation = {"board": encoded, "ghost_row": ghost, "next_pairs": pairs,
                   "schema_version": REALTIME_OBSERVATION_SCHEMA_VERSION}
    info = {
        "action_mask": tuple(mask), "action_mask_source": "reachable_planner",
        "action_contract_version": REALTIME_ACTION_CONTRACT_VERSION,
        "score": score, "step_count": tick, "max_ticks": 30000,
        "last_chain_end_score": last_chain_end_score,
        "last_chain_score_delta": last_chain_score_delta,
        "all_clear_bonus_pending": public.all_clear_bonus_pending, "game_over": False,
    }
    return observation, info


def checkpoint(chains, tick, state_hash):
    return {"placements": len(chains), "tick": tick, "state_hash": state_hash,
            "max_chain": max(chains, default=0),
            "small_clears": sum(0 < value < 10 for value in chains)}


def _decision_payload(policy, kind):
    context = policy.last_context
    if kind == "nextgen_tactic_manager":
        search = context.require("search")
        return {"action": context.require("candidate").root_action,
                "phase": context.require("phase").diagnostics(),
                "search": search.diagnostics, "counters": search.batch.counters.to_dict(),
                "selection": context.require("selection").to_dict(),
                "root_evidence": [v.to_dict() for v in search.shared_result.ranked_roots]}
    diagnostics = policy.tactical_diagnostics
    return {"action": context.require("selected_action"),
            "search": diagnostics["search"], "counters": diagnostics["search"]["counters"],
            "selection": diagnostics["selection_evidence"],
            "fallback": diagnostics["fallback"], "phase": None}


def measure(match, policy, kind, *, placements=46, max_ticks=30000, progress=None):
    """Run the unchanged policy through actual tick controls and resolutions."""
    controller = RealtimePolicyController(policy, config=RealtimeDecisionConfig(
        latency_mode="configured", use_reachable_action_mask=True))
    rows, receipts, ticks, events, chains = [], [], [], [], []
    seen_context, seen_receipt = None, None
    forty = None
    initial_hash = match.state_hash()

    def snapshot():
        runtime = controller.nextgen_scheduler
        game_over = match.player_states["player_0"].simulator.game.game_over
        return {
            "policy": kind, "match_rules": match.replay_rules(), "initial_hash": initial_hash,
            "ticks": ticks, "events": events, "chains": chains, "rows": rows, "receipts": receipts,
            "ledger": [d.to_dict() for d in runtime.ledger] if runtime else [],
            "errors": list(runtime.errors) if runtime else [],
            "controller": controller.diagnostics.to_dict(), "game_over": game_over,
            "termination": "game_over" if game_over else "placements" if len(chains) == placements else "tick_limit",
            "checkpoint40": forty, "final": checkpoint(chains, match.tick, match.state_hash()),
        }

    if progress is not None:
        progress(snapshot())
    for _ in range(max_ticks):
        game = match.player_states["player_0"].simulator.game
        public = match.public_snapshot().own
        inference = match.public_board_inference()
        mask = None
        kwargs = {}
        # Only the reference adapter is constructed here; nextgen's scheduler
        # supplies its established public request and public inference sidecar.
        if kind == "deep_chain_builder" and public.phase == "control" and not match.ending:
            mask = tuple(bool(v) for v in realtime_reachable_action_mask(
                match.player_states["player_0"].simulator))
            observation, info = reference_input(
                public, inference, mask, tick=match.tick, score=game.score,
                last_chain_end_score=game.last_chain_end_score,
                last_chain_score_delta=game.last_chain_score_delta)
            kwargs = {"observation": observation, "info": info}
        value = {} if match.ending else {
            "player_0": controller.next_input(match, "player_0", **kwargs)}
        context = policy.last_context
        if context is not None and context is not seen_context:
            seen_context = context
            if kind == "nextgen_tactic_manager":
                request = context.require("request")
                mask = request.execution.reachable_mask
                if request.public.own != public:
                    raise ValueError("decision public snapshot differs from observer")
            row = {"tick": match.tick, "seconds": context.trace.elapsed_seconds,
                   "public": public.to_dict(), "inference": inference.to_dict(),
                   "reachable_mask": list(mask), **_decision_payload(policy, kind)}
            rows.append(row)
        receipt = controller.diagnostics.last_decision
        if receipt is not None and receipt is not seen_receipt:
            seen_receipt = receipt
            if receipt.activation_tick is not None:
                receipts.append(receipt.to_json())
        result = match.step(value)
        ticks.append({"tick": result.tick, "inputs": {k: v.to_json() for k, v in value.items()},
                      "state_hash": match.state_hash()})
        for event in result.player_results["player_0"].events:
            if event.type in ("lock", "resolution_complete"):
                events.append({"type": event.type, "tick": event.tick, **event.data})
            if event.type == "resolution_complete":
                chains.append(event.data["chain_count"])
                if len(chains) == 40:
                    forty = checkpoint(chains, match.tick, ticks[-1]["state_hash"])
                if progress is not None:
                    progress(snapshot())
        if len(chains) >= placements or match.finished:
            break
    return snapshot()


def replay_audit(raw, match):
    """No policies run; every saved input, actual lock and state hash is checked."""
    if match.state_hash() != raw["initial_hash"] or match.replay_rules() != raw["match_rules"]:
        raise ValueError("replay initial identity mismatch")
    decisions = {row["tick"]: row for row in raw["rows"]}
    if len(decisions) != len(raw["rows"]):
        raise ValueError("duplicate decision tick")
    activations = {r["activation_tick"]: r for r in raw["receipts"]}
    if len(activations) != len(raw["receipts"]):
        raise ValueError("duplicate activation tick")
    actual_events, records = [], []
    active = None
    for saved in raw["ticks"]:
        if match.tick in decisions:
            row = decisions[match.tick]
            if (match.public_snapshot().own.to_dict() != row["public"]
                    or match.public_board_inference().to_dict() != row["inference"]):
                raise ValueError("public decision input differs from replay")
        if match.tick in activations:
            receipt = activations[match.tick]
            row = decisions.get(receipt["request_tick"])
            if row is None:
                raise ValueError("activation without decision")
            active = {"request_tick": receipt["request_tick"], "action": row["action"],
                      "executed_action": receipt["executed_action"],
                      "outcome": receipt["outcome"], "lock": None, "resolution": None}
            records.append(active)
        result = match.step({key: TickInput.from_names(**value)
                             for key, value in saved["inputs"].items()})
        if result.tick != saved["tick"] or match.state_hash() != saved["state_hash"]:
            raise ValueError("replay tick/hash mismatch")
        for event in result.player_results["player_0"].events:
            if event.type in ("lock", "resolution_complete"):
                value = {"type": event.type, "tick": event.tick, **event.data}
                actual_events.append(value)
                if active is None:
                    raise ValueError("unattributed replay lock/resolution")
                key = "lock" if event.type == "lock" else "resolution"
                if active[key] is not None:
                    raise ValueError("duplicate replay lock/resolution")
                active[key] = value
    if actual_events != raw["events"] or match.state_hash() != raw["final"]["state_hash"]:
        raise ValueError("replay final/events mismatch")
    mismatches = []
    for record in records:
        lock = record["lock"]
        expected = action_to_placement(record["action"])
        record["lock_matches"] = bool(lock and (lock["axis_x"], lock["rotation"]) == (
            expected.axis_x, expected.rotation.name))
        if lock and not record["lock_matches"]:
            mismatches.append(record["request_tick"])
    return {"all_tick_hashes_match": True, "records": records,
            "lock_mismatches": mismatches,
            "unlocked_requests": [r["request_tick"] for r in records if r["lock"] is None]}


class _AuditProof(_ControlProof):
    """Independent offline proof budget; not borrowed from runtime quotas."""
    def __init__(self):
        super().__init__(None, 0)
        self.nodes = 0

    def charge(self, kind):
        if self.nodes >= PROOF_NODE_LIMIT:
            raise _ProofCutoff
        self.nodes += 1
        self.charged[kind] += 1


def classify_small_clear(row, chain_count):
    """Only accept an explicit public proof; finite witness != long-term safety."""
    public = c.PublicPlayerState.from_dict(row["public"])
    inference = c.PublicBoardInference.from_dict(row["inference"])
    result = {"status": "unknown", "reason": "public_proof_unavailable",
              "chain_count": chain_count, "request_tick": row["tick"], "proof_nodes": 0}
    try:
        state = public_state(public, inference)
    except ValueError:
        return result
    pairs = tuple(tuple(c.PUBLIC_CELL_TO_COLOR[v] for v in pair) for pair in public.known_pieces)
    roots = [(a, transition(state, pairs[0], a)) for a in legal_action_indices(state)
             if row["reachable_mask"][a]]
    chosen = next((v for a, v in roots if a == row["action"]), None)
    if chosen is None or not chosen.valid or chosen.chain_count != chain_count or chosen.game_over:
        return {**result, "reason": "selected_public_prediction_mismatch_or_fatal"}
    quiet = [(a, v) for a, v in roots if v.valid and v.chain_count == 0 and not v.game_over]
    target = [(a, v) for a, v in roots if v.valid and v.chain_count >= 10 and not v.game_over]
    if not quiet and not target:
        return {**result, "status": "necessary_survival", "reason": "all_reachable_non_small_roots_fatal",
                "root_results": [{"action": a, "chain": v.chain_count, "fatal": v.game_over,
                                  "valid": v.valid} for a, v in roots]}
    proof = _AuditProof()

    def visit(current, path):
        if len(path) == len(pairs):
            terminal = proof.terminal(current, pairs[0])
            return (path, terminal) if terminal is not None else None
        for action in legal_action_indices(current):
            proof.charge("placement")
            value = transition(current, pairs[len(path)], action)
            if value.valid and not value.game_over and proof.reachable(current, pairs[len(path)], action):
                found = visit(value.state, path + [action])
                if found:
                    return found
        return None

    try:
        for action, value in (*target, *quiet):
            found = visit(value.state, [action])
            if found:
                return {**result, "status": "unjustified", "reason": "public_non_small_continuation",
                        "witness": found[0], "terminal_action": found[1], "proof_nodes": proof.nodes,
                        "scope": "known three pairs plus one color-independent geometric placement"}
    except _ProofCutoff:
        return {**result, "reason": "offline_proof_cutoff", "proof_nodes": proof.nodes}
    return {**result, "reason": "non_small_continuation_not_certified", "proof_nodes": proof.nodes}


def semantic_payload(raw):
    """Wall time and profiling fields cannot alter repeat matching."""
    return {"match_rules": raw["match_rules"], "ticks": raw["ticks"], "events": raw["events"], "chains": raw["chains"],
            "decisions": [{k: r[k] for k in ("tick", "public", "inference", "reachable_mask", "action", "phase")}
                          for r in raw["rows"]],
            "receipts": [{k: r[k] for k in ("request_tick", "activation_tick", "requested_action",
                                           "executed_action", "outcome", "fallback", "reason")}
                         for r in raw["receipts"]],
            "batches": [d["selection"]["batch_digest"] for d in raw["ledger"]],
            "reference_search": [r["search"].get("deterministic_digest") for r in raw["rows"]
                                 if raw["policy"] == "deep_chain_builder"],
            "quality_classifications": [raw.get("small_clear_classifications"), raw.get("suffocation_classification")],
            "final": raw["final"], "checkpoint40": raw["checkpoint40"]}
