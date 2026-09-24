"""One bounded decision batch, shared by all six next-generation tactics.

Hidden cells are an explicitly incomplete public estimate, never exact witnesses.
The scheduler must revalidate roots against its authoritative board at activation.
Response search is injected (PUYO-250); selection only reads the finished batch.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, replace
from typing import Protocol

from agents import nextgen_contracts as c
from agents.chain_structure import ChainStructureConfig, load_chain_structure_config
from agents.compact_search import CompactSearchState, legal_action_indices
from agents.deep_chain_search_backend import (
    LongHorizonBackendRequest,
    LongHorizonSearchBackend,
)
from agents.long_horizon_search import (
    LongHorizonSearchConfig,
    LongHorizonSearchResult,
    ScenarioPairSequence,
    build_scenario_sequences_from_known_pairs,
)
from agents.template_catalog import MatchResult, TemplateCatalog, match_templates
from src.core.constants import GRID_HEIGHT, PuyoColor

SCENARIO_GENERATOR_VERSION = "nextgen.long_horizon_scenarios.v1"


def scenario_provenance(known_pieces, config: LongHorizonSearchConfig):
    """Public helper used when constructing the request's scenario contract."""
    sequences = _sequences(known_pieces, config) if known_pieces else ()
    return c.ScenarioProvenance(
        SCENARIO_GENERATOR_VERSION,
        c.semantic_digest(known_pieces),
        "shared",
        c.semantic_digest(tuple(s.to_dict() for s in sequences)),
    )


def _pairs(known_pieces):
    return tuple(
        tuple(c.PUBLIC_CELL_TO_COLOR[v] for v in pair) for pair in known_pieces
    )


def _sequences(known_pieces, config):
    return build_scenario_sequences_from_known_pairs(
        _pairs(known_pieces),
        scenarios=config.scenarios,
        depth=config.depth,
        decision_seed=config.resolved_decision_seed,
        sampling_mode=config.future_sampling_mode,
    )


@dataclass
class ResponseBudget:
    """PUYO-250 must charge BEFORE each extra transition; no borrowed quota.

    Reading shared results is free. Each newly expanded placement/resolution is
    one node, including failed/fatal transitions. Feature calls are counted too.
    """

    quota: int
    nodes: int = 0
    feature_evaluations: int = 0

    def consume(self, *, feature_evaluations: int = 0) -> bool:
        if type(feature_evaluations) is not int or feature_evaluations < 0:
            raise ValueError("invalid response feature counter")
        if self.nodes >= self.quota:
            return False
        self.nodes += 1
        self.feature_evaluations += feature_evaluations
        return True


@dataclass(frozen=True)
class ResponseSearchContext:
    request: c.NextgenRequest
    root_state: CompactSearchState
    legal_roots: tuple[int, ...]
    sequences: tuple[ScenarioPairSequence, ...]
    shared_result: LongHorizonSearchResult | None
    board_complete: bool


@dataclass(frozen=True)
class ResponseProposal:
    plan: tuple[c.PlanStep, ...]
    tactics: tuple[c.TacticId, ...]
    evidence: tuple[c.NamedEvidence, ...]
    priority: float = 0.0


@dataclass(frozen=True)
class ResponseDropOutcome:
    columns: tuple[int, ...]
    placed: int
    remaining_packets: tuple[c.PublicAttackPacket, ...]
    game_over: bool
    conditional: bool = False


@dataclass(frozen=True)
class ResponseTrace:
    """Public conditional search evidence; not a wire candidate or receipt."""

    plan: tuple[c.PlanStep, ...]
    tactic: c.TacticId
    first_drop_count: int
    first_drop_columns: tuple[int, ...]
    remaining_packets: tuple[c.PublicAttackPacket, ...]
    following_drop_count: int
    conditional: bool
    fire_start: tuple[int | None, int | None]
    fire_end: tuple[int | None, int | None]
    following_drops: tuple[ResponseDropOutcome, ...] = ()
    provenance: str = "public_response_search.v1; replan_after_observed_drop"

    @property
    def first_drop_after_step(self) -> int | None:
        """One-based placement boundary; the final step is the next decision."""
        return len(self.plan) - 1 if self.first_drop_count else None


@dataclass(frozen=True)
class ResponseSearchResult:
    proposals: tuple[ResponseProposal, ...] = ()
    status: c.EvidenceStatus = "unsupported"
    cutoff_reason: str | None = None
    cancel_reason: c.MaskReason = "unsupported"
    counter_reason: c.MaskReason = "unsupported"
    traces: tuple[ResponseTrace, ...] = ()


