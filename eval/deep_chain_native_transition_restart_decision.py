"""Verify the evidence-only PUYO-213 native-line restart decision."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from eval.deep_chain_native_transition_investigation import verify_investigation
from eval.deep_chain_native_transition_profile import verify_benchmark

TICKET = "PUYO-213"
SCHEMA_VERSION = "puyo.native_transition_restart_decision.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DECISION_PATH = REPOSITORY_ROOT / (
    "docs/benchmarks/puyo-213-transition-restart-decision/final_decision.json"
)
PUYO_207_DIR = REPOSITORY_ROOT / (
    "docs/benchmarks/puyo-207-native-transition-verification"
)
PUYO_211_DIR = REPOSITORY_ROOT / (
    "docs/benchmarks/puyo-211-quiet-transition-investigation"
)
BASELINE_MERGE_COMMIT = "5e76617eeeb26630ec45aa9843d40a691592341a"
INTEGRATION_HEAD = "b5865038d1bffa0970baaad20aa79369fa21d7f1"
AUTHORITY_SHA256 = {
    (
        "docs/benchmarks/puyo-207-native-transition-verification/"
        "benchmark_manifest.json"
    ): "d2987cf85995613b6f5580177c3d9dd258d98524a32eec8d8dd8ca3a440ab4b5",
    (
        "docs/benchmarks/puyo-207-native-transition-verification/benchmark_summary.json"
    ): "89423373e8149daf56004eba398d0dbdf3959e2f1ee1c1d78ae2eda4adfefa50",
    (
        "docs/benchmarks/puyo-207-native-transition-verification/go_no_go_decision.json"
    ): "94c0e715a4d8b9b53d29ec5fdce79f0e4c928efba7e1d393c7c2d6206a43d92b",
    (
        "docs/benchmarks/puyo-211-quiet-transition-investigation/"
        "benchmark_manifest.json"
    ): "7c6b775edd8b533d14b2040cadbf1a29c153d698dbafa6a2d9e17f006a051ef1",
    (
        "docs/benchmarks/puyo-211-quiet-transition-investigation/benchmark_summary.json"
    ): "41bc5f19bd13b2893f5afb3f4349dbfdcc4e5c84bc3ec5fcb18b65046436bdf4",
    (
        "docs/benchmarks/puyo-211-quiet-transition-investigation/"
        "candidate_analysis.json"
    ): "7aab2969f979d8277b9f08a21608444df1beeae68a889980bda7c72f60e4fa32",
    (
        "docs/benchmarks/puyo-211-quiet-transition-investigation/paired_baseline.json"
    ): "98d81a3ae55142a9814ee4cebd4873b347f5bfef0224f4e52faff55f94fed2ac",
    (
        "docs/benchmarks/puyo-211-quiet-transition-investigation/"
        "raw_authoritative_runs.json"
    ): "e953c9603252732612334cc24ffd823bf74f0f56d11f41114c23c04d302dc346",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _check_equal(
    issues: list[str],
    label: str,
    observed: Any,
    expected: Any,
) -> None:
    if observed != expected:
        issues.append(f"{label} differs from its authority")


def _verify_authority_artifacts(decision: Mapping[str, Any], issues: list[str]) -> None:
    entries = decision.get("authority_artifacts", [])
    recorded = {
        item.get("path"): item.get("sha256")
        for item in entries
        if isinstance(item, Mapping)
    }
    if len(recorded) != len(entries):
        issues.append("authority artifact list has duplicate or invalid entries")
    if recorded != AUTHORITY_SHA256:
        issues.append("authority artifact inventory changed")
    for relative_path, expected_sha256 in AUTHORITY_SHA256.items():
        path = REPOSITORY_ROOT / relative_path
        if not path.exists():
            issues.append(f"missing authority artifact: {relative_path}")
        elif _sha256(path) != expected_sha256:
            issues.append(f"authority artifact digest mismatch: {relative_path}")


def _verify_source_evidence(decision: Mapping[str, Any], issues: list[str]) -> None:
    puyo_207 = _read_json(PUYO_207_DIR / "go_no_go_decision.json")
    memory = _read_json(PUYO_207_DIR / "memory_verification.json")
    semantic = _read_json(PUYO_207_DIR / "semantic_verification.json")
    candidate = _read_json(PUYO_211_DIR / "candidate_analysis.json")
    puyo_211_manifest = _read_json(PUYO_211_DIR / "benchmark_manifest.json")

    expected_puyo_207 = {
        "decision": puyo_207["decision"],
        "mixed_p95_ns": puyo_207["component"]["mixed_p95_ns_per_transition"],
        "mixed_target_ns": puyo_207["component"]["mixed_target_ns_per_transition"],
        "quiet_p95_ns": puyo_207["component"]["quiet_p95_ns_per_transition"],
        "quiet_target_ns": puyo_207["component"]["quiet_target_ns_per_transition"],
        "transition_projection_p95_ms": puyo_207["remaining_budget"][
            "transition_projection_p95_ms"
        ],
        "evaluator_quiescence_residual_p95_ms": puyo_207["remaining_budget"][
            "evaluator_quiescence_p95_ms"
        ],
        "combined_transition_evaluator_p95_ms": puyo_207["remaining_budget"][
            "combined_transition_evaluator_p95_ms"
        ],
        "native_envelope_p95_ms": puyo_207["remaining_budget"][
            "native_envelope_p95_ms"
        ],
        "end_to_end_envelope_p95_ms": puyo_207["remaining_budget"][
            "end_to_end_envelope_p95_ms"
        ],
        "transition_calls": puyo_207["remaining_budget"]["transition_calls"],
        "observed_combined_p95_ms": puyo_207["observed_combined_p95_ms"],
        "required_evaluator_speedup": puyo_207["amdahl_recalculation"][
            "required_evaluator_speedup_at_residual_budget"
        ],
        "fixture_mismatch_count": semantic["fixtures"]["mismatch_count"],
        "oracle_mismatch_count": semantic["oracle"]["mismatch_count"],
        "native_mismatch_count": semantic["native_parity"]["mismatch_count"],
        "determinism_mismatch_count": semantic["determinism"]["total_mismatch_count"],
        "normal_hot_path_heap_allocations": memory["observed"][
            "normal_hot_path_heap_allocations"
        ],
        "child_state_bytes": memory["observed"]["child_state_bytes"],
        "hot_result_bytes": memory["observed"]["hot_result_bytes"],
    }
    expected_puyo_211 = {
        "decision": candidate["decision"],
        "selected_candidate": candidate["selected_candidate"],
        "wheel_sha256": puyo_211_manifest["wheel_sha256"],
        "mixed_p95_ns_by_run": candidate["engineering_projection"][
            "observed_mixed_p95_by_run"
        ],
        "quiet_p95_ns_by_run": candidate["engineering_projection"][
            "observed_quiet_p95_by_run"
        ],
        "projected_mixed_p95_ns_by_run": candidate["engineering_projection"][
            "projected_mixed_p95_by_run"
        ],
        "projected_quiet_p95_ns_by_run": candidate["engineering_projection"][
            "projected_quiet_p95_by_run"
        ],
        "projected_quiet_median_p95_ns": candidate["engineering_projection"][
            "projected_quiet_median_p95"
        ],
        "conservative_candidate_saving_ns": candidate["engineering_projection"][
            "conservative_saved_ns_per_quiet_transition"
        ],
        "mixed_maximum_same_wheel_drift_percent": candidate["evidence"][
            "paired_baseline"
        ]["mixed"]["maximum_absolute_p95_drift_percent"],
        "quiet_maximum_same_wheel_drift_percent": candidate["evidence"][
            "paired_baseline"
        ]["quiet"]["maximum_absolute_p95_drift_percent"],
    }
    evidence = decision.get("evidence", {})
    _check_equal(
        issues,
        "PUYO-207 evidence snapshot",
        evidence.get("puyo_207"),
        expected_puyo_207,
    )
    _check_equal(
        issues,
        "PUYO-211 evidence snapshot",
        evidence.get("puyo_211"),
        expected_puyo_211,
    )


def _verify_git_audit(decision: Mapping[str, Any], issues: list[str]) -> None:
    audit = decision.get("repository_audit", {})
    _check_equal(
        issues,
        "integration head",
        audit.get("integration_head"),
        INTEGRATION_HEAD,
    )
    _check_equal(
        issues,
        "baseline merge commit",
        audit.get("baseline_merge_commit"),
        BASELINE_MERGE_COMMIT,
    )
    if audit.get("native_changed_files") != []:
        issues.append("repository audit records native candidate changes")
    if audit.get("candidate_commit") is not None:
        issues.append("repository audit records a PUYO-212 candidate commit")
    if audit.get("candidate_pr", {}).get("exists") is not False:
        issues.append("repository audit records a PUYO-212 candidate PR")

    for commit in (BASELINE_MERGE_COMMIT, INTEGRATION_HEAD):
        if _git(["cat-file", "-e", f"{commit}^{{commit}}"]).returncode != 0:
            issues.append(f"recorded commit is unavailable: {commit}")
    if (
        _git(
            ["merge-base", "--is-ancestor", BASELINE_MERGE_COMMIT, INTEGRATION_HEAD]
        ).returncode
        != 0
    ):
        issues.append("recorded integration ancestry is invalid")
    native_diff = _git(
        [
            "diff",
            "--name-only",
            f"{BASELINE_MERGE_COMMIT}..{INTEGRATION_HEAD}",
            "--",
            "native/deep_chain_native",
        ]
    )
    if native_diff.returncode != 0:
        issues.append("recorded integration native diff could not be reproduced")
    elif native_diff.stdout.strip():
        issues.append("recorded integration range contains native changes")


def verify_decision(
    decision_path: str | Path = DEFAULT_DECISION_PATH,
) -> dict[str, Any]:
    path = Path(decision_path)
    issues: list[str] = []
    try:
        decision = _read_json(path)
    except (OSError, ValueError) as error:
        return {"passed": False, "issues": [f"cannot read decision: {error}"]}

    if decision.get("schema_version") != SCHEMA_VERSION:
        issues.append("unexpected PUYO-213 decision schema")
    if decision.get("ticket") != TICKET:
        issues.append("unexpected PUYO-213 decision ticket")
    if decision.get("decision") != "NO_GO_STOP":
        issues.append("PUYO-213 must record the selected NO_GO_STOP outcome")
    risk = decision.get("risk_acceptance", {})
    if risk.get("granted") is not False:
        issues.append("quiet component risk acceptance must not be granted")
    if risk.get("human_reviewer_approval_recorded") is not False:
        issues.append("decision must not claim an absent human risk approval")
    if decision.get("puyo_201", {}).get("may_start") is not False:
        issues.append("PUYO-201 must remain stopped")
    if decision.get("puyo_202", {}).get("may_start") is not False:
        issues.append("PUYO-202 must remain stopped")
    if decision.get("production_backend_changed") is not False:
        issues.append("evidence-only decision must not change production routing")
    expected_statuses = {
        "PUYO-200": "Complete",
        "PUYO-201": "To Do",
        "PUYO-202": "To Do",
        "PUYO-207": "Complete",
        "PUYO-213": "Complete",
    }
    _check_equal(
        issues,
        "Jira outcome",
        decision.get("jira_outcome", {}).get("statuses"),
        expected_statuses,
    )

    _verify_authority_artifacts(decision, issues)
    _verify_source_evidence(decision, issues)
    _verify_git_audit(decision, issues)

    try:
        puyo_207_verification = verify_benchmark(PUYO_207_DIR, ticket="PUYO-207")
        if not puyo_207_verification["passed"]:
            issues.extend(
                f"PUYO-207 verifier: {issue}"
                for issue in puyo_207_verification["issues"]
            )
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        issues.append(f"PUYO-207 verifier failed: {error}")
    try:
        puyo_211_verification = verify_investigation(PUYO_211_DIR)
        if not puyo_211_verification["passed"]:
            issues.extend(
                f"PUYO-211 verifier: {issue}"
                for issue in puyo_211_verification["issues"]
            )
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        issues.append(f"PUYO-211 verifier failed: {error}")
    return {"passed": not issues, "issues": issues}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision", default=str(DEFAULT_DECISION_PATH))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = verify_decision(args.decision)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
