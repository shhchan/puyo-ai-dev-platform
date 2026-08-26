//! Binary QA boundary for batched compact transitions.
//!
//! This protocol is intentionally separate from the one-call decision
//! envelope.  It exists only for differential tests, frozen-corpus evidence,
//! and component microbenchmarks.  Later native evaluator/search code calls
//! [`crate::compact::transition`] directly.

use std::hint::black_box;
use std::mem::MaybeUninit;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::time::{Duration, Instant};

use crate::compact::{
    ACTION_COUNT, CompactError, CompactErrorKind, CompactState, PLANE_BYTES, PLANE_COUNT, Pair,
    STATE_BYTES, TransitionSummary, TransitionTrace, legal_actions_mask, planes_to_wire,
    symmetry_reduced_actions_mask, transition_into,
};

pub(crate) const ABI_VERSION: u16 = 1;
pub(crate) const SCHEMA_NAME: &str = "puyo.native_compact_transition_batch.v1";
pub(crate) const KERNEL_PATH: &str = "scalar";

const SCHEMA_MINOR: u16 = 0;
const REQUEST_MAGIC: &[u8; 4] = b"PCTB";
const SUCCESS_MAGIC: &[u8; 4] = b"PCTS";
const ERROR_MAGIC: &[u8; 4] = b"PCTE";
const REQUEST_HEADER_BYTES: usize = 24;
const REQUEST_RECORD_BYTES: usize = STATE_BYTES + 3;
const SUCCESS_HEADER_BYTES: usize = 48;
const ERROR_HEADER_BYTES: usize = 24;
const SUCCESS_FIXED_RECORD_BYTES: usize = 164;
const TRACE_PLACEMENT_BYTES: usize = PLANE_COUNT * PLANE_BYTES;
const TRACE_CHAIN_BYTES: usize = 108;
const MAX_BATCH_RECORDS: usize = 50_000;
const MAX_TRACE_BATCH_RECORDS: usize = 4_096;
const MAX_BATCH_BYTES: usize = 16 * 1024 * 1024;
const MAX_ERROR_MESSAGE_BYTES: usize = 4_096;
const FLAG_CAPTURE_TRACE: u16 = 0x1;
const FLAG_INCLUDE_ACTIONS: u16 = 0x2;
const FLAG_MEASURE_TIMING: u16 = 0x4;
const KNOWN_FLAGS: u16 = FLAG_CAPTURE_TRACE | FLAG_INCLUDE_ACTIONS | FLAG_MEASURE_TIMING;
const MASK_NOT_REQUESTED: u32 = u32::MAX;

#[repr(u16)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum BatchErrorCode {
    InvalidInput = 1,
    ArithmeticOverflow = 2,
    InternalPanic = 3,
    ResourceExhausted = 4,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct BatchError {
    code: BatchErrorCode,
    record_index: Option<u32>,
    message: String,
}

impl BatchError {
    fn invalid(record_index: Option<usize>, message: impl Into<String>) -> Self {
        Self {
            code: BatchErrorCode::InvalidInput,
            record_index: record_index.map(bounded_index),
            message: message.into(),
        }
    }

    fn resource(message: impl Into<String>) -> Self {
        Self {
            code: BatchErrorCode::ResourceExhausted,
            record_index: None,
            message: message.into(),
        }
    }

    fn internal(message: impl Into<String>) -> Self {
        Self {
            code: BatchErrorCode::InternalPanic,
            record_index: None,
            message: message.into(),
        }
    }

    fn from_compact(index: usize, error: CompactError) -> Self {
        Self {
            code: match error.kind {
                CompactErrorKind::InvalidInput => BatchErrorCode::InvalidInput,
                CompactErrorKind::ArithmeticOverflow => BatchErrorCode::ArithmeticOverflow,
            },
            record_index: Some(bounded_index(index)),
            message: error.message,
        }
    }
}

type BatchResult<T> = Result<T, BatchError>;

fn bounded_index(value: usize) -> u32 {
    u32::try_from(value).unwrap_or(u32::MAX - 1)
}

