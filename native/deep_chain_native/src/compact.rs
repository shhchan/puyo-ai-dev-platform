//! Allocation-free scalar compact-state transition kernel.
//!
//! The canonical wire representation remains the 87-byte `CSK1` payload
//! defined by `agents.compact_search.CompactSearchState`.  The hot transition
//! path works only with fixed-size values.  Optional trace capture is owned by
//! the QA batch boundary and is never required by native evaluator/search
//! callers.

use std::fmt;
use std::hint::black_box;
use std::mem::MaybeUninit;
use std::time::Instant;

pub(crate) const WIDTH: usize = 6;
pub(crate) const HEIGHT: usize = 14;
pub(crate) const VISIBLE_HEIGHT: usize = 12;
pub(crate) const PLANE_COUNT: usize = 6;
pub(crate) const NORMAL_COLOR_COUNT: usize = 5;
pub(crate) const ACTION_COUNT: usize = 22;
pub(crate) const STATE_BYTES: usize = 87;
pub(crate) const PLANE_BYTES: usize = 11;
pub(crate) const HOT_RESULT_ABI_VERSION: u16 = 1;
pub(crate) const HOT_RESULT_SCHEMA: &str = "puyo.native_compact_hot_result.v1";
pub(crate) const HOT_CHILD_STATE_BYTES: usize = 80;
pub(crate) const HOT_RESULT_BYTES: usize = 24;
pub(crate) const HOT_RESULT_FLAGS_MASK: u8 = 0x0f;
const COLOR_BIT_COUNT: usize = 3;
const COLUMN_LANE_BITS: usize = 16;
const WIRE_BOARD_MASK: u128 = (1_u128 << (WIDTH * HEIGHT)) - 1;
pub(crate) const BOARD_MASK: u128 = lane_mask(HEIGHT);
const VISIBLE_MASK: u128 = lane_mask(VISIBLE_HEIGHT);
const ROW_14_MASK: u128 = row_mask(HEIGHT - 1);
const CELL_FINGERPRINTS: [[[u64; 2]; WIDTH * HEIGHT]; PLANE_COUNT] = fingerprint_table();
const ALL_CLEAR_BONUS_SCORE: u64 = 2_100;
const HOT_FLAG_VALID: u8 = 0x01;
const HOT_FLAG_GAME_OVER: u8 = 0x02;
const HOT_FLAG_ALL_CLEAR_ACHIEVED: u8 = 0x04;
const HOT_FLAG_ALL_CLEAR_BONUS_CONSUMED: u8 = 0x08;

pub(crate) const PROFILE_MODE_BASELINE: u16 = 0;
pub(crate) const PROFILE_MODE_FULL_TRANSITION: u16 = 1;
pub(crate) const PROFILE_MODE_DIRECT_PLACEMENT: u16 = 2;
pub(crate) const PROFILE_MODE_COLOR_PLANE_EXTRACTION: u16 = 3;
pub(crate) const PROFILE_MODE_INSERTED_CONNECTIVITY: u16 = 4;
pub(crate) const PROFILE_MODE_STATE_RESULT_MATERIALIZATION: u16 = 5;
pub(crate) const PROFILE_MODE_CHAIN_SCAN: u16 = 6;
pub(crate) const PROFILE_MODE_GRAVITY: u16 = 7;
pub(crate) const PROFILE_MODE_SCORE_LIFECYCLE: u16 = 8;
pub(crate) const PROFILE_MODE_LAYOUT_THREE_BIT: u16 = 101;
pub(crate) const PROFILE_MODE_LAYOUT_SIX_PLANE: u16 = 102;
pub(crate) const PROFILE_MODE_LAYOUT_COLUMN_LOCAL: u16 = 103;
pub(crate) const PROFILE_MODE_LAYOUT_LOCAL_METADATA: u16 = 104;
pub(crate) const PROFILE_MODE_RESULT_FULL_SUMMARY: u16 = 201;
pub(crate) const PROFILE_MODE_RESULT_MINIMAL_HOT: u16 = 202;
pub(crate) const PROFILE_MODE_RESULT_HOT_METADATA: u16 = 203;

