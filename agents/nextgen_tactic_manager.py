"""Public-only six-tactic rule policy and replaceable batch selector.

The scheduler owns the phase. Each worker receives a detached copy and returns
match evidence; only the scheduler can adopt a tactic or consume a piece.
"""

from __future__ import annotations

import copy
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from agents import nextgen_contracts as c
from agents.decision_flow import (
    DecisionContext,
    DecisionFlow,
    DecisionStep,
    DecisionStepContract,
    StepResult,
)
from agents.deep_chain_search_backend import (
    NativeLongHorizonSearchBackend,
    PythonLongHorizonSearchBackend,
)
from agents.long_horizon_search import LongHorizonSearchConfig
from agents.nextgen_response_search import PublicResponseProvider
from agents.nextgen_shared_search import (
    PreparedTemplateSearch,
    SharedSearchBatchBuilder,
    SharedSearchCache,
    scenario_provenance,
)
from agents.template_catalog import (
    MatchResult,
    TemplateCandidate,
    load_template_catalog,
    match_templates,
)
from puyo_env.nextgen_public_snapshot import derive_timing_summary

POLICY_TYPE = "nextgen_tactic_manager"
DEFAULT_CATALOG = (
    Path(__file__).resolve().parents[1] / "train/config/nextgen_templates.yaml"
)


@dataclass(frozen=True)
class RuleSelectorConfig:
    priority: tuple[str, ...] = (
        "cancel",
        "counter",
        "decisive_short_attack",
        "build_template",
        "fire_main",
        "build_main",
    )
    short_attack_ojama: int = 30
    opponent_occupied_cells: int = 48
    saturated_chain_count: int = 6

    def __post_init__(self):
        if len(self.priority) != 6 or set(self.priority) != set(c.TACTIC_IDS):
            raise ValueError("priority must contain exactly the six tactics")
        if (
            min(
                self.short_attack_ojama,
                self.opponent_occupied_cells,
                self.saturated_chain_count,
            )
            < 1
        ):
            raise ValueError("rule thresholds must be positive")


def evidence(row, name):
    return next((v.evidence.value for v in row.evidence if v.name == name), None)


class RuleTacticSelector:
    """Teacher preferences never mutate the structural batch mask."""

    def __init__(self, config=None):
        self.config = config or RuleSelectorConfig()

    def select(self, request, batch, features, timing):
        c.validate_request_batch(request, batch)
        if features.action_mask != batch.action_mask:
            raise ValueError("selector feature mask mismatch")
        rows = {row.tactic_id: row for row in batch.tactics}
        incoming = sum(p.amount for p in request.public.own.attack_packets)
        cancel = rows["cancel"]
        end, deadline = (
            evidence(cancel, "fire_end_upper"),
            evidence(cancel, "deadline_lower"),
        )
        immediate = timing.threat == "immediate"
        occupied = sum(
            bool(v) for row in request.public.opponent.visible_board for v in row
        )
        eligible = {
            "cancel": immediate
            and end is not None
            and deadline is not None
            and end <= deadline
            and (evidence(cancel, "canceled") or 0) >= incoming,
            "counter": immediate,
            "decisive_short_attack": (
                evidence(rows["decisive_short_attack"], "outgoing") or 0
            )
            >= self.config.short_attack_ojama
            and occupied >= self.config.opponent_occupied_cells,
            "build_template": request.control.phase.active,
            "fire_main": (evidence(rows["fire_main"], "chain_count") or 0)
            >= self.config.saturated_chain_count
            and evidence(rows["fire_main"], "fatal_rate") == 0,
            "build_main": True,
        }
        # When full cancellation is unavailable, compare the available response
        # witnesses on survival and firepower. Insufficient power stays unmasked.
        if immediate and not eligible["cancel"] and rows["cancel"].available:
            options = [rows[t] for t in ("cancel", "counter") if rows[t].available]
            preferred = min(
                options,
                key=lambda r: (
                    evidence(r, "fatal_rate")
                    if evidence(r, "fatal_rate") is not None
                    else 1,
                    -(evidence(r, "canceled") or 0),
                    -(evidence(r, "outgoing") or 0),
                    self.config.priority.index(r.tactic_id),
                ),
            ).tactic_id
            eligible["cancel"] = preferred == "cancel"
            eligible["counter"] = preferred == "counter"
        tactic = next(
            (t for t in self.config.priority if rows[t].available and eligible[t]), None
        )
        if tactic is None:
            raise ValueError("no reachable tactic; scheduler must handle this board")
        return c.Selection(
            tactic,
            rows[tactic].best_id,
            batch.digest,
            "rule",
            None,
            None,
            None,
            "rule_priority_" + tactic,
        )


