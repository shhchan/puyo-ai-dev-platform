//! Allocation-free native chain-structure evaluator and bounded quiescence.
//!
//! The Python implementation in `agents/chain_structure.py` remains the
//! differential oracle.  This module consumes the transition kernel's native
//! `CompactState` directly and returns fixed-width scalar values.  Candidate
//! evidence is optional and bounded; the normal search path neither allocates
//! nor materializes Python-facing objects.

use std::cmp::Ordering;
use std::mem::MaybeUninit;
use std::sync::atomic::{AtomicU8, Ordering as AtomicOrdering};

use sha2::block_api::compress256;

use crate::compact::{
    CompactState, HEIGHT, NORMAL_COLOR_COUNT, PLANE_COUNT, TransitionHotResult, VISIBLE_HEIGHT,
    WIDTH,
};

pub(crate) const FEATURE_SCHEMA: &str = "puyo.chain_structure_features.v1";
pub(crate) const RESULT_SCHEMA: &str = "puyo.native_chain_structure_hot.v1";
pub(crate) const RESULT_ABI_VERSION: u16 = 1;
pub(crate) const WEIGHT_COUNT: usize = 24;
pub(crate) const MAX_COMPONENTS: usize = WIDTH * HEIGHT;
pub(crate) const MAX_EVIDENCE_CANDIDATES: usize = 96;
pub(crate) const DEFAULT_MAX_RETAINED_CANDIDATES: usize = 12;

const PLACEMENT_PATTERN_COUNT: usize = 83;
const PLACEMENT_ORBIT_COUNT: usize = 43;
const CANDIDATE_GROUP_COUNT: usize = PLACEMENT_ORBIT_COUNT * NORMAL_COLOR_COUNT;
const MAX_FRONTIER_CANDIDATE_RECORDS: usize = PLACEMENT_PATTERN_COUNT * NORMAL_COLOR_COUNT;
const ROOT_PLACEMENT_PATTERN_INDEX: u8 = PLACEMENT_PATTERN_COUNT as u8;
const MAX_FRONTIER_COMPONENTS: usize = WIDTH + (WIDTH - 1) * 3;
const STACK_SLOT_COUNT: usize = WIDTH * 3;
const RESOLUTION_CACHE_SIZE: usize = 32;

pub(crate) const PROFILE_STAGE_DRIVER: u8 = 0;
pub(crate) const PROFILE_STAGE_TRANSITION: u8 = 1;
pub(crate) const PROFILE_STAGE_BASE_FEATURES: u8 = 2;
pub(crate) const PROFILE_STAGE_PLACEMENT_ORBIT: u8 = 3;
pub(crate) const PROFILE_STAGE_RESOLVE: u8 = 4;
pub(crate) const PROFILE_STAGE_REMAINING: u8 = 5;
pub(crate) const PROFILE_STAGE_RANKING: u8 = 6;
pub(crate) const PROFILE_STAGE_PLACEMENT_FRONTIER: u8 = 7;
pub(crate) const PROFILE_STAGE_PLACEMENT_QUALIFICATION: u8 = 8;
pub(crate) const PROFILE_STAGE_PLACEMENT_DEDUPLICATION: u8 = 9;
pub(crate) const PROFILE_STAGE_PLACEMENT_DISPATCH: u8 = 10;
pub(crate) const PROFILE_STAGE_PLACEMENT_SINGLE_FRONTIER: u8 = 11;
pub(crate) const PROFILE_STAGE_PLACEMENT_MULTI_FRONTIER: u8 = 12;
pub(crate) const PROFILE_STAGE_COUNT: usize = 13;
pub(crate) const PROFILE_COUNTER_COUNT: usize = 15;

const COLUMN_LANE_BITS: usize = 16;
const BOARD_MASK: u128 = lane_mask(HEIGHT);
const VISIBLE_MASK: u128 = lane_mask(VISIBLE_HEIGHT);
const HIDDEN_MASK: u128 = BOARD_MASK & !VISIBLE_MASK;
const ROW_ZERO_MASK: u128 = row_mask(0);
const ROW_THREE_MASK: u128 = lane_mask(3);
const TOP_VISIBLE_ROW_MASK: u128 = row_mask(VISIBLE_HEIGHT - 1);
const ROW_FOURTEEN_MASK: u128 = row_mask(HEIGHT - 1);
const LEFT_VISIBLE_COLUMN_MASK: u128 = (1_u128 << VISIBLE_HEIGHT) - 1;
const RIGHT_VISIBLE_COLUMN_MASK: u128 =
    LEFT_VISIBLE_COLUMN_MASK << ((WIDTH - 1) * COLUMN_LANE_BITS);
const CHAIN_BONUS: [u16; 20] = [
    0, 0, 8, 16, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, 480, 512,
];
const COLOR_BONUS: [u16; 6] = [0, 0, 3, 6, 12, 24];

const WEIGHT_POTENTIAL_CHAIN_COUNT: usize = 0;
const WEIGHT_POTENTIAL_CHAIN_SCORE: usize = 1;
const WEIGHT_REQUIRED_KEY_COUNT: usize = 2;
const WEIGHT_TRIGGER_HEIGHT: usize = 3;
const WEIGHT_TRIGGER_PROTECTION: usize = 4;
const WEIGHT_REMAINING_LINK_2: usize = 5;
const WEIGHT_REMAINING_LINK_3: usize = 6;
const WEIGHT_CONNECTIVITY_EDGE: usize = 7;
const WEIGHT_CONNECTION_CANDIDATE: usize = 8;
const WEIGHT_REACHABLE_IGNITION: usize = 9;
const WEIGHT_GROWTH_SITE: usize = 10;
const WEIGHT_FOUNDATION_CELL: usize = 11;
const WEIGHT_FOLD_SPACE: usize = 12;
const WEIGHT_ADJACENT_ROUGHNESS: usize = 13;
const WEIGHT_HEIGHT_SPREAD: usize = 14;
const WEIGHT_WELL_DEPTH: usize = 15;
const WEIGHT_BUMP_HEIGHT: usize = 16;
const WEIGHT_DANGER_RATIO: usize = 17;
const WEIGHT_NUISANCE_PUYO: usize = 18;
const WEIGHT_HIDDEN_ROW_PUYO: usize = 19;
const WEIGHT_TEAR: usize = 20;
const WEIGHT_WASTE: usize = 21;
const WEIGHT_TRIGGER_DAMAGE: usize = 22;
const WEIGHT_PREMATURE_FIRE: usize = 23;

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct EvaluationConfig {
    pub(crate) max_added_puyos: u8,
    pub(crate) max_pattern_nodes: u32,
    pub(crate) max_resolution_nodes: u32,
    pub(crate) max_candidates: u8,
    pub(crate) weights: [f64; WEIGHT_COUNT],
    pub(crate) fatal_score: f64,
    pub(crate) version_key: u64,
}