#[derive(Clone, Copy)]
struct BatchOptions {
    flags: u16,
    capture_trace: bool,
    include_actions: bool,
    measure_timing: bool,
}

#[derive(Clone, Copy)]
struct BatchRecord {
    state: CompactState,
    pair: Pair,
    action_id: u8,
}

struct BatchRequest {
    options: BatchOptions,
    records: Vec<BatchRecord>,
}

struct BatchRecordOutput {
    summary: TransitionSummary,
    legal_mask: Option<u32>,
    reduced_mask: Option<u32>,
    trace: Option<TransitionTrace>,
}

fn read_u16(data: &[u8], offset: usize, name: &str) -> BatchResult<u16> {
    let bytes = data
        .get(offset..offset + 2)
        .ok_or_else(|| BatchError::invalid(None, format!("truncated {name}")))?;
    Ok(u16::from_le_bytes([bytes[0], bytes[1]]))
}

fn read_u32(data: &[u8], offset: usize, name: &str) -> BatchResult<u32> {
    let bytes = data
        .get(offset..offset + 4)
        .ok_or_else(|| BatchError::invalid(None, format!("truncated {name}")))?;
    Ok(u32::from_le_bytes(
        bytes.try_into().expect("validated four-byte slice"),
    ))
}

fn parse_request(data: &[u8]) -> BatchResult<BatchRequest> {
    if data.len() < REQUEST_HEADER_BYTES {
        return Err(BatchError::invalid(
            None,
            "batch request is shorter than its header",
        ));
    }
    if data.len() > MAX_BATCH_BYTES {
        return Err(BatchError::resource("batch request exceeds the byte limit"));
    }
    if data.get(..4) != Some(REQUEST_MAGIC.as_slice()) {
        return Err(BatchError::invalid(
            None,
            "invalid compact batch request magic",
        ));
    }
    let schema_major = read_u16(data, 4, "schema major")?;
    let schema_minor = read_u16(data, 6, "schema minor")?;
    if schema_major != ABI_VERSION || schema_minor != SCHEMA_MINOR {
        return Err(BatchError::invalid(
            None,
            format!("unsupported compact batch schema {schema_major}.{schema_minor}"),
        ));
    }
    let flags = read_u16(data, 8, "batch flags")?;
    if flags & !KNOWN_FLAGS != 0 {
        return Err(BatchError::invalid(None, "unknown compact batch flag"));
    }
    if usize::from(read_u16(data, 10, "record size")?) != REQUEST_RECORD_BYTES {
        return Err(BatchError::invalid(
            None,
            "unexpected compact batch record size",
        ));
    }
    let record_count = usize::try_from(read_u32(data, 12, "record count")?)
        .map_err(|_| BatchError::invalid(None, "record count does not fit usize"))?;
    if record_count == 0 || record_count > MAX_BATCH_RECORDS {
        return Err(BatchError::resource(
            "compact batch record count is outside its limit",
        ));
    }
    let body_bytes = usize::try_from(read_u32(data, 16, "body size")?)
        .map_err(|_| BatchError::invalid(None, "body size does not fit usize"))?;
    if read_u32(data, 20, "reserved header field")? != 0 {
        return Err(BatchError::invalid(
            None,
            "compact batch reserved field is not zero",
        ));
    }
    let expected_body = record_count
        .checked_mul(REQUEST_RECORD_BYTES)
        .ok_or_else(|| BatchError::resource("compact batch body size overflow"))?;
    if body_bytes != expected_body || data.len() != REQUEST_HEADER_BYTES + body_bytes {
        return Err(BatchError::invalid(
            None,
            "compact batch body length does not match framing",
        ));
    }
    let capture_trace = flags & FLAG_CAPTURE_TRACE != 0;
    if capture_trace && record_count > MAX_TRACE_BATCH_RECORDS {
        return Err(BatchError::resource(
            "trace capture batch exceeds the diagnostic record limit",
        ));
    }

    let mut records = Vec::with_capacity(record_count);
    let mut offset = REQUEST_HEADER_BYTES;
    for index in 0..record_count {
        let state_end = offset + STATE_BYTES;
        let state = CompactState::from_bytes(&data[offset..state_end])
            .map_err(|error| BatchError::from_compact(index, error))?;
        let axis_color = data[state_end];
        let child_color = data[state_end + 1];
        let action_id = data[state_end + 2];
        if usize::from(action_id) >= ACTION_COUNT {
            return Err(BatchError::invalid(
                Some(index),
                "action ID is outside the v1 layout",
            ));
        }
        let pair = Pair::from_ids(axis_color, child_color)
            .map_err(|error| BatchError::from_compact(index, error))?;
        records.push(BatchRecord {
            state,
            pair,
            action_id,
        });
        offset += REQUEST_RECORD_BYTES;
    }
    Ok(BatchRequest {
        options: BatchOptions {
            flags,
            capture_trace,
            include_actions: flags & FLAG_INCLUDE_ACTIONS != 0,
            measure_timing: flags & FLAG_MEASURE_TIMING != 0,
        },
        records,
    })
}

