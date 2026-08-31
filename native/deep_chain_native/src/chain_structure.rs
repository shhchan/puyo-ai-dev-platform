//! Allocation-free native chain-structure evaluator and bounded quiescence.
//!
//! The Python implementation in `agents/chain_structure.py` remains the
//! differential oracle.  This module consumes the transition kernel's native
//! `CompactState` directly and returns fixed-width scalar values.  Candidate
//! evidence is optional and bounded; the normal search path neither allocates
//! nor materializes Python-facing objects.

use std::cmp::Ordering;
use std::sync::atomic::{AtomicU8, Ordering as AtomicOrdering};

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

pub(crate) const PROFILE_STAGE_DRIVER: u8 = 0;
pub(crate) const PROFILE_STAGE_TRANSITION: u8 = 1;
pub(crate) const PROFILE_STAGE_BASE_FEATURES: u8 = 2;
pub(crate) const PROFILE_STAGE_PLACEMENT: u8 = 3;
pub(crate) const PROFILE_STAGE_RESOLVE: u8 = 4;
pub(crate) const PROFILE_STAGE_REMAINING: u8 = 5;
pub(crate) const PROFILE_STAGE_RANKING: u8 = 6;
pub(crate) const PROFILE_STAGE_COUNT: usize = 7;

const COLUMN_LANE_BITS: usize = 16;
const BOARD_MASK: u128 = lane_mask(HEIGHT);
const VISIBLE_MASK: u128 = lane_mask(VISIBLE_HEIGHT);
const HIDDEN_MASK: u128 = BOARD_MASK & !VISIBLE_MASK;
const ROW_ZERO_MASK: u128 = row_mask(0);
const ROW_THREE_MASK: u128 = lane_mask(3);
const ROW_FOURTEEN_MASK: u128 = row_mask(HEIGHT - 1);
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
    pub(crate) resolution_nodes: u32,
    pub(crate) rank_comparison_calls: u32,
    pub(crate) rank_tie_calls: u32,
    pub(crate) sha256_calls: u32,
    pub(crate) stage_entries: [u32; PROFILE_STAGE_COUNT],
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
    extensions: u128,
    color: u8,
    size: u8,
    connection_edges: u8,
}

#[derive(Clone, Copy)]
struct ComponentSet {
    values: [Component; MAX_COMPONENTS],
    len: usize,
}

impl Default for ComponentSet {
    fn default() -> Self {
        Self {
            values: [Component::default(); MAX_COMPONENTS],
            len: 0,
        }
    }
}

impl ComponentSet {
    fn push(&mut self, value: Component) {
        debug_assert!(self.len < self.values.len());
        self.values[self.len] = value;
        self.len += 1;
    }

    fn as_slice(&self) -> &[Component] {
        &self.values[..self.len]
    }
}

#[derive(Clone, Copy, Debug, Default)]
struct PlacementPattern {
    mask: u128,
    counts: [u8; WIDTH],
    minimum_column: u8,
    minimum_height: u8,
}

#[derive(Clone, Copy, Debug, Default)]
struct ResolvedVirtual {
    planes: [u128; PLANE_COUNT],
    chain_count: u8,
    score: u32,
}