class ResponseProvider(Protocol):
    def search(
        self, context: ResponseSearchContext, budget: ResponseBudget
    ) -> ResponseSearchResult: ...


@dataclass(frozen=True)
class SharedSearchBatchExecution:
    batch: c.CandidateBatch
    shared_result: LongHorizonSearchResult | None
    template_result: MatchResult | None
    response_result: ResponseSearchResult
    provenance: c.ScenarioProvenance
    root_rankings: tuple[int, ...]
    diagnostics: dict

    @property
    def deterministic_digest(self):
        # Native timings/implementation provenance are telemetry, not semantics.
        return c.semantic_digest(
            {
                "batch": self.batch.digest,
                "shared": None
                if self.shared_result is None
                else self.shared_result.deterministic_digest,
                "template": self.template_result,
                "response": self.response_result,
                "provenance": self.provenance,
                "root_rankings": self.root_rankings,
            }
        )

    def select(self, tactic_id: c.TacticId) -> c.Candidate:
        """Fixed ranking only: no backend or provider call after tactic choice."""
        if tactic_id not in c.TACTIC_IDS:
            raise ValueError("unknown tactic")
        row = self.batch.tactics[c.TACTIC_IDS.index(tactic_id)]
        if not row.available:
            raise ValueError(f"masked tactic: {row.mask_reason}")
        return next(v for v in self.batch.candidates if v.candidate_id == row.best_id)


def _public_state(request):
    board = request.public.own.visible_board
    if len(board) > GRID_HEIGHT:
        raise ValueError("public board exceeds engine height")
    colors = (
        PuyoColor.RED,
        PuyoColor.BLUE,
        PuyoColor.GREEN,
        PuyoColor.YELLOW,
        PuyoColor.PURPLE,
        PuyoColor.OJAMA,
    )
    planes = [0] * len(colors)
    for y, row in enumerate(reversed(board)):
        for x, cell in enumerate(row):
            if cell:
                planes[colors.index(c.PUBLIC_CELL_TO_COLOR[cell])] |= 1 << (y * 6 + x)
    complete = len(board) == GRID_HEIGHT and all(
        v is not None for row in board for v in row
    )
    return CompactSearchState(
        planes=tuple(planes),
        all_clear_bonus_pending=request.public.own.all_clear_bonus_pending,
        game_over=request.public.own.phase in ("gameover", "game_over", "ended"),
    ), complete


def _evidence(**values):
    return tuple(
        c.NamedEvidence(name, c.NumericEvidence(*value))
        for name, value in values.items()
    )


def _plan(actions, pieces):
    inverse = {color: index for index, color in enumerate(c.PUBLIC_CELL_TO_COLOR)}
    return tuple(
        c.PlanStep(
            action,
            tuple(inverse[v] for v in pieces.pair_at(i)),
            "public_known" if i < pieces.known_pair_count else "sampled_future",
        )
        for i, action in enumerate(actions)
    )


@dataclass(frozen=True)
class PreparedTemplateSearch:
    """Matcher output bound to the exact post-lifecycle request and shape.

    Used by the scheduler policy to select/reconcile a phase before building
    the batch, without spending the template quota twice.
    """

    request_digest: str
    template_key: tuple | None
    result: MatchResult
    elapsed_seconds: float = 0.0

    def validate(self, request, catalog, template_key):
        if not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0:
            raise ValueError("invalid prepared template elapsed time")
        if (
            catalog is None
            or catalog.semantic_digest != request.control.template_config_hash
        ):
            raise ValueError("prepared template catalog mismatch")
        if (
            self.request_digest != c.semantic_digest(request)
            or self.template_key != template_key
        ):
            raise ValueError("prepared template request/phase mismatch")
        if (
            not 0
            <= self.result.coverage_nodes
            <= request.control.search_profile.template_quota
        ):
            raise ValueError("prepared template quota exceeded")
        if request.control.phase.active and not any(
            v.key == template_key and v.template_id == request.control.phase.template_id
            for v in self.result.candidates
        ):
            raise ValueError("prepared template selected shape missing")