impl EvaluationConfig {
    pub(crate) fn validate(&self) -> Result<(), &'static str> {
        if !(1..=3).contains(&self.max_added_puyos)
            || self.max_pattern_nodes == 0
            || self.max_resolution_nodes == 0
            || self.max_resolution_nodes as usize > MAX_EVIDENCE_CANDIDATES
            || self.max_candidates == 0
            || usize::from(self.max_candidates) > DEFAULT_MAX_RETAINED_CANDIDATES
        {
            return Err("native chain-structure budget is outside the v1 bounds");
        }
        if !self.fatal_score.is_finite() || self.fatal_score >= 0.0 {
            return Err("native chain-structure fatal score must be finite and negative");
        }
        let rewards = [0_usize, 1, 4, 5, 6, 7, 8, 9, 10, 11, 12];
        for (index, value) in self.weights.iter().copied().enumerate() {
            if !value.is_finite()
                || (rewards.contains(&index) && value < 0.0)
                || (!rewards.contains(&index) && value > 0.0)
            {
                return Err("native chain-structure weight layout is invalid");
            }
        }
        Ok(())
    }
}

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum EvaluationStatus {
    Available = 1,
    NotFound = 2,
    BudgetExhausted = 3,
}

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum TruncationReason {
    None = 0,
    PatternNodes = 1,
    ResolutionNodes = 2,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub(crate) struct ChainStructureFeatures {
    pub(crate) canonical_column_heights: [u8; WIDTH],
    pub(crate) normal_puyo_count: u8,
    pub(crate) component_count: u8,
    pub(crate) isolated_count: u8,
    pub(crate) link_2: u8,
    pub(crate) link_3: u8,
    pub(crate) connectivity_edges: u8,
    pub(crate) connection_candidate_count: u8,
    pub(crate) reachable_ignition_count: u8,
    pub(crate) growth_site_count: u8,
    pub(crate) foundation_cell_count: u8,
    pub(crate) fold_space: u16,
    pub(crate) adjacent_roughness: u8,
    pub(crate) height_spread: u8,
    pub(crate) well_depth: u8,
    pub(crate) bump_height: u8,
    pub(crate) danger_ratio: f64,
    pub(crate) nuisance_count: u8,
    pub(crate) hidden_row_count: u8,
    pub(crate) trigger_reachable: bool,
    pub(crate) trigger_protection: f64,
    pub(crate) potential_chain_count: u8,
    pub(crate) potential_chain_score: u32,
    pub(crate) required_key_count: i8,
    pub(crate) trigger_column: i8,
    pub(crate) trigger_height: i8,
    pub(crate) remaining_link_2: u8,
    pub(crate) remaining_link_3: u8,
    pub(crate) remaining_connection_edges: u8,
    pub(crate) death: bool,
    pub(crate) unreachable_trigger: bool,
    pub(crate) structural_dead_end: bool,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub(crate) struct ActionStructureFeatures {
    pub(crate) evaluated: bool,
    pub(crate) tear_count: u8,
    pub(crate) waste_count: u8,
    pub(crate) trigger_damage: u8,
    pub(crate) premature_fire: bool,
    pub(crate) danger_delta: f64,
    pub(crate) death: bool,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub(crate) struct ScoreBreakdown {
    pub(crate) values: [f64; 15],
}

impl ScoreBreakdown {
    pub(crate) const QUIESCENCE_CHAIN: usize = 0;
    pub(crate) const KEY_COST: usize = 1;
    pub(crate) const TRIGGER_POSITION: usize = 2;
    pub(crate) const REMAINING_LINKS: usize = 3;
    pub(crate) const COMPONENT_CONNECTIVITY: usize = 4;
    pub(crate) const CONNECTION_POTENTIAL: usize = 5;
    pub(crate) const SHAPE: usize = 6;
    pub(crate) const DANGER: usize = 7;
    pub(crate) const NUISANCE: usize = 8;
    pub(crate) const TEAR: usize = 9;
    pub(crate) const WASTE: usize = 10;
    pub(crate) const TRIGGER_DAMAGE: usize = 11;
    pub(crate) const PREMATURE_FIRE: usize = 12;
    pub(crate) const FATAL: usize = 13;
    pub(crate) const TOTAL: usize = 14;
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub(crate) struct QuiescenceCandidate {
    pub(crate) chain_count: u8,
    pub(crate) chain_score: u32,
    pub(crate) required_key_count: u8,
    pub(crate) trigger_color: u8,
    pub(crate) placements_mask: u128,
    pub(crate) anchor_mask: u128,
    pub(crate) trigger_column: u8,
    pub(crate) trigger_height: u8,
    pub(crate) trigger_protection: f64,
    pub(crate) remaining_link_2: u8,
    pub(crate) remaining_link_3: u8,
    pub(crate) remaining_connection_edges: u8,
    pub(crate) extension_space: u8,
    pub(crate) fixed_tie_break: u64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct EvaluationHot {
    pub(crate) status: EvaluationStatus,
    pub(crate) truncation_reason: TruncationReason,
    pub(crate) pattern_nodes: u32,
    pub(crate) resolution_nodes: u32,
    pub(crate) score: f64,
    pub(crate) features: ChainStructureFeatures,
    pub(crate) action_features: ActionStructureFeatures,
    pub(crate) score_breakdown: ScoreBreakdown,
    pub(crate) best: QuiescenceCandidate,
    pub(crate) has_best: bool,
}

impl Default for EvaluationHot {
    fn default() -> Self {
        Self {
            status: EvaluationStatus::NotFound,
            truncation_reason: TruncationReason::None,
            pattern_nodes: 0,
            resolution_nodes: 0,
            score: 0.0,
            features: ChainStructureFeatures::default(),
            action_features: ActionStructureFeatures::default(),
            score_breakdown: ScoreBreakdown::default(),
            best: QuiescenceCandidate::default(),
            has_best: false,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct EvaluationEvidence {
    pub(crate) hot: EvaluationHot,
    pub(crate) candidates: [QuiescenceCandidate; MAX_EVIDENCE_CANDIDATES],
    pub(crate) candidate_count: u8,
}

/// Exact per-evaluation counters emitted only by the PUYO-219 QA profile path.
///
/// `evaluate_hot` does not construct or update this value. The separate
/// profiled entry point preserves the production result while exposing the
/// work performed by bounded quiescence to a statistical stage sampler.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(crate) struct EvaluationProfileCounts {
    pub(crate) pattern_nodes: u32,
    pub(crate) executed_pattern_probes: u32,
    pub(crate) resolution_nodes: u32,
    pub(crate) rank_comparison_calls: u32,
    pub(crate) rank_tie_calls: u32,
    pub(crate) sha256_calls: u32,
    pub(crate) single_component_frontiers: u32,
    pub(crate) multi_component_frontiers: u32,
    pub(crate) frontier_state_visits: u32,
    pub(crate) qualified_candidates: u32,
    pub(crate) resolution_group_comparisons: u32,
    pub(crate) resolution_groups: u32,
    pub(crate) precomputed_resolution_groups: u32,
    pub(crate) precomputed_candidate_hits: u32,
    pub(crate) resolution_cache_hits: u32,
    pub(crate) stage_entries: [u32; PROFILE_STAGE_COUNT],
}

#[inline(always)]
fn mark_profile_stage<const PROFILE: bool>(
    marker: Option<&AtomicU8>,
    stage_entries: &mut [u32; PROFILE_STAGE_COUNT],
    stage: u8,
) {
    if !PROFILE {
        return;
    }
    let Some(marker) = marker else {
        return;
    };
    marker.store(stage, AtomicOrdering::Relaxed);
    stage_entries[usize::from(stage)] += 1;
}

impl Default for EvaluationEvidence {
    fn default() -> Self {
        Self {
            hot: EvaluationHot::default(),
            candidates: [QuiescenceCandidate::default(); MAX_EVIDENCE_CANDIDATES],
            candidate_count: 0,
        }
    }
}

#[derive(Clone, Copy, Debug, Default)]
struct Component {
    mask: u128,
    extension_columns: u8,
    frontier_slots: u32,
    color: u8,
    size: u8,
    connection_edges: u8,
}

#[derive(Clone, Copy)]
struct ComponentSet {
    #[cfg(test)]
    values: [MaybeUninit<Component>; MAX_COMPONENTS],
    len: usize,
    landing: u128,
    incremental_resolution_supported: bool,
    base_normal: u128,
    base_occupied: u128,
    base_remaining: RemainingStructure,
    isolated_count: u8,
    reachable_ignition_count: u8,
    growth_columns: u8,
    connection_seen_once: [u8; NORMAL_COLOR_COUNT],
    connection_seen_multiple: [u8; NORMAL_COLOR_COUNT],
    connection_candidate_count: u8,
    frontier_topology: [MaybeUninit<u64>; MAX_FRONTIER_COMPONENTS],
    frontier_masks: [MaybeUninit<u128>; MAX_FRONTIER_COMPONENTS],
    frontier_color_components: [u32; NORMAL_COLOR_COUNT],
    frontier_len: usize,
    stack_neighbors: [u32; STACK_SLOT_COUNT],
    stack_prefix_neighbors: [[u32; 4]; WIDTH],
    stack_prefix_components: [[u32; 4]; WIDTH],
}

impl Default for ComponentSet {
    fn default() -> Self {
        Self {
            #[cfg(test)]
            values: [MaybeUninit::uninit(); MAX_COMPONENTS],
            len: 0,
            landing: 0,
            incremental_resolution_supported: false,
            base_normal: 0,
            base_occupied: 0,
            base_remaining: RemainingStructure::default(),
            isolated_count: 0,
            reachable_ignition_count: 0,
            growth_columns: 0,
            connection_seen_once: [0; NORMAL_COLOR_COUNT],
            connection_seen_multiple: [0; NORMAL_COLOR_COUNT],
            connection_candidate_count: 0,
            frontier_topology: [MaybeUninit::uninit(); MAX_FRONTIER_COMPONENTS],
            frontier_masks: [MaybeUninit::uninit(); MAX_FRONTIER_COMPONENTS],
            frontier_color_components: [0; NORMAL_COLOR_COUNT],
            frontier_len: 0,
            stack_neighbors: [0; STACK_SLOT_COUNT],
            stack_prefix_neighbors: [[0; 4]; WIDTH],
            stack_prefix_components: [[0; 4]; WIDTH],
        }
    }
}

impl ComponentSet {
    fn push(&mut self, value: Component) {
        debug_assert!(self.len < MAX_COMPONENTS);
        #[cfg(test)]
        self.values[self.len].write(value);
        self.len += 1;
        self.base_remaining.link_2 += u8::from(value.size == 2);
        self.base_remaining.link_3 += u8::from(value.size == 3);
        self.base_remaining.connection_edges += value.connection_edges;
        self.isolated_count += u8::from(value.size == 1);
        self.reachable_ignition_count += u8::from(value.size == 3 && value.extension_columns != 0);
        self.growth_columns |= value.extension_columns;
        let color = usize::from(value.color);
        let repeated_connections = value.extension_columns & self.connection_seen_once[color];
        let new_connections = repeated_connections & !self.connection_seen_multiple[color];
        self.connection_candidate_count += new_connections.count_ones() as u8;
        self.connection_seen_once[color] |= value.extension_columns;
        self.connection_seen_multiple[color] |= repeated_connections;
        if value.frontier_slots == 0 {
            return;
        }
        debug_assert!(self.frontier_len < MAX_FRONTIER_COMPONENTS);
        let frontier_component_bit = 1_u32 << self.frontier_len;
        self.frontier_topology[self.frontier_len].write(
            u64::from(value.frontier_slots)
                | (u64::from(value.size) << 32)
                | (u64::from(value.connection_edges) << 40),
        );
        self.frontier_masks[self.frontier_len].write(value.mask);
        self.frontier_color_components[usize::from(value.color)] |= frontier_component_bit;
        let mut frontier = value.frontier_slots;
        while frontier != 0 {
            let slot = frontier.trailing_zeros() as usize;
            frontier &= frontier - 1;
            let column = slot / 3;
            let mut count = slot % 3 + 1;
            while count <= 3 {
                self.stack_prefix_components[column][count] |= frontier_component_bit;
                count += 1;
            }
        }
        self.frontier_len += 1;
    }

    #[cfg(test)]
    fn as_slice(&self) -> &[Component] {
        // SAFETY: `push` initializes every element below `len`, and `len`
        // cannot exceed the backing array's capacity.
        unsafe { std::slice::from_raw_parts(self.values.as_ptr().cast(), self.len) }
    }
}

#[derive(Clone, Copy, Debug, Default)]
struct PlacementPattern {
    mask: u128,
    #[cfg(test)]
    counts: [u8; WIDTH],
    columns: u8,
    minimum_column: u8,
}

#[derive(Clone, Copy)]
struct PlacementPatternSpec {
    counts: [u8; WIDTH],
    count_at_least_columns: [u8; 3],
    added_puyos: u8,
    minimum_column: u8,
}

impl PlacementPatternSpec {
    const EMPTY: Self = Self {
        counts: [0; WIDTH],
        count_at_least_columns: [0; 3],
        added_puyos: 0,
        minimum_column: 0,
    };
}

#[derive(Clone, Copy)]
struct PlacementOrbitSpec {
    first_pattern: u8,
    pattern_count: u8,
    added_puyos: u8,
}

impl PlacementOrbitSpec {
    const EMPTY: Self = Self {
        first_pattern: 0,
        pattern_count: 0,
        added_puyos: 0,
    };

    #[inline(always)]
    fn pattern_mask(self) -> u128 {
        ((1_u128 << self.pattern_count) - 1) << self.first_pattern
    }
}

struct PlacementCatalog {
    patterns: [PlacementPatternSpec; PLACEMENT_PATTERN_COUNT],
    orbits: [PlacementOrbitSpec; PLACEMENT_ORBIT_COUNT],
    orbit_by_pattern: [u8; PLACEMENT_PATTERN_COUNT],
    by_added_puyos: [u128; 4],
    count_at_least: [[u128; 4]; WIDTH],
    proper_subpatterns: [u128; PLACEMENT_PATTERN_COUNT],
    proper_supersets: [u128; PLACEMENT_PATTERN_COUNT],
    nonminimal_by_singletons: [u128; 1 << WIDTH],
    nonminimal_by_pairs: [[u128; 1 << 7]; 3],
    pattern_by_counts: [u8; 1 << (WIDTH * 2)],
    pattern_slots: [u32; PLACEMENT_PATTERN_COUNT],
    slot_patterns: [u128; STACK_SLOT_COUNT],
    slot_pattern_unions: [[u128; 1 << 6]; 3],
    transitions_by_slot: [[u64; WIDTH * 3]; PLACEMENT_PATTERN_COUNT + 1],
    next_slots_by_max_added: [[u32; 4]; PLACEMENT_PATTERN_COUNT + 1],
    candidate_group_specs: [u16; CANDIDATE_GROUP_COUNT],
}

const fn compare_column_patterns(left: &[u8; 3], right: &[u8; 3], len: usize) -> i8 {
    let mut index = 0_usize;
    while index < len {
        if left[index] < right[index] {
            return -1;
        }
        if left[index] > right[index] {
            return 1;
        }
        index += 1;
    }
    0
}

const fn pattern_spec(columns: &[u8; 3], len: usize) -> PlacementPatternSpec {
    let mut counts = [0_u8; WIDTH];
    let mut index = 0_usize;
    while index < len {
        counts[columns[index] as usize] += 1;
        index += 1;
    }
    let mut count_at_least_columns = [0_u8; 3];
    let mut column = 0_usize;
    while column < WIDTH {
        if counts[column] != 0 {
            let mut count = 0_usize;
            while count < counts[column] as usize {
                count_at_least_columns[count] |= 1_u8 << column;
                count += 1;
            }
        }
        column += 1;
    }
    PlacementPatternSpec {
        counts,
        count_at_least_columns,
        added_puyos: len as u8,
        minimum_column: columns[0],
    }
}

const fn push_catalog_orbit(
    catalog: &mut PlacementCatalog,
    pattern_cursor: &mut usize,
    orbit_cursor: &mut usize,
    columns: [u8; 3],
    len: usize,
) {
    let mut mirrored = [0_u8; 3];
    let mut index = 0_usize;
    while index < len {
        mirrored[index] = (WIDTH - 1) as u8 - columns[len - 1 - index];
        index += 1;
    }
    let comparison = compare_column_patterns(&columns, &mirrored, len);
    if comparison > 0 {
        return;
    }
    let first_pattern = *pattern_cursor;
    catalog.patterns[*pattern_cursor] = pattern_spec(&columns, len);
    *pattern_cursor += 1;
    if comparison < 0 {
        catalog.patterns[*pattern_cursor] = pattern_spec(&mirrored, len);
        *pattern_cursor += 1;
    }
    catalog.orbits[*orbit_cursor] = PlacementOrbitSpec {
        first_pattern: first_pattern as u8,
        pattern_count: (*pattern_cursor - first_pattern) as u8,
        added_puyos: len as u8,
    };
    *orbit_cursor += 1;
}

const fn build_placement_catalog() -> PlacementCatalog {
    let mut catalog = PlacementCatalog {
        patterns: [PlacementPatternSpec::EMPTY; PLACEMENT_PATTERN_COUNT],
        orbits: [PlacementOrbitSpec::EMPTY; PLACEMENT_ORBIT_COUNT],
        orbit_by_pattern: [u8::MAX; PLACEMENT_PATTERN_COUNT],
        by_added_puyos: [0; 4],
        count_at_least: [[0; 4]; WIDTH],
        proper_subpatterns: [0; PLACEMENT_PATTERN_COUNT],
        proper_supersets: [0; PLACEMENT_PATTERN_COUNT],
        nonminimal_by_singletons: [0; 1 << WIDTH],
        nonminimal_by_pairs: [[0; 1 << 7]; 3],
        pattern_by_counts: [u8::MAX; 1 << (WIDTH * 2)],
        pattern_slots: [0; PLACEMENT_PATTERN_COUNT],
        slot_patterns: [0; STACK_SLOT_COUNT],
        slot_pattern_unions: [[0_u128; 1 << 6]; 3],
        transitions_by_slot: [[0_u64; WIDTH * 3]; PLACEMENT_PATTERN_COUNT + 1],
        next_slots_by_max_added: [[0; 4]; PLACEMENT_PATTERN_COUNT + 1],
        candidate_group_specs: [0; CANDIDATE_GROUP_COUNT],
    };
    let mut pattern_cursor = 0_usize;
    let mut orbit_cursor = 0_usize;

    let mut first = 0_u8;
    while first < WIDTH as u8 {
        push_catalog_orbit(
            &mut catalog,
            &mut pattern_cursor,
            &mut orbit_cursor,
            [first, 0, 0],
            1,
        );
        first += 1;
    }
    first = 0;
    while first < WIDTH as u8 {
        let mut second = first;
        while second < WIDTH as u8 {
            push_catalog_orbit(
                &mut catalog,
                &mut pattern_cursor,
                &mut orbit_cursor,
                [first, second, 0],
                2,
            );
            second += 1;
        }
        first += 1;
    }
    first = 0;
    while first < WIDTH as u8 {
        let mut second = first;
        while second < WIDTH as u8 {
            let mut third = second;
            while third < WIDTH as u8 {
                push_catalog_orbit(
                    &mut catalog,
                    &mut pattern_cursor,
                    &mut orbit_cursor,
                    [first, second, third],
                    3,
                );
                third += 1;
            }
            second += 1;
        }
        first += 1;
    }

    let mut pattern_index = 0_usize;
    let mut orbit_index = 0_usize;
    while orbit_index < PLACEMENT_ORBIT_COUNT {
        let orbit = catalog.orbits[orbit_index];
        let mut offset = 0_u8;
        while offset < orbit.pattern_count {
            catalog.orbit_by_pattern[orbit.first_pattern as usize + offset as usize] =
                orbit_index as u8;
            offset += 1;
        }
        orbit_index += 1;
    }
    let mut group_index = 0_usize;
    while group_index < CANDIDATE_GROUP_COUNT {
        let orbit = catalog.orbits[group_index / NORMAL_COLOR_COUNT];
        let plane_index = group_index % NORMAL_COLOR_COUNT;
        catalog.candidate_group_specs[group_index] = orbit.first_pattern as u16
            | ((orbit.pattern_count as u16) << 7)
            | ((orbit.added_puyos as u16) << 9)
            | ((plane_index as u16) << 11);
        group_index += 1;
    }
    while pattern_index < PLACEMENT_PATTERN_COUNT {
        let bit = 1_u128 << pattern_index;
        let pattern = catalog.patterns[pattern_index];
        let mut count_code = 0_usize;
        let mut code_column = 0_usize;
        while code_column < WIDTH {
            count_code |= (pattern.counts[code_column] as usize) << (code_column * 2);
            let mut offset = 0_u8;
            while offset < pattern.counts[code_column] {
                catalog.pattern_slots[pattern_index] |=
                    1_u32 << (code_column * 3 + offset as usize);
                offset += 1;
            }
            code_column += 1;
        }
        catalog.pattern_by_counts[count_code] = pattern_index as u8;
        catalog.by_added_puyos[pattern.added_puyos as usize] |= bit;
        let mut column = 0_usize;
        while column < WIDTH {
            let mut count = 1_u8;
            while count <= pattern.counts[column] {
                catalog.count_at_least[column][count as usize] |= bit;
                count += 1;
            }
            column += 1;
        }
        pattern_index += 1;
    }
    let mut slot = 0_usize;
    while slot < STACK_SLOT_COUNT {
        catalog.slot_patterns[slot] = catalog.count_at_least[slot / 3][slot % 3 + 1];
        slot += 1;
    }
    let mut chunk = 0_usize;
    while chunk < 3 {
        let mut selection = 0_usize;
        while selection < 1 << 6 {
            let mut bit = 0_usize;
            while bit < 6 {
                if selection & (1 << bit) != 0 {
                    catalog.slot_pattern_unions[chunk][selection] |=
                        catalog.slot_patterns[chunk * 6 + bit];
                }
                bit += 1;
            }
            selection += 1;
        }
        chunk += 1;
    }
    pattern_index = 0;
    while pattern_index < PLACEMENT_PATTERN_COUNT {
        let pattern = catalog.patterns[pattern_index];
        let mut candidate_index = 0_usize;
        while candidate_index < PLACEMENT_PATTERN_COUNT {
            let candidate = catalog.patterns[candidate_index];
            if candidate.added_puyos < pattern.added_puyos {
                let mut subset = true;
                let mut column = 0_usize;
                while column < WIDTH {
                    if candidate.counts[column] > pattern.counts[column] {
                        subset = false;
                        break;
                    }
                    column += 1;
                }
                if subset {
                    catalog.proper_subpatterns[pattern_index] |= 1_u128 << candidate_index;
                    catalog.proper_supersets[candidate_index] |= 1_u128 << pattern_index;
                }
            }
            candidate_index += 1;
        }
        pattern_index += 1;
    }
    let mut selection = 0_usize;
    while selection < 1 << WIDTH {
        let mut bit = 0_usize;
        while bit < WIDTH {
            if selection & (1 << bit) != 0 {
                catalog.nonminimal_by_singletons[selection] |= catalog.proper_supersets[bit];
            }
            bit += 1;
        }
        selection += 1;
    }
    chunk = 0;
    while chunk < 3 {
        selection = 0;
        while selection < 1 << 7 {
            let mut bit = 0_usize;
            while bit < 7 {
                let pattern = WIDTH + chunk * 7 + bit;
                if pattern < WIDTH + 21 && selection & (1 << bit) != 0 {
                    catalog.nonminimal_by_pairs[chunk][selection] |=
                        catalog.proper_supersets[pattern];
                }
                bit += 1;
            }
            selection += 1;
        }
        chunk += 1;
    }
    let mut current_index = 0_usize;
    while current_index <= PLACEMENT_PATTERN_COUNT {
        let current = if current_index == PLACEMENT_PATTERN_COUNT {
            PlacementPatternSpec::EMPTY
        } else {
            catalog.patterns[current_index]
        };
        let mut slot = 0_usize;
        while slot < WIDTH * 3 {
            let column = slot / 3;
            let required_count = (slot % 3 + 1) as u8;
            let current_count = current.counts[column];
            let additional = required_count.saturating_sub(current_count);
            if additional != 0 && current.added_puyos + additional <= 3 {
                let mut count_code = 0_usize;
                let mut code_column = 0_usize;
                while code_column < WIDTH {
                    let count = if code_column == column {
                        required_count
                    } else {
                        current.counts[code_column]
                    };
                    count_code |= (count as usize) << (code_column * 2);
                    code_column += 1;
                }
                let next_pattern = catalog.pattern_by_counts[count_code];
                if next_pattern != u8::MAX {
                    let next_added = catalog.patterns[next_pattern as usize].added_puyos;
                    catalog.transitions_by_slot[current_index][slot] = (next_pattern as u64)
                        | (next_added as u64) << 8
                        | (column as u64) << 16
                        | (required_count as u64) << 24
                        | (catalog.pattern_slots[next_pattern as usize] as u64) << 32;
                    let mut maximum = next_added;
                    while maximum <= 3 {
                        catalog.next_slots_by_max_added[current_index][maximum as usize] |=
                            1_u32 << slot;
                        maximum += 1;
                    }
                }
            }
            slot += 1;
        }
        current_index += 1;
    }
    catalog
}

const PLACEMENT_CATALOG: PlacementCatalog = build_placement_catalog();

#[derive(Clone, Copy, Debug, Default)]
struct ResolvedVirtual {
    planes: [u128; PLANE_COUNT],
    chain_count: u8,
    score: u32,
    trigger_anchors: u128,
    remaining_structure: Option<RemainingStructure>,
}

#[derive(Clone, Copy, Debug, Default)]
struct CachedResolution {
    chain_count: u8,
    score: u32,
    trigger_anchors: u128,
    remaining_structure: RemainingStructure,
}

impl CachedResolution {
    #[inline(always)]
    fn from_resolved(resolved: ResolvedVirtual) -> Self {
        Self {
            chain_count: resolved.chain_count,
            score: resolved.score,
            trigger_anchors: resolved.trigger_anchors,
            remaining_structure: resolved
                .remaining_structure
                .expect("differential resolution must fuse terminal structure"),
        }
    }
}

#[derive(Clone, Copy, Debug, Default)]
struct ResolutionCacheEntry {
    garbage_mask: u128,
    resolved: CachedResolution,
    valid: bool,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct RemainingStructure {
    link_2: u8,
    link_3: u8,
    connection_edges: u8,
    extension_space: u8,
}

#[derive(Clone, Copy, Debug, Default)]
struct ComponentAnalysis {
    remaining: RemainingStructure,
    has_poppable: bool,
    normal: u128,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct VanishInfo {
    mask: u128,
    count: u32,
    connection_bonus: u16,
    color_count: u8,
}

#[derive(Clone, Copy, Debug, Default)]
struct ResolutionProgress {
    planes: [u128; PLANE_COUNT],
    chain_count: u8,
    score: u32,
    remaining_structure: Option<RemainingStructure>,
}

#[inline]
const fn cell_bit(x: usize, y: usize) -> u128 {
    1_u128 << (x * COLUMN_LANE_BITS + y)
}

const fn lane_mask(height: usize) -> u128 {
    let mut result = 0_u128;
    let mut x = 0_usize;
    while x < WIDTH {
        result |= ((1_u128 << height) - 1) << (x * COLUMN_LANE_BITS);
        x += 1;
    }
    result
}

const fn build_lane_selection_masks() -> [u128; 1 << WIDTH] {
    let mut result = [0_u128; 1 << WIDTH];
    let mut selection = 0_usize;
    while selection < result.len() {
        let mut column = 0_usize;
        while column < WIDTH {
            if selection & (1 << column) != 0 {
                result[selection] |= 0xffff_u128 << (column * COLUMN_LANE_BITS);
            }
            column += 1;
        }
        selection += 1;
    }
    result
}

const LANE_SELECTION_MASKS: [u128; 1 << WIDTH] = build_lane_selection_masks();

const fn row_mask(y: usize) -> u128 {
    let mut result = 0_u128;
    let mut x = 0_usize;
    while x < WIDTH {
        result |= cell_bit(x, y);
        x += 1;
    }
    result
}

#[inline(always)]
fn board_neighbors(mask: u128) -> u128 {
    ((mask >> COLUMN_LANE_BITS) | (mask << COLUMN_LANE_BITS) | (mask >> 1) | (mask << 1))
        & BOARD_MASK
}

#[inline(always)]
fn visible_neighbors(mask: u128) -> u128 {
    board_neighbors(mask) & VISIBLE_MASK
}

#[inline]
fn flood(seed: u128, plane: u128, visible_only: bool) -> u128 {
    let mut group = seed;
    loop {
        let neighbors = if visible_only {
            visible_neighbors(group)
        } else {
            board_neighbors(group)
        };
        let expanded = group | (neighbors & plane);
        if expanded == group {
            return group;
        }
        group = expanded;
    }
}

#[inline]
fn has_at_least_four_bits(value: u128) -> bool {
    let without_one = value & value.wrapping_sub(1);
    let without_two = without_one & without_one.wrapping_sub(1);
    let without_three = without_two & without_two.wrapping_sub(1);
    without_three != 0
}

#[inline]
fn shift_west_visible(mask: u128) -> u128 {
    mask >> COLUMN_LANE_BITS
}

#[inline]
fn shift_east_visible(mask: u128) -> u128 {
    (mask << COLUMN_LANE_BITS) & VISIBLE_MASK
}

#[inline]
fn shift_down_visible(mask: u128) -> u128 {
    mask >> 1
}

#[inline]
fn shift_up_visible(mask: u128) -> u128 {
    (mask << 1) & VISIBLE_MASK
}

/// Cells that belong to a visible four-connected component of size at least
/// four. This constant-work prefilter is exact; flood-fill is only required
/// for the components selected by the returned mask.
#[inline]
fn poppable_mask(plane: u128) -> u128 {
    let visible = plane & VISIBLE_MASK;
    let west = shift_west_visible(visible) & visible;
    let east = shift_east_visible(visible) & visible;
    let down = shift_down_visible(visible) & visible;
    let up = shift_up_visible(visible) & visible;
    let vertical_both = up & down;
    let horizontal_both = west & east;
    let vertical_either = up | down;
    let horizontal_either = west | east;
    let degree_three = (vertical_both & horizontal_either) | (horizontal_both & vertical_either);
    let degree_two = vertical_both | horizontal_both | (vertical_either & horizontal_either);
    let connected_degree_two = (shift_west_visible(degree_two) & degree_two)
        | (shift_east_visible(degree_two) & degree_two)
        | (shift_down_visible(degree_two) & degree_two)
        | (shift_up_visible(degree_two) & degree_two);
    let seeds = degree_three | connected_degree_two;
    (seeds | visible_neighbors(seeds)) & visible
}

fn lower_board_is_compact(occupied: u128) -> bool {
    let lower = occupied & VISIBLE_MASK;
    lower & lower.wrapping_add(ROW_ZERO_MASK) == 0
}

#[cfg(test)]
fn lower_board_is_compact_exact(occupied: u128) -> bool {
    (0..WIDTH).all(|column| {
        let lower = ((occupied >> (column * COLUMN_LANE_BITS)) as u16) & 0x1fff;
        lower == 0 || lower == (1_u16 << (u16::BITS - lower.leading_zeros())) - 1
    })
}

#[cfg(target_arch = "x86_64")]
#[inline]
fn differential_gravity_supported() -> bool {
    std::arch::is_x86_feature_detected!("bmi1")
        && std::arch::is_x86_feature_detected!("bmi2")
        && std::arch::is_x86_feature_detected!("popcnt")
}

#[cfg(not(target_arch = "x86_64"))]
#[inline]
fn differential_gravity_supported() -> bool {
    false
}

fn column_heights(occupied: u128) -> [u8; WIDTH] {
    let mut result = [0_u8; WIDTH];
    for (x, value) in result.iter_mut().enumerate() {
        let column = ((occupied >> (x * COLUMN_LANE_BITS)) as u16) & 0x3fff;
        *value = if column == 0 {
            0
        } else {
            (u16::BITS - column.leading_zeros()) as u8
        };
    }
    result
}

fn reachable_columns(heights: &[u8; WIDTH]) -> u8 {
    let mut open = 0_u8;
    for (x, height) in heights.iter().copied().enumerate() {
        if usize::from(height) < VISIBLE_HEIGHT {
            open |= 1_u8 << x;
        }
    }
    let mut reachable = open & ((1_u8 << 2) | (1_u8 << 3));
    loop {
        let expanded = reachable | (((reachable << 1) | (reachable >> 1)) & open & 0x3f);
        if expanded == reachable {
            return reachable;
        }
        reachable = expanded;
    }
}

fn landing_mask(heights: &[u8; WIDTH], reachable: u8) -> u128 {
    let mut result = 0_u128;
    for (x, height) in heights.iter().copied().enumerate() {
        if reachable & (1_u8 << x) != 0 && usize::from(height) < VISIBLE_HEIGHT {
            result |= cell_bit(x, usize::from(height));
        }
    }
    result
}

#[inline(always)]
fn extract_components_impl(
    planes: &[u128; PLANE_COUNT],
    occupied: u128,
    landing: u128,
    heights: &[u8; WIDTH],
    reachable: u8,
    max_added_puyos: u8,
) -> ComponentSet {
    let mut result = ComponentSet {
        landing,
        base_normal: planes
            .iter()
            .copied()
            .take(NORMAL_COLOR_COUNT)
            .fold(0_u128, |value, plane| value | plane),
        base_occupied: occupied,
        incremental_resolution_supported: differential_gravity_supported()
            && occupied & HIDDEN_MASK == 0
            && lower_board_is_compact(occupied)
            && planes
                .iter()
                .copied()
                .take(NORMAL_COLOR_COUNT)
                .all(|plane| poppable_mask(plane) == 0),
        ..ComponentSet::default()
    };
    let mut stack_mask = 0_u128;
    let mut capacities = [0_u8; WIDTH];
    for (column, height) in heights.iter().copied().enumerate() {
        if reachable & (1_u8 << column) == 0 {
            continue;
        }
        let capacity = (VISIBLE_HEIGHT as u8)
            .saturating_sub(height)
            .min(max_added_puyos);
        capacities[column] = capacity;
        if capacity != 0 {
            stack_mask |=
                ((1_u128 << capacity) - 1) << (column * COLUMN_LANE_BITS + usize::from(height));
        }
    }
    for (column, capacity) in capacities.iter().copied().enumerate() {
        for offset in 1..usize::from(capacity) {
            let lower = column * 3 + offset - 1;
            let upper = lower + 1;
            result.stack_neighbors[lower] |= 1_u32 << upper;
            result.stack_neighbors[upper] |= 1_u32 << lower;
        }
    }
    for left_column in 0..WIDTH - 1 {
        let right_column = left_column + 1;
        for left_offset in 0..usize::from(capacities[left_column]) {
            let right_offset = isize::from(heights[left_column]) + left_offset as isize
                - isize::from(heights[right_column]);
            if !(0..isize::from(capacities[right_column])).contains(&right_offset) {
                continue;
            }
            let left_slot = left_column * 3 + left_offset;
            let right_slot = right_column * 3 + right_offset as usize;
            result.stack_neighbors[left_slot] |= 1_u32 << right_slot;
            result.stack_neighbors[right_slot] |= 1_u32 << left_slot;
        }
    }
    for column in 0..WIDTH {
        for count in 1..=3 {
            let slot = column * 3 + count - 1;
            result.stack_prefix_neighbors[column][count] =
                result.stack_prefix_neighbors[column][count - 1] | result.stack_neighbors[slot];
        }
    }
    for (plane_index, plane) in planes.iter().copied().enumerate().take(NORMAL_COLOR_COUNT) {
        let mut remaining = plane;
        while remaining != 0 {
            let seed = 1_u128 << remaining.trailing_zeros();
            let mask = flood(seed, plane, false);
            remaining &= !mask;
            let size = mask.count_ones() as u8;
            let connection_edges = ((mask & (mask >> COLUMN_LANE_BITS)).count_ones()
                + (mask & (mask >> 1)).count_ones()) as u8;
            let neighbors = board_neighbors(mask);
            let stack_frontier = neighbors & stack_mask;
            let mut frontier_slots = 0_u32;
            let mut extension_columns = 0_u8;
            for (column, height) in heights.iter().copied().enumerate() {
                let slots = ((stack_frontier >> (column * COLUMN_LANE_BITS + usize::from(height)))
                    & 0x07) as u32;
                frontier_slots |= slots << (column * 3);
                extension_columns |= u8::from(slots & 1 != 0) << column;
            }
            result.push(Component {
                mask,
                extension_columns,
                frontier_slots,
                color: plane_index as u8,
                size,
                connection_edges,
            });
        }
    }
    result
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "popcnt")]
unsafe fn extract_components_popcnt(
    planes: &[u128; PLANE_COUNT],
    occupied: u128,
    landing: u128,
    heights: &[u8; WIDTH],
    reachable: u8,
    max_added_puyos: u8,
) -> ComponentSet {
    extract_components_impl(
        planes,
        occupied,
        landing,
        heights,
        reachable,
        max_added_puyos,
    )
}

fn extract_components(
    planes: &[u128; PLANE_COUNT],
    occupied: u128,
    landing: u128,
    heights: &[u8; WIDTH],
    reachable: u8,
    max_added_puyos: u8,
) -> ComponentSet {
    #[cfg(target_arch = "x86_64")]
    if differential_gravity_supported() {
        // SAFETY: the runtime feature guard includes POPCNT.
        return unsafe {
            extract_components_popcnt(
                planes,
                occupied,
                landing,
                heights,
                reachable,
                max_added_puyos,
            )
        };
    }
    extract_components_impl(
        planes,
        occupied,
        landing,
        heights,
        reachable,
        max_added_puyos,
    )
}

#[cfg(test)]
fn connection_candidate_count_exact(components: &[Component], landing: u128) -> u8 {
    let mut count = 0_u8;
    let mut cells = landing;
    while cells != 0 {
        let cell = 1_u128 << cells.trailing_zeros();
        cells &= cells - 1;
        let adjacent = board_neighbors(cell);
        for color in 0..NORMAL_COLOR_COUNT as u8 {
            let mut component_count = 0_u8;
            for component in components {
                if component.color == color && component.mask & adjacent != 0 {
                    component_count += 1;
                }
            }
            count += u8::from(component_count >= 2);
        }
    }
    count
}

fn mirror_mask(mask: u128) -> u128 {
    let mut result = 0_u128;
    for x in 0..WIDTH {
        let column = (mask >> (x * COLUMN_LANE_BITS)) & 0xffff;
        result |= column << ((WIDTH - 1 - x) * COLUMN_LANE_BITS);
    }
    result
}

fn compare_cell_sequences(mut left: u128, mut right: u128) -> Ordering {
    loop {
        match (left == 0, right == 0) {
            (true, true) => return Ordering::Equal,
            (true, false) => return Ordering::Less,
            (false, true) => return Ordering::Greater,
            (false, false) => {
                let left_index = left.trailing_zeros();
                let right_index = right.trailing_zeros();
                match left_index.cmp(&right_index) {
                    Ordering::Equal => {
                        left &= left - 1;
                        right &= right - 1;
                    }
                    ordering => return ordering,
                }
            }
        }
    }
}

fn canonical_mask(mask: u128) -> u128 {
    let mirrored = mirror_mask(mask);
    if compare_cell_sequences(mask, mirrored) == Ordering::Greater {
        mirrored
    } else {
        mask
    }
}

const CANDIDATE_JSON_CAPACITY: usize = 1024;
const CANDIDATE_SUFFIX_CAPACITY: usize = 96;

struct CandidateJson<const CAPACITY: usize = CANDIDATE_JSON_CAPACITY> {
    bytes: [MaybeUninit<u8>; CAPACITY],
    len: usize,
}

impl<const CAPACITY: usize> CandidateJson<CAPACITY> {
    const fn new() -> Self {
        Self {
            bytes: [MaybeUninit::uninit(); CAPACITY],
            len: 0,
        }
    }

    fn clear(&mut self) {
        self.len = 0;
    }

    fn push(&mut self, value: u8) {
        debug_assert!(self.len < self.bytes.len());
        self.bytes[self.len].write(value);
        self.len += 1;
    }

    fn extend(&mut self, value: &[u8]) {
        assert!(self.len + value.len() <= self.bytes.len());
        // SAFETY: the capacity check above proves the destination range is
        // in-bounds, and `value` cannot alias this private workspace.
        unsafe {
            std::ptr::copy_nonoverlapping(
                value.as_ptr(),
                self.bytes.as_mut_ptr().cast::<u8>().add(self.len),
                value.len(),
            );
        }
        self.len += value.len();
    }

    fn decimal(&mut self, mut value: u32) {
        let mut digits = [0_u8; 10];
        let mut cursor = digits.len();
        loop {
            cursor -= 1;
            digits[cursor] = b'0' + (value % 10) as u8;
            value /= 10;
            if value == 0 {
                break;
            }
        }
        self.extend(&digits[cursor..]);
    }

    fn cells(&mut self, mut mask: u128) {
        self.push(b'[');
        let mut first = true;
        while mask != 0 {
            let index = mask.trailing_zeros() as usize;
            mask &= mask - 1;
            if !first {
                self.push(b',');
            }
            first = false;
            self.push(b'[');
            self.decimal((index / COLUMN_LANE_BITS) as u32);
            self.push(b',');
            self.decimal((index % COLUMN_LANE_BITS) as u32);
            self.push(b']');
        }
        self.push(b']');
    }

    fn as_slice(&self) -> &[u8] {
        // SAFETY: every element below `len` is initialized by `push` or
        // `extend`, and neither method can advance beyond the backing array.
        unsafe { std::slice::from_raw_parts(self.bytes.as_ptr().cast(), self.len) }
    }
}

fn sha256(json: &mut CandidateJson) -> [u8; 32] {
    let message_len = json.len;
    let padded_len = (message_len + 9).div_ceil(64) * 64;
    assert!(padded_len <= json.bytes.len());
    json.bytes[message_len].write(0x80);
    for byte in &mut json.bytes[message_len + 1..padded_len - 8] {
        byte.write(0);
    }
    for (target, source) in json.bytes[padded_len - 8..padded_len]
        .iter_mut()
        .zip(((message_len as u64) * 8).to_be_bytes())
    {
        target.write(source);
    }
    // SAFETY: the canonical bytes and padding above initialize exactly
    // `padded_len` bytes, which is a non-zero multiple of 64.
    let blocks = unsafe {
        std::slice::from_raw_parts(json.bytes.as_ptr().cast::<[u8; 64]>(), padded_len / 64)
    };
    let mut state = [
        0x6a09_e667,
        0xbb67_ae85,
        0x3c6e_f372,
        0xa54f_f53a,
        0x510e_527f,
        0x9b05_688c,
        0x1f83_d9ab,
        0x5be0_cd19,
    ];
    compress256(&mut state, blocks);
    let mut result = [0_u8; 32];
    for (target, word) in result.as_chunks_mut::<4>().0.iter_mut().zip(state) {
        target.copy_from_slice(&word.to_be_bytes());
    }
    result
}

fn encode_stable_candidate_identity(
    identity: CanonicalCandidateIdentity,
    json: &mut CandidateJson,
) {
    json.clear();
    let color = match identity.trigger_color {
        0 => b"RED".as_slice(),
        1 => b"BLUE".as_slice(),
        2 => b"GREEN".as_slice(),
        3 => b"YELLOW".as_slice(),
        4 => b"PURPLE".as_slice(),
        _ => unreachable!("candidate colors are validated by the evaluator"),
    };
    json.extend(b"[\"");
    json.extend(color);
    json.extend(b"\",");
    json.cells(identity.placements_mask);
    json.push(b',');
    json.cells(identity.anchor_mask);
}

fn encode_stable_candidate_head(candidate: &QuiescenceCandidate, json: &mut CandidateJson) {
    encode_stable_candidate_identity(CanonicalCandidateIdentity::new(candidate), json);
}

fn append_stable_candidate_suffix<const CAPACITY: usize>(
    candidate: &QuiescenceCandidate,
    json: &mut CandidateJson<CAPACITY>,
) {
    for value in [
        candidate.chain_count as u32,
        candidate.chain_score,
        candidate.required_key_count as u32,
        candidate.trigger_height as u32,
        candidate.remaining_link_2 as u32,
        candidate.remaining_link_3 as u32,
        candidate.remaining_connection_edges as u32,
        candidate.extension_space as u32,
    ] {
        json.push(b',');
        json.decimal(value);
    }
    json.push(b']');
}

fn encode_stable_candidate(candidate: &QuiescenceCandidate, json: &mut CandidateJson) {
    encode_stable_candidate_head(candidate, json);
    append_stable_candidate_suffix(candidate, json);
}

fn encode_stable_candidate_with_suffix(
    identity: CanonicalCandidateIdentity,
    suffix: &[u8],
    json: &mut CandidateJson,
) {
    encode_stable_candidate_identity(identity, json);
    json.extend(suffix);
}

#[cfg(test)]
fn stable_candidate_digest(candidate: &QuiescenceCandidate) -> [u8; 32] {
    let mut json = CandidateJson::new();
    encode_stable_candidate(candidate, &mut json);
    sha256(&mut json)
}

fn same_candidate_signature(left: &QuiescenceCandidate, right: &QuiescenceCandidate) -> bool {
    left.trigger_color == right.trigger_color
        && canonical_mask(left.placements_mask) == canonical_mask(right.placements_mask)
        && canonical_mask(left.anchor_mask) == canonical_mask(right.anchor_mask)
        && left.chain_count == right.chain_count
        && left.chain_score == right.chain_score
        && left.required_key_count == right.required_key_count
        && left.trigger_height == right.trigger_height
        && left.remaining_link_2 == right.remaining_link_2
        && left.remaining_link_3 == right.remaining_link_3
        && left.remaining_connection_edges == right.remaining_connection_edges
        && left.extension_space == right.extension_space
}

fn candidate_rank_key(candidate: &QuiescenceCandidate) -> u128 {
    (u128::from(candidate.chain_count) << 72)
        | (u128::from(u8::MAX - candidate.required_key_count) << 64)
        | (u128::from(candidate.chain_score) << 32)
        | (u128::from(candidate.remaining_link_3) << 24)
        | (u128::from(candidate.remaining_link_2) << 16)
        | (u128::from(candidate.remaining_connection_edges) << 8)
        | u128::from(candidate.extension_space)
}

#[inline(always)]
fn resolved_candidate_rank_key(added_puyos: u8, resolved: CachedResolution) -> u128 {
    let remaining = resolved.remaining_structure;
    (u128::from(resolved.chain_count) << 72)
        | (u128::from(u8::MAX - added_puyos) << 64)
        | (u128::from(resolved.score) << 32)
        | (u128::from(remaining.link_3) << 24)
        | (u128::from(remaining.link_2) << 16)
        | (u128::from(remaining.connection_edges) << 8)
        | u128::from(remaining.extension_space)
}

fn compare_candidate_rank_suffix(
    left: &QuiescenceCandidate,
    right: &QuiescenceCandidate,
) -> Ordering {
    left.trigger_protection
        .partial_cmp(&right.trigger_protection)
        .unwrap_or(Ordering::Equal)
        .then_with(|| right.trigger_height.cmp(&left.trigger_height))
}

#[cfg(test)]
fn compare_candidate_rank_prefix_exact(
    left: &QuiescenceCandidate,
    right: &QuiescenceCandidate,
) -> Ordering {
    left.chain_count
        .cmp(&right.chain_count)
        .then_with(|| right.required_key_count.cmp(&left.required_key_count))
        .then_with(|| left.chain_score.cmp(&right.chain_score))
        .then_with(|| left.remaining_link_3.cmp(&right.remaining_link_3))
        .then_with(|| left.remaining_link_2.cmp(&right.remaining_link_2))
        .then_with(|| {
            left.remaining_connection_edges
                .cmp(&right.remaining_connection_edges)
        })
        .then_with(|| left.extension_space.cmp(&right.extension_space))
        .then_with(|| {
            left.trigger_protection
                .partial_cmp(&right.trigger_protection)
                .unwrap_or(Ordering::Equal)
        })
        .then_with(|| right.trigger_height.cmp(&left.trigger_height))
}

struct CandidateTieBreakComparison {
    ordering: Ordering,
    digest: [u8; 32],
    sha256_calls: u8,
}

#[derive(Clone, Copy, Eq, PartialEq)]
struct CanonicalCandidateIdentity {
    placements_mask: u128,
    anchor_mask: u128,
    trigger_color: u8,
}

impl CanonicalCandidateIdentity {
    fn new(candidate: &QuiescenceCandidate) -> Self {
        Self {
            placements_mask: canonical_mask(candidate.placements_mask),
            anchor_mask: canonical_mask(candidate.anchor_mask),
            trigger_color: candidate.trigger_color,
        }
    }
}

#[derive(Clone, Copy)]
struct CandidateDigestCacheEntry {
    identity: CanonicalCandidateIdentity,
    digest: [u8; 32],
}

const CANDIDATE_DIGEST_CACHE_SIZE: usize = 16;

struct CandidateTieBreakWorkspace {
    encodings: [CandidateJson; 2],
    suffix: CandidateJson<CANDIDATE_SUFFIX_CAPACITY>,
    digest_cache: [MaybeUninit<CandidateDigestCacheEntry>; CANDIDATE_DIGEST_CACHE_SIZE],
    digest_cache_len: usize,
    best_slot: usize,
    best_identity: Option<CanonicalCandidateIdentity>,
    best_digest: [u8; 32],
    best_valid: bool,
    suffix_valid: bool,
}

impl Default for CandidateTieBreakWorkspace {
    fn default() -> Self {
        Self {
            encodings: [CandidateJson::new(), CandidateJson::new()],
            suffix: CandidateJson::new(),
            digest_cache: [MaybeUninit::uninit(); CANDIDATE_DIGEST_CACHE_SIZE],
            digest_cache_len: 0,
            best_slot: 0,
            best_identity: None,
            best_digest: [0; 32],
            best_valid: false,
            suffix_valid: false,
        }
    }
}

impl CandidateTieBreakWorkspace {
    fn invalidate_best(&mut self) {
        self.best_valid = false;
        self.suffix_valid = false;
        self.digest_cache_len = 0;
    }

    fn scratch_slot(&self) -> usize {
        self.best_slot ^ 1
    }

    fn cached_digest(&self, identity: CanonicalCandidateIdentity) -> Option<[u8; 32]> {
        // SAFETY: `remember_digest` initializes every entry below
        // `digest_cache_len`, which never exceeds the array capacity.
        let entries = unsafe {
            std::slice::from_raw_parts(
                self.digest_cache
                    .as_ptr()
                    .cast::<CandidateDigestCacheEntry>(),
                self.digest_cache_len,
            )
        };
        entries
            .iter()
            .find(|entry| entry.identity == identity)
            .map(|entry| entry.digest)
    }

    fn remember_digest(&mut self, identity: CanonicalCandidateIdentity, digest: [u8; 32]) {
        if self.digest_cache_len == self.digest_cache.len() {
            return;
        }
        self.digest_cache[self.digest_cache_len]
            .write(CandidateDigestCacheEntry { identity, digest });
        self.digest_cache_len += 1;
    }

    fn digest_candidate(
        &mut self,
        candidate: &QuiescenceCandidate,
        promote_to_best: bool,
    ) -> [u8; 32] {
        let candidate_slot = self.scratch_slot();
        encode_stable_candidate(candidate, &mut self.encodings[candidate_slot]);
        let digest = sha256(&mut self.encodings[candidate_slot]);
        if promote_to_best {
            let identity = CanonicalCandidateIdentity::new(candidate);
            self.best_slot = candidate_slot;
            self.best_identity = Some(identity);
            self.best_digest = digest;
            self.best_valid = true;
            self.remember_digest(identity, digest);
        }
        digest
    }

    fn compare(
        &mut self,
        candidate: &QuiescenceCandidate,
        best: &QuiescenceCandidate,
    ) -> CandidateTieBreakComparison {
        if !self.suffix_valid {
            self.suffix.clear();
            append_stable_candidate_suffix(best, &mut self.suffix);
            self.suffix_valid = true;
        }
        let candidate_slot = self.scratch_slot();
        let mut sha256_calls = 0;
        if !self.best_valid {
            let identity = CanonicalCandidateIdentity::new(best);
            encode_stable_candidate_with_suffix(
                identity,
                self.suffix.as_slice(),
                &mut self.encodings[self.best_slot],
            );
            self.best_digest = sha256(&mut self.encodings[self.best_slot]);
            self.best_identity = Some(identity);
            self.best_valid = true;
            self.remember_digest(identity, self.best_digest);
            sha256_calls += 1;
        }
        let candidate_identity = CanonicalCandidateIdentity::new(candidate);
        if Some(candidate_identity) == self.best_identity {
            return CandidateTieBreakComparison {
                ordering: Ordering::Equal,
                digest: self.best_digest,
                sha256_calls,
            };
        }
        let candidate_digest = if let Some(digest) = self.cached_digest(candidate_identity) {
            digest
        } else {
            encode_stable_candidate_with_suffix(
                candidate_identity,
                self.suffix.as_slice(),
                &mut self.encodings[candidate_slot],
            );
            let digest = sha256(&mut self.encodings[candidate_slot]);
            self.remember_digest(candidate_identity, digest);
            sha256_calls += 1;
            digest
        };
        let ordering = candidate_digest.cmp(&self.best_digest);
        if ordering == Ordering::Greater {
            self.best_slot = candidate_slot;
            self.best_identity = Some(candidate_identity);
            self.best_digest = candidate_digest;
        }
        CandidateTieBreakComparison {
            ordering,
            digest: candidate_digest,
            sha256_calls,
        }
    }
}

#[cfg(test)]
fn virtual_trigger_anchors(plane: u128, placements: u128) -> Option<u128> {
    let combined = (plane | placements) & VISIBLE_MASK;
    let mut origins = placements;
    let mut visited = 0_u128;
    while origins != 0 {
        let origin = 1_u128 << origins.trailing_zeros();
        origins &= origins - 1;
        if visited & origin != 0 {
            continue;
        }
        let component = flood(origin, combined, true);
        visited |= component;
        let anchors = component & !placements;
        if component.count_ones() >= 4 && anchors != 0 {
            return Some(anchors);
        }
    }
    None
}

#[cfg(test)]
fn pattern_from_columns(columns: &[u8], heights: &[u8; WIDTH]) -> PlacementPattern {
    let mut counts = [0_u8; WIDTH];
    for column in columns {
        counts[usize::from(*column)] += 1;
    }
    let mut mask = 0_u128;
    let mut selected_columns = 0_u8;
    for (x, count) in counts.iter().copied().enumerate() {
        selected_columns |= u8::from(count != 0) << x;
        for offset in 0..count {
            let y = heights[x] + offset;
            mask |= cell_bit(x, usize::from(y));
        }
    }
    PlacementPattern {
        mask,
        counts,
        columns: selected_columns,
        minimum_column: columns[0],
    }
}

#[cfg(test)]
fn valid_pattern(columns: &[u8], heights: &[u8; WIDTH], reachable: u8) -> Option<PlacementPattern> {
    let mut counts = [0_u8; WIDTH];
    for column in columns {
        if reachable & (1_u8 << column) == 0 {
            return None;
        }
        counts[usize::from(*column)] += 1;
    }
    if counts
        .iter()
        .copied()
        .enumerate()
        .any(|(x, count)| usize::from(heights[x] + count) > VISIBLE_HEIGHT)
    {
        return None;
    }
    Some(pattern_from_columns(columns, heights))
}

#[inline(always)]
fn pattern_from_spec(specification: &PlacementPatternSpec, landing: u128) -> PlacementPattern {
    let [first_columns, second_columns, third_columns] = specification.count_at_least_columns;
    // SAFETY: the catalog stores six-bit column selections and column IDs in
    // the fixed board range.
    let mask = unsafe {
        (landing & *LANE_SELECTION_MASKS.get_unchecked(usize::from(first_columns)))
            | ((landing & *LANE_SELECTION_MASKS.get_unchecked(usize::from(second_columns))) << 1)
            | ((landing & *LANE_SELECTION_MASKS.get_unchecked(usize::from(third_columns))) << 2)
    };
    PlacementPattern {
        mask,
        #[cfg(test)]
        counts: specification.counts,
        columns: first_columns,
        minimum_column: specification.minimum_column,
    }
}

#[inline(always)]
fn pattern_minimum_height(mut columns: u8, heights: &[u8; WIDTH]) -> u8 {
    let mut minimum = u8::MAX;
    while columns != 0 {
        let column = columns.trailing_zeros() as usize;
        columns &= columns - 1;
        // SAFETY: placement column masks contain only the six board columns.
        minimum = minimum.min(unsafe { *heights.get_unchecked(column) });
    }
    minimum
}

fn valid_catalog_patterns(heights: &[u8; WIDTH], reachable: u8, max_added_puyos: u8) -> u128 {
    if heights
        .iter()
        .copied()
        .any(|height| usize::from(height) > VISIBLE_HEIGHT)
    {
        return 0;
    }
    let mut valid = 0_u128;
    for added_puyos in 1..=max_added_puyos {
        valid |= PLACEMENT_CATALOG.by_added_puyos[usize::from(added_puyos)];
    }
    for (column, height) in heights.iter().copied().enumerate() {
        if reachable & (1_u8 << column) == 0 {
            valid &= !PLACEMENT_CATALOG.count_at_least[column][1];
            continue;
        }
        let capacity = (VISIBLE_HEIGHT as u8).saturating_sub(height).min(3);
        if capacity < 3 {
            valid &= !PLACEMENT_CATALOG.count_at_least[column][usize::from(capacity + 1)];
        }
    }
    valid
}

#[derive(Clone, Copy, Debug)]
struct FrontierResolutionSpec {
    group: u8,
    anchors: u128,
}

impl Default for FrontierResolutionSpec {
    fn default() -> Self {
        Self {
            group: u8::MAX,
            anchors: 0,
        }
    }
}

#[derive(Clone, Copy)]
struct TriggerCandidateRecord {
    trigger_components: u32,
    pattern_index: u8,
    plane_index: u8,
}

struct TriggerFrontier {
    candidate_patterns: [u128; NORMAL_COLOR_COUNT],
    candidate_records: [MaybeUninit<TriggerCandidateRecord>; MAX_FRONTIER_CANDIDATE_RECORDS],
    candidate_record_count: usize,
    candidate_group_resolutions: [u16; CANDIDATE_GROUP_COUNT],
    resolution_anchors: [u128; RESOLUTION_CACHE_SIZE],
    resolution_precomputed: [MaybeUninit<CachedResolution>; RESOLUTION_CACHE_SIZE],
    resolution_precomputed_rank_keys: [MaybeUninit<u128>; RESOLUTION_CACHE_SIZE],
    precomputed_groups: u32,
    candidate_groups: [u64; 4],
    candidate_count: u32,
}

impl TriggerFrontier {
    #[inline(always)]
    fn new() -> Self {
        Self {
            candidate_patterns: [0_u128; NORMAL_COLOR_COUNT],
            candidate_records: [MaybeUninit::uninit(); MAX_FRONTIER_CANDIDATE_RECORDS],
            candidate_record_count: 0,
            candidate_group_resolutions: [0; CANDIDATE_GROUP_COUNT],
            resolution_anchors: [0_u128; RESOLUTION_CACHE_SIZE],
            resolution_precomputed: [MaybeUninit::uninit(); RESOLUTION_CACHE_SIZE],
            resolution_precomputed_rank_keys: [MaybeUninit::uninit(); RESOLUTION_CACHE_SIZE],
            precomputed_groups: 0,
            candidate_groups: [0_u64; 4],
            candidate_count: 0,
        }
    }

    #[inline(always)]
    fn record_candidate(
        &mut self,
        plane_index: usize,
        pattern_index: usize,
        trigger_components: u32,
    ) {
        debug_assert!(self.candidate_record_count < self.candidate_records.len());
        self.candidate_records[self.candidate_record_count].write(TriggerCandidateRecord {
            trigger_components,
            pattern_index: pattern_index as u8,
            plane_index: plane_index as u8,
        });
        self.candidate_record_count += 1;
    }
}

#[inline(always)]
fn minimal_trigger_patterns(triggered: u128, valid_patterns: u128) -> u128 {
    let singleton_selection = triggered as usize & ((1 << WIDTH) - 1);
    let pair_selection = ((triggered >> WIDTH) as u32) & ((1 << 21) - 1);
    let nonminimal = PLACEMENT_CATALOG.nonminimal_by_singletons[singleton_selection]
        | PLACEMENT_CATALOG.nonminimal_by_pairs[0][(pair_selection & 0x7f) as usize]
        | PLACEMENT_CATALOG.nonminimal_by_pairs[1][((pair_selection >> 7) & 0x7f) as usize]
        | PLACEMENT_CATALOG.nonminimal_by_pairs[2][((pair_selection >> 14) & 0x7f) as usize];
    triggered & !nonminimal & valid_patterns
}

#[inline(always)]
fn build_trigger_frontier_impl<const PROFILE: bool>(
    result: &mut TriggerFrontier,
    components: &ComponentSet,
    max_added_puyos: u8,
    valid_patterns: u128,
    profile_stage: Option<&AtomicU8>,
    profile_counts: &mut EvaluationProfileCounts,
) {
    #[derive(Clone, Copy)]
    struct FrontierState {
        connected_components: u32,
        frontier: u32,
        group_size: u8,
        added_puyos: u8,
        pattern_index: u8,
    }

    #[inline(always)]
    fn simple_component_candidates(
        component_size: u8,
        component_frontier: u32,
        stack_neighbors: &[u32; STACK_SLOT_COUNT],
    ) -> u128 {
        #[inline(always)]
        fn patterns_for_slots(slots: u32) -> u128 {
            PLACEMENT_CATALOG.slot_pattern_unions[0][(slots & 0x3f) as usize]
                | PLACEMENT_CATALOG.slot_pattern_unions[1][((slots >> 6) & 0x3f) as usize]
                | PLACEMENT_CATALOG.slot_pattern_unions[2][((slots >> 12) & 0x3f) as usize]
        }

        let deficit = 4_u8.saturating_sub(component_size).max(1);
        if deficit == 1 {
            return patterns_for_slots(component_frontier);
        }
        let mut candidates = 0_u128;
        let mut first_slots = component_frontier;
        while first_slots != 0 {
            let first = first_slots.trailing_zeros() as usize;
            first_slots &= first_slots - 1;
            // SAFETY: component frontier bits use the 18-slot topology.
            let first_patterns = unsafe { *PLACEMENT_CATALOG.slot_patterns.get_unchecked(first) };
            let mut second_slots =
                (component_frontier | stack_neighbors[first]) & !(1_u32 << first);
            if deficit == 2 {
                candidates |= first_patterns & patterns_for_slots(second_slots);
                continue;
            }
            while second_slots != 0 {
                let second = second_slots.trailing_zeros() as usize;
                second_slots &= second_slots - 1;
                // SAFETY: neighbor bits use the 18-slot topology.
                let pair_patterns = first_patterns
                    & unsafe { *PLACEMENT_CATALOG.slot_patterns.get_unchecked(second) };
                if pair_patterns == 0 {
                    continue;
                }
                let selected = (1_u32 << first) | (1_u32 << second);
                let third_slots =
                    (component_frontier | stack_neighbors[first] | stack_neighbors[second])
                        & !selected;
                candidates |= pair_patterns & patterns_for_slots(third_slots);
            }
        }
        candidates
    }

    struct Expansion<'a, const PROFILED: bool> {
        component_topology: &'a [MaybeUninit<u64>; MAX_FRONTIER_COMPONENTS],
        stack_prefix_neighbors: &'a [[u32; 4]; WIDTH],
        stack_prefix_components: &'a [[u32; 4]; WIDTH],
        color_components: u32,
        max_added_puyos: u8,
        plane_index: u8,
        candidates: &'a mut u128,
        candidate_records:
            &'a mut [MaybeUninit<TriggerCandidateRecord>; MAX_FRONTIER_CANDIDATE_RECORDS],
        candidate_record_count: &'a mut usize,
        frontier_state_visits: &'a mut u32,
    }

    impl<const PROFILED: bool> Expansion<'_, PROFILED> {
        #[inline(always)]
        fn should_expand(&mut self, state: FrontierState, visited: &mut u128) -> bool {
            if PROFILED {
                *self.frontier_state_visits += 1;
            }
            if state.added_puyos != 0 {
                let pattern_index = state.pattern_index;
                let pattern_bit = 1_u128 << pattern_index;
                if *visited & pattern_bit != 0 {
                    return false;
                }
                *visited |= pattern_bit;
                let excluded = pattern_bit
                    | unsafe {
                        *PLACEMENT_CATALOG
                            .proper_subpatterns
                            .get_unchecked(usize::from(pattern_index))
                    };
                if excluded & *self.candidates != 0 {
                    return false;
                }
                if state.group_size >= 4 {
                    debug_assert!(*self.candidate_record_count < self.candidate_records.len());
                    self.candidate_records[*self.candidate_record_count].write(
                        TriggerCandidateRecord {
                            trigger_components: state.connected_components,
                            pattern_index,
                            plane_index: self.plane_index,
                        },
                    );
                    *self.candidate_record_count += 1;
                    *self.candidates |= pattern_bit;
                    return false;
                }
            }
            state.added_puyos < self.max_added_puyos
        }

        #[inline(always)]
        fn advance(&self, state: FrontierState, slot: usize) -> FrontierState {
            // SAFETY: frontier bits are limited to the fixed 18-slot topology.
            let transition = unsafe {
                *PLACEMENT_CATALOG
                    .transitions_by_slot
                    .get_unchecked(usize::from(state.pattern_index))
                    .get_unchecked(slot)
            };
            let next_pattern_index = transition as u8;
            let next_added = (transition >> 8) as u8;
            let column = ((transition >> 16) as u8) as usize;
            let required_count = ((transition >> 24) as u8) as usize;
            let next_placements = (transition >> 32) as u32;
            debug_assert!(next_pattern_index != u8::MAX && next_added <= self.max_added_puyos);
            let additional = next_added - state.added_puyos;
            let joined_components = (unsafe {
                *self
                    .stack_prefix_components
                    .get_unchecked(column)
                    .get_unchecked(required_count)
            }) & self.color_components
                & !state.connected_components;
            let mut new_frontier = state.frontier
                | unsafe {
                    *self
                        .stack_prefix_neighbors
                        .get_unchecked(column)
                        .get_unchecked(required_count)
                };
            let mut next_size = state.group_size.wrapping_add(additional);
            let mut joined = joined_components;
            while joined != 0 {
                let component_index = joined.trailing_zeros() as usize;
                joined &= joined - 1;
                // SAFETY: joined bits come from compact component-index
                // topology written during frontier construction.
                let topology = unsafe {
                    *self
                        .component_topology
                        .get_unchecked(component_index)
                        .assume_init_ref()
                };
                next_size = next_size.wrapping_add((topology >> 32) as u8);
                new_frontier |= topology as u32;
            }
            FrontierState {
                connected_components: state.connected_components | joined_components,
                frontier: new_frontier & !next_placements,
                group_size: next_size,
                added_puyos: next_added,
                pattern_index: next_pattern_index,
            }
        }

        #[inline(always)]
        fn valid_frontier(&self, state: FrontierState) -> u32 {
            // SAFETY: every search state references a catalog pattern or the
            // dedicated root entry, and max_added_puyos is validated in 1..=3.
            state.frontier
                & unsafe {
                    *PLACEMENT_CATALOG
                        .next_slots_by_max_added
                        .get_unchecked(usize::from(state.pattern_index))
                        .get_unchecked(usize::from(self.max_added_puyos))
                }
        }

        #[inline(always)]
        fn visit(&mut self, root: FrontierState, visited: &mut u128) {
            if !self.should_expand(root, visited) {
                return;
            }
            let mut first_slots = self.valid_frontier(root);
            while first_slots != 0 {
                let first = first_slots.trailing_zeros() as usize;
                first_slots &= first_slots - 1;
                let first_state = self.advance(root, first);
                if !self.should_expand(first_state, visited) {
                    continue;
                }
                let mut second_slots = self.valid_frontier(first_state);
                while second_slots != 0 {
                    let second = second_slots.trailing_zeros() as usize;
                    second_slots &= second_slots - 1;
                    let second_state = self.advance(first_state, second);
                    if !self.should_expand(second_state, visited) {
                        continue;
                    }
                    let mut third_slots = self.valid_frontier(second_state);
                    while third_slots != 0 {
                        let third = third_slots.trailing_zeros() as usize;
                        third_slots &= third_slots - 1;
                        let third_state = self.advance(second_state, third);
                        let _ = self.should_expand(third_state, visited);
                    }
                }
            }
        }
    }

    if valid_patterns == 0 || components.frontier_len == 0 {
        return;
    }
    let component_topology = &components.frontier_topology;
    let stack_neighbors = &components.stack_neighbors;
    let stack_prefix_neighbors = &components.stack_prefix_neighbors;
    let stack_prefix_components = &components.stack_prefix_components;
    let mut multi_component_planes = 0_u8;
    mark_profile_stage::<PROFILE>(
        profile_stage,
        &mut profile_counts.stage_entries,
        PROFILE_STAGE_PLACEMENT_FRONTIER,
    );
    for (plane_index, color_component_mask) in components
        .frontier_color_components
        .iter()
        .copied()
        .enumerate()
    {
        if color_component_mask == 0 {
            continue;
        }
        if color_component_mask & (color_component_mask - 1) == 0 {
            mark_profile_stage::<PROFILE>(
                profile_stage,
                &mut profile_counts.stage_entries,
                PROFILE_STAGE_PLACEMENT_SINGLE_FRONTIER,
            );
            if PROFILE {
                profile_counts.single_component_frontiers += 1;
            }
            let component_index = color_component_mask.trailing_zeros() as usize;
            // SAFETY: color-component bits refer to compact entries written by
            // the topology loop.
            let topology = unsafe { *component_topology[component_index].assume_init_ref() };
            let component_size = (topology >> 32) as u8;
            let component_frontier = topology as u32;
            let candidates =
                simple_component_candidates(component_size, component_frontier, stack_neighbors);
            let candidates = minimal_trigger_patterns(candidates, valid_patterns);
            result.candidate_patterns[plane_index] = candidates;
            let mut pending = candidates;
            while pending != 0 {
                let pattern_index = pending.trailing_zeros() as usize;
                pending &= pending - 1;
                result.record_candidate(plane_index, pattern_index, 1_u32 << component_index);
            }
        } else {
            multi_component_planes |= 1_u8 << plane_index;
            mark_profile_stage::<PROFILE>(
                profile_stage,
                &mut profile_counts.stage_entries,
                PROFILE_STAGE_PLACEMENT_MULTI_FRONTIER,
            );
            if PROFILE {
                profile_counts.multi_component_frontiers += 1;
            }
            let mut roots = color_component_mask;
            while roots != 0 {
                let component_index = roots.trailing_zeros() as usize;
                roots &= roots - 1;
                // SAFETY: root bits refer to compact entries written by the
                // topology loop.
                let topology = unsafe { *component_topology[component_index].assume_init_ref() };
                let component_size = (topology >> 32) as u8;
                let component_frontier = topology as u32;
                let mut visited = 0_u128;
                Expansion::<PROFILE> {
                    component_topology,
                    stack_prefix_neighbors,
                    stack_prefix_components,
                    color_components: color_component_mask,
                    max_added_puyos,
                    plane_index: plane_index as u8,
                    candidates: &mut result.candidate_patterns[plane_index],
                    candidate_records: &mut result.candidate_records,
                    candidate_record_count: &mut result.candidate_record_count,
                    frontier_state_visits: &mut profile_counts.frontier_state_visits,
                }
                .visit(
                    FrontierState {
                        connected_components: 1_u32 << component_index,
                        frontier: component_frontier,
                        group_size: component_size,
                        added_puyos: 0,
                        pattern_index: ROOT_PLACEMENT_PATTERN_INDEX,
                    },
                    &mut visited,
                );
            }
        }
    }
    for (plane_index, candidates) in result.candidate_patterns.iter_mut().enumerate() {
        mark_profile_stage::<PROFILE>(
            profile_stage,
            &mut profile_counts.stage_entries,
            PROFILE_STAGE_PLACEMENT_DEDUPLICATION,
        );
        if multi_component_planes & (1_u8 << plane_index) != 0 {
            *candidates = minimal_trigger_patterns(*candidates, valid_patterns);
        }
    }
    mark_profile_stage::<PROFILE>(
        profile_stage,
        &mut profile_counts.stage_entries,
        PROFILE_STAGE_PLACEMENT_QUALIFICATION,
    );
    let mut resolution_spec_keys = [0_u32; RESOLUTION_CACHE_SIZE];
    let mut resolution_spec_anchors = [MaybeUninit::<u128>::uninit(); RESOLUTION_CACHE_SIZE];
    let mut resolution_spec_remaining =
        [MaybeUninit::<RemainingStructure>::uninit(); RESOLUTION_CACHE_SIZE];
    let mut resolution_spec_anchor_counts = [MaybeUninit::<u8>::uninit(); RESOLUTION_CACHE_SIZE];
    let mut resolution_spec_precomputed = 0_u32;
    let mut resolution_groups_by_added = [MaybeUninit::<[u8; 4]>::uninit(); RESOLUTION_CACHE_SIZE];
    let mut previous_trigger_components = 0_u32;
    let mut previous_resolution_spec = u8::MAX;
    let mut resolution_group_count = 0_usize;
    for record_index in 0..result.candidate_record_count {
        // SAFETY: the compact record count is advanced only after a record is initialized.
        let record = unsafe { *result.candidate_records[record_index].assume_init_ref() };
        let plane_index = usize::from(record.plane_index);
        let pattern_index = usize::from(record.pattern_index);
        if result.candidate_patterns[plane_index] & (1_u128 << pattern_index) == 0 {
            continue;
        }
        if PROFILE {
            profile_counts.qualified_candidates += 1;
        }
        let trigger_components = record.trigger_components;
        let added_puyos = PLACEMENT_CATALOG.patterns[pattern_index].added_puyos;
        let resolution_spec = if trigger_components == previous_trigger_components {
            usize::from(previous_resolution_spec)
        } else {
            let mut resolution_spec =
                usize::try_from(trigger_components.wrapping_mul(0x9e37_79b1) >> 27)
                    .expect("five-bit resolution hash fits usize");
            loop {
                if PROFILE {
                    profile_counts.resolution_group_comparisons += 1;
                }
                let existing = resolution_spec_keys[resolution_spec];
                if existing == trigger_components {
                    break;
                }
                if existing == 0 {
                    resolution_spec_keys[resolution_spec] = trigger_components;
                    resolution_groups_by_added[resolution_spec].write([0_u8; 4]);
                    let mut anchors = 0_u128;
                    let mut surface_remaining = components.base_remaining;
                    let mut component_bits = trigger_components;
                    while component_bits != 0 {
                        let component_index = component_bits.trailing_zeros() as usize;
                        component_bits &= component_bits - 1;
                        // SAFETY: trigger bits refer to initialized compact
                        // frontier component metadata.
                        anchors |= unsafe {
                            *components.frontier_masks[component_index].assume_init_ref()
                        } & VISIBLE_MASK;
                        // SAFETY: the same trigger bit refers to initialized size
                        // and edge metadata for this frontier component.
                        let topology = unsafe {
                            *components.frontier_topology[component_index].assume_init_ref()
                        };
                        let component_size = (topology >> 32) as u8;
                        let component_edges = (topology >> 40) as u8;
                        surface_remaining.link_2 -= u8::from(component_size == 2);
                        surface_remaining.link_3 -= u8::from(component_size == 3);
                        surface_remaining.connection_edges -= component_edges;
                    }
                    resolution_spec_anchors[resolution_spec].write(anchors);
                    let final_normal = components.base_normal & !anchors;
                    let final_occupied = components.base_occupied & !anchors;
                    if components.incremental_resolution_supported
                        && components.base_normal == components.base_occupied
                        && lower_board_is_compact(final_occupied)
                    {
                        // SAFETY: incremental support includes the POPCNT
                        // runtime feature check on x86-64.
                        let (anchor_count, updated_remaining) = unsafe {
                            precomputed_surface_with_popcnt(
                                final_normal,
                                final_occupied,
                                anchors,
                                0,
                                surface_remaining,
                            )
                        };
                        surface_remaining = updated_remaining;
                        resolution_spec_anchor_counts[resolution_spec].write(anchor_count as u8);
                        resolution_spec_precomputed |= 1_u32 << resolution_spec;
                    }
                    resolution_spec_remaining[resolution_spec].write(surface_remaining);
                    break;
                }
                resolution_spec = (resolution_spec + 1) & (RESOLUTION_CACHE_SIZE - 1);
            }
            previous_trigger_components = trigger_components;
            previous_resolution_spec = resolution_spec as u8;
            resolution_spec
        };
        // SAFETY: group slots are initialized with their resolution spec.
        let groups_by_added =
            unsafe { resolution_groups_by_added[resolution_spec].assume_init_mut() };
        let encoded_group = groups_by_added[usize::from(added_puyos)];
        let resolution_group = if encoded_group != 0 {
            usize::from(encoded_group - 1)
        } else if resolution_group_count < RESOLUTION_CACHE_SIZE {
            let resolution_group = resolution_group_count;
            groups_by_added[usize::from(added_puyos)] = resolution_group as u8 + 1;
            // SAFETY: every compact resolution spec has initialized anchors.
            let anchors = unsafe { *resolution_spec_anchors[resolution_spec].assume_init_ref() };
            result.resolution_anchors[resolution_group] = anchors;
            if resolution_spec_precomputed & (1_u32 << resolution_spec) != 0 {
                // SAFETY: a resolution spec is published in the hash table
                // only after its remaining-structure entry is initialized.
                let surface_remaining =
                    unsafe { *resolution_spec_remaining[resolution_spec].assume_init_ref() };
                // SAFETY: the precomputed-spec bit publishes the anchor count.
                let first_count = u32::from(unsafe {
                    *resolution_spec_anchor_counts[resolution_spec].assume_init_ref()
                }) + u32::from(added_puyos);
                let precomputed = CachedResolution {
                    chain_count: 1,
                    score: first_count * 10 * u32::from(connection_bonus(first_count).max(1)),
                    trigger_anchors: anchors,
                    remaining_structure: surface_remaining,
                };
                result.resolution_precomputed[resolution_group].write(precomputed);
                result.resolution_precomputed_rank_keys[resolution_group]
                    .write(resolved_candidate_rank_key(added_puyos, precomputed));
                result.precomputed_groups |= 1_u32 << resolution_group;
            }
            resolution_group_count += 1;
            if PROFILE {
                profile_counts.resolution_groups += 1;
                profile_counts.precomputed_resolution_groups +=
                    u32::from(result.precomputed_groups & (1_u32 << resolution_group) != 0);
            }
            resolution_group
        } else {
            RESOLUTION_CACHE_SIZE
        };
        let encoded_resolution_group = if resolution_group < RESOLUTION_CACHE_SIZE {
            resolution_group as u8 + 1
        } else {
            u8::MAX
        };
        result.candidate_count += 1;
        let orbit_index = usize::from(PLACEMENT_CATALOG.orbit_by_pattern[pattern_index]);
        let group_index = orbit_index * NORMAL_COLOR_COUNT + plane_index;
        let pattern_offset =
            pattern_index - usize::from(PLACEMENT_CATALOG.orbits[orbit_index].first_pattern);
        debug_assert!(pattern_offset < 2);
        debug_assert_eq!(
            result.candidate_group_resolutions[group_index] & (0xff << (pattern_offset * 8)),
            0
        );
        result.candidate_group_resolutions[group_index] |=
            u16::from(encoded_resolution_group) << (pattern_offset * 8);
        result.candidate_groups[group_index / 64] |= 1_u64 << (group_index % 64);
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "bmi1,bmi2,popcnt")]
unsafe fn build_trigger_frontier_accelerated<const PROFILE: bool>(
    result: &mut TriggerFrontier,
    components: &ComponentSet,
    max_added_puyos: u8,
    valid_patterns: u128,
    profile_stage: Option<&AtomicU8>,
    profile_counts: &mut EvaluationProfileCounts,
) {
    build_trigger_frontier_impl::<PROFILE>(
        result,
        components,
        max_added_puyos,
        valid_patterns,
        profile_stage,
        profile_counts,
    );
}

fn build_trigger_frontier<const PROFILE: bool>(
    result: &mut TriggerFrontier,
    components: &ComponentSet,
    max_added_puyos: u8,
    valid_patterns: u128,
    profile_stage: Option<&AtomicU8>,
    profile_counts: &mut EvaluationProfileCounts,
) {
    #[cfg(target_arch = "x86_64")]
    if differential_gravity_supported() {
        // SAFETY: the runtime guard covers every enabled target feature.
        return unsafe {
            build_trigger_frontier_accelerated::<PROFILE>(
                result,
                components,
                max_added_puyos,
                valid_patterns,
                profile_stage,
                profile_counts,
            )
        };
    }
    build_trigger_frontier_impl::<PROFILE>(
        result,
        components,
        max_added_puyos,
        valid_patterns,
        profile_stage,
        profile_counts,
    );
}

#[cfg(test)]
fn has_smaller_trigger(plane: u128, pattern: &PlacementPattern, heights: &[u8; WIDTH]) -> bool {
    if pattern.mask.count_ones() <= 1 {
        return false;
    }
    for removed_column in 0..WIDTH {
        if pattern.counts[removed_column] == 0 {
            continue;
        }
        let mut reduced_counts = pattern.counts;
        reduced_counts[removed_column] -= 1;
        let mut reduced_mask = 0_u128;
        for (x, count) in reduced_counts.iter().copied().enumerate() {
            for offset in 0..count {
                reduced_mask |= cell_bit(x, usize::from(heights[x] + offset));
            }
        }
        if virtual_trigger_anchors(plane, reduced_mask).is_some() {
            return true;
        }
    }
    false
}

#[cfg(test)]
fn trigger_protection_exact(occupied: u128, anchors: u128) -> f64 {
    if anchors == 0 {
        return 0.0;
    }
    let mut possible = 0_u32;
    let mut protected = 0_u32;
    let mut remaining = anchors;
    while remaining != 0 {
        let index = remaining.trailing_zeros() as usize;
        remaining &= remaining - 1;
        let x = index / COLUMN_LANE_BITS;
        let y = index % COLUMN_LANE_BITS;
        for (dx, dy) in [(1_i8, 0_i8), (-1, 0), (0, 1), (0, -1)] {
            let target_x = x as i8 + dx;
            let target_y = y as i8 + dy;
            if !(0..WIDTH as i8).contains(&target_x)
                || !(0..VISIBLE_HEIGHT as i8).contains(&target_y)
            {
                continue;
            }
            possible += 1;
            protected += u32::from(occupied & cell_bit(target_x as usize, target_y as usize) != 0);
        }
    }
    if possible == 0 {
        0.0
    } else {
        f64::from(protected) / f64::from(possible)
    }
}

#[inline(always)]
fn trigger_protection_impl(occupied: u128, anchors: u128) -> f64 {
    if anchors == 0 {
        return 0.0;
    }
    let right = (anchors & !RIGHT_VISIBLE_COLUMN_MASK) << COLUMN_LANE_BITS;
    let left = (anchors & !LEFT_VISIBLE_COLUMN_MASK) >> COLUMN_LANE_BITS;
    let up = (anchors & !TOP_VISIBLE_ROW_MASK) << 1;
    let down = (anchors & !ROW_ZERO_MASK) >> 1;
    let possible = right.count_ones() + left.count_ones() + up.count_ones() + down.count_ones();
    let protected = (right & occupied).count_ones()
        + (left & occupied).count_ones()
        + (up & occupied).count_ones()
        + (down & occupied).count_ones();
    if possible == 0 {
        0.0
    } else {
        f64::from(protected) / f64::from(possible)
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "popcnt")]
unsafe fn trigger_protection_popcnt(occupied: u128, anchors: u128) -> f64 {
    trigger_protection_impl(occupied, anchors)
}

#[inline(always)]
fn trigger_protection(occupied: u128, anchors: u128) -> f64 {
    trigger_protection_impl(occupied, anchors)
}

fn connection_bonus(group_size: u32) -> u16 {
    match group_size {
        0..=4 => 0,
        5 => 2,
        6 => 3,
        7 => 4,
        8 => 5,
        9 => 6,
        10 => 7,
        _ => 10,
    }
}

fn parallel_extract_13(value: u16, mut mask: u16) -> u16 {
    let mut result = 0_u16;
    let mut destination = 1_u16;
    while mask != 0 {
        let source = mask.isolate_lowest_one();
        if value & source != 0 {
            result |= destination;
        }
        mask &= mask - 1;
        destination <<= 1;
    }
    result
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "bmi2")]
unsafe fn parallel_extract_13_bmi2(value: u16, mask: u16) -> u16 {
    // SAFETY: the caller verifies BMI2 once while extracting the base-board
    // component metadata, before the differential resolver can be selected.
    core::arch::x86_64::_pext_u32(u32::from(value), u32::from(mask)) as u16
}

fn apply_gravity_exact(planes: &[u128; PLANE_COUNT]) -> [u128; PLANE_COUNT] {
    let mut result = std::array::from_fn(|index| planes[index] & ROW_FOURTEEN_MASK);
    let occupied = planes
        .iter()
        .copied()
        .fold(0_u128, |value, plane| value | plane);
    for x in 0..WIDTH {
        let shift = x * COLUMN_LANE_BITS;
        let occupied_column = ((occupied >> shift) as u16) & 0x1fff;
        for (plane_index, plane) in planes.iter().copied().enumerate() {
            let source = ((plane >> shift) as u16) & 0x1fff;
            let packed = parallel_extract_13(source, occupied_column);
            result[plane_index] |= u128::from(packed) << shift;
        }
    }
    result
}

fn affected_columns(mask: u128) -> u8 {
    let mut result = 0_u8;
    for column in 0..WIDTH {
        if mask & (0xffff_u128 << (column * COLUMN_LANE_BITS)) != 0 {
            result |= 1_u8 << column;
        }
    }
    result
}

fn drop_affected_columns(planes: &mut [u128; PLANE_COUNT], columns: u8) {
    let occupied = planes
        .iter()
        .copied()
        .fold(0_u128, |value, plane| value | plane);
    for column in 0..WIDTH {
        if columns & (1_u8 << column) == 0 {
            continue;
        }
        let shift = column * COLUMN_LANE_BITS;
        let lower_lane = 0x1fff_u128 << shift;
        let occupied_column = ((occupied >> shift) as u16) & 0x1fff;
        if occupied_column == 0 || occupied_column & occupied_column.wrapping_add(1) == 0 {
            continue;
        }
        for plane in planes.iter_mut() {
            let source = ((*plane >> shift) as u16) & 0x1fff;
            if source == 0 {
                continue;
            }
            #[cfg(target_arch = "x86_64")]
            // SAFETY: this function is only reached through the differential
            // resolver after `differential_gravity_supported` returned true.
            let packed = unsafe { parallel_extract_13_bmi2(source, occupied_column) };
            #[cfg(not(target_arch = "x86_64"))]
            let packed = parallel_extract_13(source, occupied_column);
            *plane = (*plane & !lower_lane) | (u128::from(packed) << shift);
        }
    }
}

impl ResolutionProgress {
    fn record_vanish(&mut self, vanish: VanishInfo) -> u128 {
        self.chain_count += 1;
        let garbage = visible_neighbors(vanish.mask) & self.planes[PLANE_COUNT - 1] & VISIBLE_MASK;
        let chain_bonus = CHAIN_BONUS[usize::from(self.chain_count).min(CHAIN_BONUS.len() - 1)];
        let color_bonus = COLOR_BONUS[usize::from(vanish.color_count)];
        let bonus = (chain_bonus + vanish.connection_bonus + color_bonus).max(1);
        self.score += vanish.count * 10 * u32::from(bonus);
        let cleared = vanish.mask | garbage;
        for plane in &mut self.planes {
            *plane &= !cleared;
        }
        cleared
    }

    fn apply_exact(&mut self, vanish: VanishInfo) {
        self.record_vanish(vanish);
        self.planes = apply_gravity_exact(&self.planes);
    }

    fn apply_incremental(&mut self, vanish: VanishInfo, virtual_only: u128) {
        let cleared = self.record_vanish(vanish);
        let columns = affected_columns(cleared & !virtual_only);
        drop_affected_columns(&mut self.planes, columns);
    }
}

fn find_vanish_exact(planes: &[u128; PLANE_COUNT]) -> VanishInfo {
    let mut result = VanishInfo::default();
    for plane in planes.iter().copied().take(NORMAL_COLOR_COUNT) {
        let visible_plane = plane & VISIBLE_MASK;
        let mut remaining = visible_plane;
        let mut color_vanished = false;
        while remaining != 0 {
            let seed = 1_u128 << remaining.trailing_zeros();
            let group = flood(seed, visible_plane, true);
            remaining &= !group;
            let count = group.count_ones();
            if count < 4 {
                continue;
            }
            result.mask |= group;
            result.count += count;
            result.connection_bonus += connection_bonus(count);
            color_vanished = true;
        }
        result.color_count += u8::from(color_vanished);
    }
    result
}

fn find_vanish_prefiltered(planes: &[u128; PLANE_COUNT]) -> VanishInfo {
    let mut result = VanishInfo::default();
    for plane in planes.iter().copied().take(NORMAL_COLOR_COUNT) {
        let visible_plane = plane & VISIBLE_MASK;
        let mut remaining = poppable_mask(visible_plane);
        if remaining == 0 {
            continue;
        }
        let mut color_vanished = false;
        while remaining != 0 {
            let seed = 1_u128 << remaining.trailing_zeros();
            let group = flood(seed, visible_plane, true);
            remaining &= !group;
            let count = group.count_ones();
            debug_assert!(count >= 4);
            result.mask |= group;
            result.count += count;
            result.connection_bonus += connection_bonus(count);
            color_vanished = true;
        }
        result.color_count += u8::from(color_vanished);
    }
    result
}

fn find_placement_vanish(plane: u128, placements: u128) -> (VanishInfo, u128) {
    let visible_plane = plane & VISIBLE_MASK;
    let mut result = VanishInfo::default();
    let mut trigger_anchors = 0_u128;
    let mut first_trigger_cell = u32::MAX;
    let mut pending = placements & VISIBLE_MASK;
    while pending != 0 {
        let seed = 1_u128 << pending.trailing_zeros();
        let mut group = seed;
        for _ in 0..3 {
            let expanded = group | (visible_neighbors(group) & visible_plane);
            if expanded == group {
                break;
            }
            group = expanded;
        }
        if has_at_least_four_bits(group) {
            group = flood(group, visible_plane, true);
        }
        pending &= !group;
        let count = group.count_ones();
        if count < 4 {
            continue;
        }
        result.mask |= group;
        result.count += count;
        result.connection_bonus += connection_bonus(count);
        let trigger_cells = group & placements;
        let first_cell = trigger_cells.trailing_zeros();
        if first_cell < first_trigger_cell {
            first_trigger_cell = first_cell;
            trigger_anchors = group & !placements;
        }
    }
    result.color_count = u8::from(result.mask != 0);
    (result, trigger_anchors)
}

#[inline]
fn shift_west_board(mask: u128) -> u128 {
    mask >> COLUMN_LANE_BITS
}

#[inline]
fn shift_east_board(mask: u128) -> u128 {
    (mask << COLUMN_LANE_BITS) & BOARD_MASK
}

#[inline]
fn shift_down_board(mask: u128) -> u128 {
    mask >> 1
}

#[inline]
fn shift_up_board(mask: u128) -> u128 {
    (mask << 1) & BOARD_MASK
}

#[inline]
fn at_least_two_of_four(first: u128, second: u128, third: u128, fourth: u128) -> u128 {
    (first & second)
        | (first & third)
        | (first & fourth)
        | (second & third)
        | (second & fourth)
        | (third & fourth)
}

#[inline(always)]
fn component_analysis<const TERMINAL_ONLY: bool>(
    planes: &[u128; PLANE_COUNT],
) -> ComponentAnalysis {
    let mut west = 0_u128;
    let mut east = 0_u128;
    let mut down = 0_u128;
    let mut up = 0_u128;
    let mut normal = 0_u128;
    for plane in planes.iter().copied().take(NORMAL_COLOR_COUNT) {
        normal |= plane;
        let horizontal = shift_west_board(plane) & plane;
        let vertical = shift_down_board(plane) & plane;
        west |= horizontal;
        east |= shift_east_board(horizontal);
        down |= vertical;
        up |= shift_up_board(vertical);
    }
    let vertical_both = up & down;
    let horizontal_both = west & east;
    let vertical_either = up | down;
    let horizontal_either = west | east;
    let at_least_one = vertical_either | horizontal_either;
    let at_least_two = vertical_both | horizontal_both | (vertical_either & horizontal_either);
    let at_least_three = (vertical_both & horizontal_either) | (horizontal_both & vertical_either);
    let connected_degree_two = at_least_two
        & ((west & shift_west_board(at_least_two))
            | (east & shift_east_board(at_least_two))
            | (down & shift_down_board(at_least_two))
            | (up & shift_up_board(at_least_two)));
    let poppable_seeds = at_least_three | connected_degree_two;
    // Resolution only needs to know whether another chain exists; every
    // size-four-or-larger grid component has at least one such seed.
    let has_poppable = poppable_seeds != 0;
    let connection_edges = (west.count_ones() + down.count_ones()) as u8;
    let remaining = if TERMINAL_ONLY {
        if !has_poppable {
            // The differential resolver excludes hidden cells. With no
            // poppable component, every component has at most three cells.
            // A three-cell grid component has exactly one degree-two center;
            // a two-cell component contributes exactly one edge.
            let link_3 = at_least_two.count_ones() as u8;
            debug_assert!(connection_edges >= link_3 * 2);
            RemainingStructure {
                link_2: connection_edges - link_3 * 2,
                link_3,
                connection_edges,
                extension_space: 0,
            }
        } else {
            RemainingStructure::default()
        }
    } else {
        let degree_one = at_least_one & !at_least_two;
        let degree_two = at_least_two & !at_least_three;
        let leaf_west = east & shift_east_board(degree_one);
        let leaf_east = west & shift_west_board(degree_one);
        let leaf_down = up & shift_up_board(degree_one);
        let leaf_up = down & shift_down_board(degree_one);
        let adjacent_leaf = leaf_west | leaf_east | leaf_down | leaf_up;
        let link_2_cells = degree_one & adjacent_leaf;
        let centers_with_two_leaves =
            degree_two & at_least_two_of_four(leaf_west, leaf_east, leaf_down, leaf_up);
        RemainingStructure {
            link_2: (link_2_cells.count_ones() / 2) as u8,
            link_3: centers_with_two_leaves.count_ones() as u8,
            connection_edges,
            extension_space: 0,
        }
    };
    ComponentAnalysis {
        remaining,
        has_poppable,
        normal,
    }
}

#[inline(always)]
fn remaining_structure_with_extension(
    planes: &[u128; PLANE_COUNT],
    extension_space: u8,
) -> RemainingStructure {
    let mut result = component_analysis::<false>(planes).remaining;
    result.extension_space = extension_space;
    result
}

fn exact_extension_space(normal: u128, occupied: u128) -> u8 {
    let heights = column_heights(occupied);
    let reachable = reachable_columns(&heights);
    let landing = landing_mask(&heights, reachable);
    (board_neighbors(normal) & landing & !occupied).count_ones() as u8
}

fn remaining_structure(planes: &[u128; PLANE_COUNT]) -> RemainingStructure {
    let normal = planes
        .iter()
        .copied()
        .take(NORMAL_COLOR_COUNT)
        .fold(0_u128, |value, plane| value | plane);
    if normal == 0 {
        return RemainingStructure::default();
    }
    let occupied = planes
        .iter()
        .copied()
        .fold(0_u128, |value, plane| value | plane);
    remaining_structure_with_extension(planes, exact_extension_space(normal, occupied))
}

fn remaining_structure_compact(planes: &[u128; PLANE_COUNT]) -> RemainingStructure {
    let normal = planes
        .iter()
        .copied()
        .take(NORMAL_COLOR_COUNT)
        .fold(0_u128, |value, plane| value | plane);
    if normal == 0 {
        return RemainingStructure::default();
    }
    let occupied = normal | planes[PLANE_COUNT - 1];
    let extension_space = compact_extension_space(normal, occupied);
    remaining_structure_with_extension(planes, extension_space)
}

#[inline(always)]
fn compact_extension_space(normal: u128, occupied: u128) -> u8 {
    if occupied & (HIDDEN_MASK | TOP_VISIBLE_ROW_MASK) == 0 {
        let landing = ((occupied << 1) | ROW_ZERO_MASK) & !occupied & VISIBLE_MASK;
        (board_neighbors(normal) & landing).count_ones() as u8
    } else {
        exact_extension_space(normal, occupied)
    }
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "popcnt")]
unsafe fn precomputed_surface_with_popcnt(
    final_normal: u128,
    final_occupied: u128,
    anchors: u128,
    added_puyos: u8,
    mut remaining: RemainingStructure,
) -> (u32, RemainingStructure) {
    remaining.extension_space = if final_normal == 0 {
        0
    } else if final_occupied & (HIDDEN_MASK | TOP_VISIBLE_ROW_MASK) == 0 {
        let landing = ((final_occupied << 1) | ROW_ZERO_MASK) & !final_occupied & VISIBLE_MASK;
        let extensions = board_neighbors(final_normal) & landing;
        (core::arch::x86_64::_popcnt64(extensions as i64)
            + core::arch::x86_64::_popcnt64((extensions >> 64) as i64)) as u8
    } else {
        exact_extension_space(final_normal, final_occupied)
    };
    let first_count = (core::arch::x86_64::_popcnt64(anchors as i64)
        + core::arch::x86_64::_popcnt64((anchors >> 64) as i64)) as u32
        + u32::from(added_puyos);
    (first_count, remaining)
}

#[cfg(not(target_arch = "x86_64"))]
unsafe fn precomputed_surface_with_popcnt(
    final_normal: u128,
    final_occupied: u128,
    anchors: u128,
    added_puyos: u8,
    mut remaining: RemainingStructure,
) -> (u32, RemainingStructure) {
    remaining.extension_space = if final_normal == 0 {
        0
    } else {
        compact_extension_space(final_normal, final_occupied)
    };
    (anchors.count_ones() + u32::from(added_puyos), remaining)
}

#[inline(always)]
fn finish_fused_remaining(
    normal: u128,
    garbage: u128,
    mut remaining: RemainingStructure,
) -> RemainingStructure {
    if normal == 0 {
        return RemainingStructure::default();
    }
    remaining.extension_space = compact_extension_space(normal, normal | garbage);
    remaining
}

fn resolved_result(progress: ResolutionProgress, trigger_anchors: u128) -> ResolvedVirtual {
    ResolvedVirtual {
        planes: progress.planes,
        chain_count: progress.chain_count,
        score: progress.score,
        trigger_anchors,
        remaining_structure: progress.remaining_structure,
    }
}

fn resolve_virtual_exact(
    planes: [u128; PLANE_COUNT],
    trigger_plane: usize,
    placements: u128,
) -> ResolvedVirtual {
    let (_, trigger_anchors) = find_placement_vanish(planes[trigger_plane], placements);
    let mut progress = ResolutionProgress {
        planes,
        ..ResolutionProgress::default()
    };
    loop {
        let vanish = find_vanish_exact(&progress.planes);
        if vanish.mask == 0 {
            return resolved_result(progress, trigger_anchors);
        }
        progress.apply_exact(vanish);
    }
}

fn resolve_virtual_incremental(
    planes: [u128; PLANE_COUNT],
    trigger_plane: usize,
    placements: u128,
    supported: bool,
) -> ResolvedVirtual {
    if !supported {
        return resolve_virtual_exact(planes, trigger_plane, placements);
    }
    let (first_vanish, trigger_anchors) = find_placement_vanish(planes[trigger_plane], placements);
    if first_vanish.mask == 0 || trigger_anchors == 0 {
        return resolve_virtual_exact(planes, trigger_plane, placements);
    }
    // SAFETY: `supported` includes the BMI2 and POPCNT runtime checks.
    unsafe { resolve_virtual_incremental_seeded(planes, first_vanish, trigger_anchors, placements) }
}

#[cfg_attr(target_arch = "x86_64", target_feature(enable = "bmi2,popcnt"))]
unsafe fn resolve_virtual_incremental_seeded(
    planes: [u128; PLANE_COUNT],
    first_vanish: VanishInfo,
    trigger_anchors: u128,
    placements: u128,
) -> ResolvedVirtual {
    let mut progress = ResolutionProgress {
        planes,
        ..ResolutionProgress::default()
    };
    progress.apply_incremental(first_vanish, placements);
    loop {
        let analysis = component_analysis::<true>(&progress.planes);
        if !analysis.has_poppable {
            progress.remaining_structure = Some(finish_fused_remaining(
                analysis.normal,
                progress.planes[PLANE_COUNT - 1],
                analysis.remaining,
            ));
            return resolved_result(progress, trigger_anchors);
        }
        let vanish = find_vanish_prefiltered(&progress.planes);
        debug_assert_ne!(vanish.mask, 0);
        progress.apply_incremental(vanish, 0);
    }
}

#[cfg(test)]
fn remaining_structure_exact(planes: &[u128; PLANE_COUNT]) -> RemainingStructure {
    let occupied = planes
        .iter()
        .copied()
        .fold(0_u128, |value, plane| value | plane);
    let heights = column_heights(occupied);
    let reachable = reachable_columns(&heights);
    let landing = landing_mask(&heights, reachable);
    let normal = planes
        .iter()
        .copied()
        .take(NORMAL_COLOR_COUNT)
        .fold(0_u128, |value, plane| value | plane);
    let mut result = RemainingStructure {
        extension_space: (board_neighbors(normal) & landing & !occupied).count_ones() as u8,
        ..RemainingStructure::default()
    };
    for plane in planes.iter().copied().take(NORMAL_COLOR_COUNT) {
        let mut pending = plane;
        while pending != 0 {
            let seed = 1_u128 << pending.trailing_zeros();
            let component = flood(seed, plane, false);
            pending &= !component;
            let size = component.count_ones();
            result.link_2 += u8::from(size == 2);
            result.link_3 += u8::from(size == 3);
            result.connection_edges += ((component & (component >> COLUMN_LANE_BITS)).count_ones()
                + (component & (component >> 1)).count_ones())
                as u8;
        }
    }
    result
}

#[cfg(test)]
fn compare_columns(left: &[u8], right: &[u8]) -> Ordering {
    for (left_value, right_value) in left.iter().zip(right) {
        match left_value.cmp(right_value) {
            Ordering::Equal => {}
            ordering => return ordering,
        }
    }
    Ordering::Equal
}

struct QuiescenceSearch<'a, const EVIDENCE: bool, const PROFILE: bool, const INCREMENTAL: bool> {
    planes: &'a [u128; PLANE_COUNT],
    occupied: u128,
    heights: &'a [u8; WIDTH],
    reachable: u8,
    components: &'a ComponentSet,
    config: &'a EvaluationConfig,
    pattern_nodes: u32,
    resolution_nodes: u32,
    truncation_reason: TruncationReason,
    best: QuiescenceCandidate,
    best_rank_key: u128,
    tie_break_workspace: CandidateTieBreakWorkspace,
    has_best: bool,
    candidates: [QuiescenceCandidate; MAX_EVIDENCE_CANDIDATES],
    candidate_count: usize,
    resolution_cache: [ResolutionCacheEntry; RESOLUTION_CACHE_SIZE],
    trigger_protection_cache: [MaybeUninit<f64>; RESOLUTION_CACHE_SIZE],
    trigger_protection_cached: u32,
    profile_stage: Option<&'a AtomicU8>,
    profile_counts: EvaluationProfileCounts,
}

impl<'a, const EVIDENCE: bool, const PROFILE: bool, const INCREMENTAL: bool>
    QuiescenceSearch<'a, EVIDENCE, PROFILE, INCREMENTAL>
{
    fn new(
        planes: &'a [u128; PLANE_COUNT],
        occupied: u128,
        heights: &'a [u8; WIDTH],
        reachable: u8,
        components: &'a ComponentSet,
        config: &'a EvaluationConfig,
        profile_stage: Option<&'a AtomicU8>,
    ) -> Self {
        Self {
            planes,
            occupied,
            heights,
            reachable,
            components,
            config,
            pattern_nodes: 0,
            resolution_nodes: 0,
            truncation_reason: TruncationReason::None,
            best: QuiescenceCandidate::default(),
            best_rank_key: 0,
            tie_break_workspace: CandidateTieBreakWorkspace::default(),
            has_best: false,
            candidates: [QuiescenceCandidate::default(); MAX_EVIDENCE_CANDIDATES],
            candidate_count: 0,
            resolution_cache: [ResolutionCacheEntry::default(); RESOLUTION_CACHE_SIZE],
            trigger_protection_cache: [MaybeUninit::uninit(); RESOLUTION_CACHE_SIZE],
            trigger_protection_cached: 0,
            profile_stage,
            profile_counts: EvaluationProfileCounts::default(),
        }
    }

    #[inline]
    fn enter_profile_stage(&mut self, stage: u8) {
        mark_profile_stage::<PROFILE>(
            self.profile_stage,
            &mut self.profile_counts.stage_entries,
            stage,
        );
    }

    fn candidate_is_better(
        &mut self,
        candidate: &mut QuiescenceCandidate,
        rank_key: u128,
        rank_key_ordering: Option<Ordering>,
    ) -> bool {
        let mut digest = None;
        let better = match rank_key_ordering {
            None => {
                self.tie_break_workspace.invalidate_best();
                true
            }
            Some(ordering) => {
                if PROFILE {
                    self.profile_counts.rank_comparison_calls += 1;
                }
                match ordering {
                    Ordering::Greater => {
                        self.tie_break_workspace.invalidate_best();
                        true
                    }
                    Ordering::Less => false,
                    Ordering::Equal => match compare_candidate_rank_suffix(candidate, &self.best) {
                        Ordering::Greater => {
                            self.tie_break_workspace.invalidate_best();
                            true
                        }
                        Ordering::Less => false,
                        Ordering::Equal => {
                            if PROFILE {
                                self.profile_counts.rank_tie_calls += 1;
                            }
                            let comparison =
                                self.tie_break_workspace.compare(candidate, &self.best);
                            if PROFILE {
                                self.profile_counts.sha256_calls +=
                                    u32::from(comparison.sha256_calls);
                            }
                            digest = Some(comparison.digest);
                            comparison.ordering == Ordering::Greater
                        }
                    },
                }
            }
        };
        if EVIDENCE && digest.is_none() {
            digest = Some(self.tie_break_workspace.digest_candidate(candidate, better));
            if PROFILE {
                self.profile_counts.sha256_calls += 1;
            }
        }
        if let Some(digest) = digest
            && EVIDENCE
        {
            candidate.fixed_tie_break = u64::from_be_bytes(
                digest[..8]
                    .try_into()
                    .expect("SHA-256 prefix has eight bytes"),
            );
        }
        if better {
            self.best_rank_key = rank_key;
        }
        better
    }

    fn truncated(&self) -> bool {
        self.truncation_reason != TruncationReason::None
    }

    #[inline(always)]
    fn cached_resolution(
        &self,
        resolution_group: u8,
        garbage_mask: u128,
    ) -> Option<CachedResolution> {
        if resolution_group == u8::MAX {
            return None;
        }
        let entry = self.resolution_cache[usize::from(resolution_group)];
        (entry.valid && entry.garbage_mask == garbage_mask).then_some(entry.resolved)
    }

    #[inline(always)]
    fn store_resolution(
        &mut self,
        resolution_group: u8,
        garbage_mask: u128,
        resolved: CachedResolution,
    ) {
        if resolution_group == u8::MAX {
            return;
        }
        self.resolution_cache[usize::from(resolution_group)] = ResolutionCacheEntry {
            garbage_mask,
            resolved,
            valid: true,
        };
    }

    #[inline(always)]
    fn trigger_protection_for_group(&mut self, resolution_group: u8, anchors: u128) -> f64 {
        let calculate = || {
            #[cfg(target_arch = "x86_64")]
            if self.components.incremental_resolution_supported {
                // SAFETY: incremental support includes the POPCNT runtime check.
                return unsafe { trigger_protection_popcnt(self.occupied, anchors) };
            }
            trigger_protection(self.occupied, anchors)
        };
        if resolution_group == u8::MAX {
            return calculate();
        }
        let group = usize::from(resolution_group);
        let group_bit = 1_u32 << group;
        if self.trigger_protection_cached & group_bit == 0 {
            self.trigger_protection_cache[group].write(calculate());
            self.trigger_protection_cached |= group_bit;
        }
        // SAFETY: the group bit is published only after its cache entry has
        // been initialized.
        unsafe { *self.trigger_protection_cache[group].assume_init_ref() }
    }

    #[inline(always)]
    fn rank_resolved_candidate(
        &mut self,
        plane_index: usize,
        added_puyos: u8,
        pattern: PlacementPattern,
        resolved: CachedResolution,
        resolution_group: u8,
    ) {
        self.enter_profile_stage(PROFILE_STAGE_RANKING);
        let anchors = resolved.trigger_anchors;
        debug_assert!(anchors != 0);
        let remaining = resolved.remaining_structure;
        let mut candidate = QuiescenceCandidate {
            chain_count: resolved.chain_count,
            chain_score: resolved.score,
            required_key_count: added_puyos,
            trigger_color: plane_index as u8,
            placements_mask: pattern.mask,
            anchor_mask: anchors,
            trigger_column: pattern.minimum_column,
            trigger_height: 0,
            trigger_protection: 0.0,
            remaining_link_2: remaining.link_2,
            remaining_link_3: remaining.link_3,
            remaining_connection_edges: remaining.connection_edges,
            extension_space: remaining.extension_space,
            fixed_tie_break: 0,
        };
        let rank_key = candidate_rank_key(&candidate);
        let rank_key_ordering = self.has_best.then(|| rank_key.cmp(&self.best_rank_key));
        if EVIDENCE || rank_key_ordering != Some(Ordering::Less) {
            candidate.trigger_protection =
                self.trigger_protection_for_group(resolution_group, anchors);
            candidate.trigger_height = pattern_minimum_height(pattern.columns, self.heights);
        }
        if self.candidate_is_better(&mut candidate, rank_key, rank_key_ordering) {
            self.best = candidate;
            self.has_best = true;
        }
        if EVIDENCE {
            self.push_evidence(candidate);
        }
        self.enter_profile_stage(PROFILE_STAGE_PLACEMENT_DISPATCH);
    }

    #[inline(always)]
    fn finish_cached_candidate(
        &mut self,
        plane_index: usize,
        added_puyos: u8,
        pattern: PlacementPattern,
        resolved: CachedResolution,
        resolution_group: u8,
    ) {
        self.resolution_nodes += 1;
        if PROFILE {
            self.profile_counts.resolution_nodes += 1;
        }
        self.rank_resolved_candidate(
            plane_index,
            added_puyos,
            pattern,
            resolved,
            resolution_group,
        );
    }

    #[inline(always)]
    fn finish_precomputed_frontier_candidate(
        &mut self,
        plane_index: usize,
        specification: &PlacementPatternSpec,
        landing: u128,
        resolved: CachedResolution,
        rank_key: u128,
        resolution_group: u8,
    ) {
        self.resolution_nodes += 1;
        if PROFILE {
            self.profile_counts.resolution_nodes += 1;
        }
        self.enter_profile_stage(PROFILE_STAGE_RANKING);
        let rank_key_ordering = self.has_best.then(|| rank_key.cmp(&self.best_rank_key));
        if rank_key_ordering == Some(Ordering::Less) {
            if PROFILE {
                self.profile_counts.rank_comparison_calls += 1;
            }
            self.enter_profile_stage(PROFILE_STAGE_PLACEMENT_DISPATCH);
            return;
        }
        let protection =
            self.trigger_protection_for_group(resolution_group, resolved.trigger_anchors);
        let trigger_height =
            pattern_minimum_height(specification.count_at_least_columns[0], self.heights);
        if rank_key_ordering == Some(Ordering::Equal)
            && protection
                .partial_cmp(&self.best.trigger_protection)
                .unwrap_or(Ordering::Equal)
                .then_with(|| self.best.trigger_height.cmp(&trigger_height))
                == Ordering::Less
        {
            if PROFILE {
                self.profile_counts.rank_comparison_calls += 1;
            }
            self.enter_profile_stage(PROFILE_STAGE_PLACEMENT_DISPATCH);
            return;
        }
        let pattern = pattern_from_spec(specification, landing);
        let remaining = resolved.remaining_structure;
        let mut candidate = QuiescenceCandidate {
            chain_count: resolved.chain_count,
            chain_score: resolved.score,
            required_key_count: specification.added_puyos,
            trigger_color: plane_index as u8,
            placements_mask: pattern.mask,
            anchor_mask: resolved.trigger_anchors,
            trigger_column: pattern.minimum_column,
            trigger_height,
            trigger_protection: protection,
            remaining_link_2: remaining.link_2,
            remaining_link_3: remaining.link_3,
            remaining_connection_edges: remaining.connection_edges,
            extension_space: remaining.extension_space,
            fixed_tie_break: 0,
        };
        if self.candidate_is_better(&mut candidate, rank_key, rank_key_ordering) {
            self.best = candidate;
            self.has_best = true;
        }
        self.enter_profile_stage(PROFILE_STAGE_PLACEMENT_DISPATCH);
    }

    #[inline(always)]
    fn resolve_candidate_marked(
        &mut self,
        plane_index: usize,
        added_puyos: u8,
        pattern: PlacementPattern,
        resolution_spec: FrontierResolutionSpec,
    ) {
        let trigger_anchors = resolution_spec.anchors;
        let resolution_group = resolution_spec.group;
        let resolved = if INCREMENTAL
            && self.components.incremental_resolution_supported
            && resolution_group != u8::MAX
            && trigger_anchors != 0
        {
            let garbage_plane = self.planes[PLANE_COUNT - 1];
            let empty_garbage_cached = if garbage_plane == 0 {
                self.cached_resolution(resolution_group, 0)
            } else {
                None
            };
            if let Some(cached) = empty_garbage_cached {
                if PROFILE {
                    self.profile_counts.resolution_cache_hits += 1;
                }
                self.finish_cached_candidate(
                    plane_index,
                    added_puyos,
                    pattern,
                    cached,
                    resolution_group,
                );
                return;
            } else {
                let first_mask = trigger_anchors | (pattern.mask & VISIBLE_MASK);
                let first_count = first_mask.count_ones();
                if first_count >= 4 {
                    let first_vanish = VanishInfo {
                        mask: first_mask,
                        count: first_count,
                        connection_bonus: connection_bonus(first_count),
                        color_count: 1,
                    };
                    debug_assert_eq!(
                        (first_vanish, trigger_anchors),
                        find_placement_vanish(
                            self.planes[plane_index] | pattern.mask,
                            pattern.mask,
                        )
                    );
                    let garbage_mask = if garbage_plane == 0 {
                        0
                    } else {
                        visible_neighbors(first_vanish.mask) & garbage_plane & VISIBLE_MASK
                    };
                    let nonempty_garbage_cached = if garbage_plane == 0 {
                        None
                    } else {
                        self.cached_resolution(resolution_group, garbage_mask)
                    };
                    if let Some(cached) = nonempty_garbage_cached {
                        if PROFILE {
                            self.profile_counts.resolution_cache_hits += 1;
                        }
                        self.finish_cached_candidate(
                            plane_index,
                            added_puyos,
                            pattern,
                            cached,
                            resolution_group,
                        );
                        return;
                    } else {
                        // SAFETY: the fast-path guard includes the BMI2 and
                        // POPCNT runtime checks.
                        let resolved = unsafe {
                            resolve_virtual_incremental_seeded(
                                *self.planes,
                                first_vanish,
                                trigger_anchors,
                                pattern.mask,
                            )
                        };
                        self.store_resolution(
                            resolution_group,
                            garbage_mask,
                            CachedResolution::from_resolved(resolved),
                        );
                        resolved
                    }
                } else {
                    let mut virtual_planes = *self.planes;
                    virtual_planes[plane_index] |= pattern.mask;
                    resolve_virtual_exact(virtual_planes, plane_index, pattern.mask)
                }
            }
        } else {
            let mut virtual_planes = *self.planes;
            virtual_planes[plane_index] |= pattern.mask;
            if INCREMENTAL {
                resolve_virtual_incremental(
                    virtual_planes,
                    plane_index,
                    pattern.mask,
                    self.components.incremental_resolution_supported,
                )
            } else {
                resolve_virtual_exact(virtual_planes, plane_index, pattern.mask)
            }
        };
        self.resolution_nodes += 1;
        if PROFILE {
            self.profile_counts.resolution_nodes += 1;
        }
        if resolved.chain_count == 0 {
            self.enter_profile_stage(PROFILE_STAGE_PLACEMENT_DISPATCH);
            return;
        }
        let remaining = if INCREMENTAL && self.components.incremental_resolution_supported {
            if let Some(remaining) = resolved.remaining_structure {
                remaining
            } else {
                self.enter_profile_stage(PROFILE_STAGE_REMAINING);
                remaining_structure_compact(&resolved.planes)
            }
        } else {
            self.enter_profile_stage(PROFILE_STAGE_REMAINING);
            remaining_structure(&resolved.planes)
        };
        self.rank_resolved_candidate(
            plane_index,
            added_puyos,
            pattern,
            CachedResolution {
                chain_count: resolved.chain_count,
                score: resolved.score,
                trigger_anchors: resolved.trigger_anchors,
                remaining_structure: remaining,
            },
            resolution_group,
        );
    }

    #[cfg(test)]
    #[inline(always)]
    fn resolve_candidate(
        &mut self,
        plane_index: usize,
        added_puyos: u8,
        pattern: PlacementPattern,
    ) {
        self.enter_profile_stage(PROFILE_STAGE_RESOLVE);
        self.resolve_candidate_marked(
            plane_index,
            added_puyos,
            pattern,
            FrontierResolutionSpec::default(),
        );
    }

    #[inline(always)]
    fn resolve_frontier_candidate(
        &mut self,
        frontier: &TriggerFrontier,
        plane_index: usize,
        added_puyos: u8,
        pattern_index: usize,
        landing: u128,
        resolution_group: u8,
    ) {
        // SAFETY: frontier candidate masks contain only catalog pattern bits.
        let specification = unsafe { PLACEMENT_CATALOG.patterns.get_unchecked(pattern_index) };
        if INCREMENTAL && resolution_group != u8::MAX {
            let group = usize::from(resolution_group);
            if frontier.precomputed_groups & (1_u32 << group) != 0 {
                // SAFETY: a true validity flag is published only after the
                // corresponding fixed resolution has been initialized.
                let precomputed = unsafe {
                    *frontier
                        .resolution_precomputed
                        .get_unchecked(group)
                        .assume_init_ref()
                };
                // SAFETY: the precomputed-group bit publishes the resolution
                // and its rank prefix together.
                let precomputed_rank_key = unsafe {
                    *frontier
                        .resolution_precomputed_rank_keys
                        .get_unchecked(group)
                        .assume_init_ref()
                };
                if PROFILE {
                    self.profile_counts.precomputed_candidate_hits += 1;
                }
                if EVIDENCE {
                    self.finish_cached_candidate(
                        plane_index,
                        added_puyos,
                        pattern_from_spec(specification, landing),
                        precomputed,
                        resolution_group,
                    );
                } else {
                    self.finish_precomputed_frontier_candidate(
                        plane_index,
                        specification,
                        landing,
                        precomputed,
                        precomputed_rank_key,
                        resolution_group,
                    );
                }
                return;
            }
        }
        let pattern = pattern_from_spec(specification, landing);
        let resolution_spec = if resolution_group == u8::MAX {
            FrontierResolutionSpec::default()
        } else {
            let group = usize::from(resolution_group);
            FrontierResolutionSpec {
                group: resolution_group,
                // SAFETY: resolution groups never exceed the fixed cache size.
                anchors: unsafe { *frontier.resolution_anchors.get_unchecked(group) },
            }
        };
        self.enter_profile_stage(PROFILE_STAGE_RESOLVE);
        self.resolve_candidate_marked(plane_index, added_puyos, pattern, resolution_spec);
    }

    #[cfg(test)]
    fn resolve_pending_group(
        &mut self,
        plane_index: usize,
        added_puyos: u8,
        pending: &[PlacementPattern],
    ) {
        if self.resolution_nodes + pending.len() as u32 > self.config.max_resolution_nodes {
            self.truncation_reason = TruncationReason::ResolutionNodes;
            return;
        }
        for pattern in pending.iter().copied() {
            self.resolve_candidate(plane_index, added_puyos, pattern);
        }
    }

    fn evaluate_frontier_group(
        &mut self,
        frontier: &TriggerFrontier,
        orbit_index: usize,
        orbit: PlacementOrbitSpec,
        plane_index: usize,
        landing: u128,
    ) {
        let mut probes = [0_u8; 2];
        let mut probe_count = 0_usize;
        let mut pending_mask = orbit.pattern_mask() & frontier.candidate_patterns[plane_index];
        while pending_mask != 0 {
            let pattern_index = pending_mask.trailing_zeros() as usize;
            pending_mask &= pending_mask - 1;
            probes[probe_count] = pattern_index as u8;
            probe_count += 1;
        }
        if PROFILE {
            self.profile_counts.executed_pattern_probes += probe_count as u32;
        }
        if self.resolution_nodes + probe_count as u32 > self.config.max_resolution_nodes {
            self.truncation_reason = TruncationReason::ResolutionNodes;
            return;
        }
        for pattern_index in probes[..probe_count].iter().copied() {
            let pattern_index = usize::from(pattern_index);
            let pattern_offset = pattern_index - usize::from(orbit.first_pattern);
            let encoded_resolution_group = (frontier.candidate_group_resolutions
                [orbit_index * NORMAL_COLOR_COUNT + plane_index]
                >> (pattern_offset * 8)) as u8;
            debug_assert_ne!(encoded_resolution_group, 0);
            let resolution_group = if encoded_resolution_group == u8::MAX {
                u8::MAX
            } else {
                encoded_resolution_group - 1
            };
            self.resolve_frontier_candidate(
                frontier,
                plane_index,
                orbit.added_puyos,
                pattern_index,
                landing,
                resolution_group,
            );
        }
    }

    #[inline(always)]
    fn evaluate_frontier_group_prechecked(
        &mut self,
        frontier: &TriggerFrontier,
        group_index: usize,
        landing: u128,
    ) {
        // The compact group specification encodes first pattern (bits 0..6),
        // pattern count (7..8), added puyos (9..10), and color plane (11..13).
        let group_spec = PLACEMENT_CATALOG.candidate_group_specs[group_index];
        let first_pattern = usize::from(group_spec & 0x7f);
        let pattern_count = usize::from((group_spec >> 7) & 0x03);
        let added_puyos = ((group_spec >> 9) & 0x03) as u8;
        let plane_index = usize::from(group_spec >> 11);
        let resolution_groups = frontier.candidate_group_resolutions[group_index];
        if PROFILE {
            self.profile_counts.executed_pattern_probes += u32::from(resolution_groups as u8 != 0)
                + u32::from((resolution_groups >> 8) as u8 != 0);
        }
        let first_resolution_group = resolution_groups as u8;
        if first_resolution_group != 0 {
            let first_resolution_group = if first_resolution_group == u8::MAX {
                u8::MAX
            } else {
                first_resolution_group - 1
            };
            self.resolve_frontier_candidate(
                frontier,
                plane_index,
                added_puyos,
                first_pattern,
                landing,
                first_resolution_group,
            );
        }
        if pattern_count == 2 {
            let second_resolution_group = (resolution_groups >> 8) as u8;
            if second_resolution_group == 0 {
                return;
            }
            let second_resolution_group = if second_resolution_group == u8::MAX {
                u8::MAX
            } else {
                second_resolution_group - 1
            };
            self.resolve_frontier_candidate(
                frontier,
                plane_index,
                added_puyos,
                first_pattern + 1,
                landing,
                second_resolution_group,
            );
        }
    }

    #[cfg(test)]
    fn evaluate_exhaustive_probe_group(
        &mut self,
        plane_index: usize,
        added_puyos: u8,
        patterns: &[PlacementPattern],
    ) {
        let plane = self.planes[plane_index];
        let mut pending = [PlacementPattern::default(); 2];
        let mut pending_count = 0_usize;
        for pattern in patterns {
            if virtual_trigger_anchors(plane, pattern.mask).is_none() {
                continue;
            }
            if has_smaller_trigger(plane, pattern, self.heights) {
                continue;
            }
            pending[pending_count] = *pattern;
            pending_count += 1;
        }
        self.resolve_pending_group(plane_index, added_puyos, &pending[..pending_count]);
    }

    fn evaluate_frontier_catalog(&mut self, components: &ComponentSet, landing: u128) {
        self.enter_profile_stage(PROFILE_STAGE_PLACEMENT_ORBIT);
        let valid_patterns =
            valid_catalog_patterns(self.heights, self.reachable, self.config.max_added_puyos);
        let mut frontier = TriggerFrontier::new();
        build_trigger_frontier::<PROFILE>(
            &mut frontier,
            components,
            self.config.max_added_puyos,
            valid_patterns,
            self.profile_stage,
            &mut self.profile_counts,
        );
        self.enter_profile_stage(PROFILE_STAGE_PLACEMENT_DISPATCH);
        let total_pattern_nodes = valid_patterns.count_ones() * NORMAL_COLOR_COUNT as u32;
        let total_resolution_nodes = frontier.candidate_count;
        if self.pattern_nodes + total_pattern_nodes <= self.config.max_pattern_nodes
            && self.resolution_nodes + total_resolution_nodes <= self.config.max_resolution_nodes
        {
            self.pattern_nodes += total_pattern_nodes;
            if PROFILE {
                self.profile_counts.pattern_nodes += total_pattern_nodes;
            }
            for (word_index, mut groups) in frontier.candidate_groups.into_iter().enumerate() {
                while groups != 0 {
                    let bit_index = groups.trailing_zeros() as usize;
                    groups &= groups - 1;
                    let group_index = word_index * 64 + bit_index;
                    self.evaluate_frontier_group_prechecked(&frontier, group_index, landing);
                }
            }
            return;
        }
        for (orbit_index, orbit) in PLACEMENT_CATALOG.orbits.into_iter().enumerate() {
            let valid_orbit = valid_patterns & orbit.pattern_mask();
            if valid_orbit == 0 {
                continue;
            }
            let valid_count = valid_orbit.count_ones();
            for (plane_index, candidate_patterns) in
                frontier.candidate_patterns.iter().copied().enumerate()
            {
                if self.pattern_nodes + valid_count > self.config.max_pattern_nodes {
                    self.truncation_reason = TruncationReason::PatternNodes;
                    return;
                }
                self.pattern_nodes += valid_count;
                if PROFILE {
                    self.profile_counts.pattern_nodes += valid_count;
                }
                if valid_orbit & candidate_patterns == 0 {
                    continue;
                }
                self.evaluate_frontier_group(&frontier, orbit_index, orbit, plane_index, landing);
                if self.truncated() {
                    return;
                }
            }
        }
    }

    #[cfg(test)]
    fn consider_columns_exhaustive(&mut self, columns: &[u8], added_puyos: u8) {
        let mut mirrored_storage = [0_u8; 3];
        for index in 0..columns.len() {
            mirrored_storage[index] = (WIDTH - 1) as u8 - columns[columns.len() - 1 - index];
        }
        let mirrored = &mirrored_storage[..columns.len()];
        match compare_columns(columns, mirrored) {
            Ordering::Greater => {}
            Ordering::Equal => {
                let mut valid = [PlacementPattern::default(); 2];
                let mut valid_count = 0_usize;
                if let Some(pattern) = valid_pattern(columns, self.heights, self.reachable) {
                    valid[0] = pattern;
                    valid_count = 1;
                }
                self.evaluate_orbit_exhaustive(&valid[..valid_count], added_puyos);
            }
            Ordering::Less => {
                let mut valid = [PlacementPattern::default(); 2];
                let mut valid_count = 0_usize;
                if let Some(pattern) = valid_pattern(columns, self.heights, self.reachable) {
                    valid[valid_count] = pattern;
                    valid_count += 1;
                }
                if let Some(pattern) = valid_pattern(mirrored, self.heights, self.reachable) {
                    valid[valid_count] = pattern;
                    valid_count += 1;
                }
                self.evaluate_orbit_exhaustive(&valid[..valid_count], added_puyos);
            }
        }
    }

    #[cfg(test)]
    fn evaluate_orbit_exhaustive(&mut self, valid: &[PlacementPattern], added_puyos: u8) {
        if valid.is_empty() {
            return;
        }
        for plane_index in 0..NORMAL_COLOR_COUNT {
            if self.pattern_nodes + valid.len() as u32 > self.config.max_pattern_nodes {
                self.truncation_reason = TruncationReason::PatternNodes;
                return;
            }
            self.pattern_nodes += valid.len() as u32;
            if PROFILE {
                self.profile_counts.pattern_nodes += valid.len() as u32;
            }
            let plane = self.planes[plane_index];
            if (plane & VISIBLE_MASK).count_ones() + u32::from(added_puyos) < 4 {
                continue;
            }
            let mut probes = [PlacementPattern::default(); 2];
            let mut probe_count = 0_usize;
            for pattern in valid {
                if visible_neighbors(pattern.mask) & plane == 0 {
                    continue;
                }
                probes[probe_count] = *pattern;
                probe_count += 1;
            }
            if probe_count != 0 {
                self.evaluate_exhaustive_probe_group(
                    plane_index,
                    added_puyos,
                    &probes[..probe_count],
                );
                if self.truncated() {
                    return;
                }
            }
        }
    }

    fn push_evidence(&mut self, candidate: QuiescenceCandidate) {
        if self.candidates[..self.candidate_count]
            .iter()
            .any(|current| same_candidate_signature(current, &candidate))
        {
            return;
        }
        debug_assert!(self.candidate_count < self.candidates.len());
        self.candidates[self.candidate_count] = candidate;
        self.candidate_count += 1;
    }
}

fn bounded_quiescence<'a, const EVIDENCE: bool, const PROFILE: bool, const INCREMENTAL: bool>(
    planes: &'a [u128; PLANE_COUNT],
    occupied: u128,
    heights: &'a [u8; WIDTH],
    reachable: u8,
    components: &'a ComponentSet,
    config: &'a EvaluationConfig,
    profile_stage: Option<&'a AtomicU8>,
) -> QuiescenceSearch<'a, EVIDENCE, PROFILE, INCREMENTAL> {
    let mut search = QuiescenceSearch::<EVIDENCE, PROFILE, INCREMENTAL>::new(
        planes,
        occupied,
        heights,
        reachable,
        components,
        config,
        profile_stage,
    );
    search.enter_profile_stage(PROFILE_STAGE_PLACEMENT_ORBIT);
    search.evaluate_frontier_catalog(components, components.landing);
    if PROFILE {
        search.profile_counts.pattern_nodes = search.pattern_nodes;
        search.profile_counts.resolution_nodes = search.resolution_nodes;
    }
    search
}

#[cfg(test)]
fn bounded_quiescence_exhaustive<'a>(
    planes: &'a [u128; PLANE_COUNT],
    occupied: u128,
    heights: &'a [u8; WIDTH],
    reachable: u8,
    components: &'a ComponentSet,
    config: &'a EvaluationConfig,
) -> QuiescenceSearch<'a, true, false, false> {
    let mut search = QuiescenceSearch::<true, false, false>::new(
        planes, occupied, heights, reachable, components, config, None,
    );
    for first in 0..WIDTH as u8 {
        search.consider_columns_exhaustive(&[first], 1);
        if search.truncated() {
            return search;
        }
    }
    if config.max_added_puyos >= 2 {
        for first in 0..WIDTH as u8 {
            for second in first..WIDTH as u8 {
                search.consider_columns_exhaustive(&[first, second], 2);
                if search.truncated() {
                    return search;
                }
            }
        }
    }
    if config.max_added_puyos >= 3 {
        for first in 0..WIDTH as u8 {
            for second in first..WIDTH as u8 {
                for third in second..WIDTH as u8 {
                    search.consider_columns_exhaustive(&[first, second, third], 3);
                    if search.truncated() {
                        return search;
                    }
                }
            }
        }
    }
    search
}

