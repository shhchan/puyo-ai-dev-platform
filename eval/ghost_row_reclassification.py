"""Reconstruct PUYO-204's 2,400 decisions without rerunning policy search.

The old plan summary omitted predicted boards. Its exact compact fingerprint
authenticates the board reconstructed from the old 13-row observation and pair.
Historical failures remain failures; corrected transitions are separate evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from agents.compact_search import CompactSearchState, legal_action_indices, transition
from agents.deep_chain_builder import (
    _compact_state_from_observation,
    build_visible_runtime_input,
)
from agents.deep_chain_native_transition import (
    NativeCompactBatchClient,
    NativeCompactTransitionInput,
)
from eval.simulator_parity import (
    BOARD_STATE_CONTRACT_VERSION,
    PARITY_CONTRACT_VERSION,
    board_names,
    state_fingerprint,
)
from puyo_env.actions import PLACEMENT_ACTIONS
from puyo_env.obs import encode_observation
from src.core.headless import HeadlessPuyoSimulator

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "docs/benchmarks/puyo-204-deep-chain-native-baseline/runs"
DEFAULT_OUTPUT = (
    ROOT / "docs/benchmarks/puyo-229-ghost-row-parity/reclassification.json"
)
SCHEMA_VERSION = "puyo.ghost_row_reclassification.v1"
_PUBLIC_MASK = (1 << (13 * 6)) - 1


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _signature(result):
    return tuple(
        getattr(result, key)
        for key in (
            "valid",
            "axis_y",
            "chain_count",
            "score_delta",
            "attack_score_delta",
            "game_over",
            "all_clear_achieved",
            "all_clear_bonus_pending",
            "all_clear_bonus_consumed",
            "all_clear_bonus_score",
        )
    )


def audit_run(path: Path, client: NativeCompactBatchClient) -> dict:
    run = json.loads(path.read_text(encoding="utf-8"))
    sim = HeadlessPuyoSimulator(seed=int(run["seed"]))
    mismatches, errors, inputs, python_results = [], [], [], []
    legal_sets = []
    component_counts = {
        key: 0
        for key in (
            "public_board",
            "ghost_row",
            "action",
            "chain_count",
            "score_delta",
            "game_over",
        )
    }
    legacy_failures = 0
    for turn, record in enumerate(run["records"]):
        identity = f"{run['run_id']}/turn-{turn}"
        root = CompactSearchState.from_simulator(sim)
        pair = (sim.game.current_puyo_1.color, sim.game.current_puyo_2.color)
        action = int(record["action"])
        step = record["plan"]["steps"][0]
        if record["turn"] != turn or board_names(root) != record["board_before"]:
            errors.append(
                f"{identity}: authoritative root/turn differs from raw evidence"
            )
        if [color.name for color in pair] != step["tsumo"]:
            errors.append(
                f"{identity}: seeded current pair differs from recorded tsumo"
            )
        # Explicitly reproduce the old adapter, including its lifecycle defaults.
        legacy_root = CompactSearchState(
            planes=tuple(plane & _PUBLIC_MASK for plane in root.planes),
            score=root.score,
            last_chain_end_score=root.last_chain_end_score,
        )
        legacy = transition(legacy_root, pair, action)
        if state_fingerprint(legacy.state) != step["state_fingerprint"]:
            errors.append(
                f"{identity}: legacy reconstruction fingerprint is unexplained"
            )
        if record["parity"]["predicted_state_fingerprint"] != step["state_fingerprint"]:
            errors.append(f"{identity}: stored parity/plan fingerprints disagree")
        observed = build_visible_runtime_input(
            encode_observation(sim, turn, run["max_steps"]),
            {
                "score": root.score,
                "last_chain_end_score": root.last_chain_end_score,
                "all_clear_bonus_pending": root.all_clear_bonus_pending,
                "game_over": root.game_over,
            },
        )
        adapted = _compact_state_from_observation(observed)
        if adapted != root:
            errors.append(
                f"{identity}: corrected observation loses complete root state"
            )
        corrected = transition(adapted, pair, action)
        actual = sim.step(PLACEMENT_ACTIONS[action])
        final = CompactSearchState.from_simulator(sim)
        if board_names(final) != record["board_after"]:
            errors.append(
                f"{identity}: authoritative final board differs from raw evidence"
            )
        if {
            key: getattr(actual, key)
            for key in ("valid", "chain_count", "score_delta", "game_over")
        } != record["actual_result"]:
            errors.append(f"{identity}: authoritative result differs from raw evidence")
        if corrected.state != final or _signature(corrected) != _signature(actual):
            errors.append(
                f"{identity}: corrected Python/authoritative transition differs"
            )
        inputs.append(NativeCompactTransitionInput(adapted, pair, action))
        python_results.append(corrected)
        legal_sets.append(legal_action_indices(adapted))
        legacy_board = board_names(legacy.state)
        cells = [
            {"x": x, "y": y, "predicted": legacy_board[y][x], "actual": color}
            for y, row in enumerate(record["board_after"])
            for x, color in enumerate(row)
            if legacy_board[y][x] != color
        ]
        components = {
            "public_board": not any(cell["y"] < 13 for cell in cells),
            "ghost_row": not any(cell["y"] == 13 for cell in cells),
            "action": step["action"] == action,
            "chain_count": step["predicted_chain_count"] == actual.chain_count,
            "score_delta": step["predicted_score"] == actual.score_delta,
            "game_over": step["game_over"] == actual.game_over,
        }
        for key, matched in components.items():
            component_counts[key] += int(not matched)
        old_mismatches = [
            key
            for key in ("action", "chain_count", "score_delta", "game_over")
            if not components[key]
        ]
        if cells:
            old_mismatches.append("board")
        if (
            old_mismatches != record["parity"]["mismatches"]
            or bool(old_mismatches) == record["parity"]["passed"]
        ):
            errors.append(f"{identity}: legacy parity verdict is unexplained")
        if old_mismatches:
            legacy_failures += 1
            explained = (
                bool(cells)
                and all(cell["y"] == 13 for cell in cells)
                and all(
                    matched for key, matched in components.items() if key != "ghost_row"
                )
                and state_fingerprint(legacy.state) == step["state_fingerprint"]
            )
            mismatches.append(
                {
                    "run_id": run["run_id"],
                    "seed": run["seed"],
                    "repeat": run["repeat"],
                    "turn": turn,
                    "action": action,
                    "decision_digest": record["decision_digest"],
                    "classification": "ghost_row_lost_at_observation_boundary"
                    if explained
                    else "unexplained",
                    "historical_full_parity_passed": False,
                    "legacy_components": components,
                    "cell_differences": cells,
                    "legacy_root_fingerprint": state_fingerprint(legacy_root),
                    "complete_root_fingerprint": state_fingerprint(root),
                    "legacy_predicted_fingerprint": state_fingerprint(legacy.state),
                    "complete_after_fingerprint": state_fingerprint(final),
                    "legacy_legal_actions": list(legal_action_indices(legacy_root)),
                    "complete_legal_actions": list(legal_action_indices(root)),
                    "corrected_python_authoritative_passed": corrected.state == final
                    and _signature(corrected) == _signature(actual),
                }
            )
    native = client.transition_batch(inputs, include_actions=True)
    for turn, (actual, expected, legal) in enumerate(
        zip(native.records, python_results, legal_sets, strict=True)
    ):
        if (
            actual.state != expected.state
            or _signature(actual) != _signature(expected)
            or actual.legal_action_indices != legal
        ):
            errors.append(
                f"{run['run_id']}/turn-{turn}: corrected native/Python state, result or legal actions differ"
            )
    if legacy_failures != run["simulator_parity_mismatch_count"]:
        errors.append(
            f"{run['run_id']}: raw mismatch count differs from reconstructed decisions"
        )
    return {
        "run_id": run["run_id"],
        "source_sha256": _sha256(path),
        "decisions": len(inputs),
        "component_mismatch_counts": component_counts,
        "mismatches": mismatches,
        "errors": errors,
    }


def build_report(source_dir: Path = SOURCE_DIR) -> dict:
    client = NativeCompactBatchClient()
    runs = [audit_run(path, client) for path in sorted(source_dir.glob("*.json"))]
    errors = [error for run in runs for error in run["errors"]]
    expected_ids = {
        f"seed-{seed}-repeat-{repeat:02}"
        for seed in range(123, 153)
        for repeat in (1, 2)
    }
    if {run["run_id"] for run in runs} != expected_ids or len(runs) != 60:
        errors.append("canonical run identity coverage is not 30 seeds x 2 repeats")
    decisions = sum(run["decisions"] for run in runs)
    mismatches = [record for run in runs for record in run["mismatches"]]
    if decisions != 2400 or len(mismatches) != 34:
        errors.append("canonical decision/mismatch coverage is not 2400/34")
    if any(record["classification"] == "unexplained" for record in mismatches):
        errors.append("unexplained historical mismatch remains")
    return {
        "schema_version": SCHEMA_VERSION,
        "ticket": "PUYO-229",
        "parity_contract_version": PARITY_CONTRACT_VERSION,
        "board_state_contract_version": BOARD_STATE_CONTRACT_VERSION,
        "method": "seeded authoritative replay; old projection fingerprint reconstruction; corrected observation -> Python/native transition",
        "native_build": client.capabilities.to_dict(),
        "source_runs": [
            {
                "run_id": run["run_id"],
                "sha256": run["source_sha256"],
                "decisions": run["decisions"],
            }
            for run in runs
        ],
        "summary": {
            "run_count": len(runs),
            "decision_count": decisions,
            "historical_mismatch_count": len(mismatches),
            "historical_differing_cell_count": sum(
                len(record["cell_differences"]) for record in mismatches
            ),
            "component_mismatch_counts": {
                key: sum(run["component_mismatch_counts"][key] for run in runs)
                for key in (
                    "public_board",
                    "ghost_row",
                    "action",
                    "chain_count",
                    "score_delta",
                    "game_over",
                )
            },
            "reclassification_verified": not errors,
            "corrected_transition_parity_passed": not errors,
            "historical_full_parity_passed": False,
            "baseline_promoted": False,
            "scope": "fixed recorded actions, not a new search/quality/performance benchmark",
        },
        "mismatches": mismatches,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Reconstruct again and compare stored semantic evidence",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    historical = SOURCE_DIR.parent.resolve()
    if not args.verify and (output == historical or historical in output.parents):
        parser.error("PUYO-204 artifacts are immutable; use a new output path")
    report = build_report(args.source_dir)
    if args.verify:
        stored = json.loads(output.read_text(encoding="utf-8"))
        # Native build identity can differ across machines; semantic evidence cannot.
        if {k: v for k, v in report.items() if k != "native_build"} != {
            k: v for k, v in stored.items() if k != "native_build"
        }:
            raise ValueError("stored reclassification differs from replayed evidence")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(report["summary"], sort_keys=True))
    return 0 if report["summary"]["reclassification_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