class SharedSearchBatchBuilder:
    """Fixed search configuration; each request owns fresh non-transferable quotas."""

    def __init__(
        self,
        backend: LongHorizonSearchBackend,
        config: LongHorizonSearchConfig,
        *,
        evaluator_config: ChainStructureConfig | None = None,
        template_catalog: TemplateCatalog | None = None,
        template_binding_budget: int = 4096,
        response_provider: ResponseProvider | None = None,
    ):
        if type(template_binding_budget) is not int or template_binding_budget < 0:
            raise ValueError("invalid template binding budget")
        self.backend = backend
        self.config = config
        self.evaluator_config = evaluator_config or load_chain_structure_config()
        self.template_catalog = template_catalog
        self.template_binding_budget = template_binding_budget
        self.response_provider = response_provider

    def build(
        self,
        request: c.NextgenRequest,
        *,
        prepared_template=None,
        template_key=None,
    ) -> SharedSearchBatchExecution:
        started = time.perf_counter()
        profile = request.control.search_profile
        known = request.public.own.known_pieces
        provenance = scenario_provenance(known, self.config)
        if request.control.scenario_provenance != provenance:
            raise ValueError("scenario provenance mismatch")
        if self.template_catalog is not None and (
            self.template_catalog.semantic_digest
            != request.control.template_config_hash
        ):
            raise ValueError("template config hash mismatch")
        if prepared_template is not None:
            if not isinstance(prepared_template, PreparedTemplateSearch):
                raise TypeError("prepared template must be a bound matcher result")
            prepared_template.validate(request, self.template_catalog, template_key)
        state, board_complete = _public_state(request)
        roots = (
            () if not known or state.game_over else tuple(legal_action_indices(state))
        )
        reachable = tuple(a for a in roots if request.execution.reachable_mask[a])
        sequences = _sequences(known, self.config) if known else ()
        shared = None
        backend_diagnostics = {}
        cutoffs = []
        if not board_complete:
            cutoffs.append("public_board_incomplete")
        stage_started = time.perf_counter()
        if reachable and profile.shared_quota:
            config = replace(self.config, max_expanded_nodes=profile.shared_quota)
            h = c.semantic_digest(asdict(config))
            execution = self.backend.search(
                LongHorizonBackendRequest(
                    state,
                    _pairs(known),
                    config,
                    self.evaluator_config,
                    profile.profile_id,
                    "1",
                    "nextgen.shared.v1",
                    h,
                    self.evaluator_config.weight_version,
                    c.semantic_digest(self.evaluator_config.to_dict()),
                    "nextgen.fixed_quotas.v1",
                    c.semantic_digest(profile),
                    int(c.semantic_digest(request.identity)[:16], 16),
                    self.backend.backend_id == "native",
                    False,
                )
            )
            shared, backend_diagnostics = execution.result, dict(execution.diagnostics)
            if tuple(s.to_dict() for s in shared.scenario_sequences) != tuple(
                s.to_dict() for s in sequences
            ):
                raise ValueError("backend scenario provenance mismatch")
            if tuple(sorted(shared.evidence_by_action)) != tuple(sorted(roots)):
                raise ValueError("backend root coverage mismatch")
            if not 0 <= shared.counters.expanded_nodes <= profile.shared_quota:
                raise ValueError("backend exceeded shared quota")
            if shared.counters.budget_exhausted:
                cutoffs.append("shared_quota")
        elif reachable:
            cutoffs.append("shared_quota")
        stage_ms = {"shared": (time.perf_counter() - stage_started) * 1000}
        stage_started = time.perf_counter()
        template = None
        phase = request.control.phase
        if prepared_template is not None:
            template = prepared_template.result
            if template.cutoff:
                cutoffs.append("template_quota")
        elif reachable and phase.active:
            if self.template_catalog is None:
                cutoffs.append("template_provider_unavailable")
            else:
                template = match_templates(
                    self.template_catalog,
                    request.public.own.visible_board,
                    known,
                    node_budget=profile.template_quota,
                    binding_budget=self.template_binding_budget,
                    reachable_mask=request.execution.reachable_mask,
                )
                if template.cutoff:
                    cutoffs.append("template_quota")
        stage_ms["template"] = (time.perf_counter() - stage_started) * 1000
        if prepared_template is not None:
            stage_ms["template"] += prepared_template.elapsed_seconds * 1000
        stage_started = time.perf_counter()
        response_budget = ResponseBudget(profile.response_quota)
        response = ResponseSearchResult()
        threat = any(
            p.amount and p.landed_tick is None
            for p in request.public.own.attack_packets
        )
        if reachable and self.response_provider is not None:
            response = self.response_provider.search(
                ResponseSearchContext(
                    request,
                    state,
                    roots,
                    sequences,
                    shared,
                    board_complete,
                ),
                response_budget,
            )
            if not 0 <= response_budget.nodes <= profile.response_quota:
                raise ValueError("provider exceeded response quota")
            if response.cutoff_reason:
                cutoffs.append(response.cutoff_reason)
            elif response.status != "evaluated":
                cutoffs.append("response_" + response.status)
        elif threat:
            cutoffs.append("response_provider_unavailable")
        stage_ms["response"] = (time.perf_counter() - stage_started) * 1000
        assumptions = c.CandidateAssumptions(
            request.public.digest,
            provenance.scenario_digest,
            request.execution.timing_digest,
            c.semantic_digest(profile),
            request.control.template_config_hash,
        )
        entries = {}

        def add(plan, tactics, evidence, key, *, fallback=False):
            if not plan or plan[0].action not in roots:
                raise ValueError("proposal has no legal root")
            for i, step in enumerate(plan):
                if step.provenance == "public_known" and (
                    i >= len(known) or step.piece != known[i]
                ):
                    raise ValueError("proposal known piece mismatch")
            cid = c.candidate_id(request.identity, plan, assumptions)
            if cid in entries:
                old = entries[cid]
                tactics = tuple(t for t in c.TACTIC_IDS if t in tactics or t in old[1])
                evidence = tuple({e.name: e for e in (*old[2], *evidence)}.values())
                key = min(key, old[3])
                fallback = fallback and old[4]
            entries[cid] = (plan, tactics, evidence, key, fallback)

        root_rankings = (
            tuple(v.root_action for v in shared.ranked_roots) if shared else roots
        )
        source = "visible_exact" if board_complete else "public_estimate"
        for root_rank, action in enumerate(root_rankings):
            root = shared.evidence_by_action[action] if shared else None
            fallback = root is None or root.evaluated_scenarios == 0
            evidence = ()
            if root is not None:
                status = (
                    "evaluated"
                    if board_complete
                    and all(v.search_complete for v in root.scenario_values)
                    else "partial"
                )
                evaluated = [v for v in root.scenario_values if v.evaluated]
                evidence = _evidence(
                    scenario_support=(
                        root.support / root.requested_scenarios,
                        status,
                        "sampled_future",
                    ),
                    scenario_coverage=(root.coverage, status, "sampled_future"),
                    scenario_worst=(
                        root.worst_chain_score if evaluated else None,
                        status if evaluated else "not_evaluated",
                        "sampled_future",
                    ),
                    # Observed fatal node frequency is NOT a safety probability.
                    fatal_rate=(
                        sum(v.game_over_nodes for v in evaluated)
                        / max(1, sum(v.expanded_nodes for v in evaluated))
                        if evaluated
                        else None,
                        "partial" if evaluated else "not_evaluated",
                        "sampled_future",
                    ),
                )
            # A root-only build plan cannot promise its sampled representative future.
            add(
                (c.PlanStep(action, known[0], "public_known"),),
                ("build_main",),
                evidence,
                (3, root_rank, action),
                fallback=fallback,
            )
            if root is None:
                continue
            for scenario in root.scenario_values:
                seq = next(
                    s for s in sequences if s.scenario_id == scenario.scenario_id
                )
                for fire in (scenario.best_fire, scenario.selected_fire):
                    if fire is None or fire.depth > len(known):
                        continue
                    tactics = (
                        ("fire_main", "decisive_short_attack")
                        if fire.chain_count >= self.config.minimum_chain_count
                        else ("decisive_short_attack",)
                    )
                    add(
                        _plan(fire.path, seq),
                        tactics,
                        _evidence(
                            chain_count=(
                                fire.chain_count,
                                "evaluated" if board_complete else "partial",
                                source,
                            ),
                            score=(
                                fire.chain_score,
                                "evaluated" if board_complete else "partial",
                                source,
                            ),
                            fire_depth=(
                                fire.depth,
                                "evaluated" if board_complete else "partial",
                                source,
                            ),
                        ),
                        (2, -fire.chain_score, fire.depth, fire.path),
                    )
        if template is not None and phase.active:
            for value in sorted(template.candidates, key=lambda v: (-v.score, v.key)):
                if value.template_id != phase.template_id or value.fit_status != "fit":
                    continue
                if template_key is not None and value.key != template_key:
                    continue
                selected_template = next(
                    t
                    for t in self.template_catalog.templates
                    if t.id == value.template_id
                )
                variant = next(
                    v for v in selected_template.variants if v.id == value.variant_id
                )
                progress = (
                    value.score
                    * sum(v.weight for v in selected_template.variants)
                    / variant.weight
                )
                add(
                    _plan(value.witness_actions, sequences[0]),
                    ("build_template",),
                    _evidence(
                        template_progress=(
                            progress,
                            "evaluated" if board_complete else "partial",
                            source,
                        )
                    ),
                    (1, -value.score, value.key),
                )
        for value in response.proposals:
            if not value.tactics or any(
                t not in ("cancel", "counter", "decisive_short_attack")
                for t in value.tactics
            ):
                raise ValueError("invalid response provider tactic")
            if not math.isfinite(value.priority):
                raise ValueError("invalid response priority")
            if response.status not in ("evaluated", "partial"):
                raise ValueError("unevaluated response proposals")
            add(
                value.plan,
                value.tactics,
                value.evidence,
                (0, -value.priority, tuple(s.action for s in value.plan)),
            )
        # Preserve explicit missingness for every wire evidence dimension.
        for cid, (plan, tactics, evidence, key, fallback) in tuple(entries.items()):
            by_name = {e.name: e for e in evidence}
            evidence = tuple(
                by_name.get(
                    name,
                    c.NamedEvidence(
                        name,
                        c.NumericEvidence(None, "not_evaluated", source),
                    ),
                )
                for name in c.EVIDENCE_NAMES
            )
            entries[cid] = (plan, tactics, evidence, key, fallback)
        candidates = tuple(
            c.Candidate(
                request.identity,
                cid,
                value[0][0].action,
                value[0],
                value[1],
                rank,
                True,
                value[0][0].action in reachable,
                assumptions,
                value[2],
                value[4],
            )
            for rank, (cid, value) in enumerate(
                sorted(entries.items(), key=lambda item: (item[1][3], item[0]))
            )
        )
        rows = []
        for tactic in c.TACTIC_IDS:
            members = tuple(
                v for v in candidates if tactic in v.tactics and v.root_reachable
            )
            reason = "not_found_within_budget"
            status = "partial" if cutoffs else "evaluated"
            if not roots:
                reason, status = "no_legal_root", "not_evaluated"
            elif not reachable:
                reason, status = "no_reachable_root", "not_evaluated"
            elif tactic == "build_template" and not phase.active:
                reason, status = "inactive_phase", "not_evaluated"
            elif tactic == "build_template" and template is None:
                reason, status = "unsupported", "unsupported"
            elif tactic in ("cancel", "counter"):
                reason = (
                    (
                        response.cancel_reason
                        if tactic == "cancel"
                        else response.counter_reason
                    )
                    if threat
                    else "no_public_threat"
                )
                status = response.status if threat else "not_evaluated"
            if members and status not in ("evaluated", "partial"):
                status = "partial"
            rows.append(
                c.TacticSummary(
                    tactic,
                    tuple(v.candidate_id for v in members),
                    members[0].candidate_id if members else None,
                    bool(members),
                    "available" if members else reason,
                    status,
                    board_complete
                    and any(
                        not v.fallback
                        and all(s.provenance == "public_known" for s in v.plan)
                        and (
                            tactic not in ("cancel", "counter")
                            or any(
                                e.name
                                == (
                                    "canceled"
                                    if tactic == "cancel"
                                    else "counter_after_first_drop"
                                )
                                and e.evidence.source == "visible_exact"
                                and e.evidence.status == "evaluated"
                                for e in v.evidence
                            )
                        )
                        for v in members
                    ),
                    members[0].evidence if members else (),
                )
            )
        if not reachable:
            candidates = ()
            cutoffs = ["no_legal_root" if not roots else "no_reachable_root"]
        counters = c.SearchCounters(
            shared.counters.expanded_nodes if shared else 0,
            template.coverage_nodes if template else 0,
            response_budget.nodes,
            (shared.counters.evaluated_nodes + 1 if shared else 0)
            + (template.feature_evaluations if template else 0)
            + response_budget.feature_evaluations,
            (
                time.perf_counter()
                - started
                + (prepared_template.elapsed_seconds if prepared_template else 0.0)
            )
            * 1000,
        )
        batch = c.CandidateBatch(
            request.identity,
            "unavailable" if not reachable else "partial" if cutoffs else "complete",
            ";".join(dict.fromkeys(cutoffs)) if cutoffs else None,
            len(known),
            candidates,
            tuple(rows),
            counters,
        )
        return SharedSearchBatchExecution(
            batch,
            shared,
            template,
            response,
            provenance,
            root_rankings,
            {
                "stage_elapsed_ms": stage_ms,
                "backend": backend_diagnostics,
                "quotas": profile.to_dict(),
                "board_complete": board_complete,
                "root_legality": "public_board_estimate_intersect_reachable_mask",
                "safety_guarantee": False,
                "template_binding_budget": self.template_binding_budget,
            },
        )