def reconcile_phase(phase, result, public, history, identity):
    if not phase._initialized:
        phase.start(result, decision_id=str(identity.decision_id))
    phase.observe(
        public,
        history,
        player_id=identity.player_id,
        controllable=True,
        match_result=result,
        decision_id=str(identity.decision_id),
    )


def match_result_from_dict(value):
    values = dict(value)
    candidates = []
    for raw in values.pop("candidates"):
        item = dict(raw)
        item["binding"] = tuple(tuple(v) for v in item["binding"])
        item["witness_actions"] = tuple(item["witness_actions"])
        candidates.append(TemplateCandidate(**item))
    return MatchResult(tuple(candidates), **values)


def feature_summaries(request, batch, timing):
    phase = request.control.phase
    summaries = timing.feature_summaries()
    summaries.update(
        {
            "phase.active": float(phase.active),
            "phase.remaining_ratio": phase.remaining_decisions / phase.decision_limit,
            "phase.progress": phase.progress,
            "phase.fit": float(phase.fit_status == "fit"),
            "phase.no_fit": float(phase.fit_status == "no_fit"),
            "phase.fit_unknown": float(phase.fit_status == "unknown"),
            "phase.continuation": float(phase.tactic_continuation),
        }
    )
    for t in c.TACTIC_IDS:
        summaries[f"phase.previous.{t}"] = float(phase.previous_tactic == t)
    for row in batch.tactics:
        prefix = f"tactic.{row.tactic_id}."
        summaries.update(
            {
                prefix + "available": float(row.available),
                prefix + "known_witness": float(row.known_witness),
                prefix + "count": float(len(row.candidate_ids)),
            }
        )
        for feature, name in (
            ("firepower", "generated"),
            ("scenario_coverage", "scenario_coverage"),
            ("fatal_rate", "fatal_rate"),
        ):
            summaries[prefix + feature] = evidence(row, name)
        end, deadline = evidence(row, "fire_end_upper"), evidence(row, "deadline_lower")
        margin = None if end is None or deadline is None else deadline - end
        for label, predicate in (
            ("negative", lambda v: v < 0),
            ("zero", lambda v: v == 0),
            ("positive", lambda v: v > 0),
        ):
            summaries[prefix + "deadline_" + label] = (
                None if margin is None else float(predicate(margin))
            )
    return summaries


class PreparePhaseStep(DecisionStep):
    contract = DecisionStepContract(
        "template_phase",
        ("policy", "input"),
        ("request", "prepared_template", "phase", "template_key"),
        "Match once and select/reconcile the scheduler-owned template phase",
    )

    def run(self, context):
        policy, data = context.require("policy"), context.require("input")
        phase = copy.deepcopy(data["phase"])
        public, identity = data["public"], data["identity"]
        started = time.perf_counter()
        result = match_templates(
            policy.catalog,
            public.own.visible_board,
            public.own.known_pieces,
            node_budget=policy.profile.template_quota,
            binding_budget=policy.template_binding_budget,
            reachable_mask=data["execution"].reachable_mask,
            preferred_key=phase.candidate.key if phase.candidate is not None else None,
            prioritize_static_binding=True,
        )
        elapsed = time.perf_counter() - started
        reconcile_phase(phase, result, public, data["history"], identity)
        request = c.NextgenRequest(
            identity,
            public,
            data["execution"],
            c.ControlContext(
                phase.phase_snapshot(),
                policy.profile,
                policy.catalog.semantic_digest,
                scenario_provenance(public.own.known_pieces, policy.search_config),
            ),
        )
        key = phase.candidate.key if phase.candidate is not None else None
        prepared = PreparedTemplateSearch(
            c.semantic_digest(request), key, result, elapsed
        )
        return StepResult(
            {
                "request": request,
                "prepared_template": prepared,
                "phase": phase,
                "template_key": key,
            },
            len(result.candidates),
            phase.exit_reason or "active_template",
        )


