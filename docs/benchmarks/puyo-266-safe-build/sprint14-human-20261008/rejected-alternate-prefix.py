import runpy
import json
from dataclasses import replace
from pathlib import Path
from agents.compact_search import find_landing_y, transition
from puyo_env.actions import PLACEMENT_ACTIONS
from src.core.game import GameState

scope = runpy.run_path(str(Path(__file__).with_name("rejected-probe-cache.py")))
module = scope["module"]
cases = scope["cases"]
ResponseBudget = scope["ResponseBudget"]
c = scope["c"]
selection = scope["selection"]
raw = scope["read"](
    scope["root"] / "sprint14-human-20261008/inference-v1-human/report.json.gz"
)
for a in raw["attempts"]:
    if a["record"]["outcome"] != "activated":
        continue
    wire = a["payload"]["nextgen"]
    req = c.NextgenRequest.from_dict(wire["request"])
    if req.identity.decision_id >= 26:
        scope["add"](f"current-human/{req.identity.decision_id}", wire, req.inference)


def static_terminal(state):
    if any(state.occupied_mask & (1 << (y * 6 + 2)) for y in [11, 12, 13]):
        return False
    count = sum(bool(state.occupied_mask & (1 << (y * 6 + 2))) for y in range(13))
    for action in module.continuation_actions(state):
        pose = PLACEMENT_ACTIONS[action]
        y = find_landing_y(state, pose)
        dx, dy = GameState.get_sub_puyo_offset(None, pose.rotation)
        if (
            count
            + sum(
                x == 2 and cy < 13
                for x, cy in [(pose.axis_x, y), (pose.axis_x + dx, y + dy)]
            )
            <= 11
        ):
            return True
    return False


def refine(req, state, roots, diag, budget, order, cache, batch):
    proof = module._ControlProof(budget, budget.nodes - diag["nodes"])
    pairs = tuple(
        tuple(c.PUBLIC_CELL_TO_COLOR[v] for v in pair)
        for pair in req.public.own.known_pieces
    )
    ordered = list(dict.fromkeys((*order, *roots)))
    ordered.sort(
        key=lambda a: (
            0
            if a in roots and roots[a].status == "witness" and not roots[a].root_chain
            else 1
        )
    )
    failures = []
    trials = []
    certified = None
    newpath = None
    cutoff = False

    def step(s, depth, a):
        key = s, pairs[depth], a
        if key not in cache:
            proof.charge("placement")
            cache[key] = transition(*key)
        return cache[key]

    def paths(path):
        current = state
        states = []
        for depth, a in enumerate(path):
            if (
                len(path) < len(pairs)
                and depth
                and not proof.reachable(current, pairs[depth], a)
            ):
                return
            states.append(current)
            r = step(current, depth, a)
            if not r.valid or r.game_over:
                return
            current = r.state

        def extend(curr, plan, st):
            if len(plan) == len(pairs):
                yield plan, st, curr
                return
            for a in module.continuation_actions(curr):
                r = step(curr, len(plan), a)
                if r.valid and not r.game_over:
                    yield from extend(r.state, plan + (a,), st + (curr,))

        yield from extend(current, tuple(path), tuple(states))

    for action in ordered:
        root = roots.get(action)
        if root is None or root.status != "witness":
            continue
        options = []
        if not root.root_chain:
            for candidate in batch.candidates:
                if (
                    candidate.root_action != action
                    or "fire_main" not in candidate.tactics
                    or not 1 < len(candidate.plan) <= len(pairs)
                ):
                    continue
                if any(
                    s.provenance != "public_known"
                    or s.piece != req.public.own.known_pieces[d]
                    for d, s in enumerate(candidate.plan)
                ):
                    continue
                values = {e.name: e.evidence.value for e in candidate.evidence}
                options.append(
                    (
                        -(values.get("chain_count") or 0),
                        -(values.get("score") or 0),
                        len(candidate.plan),
                        tuple(s.action for s in candidate.plan),
                    )
                )
        attempts = []
        if options:
            attempts.append(min(options)[3])
        if root.witness not in attempts:
            attempts.append(root.witness)
        try:
            for path in attempts:
                for expanded, states, last in paths(path):
                    if not static_terminal(last):
                        trials.append(
                            {
                                "root": action,
                                "plan": expanded,
                                "status": "unknown_static_terminal",
                            }
                        )
                        continue
                    if any(
                        not proof.reachable(s, pairs[d], a)
                        for d, (s, a) in enumerate(zip(states, expanded))
                        if d
                    ):
                        trials.append(
                            {
                                "root": action,
                                "plan": expanded,
                                "status": "unknown_control",
                            }
                        )
                        continue
                    terminal = proof.terminal(last, pairs[0])
                    if terminal is None:
                        trials.append(
                            {
                                "root": action,
                                "plan": expanded,
                                "status": "unknown_terminal",
                            }
                        )
                        continue
                    certified = action
                    newpath = expanded
                    trials.append(
                        {
                            "root": action,
                            "plan": expanded,
                            "status": "certified",
                            "terminal": terminal,
                        }
                    )
                    break
                if certified is not None:
                    break
            if certified is not None:
                break
        except module._ProofCutoff:
            cutoff = True
            trials.append({"root": action, "status": "unknown_cutoff"})
            break
        failures.append(action)
    refined = dict(roots)
    if certified is not None:
        for a in failures:
            refined[a] = replace(roots[a], status="unknown", witness=())
        refined[certified] = replace(roots[certified], witness=newpath)
    details = {
        "certified": certified,
        "nodes": budget.nodes,
        "charged": proof.charged,
        "trials": trials,
        "cutoff": cutoff,
    }
    return (
        refined,
        details,
        diag.get("active", False) or (certified is not None and bool(failures)),
    )


rows = []
for name, (req, batch, selected, order) in cases.items():
    state = module.inferred_state(req, scope["_public_state"](req)[0])
    budget = ResponseBudget(256)
    cache = {}
    roots, diag = scope["cached_probe"](
        req, state, scope["legal_action_indices"](state), budget, transition_cache=cache
    )
    refined, details, active = refine(
        req, state, roots, diag, budget, order, cache, batch
    )
    action, reason = selection(batch, selected, refined, active)
    row = {
        "case": name,
        "selected": action,
        "reason": reason,
        "probe_nodes": diag["nodes"],
        **details,
    }
    rows.append(row)
    if name in [
        "gtr-123/28",
        "human-127/26",
        "current-123/30",
        "current-128/38",
    ] or name.startswith(("old-132/3", "old-135/3", "old-144/3", "current-human")):
        print(
            "ALTERNATE",
            name,
            action,
            budget.nodes,
            details["certified"],
            details["cutoff"],
        )
Path("/tmp/puyo266-rejected-alternate-prefix-result.json").write_text(
    json.dumps(rows, indent=2) + "\n"
)