fn canonical_heights(heights: &[u8; WIDTH]) -> [u8; WIDTH] {
    let mut mirrored = *heights;
    mirrored.reverse();
    if heights.as_slice() <= mirrored.as_slice() {
        *heights
    } else {
        mirrored
    }
}

fn build_features<const EVIDENCE: bool, const PROFILE: bool, const INCREMENTAL: bool>(
    state: &CompactState,
    planes: &[u128; PLANE_COUNT],
    occupied: u128,
    heights: &[u8; WIDTH],
    components: &ComponentSet,
    quiescence: &QuiescenceSearch<'_, EVIDENCE, PROFILE, INCREMENTAL>,
) -> ChainStructureFeatures {
    let normal_mask = components.base_normal;
    let normal_count = normal_mask.count_ones() as u8;
    let nuisance_count = planes[PLANE_COUNT - 1].count_ones() as u8;
    let hidden_row_count = (occupied & HIDDEN_MASK).count_ones() as u8;
    let isolated_count = components.isolated_count;
    let link_2 = components.base_remaining.link_2;
    let link_3 = components.base_remaining.link_3;
    let connectivity_edges = components.base_remaining.connection_edges;
    let reachable_ignition_count = components.reachable_ignition_count;
    let growth_columns = components.growth_columns;
    let supported = normal_mask & ((occupied << 1) | ROW_ZERO_MASK) & ROW_THREE_MASK;
    let foundation_cell_count = supported.count_ones() as u8;
    let mut adjacent_roughness = 0_u8;
    for x in 0..WIDTH - 1 {
        adjacent_roughness += heights[x].abs_diff(heights[x + 1]);
    }
    let minimum_height = heights.iter().copied().min().unwrap_or(0);
    let maximum_height = heights.iter().copied().max().unwrap_or(0);
    let height_spread = maximum_height - minimum_height;
    let mut well_depth = 0_u8;
    let mut bump_height = 0_u8;
    for (x, height) in heights.iter().copied().enumerate() {
        let left = if x > 0 { heights[x - 1] } else { heights[1] };
        let right = if x < WIDTH - 1 {
            heights[x + 1]
        } else {
            heights[WIDTH - 2]
        };
        if height < left && height < right {
            well_depth += left.min(right) - height;
        }
        if x > 0 && x < WIDTH - 1 && height > left && height > right {
            bump_height += height - left.max(right);
        }
    }
    let mut fold_space = 0_u16;
    if normal_count > 0 {
        for x in 0..WIDTH - 1 {
            let left = VISIBLE_HEIGHT.saturating_sub(usize::from(heights[x]));
            let right = VISIBLE_HEIGHT.saturating_sub(usize::from(heights[x + 1]));
            fold_space += left.min(right) as u16;
        }
    }
    let peak = f64::from(maximum_height) / VISIBLE_HEIGHT as f64;
    let center = f64::from(heights[2].max(heights[3])) / VISIBLE_HEIGHT as f64;
    let nuisance_ratio = (f64::from(nuisance_count) / 30.0).min(1.0);
    let danger_ratio = (center * 0.55 + peak * 0.35 + nuisance_ratio * 0.10).min(1.0);
    let death = state.game_over() || heights[2].max(heights[3]) as usize >= VISIBLE_HEIGHT;
    let search_complete = quiescence.truncation_reason == TruncationReason::None;
    let unreachable_trigger = normal_count > 0 && search_complete && !quiescence.has_best;
    let structural_dead_end =
        unreachable_trigger && components.connection_candidate_count == 0 && growth_columns == 0;
    let mut result = ChainStructureFeatures {
        canonical_column_heights: canonical_heights(heights),
        normal_puyo_count: normal_count,
        component_count: components.len as u8,
        isolated_count,
        link_2,
        link_3,
        connectivity_edges,
        connection_candidate_count: components.connection_candidate_count,
        reachable_ignition_count,
        growth_site_count: growth_columns.count_ones() as u8,
        foundation_cell_count,
        fold_space,
        adjacent_roughness,
        height_spread,
        well_depth,
        bump_height,
        danger_ratio,
        nuisance_count,
        hidden_row_count,
        death,
        unreachable_trigger,
        structural_dead_end,
        required_key_count: -1,
        trigger_column: -1,
        trigger_height: -1,
        ..ChainStructureFeatures::default()
    };
    if quiescence.has_best {
        let best = quiescence.best;
        result.trigger_reachable = true;
        result.trigger_protection = best.trigger_protection;
        result.potential_chain_count = best.chain_count;
        result.potential_chain_score = best.chain_score;
        result.required_key_count = best.required_key_count as i8;
        result.trigger_column = canonical_trigger_column(best.placements_mask) as i8;
        result.trigger_height = best.trigger_height as i8;
        result.remaining_link_2 = best.remaining_link_2;
        result.remaining_link_3 = best.remaining_link_3;
        result.remaining_connection_edges = best.remaining_connection_edges;
    }
    result
}