#[derive(Clone, Copy, Debug, Default)]
struct RemainingStructure {
    link_2: u8,
    link_3: u8,
    connection_edges: u8,
    extension_space: u8,
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

fn extract_components(planes: &[u128; PLANE_COUNT], occupied: u128, landing: u128) -> ComponentSet {
    let mut result = ComponentSet::default();
    for (plane_index, plane) in planes.iter().copied().enumerate().take(NORMAL_COLOR_COUNT) {
        let mut remaining = plane;
        while remaining != 0 {
            let seed = 1_u128 << remaining.trailing_zeros();
            let mask = flood(seed, plane, false);
            remaining &= !mask;
            let connection_edges = ((mask & (mask >> COLUMN_LANE_BITS)).count_ones()
                + (mask & (mask >> 1)).count_ones()) as u8;
            let extensions = board_neighbors(mask) & landing & !occupied;
            result.push(Component {
                mask,
                extensions,
                color: plane_index as u8,
                size: mask.count_ones() as u8,
                connection_edges,
            });
        }
    }
    result
}

fn connection_candidate_count(components: &[Component], landing: u128) -> u8 {
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

struct CandidateJson {
    bytes: [u8; 1024],
    len: usize,
}

impl CandidateJson {
    fn new() -> Self {
        Self {
            bytes: [0; 1024],
            len: 0,
        }
    }

    fn push(&mut self, value: u8) {
        debug_assert!(self.len < self.bytes.len());
        self.bytes[self.len] = value;
        self.len += 1;
    }

    fn extend(&mut self, value: &[u8]) {
        debug_assert!(self.len + value.len() <= self.bytes.len());
        self.bytes[self.len..self.len + value.len()].copy_from_slice(value);
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
        &self.bytes[..self.len]
    }
}

const SHA256_ROUND_CONSTANTS: [u32; 64] = [
    0x428a_2f98,
    0x7137_4491,
    0xb5c0_fbcf,
    0xe9b5_dba5,
    0x3956_c25b,
    0x59f1_11f1,
    0x923f_82a4,
    0xab1c_5ed5,
    0xd807_aa98,
    0x1283_5b01,
    0x2431_85be,
    0x550c_7dc3,
    0x72be_5d74,
    0x80de_b1fe,
    0x9bdc_06a7,
    0xc19b_f174,
    0xe49b_69c1,
    0xefbe_4786,
    0x0fc1_9dc6,
    0x240c_a1cc,
    0x2de9_2c6f,
    0x4a74_84aa,
    0x5cb0_a9dc,
    0x76f9_88da,
    0x983e_5152,
    0xa831_c66d,
    0xb003_27c8,
    0xbf59_7fc7,
    0xc6e0_0bf3,
    0xd5a7_9147,
    0x06ca_6351,
    0x1429_2967,
    0x27b7_0a85,
    0x2e1b_2138,
    0x4d2c_6dfc,
    0x5338_0d13,
    0x650a_7354,
    0x766a_0abb,
    0x81c2_c92e,
    0x9272_2c85,
    0xa2bf_e8a1,
    0xa81a_664b,
    0xc24b_8b70,
    0xc76c_51a3,
    0xd192_e819,
    0xd699_0624,
    0xf40e_3585,
    0x106a_a070,
    0x19a4_c116,
    0x1e37_6c08,
    0x2748_774c,
    0x34b0_bcb5,
    0x391c_0cb3,
    0x4ed8_aa4a,
    0x5b9c_ca4f,
    0x682e_6ff3,
    0x748f_82ee,
    0x78a5_636f,
    0x84c8_7814,
    0x8cc7_0208,
    0x90be_fffa,
    0xa450_6ceb,
    0xbef9_a3f7,
    0xc671_78f2,
];

fn sha256_compress(state: &mut [u32; 8], block: &[u8]) {
    debug_assert_eq!(block.len(), 64);
    let mut schedule = [0_u32; 64];
    let (words, remainder) = block.as_chunks::<4>();
    debug_assert!(remainder.is_empty());
    for (index, bytes) in words.iter().take(16).enumerate() {
        schedule[index] = u32::from_be_bytes(*bytes);
    }
    for index in 16..64 {
        let s0 = schedule[index - 15].rotate_right(7)
            ^ schedule[index - 15].rotate_right(18)
            ^ (schedule[index - 15] >> 3);
        let s1 = schedule[index - 2].rotate_right(17)
            ^ schedule[index - 2].rotate_right(19)
            ^ (schedule[index - 2] >> 10);
        schedule[index] = schedule[index - 16]
            .wrapping_add(s0)
            .wrapping_add(schedule[index - 7])
            .wrapping_add(s1);
    }
    let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = *state;
    for index in 0..64 {
        let sum_one = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
        let choose = (e & f) ^ (!e & g);
        let first = h
            .wrapping_add(sum_one)
            .wrapping_add(choose)
            .wrapping_add(SHA256_ROUND_CONSTANTS[index])
            .wrapping_add(schedule[index]);
        let sum_zero = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
        let majority = (a & b) ^ (a & c) ^ (b & c);
        let second = sum_zero.wrapping_add(majority);
        h = g;
        g = f;
        f = e;
        e = d.wrapping_add(first);
        d = c;
        c = b;
        b = a;
        a = first.wrapping_add(second);
    }
    for (target, value) in state.iter_mut().zip([a, b, c, d, e, f, g, h]) {
        *target = target.wrapping_add(value);
    }
}

fn sha256(value: &[u8]) -> [u8; 32] {
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
    let (blocks, remainder) = value.as_chunks::<64>();
    for block in blocks {
        sha256_compress(&mut state, block);
    }
    let mut tail = [0_u8; 128];
    tail[..remainder.len()].copy_from_slice(remainder);
    tail[remainder.len()] = 0x80;
    let tail_len = if remainder.len() < 56 { 64 } else { 128 };
    tail[tail_len - 8..tail_len].copy_from_slice(&((value.len() as u64) * 8).to_be_bytes());
    let (tail_blocks, tail_remainder) = tail[..tail_len].as_chunks::<64>();
    debug_assert!(tail_remainder.is_empty());
    for block in tail_blocks {
        sha256_compress(&mut state, block);
    }
    let mut result = [0_u8; 32];
    let (result_words, result_remainder) = result.as_chunks_mut::<4>();
    debug_assert!(result_remainder.is_empty());
    for (target, word) in result_words.iter_mut().zip(state) {
        target.copy_from_slice(&word.to_be_bytes());
    }
    result
}

fn stable_candidate_digest(candidate: &QuiescenceCandidate) -> [u8; 32] {
    let mut json = CandidateJson::new();
    let color = match candidate.trigger_color {
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
    json.cells(canonical_mask(candidate.placements_mask));
    json.push(b',');
    json.cells(canonical_mask(candidate.anchor_mask));
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
    sha256(json.as_slice())
}

fn fixed_candidate_key(candidate: &QuiescenceCandidate) -> u64 {
    u64::from_be_bytes(
        stable_candidate_digest(candidate)[..8]
            .try_into()
            .expect("SHA-256 prefix has eight bytes"),
    )
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

fn compare_candidate_rank_prefix(
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

fn pattern_from_columns(columns: &[u8], heights: &[u8; WIDTH]) -> PlacementPattern {
    let mut counts = [0_u8; WIDTH];
    for column in columns {
        counts[usize::from(*column)] += 1;
    }
    let mut mask = 0_u128;
    let mut minimum_height = u8::MAX;
    for (x, count) in counts.iter().copied().enumerate() {
        for offset in 0..count {
            let y = heights[x] + offset;
            minimum_height = minimum_height.min(y);
            mask |= cell_bit(x, usize::from(y));
        }
    }
    PlacementPattern {
        mask,
        counts,
        minimum_column: columns[0],
        minimum_height,
    }
}

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

fn trigger_protection(occupied: u128, anchors: u128) -> f64 {
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

fn apply_gravity(planes: &[u128; PLANE_COUNT]) -> [u128; PLANE_COUNT] {
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
            let mut mask = occupied_column;
            let mut packed = 0_u16;
            let mut destination = 1_u16;
            while mask != 0 {
                let lowest = mask.isolate_lowest_one();
                if source & lowest != 0 {
                    packed |= destination;
                }
                mask &= mask - 1;
                destination <<= 1;
            }
            result[plane_index] |= u128::from(packed) << shift;
        }
    }
    result
}

fn resolve_virtual(mut planes: [u128; PLANE_COUNT]) -> ResolvedVirtual {
    let mut chain_count = 0_u8;
    let mut score = 0_u32;
    loop {
        let mut vanished = 0_u128;
        let mut vanished_count = 0_u32;
        let mut total_connection_bonus = 0_u16;
        let mut color_count = 0_u8;
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
                vanished |= group;
                vanished_count += count;
                total_connection_bonus += connection_bonus(count);
                color_vanished = true;
            }
            color_count += u8::from(color_vanished);
        }
        if vanished == 0 {
            break;
        }
        chain_count += 1;
        let garbage = visible_neighbors(vanished) & planes[PLANE_COUNT - 1] & VISIBLE_MASK;
        let chain_bonus = CHAIN_BONUS[usize::from(chain_count).min(CHAIN_BONUS.len() - 1)];
        let color_bonus = COLOR_BONUS[usize::from(color_count)];
        let bonus = (chain_bonus + total_connection_bonus + color_bonus).max(1);
        score += vanished_count * 10 * u32::from(bonus);
        for plane in planes.iter_mut().take(NORMAL_COLOR_COUNT) {
            *plane &= !vanished;
        }
        planes[PLANE_COUNT - 1] &= !garbage;
        planes = apply_gravity(&planes);
    }
    ResolvedVirtual {
        planes,
        chain_count,
        score,
    }
}

fn remaining_structure(planes: &[u128; PLANE_COUNT]) -> RemainingStructure {
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
        let mut remaining = plane;
        while remaining != 0 {
            let seed = 1_u128 << remaining.trailing_zeros();
            let component = flood(seed, plane, false);
            remaining &= !component;
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

fn compare_columns(left: &[u8], right: &[u8]) -> Ordering {
    for (left_value, right_value) in left.iter().zip(right) {
        match left_value.cmp(right_value) {
            Ordering::Equal => {}
            ordering => return ordering,
        }
    }
    Ordering::Equal
}

struct QuiescenceSearch<'a, const EVIDENCE: bool, const PROFILE: bool> {
    planes: &'a [u128; PLANE_COUNT],
    occupied: u128,
    heights: &'a [u8; WIDTH],
    reachable: u8,
    config: &'a EvaluationConfig,
    pattern_nodes: u32,
    resolution_nodes: u32,
    truncation_reason: TruncationReason,
    best: QuiescenceCandidate,
    best_tie_break: [u8; 32],
    best_tie_break_valid: bool,
    has_best: bool,
    candidates: [QuiescenceCandidate; MAX_EVIDENCE_CANDIDATES],
    candidate_count: usize,
    profile_stage: Option<&'a AtomicU8>,
    profile_counts: EvaluationProfileCounts,
}

impl<'a, const EVIDENCE: bool, const PROFILE: bool> QuiescenceSearch<'a, EVIDENCE, PROFILE> {
    fn new(
        planes: &'a [u128; PLANE_COUNT],
        occupied: u128,
        heights: &'a [u8; WIDTH],
        reachable: u8,
        config: &'a EvaluationConfig,
        profile_stage: Option<&'a AtomicU8>,
    ) -> Self {
        Self {
            planes,
            occupied,
            heights,
            reachable,
            config,
            pattern_nodes: 0,
            resolution_nodes: 0,
            truncation_reason: TruncationReason::None,
            best: QuiescenceCandidate::default(),
            best_tie_break: [0; 32],
            best_tie_break_valid: false,
            has_best: false,
            candidates: [QuiescenceCandidate::default(); MAX_EVIDENCE_CANDIDATES],
            candidate_count: 0,
            profile_stage,
            profile_counts: EvaluationProfileCounts::default(),
        }
    }

    #[inline]
    fn enter_profile_stage(&mut self, stage: u8) {
        if !PROFILE {
            return;
        }
        let Some(marker) = self.profile_stage else {
            return;
        };
        marker.store(stage, AtomicOrdering::Relaxed);
        self.profile_counts.stage_entries[usize::from(stage)] += 1;
    }

    fn candidate_is_better(&mut self, candidate: &QuiescenceCandidate) -> bool {
        if PROFILE {
            self.profile_counts.rank_comparison_calls += 1;
        }
        match compare_candidate_rank_prefix(candidate, &self.best) {
            Ordering::Greater => {
                self.best_tie_break_valid = false;
                true
            }
            Ordering::Less => false,
            Ordering::Equal => {
                if PROFILE {
                    self.profile_counts.rank_tie_calls += 1;
                    self.profile_counts.sha256_calls += 1;
                }
                let candidate_tie_break = stable_candidate_digest(candidate);
                if !self.best_tie_break_valid {
                    if PROFILE {
                        self.profile_counts.sha256_calls += 1;
                    }
                    self.best_tie_break = stable_candidate_digest(&self.best);
                    self.best_tie_break_valid = true;
                }
                if candidate_tie_break > self.best_tie_break {
                    self.best_tie_break = candidate_tie_break;
                    true
                } else {
                    false
                }
            }
        }
    }

    fn truncated(&self) -> bool {
        self.truncation_reason != TruncationReason::None
    }

    fn consider_columns(&mut self, columns: &[u8], added_puyos: u8) {
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
                self.evaluate_orbit(&valid[..valid_count], added_puyos);
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
                self.evaluate_orbit(&valid[..valid_count], added_puyos);
            }
        }
    }

    fn evaluate_orbit(&mut self, valid: &[PlacementPattern], added_puyos: u8) {
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
            let mut pending = [(PlacementPattern::default(), 0_u128); 2];
            let mut pending_count = 0_usize;
            for pattern in valid {
                if visible_neighbors(pattern.mask) & plane == 0 {
                    continue;
                }
                let Some(anchors) = virtual_trigger_anchors(plane, pattern.mask) else {
                    continue;
                };
                if has_smaller_trigger(plane, pattern, self.heights) {
                    continue;
                }
                pending[pending_count] = (*pattern, anchors);
                pending_count += 1;
            }
            if self.resolution_nodes + pending_count as u32 > self.config.max_resolution_nodes {
                self.truncation_reason = TruncationReason::ResolutionNodes;
                return;
            }
            for (pattern, anchors) in pending.iter().copied().take(pending_count) {
                self.enter_profile_stage(PROFILE_STAGE_RESOLVE);
                let mut virtual_planes = *self.planes;
                virtual_planes[plane_index] |= pattern.mask;
                let resolved = resolve_virtual(virtual_planes);
                self.resolution_nodes += 1;
                if PROFILE {
                    self.profile_counts.resolution_nodes += 1;
                }
                if resolved.chain_count == 0 {
                    self.enter_profile_stage(PROFILE_STAGE_PLACEMENT);
                    continue;
                }
                self.enter_profile_stage(PROFILE_STAGE_REMAINING);
                let remaining = remaining_structure(&resolved.planes);
                self.enter_profile_stage(PROFILE_STAGE_RANKING);
                let mut candidate = QuiescenceCandidate {
                    chain_count: resolved.chain_count,
                    chain_score: resolved.score,
                    required_key_count: added_puyos,
                    trigger_color: plane_index as u8,
                    placements_mask: pattern.mask,
                    anchor_mask: anchors,
                    trigger_column: pattern.minimum_column,
                    trigger_height: pattern.minimum_height,
                    trigger_protection: trigger_protection(self.occupied, anchors),
                    remaining_link_2: remaining.link_2,
                    remaining_link_3: remaining.link_3,
                    remaining_connection_edges: remaining.connection_edges,
                    extension_space: remaining.extension_space,
                    fixed_tie_break: 0,
                };
                if EVIDENCE {
                    candidate.fixed_tie_break = fixed_candidate_key(&candidate);
                }
                if !self.has_best || self.candidate_is_better(&candidate) {
                    self.best = candidate;
                    self.has_best = true;
                }
                if EVIDENCE {
                    self.push_evidence(candidate);
                }
                self.enter_profile_stage(PROFILE_STAGE_PLACEMENT);
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

fn bounded_quiescence<'a, const EVIDENCE: bool, const PROFILE: bool>(
    planes: &'a [u128; PLANE_COUNT],
    occupied: u128,
    heights: &'a [u8; WIDTH],
    reachable: u8,
    config: &'a EvaluationConfig,
    profile_stage: Option<&'a AtomicU8>,
) -> QuiescenceSearch<'a, EVIDENCE, PROFILE> {
    let mut search = QuiescenceSearch::<EVIDENCE, PROFILE>::new(
        planes,
        occupied,
        heights,
        reachable,
        config,
        profile_stage,
    );
    search.enter_profile_stage(PROFILE_STAGE_PLACEMENT);
    for first in 0..WIDTH as u8 {
        search.consider_columns(&[first], 1);
        if search.truncated() {
            return search;
        }
    }
    if config.max_added_puyos >= 2 {
        for first in 0..WIDTH as u8 {
            for second in first..WIDTH as u8 {
                search.consider_columns(&[first, second], 2);
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
                    search.consider_columns(&[first, second, third], 3);
                    if search.truncated() {
                        return search;
                    }
                }
            }
        }
    }
    if PROFILE {
        search.profile_counts.pattern_nodes = search.pattern_nodes;
        search.profile_counts.resolution_nodes = search.resolution_nodes;
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

fn build_features<const EVIDENCE: bool, const PROFILE: bool>(
    state: &CompactState,
    planes: &[u128; PLANE_COUNT],
    occupied: u128,
    heights: &[u8; WIDTH],
    components: &[Component],
    connections: u8,
    quiescence: &QuiescenceSearch<'_, EVIDENCE, PROFILE>,
) -> ChainStructureFeatures {
    let normal_mask = planes
        .iter()
        .copied()
        .take(NORMAL_COLOR_COUNT)
        .fold(0_u128, |value, plane| value | plane);
    let normal_count = normal_mask.count_ones() as u8;
    let nuisance_count = planes[PLANE_COUNT - 1].count_ones() as u8;
    let hidden_row_count = (occupied & HIDDEN_MASK).count_ones() as u8;
    let isolated_count = components.iter().filter(|value| value.size == 1).count() as u8;
    let link_2 = components.iter().filter(|value| value.size == 2).count() as u8;
    let link_3 = components.iter().filter(|value| value.size == 3).count() as u8;
    let connectivity_edges = components
        .iter()
        .map(|value| value.connection_edges)
        .sum::<u8>();
    let reachable_ignition_count = components
        .iter()
        .filter(|value| value.size == 3 && value.extensions != 0)
        .count() as u8;
    let growth_mask = components
        .iter()
        .fold(0_u128, |value, component| value | component.extensions);
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
    let structural_dead_end = unreachable_trigger && connections == 0 && growth_mask == 0;
    let mut result = ChainStructureFeatures {
        canonical_column_heights: canonical_heights(heights),
        normal_puyo_count: normal_count,
        component_count: components.len() as u8,
        isolated_count,
        link_2,
        link_3,
        connectivity_edges,
        connection_candidate_count: connections,
        reachable_ignition_count,
        growth_site_count: growth_mask.count_ones() as u8,
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
    let components = extract_components(&planes, occupied, landing);
    let connections = connection_candidate_count(components.as_slice(), landing);
    let mut quiescence = bounded_quiescence::<EVIDENCE, PROFILE>(
        &planes,
        occupied,
        &heights,
        reachable,
        config,
        profile_stage,
    );
    if PROFILE && let Some(marker) = profile_stage {
        marker.store(PROFILE_STAGE_BASE_FEATURES, AtomicOrdering::Relaxed);
        quiescence.profile_counts.stage_entries[usize::from(PROFILE_STAGE_BASE_FEATURES)] += 2;
    }
    let features = build_features(
        state,
        &planes,
        occupied,
        &heights,
        components.as_slice(),
        connections,
        &quiescence,
    );
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
        assert_eq!(counts.resolution_nodes, actual.resolution_nodes);
        assert_eq!(
            counts.stage_entries[usize::from(PROFILE_STAGE_RESOLVE)],
            counts.resolution_nodes
        );
        assert_eq!(
            counts.stage_entries[usize::from(PROFILE_STAGE_BASE_FEATURES)],
            2
        );
        assert!(counts.stage_entries[usize::from(PROFILE_STAGE_PLACEMENT)] > 0);
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
