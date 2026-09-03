//! Native deterministic long-horizon beam search.
//!
//! The search owns every hot-loop value. Python supplies one compact state,
//! the visible pair queue, versioned scalar configuration, and a deterministic
//! completion seed. Only bounded evidence for roots and representatives is
//! serialized after aggregation.

use std::cmp::Ordering;
use std::mem::MaybeUninit;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering as AtomicOrdering};
use std::sync::{Arc, Mutex, OnceLock, mpsc};
use std::thread;
use std::time::Instant;

use sha2::{Digest, Sha256};

use crate::chain_structure::{
    EvaluationConfig, EvaluationEvidence, EvaluationHot, MAX_EVIDENCE_CANDIDATES,
    QuiescenceCandidate, WEIGHT_COUNT, evaluate_evidence, evaluate_hot,
};
use crate::chain_structure_batch::encode_evaluation;
use crate::compact::{
    ACTION_COUNT, CompactState, Pair, SearchStateKey, TransitionHotResult, legal_actions_mask,
    transition_hot_into,
};
use crate::{
    BUILD_PROFILE, COMPILER, ContractError, ContractResult, MAX_RESPONSE_BYTES,
    REQUEST_EVALUATOR_CONFIG_TAG, REQUEST_EXECUTION_TAG, REQUEST_KNOWN_PAIRS_TAG,
    REQUEST_ROOT_STATE_TAG, REQUEST_SEARCH_CONFIG_TAG, Reader, SOURCE_REVISION, TARGET,
};

