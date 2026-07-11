"""Run the versioned State Analyzer scenario dataset."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agents.state_analyzer import (
    ANALYZER_INPUT_SCHEMA_VERSION,
    AnalyzerInput,
    AttackPacket,
    PlayerSnapshot,
    StateAnalyzer,
)


SCENARIO_SCHEMA_VERSION = "puyo.state_analyzer.scenarios.v1"
SCENARIO_REPORT_SCHEMA_VERSION = "puyo.state_analyzer.scenario_report.v1"
DEFAULT_DATASET = Path(__file__).with_name("scenarios") / "v1_7_analyzer.json"
_COLOR_NAMES = {
    ".": "EMPTY",
    "R": "RED",
    "B": "BLUE",
    "G": "GREEN",
    "Y": "YELLOW",
    "P": "PURPLE",
    "O": "OJAMA",
}


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    category: str
    passed: bool
    checks: tuple[dict[str, Any], ...]
    diagnostics: dict[str, Any]


def load_scenarios(path: str | Path = DEFAULT_DATASET) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCENARIO_SCHEMA_VERSION:
        raise ValueError(f"unsupported scenario schema: {payload.get('schema_version')}")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("scenario dataset must contain a non-empty scenarios list")
    return scenarios


def scenario_input(scenario: Mapping[str, Any]) -> AnalyzerInput:
    return AnalyzerInput(
        own=_player(scenario["own"]),
        opponent=_player(scenario["opponent"]),
        turn=max(0, int(scenario.get("turn", 0))),
        tick=max(0, int(scenario.get("tick", 0))),
        policy_deadline=max(0, int(scenario.get("policy_deadline", 0))),
        schema_version=ANALYZER_INPUT_SCHEMA_VERSION,
    )


def evaluate_scenarios(
    scenarios: list[dict[str, Any]] | None = None,
    *,
    analyzer: StateAnalyzer | None = None,
) -> list[ScenarioResult]:
    selected = scenarios if scenarios is not None else load_scenarios()
    state_analyzer = analyzer or StateAnalyzer()
    results = []
    for scenario in selected:
        diagnostics = state_analyzer.analyze(scenario_input(scenario)).to_dict()
        checks = tuple(_check(diagnostics, expectation) for expectation in scenario.get("expectations", ()))
        results.append(
            ScenarioResult(
                name=str(scenario["name"]),
                category=str(scenario["category"]),
                passed=bool(checks) and all(check["passed"] for check in checks),
                checks=checks,
                diagnostics=diagnostics,
            )
        )
    return results


def build_report(results: list[ScenarioResult]) -> dict[str, Any]:
    passed = sum(result.passed for result in results)
    return {
        "schema_version": SCENARIO_REPORT_SCHEMA_VERSION,
        "summary": {
            "scenarios": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "pass_rate": passed / max(1, len(results)),
        },
        "results": [
            {
                "name": result.name,
                "category": result.category,
                "passed": result.passed,
                "checks": list(result.checks),
                "diagnostics": result.diagnostics,
            }
            for result in results
        ],
    }


def write_report(path: str | Path, report: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Evaluate State Analyzer diagnostics on fixed scenarios.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--json", dest="json_path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    results = evaluate_scenarios(load_scenarios(args.dataset))
    report = build_report(results)
    for result in results:
        label = "PASS" if result.passed else "FAIL"
        print(f"{label} {result.category}: {result.name}")
        for check in result.checks:
            if not check["passed"]:
                print(
                    f"  {check['path']} {check['op']} {check['expected']!r}; "
                    f"actual={check['actual']!r}"
                )
    summary = report["summary"]
    print(f"scenarios={summary['scenarios']} passed={summary['passed']} failed={summary['failed']}")
    if args.json_path:
        write_report(args.json_path, report)
    return 0 if summary["failed"] == 0 else 1


def _player(value: Mapping[str, Any]) -> PlayerSnapshot:
    rows = value["board"]
    board = tuple(tuple(_COLOR_NAMES[color] for color in row) for row in rows)
    current = tuple(_COLOR_NAMES[color] for color in value["current_pair"])
    next_pairs = tuple(tuple(_COLOR_NAMES[color] for color in pair) for pair in value["next_pairs"])
    return PlayerSnapshot(
        board=board,
        current_pair=current,
        next_pairs=next_pairs,
        incoming=tuple(
            AttackPacket(int(packet["amount"]), int(packet["deadline"]))
            for packet in value.get("incoming", ())
        ),
        score=int(value.get("score", 0)),
        sent_ojama_total=int(value.get("sent_ojama_total", 0)),
        canceled_ojama_total=int(value.get("canceled_ojama_total", 0)),
        received_ojama_total=int(value.get("received_ojama_total", 0)),
    )


def _resolve_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


def _check(diagnostics: Mapping[str, Any], expectation: Mapping[str, Any]) -> dict[str, Any]:
    path = str(expectation["path"])
    operator = str(expectation["op"])
    expected = expectation["value"]
    actual = _resolve_path(diagnostics, path)
    operations = {
        "eq": lambda: actual == expected,
        "gte": lambda: actual >= expected,
        "lte": lambda: actual <= expected,
        "len_gte": lambda: len(actual) >= int(expected),
        "contains": lambda: expected in actual,
        "any_field_contains": lambda: any(
            expected["value"] in item[expected["field"]] for item in actual
        ),
    }
    if operator not in operations:
        raise ValueError(f"unsupported expectation operator: {operator}")
    return {
        "path": path,
        "op": operator,
        "expected": expected,
        "actual": actual,
        "passed": bool(operations[operator]()),
    }


if __name__ == "__main__":
    raise SystemExit(main())