fn canonical_trigger_column(placements: u128) -> u8 {
    let canonical = canonical_mask(placements);
    (canonical.trailing_zeros() as usize / COLUMN_LANE_BITS) as u8
}

fn action_features(
    features: &ChainStructureFeatures,
    parent: Option<&EvaluationHot>,
    action: Option<TransitionHotResult>,
    target_chain_count: u8,
) -> ActionStructureFeatures {
    let (Some(parent), Some(action)) = (parent, action) else {
        return ActionStructureFeatures {
            death: features.death,
            ..ActionStructureFeatures::default()
        };
    };
    let before = &parent.features;
    let premature_fire = action.chain_count > 0 && action.chain_count < target_chain_count;
    let target_achieved = action.chain_count >= target_chain_count;
    let link_loss = before
        .connectivity_edges
        .saturating_sub(features.connectivity_edges);
    let bridge_loss = before
        .connection_candidate_count
        .saturating_sub(features.connection_candidate_count);
    let tear_count = if target_achieved {
        0
    } else {
        link_loss + bridge_loss
    };
    let expected_normal = u16::from(before.normal_puyo_count) + 2;
    let resource_loss = expected_normal.saturating_sub(u16::from(features.normal_puyo_count));
    let hidden_growth = features
        .hidden_row_count
        .saturating_sub(before.hidden_row_count);
    let waste_count = u16::from(hidden_growth)
        + if premature_fire {
            resource_loss.max(action.vanished_count)
        } else {
            0
        };
    let mut trigger_damage = 0_u8;
    if before.trigger_reachable && !target_achieved {
        if features.unreachable_trigger {
            trigger_damage = before.potential_chain_count.max(1);
        } else if features.trigger_reachable {
            trigger_damage += before
                .potential_chain_count
                .saturating_sub(features.potential_chain_count);
            if before.required_key_count >= 0 && features.required_key_count >= 0 {
                trigger_damage += (features.required_key_count as u8)
                    .saturating_sub(before.required_key_count as u8);
            }
        }
    }
    ActionStructureFeatures {
        evaluated: true,
        tear_count,
        waste_count: waste_count as u8,
        trigger_damage,
        premature_fire,
        danger_delta: features.danger_ratio - before.danger_ratio,
        death: features.death || action.game_over(),
    }
}