fn duration_nanos(duration: Duration) -> u64 {
    u64::try_from(duration.as_nanos()).unwrap_or(u64::MAX)
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

fn write_u128_prefix(output: &mut Vec<u8>, value: u128, bytes: usize) {
    output.extend_from_slice(&value.to_le_bytes()[..bytes]);
}

fn transition_flags(summary: &TransitionSummary) -> u8 {
    u8::from(summary.state.game_over())
        | (u8::from(summary.all_clear_achieved) << 1)
        | (u8::from(summary.state.all_clear_bonus_pending()) << 2)
        | (u8::from(summary.all_clear_bonus_consumed) << 3)
}

fn encode_record(output: &BatchRecordOutput) -> BatchResult<Vec<u8>> {
    let trace_count = output.trace.as_ref().map_or(0, |trace| trace.chains.len());
    if trace_count > usize::from(u8::MAX) {
        return Err(BatchError::resource(
            "compact transition trace is too large",
        ));
    }
    let placement_bytes = output
        .trace
        .as_ref()
        .and_then(|trace| trace.placement_planes)
        .map_or(0, |_| TRACE_PLACEMENT_BYTES);
    let capacity = SUCCESS_FIXED_RECORD_BYTES
        .checked_add(placement_bytes)
        .and_then(|value| value.checked_add(trace_count * TRACE_CHAIN_BYTES))
        .ok_or_else(|| BatchError::resource("compact result record size overflow"))?;
    let mut record = Vec::with_capacity(capacity);
    record.extend_from_slice(&output.summary.state.to_bytes());
    record.push(output.summary.action_id);
    record.push(u8::from(output.summary.valid));
    record.push(output.summary.axis_y.unwrap_or(u8::MAX));
    record.push(output.summary.chain_count);
    record.push(transition_flags(&output.summary));
    record.push(u8::try_from(trace_count).expect("bounded trace count"));
    record.push(u8::from(placement_bytes != 0));
    record.push(0);
    write_u16(&mut record, output.summary.vanished_count);
    write_u16(&mut record, output.summary.garbage_cleared_count);
    write_u32(&mut record, output.legal_mask.unwrap_or(MASK_NOT_REQUESTED));
    write_u32(
        &mut record,
        output.reduced_mask.unwrap_or(MASK_NOT_REQUESTED),
    );
    write_u64(&mut record, output.summary.score_delta);
    write_u64(&mut record, output.summary.attack_score_delta);
    write_u64(&mut record, output.summary.all_clear_bonus_score);
    write_u128_prefix(&mut record, output.summary.state.occupied(), PLANE_BYTES);
    record.extend_from_slice(&output.summary.state.column_heights());
    for lane in output.summary.state.board_fingerprint() {
        write_u64(&mut record, lane);
    }
    if let Some(trace) = &output.trace {
        if let Some(planes) = trace.placement_planes {
            record.extend_from_slice(&planes_to_wire(&planes));
        }
        for step in &trace.chains {
            record.push(step.chain_index);
            record.push(step.vanished_count);
            record.push(step.garbage_cleared_count);
            record.push(0);
            write_u16(&mut record, step.base);
            write_u16(&mut record, step.bonus);
            write_u64(&mut record, step.score);
            write_u64(&mut record, step.all_clear_bonus_score);
            record.extend_from_slice(&planes_to_wire(&step.board_planes));
            write_u128_prefix(&mut record, step.vanished_mask, 9);
            write_u128_prefix(&mut record, step.garbage_mask, 9);
        }
    }
    debug_assert_eq!(record.len(), capacity);
    Ok(record)
}

fn encode_success(
    options: BatchOptions,
    records: &[BatchRecordOutput],
    parse_duration: Duration,
    kernel_duration: Duration,
) -> BatchResult<Vec<u8>> {
    let encode_started = Instant::now();
    let mut body = Vec::new();
    for output in records {
        let record = encode_record(output)?;
        let record_length = u32::try_from(record.len())
            .map_err(|_| BatchError::resource("compact result record exceeds u32"))?;
        write_u32(&mut body, record_length);
        body.extend_from_slice(&record);
        if body.len() > MAX_BATCH_BYTES {
            return Err(BatchError::resource(
                "compact batch response exceeds the byte limit",
            ));
        }
    }
    let encode_duration = encode_started.elapsed();
    let mut result = Vec::with_capacity(SUCCESS_HEADER_BYTES + body.len());
    result.extend_from_slice(SUCCESS_MAGIC);
    write_u16(&mut result, ABI_VERSION);
    write_u16(&mut result, SCHEMA_MINOR);
    write_u16(&mut result, options.flags);
    write_u16(&mut result, 0);
    write_u32(
        &mut result,
        u32::try_from(records.len()).expect("bounded record count"),
    );
    write_u32(
        &mut result,
        u32::try_from(body.len()).expect("bounded response body"),
    );
    write_u32(&mut result, 0);
    if options.measure_timing {
        write_u64(&mut result, duration_nanos(parse_duration));
        write_u64(&mut result, duration_nanos(kernel_duration));
        write_u64(&mut result, duration_nanos(encode_duration));
    } else {
        write_u64(&mut result, 0);
        write_u64(&mut result, 0);
        write_u64(&mut result, 0);
    }
    result.extend_from_slice(&body);
    Ok(result)
}

fn bounded_message(message: &str) -> &str {
    let mut end = message.len().min(MAX_ERROR_MESSAGE_BYTES);
    while !message.is_char_boundary(end) {
        end -= 1;
    }
    &message[..end]
}

fn encode_error(error: &BatchError) -> Vec<u8> {
    let message = bounded_message(&error.message).as_bytes();
    let mut result = Vec::with_capacity(ERROR_HEADER_BYTES + message.len());
    result.extend_from_slice(ERROR_MAGIC);
    write_u16(&mut result, ABI_VERSION);
    write_u16(&mut result, SCHEMA_MINOR);
    write_u16(&mut result, error.code as u16);
    write_u16(&mut result, 0);
    write_u32(&mut result, error.record_index.unwrap_or(u32::MAX));
    write_u32(
        &mut result,
        u32::try_from(message.len()).expect("bounded error message"),
    );
    write_u32(&mut result, 0);
    result.extend_from_slice(message);
    result
}

fn execute(data: &[u8]) -> BatchResult<Vec<u8>> {
    let parse_started = Instant::now();
    let request = parse_request(data)?;
    let parse_duration = parse_started.elapsed();
    let kernel_duration = if request.options.measure_timing {
        let kernel_started = Instant::now();
        for (index, record) in request.records.iter().copied().enumerate() {
            let mut summary = MaybeUninit::uninit();
            transition_into(
                &record.state,
                record.pair,
                record.action_id,
                None,
                &mut summary,
            )
            .map_err(|error| BatchError::from_compact(index, error))?;
            // SAFETY: a successful native transition always initializes the slot.
            black_box(unsafe { summary.assume_init_ref() });
        }
        kernel_started.elapsed()
    } else {
        Duration::ZERO
    };
    let mut outputs = Vec::with_capacity(request.records.len());
    for (index, record) in request.records.iter().copied().enumerate() {
        let mut trace_output = request.options.capture_trace.then(TransitionTrace::default);
        let mut summary = MaybeUninit::uninit();
        transition_into(
            &record.state,
            record.pair,
            record.action_id,
            trace_output.as_mut(),
            &mut summary,
        )
        .map_err(|error| BatchError::from_compact(index, error))?;
        // SAFETY: a successful native transition always initializes the slot.
        let summary = unsafe { summary.assume_init() };
        let (legal_mask, reduced_mask) = if request.options.include_actions {
            let legal = legal_actions_mask(&record.state);
            let reduced = symmetry_reduced_actions_mask(&record.state, record.pair)
                .map_err(|error| BatchError::from_compact(index, error))?;
            (Some(legal), Some(reduced))
        } else {
            (None, None)
        };
        outputs.push(BatchRecordOutput {
            summary,
            legal_mask,
            reduced_mask,
            trace: trace_output,
        });
    }
    encode_success(request.options, &outputs, parse_duration, kernel_duration)
}

pub(crate) fn guarded_execute(data: &[u8]) -> Vec<u8> {
    match catch_unwind(AssertUnwindSafe(|| execute(data))) {
        Ok(Ok(response)) => response,
        Ok(Err(error)) => encode_error(&error),
        Err(_) => encode_error(&BatchError::internal(
            "panic caught at the compact transition batch boundary",
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(state: CompactState, pair: (u8, u8), action: u8, flags: u16) -> Vec<u8> {
        let mut result = Vec::new();
        result.extend_from_slice(REQUEST_MAGIC);
        write_u16(&mut result, ABI_VERSION);
        write_u16(&mut result, SCHEMA_MINOR);
        write_u16(&mut result, flags);
        write_u16(&mut result, REQUEST_RECORD_BYTES as u16);
        write_u32(&mut result, 1);
        write_u32(&mut result, REQUEST_RECORD_BYTES as u32);
        write_u32(&mut result, 0);
        result.extend_from_slice(&state.to_bytes());
        result.push(pair.0);
        result.push(pair.1);
        result.push(action);
        result
    }

    fn empty_state() -> CompactState {
        CompactState::from_parts([0; PLANE_COUNT], false, false, 0, 0).expect("valid empty state")
    }

    #[test]
    fn deterministic_success_omits_timing_by_default() {
        let input = request(
            empty_state(),
            (1, 2),
            7,
            FLAG_CAPTURE_TRACE | FLAG_INCLUDE_ACTIONS,
        );

        let first = guarded_execute(&input);
        let second = guarded_execute(&input);

        assert_eq!(&first[..4], SUCCESS_MAGIC);
        assert_eq!(first, second);
        assert_eq!(read_u32(&first, 12, "record count").expect("header"), 1);
        assert!(first[24..48].iter().all(|value| *value == 0));
    }

    #[test]
    fn invalid_action_returns_typed_error_with_record_index() {
        let input = request(empty_state(), (1, 2), ACTION_COUNT as u8, 0);

        let response = guarded_execute(&input);

        assert_eq!(&response[..4], ERROR_MAGIC);
        assert_eq!(read_u16(&response, 8, "error code").expect("header"), 1);
        assert_eq!(read_u32(&response, 12, "record index").expect("header"), 0);
    }

    #[test]
    fn score_overflow_returns_typed_error_instead_of_panicking() {
        let mut planes = [0_u128; PLANE_COUNT];
        planes[0] = 1_u128 << 1 | 1_u128 << 7;
        let state = CompactState::from_parts(planes, false, false, u64::MAX - 39, 0)
            .expect("valid near-overflow state");
        let input = request(state, (1, 1), 7, 0);

        let response = guarded_execute(&input);

        assert_eq!(&response[..4], ERROR_MAGIC);
        assert_eq!(read_u16(&response, 8, "error code").expect("header"), 2);
    }

    #[test]
    fn malformed_length_is_rejected_without_index() {
        let mut input = request(empty_state(), (1, 2), 7, 0);
        input.pop();

        let response = guarded_execute(&input);

        assert_eq!(&response[..4], ERROR_MAGIC);
        assert_eq!(
            read_u32(&response, 12, "record index").expect("header"),
            u32::MAX
        );
    }
}
