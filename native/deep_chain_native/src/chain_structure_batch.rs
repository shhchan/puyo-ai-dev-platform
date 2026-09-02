//! QA-only binary boundary for native chain-structure differential tests.
//!
//! Search nodes call `chain_structure::evaluate_hot` directly.  This module
//! exists only to batch oracle comparisons and to time an exact operation
//! count without per-node FFI crossings.

use std::hint::black_box;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering as AtomicOrdering};
use std::sync::{Arc, Barrier};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use crate::chain_structure::{
    ActionStructureFeatures, ChainStructureFeatures, EvaluationConfig, EvaluationEvidence,
    EvaluationHot, EvaluationProfileCounts, PROFILE_COUNTER_COUNT, PROFILE_STAGE_BASE_FEATURES,
    PROFILE_STAGE_COUNT, PROFILE_STAGE_DRIVER, PROFILE_STAGE_TRANSITION, QuiescenceCandidate,
    RESULT_ABI_VERSION, RESULT_SCHEMA, WEIGHT_COUNT, evaluate_evidence, evaluate_hot,
    evaluate_profiled,
};
use crate::compact::{CompactState, Pair, TransitionHotResult, transition_hot};

pub(crate) const BATCH_SCHEMA: &str = "puyo.native_chain_structure_batch.v1";
pub(crate) const PROFILE_SCHEMA: &str = "puyo.native_chain_structure_combined_profile.v1";
pub(crate) const STAGE_PROFILE_SCHEMA: &str = "puyo.native_chain_structure_stage_profile.v6";

const REQUEST_MAGIC: &[u8; 4] = b"NCSB";
const SUCCESS_MAGIC: &[u8; 4] = b"NCSS";
const PROFILE_MAGIC: &[u8; 4] = b"NCSP";
const STAGE_PROFILE_MAGIC: &[u8; 4] = b"NCST";
const ABI_VERSION: u16 = 1;
const REQUEST_HEADER_BYTES: usize = 48 + WEIGHT_COUNT * 8;
const REQUEST_RECORD_BYTES: usize = 184;
const FLAG_EVIDENCE: u16 = 0x1;
const KNOWN_FLAGS: u16 = FLAG_EVIDENCE;
const MAX_RECORDS: usize = 50_000;
const MAX_REQUEST_BYTES: usize = 16 * 1024 * 1024;
const MAX_RESPONSE_BYTES: usize = 16 * 1024 * 1024;
const STAGE_PROFILE_HEADER_BYTES: usize = 64 + PROFILE_COUNTER_COUNT * 8 + PROFILE_STAGE_COUNT * 16;
const STAGE_PROFILE_RECORD_BYTES: usize = PROFILE_COUNTER_COUNT * 4;
const MIN_SAMPLE_INTERVAL_US: u32 = 10;
const MAX_SAMPLE_INTERVAL_US: u32 = 10_000;

#[derive(Clone, Debug)]
struct BatchError(String);