const CHAIN_BONUS: [u16; 20] = [
    0, 0, 8, 16, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, 480, 512,
];
const COLOR_BONUS: [u16; 6] = [0, 0, 3, 6, 12, 24];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum CompactErrorKind {
    InvalidInput,
    ArithmeticOverflow,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct CompactError {
    pub(crate) kind: CompactErrorKind,
    pub(crate) message: String,
}

impl CompactError {
    fn invalid(message: impl Into<String>) -> Self {
        Self {
            kind: CompactErrorKind::InvalidInput,
            message: message.into(),
        }
    }

    fn overflow(message: impl Into<String>) -> Self {
        Self {
            kind: CompactErrorKind::ArithmeticOverflow,
            message: message.into(),
        }
    }
}

impl fmt::Display for CompactError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

type CompactResult<T> = Result<T, CompactError>;

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum Color {
    Red = 1,
    Blue = 2,
    Green = 3,
    Yellow = 4,
    Purple = 5,
}

impl TryFrom<u8> for Color {
    type Error = CompactError;

    fn try_from(value: u8) -> Result<Self, Self::Error> {
        match value {
            1 => Ok(Self::Red),
            2 => Ok(Self::Blue),
            3 => Ok(Self::Green),
            4 => Ok(Self::Yellow),
            5 => Ok(Self::Purple),
            _ => Err(CompactError::invalid("pair contains an invalid color ID")),
        }
    }
}

impl Color {
    const fn plane_index(self) -> usize {
        self as usize - 1
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct Pair {
    pub(crate) axis: Color,
    pub(crate) child: Color,
}

impl Pair {
    pub(crate) fn from_ids(axis: u8, child: u8) -> CompactResult<Self> {
        Ok(Self {
            axis: Color::try_from(axis)?,
            child: Color::try_from(child)?,
        })
    }
}

#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Direction {
    Up,
    Right,
    Down,
    Left,
}

impl Direction {
    const fn offset(self) -> (i8, i8) {
        match self {
            Self::Up => (0, 1),
            Self::Right => (1, 0),
            Self::Down => (0, -1),
            Self::Left => (-1, 0),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct PlacementAction {
    axis_x: u8,
    direction: Direction,
}

const ACTIONS: [PlacementAction; ACTION_COUNT] = [
    PlacementAction {
        axis_x: 0,
        direction: Direction::Up,
    },
    PlacementAction {
        axis_x: 0,
        direction: Direction::Right,
    },
    PlacementAction {
        axis_x: 0,
        direction: Direction::Down,
    },
    PlacementAction {
        axis_x: 1,
        direction: Direction::Up,
    },
    PlacementAction {
        axis_x: 1,
        direction: Direction::Right,
    },
    PlacementAction {
        axis_x: 1,
        direction: Direction::Down,
    },
    PlacementAction {
        axis_x: 1,
        direction: Direction::Left,
    },
    PlacementAction {
        axis_x: 2,
        direction: Direction::Up,
    },
    PlacementAction {
        axis_x: 2,
        direction: Direction::Right,
    },
    PlacementAction {
        axis_x: 2,
        direction: Direction::Down,
    },
    PlacementAction {
        axis_x: 2,
        direction: Direction::Left,
    },
    PlacementAction {
        axis_x: 3,
        direction: Direction::Up,
    },
    PlacementAction {
        axis_x: 3,
        direction: Direction::Right,
    },
    PlacementAction {
        axis_x: 3,
        direction: Direction::Down,
    },
    PlacementAction {
        axis_x: 3,
        direction: Direction::Left,
    },
    PlacementAction {
        axis_x: 4,
        direction: Direction::Up,
    },
    PlacementAction {
        axis_x: 4,
        direction: Direction::Right,
    },
    PlacementAction {
        axis_x: 4,
        direction: Direction::Down,
    },
    PlacementAction {
        axis_x: 4,
        direction: Direction::Left,
    },
    PlacementAction {
        axis_x: 5,
        direction: Direction::Up,
    },
    PlacementAction {
        axis_x: 5,
        direction: Direction::Down,
    },
    PlacementAction {
        axis_x: 5,
        direction: Direction::Left,
    },
];

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) struct BoardKey {
    color_bits: [u128; COLOR_BIT_COUNT],
}

impl BoardKey {
    #[allow(dead_code)]
    pub(crate) const fn color_bits(&self) -> &[u128; COLOR_BIT_COUNT] {
        &self.color_bits
    }
}

/// Complete native search identity.  Search coordinates deliberately cannot
/// be constructed from a board fingerprint alone.
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) struct SearchStateKey {
    board: BoardKey,
    all_clear_bonus_pending: bool,
    game_over: bool,
    score: u64,
    last_chain_end_score: u64,
    root_action: u8,
    scenario_id: u8,
    pair_cursor: u16,
    depth: u16,
}

#[allow(dead_code)]
impl SearchStateKey {
    pub(crate) fn new(
        state: &CompactState,
        root_action: u8,
        scenario_id: u8,
        pair_cursor: u16,
        depth: u16,
    ) -> CompactResult<Self> {
        if usize::from(root_action) >= ACTION_COUNT {
            return Err(CompactError::invalid(
                "root action is outside the v1 layout",
            ));
        }
        Ok(Self {
            board: state.board_key(),
            all_clear_bonus_pending: state.all_clear_bonus_pending,
            game_over: state.game_over,
            score: state.score,
            last_chain_end_score: state.last_chain_end_score,
            root_action,
            scenario_id,
            pair_cursor,
            depth,
        })
    }
}

#[repr(C, align(16))]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct CompactState {
    color_bits: [u128; COLOR_BIT_COUNT],
    drop_heights: [u8; WIDTH],
    lower_compact: bool,
    settled: bool,
    all_clear_bonus_pending: bool,
    game_over: bool,
    score: u64,
    last_chain_end_score: u64,
}

impl CompactState {
    pub(crate) fn from_bytes(data: &[u8]) -> CompactResult<Self> {
        if data.len() != STATE_BYTES || data.get(..4) != Some(b"CSK1") {
            return Err(CompactError::invalid("invalid compact state framing"));
        }
        let mut planes = [0_u128; PLANE_COUNT];
        let mut offset = 4;
        for plane in &mut planes {
            let mut bytes = [0_u8; 16];
            bytes[..PLANE_BYTES].copy_from_slice(&data[offset..offset + PLANE_BYTES]);
            *plane = u128::from_le_bytes(bytes);
            offset += PLANE_BYTES;
        }
        let flags = data[offset];
        offset += 1;
        if flags & !0x3 != 0 {
            return Err(CompactError::invalid(
                "compact state contains unknown lifecycle flags",
            ));
        }
        let score = u64::from_le_bytes(
            data[offset..offset + 8]
                .try_into()
                .expect("validated state score slice"),
        );
        offset += 8;
        let last_chain_end_score = u64::from_le_bytes(
            data[offset..offset + 8]
                .try_into()
                .expect("validated state lifecycle slice"),
        );
        Self::from_parts(
            planes,
            flags & 0x1 != 0,
            flags & 0x2 != 0,
            score,
            last_chain_end_score,
        )
    }

    pub(crate) fn from_parts(
        planes: [u128; PLANE_COUNT],
        all_clear_bonus_pending: bool,
        game_over: bool,
        score: u64,
        last_chain_end_score: u64,
    ) -> CompactResult<Self> {
        let mut occupied = 0_u128;
        for plane in planes {
            if plane & !WIRE_BOARD_MASK != 0 {
                return Err(CompactError::invalid(
                    "compact plane contains a cell outside the 6x14 board",
                ));
            }
            if occupied & plane != 0 {
                return Err(CompactError::invalid("compact color planes overlap"));
            }
            occupied |= plane;
        }
        if last_chain_end_score > score {
            return Err(CompactError::invalid(
                "last chain end score exceeds total score",
            ));
        }
        let color_bits = color_bits_from_wire_planes(&planes);
        let internal_occupied = occupied_from_color_bits(&color_bits);
        let (drop_heights, lower_compact) = board_geometry(internal_occupied);
        let settled = find_vanish(&color_bits).vanished_mask == 0;
        Ok(Self {
            color_bits,
            drop_heights,
            lower_compact,
            settled,
            all_clear_bonus_pending,
            game_over,
            score,
            last_chain_end_score,
        })
    }

    pub(crate) fn to_bytes(self) -> [u8; STATE_BYTES] {
        let mut result = [0_u8; STATE_BYTES];
        result[..4].copy_from_slice(b"CSK1");
        let mut offset = 4;
        for plane_index in 0..PLANE_COUNT {
            let plane = internal_plane_to_wire(color_plane(&self.color_bits, plane_index));
            let bytes = plane.to_le_bytes();
            result[offset..offset + PLANE_BYTES].copy_from_slice(&bytes[..PLANE_BYTES]);
            offset += PLANE_BYTES;
        }
        result[offset] = u8::from(self.all_clear_bonus_pending) | (u8::from(self.game_over) << 1);
        offset += 1;
        result[offset..offset + 8].copy_from_slice(&self.score.to_le_bytes());
        offset += 8;
        result[offset..offset + 8].copy_from_slice(&self.last_chain_end_score.to_le_bytes());
        result
    }

    #[allow(dead_code)]
    pub(crate) fn wire_planes(&self) -> [u128; PLANE_COUNT] {
        wire_planes_from_color_bits(&self.color_bits)
    }

    pub(crate) fn occupied(&self) -> u128 {
        internal_plane_to_wire(self.internal_occupied())
    }

    pub(crate) fn internal_occupied(&self) -> u128 {
        occupied_from_color_bits(&self.color_bits)
    }

    /// Fixed six-plane view shared with the native structural evaluator.
    ///
    /// The transition kernel keeps its compact three-bit representation as
    /// the source of truth.  Materializing these six registers once at the
    /// evaluator boundary avoids wire conversion and preserves the 80-byte
    /// child-state ABI.
    pub(crate) fn evaluator_planes(&self) -> [u128; PLANE_COUNT] {
        std::array::from_fn(|index| color_plane(&self.color_bits, index))
    }

    pub(crate) fn board_fingerprint(&self) -> [u64; 2] {
        fingerprint_color_bits(&self.color_bits)
    }

    pub(crate) fn column_heights(&self) -> [u8; WIDTH] {
        let occupied = self.internal_occupied();
        let mut heights = self.drop_heights;
        for (x, height) in heights.iter_mut().enumerate() {
            if occupied & cell_bit(x, HEIGHT - 1) != 0 {
                *height = HEIGHT as u8;
            }
        }
        heights
    }

    pub(crate) const fn board_key(&self) -> BoardKey {
        BoardKey {
            color_bits: self.color_bits,
        }
    }

    pub(crate) const fn all_clear_bonus_pending(&self) -> bool {
        self.all_clear_bonus_pending
    }

    pub(crate) const fn game_over(&self) -> bool {
        self.game_over
    }

    #[allow(dead_code)]
    pub(crate) const fn score(&self) -> u64 {
        self.score
    }

    #[allow(dead_code)]
    pub(crate) const fn last_chain_end_score(&self) -> u64 {
        self.last_chain_end_score
    }

    fn from_settled_color_bits(
        color_bits: [u128; COLOR_BIT_COUNT],
        all_clear_bonus_pending: bool,
        game_over: bool,
        score: u64,
        last_chain_end_score: u64,
    ) -> Self {
        debug_assert!(last_chain_end_score <= score);
        let (drop_heights, lower_compact) = board_geometry(occupied_from_color_bits(&color_bits));
        Self {
            color_bits,
            drop_heights,
            lower_compact,
            settled: true,
            all_clear_bonus_pending,
            game_over,
            score,
            last_chain_end_score,
        }
    }
}

const _: () = assert!(std::mem::size_of::<CompactState>() == HOT_CHILD_STATE_BYTES);

/// Versioned fixed-width result consumed by the native search/evaluator loop.
/// Persistent lifecycle state belongs to the caller-owned `CompactState`;
/// these flags describe only the transition event and duplicate game-over for
/// branch-local access.  QA-only planes, fingerprints, and trace evidence are
/// deliberately absent.
#[repr(C)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct TransitionHotResult {
    pub(crate) score_delta: u64,
    pub(crate) attack_score_delta: u64,
    pub(crate) vanished_count: u16,
    pub(crate) garbage_cleared_count: u16,
    pub(crate) action_id: u8,
    pub(crate) axis_y: u8,
    pub(crate) chain_count: u8,
    pub(crate) flags: u8,
}

impl TransitionHotResult {
    #[allow(clippy::too_many_arguments)]
    fn new(
        state: &CompactState,
        action_id: u8,
        axis_y: Option<u8>,
        score_delta: u64,
        attack_score_delta: u64,
        chain_count: u8,
        vanished_count: u16,
        garbage_cleared_count: u16,
        valid: bool,
        all_clear_achieved: bool,
        all_clear_bonus_consumed: bool,
    ) -> Self {
        Self {
            score_delta,
            attack_score_delta,
            vanished_count,
            garbage_cleared_count,
            action_id,
            axis_y: axis_y.unwrap_or(u8::MAX),
            chain_count,
            flags: (u8::from(valid) * HOT_FLAG_VALID)
                | (u8::from(state.game_over) * HOT_FLAG_GAME_OVER)
                | (u8::from(all_clear_achieved) * HOT_FLAG_ALL_CLEAR_ACHIEVED)
                | (u8::from(all_clear_bonus_consumed) * HOT_FLAG_ALL_CLEAR_BONUS_CONSUMED),
        }
    }

    pub(crate) const fn valid(self) -> bool {
        self.flags & HOT_FLAG_VALID != 0
    }

    pub(crate) const fn game_over(self) -> bool {
        self.flags & HOT_FLAG_GAME_OVER != 0
    }

    pub(crate) const fn all_clear_achieved(self) -> bool {
        self.flags & HOT_FLAG_ALL_CLEAR_ACHIEVED != 0
    }

    pub(crate) const fn all_clear_bonus_consumed(self) -> bool {
        self.flags & HOT_FLAG_ALL_CLEAR_BONUS_CONSUMED != 0
    }

    pub(crate) const fn axis_y(self) -> Option<u8> {
        if self.axis_y == u8::MAX {
            None
        } else {
            Some(self.axis_y)
        }
    }
}

const _: () = assert!(std::mem::size_of::<TransitionHotResult>() == HOT_RESULT_BYTES);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct TransitionSummary {
    pub(crate) state: CompactState,
    pub(crate) action_id: u8,
    pub(crate) valid: bool,
    pub(crate) axis_y: Option<u8>,
    pub(crate) score_delta: u64,
    pub(crate) attack_score_delta: u64,
    pub(crate) chain_count: u8,
    pub(crate) vanished_count: u16,
    pub(crate) garbage_cleared_count: u16,
    pub(crate) all_clear_achieved: bool,
    pub(crate) all_clear_bonus_consumed: bool,
    pub(crate) all_clear_bonus_score: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct ChainStepTrace {
    pub(crate) chain_index: u8,
    pub(crate) vanished_count: u8,
    pub(crate) garbage_cleared_count: u8,
    pub(crate) base: u16,
    pub(crate) bonus: u16,
    pub(crate) score: u64,
    pub(crate) all_clear_bonus_score: u64,
    pub(crate) board_planes: [u128; PLANE_COUNT],
    pub(crate) vanished_mask: u128,
    pub(crate) garbage_mask: u128,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(crate) struct TransitionTrace {
    pub(crate) placement_planes: Option<[u128; PLANE_COUNT]>,
    pub(crate) chains: Vec<ChainStepTrace>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct VanishInfo {
    vanished_mask: u128,
    vanished_count: u8,
    connection_bonus: u16,
    color_count: u8,
}

#[derive(Clone, Copy)]
struct DirectPlacement {
    color_bits: [u128; COLOR_BIT_COUNT],
    occupied: u128,
    drop_heights: [u8; WIDTH],
    inserted_indices: [u8; 2],
}

#[derive(Clone, Copy)]
struct PlacementMetadata {
    landing_y: u8,
    inserted_indices: [u8; 2],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct OutcomeSignature {
    state: CompactState,
    chain_count: u8,
    score_delta: u64,
    garbage_cleared_count: u16,
    game_over: bool,
}

#[inline]
const fn cell_bit(x: usize, y: usize) -> u128 {
    1_u128 << (x * COLUMN_LANE_BITS + y)
}

#[inline]
const fn wire_cell_bit(x: usize, y: usize) -> u128 {
    1_u128 << (y * WIDTH + x)
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

#[inline]
fn occupied_from_color_bits(color_bits: &[u128; COLOR_BIT_COUNT]) -> u128 {
    color_bits[0] | color_bits[1] | color_bits[2]
}

#[inline]
fn color_plane(color_bits: &[u128; COLOR_BIT_COUNT], plane_index: usize) -> u128 {
    let low = color_bits[0];
    let middle = color_bits[1];
    let high = color_bits[2];
    (match plane_index {
        0 => low & !middle & !high,
        1 => !low & middle & !high,
        2 => low & middle & !high,
        3 => !low & !middle & high,
        4 => low & !middle & high,
        5 => !low & middle & high,
        _ => 0,
    }) & BOARD_MASK
}

#[inline]
fn set_color_bit(color_bits: &mut [u128; COLOR_BIT_COUNT], bit: u128, color_id: usize) {
    debug_assert!((1..=PLANE_COUNT).contains(&color_id));
    for (index, slice) in color_bits.iter_mut().enumerate() {
        if color_id & (1 << index) != 0 {
            *slice |= bit;
        }
    }
}

fn color_bits_from_wire_planes(planes: &[u128; PLANE_COUNT]) -> [u128; COLOR_BIT_COUNT] {
    let mut result = [0_u128; COLOR_BIT_COUNT];
    for (plane_index, plane) in planes.iter().copied().enumerate() {
        let mut remaining = plane;
        while remaining != 0 {
            let index = remaining.trailing_zeros() as usize;
            let x = index % WIDTH;
            let y = index / WIDTH;
            set_color_bit(&mut result, cell_bit(x, y), plane_index + 1);
            remaining &= remaining - 1;
        }
    }
    result
}

fn internal_plane_to_wire(plane: u128) -> u128 {
    let mut result = 0_u128;
    let mut remaining = plane;
    while remaining != 0 {
        let index = remaining.trailing_zeros() as usize;
        let x = index / COLUMN_LANE_BITS;
        let y = index % COLUMN_LANE_BITS;
        debug_assert!(x < WIDTH && y < HEIGHT);
        result |= wire_cell_bit(x, y);
        remaining &= remaining - 1;
    }
    result
}

fn wire_planes_from_color_bits(color_bits: &[u128; COLOR_BIT_COUNT]) -> [u128; PLANE_COUNT] {
    std::array::from_fn(|index| internal_plane_to_wire(color_plane(color_bits, index)))
}

fn board_geometry(occupied: u128) -> ([u8; WIDTH], bool) {
    let mut drop_heights = [0_u8; WIDTH];
    let mut lower_compact = true;
    for (x, height) in drop_heights.iter_mut().enumerate() {
        let mut seen_empty = false;
        for y in 0..(HEIGHT - 1) {
            if occupied & cell_bit(x, y) != 0 {
                *height += 1;
                if seen_empty {
                    lower_compact = false;
                }
            } else {
                seen_empty = true;
            }
        }
    }
    (drop_heights, lower_compact)
}

#[inline]
const fn splitmix64(value: u64) -> u64 {
    let mut mixed = value.wrapping_add(0x9e37_79b9_7f4a_7c15);
    mixed = (mixed ^ (mixed >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    mixed = (mixed ^ (mixed >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    mixed ^ (mixed >> 31)
}

const fn fingerprint_table() -> [[[u64; 2]; WIDTH * HEIGHT]; PLANE_COUNT] {
    let mut result = [[[0_u64; 2]; WIDTH * HEIGHT]; PLANE_COUNT];
    let mut plane_index = 0_usize;
    while plane_index < PLANE_COUNT {
        let mut bit_index = 0_usize;
        while bit_index < WIDTH * HEIGHT {
            let identity = (plane_index * WIDTH * HEIGHT + bit_index + 1) as u64;
            result[plane_index][bit_index] = [
                splitmix64(0x243f_6a88_85a3_08d3 ^ identity),
                splitmix64(0x1319_8a2e_0370_7344 ^ identity.rotate_left(29)),
            ];
            bit_index += 1;
        }
        plane_index += 1;
    }
    result
}

fn fingerprint_color_bits(color_bits: &[u128; COLOR_BIT_COUNT]) -> [u64; 2] {
    let mut result = [0_u64; 2];
    for plane_index in 0..PLANE_COUNT {
        let plane = color_plane(color_bits, plane_index);
        let mut remaining = plane;
        while remaining != 0 {
            let internal_index = remaining.trailing_zeros() as usize;
            let x = internal_index / COLUMN_LANE_BITS;
            let y = internal_index % COLUMN_LANE_BITS;
            let contribution = cell_fingerprint(plane_index, y * WIDTH + x);
            result[0] ^= contribution[0];
            result[1] ^= contribution[1];
            remaining &= remaining - 1;
        }
    }
    result
}

#[inline]
fn cell_fingerprint(plane_index: usize, bit_index: usize) -> [u64; 2] {
    CELL_FINGERPRINTS[plane_index][bit_index]
}

fn placement(action_id: u8) -> CompactResult<PlacementAction> {
    ACTIONS
        .get(usize::from(action_id))
        .copied()
        .ok_or_else(|| CompactError::invalid("action ID is outside the v1 layout"))
}

#[inline]
fn can_place_pair(occupied: u128, axis_x: i8, axis_y: i8, direction: Direction) -> bool {
    if axis_x < 0 || axis_x >= WIDTH as i8 || axis_y < 0 || axis_y >= HEIGHT as i8 {
        return false;
    }
    let axis_bit = cell_bit(axis_x as usize, axis_y as usize);
    if occupied & axis_bit != 0 {
        return false;
    }
    let (offset_x, offset_y) = direction.offset();
    let child_x = axis_x + offset_x;
    let child_y = axis_y + offset_y;
    if child_x < 0 || child_x >= WIDTH as i8 || child_y < 0 || child_y >= HEIGHT as i8 {
        return false;
    }
    occupied & cell_bit(child_x as usize, child_y as usize) == 0
}

fn find_landing_y(state: &CompactState, action: PlacementAction) -> Option<u8> {
    if state.game_over {
        return None;
    }
    let axis_x = action.axis_x as i8;
    let mut landing_y = 12_i8;
    let occupied = state.internal_occupied();
    if !can_place_pair(occupied, axis_x, landing_y, action.direction) {
        return None;
    }
    while can_place_pair(occupied, axis_x, landing_y - 1, action.direction) {
        landing_y -= 1;
    }
    Some(landing_y as u8)
}

pub(crate) fn legal_actions_mask(state: &CompactState) -> u32 {
    if state.game_over {
        return 0;
    }
    ACTIONS
        .iter()
        .enumerate()
        .fold(0_u32, |mask, (index, action)| {
            if find_landing_y(state, *action).is_some() {
                mask | (1_u32 << index)
            } else {
                mask
            }
        })
}

pub(crate) fn symmetry_reduced_actions_mask(
    state: &CompactState,
    pair: Pair,
) -> CompactResult<u32> {
    let legal = legal_actions_mask(state);
    if pair.axis != pair.child {
        return Ok(legal);
    }
    let mut selected = [None; ACTION_COUNT];
    let mut selected_count = 0_usize;
    let mut result_mask = 0_u32;
    for action_id in 0..ACTION_COUNT {
        if legal & (1_u32 << action_id) == 0 {
            continue;
        }
        let outcome = transition(
            state,
            pair,
            u8::try_from(action_id).expect("action ID fits u8"),
            None,
        )?;
        let signature = OutcomeSignature {
            state: outcome.state,
            chain_count: outcome.chain_count,
            score_delta: outcome.score_delta,
            garbage_cleared_count: outcome.garbage_cleared_count,
            game_over: outcome.state.game_over,
        };
        if selected[..selected_count]
            .iter()
            .flatten()
            .any(|existing| *existing == signature)
        {
            continue;
        }
        selected[selected_count] = Some(signature);
        selected_count += 1;
        result_mask |= 1_u32 << action_id;
    }
    Ok(result_mask)
}

#[inline]
fn set_plane_cell(
    color_bits: &mut [u128; COLOR_BIT_COUNT],
    occupied: &mut u128,
    x: usize,
    y: usize,
    color: Color,
) -> CompactResult<()> {
    let bit = cell_bit(x, y);
    if *occupied & bit != 0 {
        return Err(CompactError::invalid(
            "cannot place a compact puyo into an occupied cell",
        ));
    }
    set_color_bit(color_bits, bit, color.plane_index() + 1);
    *occupied |= bit;
    Ok(())
}

#[inline]
fn add_direct_cell(
    color_bits: &mut [u128; COLOR_BIT_COUNT],
    occupied: &mut u128,
    x: usize,
    y: usize,
    color: Color,
) {
    let bit = cell_bit(x, y);
    debug_assert_eq!(*occupied & bit, 0);
    set_color_bit(color_bits, bit, color.plane_index() + 1);
    *occupied |= bit;
}

#[inline(always)]
fn place_reachable_state(
    state: &CompactState,
    pair: Pair,
    action: PlacementAction,
    child: &mut CompactState,
) -> Option<PlacementMetadata> {
    if state.game_over || !state.lower_compact {
        return None;
    }
    let axis_x = usize::from(action.axis_x);
    let mut occupied = state.internal_occupied();
    *child = *state;
    let landing_y;
    let inserted;
    match action.direction {
        Direction::Up => {
            let height = usize::from(child.drop_heights[axis_x]);
            if height > HEIGHT - 2
                || (height == HEIGHT - 2 && occupied & cell_bit(axis_x, HEIGHT - 1) != 0)
            {
                return None;
            }
            landing_y = height as u8;
            add_direct_cell(
                &mut child.color_bits,
                &mut occupied,
                axis_x,
                height,
                pair.axis,
            );
            add_direct_cell(
                &mut child.color_bits,
                &mut occupied,
                axis_x,
                height + 1,
                pair.child,
            );
            inserted = [
                (axis_x * COLUMN_LANE_BITS + height) as u8,
                (axis_x * COLUMN_LANE_BITS + height + 1) as u8,
            ];
            child.drop_heights[axis_x] += 1;
            if height + 1 < HEIGHT - 1 {
                child.drop_heights[axis_x] += 1;
            }
        }
        Direction::Down => {
            let height = usize::from(child.drop_heights[axis_x]);
            if height > HEIGHT - 3 {
                return None;
            }
            landing_y = (height + 1) as u8;
            add_direct_cell(
                &mut child.color_bits,
                &mut occupied,
                axis_x,
                height,
                pair.child,
            );
            add_direct_cell(
                &mut child.color_bits,
                &mut occupied,
                axis_x,
                height + 1,
                pair.axis,
            );
            inserted = [
                (axis_x * COLUMN_LANE_BITS + height + 1) as u8,
                (axis_x * COLUMN_LANE_BITS + height) as u8,
            ];
            child.drop_heights[axis_x] += 2;
        }
        Direction::Right | Direction::Left => {
            let child_x = if action.direction == Direction::Right {
                axis_x + 1
            } else {
                axis_x - 1
            };
            let axis_height = usize::from(child.drop_heights[axis_x]);
            let child_height = usize::from(child.drop_heights[child_x]);
            if axis_height > HEIGHT - 2 || child_height > HEIGHT - 2 {
                return None;
            }
            landing_y = axis_height.max(child_height) as u8;
            add_direct_cell(
                &mut child.color_bits,
                &mut occupied,
                axis_x,
                axis_height,
                pair.axis,
            );
            add_direct_cell(
                &mut child.color_bits,
                &mut occupied,
                child_x,
                child_height,
                pair.child,
            );
            inserted = [
                (axis_x * COLUMN_LANE_BITS + axis_height) as u8,
                (child_x * COLUMN_LANE_BITS + child_height) as u8,
            ];
            child.drop_heights[axis_x] += 1;
            child.drop_heights[child_x] += 1;
        }
    }
    child.lower_compact = true;
    child.settled = true;
    child.game_over = occupied & cell_bit(2, VISIBLE_HEIGHT - 1) != 0;
    Some(PlacementMetadata {
        landing_y,
        inserted_indices: inserted,
    })
}

/// QA/profile compatibility view. The production path mutates its
/// caller-owned child state directly through `place_reachable_state`.
fn direct_placement(
    state: &CompactState,
    pair: Pair,
    action: PlacementAction,
) -> Option<DirectPlacement> {
    let mut child = *state;
    let metadata = place_reachable_state(state, pair, action, &mut child)?;
    Some(DirectPlacement {
        color_bits: child.color_bits,
        occupied: child.internal_occupied(),
        drop_heights: child.drop_heights,
        inserted_indices: metadata.inserted_indices,
    })
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

fn apply_gravity(color_bits: &[u128; COLOR_BIT_COUNT]) -> [u128; COLOR_BIT_COUNT] {
    let mut result = std::array::from_fn(|index| color_bits[index] & ROW_14_MASK);
    let occupied = occupied_from_color_bits(color_bits);
    for x in 0..WIDTH {
        let shift = x * COLUMN_LANE_BITS;
        let occupied_column = ((occupied >> shift) as u16) & 0x1fff;
        for (index, slice) in color_bits.iter().copied().enumerate() {
            let column = ((slice >> shift) as u16) & 0x1fff;
            result[index] |= u128::from(parallel_extract_13(column, occupied_column)) << shift;
        }
    }
    result
}

#[inline(always)]
fn visible_neighbors(mask: u128) -> u128 {
    ((mask >> COLUMN_LANE_BITS) | (mask << COLUMN_LANE_BITS) | (mask >> 1) | (mask << 1))
        & VISIBLE_MASK
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

#[inline]
fn has_at_least_four_bits(value: u128) -> bool {
    let without_one = value & value.wrapping_sub(1);
    let without_two = without_one & without_one.wrapping_sub(1);
    let without_three = without_two & without_two.wrapping_sub(1);
    without_three != 0
}

#[inline]
fn shift_west(mask: u128) -> u128 {
    mask >> COLUMN_LANE_BITS
}

#[inline]
fn shift_east(mask: u128) -> u128 {
    (mask << COLUMN_LANE_BITS) & VISIBLE_MASK
}

#[inline]
fn shift_down(mask: u128) -> u128 {
    mask >> 1
}

#[inline]
fn shift_up(mask: u128) -> u128 {
    (mask << 1) & VISIBLE_MASK
}

/// Return cells belonging to a visible four-connected component of size at
/// least four.  The degree pattern is an allocation-free scalar translation
/// of the standard bitboard connectivity identity; component flood-fill is
/// only needed after this filter reports a candidate.
#[inline]
fn poppable_mask(plane: u128) -> u128 {
    let visible = plane & VISIBLE_MASK;
    let west = shift_west(visible) & visible;
    let east = shift_east(visible) & visible;
    let down = shift_down(visible) & visible;
    let up = shift_up(visible) & visible;
    let vertical_both = up & down;
    let horizontal_both = west & east;
    let vertical_either = up | down;
    let horizontal_either = west | east;
    let degree_three = (vertical_both & horizontal_either) | (horizontal_both & vertical_either);
    let degree_two = vertical_both | horizontal_both | (vertical_either & horizontal_either);
    let connected_degree_two = (shift_west(degree_two) & degree_two)
        | (shift_east(degree_two) & degree_two)
        | (shift_down(degree_two) & degree_two)
        | (shift_up(degree_two) & degree_two);
    let seeds = degree_three | connected_degree_two;
    (seeds | visible_neighbors(seeds)) & visible
}

fn find_vanish(color_bits: &[u128; COLOR_BIT_COUNT]) -> VanishInfo {
    let mut vanished_mask = 0_u128;
    let mut vanished_count = 0_u8;
    let mut total_connection_bonus = 0_u16;
    let mut color_count = 0_u8;
    for plane_index in 0..NORMAL_COLOR_COUNT {
        let visible_plane = color_plane(color_bits, plane_index) & VISIBLE_MASK;
        let mut remaining = poppable_mask(visible_plane);
        if remaining == 0 {
            continue;
        }
        let mut color_vanished = false;
        while remaining != 0 {
            let seed = 1_u128 << remaining.trailing_zeros();
            let mut group = seed;
            loop {
                let expanded = group | (visible_neighbors(group) & visible_plane);
                if expanded == group {
                    break;
                }
                group = expanded;
            }
            remaining &= !group;
            let count = group.count_ones();
            if count < 4 {
                continue;
            }
            vanished_mask |= group;
            vanished_count += u8::try_from(count).expect("visible board count fits u8");
            total_connection_bonus += connection_bonus(count);
            color_vanished = true;
        }
        color_count += u8::from(color_vanished);
    }
    VanishInfo {
        vanished_mask,
        vanished_count,
        connection_bonus: total_connection_bonus,
        color_count,
    }
}

/// Stable compact states cannot gain a new group except through one of the
/// two cells placed by the current action.  Restricting the first-chain scan
/// to those components preserves arbitrary-state semantics through the full
/// scanner while making the normal reachable-state path constant work. Three
/// fixed expansions are sufficient to reject a component smaller than four;
/// complete convergence runs only for an actual pop candidate.
#[inline(always)]
fn find_inserted_vanish_from_planes(
    pair: Pair,
    inserted_indices: [u8; 2],
    inserted_planes: [u128; 2],
) -> VanishInfo {
    let mut vanished_mask = 0_u128;
    let mut vanished_count = 0_u8;
    let mut total_connection_bonus = 0_u16;
    let mut vanished_colors = 0_u8;
    let mut checked_mask = 0_u128;
    let plane_indices = [pair.axis.plane_index(), pair.child.plane_index()];
    for index in 0..2 {
        let plane_index = plane_indices[index];
        let seed = 1_u128 << inserted_indices[index];
        if seed & VISIBLE_MASK == 0 || checked_mask & seed != 0 {
            continue;
        }
        let visible_plane = inserted_planes[index] & VISIBLE_MASK;
        let mut group = seed;
        for _ in 0..3 {
            let expanded = group | (visible_neighbors(group) & visible_plane);
            if expanded == group {
                break;
            }
            group = expanded;
        }
        checked_mask |= group;
        if !has_at_least_four_bits(group) {
            continue;
        }
        loop {
            let expanded = group | (visible_neighbors(group) & visible_plane);
            if expanded == group {
                break;
            }
            group = expanded;
        }
        checked_mask |= group;
        let count = group.count_ones();
        vanished_mask |= group;
        vanished_count += u8::try_from(count).expect("visible board count fits u8");
        total_connection_bonus += connection_bonus(count);
        vanished_colors |= 1_u8 << plane_index;
    }
    VanishInfo {
        vanished_mask,
        vanished_count,
        connection_bonus: total_connection_bonus,
        color_count: vanished_colors.count_ones() as u8,
    }
}

#[inline(always)]
fn inserted_color_planes(state: &CompactState, pair: Pair, inserted_indices: [u8; 2]) -> [u128; 2] {
    let axis_bit = 1_u128 << inserted_indices[0];
    let child_bit = 1_u128 << inserted_indices[1];
    let axis_plane = color_plane(&state.color_bits, pair.axis.plane_index());
    let child_plane = if pair.axis == pair.child {
        axis_plane
    } else {
        color_plane(&state.color_bits, pair.child.plane_index())
    };
    let axis_inserted_plane = axis_plane
        | axis_bit
        | if pair.axis == pair.child {
            child_bit
        } else {
            0
        };
    [axis_inserted_plane, child_plane | child_bit]
}

#[inline(always)]
fn find_inserted_vanish(state: &CompactState, pair: Pair, inserted_indices: [u8; 2]) -> VanishInfo {
    find_inserted_vanish_from_planes(
        pair,
        inserted_indices,
        inserted_color_planes(state, pair, inserted_indices),
    )
}

#[cfg(test)]
fn reference_inserted_vanish(
    color_bits: &[u128; COLOR_BIT_COUNT],
    pair: Pair,
    inserted_indices: [u8; 2],
) -> VanishInfo {
    let mut vanished_mask = 0_u128;
    let mut vanished_count = 0_u8;
    let mut total_connection_bonus = 0_u16;
    let mut vanished_colors = 0_u8;
    let inserted = [
        (pair.axis.plane_index(), inserted_indices[0]),
        (pair.child.plane_index(), inserted_indices[1]),
    ];
    for (plane_index, bit_index) in inserted {
        let seed = 1_u128 << bit_index;
        if seed & VISIBLE_MASK == 0 || vanished_mask & seed != 0 {
            continue;
        }
        let visible_plane = color_plane(color_bits, plane_index) & VISIBLE_MASK;
        let mut group = seed;
        loop {
            let expanded = group | (visible_neighbors(group) & visible_plane);
            if expanded == group {
                break;
            }
            group = expanded;
        }
        if vanished_mask & group != 0 {
            continue;
        }
        let count = group.count_ones();
        if count < 4 {
            continue;
        }
        vanished_mask |= group;
        vanished_count += u8::try_from(count).expect("visible board count fits u8");
        total_connection_bonus += connection_bonus(count);
        vanished_colors |= 1_u8 << plane_index;
    }
    VanishInfo {
        vanished_mask,
        vanished_count,
        connection_bonus: total_connection_bonus,
        color_count: vanished_colors.count_ones() as u8,
    }
}

fn clear_and_drop(
    color_bits: &[u128; COLOR_BIT_COUNT],
    vanished_mask: u128,
    garbage_mask: u128,
) -> [u128; COLOR_BIT_COUNT] {
    let cleared_mask = vanished_mask | garbage_mask;
    let mut cleared = *color_bits;
    for slice in &mut cleared {
        *slice &= !cleared_mask;
    }
    apply_gravity(&cleared)
}

pub(crate) fn transition(
    state: &CompactState,
    pair: Pair,
    action_id: u8,
    trace: Option<&mut TransitionTrace>,
) -> CompactResult<TransitionSummary> {
    let mut output = MaybeUninit::uninit();
    transition_into(state, pair, action_id, trace, &mut output)?;
    // SAFETY: `transition_into` returns `Ok` only after writing one summary.
    Ok(unsafe { output.assume_init() })
}

/// Primary search transition. The caller owns both fixed-width output slots;
/// no QA summary, trace, wire plane, or fingerprint is materialized here.
#[inline]
pub(crate) fn transition_hot_into(
    state: &CompactState,
    pair: Pair,
    action_id: u8,
    child_output: &mut MaybeUninit<CompactState>,
    hot_output: &mut MaybeUninit<TransitionHotResult>,
) -> CompactResult<()> {
    transition_hot_core::<false>(state, pair, action_id, None, child_output, hot_output)
}

#[allow(dead_code)]
pub(crate) fn transition_hot(
    state: &CompactState,
    pair: Pair,
    action_id: u8,
) -> CompactResult<(CompactState, TransitionHotResult)> {
    let mut child = MaybeUninit::uninit();
    let mut hot = MaybeUninit::uninit();
    transition_hot_into(state, pair, action_id, &mut child, &mut hot)?;
    // SAFETY: `transition_hot_into` initializes both slots before success.
    Ok(unsafe { (child.assume_init(), hot.assume_init()) })
}

pub(crate) fn transition_into(
    state: &CompactState,
    pair: Pair,
    action_id: u8,
    trace: Option<&mut TransitionTrace>,
    output: &mut MaybeUninit<TransitionSummary>,
) -> CompactResult<()> {
    let mut child = MaybeUninit::uninit();
    let mut hot = MaybeUninit::uninit();
    if let Some(trace) = trace {
        transition_hot_core::<true>(state, pair, action_id, Some(trace), &mut child, &mut hot)?;
    } else {
        transition_hot_into(state, pair, action_id, &mut child, &mut hot)?;
    }
    // SAFETY: either successful core path initialized both output slots.
    let child = unsafe { child.assume_init() };
    // SAFETY: either successful core path initialized both output slots.
    let hot = unsafe { hot.assume_init() };
    output.write(materialize_transition_summary(child, hot));
    Ok(())
}

#[inline(always)]
fn transition_hot_core<const TRACE: bool>(
    state: &CompactState,
    pair: Pair,
    action_id: u8,
    mut trace: Option<&mut TransitionTrace>,
    child_output: &mut MaybeUninit<CompactState>,
    hot_output: &mut MaybeUninit<TransitionHotResult>,
) -> CompactResult<()> {
    let action = placement(action_id)?;
    if state.lower_compact && state.settled {
        let mut child = *state;
        let Some(metadata) = place_reachable_state(state, pair, action, &mut child) else {
            write_invalid_hot(state, action_id, child_output, hot_output);
            return Ok(());
        };
        if TRACE {
            trace
                .as_deref_mut()
                .expect("trace-enabled transition has a sink")
                .placement_planes = Some(wire_planes_from_color_bits(&child.color_bits));
        }
        let vanish = find_inserted_vanish(state, pair, metadata.inserted_indices);
        if vanish.vanished_mask == 0 {
            write_quiet_hot(
                child,
                action_id,
                metadata.landing_y,
                child_output,
                hot_output,
            );
            return Ok(());
        }
        return resolve_chains_hot::<TRACE>(
            state,
            action_id,
            metadata.landing_y,
            child.color_bits,
            vanish,
            trace,
            child_output,
            hot_output,
        );
    }
    transition_general_hot::<TRACE>(
        state,
        pair,
        action_id,
        action,
        trace,
        child_output,
        hot_output,
    )
}

#[inline(always)]
fn write_invalid_hot(
    state: &CompactState,
    action_id: u8,
    child_output: &mut MaybeUninit<CompactState>,
    hot_output: &mut MaybeUninit<TransitionHotResult>,
) {
    child_output.write(*state);
    hot_output.write(TransitionHotResult::new(
        state, action_id, None, 0, 0, 0, 0, 0, false, false, false,
    ));
}

#[inline(always)]
fn write_quiet_hot(
    child: CompactState,
    action_id: u8,
    landing_y: u8,
    child_output: &mut MaybeUninit<CompactState>,
    hot_output: &mut MaybeUninit<TransitionHotResult>,
) {
    child_output.write(child);
    hot_output.write(TransitionHotResult::new(
        &child,
        action_id,
        Some(landing_y),
        0,
        0,
        0,
        0,
        0,
        true,
        false,
        false,
    ));
}

fn materialize_transition_summary(
    state: CompactState,
    hot: TransitionHotResult,
) -> TransitionSummary {
    debug_assert_eq!(state.game_over, hot.game_over());
    debug_assert_eq!(hot.flags & !HOT_RESULT_FLAGS_MASK, 0);
    TransitionSummary {
        state,
        action_id: hot.action_id,
        valid: hot.valid(),
        axis_y: hot.axis_y(),
        score_delta: hot.score_delta,
        attack_score_delta: hot.attack_score_delta,
        chain_count: hot.chain_count,
        vanished_count: hot.vanished_count,
        garbage_cleared_count: hot.garbage_cleared_count,
        all_clear_achieved: hot.all_clear_achieved(),
        all_clear_bonus_consumed: hot.all_clear_bonus_consumed(),
        all_clear_bonus_score: if hot.all_clear_bonus_consumed() {
            ALL_CLEAR_BONUS_SCORE
        } else {
            0
        },
    }
}

#[cold]
#[inline(never)]
fn transition_general_hot<const TRACE: bool>(
    state: &CompactState,
    pair: Pair,
    action_id: u8,
    action: PlacementAction,
    mut trace: Option<&mut TransitionTrace>,
    child_output: &mut MaybeUninit<CompactState>,
    hot_output: &mut MaybeUninit<TransitionHotResult>,
) -> CompactResult<()> {
    let mut direct_child = None;
    let (current_color_bits, landing_y) = if state.lower_compact {
        let mut child = *state;
        let Some(metadata) = place_reachable_state(state, pair, action, &mut child) else {
            write_invalid_hot(state, action_id, child_output, hot_output);
            return Ok(());
        };
        direct_child = Some(child);
        (child.color_bits, metadata.landing_y)
    } else {
        let Some(landing_y) = find_landing_y(state, action) else {
            write_invalid_hot(state, action_id, child_output, hot_output);
            return Ok(());
        };
        let mut color_bits = state.color_bits;
        let mut occupied = state.internal_occupied();
        set_plane_cell(
            &mut color_bits,
            &mut occupied,
            usize::from(action.axis_x),
            usize::from(landing_y),
            pair.axis,
        )?;
        let (offset_x, offset_y) = action.direction.offset();
        let child_x = (action.axis_x as i8 + offset_x) as usize;
        let child_y = (landing_y as i8 + offset_y) as usize;
        set_plane_cell(&mut color_bits, &mut occupied, child_x, child_y, pair.child)?;
        (apply_gravity(&color_bits), landing_y)
    };
    if TRACE {
        trace
            .as_deref_mut()
            .expect("trace-enabled transition has a sink")
            .placement_planes = Some(wire_planes_from_color_bits(&current_color_bits));
    }
    let vanish = find_vanish(&current_color_bits);
    if vanish.vanished_mask == 0 {
        let final_occupied = occupied_from_color_bits(&current_color_bits);
        let game_over = final_occupied & cell_bit(2, VISIBLE_HEIGHT - 1) != 0;
        let child = if let Some(mut child) = direct_child {
            child.game_over = game_over;
            child
        } else {
            CompactState::from_settled_color_bits(
                current_color_bits,
                state.all_clear_bonus_pending,
                game_over,
                state.score,
                state.last_chain_end_score,
            )
        };
        write_quiet_hot(child, action_id, landing_y, child_output, hot_output);
        return Ok(());
    }
    resolve_chains_hot::<TRACE>(
        state,
        action_id,
        landing_y,
        current_color_bits,
        vanish,
        trace,
        child_output,
        hot_output,
    )
}

#[cold]
#[inline(never)]
#[allow(clippy::too_many_arguments)]
fn resolve_chains_hot<const TRACE: bool>(
    state: &CompactState,
    action_id: u8,
    landing_y: u8,
    mut current_color_bits: [u128; COLOR_BIT_COUNT],
    mut vanish: VanishInfo,
    mut trace: Option<&mut TransitionTrace>,
    child_output: &mut MaybeUninit<CompactState>,
    hot_output: &mut MaybeUninit<TransitionHotResult>,
) -> CompactResult<()> {
    let mut pending = state.all_clear_bonus_pending;
    let mut all_clear_bonus_consumed = false;
    let mut score = state.score;
    let mut chain_count = 0_u8;
    let mut vanished_total = 0_u16;
    let mut garbage_total = 0_u16;

    loop {
        chain_count = chain_count
            .checked_add(1)
            .ok_or_else(|| CompactError::overflow("chain count overflow"))?;
        let garbage_mask = visible_neighbors(vanish.vanished_mask)
            & color_plane(&current_color_bits, PLANE_COUNT - 1)
            & VISIBLE_MASK;
        let garbage_count =
            u8::try_from(garbage_mask.count_ones()).expect("visible garbage count fits u8");
        let chain_bonus = CHAIN_BONUS[usize::from(chain_count).min(CHAIN_BONUS.len() - 1)];
        let color_bonus = COLOR_BONUS[usize::from(vanish.color_count)];
        let bonus = (chain_bonus + vanish.connection_bonus + color_bonus).max(1);
        let base = u16::from(vanish.vanished_count) * 10;
        let step_all_clear_bonus = if chain_count == 1 && pending {
            pending = false;
            all_clear_bonus_consumed = true;
            ALL_CLEAR_BONUS_SCORE
        } else {
            0
        };
        let step_score = u64::from(base)
            .checked_mul(u64::from(bonus))
            .and_then(|value| value.checked_add(step_all_clear_bonus))
            .ok_or_else(|| CompactError::overflow("chain step score overflow"))?;
        score = score
            .checked_add(step_score)
            .ok_or_else(|| CompactError::overflow("total score exceeds u64"))?;
        vanished_total = vanished_total
            .checked_add(u16::from(vanish.vanished_count))
            .ok_or_else(|| CompactError::overflow("vanished count overflow"))?;
        garbage_total = garbage_total
            .checked_add(u16::from(garbage_count))
            .ok_or_else(|| CompactError::overflow("garbage count overflow"))?;
        if TRACE {
            trace
                .as_deref_mut()
                .expect("trace-enabled transition has a sink")
                .chains
                .push(ChainStepTrace {
                    chain_index: chain_count,
                    vanished_count: vanish.vanished_count,
                    garbage_cleared_count: garbage_count,
                    base,
                    bonus,
                    score: step_score,
                    all_clear_bonus_score: step_all_clear_bonus,
                    board_planes: wire_planes_from_color_bits(&current_color_bits),
                    vanished_mask: internal_plane_to_wire(vanish.vanished_mask),
                    garbage_mask: internal_plane_to_wire(garbage_mask),
                });
        }
        current_color_bits =
            clear_and_drop(&current_color_bits, vanish.vanished_mask, garbage_mask);
        vanish = find_vanish(&current_color_bits);
        if vanish.vanished_mask == 0 {
            break;
        }
    }

    let final_occupied = occupied_from_color_bits(&current_color_bits);
    let all_clear_achieved = chain_count > 0 && final_occupied == 0;
    if all_clear_achieved {
        pending = true;
    }
    let mut last_chain_end_score = state.last_chain_end_score;
    let attack_score_delta = if chain_count > 0 {
        let delta = score
            .checked_sub(last_chain_end_score)
            .ok_or_else(|| CompactError::overflow("attack score lifecycle underflow"))?;
        last_chain_end_score = score;
        delta
    } else {
        0
    };
    let game_over = final_occupied & cell_bit(2, VISIBLE_HEIGHT - 1) != 0;
    let child = CompactState::from_settled_color_bits(
        current_color_bits,
        pending,
        game_over,
        score,
        last_chain_end_score,
    );
    child_output.write(child);
    hot_output.write(TransitionHotResult::new(
        &child,
        action_id,
        Some(landing_y),
        score - state.score,
        attack_score_delta,
        chain_count,
        vanished_total,
        garbage_total,
        true,
        all_clear_achieved,
        all_clear_bonus_consumed,
    ));
    Ok(())
}

/// QA-only measurement returned by the PUYO-205 profile boundary.  The
/// production transition/search path never constructs this value.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct CompactProfileMeasurement {
    pub(crate) mode: u16,
    pub(crate) flags: u16,
    pub(crate) record_count: u32,
    pub(crate) repeats: u32,
    pub(crate) operations: u64,
    pub(crate) elapsed_ns: u64,
    pub(crate) cycles: u64,
    pub(crate) checksum: u64,
    pub(crate) mismatch_count: u32,
    pub(crate) state_bytes: u32,
    pub(crate) result_bytes: u32,
    pub(crate) copy_bytes_per_record: u32,
    pub(crate) update_bytes_per_record: u32,
    pub(crate) reusable_metadata_bytes: u32,
}

#[repr(C, align(16))]
#[derive(Clone, Copy)]
struct SixPlaneProfileState {
    planes: [u128; PLANE_COUNT],
    drop_heights: [u8; WIDTH],
    all_clear_bonus_pending: bool,
    game_over: bool,
    score: u64,
    last_chain_end_score: u64,
}

impl SixPlaneProfileState {
    fn from_state(state: &CompactState) -> Self {
        Self {
            planes: std::array::from_fn(|index| color_plane(&state.color_bits, index)),
            drop_heights: state.drop_heights,
            all_clear_bonus_pending: state.all_clear_bonus_pending,
            game_over: state.game_over,
            score: state.score,
            last_chain_end_score: state.last_chain_end_score,
        }
    }
}

#[repr(C, align(8))]
#[derive(Clone, Copy)]
struct ColumnLocalProfileState {
    columns: [u64; WIDTH],
    drop_heights: [u8; WIDTH],
    all_clear_bonus_pending: bool,
    game_over: bool,
    score: u64,
    last_chain_end_score: u64,
}

impl ColumnLocalProfileState {
    fn from_state(state: &CompactState) -> Self {
        let mut columns = [0_u64; WIDTH];
        for plane_index in 0..PLANE_COUNT {
            let mut remaining = color_plane(&state.color_bits, plane_index);
            while remaining != 0 {
                let index = remaining.trailing_zeros() as usize;
                let x = index / COLUMN_LANE_BITS;
                let y = index % COLUMN_LANE_BITS;
                columns[x] |= ((plane_index + 1) as u64) << (y * COLOR_BIT_COUNT);
                remaining &= remaining - 1;
            }
        }
        Self {
            columns,
            drop_heights: state.drop_heights,
            all_clear_bonus_pending: state.all_clear_bonus_pending,
            game_over: state.game_over,
            score: state.score,
            last_chain_end_score: state.last_chain_end_score,
        }
    }

    fn color_bits(&self) -> [u128; COLOR_BIT_COUNT] {
        let mut result = [0_u128; COLOR_BIT_COUNT];
        for x in 0..WIDTH {
            for y in 0..HEIGHT {
                let color = ((self.columns[x] >> (y * COLOR_BIT_COUNT)) & 0x7) as usize;
                if color != 0 {
                    set_color_bit(&mut result, cell_bit(x, y), color);
                }
            }
        }
        result
    }
}

#[repr(C, align(16))]
#[derive(Clone, Copy)]
struct LocalMetadataProfileState {
    core: CompactState,
    occupied: u128,
    normal_planes: [u128; NORMAL_COLOR_COUNT],
    trigger_masks: [u128; NORMAL_COLOR_COUNT],
}

impl LocalMetadataProfileState {
    fn from_state(state: &CompactState) -> Self {
        let normal_planes = std::array::from_fn(|index| color_plane(&state.color_bits, index));
        let trigger_masks = std::array::from_fn(|index| poppable_mask(normal_planes[index]));
        Self {
            core: *state,
            occupied: state.internal_occupied(),
            normal_planes,
            trigger_masks,
        }
    }
}

#[repr(C, align(16))]
#[derive(Clone, Copy)]
struct MetadataHotProfileResult {
    hot: TransitionHotResult,
    occupied: u128,
    inserted_component_mask: u128,
}

#[derive(Clone, Copy)]
struct CompactProfileRecord {
    state: CompactState,
    pair: Pair,
    action_id: u8,
    action: PlacementAction,
    direct: DirectPlacement,
    inserted_planes: [u128; 2],
    first_vanish: VanishInfo,
    final_color_bits: [u128; COLOR_BIT_COUNT],
    summary: TransitionSummary,
    six_plane: SixPlaneProfileState,
    column_local: ColumnLocalProfileState,
    local_metadata: LocalMetadataProfileState,
}

#[derive(Clone, Copy)]
struct GravityProfileInput {
    color_bits: [u128; COLOR_BIT_COUNT],
    vanished_mask: u128,
    garbage_mask: u128,
}

#[derive(Clone, Copy)]
struct ScoreProfileInput {
    chain_index: u8,
    vanish: VanishInfo,
    garbage_count: u8,
    pending: bool,
    score: u64,
}

struct CompactProfileWorkload {
    records: Vec<CompactProfileRecord>,
    chain_scan_inputs: Vec<[u128; COLOR_BIT_COUNT]>,
    gravity_inputs: Vec<GravityProfileInput>,
    score_inputs: Vec<ScoreProfileInput>,
    layout_mismatches: [u32; 4],
    semantic_mismatches: u32,
}

fn preextracted_inserted_vanish(
    pair: Pair,
    inserted_indices: [u8; 2],
    inserted_planes: [u128; 2],
) -> VanishInfo {
    find_inserted_vanish_from_planes(pair, inserted_indices, inserted_planes)
}

fn score_profile_step(input: ScoreProfileInput) -> (u64, bool, u64) {
    let chain_bonus = CHAIN_BONUS[usize::from(input.chain_index).min(CHAIN_BONUS.len() - 1)];
    let color_bonus = COLOR_BONUS[usize::from(input.vanish.color_count)];
    let bonus = (chain_bonus + input.vanish.connection_bonus + color_bonus).max(1);
    let base = u16::from(input.vanish.vanished_count) * 10;
    let all_clear_bonus = if input.chain_index == 1 && input.pending {
        ALL_CLEAR_BONUS_SCORE
    } else {
        0
    };
    let step_score = u64::from(base)
        .checked_mul(u64::from(bonus))
        .and_then(|value| value.checked_add(all_clear_bonus))
        .expect("profile corpus score fits u64");
    let score = input
        .score
        .checked_add(step_score)
        .expect("profile corpus total score fits u64");
    black_box(input.garbage_count);
    (
        score,
        input.pending && input.chain_index != 1,
        all_clear_bonus,
    )
}

fn profile_checksum(value: u128) -> u64 {
    value as u64 ^ (value >> 64) as u64
}

fn update_three_bit_layout(record: &CompactProfileRecord) -> CompactState {
    let mut result = record.state;
    let colors = [record.pair.axis, record.pair.child];
    for (index, color) in colors.into_iter().enumerate() {
        let bit = 1_u128 << record.direct.inserted_indices[index];
        set_color_bit(&mut result.color_bits, bit, color.plane_index() + 1);
    }
    result.drop_heights = record.direct.drop_heights;
    result.lower_compact = true;
    result.settled = record.first_vanish.vanished_mask == 0;
    result
}

fn update_six_plane_layout(record: &CompactProfileRecord) -> SixPlaneProfileState {
    let mut result = record.six_plane;
    let colors = [record.pair.axis, record.pair.child];
    for (index, color) in colors.into_iter().enumerate() {
        result.planes[color.plane_index()] |= 1_u128 << record.direct.inserted_indices[index];
    }
    result.drop_heights = record.direct.drop_heights;
    result
}

fn update_column_local_layout(record: &CompactProfileRecord) -> ColumnLocalProfileState {
    let mut result = record.column_local;
    let colors = [record.pair.axis, record.pair.child];
    for (index, color) in colors.into_iter().enumerate() {
        let bit_index = usize::from(record.direct.inserted_indices[index]);
        let x = bit_index / COLUMN_LANE_BITS;
        let y = bit_index % COLUMN_LANE_BITS;
        result.columns[x] |= (color as u64) << (y * COLOR_BIT_COUNT);
    }
    result.drop_heights = record.direct.drop_heights;
    result
}

fn update_local_metadata_layout(record: &CompactProfileRecord) -> LocalMetadataProfileState {
    let mut result = record.local_metadata;
    let colors = [record.pair.axis, record.pair.child];
    let mut touched = 0_u8;
    for (index, color) in colors.into_iter().enumerate() {
        let bit = 1_u128 << record.direct.inserted_indices[index];
        let plane_index = color.plane_index();
        set_color_bit(&mut result.core.color_bits, bit, plane_index + 1);
        result.occupied |= bit;
        result.normal_planes[plane_index] |= bit;
        touched |= 1_u8 << plane_index;
    }
    for (index, plane) in result.normal_planes.iter().copied().enumerate() {
        if touched & (1_u8 << index) != 0 {
            result.trigger_masks[index] = poppable_mask(plane);
        }
    }
    result.core.drop_heights = record.direct.drop_heights;
    result.core.lower_compact = true;
    result.core.settled = record.first_vanish.vanished_mask == 0;
    result
}

fn hot_result_from_summary(summary: TransitionSummary) -> TransitionHotResult {
    TransitionHotResult::new(
        &summary.state,
        summary.action_id,
        summary.axis_y,
        summary.score_delta,
        summary.attack_score_delta,
        summary.chain_count,
        summary.vanished_count,
        summary.garbage_cleared_count,
        summary.valid,
        summary.all_clear_achieved,
        summary.all_clear_bonus_consumed,
    )
}

impl CompactProfileWorkload {
    fn new(inputs: &[(CompactState, Pair, u8)]) -> CompactResult<Self> {
        let mut records = Vec::with_capacity(inputs.len());
        let mut chain_scan_inputs = Vec::new();
        let mut gravity_inputs = Vec::new();
        let mut score_inputs = Vec::new();
        let mut semantic_mismatches = 0_u32;

        for &(state, pair, action_id) in inputs {
            let action = placement(action_id)?;
            let direct = direct_placement(&state, pair, action).ok_or_else(|| {
                CompactError::invalid("profile input is not a reachable direct placement")
            })?;
            let inserted_planes = inserted_color_planes(&state, pair, direct.inserted_indices);
            let first_vanish = find_inserted_vanish(&state, pair, direct.inserted_indices);
            if first_vanish
                != preextracted_inserted_vanish(pair, direct.inserted_indices, inserted_planes)
            {
                semantic_mismatches += 1;
            }
            let summary = transition(&state, pair, action_id, None)?;
            let mut current_color_bits = direct.color_bits;
            let mut vanish = first_vanish;
            let mut chain_index = 0_u8;
            let mut pending = state.all_clear_bonus_pending;
            let mut score = state.score;
            while vanish.vanished_mask != 0 {
                chain_index += 1;
                let garbage_mask = visible_neighbors(vanish.vanished_mask)
                    & color_plane(&current_color_bits, PLANE_COUNT - 1)
                    & VISIBLE_MASK;
                let garbage_count =
                    u8::try_from(garbage_mask.count_ones()).expect("visible garbage count fits u8");
                score_inputs.push(ScoreProfileInput {
                    chain_index,
                    vanish,
                    garbage_count,
                    pending,
                    score,
                });
                (score, pending, _) = score_profile_step(*score_inputs.last().expect("pushed"));
                gravity_inputs.push(GravityProfileInput {
                    color_bits: current_color_bits,
                    vanished_mask: vanish.vanished_mask,
                    garbage_mask,
                });
                current_color_bits =
                    clear_and_drop(&current_color_bits, vanish.vanished_mask, garbage_mask);
                chain_scan_inputs.push(current_color_bits);
                vanish = find_vanish(&current_color_bits);
            }
            if current_color_bits != summary.state.color_bits || chain_index != summary.chain_count
            {
                semantic_mismatches += 1;
            }
            records.push(CompactProfileRecord {
                state,
                pair,
                action_id,
                action,
                direct,
                inserted_planes,
                first_vanish,
                final_color_bits: current_color_bits,
                summary,
                six_plane: SixPlaneProfileState::from_state(&state),
                column_local: ColumnLocalProfileState::from_state(&state),
                local_metadata: LocalMetadataProfileState::from_state(&state),
            });
        }

        let mut layout_mismatches = [0_u32; 4];
        for record in &records {
            if update_three_bit_layout(record).color_bits != record.direct.color_bits {
                layout_mismatches[0] += 1;
            }
            let six = update_six_plane_layout(record);
            let six_bits = color_bits_from_wire_planes(&std::array::from_fn(|index| {
                internal_plane_to_wire(six.planes[index])
            }));
            if six_bits != record.direct.color_bits {
                layout_mismatches[1] += 1;
            }
            if update_column_local_layout(record).color_bits() != record.direct.color_bits {
                layout_mismatches[2] += 1;
            }
            let metadata = update_local_metadata_layout(record);
            if metadata.core.color_bits != record.direct.color_bits
                || metadata.occupied != record.direct.occupied
                || metadata
                    .normal_planes
                    .iter()
                    .copied()
                    .enumerate()
                    .any(|(index, plane)| plane != color_plane(&record.direct.color_bits, index))
            {
                layout_mismatches[3] += 1;
            }
        }
        Ok(Self {
            records,
            chain_scan_inputs,
            gravity_inputs,
            score_inputs,
            layout_mismatches,
            semantic_mismatches,
        })
    }
}

#[cfg(target_arch = "x86_64")]
fn profile_cycle_counter() -> u64 {
    // SAFETY: LFENCE/RDTSC are available on the supported x86_64 target and
    // only read the invariant timestamp counter.
    unsafe {
        std::arch::x86_64::_mm_lfence();
        let value = std::arch::x86_64::_rdtsc();
        std::arch::x86_64::_mm_lfence();
        value
    }
}

#[cfg(not(target_arch = "x86_64"))]
fn profile_cycle_counter() -> u64 {
    0
}

fn measured_profile_loop(
    workload: &CompactProfileWorkload,
    repeats: u32,
    mut operation: impl FnMut(&CompactProfileWorkload, &mut u64),
) -> (u64, u64, u64) {
    let started = Instant::now();
    let started_cycles = profile_cycle_counter();
    let mut checksum = 0_u64;
    for _ in 0..repeats {
        operation(workload, &mut checksum);
    }
    black_box(checksum);
    let cycles = profile_cycle_counter().wrapping_sub(started_cycles);
    let elapsed_ns = u64::try_from(started.elapsed().as_nanos()).unwrap_or(u64::MAX);
    (elapsed_ns, cycles, checksum)
}

pub(crate) fn profile_compact_records(
    inputs: &[(CompactState, Pair, u8)],
    mode: u16,
    repeats: u32,
) -> CompactResult<CompactProfileMeasurement> {
    if inputs.is_empty() || repeats == 0 || repeats > 10_000 {
        return Err(CompactError::invalid(
            "profile records/repeats are outside the supported range",
        ));
    }
    let workload = CompactProfileWorkload::new(inputs)?;
    let record_operations = (workload.records.len() as u64) * u64::from(repeats);
    let (operations, mismatch_count, state_bytes, result_bytes, copy_bytes, update_bytes, reuse) =
        match mode {
            PROFILE_MODE_BASELINE
            | PROFILE_MODE_FULL_TRANSITION
            | PROFILE_MODE_DIRECT_PLACEMENT
            | PROFILE_MODE_COLOR_PLANE_EXTRACTION
            | PROFILE_MODE_INSERTED_CONNECTIVITY
            | PROFILE_MODE_STATE_RESULT_MATERIALIZATION => (
                record_operations,
                workload.semantic_mismatches,
                0,
                0,
                0,
                0,
                0,
            ),
            PROFILE_MODE_CHAIN_SCAN => (
                (workload.chain_scan_inputs.len() as u64) * u64::from(repeats),
                workload.semantic_mismatches,
                0,
                0,
                0,
                0,
                0,
            ),
            PROFILE_MODE_GRAVITY => (
                (workload.gravity_inputs.len() as u64) * u64::from(repeats),
                workload.semantic_mismatches,
                0,
                0,
                0,
                0,
                0,
            ),
            PROFILE_MODE_SCORE_LIFECYCLE => (
                ((workload.score_inputs.len() + workload.records.len()) as u64)
                    * u64::from(repeats),
                workload.semantic_mismatches,
                0,
                0,
                0,
                0,
                0,
            ),
            PROFILE_MODE_LAYOUT_THREE_BIT => (
                record_operations,
                workload.layout_mismatches[0],
                std::mem::size_of::<CompactState>() as u32,
                0,
                std::mem::size_of::<CompactState>() as u32,
                54,
                8,
            ),
            PROFILE_MODE_LAYOUT_SIX_PLANE => (
                record_operations,
                workload.layout_mismatches[1],
                std::mem::size_of::<SixPlaneProfileState>() as u32,
                0,
                std::mem::size_of::<SixPlaneProfileState>() as u32,
                38,
                102,
            ),
            PROFILE_MODE_LAYOUT_COLUMN_LOCAL => (
                record_operations,
                workload.layout_mismatches[2],
                std::mem::size_of::<ColumnLocalProfileState>() as u32,
                0,
                std::mem::size_of::<ColumnLocalProfileState>() as u32,
                22,
                6,
            ),
            PROFILE_MODE_LAYOUT_LOCAL_METADATA => (
                record_operations,
                workload.layout_mismatches[3],
                std::mem::size_of::<LocalMetadataProfileState>() as u32,
                0,
                std::mem::size_of::<LocalMetadataProfileState>() as u32,
                134,
                182,
            ),
            PROFILE_MODE_RESULT_FULL_SUMMARY => (
                record_operations,
                workload.semantic_mismatches,
                0,
                std::mem::size_of::<TransitionSummary>() as u32,
                std::mem::size_of::<TransitionSummary>() as u32,
                0,
                0,
            ),
            PROFILE_MODE_RESULT_MINIMAL_HOT => (
                record_operations,
                workload.semantic_mismatches,
                std::mem::size_of::<CompactState>() as u32,
                std::mem::size_of::<TransitionHotResult>() as u32,
                (std::mem::size_of::<CompactState>() + std::mem::size_of::<TransitionHotResult>())
                    as u32,
                0,
                0,
            ),
            PROFILE_MODE_RESULT_HOT_METADATA => (
                record_operations,
                workload.semantic_mismatches,
                std::mem::size_of::<CompactState>() as u32,
                std::mem::size_of::<MetadataHotProfileResult>() as u32,
                (std::mem::size_of::<CompactState>()
                    + std::mem::size_of::<MetadataHotProfileResult>()) as u32,
                0,
                32,
            ),
            _ => return Err(CompactError::invalid("unknown compact profile mode")),
        };

    let (elapsed_ns, cycles, checksum) = match mode {
        PROFILE_MODE_BASELINE => measured_profile_loop(&workload, repeats, |value, checksum| {
            for record in &value.records {
                *checksum ^= u64::from(black_box(record.action_id));
            }
        }),
        PROFILE_MODE_FULL_TRANSITION => {
            measured_profile_loop(&workload, repeats, |value, checksum| {
                for record in &value.records {
                    let mut state_output = MaybeUninit::uninit();
                    let mut hot_output = MaybeUninit::uninit();
                    transition_hot_into(
                        black_box(&record.state),
                        record.pair,
                        record.action_id,
                        &mut state_output,
                        &mut hot_output,
                    )
                    .expect("validated profile transition succeeds");
                    // SAFETY: the successful transition initialized both output slots.
                    let child = unsafe { state_output.assume_init() };
                    // SAFETY: the successful transition initialized both output slots.
                    let hot = unsafe { hot_output.assume_init() };
                    *checksum ^= hot.score_delta
                        ^ u64::from(hot.chain_count)
                        ^ profile_checksum(child.color_bits[0]);
                    black_box((child, hot));
                }
            })
        }
        PROFILE_MODE_DIRECT_PLACEMENT => {
            measured_profile_loop(&workload, repeats, |value, checksum| {
                for record in &value.records {
                    let mut child = record.state;
                    let metadata = place_reachable_state(
                        black_box(&record.state),
                        record.pair,
                        record.action,
                        &mut child,
                    )
                    .expect("validated direct placement");
                    *checksum ^=
                        profile_checksum(child.color_bits[0]) ^ u64::from(metadata.landing_y);
                    black_box((child, metadata));
                }
            })
        }
        PROFILE_MODE_COLOR_PLANE_EXTRACTION => {
            measured_profile_loop(&workload, repeats, |value, checksum| {
                for record in &value.records {
                    let planes = inserted_color_planes(
                        black_box(&record.state),
                        record.pair,
                        record.direct.inserted_indices,
                    );
                    *checksum ^= profile_checksum(planes[0] ^ planes[1].rotate_left(17));
                    black_box(planes);
                }
            })
        }
        PROFILE_MODE_INSERTED_CONNECTIVITY => {
            measured_profile_loop(&workload, repeats, |value, checksum| {
                for record in &value.records {
                    let vanish = preextracted_inserted_vanish(
                        record.pair,
                        record.direct.inserted_indices,
                        black_box(record.inserted_planes),
                    );
                    *checksum ^=
                        profile_checksum(vanish.vanished_mask) ^ u64::from(vanish.vanished_count);
                    black_box(vanish);
                }
            })
        }
        PROFILE_MODE_STATE_RESULT_MATERIALIZATION => {
            measured_profile_loop(&workload, repeats, |value, checksum| {
                for record in &value.records {
                    let mut state_output = MaybeUninit::uninit();
                    let mut hot_output = MaybeUninit::uninit();
                    state_output.write(record.summary.state);
                    hot_output.write(hot_result_from_summary(record.summary));
                    // SAFETY: both output slots were initialized immediately above.
                    let child = unsafe { state_output.assume_init() };
                    // SAFETY: both output slots were initialized immediately above.
                    let hot = unsafe { hot_output.assume_init() };
                    *checksum ^= hot.score_delta
                        ^ u64::from(hot.chain_count)
                        ^ profile_checksum(child.color_bits[0]);
                    black_box((child, hot));
                }
            })
        }
        PROFILE_MODE_CHAIN_SCAN => measured_profile_loop(&workload, repeats, |value, checksum| {
            for color_bits in &value.chain_scan_inputs {
                let vanish = find_vanish(black_box(color_bits));
                *checksum ^= profile_checksum(vanish.vanished_mask);
                black_box(vanish);
            }
        }),
        PROFILE_MODE_GRAVITY => measured_profile_loop(&workload, repeats, |value, checksum| {
            for input in &value.gravity_inputs {
                let dropped = clear_and_drop(
                    black_box(&input.color_bits),
                    input.vanished_mask,
                    input.garbage_mask,
                );
                *checksum ^= profile_checksum(dropped[0]);
                black_box(dropped);
            }
        }),
        PROFILE_MODE_SCORE_LIFECYCLE => {
            measured_profile_loop(&workload, repeats, |value, checksum| {
                for input in &value.score_inputs {
                    let output = score_profile_step(black_box(*input));
                    *checksum ^= output.0 ^ output.2;
                    black_box(output);
                }
                for record in &value.records {
                    let occupied = occupied_from_color_bits(&record.final_color_bits);
                    let all_clear = record.summary.chain_count > 0 && occupied == 0;
                    let attack = if record.summary.chain_count > 0 {
                        record
                            .summary
                            .state
                            .score
                            .checked_sub(record.state.last_chain_end_score)
                            .expect("validated attack lifecycle")
                    } else {
                        0
                    };
                    *checksum ^= attack ^ u64::from(all_clear);
                    black_box((attack, all_clear));
                }
            })
        }
        PROFILE_MODE_LAYOUT_THREE_BIT => {
            measured_profile_loop(&workload, repeats, |value, checksum| {
                for record in &value.records {
                    let output = update_three_bit_layout(black_box(record));
                    *checksum ^= profile_checksum(output.color_bits[0]);
                    black_box(output);
                }
            })
        }
        PROFILE_MODE_LAYOUT_SIX_PLANE => {
            measured_profile_loop(&workload, repeats, |value, checksum| {
                for record in &value.records {
                    let output = update_six_plane_layout(black_box(record));
                    *checksum ^= profile_checksum(output.planes[0]);
                    black_box(output);
                }
            })
        }
        PROFILE_MODE_LAYOUT_COLUMN_LOCAL => {
            measured_profile_loop(&workload, repeats, |value, checksum| {
                for record in &value.records {
                    let output = update_column_local_layout(black_box(record));
                    *checksum ^= output.columns[0];
                    black_box(output);
                }
            })
        }
        PROFILE_MODE_LAYOUT_LOCAL_METADATA => {
            measured_profile_loop(&workload, repeats, |value, checksum| {
                for record in &value.records {
                    let output = update_local_metadata_layout(black_box(record));
                    *checksum ^= profile_checksum(output.occupied ^ output.trigger_masks[0]);
                    black_box(output);
                }
            })
        }
        PROFILE_MODE_RESULT_FULL_SUMMARY => {
            measured_profile_loop(&workload, repeats, |value, checksum| {
                for record in &value.records {
                    let mut output = MaybeUninit::uninit();
                    output.write(record.summary);
                    // SAFETY: the output slot was initialized immediately above.
                    let result = unsafe { output.assume_init() };
                    *checksum ^= result.score_delta ^ u64::from(result.chain_count);
                    black_box(result);
                }
            })
        }
        PROFILE_MODE_RESULT_MINIMAL_HOT => {
            measured_profile_loop(&workload, repeats, |value, checksum| {
                for record in &value.records {
                    let mut state_output = MaybeUninit::uninit();
                    let mut hot_output = MaybeUninit::uninit();
                    state_output.write(record.summary.state);
                    hot_output.write(hot_result_from_summary(record.summary));
                    // SAFETY: both output slots were initialized immediately above.
                    let state = unsafe { state_output.assume_init() };
                    // SAFETY: both output slots were initialized immediately above.
                    let hot = unsafe { hot_output.assume_init() };
                    *checksum ^= hot.score_delta ^ profile_checksum(state.color_bits[0]);
                    black_box((state, hot));
                }
            })
        }
        PROFILE_MODE_RESULT_HOT_METADATA => {
            measured_profile_loop(&workload, repeats, |value, checksum| {
                for record in &value.records {
                    let mut state_output = MaybeUninit::uninit();
                    let mut hot_output = MaybeUninit::uninit();
                    state_output.write(record.summary.state);
                    hot_output.write(MetadataHotProfileResult {
                        hot: hot_result_from_summary(record.summary),
                        occupied: record.summary.state.internal_occupied(),
                        inserted_component_mask: record.first_vanish.vanished_mask,
                    });
                    // SAFETY: both output slots were initialized immediately above.
                    let state = unsafe { state_output.assume_init() };
                    // SAFETY: both output slots were initialized immediately above.
                    let hot = unsafe { hot_output.assume_init() };
                    *checksum ^= hot.hot.score_delta
                        ^ profile_checksum(hot.occupied ^ hot.inserted_component_mask)
                        ^ profile_checksum(state.color_bits[0]);
                    black_box((state, hot));
                }
            })
        }
        _ => unreachable!("profile mode validated above"),
    };

    Ok(CompactProfileMeasurement {
        mode,
        flags: if cfg!(target_arch = "x86_64") { 0x1 } else { 0 },
        record_count: u32::try_from(workload.records.len())
            .expect("profile request record count fits u32"),
        repeats,
        operations,
        elapsed_ns,
        cycles,
        checksum,
        mismatch_count,
        state_bytes,
        result_bytes,
        copy_bytes_per_record: copy_bytes,
        update_bytes_per_record: update_bytes,
        reusable_metadata_bytes: reuse,
    })
}

pub(crate) fn planes_to_wire(planes: &[u128; PLANE_COUNT]) -> [u8; PLANE_COUNT * PLANE_BYTES] {
    let mut result = [0_u8; PLANE_COUNT * PLANE_BYTES];
    for (index, plane) in planes.iter().copied().enumerate() {
        let bytes = plane.to_le_bytes();
        let offset = index * PLANE_BYTES;
        result[offset..offset + PLANE_BYTES].copy_from_slice(&bytes[..PLANE_BYTES]);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::hint::black_box;
    use std::time::Instant;

    fn reference_poppable_mask(plane: u128) -> u128 {
        let visible = plane & VISIBLE_MASK;
        let mut remaining = visible;
        let mut result = 0_u128;
        while remaining != 0 {
            let mut group = 1_u128 << remaining.trailing_zeros();
            loop {
                let expanded = group | (visible_neighbors(group) & visible);
                if expanded == group {
                    break;
                }
                group = expanded;
            }
            remaining &= !group;
            if group.count_ones() >= 4 {
                result |= group;
            }
        }
        result
    }

    fn state_with_cells(cells: &[(usize, usize, usize)]) -> CompactState {
        let mut planes = [0_u128; PLANE_COUNT];
        for &(plane, x, y) in cells {
            planes[plane] |= wire_cell_bit(x, y);
        }
        CompactState::from_parts(planes, false, false, 0, 0).expect("valid test state")
    }

    #[test]
    fn state_round_trip_and_derived_values_include_hidden_ojama() {
        let hidden = state_with_cells(&[(0, 0, 13)]);
        let ojama = state_with_cells(&[(5, 0, 13)]);

        assert_eq!(CompactState::from_bytes(&hidden.to_bytes()), Ok(hidden));
        assert_eq!(hidden.column_heights(), [14, 0, 0, 0, 0, 0]);
        assert_ne!(hidden.board_key(), ojama.board_key());
        assert_ne!(hidden.board_fingerprint(), ojama.board_fingerprint());
    }

    #[test]
    fn poppable_prefilter_matches_reference_components() {
        let mut random = 0x9e37_79b9_7f4a_7c15_u64;
        for _ in 0..20_000 {
            random ^= random << 13;
            random ^= random >> 7;
            random ^= random << 17;
            let lower = u128::from(random);
            random = random.rotate_left(23).wrapping_add(0xa076_1d64_78bd_642f);
            let plane = (lower | (u128::from(random) << 64)) & VISIBLE_MASK;
            assert_eq!(poppable_mask(plane), reference_poppable_mask(plane));
        }
    }

    #[test]
    fn inserted_component_fast_path_matches_full_scanner() {
        let mut random = 0xd1b5_4a32_d192_ed03_u64;
        let mut checked = 0_usize;
        for _ in 0..300 {
            let mut planes = [0_u128; PLANE_COUNT];
            for x in 0..WIDTH {
                random ^= random << 13;
                random ^= random >> 7;
                random ^= random << 17;
                let height = (random as usize) % 7;
                for y in 0..height {
                    random = random.rotate_left(19).wrapping_add(0x9e37_79b9_7f4a_7c15);
                    let plane = (random as usize) % PLANE_COUNT;
                    planes[plane] |= wire_cell_bit(x, y);
                }
            }
            let state = CompactState::from_parts(planes, false, false, 0, 0)
                .expect("generated compact state is valid");
            if !state.settled {
                continue;
            }
            for axis in 1..=NORMAL_COLOR_COUNT as u8 {
                for child in 1..=NORMAL_COLOR_COUNT as u8 {
                    let pair = Pair::from_ids(axis, child).expect("generated pair is valid");
                    for (action_id, action) in ACTIONS.into_iter().enumerate() {
                        let Some(placed) = direct_placement(&state, pair, action) else {
                            continue;
                        };
                        assert_eq!(
                            find_inserted_vanish(&state, pair, placed.inserted_indices),
                            find_vanish(&placed.color_bits),
                        );
                        assert_eq!(
                            reference_inserted_vanish(
                                &placed.color_bits,
                                pair,
                                placed.inserted_indices,
                            ),
                            find_vanish(&placed.color_bits),
                        );
                        let (fast_child, fast_hot) = transition_hot(
                            &state,
                            pair,
                            u8::try_from(action_id).expect("action ID fits u8"),
                        )
                        .expect("reachable fast transition succeeds");
                        let mut scanner_state = state;
                        scanner_state.settled = false;
                        let (scanner_child, scanner_hot) = transition_hot(
                            &scanner_state,
                            pair,
                            u8::try_from(action_id).expect("action ID fits u8"),
                        )
                        .expect("full-scanner transition succeeds");
                        assert_eq!(fast_child, scanner_child);
                        assert_eq!(fast_hot, scanner_hot);
                        checked += 1;
                    }
                }
            }
        }
        assert!(
            checked > 100_000,
            "insufficient fast-path differential cases"
        );
    }

    #[test]
    fn empty_legal_and_equal_pair_reduction_preserve_action_ids() {
        let state = state_with_cells(&[]);
        let pair = Pair::from_ids(1, 1).expect("valid pair");

        assert_eq!(legal_actions_mask(&state), (1_u32 << ACTION_COUNT) - 1);
        assert_eq!(
            symmetry_reduced_actions_mask(&state, pair).expect("reduction succeeds"),
            [0, 1, 3, 4, 7, 8, 11, 12, 15, 16, 19]
                .into_iter()
                .fold(0, |mask, action| mask | (1_u32 << action))
        );
    }

    #[test]
    fn one_chain_all_clear_matches_python_lifecycle() {
        let state = state_with_cells(&[(0, 1, 0), (0, 1, 1)]);
        let pair = Pair::from_ids(1, 1).expect("valid pair");
        let mut trace = TransitionTrace::default();

        let result = transition(&state, pair, 7, Some(&mut trace)).expect("transition succeeds");

        assert!(result.valid);
        assert_eq!(result.axis_y, Some(0));
        assert_eq!(result.score_delta, 40);
        assert_eq!(result.attack_score_delta, 40);
        assert_eq!(result.chain_count, 1);
        assert_eq!(result.vanished_count, 4);
        assert!(result.all_clear_achieved);
        assert!(result.state.all_clear_bonus_pending());
        assert_eq!(trace.chains.len(), 1);
        assert_eq!(trace.chains[0].base, 40);
        assert_eq!(trace.chains[0].bonus, 1);
    }

    #[test]
    fn adjacent_ojama_is_cleared_without_scoring() {
        let state = state_with_cells(&[(0, 0, 0), (0, 1, 0), (0, 2, 0), (5, 2, 1)]);
        let pair = Pair::from_ids(1, 4).expect("valid pair");
        let result = transition(&state, pair, 12, None).expect("transition succeeds");

        assert_eq!(result.score_delta, 40);
        assert_eq!(result.vanished_count, 4);
        assert_eq!(result.garbage_cleared_count, 1);
        assert_eq!(result.state.wire_planes()[5], 0);
    }

    #[test]
    fn score_overflow_is_typed_and_does_not_wrap() {
        let mut planes = [0_u128; PLANE_COUNT];
        planes[0] = wire_cell_bit(1, 0) | wire_cell_bit(1, 1);
        let state = CompactState::from_parts(planes, false, false, u64::MAX - 39, 0)
            .expect("valid near-overflow state");
        let pair = Pair::from_ids(1, 1).expect("valid pair");

        let error = transition(&state, pair, 7, None).expect_err("overflow must fail");

        assert_eq!(error.kind, CompactErrorKind::ArithmeticOverflow);
    }

    #[test]
    fn normal_hot_transition_performs_no_heap_allocation() {
        let state = state_with_cells(&[
            (0, 0, 0),
            (1, 1, 0),
            (2, 2, 0),
            (3, 3, 0),
            (4, 4, 0),
            (5, 5, 0),
        ]);
        let pair = Pair::from_ids(1, 2).expect("valid pair");

        let (result, allocation_count) = crate::allocation_probe::count_allocations(|| {
            let mut child = MaybeUninit::uninit();
            let mut hot = MaybeUninit::uninit();
            transition_hot_into(&state, pair, 7, &mut child, &mut hot)?;
            // SAFETY: successful hot transitions initialize both output slots.
            Ok::<_, CompactError>(unsafe { (child.assume_init(), hot.assume_init()) })
        });

        let (child, hot) = result.expect("transition succeeds");
        assert!(hot.valid());
        assert_eq!(child.game_over(), hot.game_over());
        assert_eq!(allocation_count, 0);
    }

    #[test]
    fn fixed_hot_result_materializes_the_same_detailed_trace_summary() {
        let state = state_with_cells(&[(0, 1, 0), (0, 1, 1)]);
        let pair = Pair::from_ids(1, 1).expect("valid pair");
        let (child, hot) = transition_hot(&state, pair, 7).expect("hot transition succeeds");
        let mut trace = TransitionTrace::default();
        let detailed =
            transition(&state, pair, 7, Some(&mut trace)).expect("detailed transition succeeds");

        assert_eq!(HOT_RESULT_ABI_VERSION, 1);
        assert_eq!(HOT_RESULT_SCHEMA, "puyo.native_compact_hot_result.v1");
        assert_eq!(std::mem::size_of::<CompactState>(), HOT_CHILD_STATE_BYTES);
        assert_eq!(std::mem::size_of::<TransitionHotResult>(), HOT_RESULT_BYTES);
        assert_eq!(hot.flags & !HOT_RESULT_FLAGS_MASK, 0);
        assert_eq!(materialize_transition_summary(child, hot), detailed);
        assert_eq!(trace.chains.len(), 1);
    }

    #[test]
    fn search_key_requires_external_coordinates_and_exact_board() {
        let hidden = state_with_cells(&[(0, 0, 13)]);
        let ojama = state_with_cells(&[(5, 0, 13)]);
        let first = SearchStateKey::new(&hidden, 0, 1, 2, 3).expect("valid key");
        let second = SearchStateKey::new(&ojama, 0, 1, 2, 3).expect("valid key");
        let different_depth = SearchStateKey::new(&hidden, 0, 1, 2, 4).expect("valid key");

        assert_ne!(first, second);
        assert_ne!(first, different_depth);
        assert_ne!(first.board.color_bits(), second.board.color_bits());
    }

    #[test]
    fn qa_profile_modes_preserve_semantics_and_publish_fixed_sizes() {
        let state = state_with_cells(&[
            (0, 0, 0),
            (1, 1, 0),
            (2, 2, 0),
            (3, 3, 0),
            (4, 4, 0),
            (5, 5, 0),
        ]);
        let pair = Pair::from_ids(1, 2).expect("valid pair");
        let inputs = [(state, pair, 7)];
        for mode in [
            PROFILE_MODE_FULL_TRANSITION,
            PROFILE_MODE_DIRECT_PLACEMENT,
            PROFILE_MODE_COLOR_PLANE_EXTRACTION,
            PROFILE_MODE_INSERTED_CONNECTIVITY,
            PROFILE_MODE_STATE_RESULT_MATERIALIZATION,
            PROFILE_MODE_LAYOUT_THREE_BIT,
            PROFILE_MODE_LAYOUT_SIX_PLANE,
            PROFILE_MODE_LAYOUT_COLUMN_LOCAL,
            PROFILE_MODE_LAYOUT_LOCAL_METADATA,
            PROFILE_MODE_RESULT_FULL_SUMMARY,
            PROFILE_MODE_RESULT_MINIMAL_HOT,
            PROFILE_MODE_RESULT_HOT_METADATA,
        ] {
            let measurement =
                profile_compact_records(&inputs, mode, 3).expect("profile mode succeeds");
            assert_eq!(measurement.record_count, 1);
            assert_eq!(measurement.repeats, 3);
            assert_eq!(measurement.mismatch_count, 0);
            assert!(measurement.cycles > 0);
        }
        let full = profile_compact_records(&inputs, PROFILE_MODE_RESULT_FULL_SUMMARY, 1)
            .expect("full result profile succeeds");
        let minimal = profile_compact_records(&inputs, PROFILE_MODE_RESULT_MINIMAL_HOT, 1)
            .expect("minimal result profile succeeds");
        assert_eq!(full.result_bytes, 128);
        assert_eq!(minimal.state_bytes, 80);
        assert_eq!(minimal.result_bytes, 24);
        assert_eq!(minimal.copy_bytes_per_record, 104);
    }

    #[test]
    #[ignore = "manual release-only component profile"]
    fn profile_quiet_transition_components() {
        const ITERATIONS: u32 = 2_000_000;
        let state = state_with_cells(&[
            (0, 0, 0),
            (1, 1, 0),
            (2, 2, 0),
            (3, 3, 0),
            (4, 4, 0),
            (5, 5, 0),
        ]);
        let pair = Pair::from_ids(1, 2).expect("valid pair");
        let action = placement(7).expect("valid action");
        let direct = direct_placement(&state, pair, action).expect("valid placement");
        let mut direct_child = state;
        let direct_metadata = place_reachable_state(&state, pair, action, &mut direct_child)
            .expect("valid placement");

        let baseline_started = Instant::now();
        for index in 0..ITERATIONS {
            black_box(index);
        }
        let baseline = baseline_started.elapsed();

        let placement_started = Instant::now();
        for _ in 0..ITERATIONS {
            black_box(direct_placement(black_box(&state), pair, action));
        }
        let placement_duration = placement_started.elapsed();

        let vanish_started = Instant::now();
        for _ in 0..ITERATIONS {
            black_box(find_inserted_vanish(
                black_box(&state),
                pair,
                direct.inserted_indices,
            ));
        }
        let vanish_duration = vanish_started.elapsed();

        let combined_started = Instant::now();
        for _ in 0..ITERATIONS {
            let placed =
                direct_placement(black_box(&state), pair, action).expect("valid placement");
            black_box(find_inserted_vanish(&state, pair, placed.inserted_indices));
        }
        let combined_duration = combined_started.elapsed();

        let result_write_started = Instant::now();
        for _ in 0..ITERATIONS {
            let mut child_output = MaybeUninit::uninit();
            let mut hot_output = MaybeUninit::uninit();
            child_output.write(direct_child);
            hot_output.write(TransitionHotResult::new(
                &direct_child,
                7,
                Some(direct_metadata.landing_y),
                0,
                0,
                0,
                0,
                0,
                true,
                false,
                false,
            ));
            // SAFETY: both output slots were initialized immediately above.
            black_box(unsafe { (child_output.assume_init_ref(), hot_output.assume_init_ref()) });
        }
        let result_write_duration = result_write_started.elapsed();

        let manual_started = Instant::now();
        for _ in 0..ITERATIONS {
            let mut next = *black_box(&state);
            let metadata =
                place_reachable_state(&state, pair, action, &mut next).expect("valid placement");
            let vanish = find_inserted_vanish(&state, pair, metadata.inserted_indices);
            black_box(vanish.vanished_mask);
            black_box((
                next,
                TransitionHotResult::new(
                    &next,
                    7,
                    Some(metadata.landing_y),
                    0,
                    0,
                    0,
                    0,
                    0,
                    true,
                    false,
                    false,
                ),
            ));
        }
        let manual_duration = manual_started.elapsed();

        let hot_into_started = Instant::now();
        for _ in 0..ITERATIONS {
            let mut child = MaybeUninit::uninit();
            let mut hot = MaybeUninit::uninit();
            transition_hot_into(black_box(&state), pair, 7, &mut child, &mut hot)
                .expect("valid transition");
            // SAFETY: the successful transition initialized both output slots.
            black_box(unsafe { (child.assume_init_ref(), hot.assume_init_ref()) });
        }
        let hot_into_duration = hot_into_started.elapsed();

        let transition_started = Instant::now();
        for _ in 0..ITERATIONS {
            black_box(transition(black_box(&state), pair, 7, None).expect("valid transition"));
        }
        let transition_duration = transition_started.elapsed();

        let into_started = Instant::now();
        for _ in 0..ITERATIONS {
            let mut summary = MaybeUninit::uninit();
            transition_into(black_box(&state), pair, 7, None, &mut summary)
                .expect("valid transition");
            // SAFETY: the successful transition initialized the output slot.
            black_box(unsafe { summary.assume_init_ref() });
        }
        let into_duration = into_started.elapsed();

        let per_iteration =
            |duration: std::time::Duration| duration.as_nanos() as f64 / f64::from(ITERATIONS);
        println!(
            "state_bytes={} hot_result_bytes={} summary_bytes={} baseline_ns={:.3} placement_ns={:.3} inserted_vanish_ns={:.3} combined_ns={:.3} result_write_ns={:.3} manual_ns={:.3} hot_into_ns={:.3} transition_ns={:.3} into_ns={:.3}",
            std::mem::size_of::<CompactState>(),
            std::mem::size_of::<TransitionHotResult>(),
            std::mem::size_of::<TransitionSummary>(),
            per_iteration(baseline),
            per_iteration(placement_duration),
            per_iteration(vanish_duration),
            per_iteration(combined_duration),
            per_iteration(result_write_duration),
            per_iteration(manual_duration),
            per_iteration(hot_into_duration),
            per_iteration(transition_duration),
            per_iteration(into_duration),
        );
    }
}
