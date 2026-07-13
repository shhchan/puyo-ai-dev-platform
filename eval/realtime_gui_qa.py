"""Versioned quality gates for realtime GUI QA artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


GUI_QA_GATE_SCHEMA_VERSION = "puyo.gui_qa_gate.v1"
GUI_QA_PROFILES = ("playability", "attack", "stress", "deterministic")


@dataclass(frozen=True)
class RealtimeGuiQaCriteria:
    profile: str
    min_decisions_per_ai: int
    min_placements_per_ai: int
    max_idle_ratio_per_ai: float
    max_timeouts_per_ai: int
    max_deadline_misses_per_ai: int
    expected_latency_mode: str
    require_attack: bool = False
    require_terminal_outcome: bool = False
    max_mean_latency_ticks: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_PROFILE_CRITERIA = {
    "playability": RealtimeGuiQaCriteria(
        profile="playability",
        min_decisions_per_ai=4,
        min_placements_per_ai=3,
        max_idle_ratio_per_ai=0.75,
        max_timeouts_per_ai=0,
        max_deadline_misses_per_ai=0,
        expected_latency_mode="measured",
        require_terminal_outcome=True,
    ),
    "attack": RealtimeGuiQaCriteria(
        profile="attack",
        min_decisions_per_ai=4,
        min_placements_per_ai=3,
        max_idle_ratio_per_ai=0.75,
        max_timeouts_per_ai=0,
        max_deadline_misses_per_ai=0,
        expected_latency_mode="measured",
        require_attack=True,
    ),
    "stress": RealtimeGuiQaCriteria(
        profile="stress",
        min_decisions_per_ai=2,
        min_placements_per_ai=2,
        max_idle_ratio_per_ai=0.85,
        max_timeouts_per_ai=1,
        max_deadline_misses_per_ai=0,
        expected_latency_mode="measured",
        max_mean_latency_ticks=90.0,
    ),
    "deterministic": RealtimeGuiQaCriteria(
        profile="deterministic",
        min_decisions_per_ai=4,
        min_placements_per_ai=3,
        max_idle_ratio_per_ai=0.75,
        max_timeouts_per_ai=0,
        max_deadline_misses_per_ai=0,
        expected_latency_mode="configured",
    ),
}


def criteria_for_profile(profile: str) -> RealtimeGuiQaCriteria:
    try:
        return _PROFILE_CRITERIA[profile]
    except KeyError as exc:
        raise ValueError(f"qa profile must be one of: {GUI_QA_PROFILES}") from exc


def evaluate_realtime_gui_qa(
    criteria: RealtimeGuiQaCriteria,
    *,
    agents: Sequence[str],
    ticks: int,
    interrupted: bool,
    termination_reason: str,
    latency_mode: str,
    controller_diagnostics: Mapping[str, Mapping[str, Any]],
    attack_totals: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate one GUI run without treating tick-limit arrival as success."""

    failures: list[dict[str, Any]] = []
    if interrupted:
        failures.append(_failure("execution_interrupted", actual=True, expected=False))
    if criteria.require_terminal_outcome and termination_reason != "game_over":
        failures.append(
            _failure(
                "terminal_outcome_required",
                actual=termination_reason,
                expected="game_over",
            )
        )
    if latency_mode != criteria.expected_latency_mode:
        failures.append(
            _failure(
                "latency_mode_mismatch",
                actual=latency_mode,
                expected=criteria.expected_latency_mode,
            )
        )

    observed_agents: dict[str, dict[str, Any]] = {}
    denominator = max(1, int(ticks))
    for agent in agents:
        diagnostics = controller_diagnostics.get(agent, {})
        decisions = int(diagnostics.get("decisions_activated", 0))
        placements = int(diagnostics.get("placements_completed", 0))
        idle_ratio = float(diagnostics.get("idle_ticks", 0)) / denominator
        timeouts = int(diagnostics.get("timeouts", 0))
        deadline_misses = int(diagnostics.get("deadline_misses", 0))
        mean_latency = float(diagnostics.get("mean_inference_latency_ticks", 0.0))
        observed_agents[agent] = {
            "decisions_activated": decisions,
            "placements_completed": placements,
            "idle_ticks": int(diagnostics.get("idle_ticks", 0)),
            "idle_ratio": idle_ratio,
            "timeouts": timeouts,
            "deadline_misses": deadline_misses,
            "latency_mode": diagnostics.get("latency_mode", latency_mode),
            "mean_inference_latency_ticks": mean_latency,
        }
        _append_minimum_failure(
            failures,
            "minimum_decisions_not_met",
            agent,
            decisions,
            criteria.min_decisions_per_ai,
        )
        _append_minimum_failure(
            failures,
            "minimum_placements_not_met",
            agent,
            placements,
            criteria.min_placements_per_ai,
        )
        _append_maximum_failure(
            failures,
            "idle_ratio_exceeded",
            agent,
            idle_ratio,
            criteria.max_idle_ratio_per_ai,
        )
        _append_maximum_failure(
            failures,
            "timeout_limit_exceeded",
            agent,
            timeouts,
            criteria.max_timeouts_per_ai,
        )
        _append_maximum_failure(
            failures,
            "deadline_miss_limit_exceeded",
            agent,
            deadline_misses,
            criteria.max_deadline_misses_per_ai,
        )
        if criteria.max_mean_latency_ticks is not None:
            _append_maximum_failure(
                failures,
                "mean_latency_exceeded",
                agent,
                mean_latency,
                criteria.max_mean_latency_ticks,
            )

    generated_attack = sum(
        int(values.get("generated", 0)) for values in attack_totals.values()
    )
    if criteria.require_attack and generated_attack <= 0:
        failures.append(_failure("attack_not_generated", actual=generated_attack, expected="> 0"))

    return {
        "schema_version": GUI_QA_GATE_SCHEMA_VERSION,
        "enabled": True,
        "profile": criteria.profile,
        "passed": not failures,
        "criteria": criteria.to_dict(),
        "observed": {
            "ticks": int(ticks),
            "termination_reason": termination_reason,
            "latency_mode": latency_mode,
            "generated_attack": generated_attack,
            "agents": observed_agents,
        },
        "failure_reasons": failures,
    }


def disabled_gui_qa_gate() -> dict[str, Any]:
    return {
        "schema_version": GUI_QA_GATE_SCHEMA_VERSION,
        "enabled": False,
        "profile": None,
        "passed": None,
        "criteria": None,
        "observed": None,
        "failure_reasons": [],
    }


def _failure(
    code: str,
    *,
    actual: Any,
    expected: Any,
    agent: str | None = None,
) -> dict[str, Any]:
    result = {"code": code, "actual": actual, "expected": expected}
    if agent is not None:
        result["agent"] = agent
    return result


def _append_minimum_failure(
    failures: list[dict[str, Any]],
    code: str,
    agent: str,
    actual: int,
    expected: int,
) -> None:
    if actual < expected:
        failures.append(_failure(code, agent=agent, actual=actual, expected=f">= {expected}"))


def _append_maximum_failure(
    failures: list[dict[str, Any]],
    code: str,
    agent: str,
    actual: int | float,
    expected: int | float,
) -> None:
    if actual > expected:
        failures.append(_failure(code, agent=agent, actual=actual, expected=f"<= {expected}"))