impl BatchError {
    fn invalid(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

type BatchResult<T> = Result<T, BatchError>;

#[derive(Clone, Copy)]
struct BatchRecord {
    state: CompactState,
    parent: Option<CompactState>,
    action: Option<TransitionHotResult>,
    target_chain_count: u8,
    pair: Pair,
    action_id: u8,
}

struct BatchRequest {
    flags: u16,
    config: EvaluationConfig,
    records: Vec<BatchRecord>,
}

fn read_u16(data: &[u8], offset: usize, name: &str) -> BatchResult<u16> {
    let bytes = data
        .get(offset..offset + 2)
        .ok_or_else(|| BatchError::invalid(format!("truncated {name}")))?;
    Ok(u16::from_le_bytes(
        bytes.try_into().expect("two-byte slice"),
    ))
}

fn read_u32(data: &[u8], offset: usize, name: &str) -> BatchResult<u32> {
    let bytes = data
        .get(offset..offset + 4)
        .ok_or_else(|| BatchError::invalid(format!("truncated {name}")))?;
    Ok(u32::from_le_bytes(
        bytes.try_into().expect("four-byte slice"),
    ))
}

fn read_u64(data: &[u8], offset: usize, name: &str) -> BatchResult<u64> {
    let bytes = data
        .get(offset..offset + 8)
        .ok_or_else(|| BatchError::invalid(format!("truncated {name}")))?;
    Ok(u64::from_le_bytes(
        bytes.try_into().expect("eight-byte slice"),
    ))
}

fn read_f64(data: &[u8], offset: usize, name: &str) -> BatchResult<f64> {
    let value = f64::from_bits(read_u64(data, offset, name)?);
    if !value.is_finite() {
        return Err(BatchError::invalid(format!("{name} must be finite")));
    }
    Ok(value)
}

fn parse_request(data: &[u8]) -> BatchResult<BatchRequest> {
    if data.len() < REQUEST_HEADER_BYTES
        || data.len() > MAX_REQUEST_BYTES
        || data.get(..4) != Some(REQUEST_MAGIC)
    {
        return Err(BatchError::invalid(
            "invalid native chain-structure request framing",
        ));
    }
    if read_u16(data, 4, "ABI version")? != ABI_VERSION {
        return Err(BatchError::invalid(
            "unsupported native chain-structure ABI",
        ));
    }
    let flags = read_u16(data, 6, "request flags")?;
    let record_count = read_u32(data, 8, "record count")? as usize;
    let record_bytes = read_u16(data, 12, "record bytes")? as usize;
    if flags & !KNOWN_FLAGS != 0
        || record_count == 0
        || record_count > MAX_RECORDS
        || record_bytes != REQUEST_RECORD_BYTES
        || read_u16(data, 14, "request reserved")? != 0
        || data.get(18..20) != Some(&[0, 0])
        || read_u32(data, 28, "request reserved 2")? != 0
    {
        return Err(BatchError::invalid(
            "invalid native chain-structure request controls",
        ));
    }
    let expected = REQUEST_HEADER_BYTES
        .checked_add(
            record_count
                .checked_mul(REQUEST_RECORD_BYTES)
                .ok_or_else(|| BatchError::invalid("request size overflow"))?,
        )
        .ok_or_else(|| BatchError::invalid("request size overflow"))?;
    if data.len() != expected {
        return Err(BatchError::invalid(
            "native chain-structure request length mismatch",
        ));
    }
    let mut weights = [0.0_f64; WEIGHT_COUNT];
    for (index, value) in weights.iter_mut().enumerate() {
        *value = read_f64(data, 48 + index * 8, "evaluator weight")?;
    }
    let config = EvaluationConfig {
        max_added_puyos: data[16],
        max_pattern_nodes: read_u32(data, 20, "max pattern nodes")?,
        max_resolution_nodes: read_u32(data, 24, "max resolution nodes")?,
        max_candidates: data[17],
        weights,
        fatal_score: read_f64(data, 40, "fatal score")?,
        version_key: read_u64(data, 32, "config version key")?,
    };
    config.validate().map_err(BatchError::invalid)?;

    let mut records = Vec::with_capacity(record_count);
    for index in 0..record_count {
        let offset = REQUEST_HEADER_BYTES + index * REQUEST_RECORD_BYTES;
        let state = CompactState::from_bytes(&data[offset..offset + 87])
            .map_err(|error| BatchError::invalid(format!("record {index}: {error}")))?;
        let parent_present = data[offset + 87];
        let parent_payload = &data[offset + 88..offset + 175];
        let parent = match parent_present {
            0 if parent_payload.iter().all(|value| *value == 0) => None,
            1 => Some(
                CompactState::from_bytes(parent_payload)
                    .map_err(|error| BatchError::invalid(format!("record {index}: {error}")))?,
            ),
            _ => {
                return Err(BatchError::invalid(format!(
                    "record {index}: invalid parent control"
                )));
            }
        };
        let action_present = data[offset + 175];
        let target_chain_count = data[offset + 176];
        let chain_count = data[offset + 177];
        let vanished_count = read_u16(data, offset + 178, "vanished count")?;
        let game_over = data[offset + 180];
        let action = match action_present {
            0 if chain_count == 0 && vanished_count == 0 && game_over == 0 => None,
            1 if game_over <= 1 => Some(TransitionHotResult {
                score_delta: 0,
                attack_score_delta: 0,
                vanished_count,
                garbage_cleared_count: 0,
                action_id: data[offset + 183],
                axis_y: u8::MAX,
                chain_count,
                flags: game_over << 1,
            }),
            _ => {
                return Err(BatchError::invalid(format!(
                    "record {index}: invalid action control"
                )));
            }
        };
        if target_chain_count == 0 || parent.is_some() != action.is_some() {
            return Err(BatchError::invalid(format!(
                "record {index}: parent/action context is incomplete"
            )));
        }
        let pair = Pair::from_ids(data[offset + 181], data[offset + 182])
            .map_err(|error| BatchError::invalid(format!("record {index}: {error}")))?;
        let action_id = data[offset + 183];
        if action_id >= 22 {
            return Err(BatchError::invalid(format!(
                "record {index}: action ID is outside the v1 layout"
            )));
        }
        records.push(BatchRecord {
            state,
            parent,
            action,
            target_chain_count,
            pair,
            action_id,
        });
    }
    Ok(BatchRequest {
        flags,
        config,
        records,
    })
}

fn write_u16(output: &mut Vec<u8>, value: u16) {
    output.extend_from_slice(&value.to_le_bytes());
}

fn write_u32(output: &mut Vec<u8>, value: u32) {
    output.extend_from_slice(&value.to_le_bytes());
}

fn write_u64(output: &mut Vec<u8>, value: u64) {
    output.extend_from_slice(&value.to_le_bytes());
}

fn write_u128(output: &mut Vec<u8>, value: u128) {
    output.extend_from_slice(&value.to_le_bytes());
}

fn write_f64(output: &mut Vec<u8>, value: f64) {
    write_u64(output, value.to_bits());
}

fn encode_features(output: &mut Vec<u8>, value: &ChainStructureFeatures) {
    output.extend_from_slice(&value.canonical_column_heights);
    output.extend_from_slice(&[
        value.normal_puyo_count,
        value.component_count,
        value.isolated_count,
        value.link_2,
        value.link_3,
        value.connectivity_edges,
        value.connection_candidate_count,
        value.reachable_ignition_count,
        value.growth_site_count,
        value.foundation_cell_count,
    ]);
    write_u16(output, value.fold_space);
    output.extend_from_slice(&[
        value.adjacent_roughness,
        value.height_spread,
        value.well_depth,
        value.bump_height,
    ]);
    write_f64(output, value.danger_ratio);
    output.extend_from_slice(&[value.nuisance_count, value.hidden_row_count]);
    output.push(
        u8::from(value.trigger_reachable)
            | (u8::from(value.death) << 1)
            | (u8::from(value.unreachable_trigger) << 2)
            | (u8::from(value.structural_dead_end) << 3),
    );
    write_f64(output, value.trigger_protection);
    output.push(value.potential_chain_count);
    write_u32(output, value.potential_chain_score);
    output.extend_from_slice(&[
        value.required_key_count as u8,
        value.trigger_column as u8,
        value.trigger_height as u8,
        value.remaining_link_2,
        value.remaining_link_3,
        value.remaining_connection_edges,
    ]);
}

fn encode_action(output: &mut Vec<u8>, value: &ActionStructureFeatures) {
    output.push(
        u8::from(value.evaluated)
            | (u8::from(value.premature_fire) << 1)
            | (u8::from(value.death) << 2),
    );
    output.extend_from_slice(&[value.tear_count, value.waste_count, value.trigger_damage]);
    write_f64(output, value.danger_delta);
}

fn encode_candidate(output: &mut Vec<u8>, value: &QuiescenceCandidate) {
    output.extend_from_slice(&[
        value.chain_count,
        value.required_key_count,
        value.trigger_color,
        value.trigger_column,
        value.trigger_height,
        value.remaining_link_2,
        value.remaining_link_3,
        value.remaining_connection_edges,
        value.extension_space,
    ]);
    write_u32(output, value.chain_score);
    write_f64(output, value.trigger_protection);
    write_u128(output, value.placements_mask);
    write_u128(output, value.anchor_mask);
    write_u64(output, value.fixed_tie_break);
}

fn encode_evaluation(value: &EvaluationEvidence, include_evidence: bool) -> Vec<u8> {
    let mut output = Vec::new();
    output.extend_from_slice(&[
        value.hot.status as u8,
        value.hot.truncation_reason as u8,
        u8::from(value.hot.has_best),
        if include_evidence {
            value.candidate_count
        } else {
            0
        },
    ]);
    write_u32(&mut output, value.hot.pattern_nodes);
    write_u32(&mut output, value.hot.resolution_nodes);
    write_f64(&mut output, value.hot.score);
    encode_features(&mut output, &value.hot.features);
    encode_action(&mut output, &value.hot.action_features);
    for item in value.hot.score_breakdown.values {
        write_f64(&mut output, item);
    }
    if value.hot.has_best {
        encode_candidate(&mut output, &value.hot.best);
    } else {
        encode_candidate(&mut output, &QuiescenceCandidate::default());
    }
    if include_evidence {
        for candidate in value
            .candidates
            .iter()
            .take(usize::from(value.candidate_count))
        {
            encode_candidate(&mut output, candidate);
        }
    }
    output
}

fn execute(data: &[u8]) -> BatchResult<Vec<u8>> {
    let request = parse_request(data)?;
    let include_evidence = request.flags & FLAG_EVIDENCE != 0;
    let mut encoded_records = Vec::with_capacity(request.records.len());
    let mut total_bytes = 16_usize;
    for record in &request.records {
        let parent = record.parent.map(|state| {
            evaluate_hot(
                &state,
                &request.config,
                None,
                None,
                record.target_chain_count,
            )
        });
        let evaluation = evaluate_evidence(
            &record.state,
            &request.config,
            parent.as_ref(),
            record.action,
            record.target_chain_count,
        );
        let encoded = encode_evaluation(&evaluation, include_evidence);
        total_bytes = total_bytes
            .checked_add(4 + encoded.len())
            .ok_or_else(|| BatchError::invalid("response size overflow"))?;
        if total_bytes > MAX_RESPONSE_BYTES {
            return Err(BatchError::invalid(
                "native chain-structure response exceeds its limit",
            ));
        }
        encoded_records.push(encoded);
    }
    let mut output = Vec::with_capacity(total_bytes);
    output.extend_from_slice(SUCCESS_MAGIC);
    write_u16(&mut output, ABI_VERSION);
    write_u16(&mut output, request.flags);
    write_u32(&mut output, request.records.len() as u32);
    write_u32(&mut output, 0);
    for encoded in encoded_records {
        write_u32(&mut output, encoded.len() as u32);
        output.extend_from_slice(&encoded);
    }
    Ok(output)
}

pub(crate) fn guarded_execute(data: &[u8]) -> Result<Vec<u8>, String> {
    match catch_unwind(AssertUnwindSafe(|| execute(data))) {
        Ok(Ok(value)) => Ok(value),
        Ok(Err(error)) => Err(error.0),
        Err(_) => Err("panic caught at native chain-structure boundary".to_owned()),
    }
}

struct StageSampler {
    marker: Arc<AtomicU8>,
    running: Arc<AtomicBool>,
    handle: JoinHandle<[u64; PROFILE_STAGE_COUNT]>,
}

impl StageSampler {
    fn start(interval_us: u32) -> Self {
        let marker = Arc::new(AtomicU8::new(PROFILE_STAGE_DRIVER));
        let running = Arc::new(AtomicBool::new(true));
        let ready = Arc::new(Barrier::new(2));
        let sampled_marker = Arc::clone(&marker);
        let sampled_running = Arc::clone(&running);
        let sampled_ready = Arc::clone(&ready);
        let handle = thread::spawn(move || {
            let mut samples = [0_u64; PROFILE_STAGE_COUNT];
            let initial = usize::from(sampled_marker.load(AtomicOrdering::Relaxed));
            if initial < samples.len() {
                samples[initial] += 1;
            }
            sampled_ready.wait();
            let interval = Duration::from_micros(u64::from(interval_us));
            while sampled_running.load(AtomicOrdering::Relaxed) {
                thread::sleep(interval);
                if !sampled_running.load(AtomicOrdering::Relaxed) {
                    break;
                }
                let stage = usize::from(sampled_marker.load(AtomicOrdering::Relaxed));
                if stage < samples.len() {
                    samples[stage] += 1;
                }
            }
            samples
        });
        ready.wait();
        Self {
            marker,
            running,
            handle,
        }
    }

    fn finish(self) -> [u64; PROFILE_STAGE_COUNT] {
        self.running.store(false, AtomicOrdering::Relaxed);
        self.handle
            .join()
            .expect("native stage sampler thread must not panic")
    }
}

#[cfg(target_arch = "x86_64")]
fn profile_cycle_counter() -> u64 {
    // SAFETY: LFENCE/RDTSC are available on the supported x86_64 release
    // target and only read the invariant timestamp counter.
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

fn accumulate_profile_counts(
    target: &mut [u64; PROFILE_COUNTER_COUNT],
    stage_entries: &mut [u64; PROFILE_STAGE_COUNT],
    value: EvaluationProfileCounts,
) {
    target[0] += u64::from(value.pattern_nodes);
    target[1] += u64::from(value.executed_pattern_probes);
    target[2] += u64::from(value.resolution_nodes);
    target[3] += u64::from(value.rank_comparison_calls);
    target[4] += u64::from(value.rank_tie_calls);
    target[5] += u64::from(value.sha256_calls);
    target[6] += u64::from(value.single_component_frontiers);
    target[7] += u64::from(value.multi_component_frontiers);
    target[8] += u64::from(value.frontier_state_visits);
    target[9] += u64::from(value.qualified_candidates);
    target[10] += u64::from(value.resolution_group_comparisons);
    target[11] += u64::from(value.resolution_groups);
    target[12] += u64::from(value.precomputed_resolution_groups);
    target[13] += u64::from(value.precomputed_candidate_hits);
    target[14] += u64::from(value.resolution_cache_hits);
    for (target_value, source_value) in stage_entries.iter_mut().zip(value.stage_entries) {
        *target_value += u64::from(source_value);
    }
}

fn stage_profile(data: &[u8], operations: u32, sample_interval_us: u32) -> BatchResult<Vec<u8>> {
    if operations == 0 {
        return Err(BatchError::invalid(
            "stage-profile operation count must be positive",
        ));
    }
    if !(MIN_SAMPLE_INTERVAL_US..=MAX_SAMPLE_INTERVAL_US).contains(&sample_interval_us) {
        return Err(BatchError::invalid(
            "stage-profile sample interval is outside the supported range",
        ));
    }
    let request = parse_request(data)?;
    let mut parents: Vec<EvaluationHot> = Vec::with_capacity(request.records.len());
    let mut record_counts = Vec::with_capacity(request.records.len());
    let validation_marker = AtomicU8::new(PROFILE_STAGE_DRIVER);
    let mut mismatch_count = 0_u32;
    for record in &request.records {
        let parent = evaluate_hot(
            &record.state,
            &request.config,
            None,
            None,
            record.target_chain_count,
        );
        let (child, transition) = transition_hot(&record.state, record.pair, record.action_id)
            .map_err(|error| BatchError::invalid(error.to_string()))?;
        if !transition.valid() {
            return Err(BatchError::invalid(
                "stage-profile records must contain valid transitions",
            ));
        }
        let expected = evaluate_hot(
            &child,
            &request.config,
            Some(&parent),
            Some(transition),
            record.target_chain_count,
        );
        let (profiled, counts) = evaluate_profiled(
            &child,
            &request.config,
            Some(&parent),
            Some(transition),
            record.target_chain_count,
            &validation_marker,
        );
        mismatch_count += u32::from(profiled != expected);
        parents.push(parent);
        record_counts.push(counts);
    }

    let sampler = StageSampler::start(sample_interval_us);
    let marker = Arc::clone(&sampler.marker);
    let started = Instant::now();
    let started_cycles = profile_cycle_counter();
    let mut checksum = 0_u64;
    let mut aggregate_counts = [0_u64; PROFILE_COUNTER_COUNT];
    let mut stage_entries = [0_u64; PROFILE_STAGE_COUNT];
    for operation in 0..operations as usize {
        let index = operation % request.records.len();
        let record = &request.records[index];
        marker.store(PROFILE_STAGE_TRANSITION, AtomicOrdering::Relaxed);
        stage_entries[usize::from(PROFILE_STAGE_TRANSITION)] += 1;
        let (child, transition) = transition_hot(
            black_box(&record.state),
            black_box(record.pair),
            black_box(record.action_id),
        )
        .map_err(|error| BatchError::invalid(error.to_string()))?;
        marker.store(PROFILE_STAGE_BASE_FEATURES, AtomicOrdering::Relaxed);
        let (evaluation, counts) = evaluate_profiled(
            black_box(&child),
            black_box(&request.config),
            Some(black_box(&parents[index])),
            Some(black_box(transition)),
            black_box(record.target_chain_count),
            marker.as_ref(),
        );
        accumulate_profile_counts(&mut aggregate_counts, &mut stage_entries, counts);
        marker.store(PROFILE_STAGE_DRIVER, AtomicOrdering::Relaxed);
        stage_entries[usize::from(PROFILE_STAGE_DRIVER)] += 1;
        checksum ^= black_box(
            evaluation
                .score
                .to_bits()
                .rotate_left((operation % 63) as u32),
        );
    }
    black_box(checksum);
    let cycles = profile_cycle_counter().wrapping_sub(started_cycles);
    let elapsed_ns = u64::try_from(started.elapsed().as_nanos()).unwrap_or(u64::MAX);
    marker.store(PROFILE_STAGE_DRIVER, AtomicOrdering::Relaxed);
    let stage_samples = sampler.finish();
    let sample_count = stage_samples.iter().sum::<u64>();

    let expected_bytes = STAGE_PROFILE_HEADER_BYTES
        .checked_add(
            record_counts
                .len()
                .checked_mul(STAGE_PROFILE_RECORD_BYTES)
                .ok_or_else(|| BatchError::invalid("stage-profile response size overflow"))?,
        )
        .ok_or_else(|| BatchError::invalid("stage-profile response size overflow"))?;
    let mut output = Vec::with_capacity(expected_bytes);
    output.extend_from_slice(STAGE_PROFILE_MAGIC);
    write_u16(&mut output, ABI_VERSION);
    write_u16(
        &mut output,
        if cfg!(target_arch = "x86_64") {
            0x3
        } else {
            0x2
        },
    );
    write_u32(&mut output, operations);
    write_u32(&mut output, request.records.len() as u32);
    write_u64(&mut output, elapsed_ns);
    write_u64(&mut output, cycles);
    write_u64(&mut output, checksum);
    write_u32(&mut output, sample_interval_us);
    write_u32(&mut output, PROFILE_STAGE_COUNT as u32);
    write_u64(&mut output, sample_count);
    write_u32(&mut output, mismatch_count);
    write_u16(&mut output, RESULT_ABI_VERSION);
    write_u16(&mut output, STAGE_PROFILE_RECORD_BYTES as u16);
    for value in aggregate_counts {
        write_u64(&mut output, value);
    }
    for value in stage_samples {
        write_u64(&mut output, value);
    }
    for value in stage_entries {
        write_u64(&mut output, value);
    }
    debug_assert_eq!(output.len(), STAGE_PROFILE_HEADER_BYTES);
    for counts in record_counts {
        for value in [
            counts.pattern_nodes,
            counts.executed_pattern_probes,
            counts.resolution_nodes,
            counts.rank_comparison_calls,
            counts.rank_tie_calls,
            counts.sha256_calls,
            counts.single_component_frontiers,
            counts.multi_component_frontiers,
            counts.frontier_state_visits,
            counts.qualified_candidates,
            counts.resolution_group_comparisons,
            counts.resolution_groups,
            counts.precomputed_resolution_groups,
            counts.precomputed_candidate_hits,
            counts.resolution_cache_hits,
        ] {
            write_u32(&mut output, value);
        }
    }
    debug_assert_eq!(output.len(), expected_bytes);
    Ok(output)
}

pub(crate) fn guarded_stage_profile(
    data: &[u8],
    operations: u32,
    sample_interval_us: u32,
) -> Result<Vec<u8>, String> {
    match catch_unwind(AssertUnwindSafe(|| {
        stage_profile(data, operations, sample_interval_us)
    })) {
        Ok(Ok(value)) => Ok(value),
        Ok(Err(error)) => Err(error.0),
        Err(_) => Err("panic caught at native stage-profile boundary".to_owned()),
    }
}

fn profile(data: &[u8], operations: u32) -> BatchResult<Vec<u8>> {
    if operations == 0 {
        return Err(BatchError::invalid(
            "profile operation count must be positive",
        ));
    }
    let request = parse_request(data)?;
    let mut parents: Vec<EvaluationHot> = Vec::with_capacity(request.records.len());
    for record in &request.records {
        let parent = evaluate_hot(
            &record.state,
            &request.config,
            None,
            None,
            record.target_chain_count,
        );
        let (_, transition) = transition_hot(&record.state, record.pair, record.action_id)
            .map_err(|error| BatchError::invalid(error.to_string()))?;
        if !transition.valid() {
            return Err(BatchError::invalid(
                "combined profile records must contain valid transitions",
            ));
        }
        parents.push(parent);
    }
    let started = Instant::now();
    let mut checksum = 0_u64;
    for operation in 0..operations as usize {
        let index = operation % request.records.len();
        let record = &request.records[index];
        let (child, transition) = transition_hot(
            black_box(&record.state),
            black_box(record.pair),
            black_box(record.action_id),
        )
        .map_err(|error| BatchError::invalid(error.to_string()))?;
        let evaluation = evaluate_hot(
            black_box(&child),
            black_box(&request.config),
            Some(black_box(&parents[index])),
            Some(black_box(transition)),
            black_box(record.target_chain_count),
        );
        checksum ^= black_box(
            evaluation
                .score
                .to_bits()
                .rotate_left((operation % 63) as u32),
        );
    }
    let elapsed_ns = started.elapsed().as_nanos() as u64;
    let mut output = Vec::with_capacity(40);
    output.extend_from_slice(PROFILE_MAGIC);
    write_u16(&mut output, ABI_VERSION);
    write_u16(&mut output, 0);
    write_u32(&mut output, operations);
    write_u32(&mut output, request.records.len() as u32);
    write_u64(&mut output, elapsed_ns);
    write_u64(&mut output, checksum);
    write_u16(&mut output, RESULT_ABI_VERSION);
    write_u16(&mut output, 0);
    write_u32(&mut output, 0);
    Ok(output)
}

pub(crate) fn guarded_profile(data: &[u8], operations: u32) -> Result<Vec<u8>, String> {
    match catch_unwind(AssertUnwindSafe(|| profile(data, operations))) {
        Ok(Ok(value)) => Ok(value),
        Ok(Err(error)) => Err(error.0),
        Err(_) => Err("panic caught at native combined-profile boundary".to_owned()),
    }
}

pub(crate) fn schema_identity() -> (&'static str, &'static str) {
    (RESULT_SCHEMA, PROFILE_SCHEMA)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chain_structure::{EvaluationStatus, ScoreBreakdown, TruncationReason};

    #[test]
    fn schema_constants_are_versioned() {
        assert_eq!(BATCH_SCHEMA, "puyo.native_chain_structure_batch.v1");
        assert_eq!(schema_identity().0, "puyo.native_chain_structure_hot.v1");
        assert_eq!(
            STAGE_PROFILE_SCHEMA,
            "puyo.native_chain_structure_stage_profile.v6"
        );
        assert_eq!(EvaluationStatus::Available as u8, 1);
        assert_eq!(TruncationReason::ResolutionNodes as u8, 2);
        assert_eq!(std::mem::size_of::<CompactState>(), 80);
        assert_eq!(std::mem::size_of::<TransitionHotResult>(), 24);
        assert_eq!(ScoreBreakdown::TOTAL, 14);
    }
}