class SharedBatchStep(DecisionStep):
    contract = DecisionStepContract(
        "shared_batch",
        ("policy", "input", "request", "prepared_template", "template_key"),
        ("search", "features", "timing_summary"),
        "Build the one fixed-budget batch for rule or RL",
    )

    def run(self, context):
        policy, request = context.require("policy"), context.require("request")
        timing = context.require("input")["timing"]
        builder = SharedSearchBatchBuilder(
            policy.backend,
            policy.search_config,
            template_catalog=policy.catalog,
            template_binding_budget=policy.template_binding_budget,
            response_provider=PublicResponseProvider(timing),
            shared_cache=policy.shared_cache,
        )
        search = builder.build(
            request,
            prepared_template=context.require("prepared_template"),
            template_key=context.require("template_key"),
        )
        summary = derive_timing_summary(
            request.public, timing, request_tick=request.execution.request_tick
        )
        features = c.build_features(
            feature_summaries(request, search.batch, summary), search.batch.action_mask
        )
        return StepResult(
            {"search": search, "features": features, "timing_summary": summary},
            len(search.batch.candidates),
        )


class SelectTacticStep(DecisionStep):
    contract = DecisionStepContract(
        "select_tactic",
        ("policy", "request", "search", "features", "timing_summary"),
        ("selection", "candidate", "diagnostics"),
        "Select a tactic and use the batch's fixed candidate rank",
    )

    def run(self, context):
        request, batch = context.require("request"), context.require("search").batch
        features = context.require("features")
        selection = context.require("policy").selector.select(
            request, batch, features, context.require("timing_summary")
        )
        candidate = selection.validate_batch(batch)
        diagnostics = c.Diagnostics(request, batch, features, selection, None)
        return StepResult(
            {
                "selection": selection,
                "candidate": candidate,
                "diagnostics": diagnostics,
            },
            len(batch.candidates),
            selection.reason,
        )


class NextgenTacticManagerPolicy:
    """Stateless worker; select_action only accepts the public scheduler envelope.

    A future RL selector implements select(request, batch, features, timing).
    It must return Selection and obey the same fixed-rank validation.
    """

    nextgen = True

    def __init__(
        self,
        *,
        catalog=None,
        template_catalog_path=None,
        seed=0,
        template_seed=None,
        search_config=None,
        profile=None,
        backend=None,
        selector=None,
        template_binding_budget=4096,
    ):
        self.catalog = catalog or load_template_catalog(
            template_catalog_path or DEFAULT_CATALOG
        )
        if not self.catalog.enabled or not any(
            t.enabled for t in self.catalog.templates
        ):
            raise ValueError("nextgen policy requires enabled templates")
        self.seed = 0 if seed is None else seed
        self.template_seed = self.seed if template_seed is None else template_seed
        if type(self.template_seed) is not int:
            raise ValueError("template_seed must be an integer")
        self.search_config = search_config or LongHorizonSearchConfig(
            depth=4,
            width=4,
            scenarios=1,
            minimum_chain_count=2,
            max_expanded_nodes=256,
            decision_seed=self.seed ^ 0x4E4753,
        )
        self.profile = profile or c.SearchProfile("nextgen_smoke", 256, 128, 256)
        # Use the existing strict release/ABI-checked adapter. Never silently
        # replace a missing/incompatible native extension with a slower policy.
        # Explicit Python remains available for parity and diagnostic runs.
        if backend is None or backend == "native":
            backend = NativeLongHorizonSearchBackend()
        elif backend == "python":
            backend = PythonLongHorizonSearchBackend()
        elif isinstance(backend, str):
            raise ValueError("nextgen backend must be native or python")
        self.backend = backend
        self.selector = selector or RuleTacticSelector()
        self.template_binding_budget = template_binding_budget
        self.flow = DecisionFlow(
            (PreparePhaseStep(), SharedBatchStep(), SelectTacticStep())
        )
        self.reset()

    def reset(self):
        self.last_context = None
        self.shared_cache = SharedSearchCache()

    def decision_input_identity(self, observation, info):
        return info["nextgen"]["identity"].to_dict()

    def select_action(self, observation, info):
        data = info["nextgen"]
        if data["public"].own.phase != "control" or not any(
            data["execution"].reachable_mask
        ):
            raise ValueError("nextgen policy requires a controllable reachable board")
        self.last_context = self.flow.execute(
            DecisionContext(
                str(data["identity"].decision_id),
                self.profile,
                {"policy": self, "input": data},
            )
        )
        # A multi-step counter plan is a witness, never an execution queue.
        return self.last_context.require("candidate").root_action

    @property
    def tactical_diagnostics(self):
        if self.last_context is None:
            return {}
        context = self.last_context
        return {
            "nextgen": context.require("diagnostics").to_dict(),
            "template_match": asdict(context.require("prepared_template").result),
            "template_phase": context.require("phase").diagnostics(),
            "template_selection": asdict(context.require("phase").selection),
            "decision_trace": context.trace.to_dict(),
            "search": context.require("search").diagnostics,
        }