pub(crate) const MAX_SEARCH_DEPTH: usize = 64;
const MAX_SEARCH_WIDTH: usize = 1_024;
const MAX_EXPANDED_NODES: u64 = 10_000_000;
const FIRE_CLASS_COUNT: usize = 6;
const NORMAL_GENERATED_COLOR_COUNT: usize = 4;
const REPRESENTATIVE_BAGS: [[usize; 4]; 6] = [
    [0, 1, 2, 3],
    [0, 2, 1, 3],
    [0, 3, 1, 2],
    [1, 2, 0, 3],
    [1, 3, 0, 2],
    [2, 3, 0, 1],
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SamplingMode {
    SeededAuthoritative,
    LegacyFixedSix,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ExecutionMode {
    OracleOne,
    ScenarioSix,
}

impl ExecutionMode {
    const fn name(self) -> &'static str {
        match self {
            Self::OracleOne => "oracle-1",
            Self::ScenarioSix => "scenario-6",
        }
    }

    const fn thread_count(self) -> u16 {
        match self {
            Self::OracleOne => 1,
            Self::ScenarioSix => 6,
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct SearchConfig {
    depth: usize,
    width: usize,
    scenarios: usize,
    minimum_chain_count: u8,
    terminal_fire_chain_count: u16,
    max_expanded_nodes: u64,
    root_survivor_quota: usize,
    decision_seed: Option<u64>,
    winning_score_threshold: Option<u64>,
    premature_target_gap_penalty: f64,
    sampling_mode: SamplingMode,
    record_and_stop: bool,
    forced_safety: bool,
    use_transposition_table: bool,
}

#[derive(Clone, Copy, Debug)]
struct ExecutionConfig {
    mode: ExecutionMode,
    max_response_bytes: usize,
}

#[derive(Clone)]
pub(crate) struct Request {
    root_state: CompactState,
    known_pairs: Vec<Pair>,
    search: SearchConfig,
    evaluator: EvaluationConfig,
    execution: ExecutionConfig,
}

#[derive(Clone, Copy, Debug)]
struct ScenarioSequence {
    scenario_id: u8,
    sample_index: u8,
    rollout_seed: Option<u64>,
    pairs: [Pair; MAX_SEARCH_DEPTH],
}

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum FireClass {
    Unavailable = 0,
    Premature = 1,
    Quiet = 2,
    ForcedSafety = 3,
    Target = 4,
    Winning = 5,
}

impl FireClass {
    const fn allowed(self) -> bool {
        matches!(self, Self::ForcedSafety | Self::Target | Self::Winning)
    }
}

#[derive(Clone, Copy, Debug)]
struct Node {
    state: CompactState,
    root_action: u8,
    scenario_id: u8,
    path: [u8; MAX_SEARCH_DEPTH],
    path_len: u8,
    evaluator_score: f64,
    evaluation: EvaluationHot,
    parent_evaluation: EvaluationHot,
    transition: TransitionHotResult,
    cumulative_action_score: u64,
    last_action: u8,
}

impl Node {
    fn root(
        state: CompactState,
        root_action: u8,
        scenario_id: u8,
        evaluation: EvaluationHot,
        parent_evaluation: EvaluationHot,
        transition: TransitionHotResult,
    ) -> Self {
        let mut path = [0_u8; MAX_SEARCH_DEPTH];
        path[0] = root_action;
        Self {
            state,
            root_action,
            scenario_id,
            path,
            path_len: 1,
            evaluator_score: evaluation.score,
            evaluation,
            parent_evaluation,
            transition,
            cumulative_action_score: transition.score_delta,
            last_action: root_action,
        }
    }

    fn child(
        parent: &Self,
        state: CompactState,
        action: u8,
        evaluation: EvaluationHot,
        transition: TransitionHotResult,
    ) -> ContractResult<Self> {
        let mut path = parent.path;
        let path_len = usize::from(parent.path_len);
        if path_len >= MAX_SEARCH_DEPTH {
            return Err(ContractError::resource("native search path arena is full"));
        }
        path[path_len] = action;
        Ok(Self {
            state,
            root_action: parent.root_action,
            scenario_id: parent.scenario_id,
            path,
            path_len: parent.path_len + 1,
            evaluator_score: evaluation.score,
            evaluation,
            parent_evaluation: parent.evaluation,
            transition,
            cumulative_action_score: parent
                .cumulative_action_score
                .checked_add(transition.score_delta)
                .ok_or_else(|| ContractError::resource("cumulative action score overflow"))?,
            last_action: action,
        })
    }

    fn path(&self) -> &[u8] {
        &self.path[..usize::from(self.path_len)]
    }
}

#[derive(Clone, Copy, Debug)]
struct Fire {
    scenario_id: u8,
    class: FireClass,
    chain_count: u8,
    chain_score: u64,
    state: CompactState,
    path: [u8; MAX_SEARCH_DEPTH],
    path_len: u8,
    terminal: bool,
    terminal_score: f64,
    terminal_breakdown: [f64; 5],
    evaluation: EvaluationHot,
}

impl Fire {
    fn path(&self) -> &[u8] {
        &self.path[..usize::from(self.path_len)]
    }

    fn trigger_action(self) -> u8 {
        self.path[usize::from(self.path_len) - 1]
    }
}

#[derive(Clone, Copy, Debug, Default)]
struct Coverage {
    depth: u16,
    candidate_count: u32,
    retained_count: u32,
    shortfall: u8,
}

#[derive(Clone, Debug)]
struct Tracker {
    active: bool,
    root_action: u8,
    scenario_id: u8,
    evaluated: bool,
    search_complete: bool,
    reached_depth: u16,
    max_chain_count: u8,
    max_chain_score: u64,
    best_fire: Option<Fire>,
    fires_by_class: [Option<Fire>; FIRE_CLASS_COUNT],
    terminals_by_class: [Option<Node>; FIRE_CLASS_COUNT],
    fire_count: u32,
    terminal_fire_count: u32,
    best_survivor: Option<Node>,
    expanded_nodes: u64,
    pruned_nodes: u64,
    transposition_hits: u64,
    invalid_nodes: u64,
    game_over_nodes: u64,
    coverage: [Coverage; MAX_SEARCH_DEPTH],
    coverage_len: u8,
    truncation_reason: u8,
}

impl Tracker {
    fn inactive() -> Self {
        Self {
            active: false,
            root_action: 0,
            scenario_id: 0,
            evaluated: false,
            search_complete: true,
            reached_depth: 0,
            max_chain_count: 0,
            max_chain_score: 0,
            best_fire: None,
            fires_by_class: [None; FIRE_CLASS_COUNT],
            terminals_by_class: [None; FIRE_CLASS_COUNT],
            fire_count: 0,
            terminal_fire_count: 0,
            best_survivor: None,
            expanded_nodes: 0,
            pruned_nodes: 0,
            transposition_hits: 0,
            invalid_nodes: 0,
            game_over_nodes: 0,
            coverage: [Coverage::default(); MAX_SEARCH_DEPTH],
            coverage_len: 0,
            truncation_reason: 0,
        }
    }

    fn new(root_action: u8, scenario_id: u8) -> Self {
        Self {
            active: true,
            root_action,
            scenario_id,
            ..Self::inactive()
        }
    }

    fn record_fire(&mut self, fire: Fire) {
        self.fire_count += 1;
        self.terminal_fire_count += u32::from(fire.terminal);
        self.max_chain_count = self.max_chain_count.max(fire.chain_count);
        self.max_chain_score = self.max_chain_score.max(fire.chain_score);
        if self
            .best_fire
            .is_none_or(|previous| fire_official_cmp(&fire, &previous).is_gt())
        {
            self.best_fire = Some(fire);
        }
        let slot = &mut self.fires_by_class[fire.class as usize];
        if slot.is_none_or(|previous| fire_rank_cmp(&fire, &previous).is_gt()) {
            *slot = Some(fire);
        }
    }

    fn record_terminal(&mut self, node: Node, class: FireClass) {
        let Some(best) = self.fires_by_class[class as usize] else {
            return;
        };
        if best.path() == node.path() {
            self.terminals_by_class[class as usize] = Some(node);
        }
    }

    fn record_survivor(&mut self, node: Node) {
        if self
            .best_survivor
            .is_none_or(|previous| survivor_cmp(&node, &previous).is_lt())
        {
            self.best_survivor = Some(node);
        }
        self.reached_depth = self.reached_depth.max(u16::from(node.path_len));
    }

    fn record_coverage(
        &mut self,
        depth: usize,
        candidate_count: usize,
        retained_count: usize,
        quota: usize,
        budget_exhausted: bool,
    ) {
        let shortfall = if retained_count >= quota {
            0
        } else if budget_exhausted {
            1
        } else if candidate_count >= quota {
            2
        } else if self.terminal_fire_count > 0 {
            3
        } else if self.game_over_nodes > 0 {
            4
        } else if self.invalid_nodes > 0 {
            5
        } else {
            6
        };
        let slot = usize::from(self.coverage_len);
        debug_assert!(slot < MAX_SEARCH_DEPTH);
        self.coverage[slot] = Coverage {
            depth: depth as u16,
            candidate_count: candidate_count as u32,
            retained_count: retained_count as u32,
            shortfall,
        };
        self.coverage_len += 1;
    }

    fn selected_class(&self) -> FireClass {
        if self.fires_by_class[FireClass::Winning as usize].is_some() {
            FireClass::Winning
        } else if self.fires_by_class[FireClass::Target as usize].is_some() {
            FireClass::Target
        } else if self.fires_by_class[FireClass::ForcedSafety as usize].is_some() {
            FireClass::ForcedSafety
        } else if self.best_survivor.is_some() {
            FireClass::Quiet
        } else if self.fires_by_class[FireClass::Premature as usize].is_some() {
            FireClass::Premature
        } else {
            FireClass::Unavailable
        }
    }

    fn selected_fire(&self) -> Option<Fire> {
        self.fires_by_class[self.selected_class() as usize]
    }

    fn representative(&self) -> Option<Node> {
        let selected = self.selected_class();
        if selected == FireClass::Quiet {
            self.best_survivor
        } else {
            self.terminals_by_class[selected as usize].or(self.best_survivor)
        }
    }

    fn finish(&mut self, budget_exhausted: bool, target_depth: usize) {
        if budget_exhausted && self.evaluated && usize::from(self.reached_depth) < target_depth {
            self.search_complete = false;
            self.truncation_reason = 1;
        } else if !self.evaluated {
            self.search_complete = false;
            self.truncation_reason = if budget_exhausted { 1 } else { 2 };
        }
    }
}

#[derive(Clone, Copy, Debug, Default)]
struct Counters {
    expanded_nodes: u64,
    generated_nodes: u64,
    evaluated_nodes: u64,
    invalid_nodes: u64,
    pruned_nodes: u64,
    terminal_fire_nodes: u64,
    game_over_nodes: u64,
    transposition_hits: u64,
    reached_depth: u64,
    budget_exhausted: bool,
}

impl Counters {
    fn add(&mut self, other: Self) {
        self.expanded_nodes += other.expanded_nodes;
        self.generated_nodes += other.generated_nodes;
        self.evaluated_nodes += other.evaluated_nodes;
        self.invalid_nodes += other.invalid_nodes;
        self.pruned_nodes += other.pruned_nodes;
        self.terminal_fire_nodes += other.terminal_fire_nodes;
        self.game_over_nodes += other.game_over_nodes;
        self.transposition_hits += other.transposition_hits;
        self.reached_depth = self.reached_depth.max(other.reached_depth);
        self.budget_exhausted |= other.budget_exhausted;
    }
}

#[derive(Clone, Debug)]
struct ScenarioResult {
    trackers: Vec<Tracker>,
    counters: Counters,
    peak_live_nodes: u64,
    tt_capacity: u64,
}

#[derive(Clone, Copy, Debug, Default)]
struct Telemetry {
    arena_capacity_nodes: u64,
    tt_capacity_slots: u64,
    peak_live_nodes: u64,
    scenario_jobs: u64,
    scenario_reruns: u64,
    pool_reuses: u64,
    search_ns: u64,
    aggregation_ns: u64,
    serialization_ns: u64,
}

pub(crate) struct Output {
    pub(crate) decision: Vec<u8>,
    pub(crate) counters: Vec<u8>,
    pub(crate) root_evidence: Vec<u8>,
    pub(crate) representatives: Vec<u8>,
    pub(crate) diagnostics: Vec<u8>,
    pub(crate) provenance: Vec<u8>,
    pub(crate) max_response_bytes: usize,
}

#[derive(Default)]
struct Bytes(Vec<u8>);

impl Bytes {
    fn u8(&mut self, value: u8) {
        self.0.push(value);
    }

    fn u16(&mut self, value: u16) {
        self.0.extend_from_slice(&value.to_le_bytes());
    }

    fn u32(&mut self, value: u32) {
        self.0.extend_from_slice(&value.to_le_bytes());
    }

    fn u64(&mut self, value: u64) {
        self.0.extend_from_slice(&value.to_le_bytes());
    }

    fn f64(&mut self, value: f64) {
        self.u64(value.to_bits());
    }

    fn raw(&mut self, value: &[u8]) {
        self.0.extend_from_slice(value);
    }

    fn string(&mut self, value: &str) {
        let bytes = value.as_bytes();
        self.u16(u16::try_from(bytes.len()).expect("bounded native result string"));
        self.raw(bytes);
    }
}

fn parse_known_pairs(data: &[u8]) -> ContractResult<Vec<Pair>> {
    let mut reader = Reader::new(data, REQUEST_KNOWN_PAIRS_TAG);
    let count = reader.u16("known pair count")?;
    let mut pairs = Vec::with_capacity(usize::from(count));
    for _ in 0..count {
        let axis = reader.u8("axis color")?;
        let child = reader.u8("child color")?;
        pairs.push(
            Pair::from_ids(axis, child).map_err(|error| {
                ContractError::invalid(REQUEST_KNOWN_PAIRS_TAG, error.to_string())
            })?,
        );
    }
    reader.finish()?;
    Ok(pairs)
}

fn parse_search(data: &[u8]) -> ContractResult<(SearchConfig, u16, u16)> {
    let mut reader = Reader::new(data, REQUEST_SEARCH_CONFIG_TAG);
    let depth = usize::from(reader.u16("search depth")?);
    let width = usize::from(reader.u16("search width")?);
    let scenarios = usize::from(reader.u8("scenario count")?);
    let minimum_chain_count = reader.u8("minimum chain count")?;
    let terminal_fire_chain_count = reader.u16("terminal fire chain count")?;
    let max_expanded_nodes = u64::from(reader.u32("max expanded nodes")?);
    let root_survivor_quota = usize::from(reader.u16("root survivor quota")?);
    let pair_cursor = reader.u16("pair cursor")?;
    let scenario_cursor = reader.u16("scenario cursor")?;
    let seed_present = reader.u8("seed present")?;
    let winning_present = reader.u8("winning score present")?;
    let use_transposition_table = reader.u8("transposition table")?;
    let reserved = reader.u8("search reserved")?;
    let raw_seed = reader.u64("decision seed")?;
    let raw_winning_score = reader.u64("winning score")?;
    let premature_target_gap_penalty = reader.f64("premature penalty")?;
    let sampling_mode = match reader.string("future sampling mode")?.as_str() {
        "seeded-authoritative" => SamplingMode::SeededAuthoritative,
        "legacy-fixed-six" => SamplingMode::LegacyFixedSix,
        _ => {
            return Err(ContractError::unsupported(
                REQUEST_SEARCH_CONFIG_TAG,
                "unsupported future sampling mode",
            ));
        }
    };
    let record_and_stop = match reader.string("terminal fire rule")?.as_str() {
        "continue" => false,
        "record_and_stop" => true,
        _ => {
            return Err(ContractError::unsupported(
                REQUEST_SEARCH_CONFIG_TAG,
                "unsupported terminal fire rule",
            ));
        }
    };
    let forced_safety = match reader.string("fire context")?.as_str() {
        "safe_build" => false,
        "forced_safety" => true,
        _ => {
            return Err(ContractError::unsupported(
                REQUEST_SEARCH_CONFIG_TAG,
                "unsupported fire context",
            ));
        }
    };
    let _profile_name = reader.string("profile name")?;
    let _profile_version = reader.string("profile version")?;
    let _config_version = reader.string("config version")?;
    reader.finish()?;
    if depth == 0
        || depth > MAX_SEARCH_DEPTH
        || width == 0
        || width > MAX_SEARCH_WIDTH
        || !(1..=6).contains(&scenarios)
        || minimum_chain_count == 0
        || terminal_fire_chain_count == 0
        || max_expanded_nodes == 0
        || max_expanded_nodes > MAX_EXPANDED_NODES
        || root_survivor_quota == 0
        || !matches!(seed_present, 0 | 1)
        || !matches!(winning_present, 0 | 1)
        || !matches!(use_transposition_table, 0 | 1)
        || reserved != 0
        || (seed_present == 0 && raw_seed != 0)
        || (winning_present == 0 && raw_winning_score != 0)
        || (winning_present == 1 && raw_winning_score == 0)
        || premature_target_gap_penalty < 0.0
    {
        return Err(ContractError::invalid(
            REQUEST_SEARCH_CONFIG_TAG,
            "native search configuration is outside its bounded v1 limits",
        ));
    }
    Ok((
        SearchConfig {
            depth,
            width,
            scenarios,
            minimum_chain_count,
            terminal_fire_chain_count,
            max_expanded_nodes,
            root_survivor_quota,
            decision_seed: (seed_present == 1).then_some(raw_seed),
            winning_score_threshold: (winning_present == 1).then_some(raw_winning_score),
            premature_target_gap_penalty,
            sampling_mode,
            record_and_stop,
            forced_safety,
            use_transposition_table: use_transposition_table == 1,
        },
        pair_cursor,
        scenario_cursor,
    ))
}

fn parse_evaluator(data: &[u8]) -> ContractResult<EvaluationConfig> {
    let mut reader = Reader::new(data, REQUEST_EVALUATOR_CONFIG_TAG);
    let schema = reader.string("evaluator schema version")?;
    let feature = reader.string("evaluator feature version")?;
    let _weight_version = reader.string("evaluator weight version")?;
    let max_added_puyos = reader.u32("max added puyos")?;
    let max_pattern_nodes = reader.u32("max pattern nodes")?;
    let max_resolution_nodes = reader.u32("max resolution nodes")?;
    let max_candidates = reader.u32("max candidates")?;
    let weight_count = reader.u16("weight count")?;
    let reserved = reader.u16("evaluator reserved")?;
    if schema != "puyo.chain_structure_weights.v1"
        || feature != "puyo.chain_structure_features.v1"
        || usize::from(weight_count) != WEIGHT_COUNT
        || reserved != 0
    {
        return Err(ContractError::incompatible(
            REQUEST_EVALUATOR_CONFIG_TAG,
            "native evaluator schema identity does not match",
        ));
    }
    let mut weights = [0.0_f64; WEIGHT_COUNT];
    for value in &mut weights {
        *value = reader.f64("evaluator weight")?;
    }
    let fatal_score = reader.f64("fatal score")?;
    reader.finish()?;
    let digest = Sha256::digest(data);
    let config = EvaluationConfig {
        max_added_puyos: u8::try_from(max_added_puyos).map_err(|_| {
            ContractError::invalid(REQUEST_EVALUATOR_CONFIG_TAG, "max added puyos exceeds u8")
        })?,
        max_pattern_nodes,
        max_resolution_nodes,
        max_candidates: u8::try_from(max_candidates).map_err(|_| {
            ContractError::invalid(REQUEST_EVALUATOR_CONFIG_TAG, "max candidates exceeds u8")
        })?,
        weights,
        fatal_score,
        version_key: u64::from_le_bytes(digest[..8].try_into().expect("SHA-256 prefix")),
    };
    config
        .validate()
        .map_err(|message| ContractError::invalid(REQUEST_EVALUATOR_CONFIG_TAG, message))?;
    Ok(config)
}

fn parse_execution(data: &[u8]) -> ContractResult<ExecutionConfig> {
    let mut reader = Reader::new(data, REQUEST_EXECUTION_TAG);
    let mode = match reader.string("execution mode")?.as_str() {
        "oracle-1" => ExecutionMode::OracleOne,
        "scenario-6" => ExecutionMode::ScenarioSix,
        _ => {
            return Err(ContractError::unsupported(
                REQUEST_EXECUTION_TAG,
                "unsupported native execution mode",
            ));
        }
    };
    let detail_flags = reader.u32("response detail flags")?;
    let max_response_bytes = usize::try_from(reader.u32("maximum response bytes")?)
        .map_err(|_| ContractError::invalid(REQUEST_EXECUTION_TAG, "response limit overflow"))?;
    let python_callbacks = reader.u8("Python callback flag")?;
    let scalar_fallback = reader.u8("scalar fallback requirement")?;
    let reserved = reader.u16("execution reserved")?;
    reader.finish()?;
    if detail_flags & !0x7 != 0
        || max_response_bytes == 0
        || max_response_bytes > MAX_RESPONSE_BYTES
        || python_callbacks != 0
        || scalar_fallback != 1
        || reserved != 0
    {
        return Err(ContractError::invalid(
            REQUEST_EXECUTION_TAG,
            "invalid native execution boundary control",
        ));
    }
    Ok(ExecutionConfig {
        mode,
        max_response_bytes,
    })
}

pub(crate) fn parse(
    root_state: &[u8],
    known_pairs: &[u8],
    search: &[u8],
    evaluator: &[u8],
    execution: &[u8],
) -> ContractResult<Request> {
    let root_state = CompactState::from_bytes(root_state)
        .map_err(|error| ContractError::invalid(REQUEST_ROOT_STATE_TAG, error.to_string()))?;
    let known_pairs = parse_known_pairs(known_pairs)?;
    let (search, pair_cursor, scenario_cursor) = parse_search(search)?;
    if pair_cursor != 0 || scenario_cursor != 0 {
        return Err(ContractError::unsupported(
            REQUEST_SEARCH_CONFIG_TAG,
            "native v1 decisions require zero pair/scenario cursors",
        ));
    }
    Ok(Request {
        root_state,
        known_pairs,
        search,
        evaluator: parse_evaluator(evaluator)?,
        execution: parse_execution(execution)?,
    })
}

/// CPython's MT19937 initialization and getrandbits behavior for non-negative
/// integer seeds. Future queues must match `random.Random(seed).choice(...)`
/// byte-for-byte without importing or calling Python.
struct PythonRandom {
    state: [u32; 624],
    index: usize,
}

impl PythonRandom {
    fn new(seed: u64) -> Self {
        let key = if seed > u64::from(u32::MAX) {
            vec![seed as u32, (seed >> 32) as u32]
        } else {
            vec![seed as u32]
        };
        let mut value = Self {
            state: [0_u32; 624],
            index: 624,
        };
        value.init_by_array(&key);
        value
    }

    fn init_genrand(&mut self, seed: u32) {
        self.state[0] = seed;
        for index in 1..624 {
            self.state[index] = 1_812_433_253_u32
                .wrapping_mul(self.state[index - 1] ^ (self.state[index - 1] >> 30))
                .wrapping_add(index as u32);
        }
        self.index = 624;
    }

    fn init_by_array(&mut self, key: &[u32]) {
        self.init_genrand(19_650_218);
        let mut state_index = 1_usize;
        let mut key_index = 0_usize;
        for _ in 0..624.max(key.len()) {
            self.state[state_index] = (self.state[state_index]
                ^ (self.state[state_index - 1] ^ (self.state[state_index - 1] >> 30))
                    .wrapping_mul(1_664_525))
            .wrapping_add(key[key_index])
            .wrapping_add(key_index as u32);
            state_index += 1;
            key_index += 1;
            if state_index >= 624 {
                self.state[0] = self.state[623];
                state_index = 1;
            }
            if key_index >= key.len() {
                key_index = 0;
            }
        }
        for _ in 0..623 {
            self.state[state_index] = (self.state[state_index]
                ^ (self.state[state_index - 1] ^ (self.state[state_index - 1] >> 30))
                    .wrapping_mul(1_566_083_941))
            .wrapping_sub(state_index as u32);
            state_index += 1;
            if state_index >= 624 {
                self.state[0] = self.state[623];
                state_index = 1;
            }
        }
        self.state[0] = 0x8000_0000;
    }

    fn next_u32(&mut self) -> u32 {
        const MATRIX_A: u32 = 0x9908_b0df;
        const UPPER_MASK: u32 = 0x8000_0000;
        const LOWER_MASK: u32 = 0x7fff_ffff;
        if self.index >= 624 {
            for index in 0..227 {
                let value = (self.state[index] & UPPER_MASK) | (self.state[index + 1] & LOWER_MASK);
                self.state[index] = self.state[index + 397]
                    ^ (value >> 1)
                    ^ if value & 1 == 0 { 0 } else { MATRIX_A };
            }
            for index in 227..623 {
                let value = (self.state[index] & UPPER_MASK) | (self.state[index + 1] & LOWER_MASK);
                self.state[index] = self.state[index - 227]
                    ^ (value >> 1)
                    ^ if value & 1 == 0 { 0 } else { MATRIX_A };
            }
            let value = (self.state[623] & UPPER_MASK) | (self.state[0] & LOWER_MASK);
            self.state[623] =
                self.state[396] ^ (value >> 1) ^ if value & 1 == 0 { 0 } else { MATRIX_A };
            self.index = 0;
        }
        let mut value = self.state[self.index];
        self.index += 1;
        value ^= value >> 11;
        value ^= (value << 7) & 0x9d2c_5680;
        value ^= (value << 15) & 0xefc6_0000;
        value ^= value >> 18;
        value
    }

    fn getrandbits(&mut self, bits: u32) -> u32 {
        debug_assert!((1..=32).contains(&bits));
        self.next_u32() >> (32 - bits)
    }

    fn randbelow(&mut self, upper: u32) -> u32 {
        debug_assert!(upper > 0);
        let bits = 32 - upper.leading_zeros();
        loop {
            let value = self.getrandbits(bits);
            if value < upper {
                return value;
            }
        }
    }

    fn shuffle(&mut self, values: &mut [u8]) {
        for index in (1..values.len()).rev() {
            let selected = self.randbelow((index + 1) as u32) as usize;
            values.swap(index, selected);
        }
    }
}

fn rollout_seed(decision_seed: u64, sample_index: usize) -> u64 {
    let payload = format!(
        "{{\"decision_seed\":{decision_seed},\"derivation\":\"sha256-decision-seed-sample-index-v1\",\"sample_index\":{sample_index},\"schema_version\":\"puyo.future_tsumo_sampling.v1\"}}"
    );
    let digest = Sha256::digest(payload.as_bytes());
    u64::from_be_bytes(digest[..8].try_into().expect("SHA-256 prefix"))
}

fn generated_pair(colors: [u8; 2]) -> ContractResult<Pair> {
    Pair::from_ids(colors[0], colors[1]).map_err(|error| {
        ContractError::internal(format!("generated future pair is invalid: {error}"))
    })
}

fn build_sequences(request: &Request) -> ContractResult<Vec<ScenarioSequence>> {
    let depth = request.search.depth;
    let known_count = request.known_pairs.len().min(depth);
    let default_pair = request.known_pairs.first().copied().ok_or_else(|| {
        ContractError::invalid(REQUEST_KNOWN_PAIRS_TAG, "known pair queue is empty")
    })?;
    let mut result = Vec::with_capacity(request.search.scenarios);
    match request.search.sampling_mode {
        SamplingMode::SeededAuthoritative => {
            let decision_seed = request.search.decision_seed.unwrap_or(0);
            for sample_index in 0..request.search.scenarios {
                let seed = rollout_seed(decision_seed, sample_index);
                let mut rng = PythonRandom::new(seed);
                let mut pairs = [default_pair; MAX_SEARCH_DEPTH];
                pairs[..known_count].copy_from_slice(&request.known_pairs[..known_count]);
                for pair in &mut pairs[known_count..depth] {
                    *pair = generated_pair([
                        rng.randbelow(NORMAL_GENERATED_COLOR_COUNT as u32) as u8 + 1,
                        rng.randbelow(NORMAL_GENERATED_COLOR_COUNT as u32) as u8 + 1,
                    ])?;
                }
                result.push(ScenarioSequence {
                    scenario_id: sample_index as u8,
                    sample_index: sample_index as u8,
                    rollout_seed: Some(seed),
                    pairs,
                });
            }
        }
        SamplingMode::LegacyFixedSix => {
            let mut scenario_ids = [0_u8, 1, 2, 3, 4, 5];
            let mut rng = request.search.decision_seed.map(PythonRandom::new);
            if let Some(generator) = rng.as_mut() {
                generator.shuffle(&mut scenario_ids);
            }
            for (sample_index, scenario_id) in scenario_ids
                .iter()
                .copied()
                .take(request.search.scenarios)
                .enumerate()
            {
                let mut colors = [1_u8, 2, 3, 4];
                if let Some(generator) = rng.as_mut() {
                    generator.shuffle(&mut colors);
                }
                let bag = REPRESENTATIVE_BAGS[usize::from(scenario_id)];
                let hidden = [
                    generated_pair([colors[bag[0]], colors[bag[1]]])?,
                    generated_pair([colors[bag[2]], colors[bag[3]]])?,
                ];
                let mut pairs = [default_pair; MAX_SEARCH_DEPTH];
                pairs[..known_count].copy_from_slice(&request.known_pairs[..known_count]);
                for (offset, pair) in pairs[known_count..depth].iter_mut().enumerate() {
                    *pair = hidden[offset % hidden.len()];
                }
                result.push(ScenarioSequence {
                    scenario_id,
                    sample_index: sample_index as u8,
                    rollout_seed: None,
                    pairs,
                });
            }
        }
    }
    Ok(result)
}

fn survivor_cmp(left: &Node, right: &Node) -> Ordering {
    right
        .evaluator_score
        .partial_cmp(&left.evaluator_score)
        .unwrap_or(Ordering::Equal)
        .then_with(|| left.root_action.cmp(&right.root_action))
        .then_with(|| left.state.to_bytes().cmp(&right.state.to_bytes()))
        .then_with(|| left.last_action.cmp(&right.last_action))
        .then_with(|| left.path().cmp(right.path()))
}

fn state_sha256(state: CompactState) -> [u8; 32] {
    Sha256::digest(state.to_bytes()).into()
}

fn inverse_path_cmp(left: &[u8], right: &[u8]) -> Ordering {
    for (left_action, right_action) in left.iter().zip(right) {
        let order = right_action.cmp(left_action);
        if !order.is_eq() {
            return order;
        }
    }
    right.len().cmp(&left.len())
}

fn fire_official_cmp(left: &Fire, right: &Fire) -> Ordering {
    left.chain_score
        .cmp(&right.chain_score)
        .then_with(|| left.chain_count.cmp(&right.chain_count))
        .then_with(|| right.path_len.cmp(&left.path_len))
        .then_with(|| right.trigger_action().cmp(&left.trigger_action()))
        .then_with(|| state_sha256(left.state).cmp(&state_sha256(right.state)))
        .then_with(|| inverse_path_cmp(left.path(), right.path()))
}

fn fire_rank_cmp(left: &Fire, right: &Fire) -> Ordering {
    (left.class as u8)
        .cmp(&(right.class as u8))
        .then_with(|| {
            left.terminal_score
                .partial_cmp(&right.terminal_score)
                .unwrap_or(Ordering::Equal)
        })
        .then_with(|| fire_official_cmp(left, right))
}

#[derive(Clone, Copy)]
struct TableEntry {
    key: SearchStateKey,
    candidate_index: usize,
}

struct TranspositionTable {
    slots: Vec<Option<TableEntry>>,
    mask: usize,
}

struct PruneWorkspace {
    quota_capacity: usize,
    quota_indices: Vec<usize>,
    candidate_counts: [usize; ACTION_COUNT],
    retained: Vec<bool>,
}

impl PruneWorkspace {
    fn new(config: SearchConfig, maximum_candidates: usize) -> Self {
        let quota_capacity = config.root_survivor_quota.min(config.width);
        Self {
            quota_capacity,
            quota_indices: vec![usize::MAX; ACTION_COUNT * quota_capacity],
            candidate_counts: [0; ACTION_COUNT],
            retained: Vec::with_capacity(maximum_candidates.max(ACTION_COUNT)),
        }
    }

    fn reset(&mut self, node_count: usize) {
        self.quota_indices.fill(usize::MAX);
        self.candidate_counts.fill(0);
        self.retained.resize(node_count, false);
        self.retained.fill(false);
    }
}

impl TranspositionTable {
    fn new(max_candidates: usize) -> ContractResult<Self> {
        let requested = max_candidates
            .checked_mul(2)
            .and_then(usize::checked_next_power_of_two)
            .ok_or_else(|| ContractError::resource("transposition table capacity overflow"))?
            .max(8);
        Ok(Self {
            slots: vec![None; requested],
            mask: requested - 1,
        })
    }

    fn clear(&mut self) {
        self.slots.fill(None);
    }

    fn find_or_insert(
        &mut self,
        key: SearchStateKey,
        candidate_index: usize,
    ) -> ContractResult<Option<usize>> {
        let mut slot = key.table_hash() as usize & self.mask;
        for _ in 0..self.slots.len() {
            match self.slots[slot] {
                Some(entry) if entry.key == key => return Ok(Some(entry.candidate_index)),
                Some(_) => slot = (slot + 1) & self.mask,
                None => {
                    self.slots[slot] = Some(TableEntry {
                        key,
                        candidate_index,
                    });
                    return Ok(None);
                }
            }
        }
        Err(ContractError::resource(
            "collision-safe transposition table is full",
        ))
    }

    fn capacity(&self) -> usize {
        self.slots.len()
    }
}

fn classify_fire(config: SearchConfig, transition: TransitionHotResult) -> FireClass {
    if transition.chain_count == 0 {
        FireClass::Quiet
    } else if config
        .winning_score_threshold
        .is_some_and(|threshold| transition.score_delta >= threshold)
    {
        FireClass::Winning
    } else if transition.chain_count >= config.minimum_chain_count {
        FireClass::Target
    } else if config.forced_safety {
        FireClass::ForcedSafety
    } else {
        FireClass::Premature
    }
}

#[allow(clippy::too_many_arguments)]
fn make_fire(
    config: SearchConfig,
    scenario_id: u8,
    state: CompactState,
    path: [u8; MAX_SEARCH_DEPTH],
    path_len: u8,
    transition: TransitionHotResult,
    evaluation: EvaluationHot,
    terminal: bool,
) -> Fire {
    let class = classify_fire(config, transition);
    let structural_score = if evaluation.score.is_finite() {
        evaluation.score
    } else {
        -1_000_000_000_000.0
    };
    let target_gap = config
        .minimum_chain_count
        .saturating_sub(transition.chain_count);
    let target_gap_penalty = if class == FireClass::Premature {
        -config.premature_target_gap_penalty * f64::from(target_gap)
    } else {
        0.0
    };
    let official_score = if class.allowed() {
        transition.score_delta as f64
    } else {
        0.0
    };
    let total = structural_score + target_gap_penalty + official_score;
    Fire {
        scenario_id,
        class,
        chain_count: transition.chain_count,
        chain_score: transition.score_delta,
        state,
        path,
        path_len,
        terminal,
        terminal_score: total,
        terminal_breakdown: [
            structural_score,
            official_score,
            f64::from(target_gap),
            target_gap_penalty,
            total,
        ],
        evaluation,
    }
}

fn consume_budget(counters: &mut Counters, limit: u64) -> bool {
    if counters.expanded_nodes >= limit {
        counters.budget_exhausted = true;
        return false;
    }
    counters.expanded_nodes += 1;
    true
}

fn transition_hot(
    state: &CompactState,
    pair: Pair,
    action: u8,
) -> ContractResult<(CompactState, TransitionHotResult)> {
    let mut child = MaybeUninit::uninit();
    let mut transition = MaybeUninit::uninit();
    transition_hot_into(state, pair, action, &mut child, &mut transition).map_err(|error| {
        ContractError::internal(format!("native search transition failed: {error}"))
    })?;
    // SAFETY: transition_hot_into initializes both slots before returning Ok.
    Ok(unsafe { (child.assume_init(), transition.assume_init()) })
}

fn initialize_trackers(root_mask: u32, scenario_id: u8) -> Vec<Tracker> {
    (0..ACTION_COUNT)
        .map(|action| {
            if root_mask & (1_u32 << action) != 0 {
                Tracker::new(action as u8, scenario_id)
            } else {
                Tracker::inactive()
            }
        })
        .collect()
}

fn prune_survivors(
    nodes: &mut [Node],
    beam: &mut Vec<Node>,
    workspace: &mut PruneWorkspace,
    depth: usize,
    config: SearchConfig,
    trackers: &mut [Tracker],
    counters: &mut Counters,
) {
    nodes.sort_unstable_by(survivor_cmp);
    workspace.reset(nodes.len());
    for (index, node) in nodes.iter().enumerate() {
        let root_action = usize::from(node.root_action);
        let root_index = workspace.candidate_counts[root_action];
        if root_index < workspace.quota_capacity {
            workspace.quota_indices[root_action * workspace.quota_capacity + root_index] = index;
        }
        workspace.candidate_counts[root_action] += 1;
    }
    beam.clear();
    for quota_index in 0..workspace.quota_capacity {
        for (root_action, tracker) in trackers.iter().enumerate() {
            if !tracker.active || beam.len() >= config.width {
                continue;
            }
            let index =
                workspace.quota_indices[root_action * workspace.quota_capacity + quota_index];
            if index == usize::MAX {
                continue;
            }
            workspace.retained[index] = true;
            beam.push(nodes[index]);
        }
    }
    for (index, node) in nodes.iter().copied().enumerate() {
        if beam.len() >= config.width {
            break;
        }
        if workspace.retained[index] {
            continue;
        }
        workspace.retained[index] = true;
        beam.push(node);
    }
    let mut retained_by_root = [0_usize; ACTION_COUNT];
    for node in beam.iter() {
        retained_by_root[usize::from(node.root_action)] += 1;
    }
    for root_action in 0..ACTION_COUNT {
        if !trackers[root_action].active {
            continue;
        }
        trackers[root_action].record_coverage(
            depth,
            workspace.candidate_counts[root_action],
            retained_by_root[root_action],
            config.root_survivor_quota,
            counters.budget_exhausted,
        );
    }
    for (index, node) in nodes.iter().copied().enumerate() {
        if workspace.retained[index] {
            continue;
        }
        trackers[usize::from(node.root_action)].pruned_nodes += 1;
        counters.pruned_nodes += 1;
    }
    for node in beam.iter().copied() {
        trackers[usize::from(node.root_action)].record_survivor(node);
    }
}

fn run_scenario(
    request: &Request,
    sequence: ScenarioSequence,
    root_evaluation: EvaluationHot,
    expanded_limit: u64,
    cancellation: &AtomicBool,
) -> ContractResult<ScenarioResult> {
    let config = request.search;
    let root_mask = legal_actions_mask(&request.root_state);
    let mut trackers = initialize_trackers(root_mask, sequence.scenario_id);
    let maximum_candidates = config
        .width
        .checked_mul(ACTION_COUNT)
        .ok_or_else(|| ContractError::resource("native candidate arena capacity overflow"))?;
    let mut candidates = Vec::with_capacity(maximum_candidates.max(ACTION_COUNT));
    let mut beam = Vec::with_capacity(config.width);
    let mut next_beam = Vec::with_capacity(config.width);
    let mut table = TranspositionTable::new(maximum_candidates)?;
    let mut prune_workspace = PruneWorkspace::new(config, maximum_candidates);
    let mut counters = Counters::default();
    let mut peak_live_nodes = 0_u64;

    for (action, tracker) in trackers.iter_mut().enumerate() {
        if root_mask & (1_u32 << action) == 0 {
            continue;
        }
        if cancellation.load(AtomicOrdering::Relaxed) {
            return Err(ContractError::internal(
                "native scenario search was cancelled",
            ));
        }
        if !consume_budget(&mut counters, expanded_limit) {
            break;
        }
        tracker.evaluated = true;
        tracker.expanded_nodes += 1;
        tracker.reached_depth = 1;
        let (state, transition) =
            transition_hot(&request.root_state, sequence.pairs[0], action as u8)?;
        if !transition.valid() {
            counters.invalid_nodes += 1;
            tracker.invalid_nodes += 1;
            continue;
        }
        counters.generated_nodes += 1;
        counters.reached_depth = counters.reached_depth.max(1);
        let terminal = config.record_and_stop
            && u16::from(transition.chain_count) >= config.terminal_fire_chain_count;
        let mut evaluation = None;
        if transition.chain_count > 0 {
            let value = evaluate_hot(
                &state,
                &request.evaluator,
                Some(&root_evaluation),
                Some(transition),
                config.minimum_chain_count,
            );
            counters.evaluated_nodes += 1;
            let mut path = [0_u8; MAX_SEARCH_DEPTH];
            path[0] = action as u8;
            tracker.record_fire(make_fire(
                config,
                sequence.scenario_id,
                state,
                path,
                1,
                transition,
                value,
                terminal,
            ));
            evaluation = Some(value);
        }
        if terminal {
            counters.terminal_fire_nodes += 1;
            let value = evaluation.expect("terminal chain was evaluated");
            let node = Node::root(
                state,
                action as u8,
                sequence.scenario_id,
                value,
                root_evaluation,
                transition,
            );
            tracker.record_terminal(node, classify_fire(config, transition));
            continue;
        }
        if transition.game_over() {
            counters.game_over_nodes += 1;
            tracker.game_over_nodes += 1;
            continue;
        }
        let evaluation = evaluation.unwrap_or_else(|| {
            counters.evaluated_nodes += 1;
            evaluate_hot(
                &state,
                &request.evaluator,
                Some(&root_evaluation),
                Some(transition),
                config.minimum_chain_count,
            )
        });
        candidates.push(Node::root(
            state,
            action as u8,
            sequence.scenario_id,
            evaluation,
            root_evaluation,
            transition,
        ));
    }
    peak_live_nodes = peak_live_nodes.max(candidates.len() as u64);
    prune_survivors(
        &mut candidates,
        &mut beam,
        &mut prune_workspace,
        1,
        config,
        &mut trackers,
        &mut counters,
    );

    for depth in 2..=config.depth {
        if counters.budget_exhausted || beam.is_empty() {
            break;
        }
        candidates.clear();
        table.clear();
        let pair = sequence.pairs[depth - 1];
        for node in &beam {
            let legal = legal_actions_mask(&node.state);
            for action in 0..ACTION_COUNT {
                if legal & (1_u32 << action) == 0 {
                    continue;
                }
                if cancellation.load(AtomicOrdering::Relaxed) {
                    return Err(ContractError::internal(
                        "native scenario search was cancelled",
                    ));
                }
                if !consume_budget(&mut counters, expanded_limit) {
                    break;
                }
                let tracker = &mut trackers[usize::from(node.root_action)];
                tracker.expanded_nodes += 1;
                tracker.reached_depth = tracker.reached_depth.max(depth as u16);
                let (state, transition) = transition_hot(&node.state, pair, action as u8)?;
                if !transition.valid() {
                    counters.invalid_nodes += 1;
                    tracker.invalid_nodes += 1;
                    continue;
                }
                counters.generated_nodes += 1;
                counters.reached_depth = counters.reached_depth.max(depth as u64);
                let terminal = config.record_and_stop
                    && u16::from(transition.chain_count) >= config.terminal_fire_chain_count;
                let mut evaluation = None;
                if transition.chain_count > 0 {
                    let value = evaluate_hot(
                        &state,
                        &request.evaluator,
                        Some(&node.evaluation),
                        Some(transition),
                        config.minimum_chain_count,
                    );
                    counters.evaluated_nodes += 1;
                    let mut path = node.path;
                    path[depth - 1] = action as u8;
                    tracker.record_fire(make_fire(
                        config,
                        sequence.scenario_id,
                        state,
                        path,
                        depth as u8,
                        transition,
                        value,
                        terminal,
                    ));
                    evaluation = Some(value);
                }
                if terminal {
                    counters.terminal_fire_nodes += 1;
                    let value = evaluation.expect("terminal chain was evaluated");
                    let terminal_node = Node::child(node, state, action as u8, value, transition)?;
                    tracker.record_terminal(terminal_node, classify_fire(config, transition));
                    continue;
                }
                if transition.game_over() {
                    counters.game_over_nodes += 1;
                    tracker.game_over_nodes += 1;
                    continue;
                }
                let evaluation = evaluation.unwrap_or_else(|| {
                    counters.evaluated_nodes += 1;
                    evaluate_hot(
                        &state,
                        &request.evaluator,
                        Some(&node.evaluation),
                        Some(transition),
                        config.minimum_chain_count,
                    )
                });
                let candidate = Node::child(node, state, action as u8, evaluation, transition)?;
                if !config.use_transposition_table {
                    candidates.push(candidate);
                    continue;
                }
                let key = SearchStateKey::new(
                    &state,
                    node.root_action,
                    sequence.scenario_id,
                    depth as u16,
                    depth as u16,
                )
                .map_err(|error| {
                    ContractError::internal(format!("native TT key failed: {error}"))
                })?;
                let new_index = candidates.len();
                if let Some(previous_index) = table.find_or_insert(key, new_index)? {
                    counters.transposition_hits += 1;
                    tracker.transposition_hits += 1;
                    if survivor_cmp(&candidate, &candidates[previous_index]).is_lt() {
                        candidates[previous_index] = candidate;
                    }
                } else {
                    candidates.push(candidate);
                }
            }
            if counters.budget_exhausted {
                break;
            }
        }
        peak_live_nodes = peak_live_nodes.max((beam.len() + candidates.len()) as u64);
        prune_survivors(
            &mut candidates,
            &mut next_beam,
            &mut prune_workspace,
            depth,
            config,
            &mut trackers,
            &mut counters,
        );
        std::mem::swap(&mut beam, &mut next_beam);
    }

    Ok(ScenarioResult {
        trackers,
        counters,
        peak_live_nodes,
        tt_capacity: table.capacity() as u64,
    })
}

struct ScenarioJob {
    index: usize,
    request: Arc<Request>,
    sequence: ScenarioSequence,
    root_evaluation: EvaluationHot,
    expanded_limit: u64,
    cancellation: Arc<AtomicBool>,
    response: mpsc::SyncSender<(usize, ContractResult<ScenarioResult>)>,
}

struct ScenarioPool {
    sender: mpsc::SyncSender<ScenarioJob>,
    batches: AtomicU64,
}

impl ScenarioPool {
    fn new() -> Result<Self, String> {
        let (sender, receiver) = mpsc::sync_channel::<ScenarioJob>(12);
        let receiver = Arc::new(Mutex::new(receiver));
        for worker_index in 0..6 {
            let receiver = Arc::clone(&receiver);
            thread::Builder::new()
                .name(format!("puyo-scenario-{worker_index}"))
                .spawn(move || {
                    loop {
                        let received = {
                            let guard = match receiver.lock() {
                                Ok(value) => value,
                                Err(_) => return,
                            };
                            guard.recv()
                        };
                        let Ok(job) = received else {
                            return;
                        };
                        let result = match catch_unwind(AssertUnwindSafe(|| {
                            run_scenario(
                                &job.request,
                                job.sequence,
                                job.root_evaluation,
                                job.expanded_limit,
                                &job.cancellation,
                            )
                        })) {
                            Ok(value) => value,
                            Err(_) => Err(ContractError::internal(
                                "panic caught in reusable native scenario worker",
                            )),
                        };
                        let _ = job.response.send((job.index, result));
                    }
                })
                .map_err(|error| format!("failed to spawn native scenario worker: {error}"))?;
        }
        Ok(Self {
            sender,
            batches: AtomicU64::new(0),
        })
    }

    fn run(
        &self,
        request: Arc<Request>,
        sequences: &[ScenarioSequence],
        root_evaluation: EvaluationHot,
    ) -> ContractResult<(Vec<ScenarioResult>, bool)> {
        let reused = self.batches.fetch_add(1, AtomicOrdering::Relaxed) > 0;
        let cancellation = Arc::new(AtomicBool::new(false));
        let (response_sender, response_receiver) = mpsc::sync_channel(sequences.len());
        for (index, sequence) in sequences.iter().copied().enumerate() {
            self.sender
                .send(ScenarioJob {
                    index,
                    request: Arc::clone(&request),
                    sequence,
                    root_evaluation,
                    expanded_limit: request.search.max_expanded_nodes,
                    cancellation: Arc::clone(&cancellation),
                    response: response_sender.clone(),
                })
                .map_err(|_| {
                    ContractError::internal("reusable native scenario pool disconnected")
                })?;
        }
        drop(response_sender);
        let mut slots: Vec<Option<ContractResult<ScenarioResult>>> =
            (0..sequences.len()).map(|_| None).collect();
        for _ in sequences {
            let (index, result) = response_receiver.recv().map_err(|_| {
                ContractError::internal("native scenario worker response disconnected")
            })?;
            if result.is_err() {
                cancellation.store(true, AtomicOrdering::Relaxed);
            }
            slots[index] = Some(result);
        }
        let mut results = Vec::with_capacity(sequences.len());
        for slot in slots {
            results.push(slot.expect("every submitted scenario returned")?);
        }
        Ok((results, reused))
    }
}

static SCENARIO_POOL: OnceLock<Result<ScenarioPool, String>> = OnceLock::new();

fn scenario_pool() -> ContractResult<&'static ScenarioPool> {
    SCENARIO_POOL
        .get_or_init(ScenarioPool::new)
        .as_ref()
        .map_err(|message| ContractError::internal(message.clone()))
}

fn empty_scenario(request: &Request, sequence: ScenarioSequence, root_mask: u32) -> ScenarioResult {
    ScenarioResult {
        trackers: initialize_trackers(root_mask, sequence.scenario_id),
        counters: Counters::default(),
        peak_live_nodes: 0,
        tt_capacity: if request.search.use_transposition_table {
            request.search.width.saturating_mul(ACTION_COUNT * 2) as u64
        } else {
            0
        },
    }
}

fn execute_scenarios(
    request: &Request,
    sequences: &[ScenarioSequence],
    root_evaluation: EvaluationHot,
    telemetry: &mut Telemetry,
) -> ContractResult<(Vec<ScenarioResult>, Counters)> {
    let root_mask = legal_actions_mask(&request.root_state);
    let never_cancel = AtomicBool::new(false);
    let mut committed = Vec::with_capacity(sequences.len());
    let mut counters = Counters::default();
    match request.execution.mode {
        ExecutionMode::OracleOne => {
            for sequence in sequences.iter().copied() {
                if counters.budget_exhausted {
                    committed.push(empty_scenario(request, sequence, root_mask));
                    continue;
                }
                let remaining = request
                    .search
                    .max_expanded_nodes
                    .saturating_sub(counters.expanded_nodes);
                if remaining == 0 {
                    counters.budget_exhausted = true;
                    committed.push(empty_scenario(request, sequence, root_mask));
                    continue;
                }
                telemetry.scenario_jobs += 1;
                let result =
                    run_scenario(request, sequence, root_evaluation, remaining, &never_cancel)?;
                counters.add(result.counters);
                committed.push(result);
            }
        }
        ExecutionMode::ScenarioSix => {
            let (speculative, reused) =
                scenario_pool()?.run(Arc::new(request.clone()), sequences, root_evaluation)?;
            telemetry.scenario_jobs += speculative.len() as u64;
            telemetry.pool_reuses = u64::from(reused);
            for (index, speculative_result) in speculative.into_iter().enumerate() {
                let sequence = sequences[index];
                if counters.budget_exhausted {
                    committed.push(empty_scenario(request, sequence, root_mask));
                    continue;
                }
                let remaining = request
                    .search
                    .max_expanded_nodes
                    .saturating_sub(counters.expanded_nodes);
                if remaining == 0 {
                    counters.budget_exhausted = true;
                    committed.push(empty_scenario(request, sequence, root_mask));
                    continue;
                }
                let result = if speculative_result.counters.expanded_nodes > remaining {
                    telemetry.scenario_reruns += 1;
                    run_scenario(request, sequence, root_evaluation, remaining, &never_cancel)?
                } else {
                    speculative_result
                };
                counters.add(result.counters);
                committed.push(result);
            }
        }
    }
    for scenario in &mut committed {
        for tracker in &mut scenario.trackers {
            if tracker.active {
                tracker.finish(counters.budget_exhausted, request.search.depth);
            }
        }
        telemetry.peak_live_nodes = telemetry.peak_live_nodes.max(scenario.peak_live_nodes);
        telemetry.tt_capacity_slots = telemetry.tt_capacity_slots.max(scenario.tt_capacity);
    }
    Ok((committed, counters))
}

#[derive(Clone, Copy, Debug)]
struct Aggregate {
    root_action: u8,
    requested_scenarios: u8,
    evaluated_scenarios: u8,
    class: FireClass,
    class_support: [u8; FIRE_CLASS_COUNT],
    chain_score_sum: u64,
    chain_count_sum: u64,
    support: u8,
    worst_chain_score: u64,
    worst_chain_count: u8,
    chain_score_dispersion: f64,
    chain_count_dispersion: f64,
    continuation_score_mean: Option<f64>,
    quiet_support: u8,
    terminal_score_sum: f64,
    best_fire: Option<Fire>,
}

impl Aggregate {
    fn coverage(self) -> f64 {
        f64::from(self.evaluated_scenarios) / f64::from(self.requested_scenarios)
    }

    fn candidate_value(self) -> f64 {
        match self.class {
            FireClass::Quiet => self.continuation_score_mean.unwrap_or(-1_000_000_000_000.0),
            FireClass::Premature
            | FireClass::ForcedSafety
            | FireClass::Target
            | FireClass::Winning => self.terminal_score_sum,
            FireClass::Unavailable => -1_000_000_000_000.0,
        }
    }
}

fn mean(values: &[f64]) -> f64 {
    if values.is_empty() {
        0.0
    } else {
        values.iter().sum::<f64>() / values.len() as f64
    }
}

fn dispersion(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let average = mean(values);
    (values
        .iter()
        .map(|value| (value - average) * (value - average))
        .sum::<f64>()
        / values.len() as f64)
        .sqrt()
}

fn aggregate_root(
    root_action: u8,
    scenarios: &[ScenarioResult],
    requested_scenarios: usize,
) -> Aggregate {
    let mut evaluated: Vec<&Tracker> = scenarios
        .iter()
        .map(|scenario| &scenario.trackers[usize::from(root_action)])
        .filter(|tracker| tracker.evaluated)
        .collect();
    evaluated.sort_by_key(|tracker| tracker.scenario_id);
    let mut class_support = [0_u8; FIRE_CLASS_COUNT];
    for tracker in &evaluated {
        class_support[tracker.selected_class() as usize] += 1;
    }
    let class = (0..FIRE_CLASS_COUNT)
        .rev()
        .find(|index| class_support[*index] > 0)
        .map(|index| match index {
            1 => FireClass::Premature,
            2 => FireClass::Quiet,
            3 => FireClass::ForcedSafety,
            4 => FireClass::Target,
            5 => FireClass::Winning,
            _ => FireClass::Unavailable,
        })
        .unwrap_or(FireClass::Unavailable);
    let mut chain_scores = Vec::with_capacity(evaluated.len());
    let mut chain_counts = Vec::with_capacity(evaluated.len());
    let mut continuations = Vec::new();
    let mut terminal_scores = Vec::new();
    let mut best_fire: Option<Fire> = None;
    let mut support = 0_u8;
    for tracker in &evaluated {
        support += u8::from(tracker.max_chain_count > 0);
        let selected_fire = if tracker.selected_class() == class {
            tracker.selected_fire()
        } else {
            None
        };
        chain_scores.push(selected_fire.map_or(0.0, |fire| fire.chain_score as f64));
        chain_counts.push(selected_fire.map_or(0.0, |fire| f64::from(fire.chain_count)));
        if tracker.selected_class() == FireClass::Quiet
            && let Some(node) = tracker.best_survivor
        {
            continuations.push(node.evaluator_score);
        }
        if let Some(fire) = selected_fire {
            terminal_scores.push(fire.terminal_score);
            if best_fire.is_none_or(|previous| fire_rank_cmp(&fire, &previous).is_gt()) {
                best_fire = Some(fire);
            }
        }
    }
    Aggregate {
        root_action,
        requested_scenarios: requested_scenarios as u8,
        evaluated_scenarios: evaluated.len() as u8,
        class,
        class_support,
        chain_score_sum: chain_scores.iter().sum::<f64>() as u64,
        chain_count_sum: chain_counts.iter().sum::<f64>() as u64,
        support,
        worst_chain_score: chain_scores.iter().copied().reduce(f64::min).unwrap_or(0.0) as u64,
        worst_chain_count: chain_counts.iter().copied().reduce(f64::min).unwrap_or(0.0) as u8,
        chain_score_dispersion: dispersion(&chain_scores),
        chain_count_dispersion: dispersion(&chain_counts),
        continuation_score_mean: (!continuations.is_empty()).then(|| mean(&continuations)),
        quiet_support: class_support[FireClass::Quiet as usize],
        terminal_score_sum: terminal_scores.iter().sum(),
        best_fire,
    }
}

fn optional_f64_cmp(left: Option<f64>, right: Option<f64>) -> Ordering {
    match (left, right) {
        (Some(left), Some(right)) => left.partial_cmp(&right).unwrap_or(Ordering::Equal),
        (Some(_), None) => Ordering::Greater,
        (None, Some(_)) => Ordering::Less,
        (None, None) => Ordering::Equal,
    }
}

fn aggregate_cmp(left: &Aggregate, right: &Aggregate) -> Ordering {
    (left.class as u8)
        .cmp(&(right.class as u8))
        .then_with(|| {
            left.coverage()
                .partial_cmp(&right.coverage())
                .unwrap_or(Ordering::Equal)
        })
        .then_with(|| {
            left.class_support[left.class as usize].cmp(&right.class_support[right.class as usize])
        })
        .then_with(|| {
            left.candidate_value()
                .partial_cmp(&right.candidate_value())
                .unwrap_or(Ordering::Equal)
        })
        .then_with(|| left.chain_score_sum.cmp(&right.chain_score_sum))
        .then_with(|| left.chain_count_sum.cmp(&right.chain_count_sum))
        .then_with(|| left.support.cmp(&right.support))
        .then_with(|| left.worst_chain_score.cmp(&right.worst_chain_score))
        .then_with(|| left.worst_chain_count.cmp(&right.worst_chain_count))
        .then_with(|| {
            right
                .chain_score_dispersion
                .partial_cmp(&left.chain_score_dispersion)
                .unwrap_or(Ordering::Equal)
        })
        .then_with(|| {
            right
                .chain_count_dispersion
                .partial_cmp(&left.chain_count_dispersion)
                .unwrap_or(Ordering::Equal)
        })
        .then_with(|| optional_f64_cmp(left.continuation_score_mean, right.continuation_score_mean))
        .then_with(|| left.quiet_support.cmp(&right.quiet_support))
        .then_with(|| right.root_action.cmp(&left.root_action))
}

fn select_representatives(
    aggregates: &[Aggregate],
    scenarios: &[ScenarioResult],
) -> [Option<Node>; ACTION_COUNT] {
    let mut result = [None; ACTION_COUNT];
    for aggregate in aggregates {
        let mut candidates: Vec<Node> = scenarios
            .iter()
            .filter_map(|scenario| {
                let tracker = &scenario.trackers[usize::from(aggregate.root_action)];
                (tracker.selected_class() == aggregate.class)
                    .then(|| tracker.representative())
                    .flatten()
            })
            .collect();
        let exact = aggregate.best_fire.and_then(|fire| {
            candidates
                .iter()
                .copied()
                .find(|node| node.scenario_id == fire.scenario_id && node.path() == fire.path())
        });
        let selected = exact.or_else(|| {
            candidates.sort_unstable_by(survivor_cmp);
            candidates.first().copied()
        });
        result[usize::from(aggregate.root_action)] = selected;
    }
    result
}

fn hot_evidence(hot: EvaluationHot) -> EvaluationEvidence {
    EvaluationEvidence {
        hot,
        candidates: [QuiescenceCandidate::default(); MAX_EVIDENCE_CANDIDATES],
        candidate_count: 0,
    }
}

fn encode_fire(output: &mut Bytes, fire: Option<Fire>, config: SearchConfig) {
    let Some(fire) = fire else {
        output.u8(0);
        return;
    };
    output.u8(1);
    output.u8(fire.scenario_id);
    output.u8(fire.class as u8);
    output.u8(u8::from(fire.terminal) | (u8::from(fire.class.allowed()) << 1));
    output.u8(fire.chain_count);
    output.u64(fire.chain_score);
    output.u16(u16::from(fire.path_len));
    output.u8(fire.trigger_action());
    output.u8(0);
    output.u16(u16::from(config.minimum_chain_count));
    output.u16(u16::from(
        config.minimum_chain_count.saturating_sub(fire.chain_count),
    ));
    output.f64(fire.terminal_score);
    for value in fire.terminal_breakdown {
        output.f64(value);
    }
    output.raw(&fire.state.to_bytes());
    output.u16(u16::from(fire.path_len));
    output.raw(fire.path());
    let evaluation = encode_evaluation(&hot_evidence(fire.evaluation), false);
    output.u32(evaluation.len() as u32);
    output.raw(&evaluation);
}

fn observed_class_mask(tracker: &Tracker) -> u8 {
    tracker
        .fires_by_class
        .iter()
        .enumerate()
        .fold(0_u8, |mask, (index, value)| {
            mask | (u8::from(value.is_some()) << index)
        })
}

fn encode_tracker(output: &mut Bytes, tracker: &Tracker, config: SearchConfig) {
    output.u8(tracker.root_action);
    output.u8(tracker.scenario_id);
    output.u8(u8::from(tracker.evaluated)
        | (u8::from(tracker.search_complete) << 1)
        | (u8::from(tracker.best_survivor.is_some()) << 2));
    output.u8(tracker.selected_class() as u8);
    output.u8(u8::from(config.record_and_stop));
    output.u8(tracker.truncation_reason);
    output.u8(observed_class_mask(tracker));
    output.u8(0);
    output.u16(tracker.reached_depth);
    output.u16(config.terminal_fire_chain_count);
    output.u16(config.root_survivor_quota as u16);
    output.u16(u16::from(tracker.max_chain_count));
    output.u64(tracker.max_chain_score);
    output.u32(tracker.fire_count);
    output.u32(tracker.terminal_fire_count);
    output.f64(
        tracker
            .best_survivor
            .map_or(0.0, |node| node.evaluator_score),
    );
    for value in [
        tracker.expanded_nodes,
        tracker.pruned_nodes,
        tracker.transposition_hits,
        tracker.invalid_nodes,
        tracker.game_over_nodes,
    ] {
        output.u64(value);
    }
    output.u16(u16::from(tracker.coverage_len));
    output.u16(0);
    encode_fire(output, tracker.best_fire, config);
    encode_fire(output, tracker.selected_fire(), config);
    for coverage in tracker
        .coverage
        .iter()
        .take(usize::from(tracker.coverage_len))
    {
        output.u16(coverage.depth);
        output.u32(coverage.candidate_count);
        output.u32(coverage.retained_count);
        output.u8(coverage.shortfall);
        output.raw(&[0, 0, 0]);
    }
}

fn record_section(record_count: usize, body: Vec<u8>) -> Vec<u8> {
    let mut output = Bytes::default();
    output.u16(1);
    output.u16(0);
    output.u32(record_count as u32);
    output.u32(body.len() as u32);
    output.raw(&body);
    output.0
}

fn encode_root_evidence(
    request: &Request,
    scenarios: &[ScenarioResult],
    root_actions: &[u8],
) -> Vec<u8> {
    let mut body = Bytes::default();
    for root_action in root_actions {
        let mut trackers: Vec<&Tracker> = scenarios
            .iter()
            .map(|scenario| &scenario.trackers[usize::from(*root_action)])
            .collect();
        trackers.sort_by_key(|tracker| tracker.scenario_id);
        for tracker in trackers {
            encode_tracker(&mut body, tracker, request.search);
        }
    }
    record_section(root_actions.len() * scenarios.len(), body.0)
}

fn encode_representatives(
    request: &Request,
    representatives: &[Option<Node>; ACTION_COUNT],
    root_actions: &[u8],
) -> Vec<u8> {
    let mut body = Bytes::default();
    let mut count = 0_usize;
    for root_action in root_actions {
        let Some(node) = representatives[usize::from(*root_action)] else {
            continue;
        };
        count += 1;
        body.u8(node.root_action);
        body.u8(node.scenario_id);
        body.u8(node.last_action);
        body.u8(0);
        body.u16(u16::from(node.path_len));
        body.u16(u16::from(node.path_len));
        body.f64(node.evaluator_score);
        body.u64(node.cumulative_action_score);
        body.raw(&node.state.to_bytes());
        body.raw(node.path());
        let evidence = evaluate_evidence(
            &node.state,
            &request.evaluator,
            Some(&node.parent_evaluation),
            Some(node.transition),
            request.search.minimum_chain_count,
        );
        let encoded = encode_evaluation(&evidence, true);
        body.u32(encoded.len() as u32);
        body.raw(&encoded);
    }
    record_section(count, body.0)
}

fn encode_diagnostics(
    request: &Request,
    sequences: &[ScenarioSequence],
    root_evaluation: EvaluationHot,
) -> Vec<u8> {
    let mut body = Bytes::default();
    body.u16(sequences.len() as u16);
    body.u16(request.search.depth as u16);
    body.u16(request.known_pairs.len().min(request.search.depth) as u16);
    body.u16(0);
    for sequence in sequences {
        body.u8(sequence.scenario_id);
        body.u8(sequence.sample_index);
        body.u8(u8::from(sequence.rollout_seed.is_some()));
        body.u8(0);
        body.u64(sequence.rollout_seed.unwrap_or(0));
        for pair in sequence.pairs.iter().take(request.search.depth) {
            body.u8(pair.axis as u8);
            body.u8(pair.child as u8);
        }
    }
    let evidence = evaluate_evidence(
        &request.root_state,
        &request.evaluator,
        None,
        None,
        request.search.minimum_chain_count,
    );
    debug_assert_eq!(evidence.hot, root_evaluation);
    let encoded = encode_evaluation(&evidence, true);
    body.u32(encoded.len() as u32);
    body.raw(&encoded);
    record_section(1 + sequences.len(), body.0)
}

fn semantic_digest(root_evidence: &[u8], representatives: &[u8], diagnostics: &[u8]) -> String {
    let mut digest = Sha256::new();
    digest.update(b"puyo.native_long_horizon_result.v1\0");
    digest.update(root_evidence);
    digest.update(representatives);
    digest.update(diagnostics);
    let encoded = digest.finalize();
    format!("native-long-horizon-{}", hex_prefix(&encoded, 24))
}

fn hex_prefix(value: &[u8], digits: usize) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut result = String::with_capacity(digits);
    for byte in value.iter().take(digits.div_ceil(2)) {
        result.push(HEX[usize::from(byte >> 4)] as char);
        if result.len() < digits {
            result.push(HEX[usize::from(byte & 0x0f)] as char);
        }
    }
    result
}

fn encode_decision(
    selected_action: u8,
    ranked_actions: &[u8],
    search_complete: bool,
    budget_exhausted: bool,
    digest: &str,
) -> Vec<u8> {
    let mut output = Bytes::default();
    output.u8(selected_action);
    output.u8(u8::from(search_complete));
    output.u8(u8::from(budget_exhausted));
    output.u8(0);
    output.u16(ranked_actions.len() as u16);
    output.u16(0);
    output.string(digest);
    output.raw(ranked_actions);
    output.0
}

fn encode_counters(counters: Counters, telemetry: Telemetry) -> Vec<u8> {
    let mut output = Bytes::default();
    for value in [
        counters.expanded_nodes,
        counters.generated_nodes,
        counters.evaluated_nodes,
        counters.invalid_nodes,
        counters.pruned_nodes,
        counters.terminal_fire_nodes,
        counters.game_over_nodes,
        counters.transposition_hits,
        counters.reached_depth,
        telemetry.arena_capacity_nodes,
        telemetry.tt_capacity_slots,
        telemetry.peak_live_nodes,
        telemetry.scenario_jobs,
        telemetry.scenario_reruns,
        telemetry.pool_reuses,
        telemetry.search_ns,
        telemetry.aggregation_ns,
        telemetry.serialization_ns,
    ] {
        output.u64(value);
    }
    output.0
}

fn encode_provenance(mode: ExecutionMode) -> Vec<u8> {
    let mut output = Bytes::default();
    for value in [
        "native",
        env!("CARGO_PKG_VERSION"),
        SOURCE_REVISION,
        COMPILER,
        BUILD_PROFILE,
        TARGET,
        "external",
        mode.name(),
        "scalar",
    ] {
        output.string(value);
    }
    output.u16(mode.thread_count());
    output.0
}

pub(crate) fn execute(request: Request) -> ContractResult<Output> {
    let started = Instant::now();
    let root_mask = legal_actions_mask(&request.root_state);
    if root_mask == 0 {
        return Err(ContractError::invalid(
            REQUEST_ROOT_STATE_TAG,
            "native decision root has no legal placement",
        ));
    }
    let sequences = build_sequences(&request)?;
    let root_evaluation = evaluate_hot(
        &request.root_state,
        &request.evaluator,
        None,
        None,
        request.search.minimum_chain_count,
    );
    let mut telemetry = Telemetry {
        arena_capacity_nodes: request
            .search
            .width
            .saturating_mul(ACTION_COUNT)
            .saturating_add(request.search.width.saturating_mul(2))
            as u64,
        ..Telemetry::default()
    };
    let (scenarios, counters) =
        execute_scenarios(&request, &sequences, root_evaluation, &mut telemetry)?;
    telemetry.search_ns = started.elapsed().as_nanos().min(u128::from(u64::MAX)) as u64;

    let aggregation_started = Instant::now();
    let root_actions: Vec<u8> = (0..ACTION_COUNT)
        .filter(|action| root_mask & (1_u32 << action) != 0)
        .map(|action| action as u8)
        .collect();
    let aggregates: Vec<Aggregate> = root_actions
        .iter()
        .copied()
        .map(|action| aggregate_root(action, &scenarios, request.search.scenarios))
        .collect();
    let mut ranked = aggregates.clone();
    ranked.sort_by(|left, right| aggregate_cmp(right, left));
    let ranked_actions: Vec<u8> = ranked.iter().map(|value| value.root_action).collect();
    let selected_action = ranked_actions[0];
    let representatives = select_representatives(&aggregates, &scenarios);
    let search_complete = scenarios.iter().all(|scenario| {
        scenario
            .trackers
            .iter()
            .filter(|tracker| tracker.active)
            .all(|tracker| tracker.search_complete)
    });
    telemetry.aggregation_ns = aggregation_started
        .elapsed()
        .as_nanos()
        .min(u128::from(u64::MAX)) as u64;

    let serialization_started = Instant::now();
    let root_evidence = encode_root_evidence(&request, &scenarios, &root_actions);
    let representative_records = encode_representatives(&request, &representatives, &root_actions);
    let diagnostics = encode_diagnostics(&request, &sequences, root_evaluation);
    let digest = semantic_digest(&root_evidence, &representative_records, &diagnostics);
    let decision = encode_decision(
        selected_action,
        &ranked_actions,
        search_complete,
        counters.budget_exhausted,
        &digest,
    );
    telemetry.serialization_ns = serialization_started
        .elapsed()
        .as_nanos()
        .min(u128::from(u64::MAX)) as u64;
    let counter_payload = encode_counters(counters, telemetry);
    let provenance = encode_provenance(request.execution.mode);
    Ok(Output {
        decision,
        counters: counter_payload,
        root_evidence,
        representatives: representative_records,
        diagnostics,
        provenance,
        max_response_bytes: request.execution.max_response_bytes,
    })
}

#[cfg(test)]
mod tests {
    use super::{
        Counters, Node, PruneWorkspace, PythonRandom, SamplingMode, SearchConfig, Tracker,
        TranspositionTable, prune_survivors, rollout_seed,
    };
    use crate::allocation_probe;
    use crate::chain_structure::EvaluationHot;
    use crate::compact::{CompactState, SearchStateKey, TransitionHotResult};

    #[test]
    fn python_random_matches_cpython_getrandbits_choice_vectors() {
        let vectors = [
            (0_u64, [3, 3, 0, 2, 3, 3, 2, 3]),
            (1_u64, [1, 0, 2, 0, 3, 3, 3, 3]),
            (123_u64, [0, 2, 0, 3, 2, 0, 0, 3]),
            (9_223_372_036_854_775_809_u64, [1, 1, 3, 0, 2, 3, 1, 2]),
        ];
        for (seed, expected) in vectors {
            let mut random = PythonRandom::new(seed);
            let actual = std::array::from_fn(|_| random.randbelow(4));
            assert_eq!(actual, expected, "seed {seed}");
        }
    }

    #[test]
    fn rollout_seed_matches_locked_python_sha256_derivation() {
        assert_eq!(rollout_seed(123, 0), 365_590_776_014_759_487);
        assert_eq!(rollout_seed(123, 1), 2_892_866_067_820_594_080);
    }

    #[test]
    fn transposition_table_resolves_collisions_with_full_identity() {
        let mut encoded = [0_u8; 87];
        encoded[..4].copy_from_slice(b"CSK1");
        let state = CompactState::from_bytes(&encoded).expect("empty state");
        let keys: Vec<SearchStateKey> = (0..22)
            .map(|root| SearchStateKey::new(&state, root, 0, 2, 2).expect("key"))
            .collect();
        let mut collision = None;
        for left in 0..keys.len() {
            for right in left + 1..keys.len() {
                if keys[left].table_hash() & 7 == keys[right].table_hash() & 7 {
                    collision = Some((keys[left], keys[right]));
                    break;
                }
            }
            if collision.is_some() {
                break;
            }
        }
        let (left, right) = collision.expect("pigeonhole collision");
        assert_ne!(left, right);
        let mut table = TranspositionTable::new(4).expect("bounded table");
        assert_eq!(table.find_or_insert(left, 7).expect("insert"), None);
        assert_eq!(table.find_or_insert(right, 11).expect("insert"), None);
        assert_eq!(table.find_or_insert(left, 99).expect("lookup"), Some(7));
        assert_eq!(table.find_or_insert(right, 99).expect("lookup"), Some(11));

        for key in [
            SearchStateKey::new(&state, 0, 1, 2, 2).expect("scenario key"),
            SearchStateKey::new(&state, 0, 0, 3, 2).expect("pair key"),
            SearchStateKey::new(&state, 0, 0, 2, 3).expect("depth key"),
        ] {
            assert_ne!(key, keys[0]);
        }
    }

    #[test]
    fn preallocated_survivor_prune_performs_no_heap_allocation() {
        let mut encoded = [0_u8; 87];
        encoded[..4].copy_from_slice(b"CSK1");
        let state = CompactState::from_bytes(&encoded).expect("empty state");
        let config = SearchConfig {
            depth: 3,
            width: 22,
            scenarios: 1,
            minimum_chain_count: 6,
            terminal_fire_chain_count: 1,
            max_expanded_nodes: 512,
            root_survivor_quota: 1,
            decision_seed: Some(1),
            winning_score_threshold: None,
            premature_target_gap_penalty: 1_000.0,
            sampling_mode: SamplingMode::SeededAuthoritative,
            record_and_stop: true,
            forced_safety: false,
            use_transposition_table: true,
        };
        let evaluation = EvaluationHot::default();
        let transition = TransitionHotResult {
            score_delta: 0,
            attack_score_delta: 0,
            vanished_count: 0,
            garbage_cleared_count: 0,
            action_id: 0,
            axis_y: 0,
            chain_count: 0,
            flags: 1,
        };
        let mut nodes = Vec::with_capacity(22);
        let mut trackers = Vec::with_capacity(22);
        for action in 0..22 {
            nodes.push(Node::root(
                state, action, 0, evaluation, evaluation, transition,
            ));
            trackers.push(Tracker::new(action, 0));
        }
        let mut beam = Vec::with_capacity(config.width);
        let mut workspace = PruneWorkspace::new(config, 22);
        let mut counters = Counters::default();
        let (_, allocations) = allocation_probe::count_allocations(|| {
            prune_survivors(
                &mut nodes,
                &mut beam,
                &mut workspace,
                1,
                config,
                &mut trackers,
                &mut counters,
            );
        });
        assert_eq!(allocations, 0);
        assert_eq!(beam.len(), 22);
    }
}