fn score(
    features: &ChainStructureFeatures,
    action: &ActionStructureFeatures,
    config: &EvaluationConfig,
) -> ScoreBreakdown {
    let weights = &config.weights;
    let key_count = features.required_key_count.max(0) as f64;
    let trigger_height = features.trigger_height.max(0) as f64;
    let mut result = ScoreBreakdown::default();
    result.values[ScoreBreakdown::QUIESCENCE_CHAIN] = f64::from(features.potential_chain_count)
        * weights[WEIGHT_POTENTIAL_CHAIN_COUNT]
        + f64::from(features.potential_chain_score) * weights[WEIGHT_POTENTIAL_CHAIN_SCORE];
    result.values[ScoreBreakdown::KEY_COST] = key_count * weights[WEIGHT_REQUIRED_KEY_COUNT];
    result.values[ScoreBreakdown::TRIGGER_POSITION] = trigger_height
        * weights[WEIGHT_TRIGGER_HEIGHT]
        + features.trigger_protection * weights[WEIGHT_TRIGGER_PROTECTION];
    result.values[ScoreBreakdown::REMAINING_LINKS] = f64::from(features.remaining_link_2)
        * weights[WEIGHT_REMAINING_LINK_2]
        + f64::from(features.remaining_link_3) * weights[WEIGHT_REMAINING_LINK_3]
        + f64::from(features.remaining_connection_edges) * weights[WEIGHT_CONNECTIVITY_EDGE];
    result.values[ScoreBreakdown::COMPONENT_CONNECTIVITY] =
        f64::from(features.connectivity_edges) * weights[WEIGHT_CONNECTIVITY_EDGE];
    result.values[ScoreBreakdown::CONNECTION_POTENTIAL] =
        f64::from(features.connection_candidate_count) * weights[WEIGHT_CONNECTION_CANDIDATE]
            + f64::from(features.reachable_ignition_count) * weights[WEIGHT_REACHABLE_IGNITION];
    result.values[ScoreBreakdown::SHAPE] = f64::from(features.growth_site_count)
        * weights[WEIGHT_GROWTH_SITE]
        + f64::from(features.foundation_cell_count) * weights[WEIGHT_FOUNDATION_CELL]
        + f64::from(features.fold_space) * weights[WEIGHT_FOLD_SPACE]
        + f64::from(features.adjacent_roughness) * weights[WEIGHT_ADJACENT_ROUGHNESS]
        + f64::from(features.height_spread) * weights[WEIGHT_HEIGHT_SPREAD]
        + f64::from(features.well_depth) * weights[WEIGHT_WELL_DEPTH]
        + f64::from(features.bump_height) * weights[WEIGHT_BUMP_HEIGHT];
    result.values[ScoreBreakdown::DANGER] = features.danger_ratio * weights[WEIGHT_DANGER_RATIO];
    result.values[ScoreBreakdown::NUISANCE] = f64::from(features.nuisance_count)
        * weights[WEIGHT_NUISANCE_PUYO]
        + f64::from(features.hidden_row_count) * weights[WEIGHT_HIDDEN_ROW_PUYO];
    result.values[ScoreBreakdown::TEAR] = f64::from(action.tear_count) * weights[WEIGHT_TEAR];
    result.values[ScoreBreakdown::WASTE] = f64::from(action.waste_count) * weights[WEIGHT_WASTE];
    result.values[ScoreBreakdown::TRIGGER_DAMAGE] =
        f64::from(action.trigger_damage) * weights[WEIGHT_TRIGGER_DAMAGE];
    result.values[ScoreBreakdown::PREMATURE_FIRE] = if action.premature_fire {
        weights[WEIGHT_PREMATURE_FIRE]
    } else {
        0.0
    };
    let fatal = features.death
        || features.unreachable_trigger
        || features.structural_dead_end
        || action.death;
    result.values[ScoreBreakdown::FATAL] = if fatal { config.fatal_score } else { 0.0 };
    result.values[ScoreBreakdown::TOTAL] = if fatal {
        config.fatal_score
    } else {
        python_float_sum(&result.values[..ScoreBreakdown::FATAL])
    };
    result
}

