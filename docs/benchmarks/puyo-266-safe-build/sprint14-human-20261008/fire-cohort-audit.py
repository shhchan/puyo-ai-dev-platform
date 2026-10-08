"""Compare frozen no-threat matches; full planes are offline audit only."""

import argparse
import gzip
import json
from pathlib import Path

from agents import nextgen_contracts as c
from agents.nextgen_shared_search import _public_state
from agents.nextgen_survival import inferred_state


def read(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def audit(root, baseline):
    summary = []
    for seed in (55, 123, 124, 126):
        raw, old = (read(folder / f"gtr-{seed}.json.gz") for folder in (root, baseline))
        locks = read(root / f"gtr-{seed}.locks.json.gz")
        records = {row["decision"]: row for row in locks["records"]}
        hidden, quota, changes, clears = [], [], [], []
        for index, (wire, row) in enumerate(zip(raw["ledger"], raw["rows"]), 1):
            req = c.NextgenRequest.from_dict(wire["request"])
            state = inferred_state(req, _public_state(req)[0])
            actual = records[index]
            hidden.append(
                {
                    "decision": index,
                    "known": state is not None,
                    "offline_planes_match": state is not None
                    and state.planes == tuple(actual["offline_full_planes"]),
                }
            )
            counters = wire["batch"]["counters"]
            if (
                counters["response_nodes"] > 256
                or counters["shared_nodes"] > 600000
                or counters["template_nodes"] > 128
                or row["search"]["survival"]["nodes"] > 128
            ):
                quota.append(index)
            if (
                index <= len(old["rows"])
                and row["action"] != old["rows"][index - 1]["action"]
            ):
                changes.append(
                    {
                        "decision": index,
                        "before": old["rows"][index - 1]["action"],
                        "after": row["action"],
                    }
                )
            if raw["chains"][index - 1]:
                clears.append(
                    {
                        "decision": index,
                        "action": row["action"],
                        "chain": raw["chains"][index - 1],
                        "tactic": wire["selection"]["selected_tactic_id"],
                        "reason": wire["selection"]["reason"],
                        "remaining_locks": raw["placements"] - index,
                        "actual_root_matches": actual["lock_matches_requested_root"],
                        "offline_predicted_chain": actual[
                            "offline_full_board_predicted_chain"
                        ],
                    }
                )
        first = changes[0]["decision"] if changes else None
        compared = min(
            len(old["ledger"]), (first if first is not None else len(raw["ledger"]))
        )
        summary.append(
            {
                "seed": seed,
                "source": raw["source"]["commit"],
                "source_changed": raw["source_changed"],
                "before": {
                    key: old[key]
                    for key in (
                        "max_chain",
                        "premature",
                        "game_over",
                        "placements",
                        "decision_seconds",
                    )
                },
                "after": {
                    key: raw[key]
                    for key in (
                        "max_chain",
                        "premature",
                        "game_over",
                        "placements",
                        "decision_seconds",
                    )
                },
                "same_native_build": old["build"] == raw["build"],
                "same_search_config": old["search_config"] == raw["search_config"],
                "same_profile": old["profile"] == raw["profile"],
                "same_initial_public_input": old["ledger"][0]["request"]["public"]
                == raw["ledger"][0]["request"]["public"],
                "public_equal_through_first_changed_action": all(
                    old["ledger"][i]["request"]["public"]
                    == raw["ledger"][i]["request"]["public"]
                    for i in range(compared)
                ),
                "compared_prefix_decisions": compared,
                "first_action_change": first,
                "changes": changes,
                "clears": clears,
                "inference": hidden,
                "quota_violations": quota,
                "lock_mismatches": locks["lock_mismatch_decisions"],
                "lock_count": len(locks["records"]),
                "final_hash_replayed": locks["final_hash_matches"],
                "same_final_hash": old["semantic"]["final_hash"]
                == raw["semantic"]["final_hash"],
            }
        )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cohort", type=Path)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rows = audit(args.cohort, args.baseline)
    args.output.write_text(json.dumps(rows, indent=2) + "\n")
    for row in rows:
        print(
            row["seed"],
            row["before"]["max_chain"],
            "->",
            row["after"]["max_chain"],
            "premature",
            row["after"]["premature"],
            "game_over",
            row["after"]["game_over"],
            "locks",
            row["lock_count"],
            "mismatch",
            row["lock_mismatches"],
            "hidden",
            sum(not v["offline_planes_match"] for v in row["inference"]),
            "quota",
            row["quota_violations"],
            "clears",
            row["clears"],
        )
