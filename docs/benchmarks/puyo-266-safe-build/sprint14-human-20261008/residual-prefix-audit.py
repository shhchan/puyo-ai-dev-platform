import contextlib
import io
import runpy
import json
from pathlib import Path
from agents.nextgen_survival import _ControlProof, _ProofCutoff, continuation_actions
from agents.nextgen_shared_search import ResponseBudget
from agents.compact_search import transition, find_landing_y
from puyo_env.actions import PLACEMENT_ACTIONS
from src.core.game import GameState

scope = runpy.run_path(str(Path(__file__).with_name("residual-inspect.py")))
with contextlib.redirect_stdout(io.StringIO()):
    req, state, pairs, order, roots, diag, cache, pn = scope["inspect"](128, 38)


def static(st):
    if any(st.occupied_mask & (1 << (y * 6 + 2)) for y in [11, 12, 13]):
        return False
    central = sum(bool(st.occupied_mask & (1 << (y * 6 + 2))) for y in range(13))
    for a in continuation_actions(st):
        p = PLACEMENT_ACTIONS[a]
        y = find_landing_y(st, p)
        dx, dy = GameState.get_sub_puyo_offset(None, p.rotation)
        if (
            central
            + sum(
                x == 2 and cy < 13 for x, cy in [(p.axis_x, y), (p.axis_x + dx, y + dy)]
            )
            <= 11
        ):
            return True
    return False


rows = []
for mode in ("cache_only", "oracle_public_enumeration"):
    for root in (11, 12, 8, 14):
        complete = []
        expanded = 0

        def dfs(st, path):
            global expanded
            if len(path) == 3:
                complete.append((path, st))
                return
            acts = (root,) if not path else continuation_actions(st)
            for a in acts:
                key = st, pairs[len(path)], a
                if mode == "cache_only":
                    if key not in cache:
                        continue
                    tr = cache[key]
                else:
                    expanded += 1
                    tr = transition(*key)
                if tr.valid and not tr.game_over:
                    dfs(tr.state, path + (a,))

        dfs(state, ())
        viable = []
        for path, end in complete:
            if not static(end):
                continue
            proof = _ControlProof(ResponseBudget(256), 0)
            current = state
            ok = True
            try:
                for depth, a in enumerate(path):
                    if depth and not proof.reachable(current, pairs[depth], a):
                        ok = False
                        break
                    current = transition(current, pairs[depth], a).state
                terminal = proof.terminal(current, pairs[0]) if ok else None
                if terminal is not None:
                    viable.append(
                        {
                            "path": path,
                            "proof_nodes": proof.budget.nodes,
                            "terminal": terminal,
                        }
                    )
            except _ProofCutoff:
                pass
        result = {
            "mode": mode,
            "root": root,
            "probe_nodes": pn,
            "cache_entries": len(cache),
            "offline_expansions": expanded,
            "complete_paths": len(complete),
            "static_terminal_paths": sum(static(st) for _, st in complete),
            "certificates": sorted(viable, key=lambda x: x["proof_nodes"]),
        }
        rows.append(result)
        print({**result, "certificates": result["certificates"][:2]})
Path(__file__).with_name("residual-prefix-audit.json").open("w").write(
    json.dumps(rows, indent=2) + "\n"
)