#[inline]
fn python_float_sum(values: &[f64]) -> f64 {
    let Some((&first, rest)) = values.split_first() else {
        return 0.0;
    };
    let mut total = first;
    let mut compensation = 0.0;
    for &value in rest {
        let next = total + value;
        if total.abs() >= value.abs() {
            compensation += (total - next) + value;
        } else {
            compensation += (value - next) + total;
        }
        total = next;
    }
    if compensation != 0.0 && compensation.is_finite() {
        total += compensation;
    }
    total
}

fn evaluate_internal<const EVIDENCE: bool, const PROFILE: bool>(
    state: &CompactState,
    config: &EvaluationConfig,
    parent: Option<&EvaluationHot>,
    action: Option<TransitionHotResult>,
    target_chain_count: u8,
    profile_stage: Option<&AtomicU8>,
) -> (EvaluationEvidence, EvaluationProfileCounts) {
    debug_assert!(config.validate().is_ok());
    debug_assert!(target_chain_count > 0);
    if PROFILE && let Some(marker) = profile_stage {
        marker.store(PROFILE_STAGE_BASE_FEATURES, AtomicOrdering::Relaxed);
    }
    let planes = state.evaluator_planes();
    let occupied = state.internal_occupied();
    let heights = column_heights(occupied);
    let reachable = reachable_columns(&heights);
    let landing = landing_mask(&heights, reachable);
    let components = extract_components(
        &planes,
        occupied,
        landing,
        &heights,
        reachable,
        config.max_added_puyos,
    );
    let mut quiescence = bounded_quiescence::<EVIDENCE, PROFILE, true>(
        &planes,
        occupied,
        &heights,
        reachable,
        &components,
        config,
        profile_stage,
    );
    if PROFILE && let Some(marker) = profile_stage {
        marker.store(PROFILE_STAGE_BASE_FEATURES, AtomicOrdering::Relaxed);
        quiescence.profile_counts.stage_entries[usize::from(PROFILE_STAGE_BASE_FEATURES)] += 2;
    }
    let features = build_features(state, &planes, occupied, &heights, &components, &quiescence);
    let action_features = action_features(&features, parent, action, target_chain_count);
    let score_breakdown = score(&features, &action_features, config);
    let status = if quiescence.truncation_reason != TruncationReason::None {
        EvaluationStatus::BudgetExhausted
    } else if quiescence.has_best {
        EvaluationStatus::Available
    } else {
        EvaluationStatus::NotFound
    };
    let profile_counts = quiescence.profile_counts;
    let evidence = EvaluationEvidence {
        hot: EvaluationHot {
            status,
            truncation_reason: quiescence.truncation_reason,
            pattern_nodes: quiescence.pattern_nodes,
            resolution_nodes: quiescence.resolution_nodes,
            score: score_breakdown.values[ScoreBreakdown::TOTAL],
            features,
            action_features,
            score_breakdown,
            best: quiescence.best,
            has_best: quiescence.has_best,
        },
        candidates: quiescence.candidates,
        candidate_count: quiescence.candidate_count as u8,
    };
    (evidence, profile_counts)
}

