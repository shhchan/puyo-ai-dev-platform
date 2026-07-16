"""PUYO-172 compact-search differential parity and profile benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import platform
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.beam_search import clone_simulator
from agents.compact_search import (
    COMPACT_SEARCH_SCHEMA_VERSION,
    CompactSearchSnapshot,
    CompactSearchState,
    legal_action_indices as compact_legal_action_indices,
    symmetry_reduced_action_indices,
    transition,
)
from puyo_env.actions import action_to_placement
from puyo_env.actions import legal_action_indices as authoritative_legal_action_indices
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, PuyoColor
from src.core.game import GameState
from src.core.headless import HeadlessPuyoSimulator
from src.core.puyo import Puyo
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp


BENCHMARK_SCHEMA_VERSION = "puyo.compact_search_kernel_benchmark.v1"
FIXTURE_SCHEMA_VERSION = "puyo.compact_search_fixtures.v1"
DEFAULT_OUTPUT_DIR = "docs/benchmarks/puyo-v1-7-2-compact-search-kernel"
DEFAULT_FIXTURE_PATH = "tests/fixtures/compact_search_kernel_cases.json"
DEFAULT_SEED_START = 123
DEFAULT_SEED_COUNT = 8
DEFAULT_MAX_TURNS = 8
DEFAULT_REPETITIONS = 2
MINIMUM_TRANSITIONS = 1_000
AMA_REFERENCE_COMMIT = "dea210bcd92965ae08fbc311f23565b0fab6dbbb"
CHAR_TO_COLOR = {
    ".": PuyoColor.EMPTY,
    "R": PuyoColor.RED,
    "B": PuyoColor.BLUE,
    "G": PuyoColor.GREEN,
    "Y": PuyoColor.YELLOW,
    "P": PuyoColor.PURPLE,
    "O": PuyoColor.OJAMA,
}
COLOR_TO_CHAR = {color: char for char, color in CHAR_TO_COLOR.items()}


def _write_json(path: Path, payload: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    options = (
        {"sort_keys": True, "separators": (",", ":")}
        if compact
        else {"indent": 2, "sort_keys": True}
    )
    path.write_text(json.dumps(payload, **options) + "\n", encoding="utf-8")


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _board_strings(grid: Sequence[Sequence[PuyoColor]]) -> list[str]:
    return ["".join(COLOR_TO_CHAR[color] for color in row) for row in grid]


def _fixture_simulator(
    case: Mapping[str, Any],
) -> tuple[HeadlessPuyoSimulator, tuple[PuyoColor, PuyoColor]]:
    rows = case["board"]
    if len(rows) != GRID_HEIGHT or any(len(row) != GRID_WIDTH for row in rows):
        raise ValueError(f"fixture {case.get('id')} must contain a 6x14 board")
    game = GameState(seed=0)
    game.spawn_puyo()
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            color = CHAR_TO_COLOR[char]
            if color != PuyoColor.EMPTY:
                game.field.place_puyo(x, y, Puyo(color))
    pair = tuple(PuyoColor[name] for name in case["pair"])
    game.current_puyo_1 = Puyo(pair[0])
    game.current_puyo_2 = Puyo(pair[1])
    game.all_clear_bonus_pending = bool(case["all_clear_bonus_pending"])
    return HeadlessPuyoSimulator(game_state=game), pair  # type: ignore[return-value]


def _coords(value: Sequence[tuple[int, int]]) -> list[list[int]]:
    return [[int(x), int(y)] for x, y in sorted(value)]


def _authoritative_garbage_cells(chain) -> list[list[int]]:
    if not chain.board:
        return []
    result = set()
    for x, y in chain.vanished:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            target_x, target_y = x + dx, y + dy
            if not (0 <= target_x < GRID_WIDTH and 0 <= target_y < 12):
                continue
            if chain.board[target_y][target_x] == PuyoColor.OJAMA:
                result.add((target_x, target_y))
    return _coords(result)


def _authoritative_signature(simulator, result) -> dict[str, Any]:
    state = CompactSearchState.from_simulator(simulator)
    return {
        "state": state.to_bytes().hex(),
        "valid": bool(result.valid),
        "axis_y": result.axis_y,
        "score_delta": int(result.score_delta),
        "attack_score_delta": int(result.attack_score_delta),
        "chain_count": int(result.chain_count),
        "vanished_count": sum(step.vanished_count for step in result.chains),
        "garbage_cleared_count": sum(
            len(_authoritative_garbage_cells(step)) for step in result.chains
        ),
        "game_over": bool(result.game_over),
        "all_clear_achieved": bool(result.all_clear_achieved),
        "all_clear_bonus_pending": bool(result.all_clear_bonus_pending),
        "all_clear_bonus_consumed": bool(result.all_clear_bonus_consumed),
        "all_clear_bonus_score": int(result.all_clear_bonus_score),
        "placement_board": (
            _board_strings(result.placement_board) if result.placement_board else []
        ),
        "chains": [
            {
                "chain_index": int(step.chain_index),
                "vanished_count": int(step.vanished_count),
                "garbage_cleared": _authoritative_garbage_cells(step),
                "score": int(step.score),
                "base": int(step.base),
                "bonus": int(step.bonus),
                "groups": sorted(_coords(group) for group in step.groups),
                "vanished": _coords(step.vanished),
                "board": _board_strings(step.board) if step.board else [],
                "all_clear_bonus_score": int(step.all_clear_bonus_score),
            }
            for step in result.chains
        ],
    }


def _compact_signature(result) -> dict[str, Any]:
    return {
        "state": result.state.to_bytes().hex(),
        "valid": bool(result.valid),
        "axis_y": result.axis_y,
        "score_delta": int(result.score_delta),
        "attack_score_delta": int(result.attack_score_delta),
        "chain_count": int(result.chain_count),
        "vanished_count": int(result.vanished_count),
        "garbage_cleared_count": int(result.garbage_cleared_count),
        "game_over": bool(result.game_over),
        "all_clear_achieved": bool(result.all_clear_achieved),
        "all_clear_bonus_pending": bool(result.all_clear_bonus_pending),
        "all_clear_bonus_consumed": bool(result.all_clear_bonus_consumed),
        "all_clear_bonus_score": int(result.all_clear_bonus_score),
        "placement_board": (
            _board_strings(result.placement_board) if result.placement_board else []
        ),
        "chains": [
            {
                "chain_index": int(step.chain_index),
                "vanished_count": int(step.vanished_count),
                "garbage_cleared": _coords(step.garbage_cleared),
                "score": int(step.score),
                "base": int(step.base),
                "bonus": int(step.bonus),
                "groups": sorted(_coords(group) for group in step.groups),
                "vanished": _coords(step.vanished),
                "board": _board_strings(step.board) if step.board else [],
                "all_clear_bonus_score": int(step.all_clear_bonus_score),
            }
            for step in result.chains
        ],
    }


def _golden_payload(
    legal_actions: Sequence[int],
    reduced_actions: Sequence[int],
    result,
) -> dict[str, Any]:
    return {
        "legal_actions": list(legal_actions),
        "symmetry_reduced_actions": list(reduced_actions),
        "axis_y": result.axis_y,
        "score_delta": int(result.score_delta),
        "attack_score_delta": int(result.attack_score_delta),
        "chain_count": int(result.chain_count),
        "vanished_count": int(result.vanished_count),
        "garbage_cleared_count": int(result.garbage_cleared_count),
        "game_over": bool(result.game_over),
        "all_clear_achieved": bool(result.all_clear_achieved),
        "all_clear_bonus_pending": bool(result.all_clear_bonus_pending),
        "all_clear_bonus_consumed": bool(result.all_clear_bonus_consumed),
        "all_clear_bonus_score": int(result.all_clear_bonus_score),
        "final_board": _board_strings(result.state.to_color_grid()),
        "chains": [
            {
                "chain_index": int(step.chain_index),
                "vanished_count": int(step.vanished_count),
                "garbage_cleared_count": int(step.garbage_cleared_count),
                "score": int(step.score),
                "base": int(step.base),
                "bonus": int(step.bonus),
                "all_clear_bonus_score": int(step.all_clear_bonus_score),
            }
            for step in result.chains
        ],
    }


def evaluate_fixtures(
    fixture_path: str | Path = DEFAULT_FIXTURE_PATH,
) -> dict[str, Any]:
    payload = _read_json(fixture_path)
    if payload.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise ValueError("unsupported compact-search fixture schema")
    records = []
    mismatches = []
    for case in payload["cases"]:
        simulator, pair = _fixture_simulator(case)
        snapshot = CompactSearchSnapshot.from_simulator(simulator)
        authoritative_legal = authoritative_legal_action_indices(simulator)
        compact_legal = compact_legal_action_indices(snapshot.state)
        reduced = symmetry_reduced_action_indices(snapshot.state, pair)
        authoritative_child = clone_simulator(simulator)
        authoritative_result = authoritative_child.step(
            action_to_placement(case["action"]),
            capture_visuals=True,
        )
        compact_result = transition(
            snapshot.state,
            pair,
            case["action"],
            capture_visuals=True,
        )
        authoritative_signature = _authoritative_signature(
            authoritative_child,
            authoritative_result,
        )
        compact_signature = _compact_signature(compact_result)
        golden = _golden_payload(compact_legal, reduced, compact_result)
        issues = []
        if tuple(authoritative_legal) != tuple(compact_legal):
            issues.append("legal_actions")
        if authoritative_signature != compact_signature:
            issues.append("transition")
        if golden != case["expected"]:
            issues.append("golden")
        if issues:
            mismatches.append({"case_id": case["id"], "issues": issues})
        records.append(
            {
                "case_id": case["id"],
                "passed": not issues,
                "result_digest": _digest(compact_signature),
                "legal_action_count": len(compact_legal),
                "reduced_action_count": len(reduced),
                "chain_count": compact_result.chain_count,
                "score_delta": compact_result.score_delta,
                "garbage_cleared_count": compact_result.garbage_cleared_count,
                "game_over": compact_result.game_over,
            }
        )
    digest_payload = {
        "fixture_sha256": file_sha256(fixture_path),
        "records": records,
        "mismatches": mismatches,
    }
    return {
        "fixture_path": str(fixture_path),
        "fixture_sha256": file_sha256(fixture_path),
        "case_count": len(records),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "records": records,
        "digest": _digest(digest_payload),
    }


def _compact_deep_size(state: CompactSearchState) -> int:
    values = [
        state,
        state.planes,
        state.column_heights,
        *state.planes,
        *state.column_heights,
    ]
    seen = set()
    size = 0
    for value in values:
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        size += sys.getsizeof(value)
    return size


def _record_hash(
    state: CompactSearchState,
    hashes: dict[int, bytes],
    unique_states: set[bytes],
) -> int:
    payload = state.to_bytes()
    unique_states.add(payload)
    value = hash(state)
    previous = hashes.setdefault(value, payload)
    return int(previous != payload)


def evaluate_seeded_corpus(
    seeds: Sequence[int],
    *,
    max_turns: int,
) -> dict[str, Any]:
    records = []
    mismatches = []
    legal_mismatch_count = 0
    clone_seconds = 0.0
    authoritative_transition_seconds = 0.0
    compact_transition_seconds = 0.0
    hashes: dict[int, bytes] = {}
    unique_states: set[bytes] = set()
    hash_collision_count = 0
    per_seed = []
    size_profile = None

    for seed in seeds:
        simulator = HeadlessPuyoSimulator(seed=int(seed))
        completed_turns = 0
        seed_transition_count = 0
        for turn in range(max_turns):
            snapshot = CompactSearchSnapshot.from_simulator(simulator)
            if size_profile is None:
                size_profile = {
                    "compact_canonical_bytes": len(snapshot.state.to_bytes()),
                    "compact_pickle_bytes": len(
                        pickle.dumps(snapshot.state, protocol=pickle.HIGHEST_PROTOCOL)
                    ),
                    "compact_python_deep_bytes": _compact_deep_size(snapshot.state),
                    "authoritative_pickle_bytes": len(
                        pickle.dumps(simulator, protocol=pickle.HIGHEST_PROTOCOL)
                    ),
                }
            hash_collision_count += _record_hash(
                snapshot.state,
                hashes,
                unique_states,
            )
            authoritative_legal = tuple(authoritative_legal_action_indices(simulator))
            compact_legal = compact_legal_action_indices(snapshot.state)
            if authoritative_legal != compact_legal:
                legal_mismatch_count += 1
                mismatches.append(
                    {
                        "seed": int(seed),
                        "turn": int(turn),
                        "action": None,
                        "kind": "legal_actions",
                        "authoritative": list(authoritative_legal),
                        "compact": list(compact_legal),
                    }
                )
            if snapshot.current_pair is None or not authoritative_legal:
                break

            children = {}
            for action in authoritative_legal:
                start = time.perf_counter()
                child = clone_simulator(simulator)
                after_clone = time.perf_counter()
                authoritative_result = child.step(
                    action_to_placement(action),
                    capture_visuals=True,
                )
                after_authoritative = time.perf_counter()
                compact_result = transition(
                    snapshot.state,
                    snapshot.current_pair,
                    action,
                    capture_visuals=True,
                )
                after_compact = time.perf_counter()
                clone_seconds += after_clone - start
                authoritative_transition_seconds += after_authoritative - after_clone
                compact_transition_seconds += after_compact - after_authoritative
                authoritative_signature = _authoritative_signature(
                    child,
                    authoritative_result,
                )
                compact_signature = _compact_signature(compact_result)
                if authoritative_signature != compact_signature:
                    mismatches.append(
                        {
                            "seed": int(seed),
                            "turn": int(turn),
                            "action": int(action),
                            "kind": "transition",
                            "authoritative_digest": _digest(authoritative_signature),
                            "compact_digest": _digest(compact_signature),
                        }
                    )
                hash_collision_count += _record_hash(
                    compact_result.state,
                    hashes,
                    unique_states,
                )
                records.append(
                    {
                        "seed": int(seed),
                        "turn": int(turn),
                        "action": int(action),
                        "result_digest": _digest(compact_signature),
                    }
                )
                children[int(action)] = child
                seed_transition_count += 1

            ordered = list(authoritative_legal)
            offset = (int(seed) * 31 + turn * 17) % len(ordered)
            ordered = ordered[offset:] + ordered[:offset]
            chosen = next(
                (action for action in ordered if not children[action].game.game_over),
                ordered[0],
            )
            simulator = children[chosen]
            completed_turns += 1
            if simulator.game.game_over:
                break
        per_seed.append(
            {
                "seed": int(seed),
                "completed_turns": completed_turns,
                "transition_count": seed_transition_count,
                "game_over": bool(simulator.game.game_over),
            }
        )

    transition_count = len(records)
    authoritative_total = clone_seconds + authoritative_transition_seconds
    profile = {
        "capture_visuals": True,
        "transition_count": transition_count,
        "authoritative_clone_seconds": clone_seconds,
        "authoritative_transition_seconds": authoritative_transition_seconds,
        "authoritative_clone_plus_transition_seconds": authoritative_total,
        "compact_transition_seconds": compact_transition_seconds,
        "authoritative_transitions_per_second": (
            transition_count / authoritative_total if authoritative_total else None
        ),
        "compact_transitions_per_second": (
            transition_count / compact_transition_seconds
            if compact_transition_seconds
            else None
        ),
        "compact_speedup_vs_clone_plus_transition": (
            authoritative_total / compact_transition_seconds
            if compact_transition_seconds
            else None
        ),
        "state_size": size_profile or {},
    }
    digest_payload = {
        "seeds": list(seeds),
        "max_turns": int(max_turns),
        "records": records,
        "mismatch_count": len(mismatches),
        "legal_mismatch_count": legal_mismatch_count,
        "hash_collision_count": hash_collision_count,
    }
    return {
        "seeds": list(seeds),
        "max_turns": int(max_turns),
        "transition_count": transition_count,
        "state_count": len(unique_states),
        "hash_collision_count": hash_collision_count,
        "legal_mismatch_count": legal_mismatch_count,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:100],
        "mismatch_digest": _digest(mismatches),
        "records": records,
        "per_seed": per_seed,
        "profile": profile,
        "digest": _digest(digest_payload),
    }


def evaluate_repetition(
    seeds: Sequence[int],
    *,
    max_turns: int,
    fixture_path: str | Path = DEFAULT_FIXTURE_PATH,
) -> dict[str, Any]:
    fixtures = evaluate_fixtures(fixture_path)
    corpus = evaluate_seeded_corpus(seeds, max_turns=max_turns)
    return {
        "fixture": fixtures,
        "corpus": corpus,
        "deterministic_fingerprint": {
            "fixture_digest": fixtures["digest"],
            "fixture_mismatch_count": fixtures["mismatch_count"],
            "corpus_digest": corpus["digest"],
            "corpus_mismatch_digest": corpus["mismatch_digest"],
            "corpus_mismatch_count": corpus["mismatch_count"],
            "transition_count": corpus["transition_count"],
        },
    }


def _cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "unknown"


def _command(args: argparse.Namespace) -> str:
    return (
        "python -m eval.v1_7_compact_search_kernel_benchmark run "
        f"--output-dir {args.output_dir} --fixture {args.fixture} "
        f"--seed-start {args.seed_start} --seed-count {args.seed_count} "
        f"--max-turns {args.max_turns} --repetitions {args.repetitions} "
        f"--minimum-transitions {args.minimum_transitions}"
    )


def _write_report(path: Path, summary: Mapping[str, Any]) -> None:
    checks = summary["checks"]
    profile = summary["profile"]
    sizes = profile["state_size"]
    lines = [
        "# PUYO-172 compact search kernel parity",
        "",
        f"- result: **{'PASS' if summary['passed'] else 'FAIL'}**",
        f"- fixed fixtures: {summary['fixture']['case_count']} cases / {summary['fixture']['mismatch_count']} mismatches",
        f"- seeded corpus: {summary['corpus']['transition_count']} transitions / {summary['corpus']['mismatch_count']} mismatches",
        f"- deterministic repeat: **{'PASS' if summary['determinism']['passed'] else 'FAIL'}** ({summary['determinism']['repetitions']} runs)",
        f"- hash collisions: {summary['corpus']['hash_collision_count']} across {summary['corpus']['state_count']} unique states",
        "",
        "## Checks",
        "",
    ]
    lines.extend(
        f"- `{name}`: **{'PASS' if passed else 'FAIL'}**"
        for name, passed in checks.items()
    )
    lines.extend(
        [
            "",
            "## Profile",
            "",
            f"- authoritative clone + transition: {profile['authoritative_clone_plus_transition_seconds']:.6f} s",
            f"- compact transition: {profile['compact_transition_seconds']:.6f} s",
            f"- observed compact speedup: {profile['compact_speedup_vs_clone_plus_transition']:.3f}x",
            f"- canonical compact state: {sizes['compact_canonical_bytes']} bytes",
            f"- serialized authoritative snapshot: {sizes['authoritative_pickle_bytes']} bytes",
            "",
            "Wall-clock values are observational and excluded from the deterministic digest. Performance is not a Go condition for PUYO-172.",
            "",
            "## Provenance",
            "",
            f"- authoritative oracle: `src/core/headless.py` at `{summary['git_commit']}`",
            f"- Ama analysis reference: v2.0.1 `{AMA_REFERENCE_COMMIT}` (MIT)",
            "- copied Ama code: none",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    seeds = tuple(range(args.seed_start, args.seed_start + args.seed_count))
    repetitions = [
        evaluate_repetition(
            seeds,
            max_turns=args.max_turns,
            fixture_path=args.fixture,
        )
        for _ in range(args.repetitions)
    ]
    fingerprints = [item["deterministic_fingerprint"] for item in repetitions]
    determinism = {
        "passed": len({_digest(value) for value in fingerprints}) == 1,
        "repetitions": len(repetitions),
        "fingerprints": fingerprints,
        "excluded_fields": [
            "profile.authoritative_clone_seconds",
            "profile.authoritative_transition_seconds",
            "profile.compact_transition_seconds",
            "profile.*_transitions_per_second",
            "profile.compact_speedup_vs_clone_plus_transition",
        ],
        "scope": [
            "fixture digest and mismatch summary",
            "seed/turn/action transition result digests",
            "legal-action mismatch count",
            "hash collision count",
        ],
    }
    first = repetitions[0]
    fixture = first["fixture"]
    corpus = first["corpus"]
    checks = {
        "fixed_fixture_parity": fixture["mismatch_count"] == 0,
        "seeded_transition_parity": corpus["mismatch_count"] == 0,
        "legal_action_parity": corpus["legal_mismatch_count"] == 0,
        "minimum_transition_count": (
            corpus["transition_count"] >= args.minimum_transitions
        ),
        "deterministic_repeat": determinism["passed"],
        "hash_collision_free": corpus["hash_collision_count"] == 0,
    }
    summary = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "created_at_utc": utc_timestamp(),
        "git_commit": git_commit(),
        "command": _command(args),
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "cpu": _cpu_model(),
        },
        "config": {
            "seeds": list(seeds),
            "max_turns": int(args.max_turns),
            "repetitions": int(args.repetitions),
            "minimum_transitions": int(args.minimum_transitions),
            "capture_visuals": True,
        },
        "contracts": {
            "compact_state": COMPACT_SEARCH_SCHEMA_VERSION,
            "fixture": FIXTURE_SCHEMA_VERSION,
            "authoritative_oracle": "src.core.headless.HeadlessPuyoSimulator",
            "future_tsumo_cursor": "external CompactTranspositionKey fields",
        },
        "references": {
            "ama_version": "v2.0.1",
            "ama_commit": AMA_REFERENCE_COMMIT,
            "ama_license": "MIT",
            "copied_code": False,
        },
        "fixture": {
            key: fixture[key]
            for key in (
                "fixture_path",
                "fixture_sha256",
                "case_count",
                "mismatch_count",
                "digest",
            )
        },
        "corpus": {
            key: corpus[key]
            for key in (
                "seeds",
                "max_turns",
                "transition_count",
                "state_count",
                "hash_collision_count",
                "legal_mismatch_count",
                "mismatch_count",
                "mismatch_digest",
                "digest",
            )
        },
        "determinism": determinism,
        "profile": corpus["profile"],
        "checks": checks,
        "passed": all(checks.values()),
        "performance_is_go_condition": False,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        output_dir / "fixture_results.json",
        {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            **fixture,
        },
    )
    _write_json(
        output_dir / "seeded_trajectory_results.json",
        {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            **{key: value for key, value in corpus.items() if key != "profile"},
        },
        compact=True,
    )
    _write_json(
        output_dir / "profile.json",
        {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "repetitions": [item["corpus"]["profile"] for item in repetitions],
            "selected_repetition": 0,
            "performance_is_go_condition": False,
        },
    )
    _write_json(output_dir / "determinism.json", determinism)
    _write_json(output_dir / "benchmark_summary.json", summary)
    _write_report(output_dir / "benchmark_report.md", summary)
    artifact_paths = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.name != "benchmark_manifest.json"
    )
    manifest = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "name": "puyo-v1-7-2-compact-search-kernel",
        "created_at_utc": summary["created_at_utc"],
        "git_commit": summary["git_commit"],
        "evaluation_completed": bool(corpus["transition_count"]),
        "parity_passed": summary["passed"],
        "config": summary["config"],
        "environment": summary["environment"],
        "command": summary["command"],
        "artifacts": [
            describe_artifact(path, run_dir=output_dir, role=path.stem)
            for path in artifact_paths
        ],
    }
    _write_json(output_dir / "benchmark_manifest.json", manifest)
    return summary


def verify_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    artifact_dir = Path(args.artifact_dir)
    manifest = _read_json(artifact_dir / "benchmark_manifest.json")
    issues = []
    if manifest.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append("unexpected manifest schema")
    for artifact in manifest.get("artifacts", []):
        path = artifact_dir / artifact["path"]
        if not path.exists():
            issues.append(f"missing artifact: {artifact['path']}")
        elif file_sha256(path) != artifact.get("sha256"):
            issues.append(f"artifact hash mismatch: {artifact['path']}")
    summary = _read_json(artifact_dir / "benchmark_summary.json")
    if summary.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append("unexpected summary schema")
    if not summary.get("passed"):
        issues.append("compact-search parity checks failed")
    if summary.get("corpus", {}).get("transition_count", 0) < summary.get(
        "config", {}
    ).get("minimum_transitions", MINIMUM_TRANSITIONS):
        issues.append("seeded corpus is below the recorded minimum")
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "passed": not issues,
        "issues": issues,
        "checks": summary.get("checks", {}),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--fixture", default=DEFAULT_FIXTURE_PATH)
    run.add_argument("--seed-start", type=int, default=DEFAULT_SEED_START)
    run.add_argument("--seed-count", type=int, default=DEFAULT_SEED_COUNT)
    run.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    run.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    run.add_argument(
        "--minimum-transitions",
        type=int,
        default=MINIMUM_TRANSITIONS,
    )
    verify = subparsers.add_parser("verify")
    verify.add_argument("--artifact-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    for name in ("seed_count", "max_turns", "repetitions", "minimum_transitions"):
        if hasattr(args, name) and getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        summary = run_benchmark(args)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["passed"] else 1
    result = verify_benchmark(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