#[inline]
pub(crate) fn evaluate_hot(
    state: &CompactState,
    config: &EvaluationConfig,
    parent: Option<&EvaluationHot>,
    action: Option<TransitionHotResult>,
    target_chain_count: u8,
) -> EvaluationHot {
    evaluate_internal::<false, false>(state, config, parent, action, target_chain_count, None)
        .0
        .hot
}

pub(crate) fn evaluate_evidence(
    state: &CompactState,
    config: &EvaluationConfig,
    parent: Option<&EvaluationHot>,
    action: Option<TransitionHotResult>,
    target_chain_count: u8,
) -> EvaluationEvidence {
    evaluate_internal::<true, false>(state, config, parent, action, target_chain_count, None).0
}

/// Evaluate the unchanged hot result while publishing PUYO-219 QA-only stage
/// markers and exact inner-loop call counts.
pub(crate) fn evaluate_profiled(
    state: &CompactState,
    config: &EvaluationConfig,
    parent: Option<&EvaluationHot>,
    action: Option<TransitionHotResult>,
    target_chain_count: u8,
    profile_stage: &AtomicU8,
) -> (EvaluationHot, EvaluationProfileCounts) {
    let (evaluation, counts) = evaluate_internal::<false, true>(
        state,
        config,
        parent,
        action,
        target_chain_count,
        Some(profile_stage),
    );
    (evaluation.hot, counts)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::allocation_probe::count_allocations;
    use crate::compact::{Pair, transition_hot};
    use std::hint::black_box;
    use std::time::Instant;

    fn default_config() -> EvaluationConfig {
        EvaluationConfig {
            max_added_puyos: 3,
            max_pattern_nodes: 512,
            max_resolution_nodes: 96,
            max_candidates: 12,
            weights: [
                45_000.0, 0.05, -9_000.0, -180.0, 600.0, 140.0, 440.0, 90.0, 550.0, 700.0, 75.0,
                25.0, 20.0, -35.0, -25.0, -55.0, -70.0, -20_000.0, -240.0, -1_500.0, -4_000.0,
                -2_000.0, -30_000.0, -10_000.0,
            ],
            fatal_score: -1_000_000_000_000.0,
            version_key: 1,
        }
    }

    fn state_with_cells(cells: &[(usize, usize, usize)]) -> CompactState {
        let mut planes = [0_u128; PLANE_COUNT];
        for &(plane, x, y) in cells {
            let wire_bit = 1_u128 << (y * WIDTH + x);
            planes[plane] |= wire_bit;
        }
        CompactState::from_parts(planes, false, false, 0, 0).expect("valid test state")
    }

    fn property_state(seed: u64) -> CompactState {
        let mut value = seed;
        let mut planes = [0_u128; PLANE_COUNT];
        for x in 0..WIDTH {
            value = value
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1);
            let height = ((value >> 32) as usize) % (HEIGHT + 1);
            for y in 0..height {
                value = value
                    .wrapping_mul(6_364_136_223_846_793_005)
                    .wrapping_add(1);
                let plane = ((value >> 32) as usize) % PLANE_COUNT;
                planes[plane] |= 1_u128 << (y * WIDTH + x);
            }
        }
        CompactState::from_parts(planes, false, false, 0, 0).expect("valid property state")
    }

    fn assert_frontier_matches_exhaustive(
        state: &CompactState,
        config: &EvaluationConfig,
        label: &str,
    ) {
        let planes = state.evaluator_planes();
        let occupied = state.internal_occupied();
        let heights = column_heights(occupied);
        let reachable = reachable_columns(&heights);
        let landing = landing_mask(&heights, reachable);
        let components = extract_components(
            &planes,
            occupied,
            landing,
            &heights,
            reachable,
            config.max_added_puyos,
        );
        assert_eq!(
            components.connection_candidate_count,
            connection_candidate_count_exact(components.as_slice(), landing),
            "connection candidates: {label}",
        );
        let frontier = bounded_quiescence::<true, false, true>(
            &planes,
            occupied,
            &heights,
            reachable,
            &components,
            config,
            None,
        );
        let exhaustive = bounded_quiescence_exhaustive(
            &planes,
            occupied,
            &heights,
            reachable,
            &components,
            config,
        );

        assert_eq!(frontier.pattern_nodes, exhaustive.pattern_nodes, "{label}");
        assert_eq!(
            frontier.resolution_nodes, exhaustive.resolution_nodes,
            "{label}"
        );
        assert_eq!(
            frontier.truncation_reason, exhaustive.truncation_reason,
            "{label}"
        );
        assert_eq!(frontier.has_best, exhaustive.has_best, "{label}");
        assert_eq!(frontier.best, exhaustive.best, "{label}");
        assert_eq!(
            frontier.candidate_count, exhaustive.candidate_count,
            "{label}"
        );
        assert_eq!(
            &frontier.candidates[..frontier.candidate_count],
            &exhaustive.candidates[..exhaustive.candidate_count],
            "{label}"
        );
    }

    #[test]
    fn placement_catalog_preserves_exhaustive_orbit_layout() {
        assert_eq!(PLACEMENT_CATALOG.by_added_puyos[1].count_ones(), 6);
        assert_eq!(PLACEMENT_CATALOG.by_added_puyos[2].count_ones(), 21);
        assert_eq!(PLACEMENT_CATALOG.by_added_puyos[3].count_ones(), 56);
        assert_eq!(
            PLACEMENT_CATALOG
                .orbits
                .iter()
                .map(|orbit| u32::from(orbit.pattern_count))
                .sum::<u32>(),
            PLACEMENT_PATTERN_COUNT as u32
        );
        assert_eq!(
            PLACEMENT_CATALOG
                .orbits
                .iter()
                .filter(|orbit| orbit.pattern_count == 1)
                .count(),
            3
        );
    }

    #[test]
    fn bit_parallel_compact_check_matches_exact_lane_scan() {
        for column in 0..WIDTH {
            for lower in 0_u128..1_u128 << VISIBLE_HEIGHT {
                let occupied = lower << (column * COLUMN_LANE_BITS);
                assert_eq!(
                    lower_board_is_compact(occupied),
                    lower_board_is_compact_exact(occupied),
                    "column={column} lower={lower:#x}",
                );
            }
        }

        let mut value = 0x2250_0000_u64;
        for index in 0..4_096 {
            value = value
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1);
            let mut occupied = 0_u128;
            for column in 0..WIDTH {
                value = value
                    .wrapping_mul(6_364_136_223_846_793_005)
                    .wrapping_add(1);
                occupied |= u128::from(value & 0x3fff) << (column * COLUMN_LANE_BITS);
            }
            assert_eq!(
                lower_board_is_compact(occupied),
                lower_board_is_compact_exact(occupied),
                "property mask {index}",
            );
        }
    }

    #[test]
    fn frontier_search_matches_exhaustive_property_corpus() {
        let mut states = Vec::with_capacity(132);
        states.push(state_with_cells(&[
            (0, 0, 13),
            (0, 1, 12),
            (5, 1, 13),
            (5, 4, 0),
        ]));
        let mut unreachable = Vec::new();
        for x in [2_usize, 3] {
            for y in 0..VISIBLE_HEIGHT {
                unreachable.push(((x + y) % NORMAL_COLOR_COUNT, x, y));
            }
        }
        unreachable.extend([(0, 0, 0), (0, 1, 0), (5, 5, 0)]);
        states.push(state_with_cells(&unreachable));
        states.extend((0..130).map(|index| property_state(0x2230_0000 + index)));

        let pattern_budgets = [1_u32, 2, 7, 24, 95, 96, 414, 415, 512];
        let resolution_budgets = [1_u32, 2, 7, 24, 96];
        for (index, state) in states.iter().enumerate() {
            let full = default_config();
            assert_frontier_matches_exhaustive(state, &full, &format!("full-{index}"));

            let mut bounded = default_config();
            bounded.max_pattern_nodes = pattern_budgets[index % pattern_budgets.len()];
            bounded.max_resolution_nodes = resolution_budgets[index % resolution_budgets.len()];
            assert_frontier_matches_exhaustive(state, &bounded, &format!("bounded-{index}"));
        }
    }

    #[test]
    fn incremental_resolution_matches_exact_property_corpus() {
        let multi_chain = state_with_cells(&[
            (0, 0, 0),
            (0, 0, 1),
            (0, 0, 2),
            (1, 0, 3),
            (1, 1, 0),
            (1, 2, 0),
            (1, 3, 0),
        ]);
        let multi_chain_evidence =
            evaluate_evidence(&multi_chain, &default_config(), None, None, 6);
        assert!(
            multi_chain_evidence.candidates[..usize::from(multi_chain_evidence.candidate_count)]
                .iter()
                .any(|candidate| candidate.chain_count >= 2),
            "multi-chain special case did not exercise continuation resolution"
        );

        let mut states = Vec::with_capacity(256);
        states.push(multi_chain);
        states.push(state_with_cells(&[
            (0, 0, 0),
            (0, 1, 0),
            (0, 2, 0),
            (PLANE_COUNT - 1, 1, 1),
        ]));
        states.push(state_with_cells(&[
            (0, 0, 13),
            (1, 1, 12),
            (PLANE_COUNT - 1, 1, 13),
            (2, 5, 0),
        ]));
        states.push(state_with_cells(&[
            (0, 0, 0),
            (0, 0, 1),
            (1, 1, 0),
            (2, 4, 0),
            (2, 5, 0),
            (PLANE_COUNT - 1, 5, 1),
        ]));
        states.extend((0..252).map(|index| property_state(0x2201_0000 + index)));

        let config = default_config();
        for (index, state) in states.iter().enumerate() {
            assert_frontier_matches_exhaustive(state, &config, &format!("resolution-{index}"));
        }
    }

    #[test]
    fn component_metadata_aggregation_matches_exact_property_corpus() {
        for index in 0..512 {
            let state = property_state(0x2220_0000 + index);
            let planes = state.evaluator_planes();
            let occupied = state.internal_occupied();
            let heights = column_heights(occupied);
            let reachable = reachable_columns(&heights);
            let landing = landing_mask(&heights, reachable);
            let components = extract_components(
                &planes,
                occupied,
                landing,
                &heights,
                reachable,
                default_config().max_added_puyos,
            );
            let values = components.as_slice();
            let expected_normal = planes
                .iter()
                .copied()
                .take(NORMAL_COLOR_COUNT)
                .fold(0_u128, |value, plane| value | plane);
            let expected_growth = values
                .iter()
                .fold(0_u8, |value, component| value | component.extension_columns);

            assert_eq!(components.len, values.len(), "component count: {index}");
            assert_eq!(
                components.base_normal, expected_normal,
                "normal mask: {index}"
            );
            assert_eq!(
                components.isolated_count,
                values
                    .iter()
                    .filter(|component| component.size == 1)
                    .count() as u8,
                "isolated count: {index}",
            );
            assert_eq!(
                components.base_remaining.link_2,
                values
                    .iter()
                    .filter(|component| component.size == 2)
                    .count() as u8,
                "link-2 count: {index}",
            );
            assert_eq!(
                components.base_remaining.link_3,
                values
                    .iter()
                    .filter(|component| component.size == 3)
                    .count() as u8,
                "link-3 count: {index}",
            );
            assert_eq!(
                components.base_remaining.connection_edges,
                values
                    .iter()
                    .map(|component| component.connection_edges)
                    .sum::<u8>(),
                "connection edges: {index}",
            );
            assert_eq!(
                components.reachable_ignition_count,
                values
                    .iter()
                    .filter(|component| { component.size == 3 && component.extension_columns != 0 })
                    .count() as u8,
                "reachable ignition count: {index}",
            );
            assert_eq!(
                components.growth_columns, expected_growth,
                "growth sites: {index}"
            );
            assert_eq!(
                components.connection_candidate_count,
                connection_candidate_count_exact(values, landing),
                "connection candidates: {index}",
            );
        }
    }

    #[test]
    fn resolution_prefilter_and_remaining_bitsets_match_exact_property_corpus() {
        for index in 0..512 {
            let state = property_state(0x2200_0000 + index);
            let planes = state.evaluator_planes();
            assert_eq!(
                find_vanish_prefiltered(&planes),
                find_vanish_exact(&planes),
                "vanish-{index}"
            );
            assert_eq!(
                remaining_structure(&planes),
                remaining_structure_exact(&planes),
                "remaining-{index}"
            );
            let visible_planes =
                std::array::from_fn(|plane_index| planes[plane_index] & VISIBLE_MASK);
            let terminal_analysis = component_analysis::<true>(&visible_planes);
            let exact_analysis = component_analysis::<false>(&visible_planes);
            assert_eq!(
                terminal_analysis.has_poppable,
                find_vanish_exact(&visible_planes).mask != 0,
                "component-analysis-{index}"
            );
            if !terminal_analysis.has_poppable {
                assert_eq!(
                    terminal_analysis.remaining, exact_analysis.remaining,
                    "terminal-remaining-{index}"
                );
            }
        }
    }

    #[test]
    fn candidate_tie_break_matches_python_canonical_sha256() {
        let candidate = QuiescenceCandidate {
            chain_count: 1,
            chain_score: 40,
            required_key_count: 3,
            trigger_color: 1,
            placements_mask: (1_u128 << 1) | (1_u128 << 16) | (1_u128 << 32),
            anchor_mask: 1,
            trigger_height: 0,
            ..QuiescenceCandidate::default()
        };
        let mut encoding = CandidateJson::new();
        encode_stable_candidate(&candidate, &mut encoding);

        assert_eq!(
            encoding.as_slice(),
            br#"["BLUE",[[0,1],[1,0],[2,0]],[[0,0]],1,40,3,0,0,0,0,0]"#,
        );
        assert_eq!(
            stable_candidate_digest(&candidate),
            [
                0xdf, 0x61, 0x74, 0x8c, 0x40, 0xb1, 0x93, 0x66, 0xf0, 0x46, 0x1b, 0x54, 0x6b, 0x1d,
                0xa5, 0x61, 0x26, 0x69, 0x3b, 0x2c, 0xde, 0xd4, 0xab, 0x59, 0x3e, 0x1c, 0xd8, 0x0c,
                0xe0, 0xa9, 0xb2, 0xbf,
            ]
        );
    }

    #[test]
    fn candidate_tie_break_workspace_reuses_canonical_best() {
        let best = QuiescenceCandidate {
            chain_count: 1,
            chain_score: 40,
            required_key_count: 3,
            trigger_color: 1,
            placements_mask: (1_u128 << 1) | (1_u128 << 16) | (1_u128 << 32),
            anchor_mask: 1,
            trigger_height: 0,
            ..QuiescenceCandidate::default()
        };
        let mut workspace = CandidateTieBreakWorkspace::default();

        let first = workspace.compare(&best, &best);
        assert_eq!(first.ordering, Ordering::Equal);
        assert_eq!(first.digest, stable_candidate_digest(&best));
        assert_eq!(first.sha256_calls, 1);

        let repeated = workspace.compare(&best, &best);
        assert_eq!(repeated.ordering, Ordering::Equal);
        assert_eq!(repeated.digest, stable_candidate_digest(&best));
        assert_eq!(repeated.sha256_calls, 0);

        let different = QuiescenceCandidate {
            placements_mask: (1_u128 << 2) | (1_u128 << 17) | (1_u128 << 33),
            ..best
        };
        let comparison = workspace.compare(&different, &best);
        assert_eq!(
            comparison.ordering,
            stable_candidate_digest(&different).cmp(&stable_candidate_digest(&best)),
        );
        assert_eq!(comparison.digest, stable_candidate_digest(&different));
        assert_eq!(comparison.sha256_calls, 1);
    }

    #[test]
    fn packed_candidate_rank_matches_fieldwise_ordering() {
        for index in 0..4_096_u32 {
            let candidate = |salt: u32| QuiescenceCandidate {
                chain_count: index.wrapping_mul(17).wrapping_add(salt) as u8,
                chain_score: index.wrapping_mul(2_654_435_761).wrapping_add(salt),
                required_key_count: index.wrapping_add(salt) as u8,
                trigger_height: index.wrapping_mul(3).wrapping_add(salt) as u8,
                trigger_protection: f64::from(index.wrapping_mul(7).wrapping_add(salt) % 17) / 16.0,
                remaining_link_2: index.wrapping_mul(5).wrapping_add(salt) as u8,
                remaining_link_3: index.wrapping_mul(11).wrapping_add(salt) as u8,
                remaining_connection_edges: index.wrapping_mul(13).wrapping_add(salt) as u8,
                extension_space: index.wrapping_mul(19).wrapping_add(salt) as u8,
                ..QuiescenceCandidate::default()
            };
            let left = candidate(0);
            let right = candidate(index.rotate_left(7));
            let packed = candidate_rank_key(&left)
                .cmp(&candidate_rank_key(&right))
                .then_with(|| compare_candidate_rank_suffix(&left, &right));
            assert_eq!(
                packed,
                compare_candidate_rank_prefix_exact(&left, &right),
                "candidate pair {index}",
            );
        }
    }

    #[test]
    fn candidate_workspace_matches_full_digest_ordering_property_corpus() {
        let candidate = |seed: u32| {
            let mut placements_mask = 0_u128;
            let mut anchor_mask = 0_u128;
            for offset in 0..3_u32 {
                let placement = seed.wrapping_mul(17).wrapping_add(offset.wrapping_mul(29));
                let anchor = seed.wrapping_mul(31).wrapping_add(offset.wrapping_mul(11));
                placements_mask |= cell_bit(
                    placement as usize % WIDTH,
                    (placement as usize / WIDTH) % VISIBLE_HEIGHT,
                );
                anchor_mask |= cell_bit(
                    anchor as usize % WIDTH,
                    (anchor as usize / WIDTH) % VISIBLE_HEIGHT,
                );
            }
            QuiescenceCandidate {
                chain_count: 3,
                chain_score: 2_280,
                required_key_count: 3,
                trigger_color: (seed % NORMAL_COLOR_COUNT as u32) as u8,
                placements_mask,
                anchor_mask,
                trigger_height: 4,
                trigger_protection: 0.5,
                remaining_link_2: 2,
                remaining_link_3: 1,
                remaining_connection_edges: 5,
                extension_space: 7,
                ..QuiescenceCandidate::default()
            }
        };
        for index in 0..4_096_u32 {
            let best = candidate(index);
            let current = candidate(index.rotate_left(11) ^ 0x2240_0000);
            let mut workspace = CandidateTieBreakWorkspace::default();

            let comparison = workspace.compare(&current, &best);

            assert_eq!(
                comparison.ordering,
                stable_candidate_digest(&current).cmp(&stable_candidate_digest(&best)),
                "candidate pair {index}",
            );
            assert_eq!(
                comparison.digest,
                stable_candidate_digest(&current),
                "candidate digest {index}",
            );
            assert!(comparison.sha256_calls <= 2);
        }
    }

    #[test]
    fn bit_parallel_trigger_protection_matches_exact_property_corpus() {
        for index in 0..512 {
            let state = property_state(0x2240_0000 + index);
            let occupied = state.internal_occupied();
            let planes = state.evaluator_planes();
            for (plane_index, plane) in planes.iter().copied().take(NORMAL_COLOR_COUNT).enumerate()
            {
                let anchors = plane & VISIBLE_MASK;
                assert_eq!(
                    trigger_protection(occupied, anchors),
                    trigger_protection_exact(occupied, anchors),
                    "state {index}, plane {plane_index}",
                );
            }
        }
        assert_eq!(trigger_protection(0, 0), trigger_protection_exact(0, 0));
    }

    #[test]
    fn empty_state_matches_python_zero_contract() {
        let state = state_with_cells(&[]);
        let evaluation = evaluate_evidence(&state, &default_config(), None, None, 6);

        assert_eq!(evaluation.hot.status, EvaluationStatus::NotFound);
        assert_eq!(evaluation.hot.pattern_nodes, 415);
        assert_eq!(evaluation.hot.resolution_nodes, 0);
        assert_eq!(evaluation.hot.score, 0.0);
        assert_eq!(evaluation.candidate_count, 0);
        assert_eq!(
            evaluation.hot.features,
            ChainStructureFeatures {
                canonical_column_heights: [0; WIDTH],
                required_key_count: -1,
                trigger_column: -1,
                trigger_height: -1,
                ..ChainStructureFeatures::default()
            }
        );
    }

    #[test]
    fn one_key_trigger_matches_python_feature_contract() {
        let state = state_with_cells(&[(0, 0, 0), (0, 1, 0), (0, 2, 0)]);
        let evaluation = evaluate_evidence(&state, &default_config(), None, None, 6);

        assert_eq!(evaluation.hot.status, EvaluationStatus::Available);
        assert!(evaluation.hot.has_best);
        assert_eq!(evaluation.hot.best.chain_count, 1);
        assert_eq!(evaluation.hot.best.required_key_count, 1);
        assert_eq!(evaluation.hot.best.chain_score, 40);
        assert_eq!(evaluation.hot.features.link_3, 1);
        assert_eq!(evaluation.hot.features.reachable_ignition_count, 1);
    }

    #[test]
    fn hot_and_evidence_paths_match_and_allocate_nothing() {
        let state = state_with_cells(&[
            (0, 0, 0),
            (0, 1, 0),
            (0, 2, 0),
            (1, 0, 1),
            (1, 1, 1),
            (1, 2, 1),
        ]);
        let config = default_config();
        let mut detailed = evaluate_evidence(&state, &config, None, None, 6);
        let (hot, allocations) = count_allocations(|| evaluate_hot(&state, &config, None, None, 6));

        detailed.hot.best.fixed_tie_break = 0;
        assert_eq!(hot, detailed.hot);
        assert_eq!(allocations, 0);
    }

    #[test]
    fn profiled_path_matches_hot_result_and_reports_exact_work() {
        let state = state_with_cells(&[
            (0, 0, 0),
            (0, 1, 0),
            (0, 2, 0),
            (1, 0, 1),
            (1, 1, 1),
            (1, 2, 1),
        ]);
        let config = default_config();
        let expected = evaluate_hot(&state, &config, None, None, 6);
        let marker = AtomicU8::new(PROFILE_STAGE_DRIVER);

        let (actual, counts) = evaluate_profiled(&state, &config, None, None, 6, &marker);

        assert_eq!(actual, expected);
        assert_eq!(counts.pattern_nodes, actual.pattern_nodes);
        assert!(counts.executed_pattern_probes <= counts.pattern_nodes);
        assert_eq!(counts.resolution_nodes, actual.resolution_nodes);
        assert!(
            counts.stage_entries[usize::from(PROFILE_STAGE_RESOLVE)] <= counts.resolution_nodes
        );
        assert_eq!(
            counts.stage_entries[usize::from(PROFILE_STAGE_BASE_FEATURES)],
            2
        );
        for stage in [
            PROFILE_STAGE_PLACEMENT_ORBIT,
            PROFILE_STAGE_PLACEMENT_FRONTIER,
            PROFILE_STAGE_PLACEMENT_QUALIFICATION,
            PROFILE_STAGE_PLACEMENT_DEDUPLICATION,
            PROFILE_STAGE_PLACEMENT_DISPATCH,
        ] {
            assert!(counts.stage_entries[usize::from(stage)] > 0);
        }
        assert!(counts.sha256_calls >= counts.rank_tie_calls);
        assert_eq!(
            marker.load(AtomicOrdering::Relaxed),
            PROFILE_STAGE_BASE_FEATURES
        );
    }

    #[test]
    fn action_features_use_parent_without_state_cache_pollution() {
        let parent_state = state_with_cells(&[(0, 0, 0), (0, 1, 0), (0, 2, 0)]);
        let config = default_config();
        let parent = evaluate_hot(&parent_state, &config, None, None, 6);
        let pair = Pair::from_ids(1, 2).expect("valid pair");
        let (child, action) = transition_hot(&parent_state, pair, 0).expect("valid transition");
        let without_action = evaluate_hot(&child, &config, None, None, 6);
        let with_action = evaluate_hot(&child, &config, Some(&parent), Some(action), 6);

        assert_eq!(without_action.features, with_action.features);
        assert!(!without_action.action_features.evaluated);
        assert!(with_action.action_features.evaluated);
    }

    #[test]
    fn combined_transition_and_evaluator_hot_path_allocates_nothing() {
        let parent_state =
            state_with_cells(&[(0, 0, 0), (0, 1, 0), (0, 2, 0), (1, 3, 0), (2, 4, 0)]);
        let config = default_config();
        let parent = evaluate_hot(&parent_state, &config, None, None, 6);
        let pair = Pair::from_ids(1, 2).expect("valid pair");

        let (_, allocations) = count_allocations(|| {
            let (child, action) = transition_hot(&parent_state, pair, 0).expect("valid transition");
            evaluate_hot(&child, &config, Some(&parent), Some(action), 6)
        });

        assert_eq!(allocations, 0);
    }

    #[test]
    #[ignore = "manual release-mode evaluator profile"]
    fn profile_hot_evaluator() {
        let states = [
            state_with_cells(&[]),
            state_with_cells(&[(0, 0, 0), (0, 1, 0), (0, 2, 0)]),
            state_with_cells(&[
                (0, 0, 0),
                (0, 1, 0),
                (1, 2, 0),
                (1, 2, 1),
                (2, 3, 0),
                (2, 4, 0),
                (5, 5, 0),
            ]),
            state_with_cells(&[
                (0, 0, 0),
                (1, 1, 0),
                (2, 2, 0),
                (3, 3, 0),
                (4, 4, 0),
                (5, 5, 0),
            ]),
        ];
        let config = default_config();
        let operations = 600_000_usize;
        for (state_index, state) in states.iter().enumerate() {
            let per_state_operations = operations / states.len();
            let state_started = Instant::now();
            let mut state_checksum = 0_u64;
            for index in 0..per_state_operations {
                let result = evaluate_hot(black_box(state), black_box(&config), None, None, 6);
                state_checksum ^=
                    black_box(result.score.to_bits().rotate_left((index % 63) as u32));
            }
            let state_elapsed = state_started.elapsed();
            let sample = evaluate_hot(state, &config, None, None, 6);
            eprintln!(
                "evaluator state={state_index} ns_per_call={:.3} pattern_nodes={} resolution_nodes={} checksum={state_checksum}",
                state_elapsed.as_nanos() as f64 / per_state_operations as f64,
                sample.pattern_nodes,
                sample.resolution_nodes,
            );
        }
        let started = Instant::now();
        let mut checksum = 0_u64;
        for index in 0..operations {
            let result = evaluate_hot(
                black_box(&states[index % states.len()]),
                black_box(&config),
                None,
                None,
                6,
            );
            checksum ^= black_box(result.score.to_bits().rotate_left((index % 63) as u32));
        }
        let elapsed = started.elapsed();
        eprintln!(
            "evaluator operations={operations} elapsed_ms={:.3} ns_per_call={:.3} checksum={checksum}",
            elapsed.as_secs_f64() * 1_000.0,
            elapsed.as_nanos() as f64 / operations as f64,
        );
    }
}
