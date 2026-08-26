use std::collections::BTreeMap;
use std::hint::black_box;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::time::{Duration, Instant};

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyModule};

const MAGIC: &[u8; 4] = b"PDCN";
const ABI_VERSION: u16 = 1;
const SCHEMA_MAJOR: u16 = 1;
const SCHEMA_MINOR: u16 = 0;
const HEADER_BYTES: usize = 32;
const TLV_BYTES: usize = 8;
const BYTE_ORDER_LITTLE: u8 = 1;
const REQUEST_KIND: u8 = 1;
const ERROR_KIND: u8 = 3;
const CAPABILITIES_KIND: u8 = 4;
const REQUIRED_TAG: u16 = 0x8000;
const MAX_REQUEST_BYTES: usize = 16 * 1024 * 1024;
const MAX_RESPONSE_BYTES: usize = 16 * 1024 * 1024;
const MAX_SECTIONS: u16 = 64;
const MAX_STRING_BYTES: usize = 4_096;
const MAX_KNOWN_PAIRS: u16 = 256;

const REQUEST_ROOT_STATE_TAG: u16 = 0x8001;
const REQUEST_KNOWN_PAIRS_TAG: u16 = 0x8002;
const REQUEST_SEARCH_CONFIG_TAG: u16 = 0x8003;
const REQUEST_EVALUATOR_CONFIG_TAG: u16 = 0x8004;
const REQUEST_SCHEMA_IDENTITIES_TAG: u16 = 0x8005;
const REQUEST_EXECUTION_TAG: u16 = 0x8006;
const CAPABILITIES_METADATA_TAG: u16 = 0x8101;
const ERROR_DETAILS_TAG: u16 = 0x8201;

const WIRE_NAME: &str = "puyo.deep_chain_native.envelope.v1";
const REQUEST_SCHEMA_DIGEST: &str =
    "fab9cfdae1b6a88a21fdfd2358df9e6f7276bd543f393ee095f581dd8f01c05e";
const RESULT_SCHEMA_DIGEST: &str =
    "eb94050789560a99296ee574f210c7cbe945f85b953f3b27801d7c9a7f800c0b";
const CHAIN_STRUCTURE_WEIGHT_SCHEMA: &str = "puyo.chain_structure_weights.v1";
const CHAIN_STRUCTURE_FEATURE_SCHEMA: &str = "puyo.chain_structure_features.v1";
const SCHEMA_IDENTITIES: [&str; 7] = [
    "puyo.placement_actions.v1",
    "puyo.compact_search_state.v1",
    "puyo.future_tsumo_sampling.v1",
    "puyo.expected_chain_ranking.v2",
    "puyo.build_main_terminal_score.v1",
    "puyo.deep_chain_builder.diagnostics.v1",
    "puyo.deep_chain_native.result.v1",
];

const SOURCE_REVISION: &str = env!("PUYO_NATIVE_SOURCE_REVISION");
const COMPILER: &str = env!("PUYO_NATIVE_COMPILER");
const BUILD_PROFILE: &str = env!("PUYO_NATIVE_BUILD_PROFILE");
const TARGET: &str = env!("PUYO_NATIVE_TARGET");

#[repr(u16)]
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ErrorCode {
    IncompatibleSchema = 1,
    InvalidInput = 2,
    UnsupportedConfig = 3,
    ResourceExhausted = 4,
    InternalPanic = 5,
    BackendUnavailable = 6,
}

#[derive(Debug)]
struct ContractError {
    code: ErrorCode,
    failing_tag: u16,
    retry_safe: bool,
    message: String,
}

impl ContractError {
    fn invalid(tag: u16, message: impl Into<String>) -> Self {
        Self {
            code: ErrorCode::InvalidInput,
            failing_tag: tag,
            retry_safe: false,
            message: message.into(),
        }
    }

    fn incompatible(tag: u16, message: impl Into<String>) -> Self {
        Self {
            code: ErrorCode::IncompatibleSchema,
            failing_tag: tag,
            retry_safe: true,
            message: message.into(),
        }
    }

    fn unsupported(tag: u16, message: impl Into<String>) -> Self {
        Self {
            code: ErrorCode::UnsupportedConfig,
            failing_tag: tag,
            retry_safe: true,
            message: message.into(),
        }
    }

    fn internal(message: impl Into<String>) -> Self {
        Self {
            code: ErrorCode::InternalPanic,
            failing_tag: 0,
            retry_safe: false,
            message: message.into(),
        }
    }
}

type ContractResult<T> = Result<T, ContractError>;

fn read_u16_at(data: &[u8], offset: usize) -> ContractResult<u16> {
    let value = data
        .get(offset..offset + 2)
        .ok_or_else(|| ContractError::invalid(0, "truncated u16"))?;
    Ok(u16::from_le_bytes([value[0], value[1]]))
}

fn read_u32_at(data: &[u8], offset: usize) -> ContractResult<u32> {
    let value = data
        .get(offset..offset + 4)
        .ok_or_else(|| ContractError::invalid(0, "truncated u32"))?;
    Ok(u32::from_le_bytes(
        value.try_into().expect("four-byte slice"),
    ))
}

fn read_u64_at(data: &[u8], offset: usize) -> ContractResult<u64> {
    let value = data
        .get(offset..offset + 8)
        .ok_or_else(|| ContractError::invalid(0, "truncated u64"))?;
    Ok(u64::from_le_bytes(
        value.try_into().expect("eight-byte slice"),
    ))
}

struct Reader<'a> {
    data: &'a [u8],
    offset: usize,
    tag: u16,
}

impl<'a> Reader<'a> {
    fn new(data: &'a [u8], tag: u16) -> Self {
        Self {
            data,
            offset: 0,
            tag,
        }
    }

    fn take(&mut self, length: usize, name: &str) -> ContractResult<&'a [u8]> {
        let end = self
            .offset
            .checked_add(length)
            .ok_or_else(|| ContractError::invalid(self.tag, format!("{name} overflow")))?;
        let value = self
            .data
            .get(self.offset..end)
            .ok_or_else(|| ContractError::invalid(self.tag, format!("truncated {name}")))?;
        self.offset = end;
        Ok(value)
    }

    fn u8(&mut self, name: &str) -> ContractResult<u8> {
        Ok(self.take(1, name)?[0])
    }

    fn u16(&mut self, name: &str) -> ContractResult<u16> {
        let value = self.take(2, name)?;
        Ok(u16::from_le_bytes([value[0], value[1]]))
    }

    fn u32(&mut self, name: &str) -> ContractResult<u32> {
        let value = self.take(4, name)?;
        Ok(u32::from_le_bytes(
            value.try_into().expect("four-byte slice"),
        ))
    }

    fn u64(&mut self, name: &str) -> ContractResult<u64> {
        let value = self.take(8, name)?;
        Ok(u64::from_le_bytes(
            value.try_into().expect("eight-byte slice"),
        ))
    }

    fn f64(&mut self, name: &str) -> ContractResult<f64> {
        let value = f64::from_le_bytes(self.take(8, name)?.try_into().expect("eight-byte slice"));
        if !value.is_finite() {
            return Err(ContractError::invalid(
                self.tag,
                format!("{name} must be finite"),
            ));
        }
        Ok(value)
    }

    fn string(&mut self, name: &str) -> ContractResult<String> {
        let length = usize::from(self.u16(&format!("{name} length"))?);
        if length == 0 || length > MAX_STRING_BYTES {
            return Err(ContractError::invalid(
                self.tag,
                format!("invalid {name} length"),
            ));
        }
        let value = self.take(length, name)?;
        std::str::from_utf8(value)
            .map(str::to_owned)
            .map_err(|_| ContractError::invalid(self.tag, format!("{name} is not UTF-8")))
    }

    fn finish(self) -> ContractResult<()> {
        if self.offset != self.data.len() {
            return Err(ContractError::invalid(
                self.tag,
                "section contains trailing data",
            ));
        }
        Ok(())
    }
}

#[derive(Default)]
struct Writer {
    data: Vec<u8>,
}

impl Writer {
    fn u8(&mut self, value: u8) {
        self.data.push(value);
    }

    fn u16(&mut self, value: u16) {
        self.data.extend_from_slice(&value.to_le_bytes());
    }

    fn u32(&mut self, value: u32) {
        self.data.extend_from_slice(&value.to_le_bytes());
    }

    fn string(&mut self, value: &str) {
        let mut end = value.len().min(MAX_STRING_BYTES);
        while !value.is_char_boundary(end) {
            end -= 1;
        }
        let bounded = &value[..end];
        self.u16(u16::try_from(bounded.len()).expect("bounded string length"));
        self.data.extend_from_slice(bounded.as_bytes());
    }
}

struct ParsedEnvelope<'a> {
    request_id: u64,
    sections: BTreeMap<u16, &'a [u8]>,
}

fn known_request_tag(tag: u16) -> bool {
    matches!(
        tag,
        REQUEST_ROOT_STATE_TAG
            | REQUEST_KNOWN_PAIRS_TAG
            | REQUEST_SEARCH_CONFIG_TAG
            | REQUEST_EVALUATOR_CONFIG_TAG
            | REQUEST_SCHEMA_IDENTITIES_TAG
            | REQUEST_EXECUTION_TAG
    )
}

fn parse_request_envelope(data: &[u8]) -> ContractResult<ParsedEnvelope<'_>> {
    if data.len() < HEADER_BYTES {
        return Err(ContractError::invalid(
            0,
            "envelope is shorter than its header",
        ));
    }
    if data.len() > MAX_REQUEST_BYTES {
        return Err(ContractError::invalid(
            0,
            "request exceeds the configured limit",
        ));
    }
    if data.get(0..4) != Some(MAGIC.as_slice()) {
        return Err(ContractError::invalid(0, "invalid envelope magic"));
    }
    let schema_major = read_u16_at(data, 4)?;
    let schema_minor = read_u16_at(data, 6)?;
    if schema_major != SCHEMA_MAJOR || schema_minor != SCHEMA_MINOR {
        return Err(ContractError::incompatible(
            0,
            format!("unsupported envelope schema {schema_major}.{schema_minor}"),
        ));
    }
    if data[8] != REQUEST_KIND {
        return Err(ContractError::invalid(0, "expected a request envelope"));
    }
    if data[9] != BYTE_ORDER_LITTLE
        || read_u16_at(data, 10)? != 0
        || read_u32_at(data, 12)? != HEADER_BYTES as u32
        || read_u16_at(data, 22)? != 0
    {
        return Err(ContractError::invalid(
            0,
            "invalid fixed-header control field",
        ));
    }
    let body_bytes = usize::try_from(read_u32_at(data, 16)?)
        .map_err(|_| ContractError::invalid(0, "body length does not fit usize"))?;
    if body_bytes != data.len() - HEADER_BYTES {
        return Err(ContractError::invalid(
            0,
            "body length does not match framing",
        ));
    }
    let section_count = read_u16_at(data, 20)?;
    if section_count > MAX_SECTIONS {
        return Err(ContractError::invalid(0, "too many request sections"));
    }
    let request_id = read_u64_at(data, 24)?;
    let mut sections = BTreeMap::new();
    let mut offset = HEADER_BYTES;
    for _ in 0..section_count {
        let header_end = offset
            .checked_add(TLV_BYTES)
            .ok_or_else(|| ContractError::invalid(0, "section header overflow"))?;
        if header_end > data.len() {
            return Err(ContractError::invalid(0, "truncated section header"));
        }
        let tag = read_u16_at(data, offset)?;
        let version = read_u16_at(data, offset + 2)?;
        let length = usize::try_from(read_u32_at(data, offset + 4)?)
            .map_err(|_| ContractError::invalid(tag, "section length does not fit usize"))?;
        offset = header_end;
        let end = offset
            .checked_add(length)
            .ok_or_else(|| ContractError::invalid(tag, "section length overflow"))?;
        let section = data
            .get(offset..end)
            .ok_or_else(|| ContractError::invalid(tag, "section exceeds envelope"))?;
        offset = end;
        let padding = (8 - (length % 8)) % 8;
        let padding_end = offset
            .checked_add(padding)
            .ok_or_else(|| ContractError::invalid(tag, "padding length overflow"))?;
        let padding_bytes = data
            .get(offset..padding_end)
            .ok_or_else(|| ContractError::invalid(tag, "truncated section padding"))?;
        if padding_bytes.iter().any(|value| *value != 0) {
            return Err(ContractError::invalid(tag, "section padding is not zero"));
        }
        offset = padding_end;
        if !known_request_tag(tag) {
            if tag & REQUIRED_TAG != 0 {
                return Err(ContractError::incompatible(tag, "unknown required section"));
            }
            continue;
        }
        if version != 1 {
            return Err(ContractError::incompatible(
                tag,
                "unsupported request section version",
            ));
        }
        if sections.insert(tag, section).is_some() {
            return Err(ContractError::invalid(tag, "duplicate singleton section"));
        }
    }
    if offset != data.len() {
        return Err(ContractError::invalid(0, "request contains trailing data"));
    }
    for tag in [
        REQUEST_ROOT_STATE_TAG,
        REQUEST_KNOWN_PAIRS_TAG,
        REQUEST_SEARCH_CONFIG_TAG,
        REQUEST_EVALUATOR_CONFIG_TAG,
        REQUEST_SCHEMA_IDENTITIES_TAG,
        REQUEST_EXECUTION_TAG,
    ] {
        if !sections.contains_key(&tag) {
            return Err(ContractError::incompatible(tag, "missing required section"));
        }
    }
    Ok(ParsedEnvelope {
        request_id,
        sections,
    })
}

fn validate_state(data: &[u8]) -> ContractResult<()> {
    if data.len() != 87 || data.get(0..4) != Some(b"CSK1") {
        return Err(ContractError::invalid(
            REQUEST_ROOT_STATE_TAG,
            "invalid compact state framing",
        ));
    }
    let mut occupied = 0_u128;
    let mut offset = 4;
    for _ in 0..6 {
        let mut bytes = [0_u8; 16];
        bytes[..11].copy_from_slice(&data[offset..offset + 11]);
        let plane = u128::from_le_bytes(bytes);
        if plane >> 84 != 0 {
            return Err(ContractError::invalid(
                REQUEST_ROOT_STATE_TAG,
                "plane contains cells above row 14",
            ));
        }
        if occupied & plane != 0 {
            return Err(ContractError::invalid(
                REQUEST_ROOT_STATE_TAG,
                "compact state planes overlap",
            ));
        }
        occupied |= plane;
        offset += 11;
    }
    let flags = data[offset];
    if flags & !0x3 != 0 {
        return Err(ContractError::invalid(
            REQUEST_ROOT_STATE_TAG,
            "compact state contains unknown flags",
        ));
    }
    let score = read_u64_at(data, offset + 1)?;
    let last_chain_end_score = read_u64_at(data, offset + 9)?;
    if last_chain_end_score > score {
        return Err(ContractError::invalid(
            REQUEST_ROOT_STATE_TAG,
            "last chain end score exceeds score",
        ));
    }
    Ok(())
}

fn validate_known_pairs(data: &[u8]) -> ContractResult<u16> {
    let mut reader = Reader::new(data, REQUEST_KNOWN_PAIRS_TAG);
    let count = reader.u16("known pair count")?;
    if count == 0 || count > MAX_KNOWN_PAIRS {
        return Err(ContractError::invalid(
            REQUEST_KNOWN_PAIRS_TAG,
            "known pair count is outside its limit",
        ));
    }
    for _ in 0..count {
        for name in ["axis color", "child color"] {
            if !(1..=5).contains(&reader.u8(name)?) {
                return Err(ContractError::invalid(
                    REQUEST_KNOWN_PAIRS_TAG,
                    "known pair contains an invalid color ID",
                ));
            }
        }
    }
    reader.finish()?;
    Ok(count)
}

struct SearchCoordinates {
    pair_cursor: u16,
    scenario_cursor: u16,
    scenarios: u8,
}

fn validate_search(data: &[u8]) -> ContractResult<SearchCoordinates> {
    let mut reader = Reader::new(data, REQUEST_SEARCH_CONFIG_TAG);
    let depth = reader.u16("search depth")?;
    let width = reader.u16("search width")?;
    let scenarios = reader.u8("scenario count")?;
    let minimum_chain_count = reader.u8("minimum chain count")?;
    let terminal_fire_chain_count = reader.u16("terminal fire chain count")?;
    let max_expanded_nodes = reader.u32("max expanded nodes")?;
    let root_survivor_quota = reader.u16("root survivor quota")?;
    let pair_cursor = reader.u16("pair cursor")?;
    let scenario_cursor = reader.u16("scenario cursor")?;
    let seed_present = reader.u8("seed present")?;
    let winning_present = reader.u8("winning score present")?;
    let transposition_table = reader.u8("transposition table")?;
    let reserved = reader.u8("search reserved")?;
    let decision_seed = reader.u64("decision seed")?;
    let winning_score = reader.u64("winning score")?;
    let premature_penalty = reader.f64("premature penalty")?;
    let sampling_mode = reader.string("future sampling mode")?;
    let terminal_rule = reader.string("terminal fire rule")?;
    let fire_context = reader.string("fire context")?;
    let _profile_name = reader.string("profile name")?;
    let _profile_version = reader.string("profile version")?;
    let _config_version = reader.string("config version")?;
    reader.finish()?;
    if depth == 0
        || width == 0
        || !(1..=6).contains(&scenarios)
        || minimum_chain_count == 0
        || terminal_fire_chain_count == 0
        || max_expanded_nodes == 0
        || root_survivor_quota == 0
    {
        return Err(ContractError::invalid(
            REQUEST_SEARCH_CONFIG_TAG,
            "search limits must be positive and bounded",
        ));
    }
    if !matches!(seed_present, 0 | 1)
        || !matches!(winning_present, 0 | 1)
        || !matches!(transposition_table, 0 | 1)
        || reserved != 0
        || (seed_present == 0 && decision_seed != 0)
        || (winning_present == 0 && winning_score != 0)
        || (winning_present == 1 && winning_score == 0)
        || premature_penalty < 0.0
    {
        return Err(ContractError::invalid(
            REQUEST_SEARCH_CONFIG_TAG,
            "invalid search option or control value",
        ));
    }
    if !matches!(
        sampling_mode.as_str(),
        "seeded-authoritative" | "legacy-fixed-six"
    ) {
        return Err(ContractError::unsupported(
            REQUEST_SEARCH_CONFIG_TAG,
            "unsupported future sampling mode",
        ));
    }
    if !matches!(terminal_rule.as_str(), "continue" | "record_and_stop")
        || !matches!(fire_context.as_str(), "safe_build" | "forced_safety")
    {
        return Err(ContractError::unsupported(
            REQUEST_SEARCH_CONFIG_TAG,
            "unsupported terminal-fire configuration",
        ));
    }
    Ok(SearchCoordinates {
        pair_cursor,
        scenario_cursor,
        scenarios,
    })
}

fn validate_evaluator(data: &[u8]) -> ContractResult<()> {
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
    if schema != CHAIN_STRUCTURE_WEIGHT_SCHEMA || feature != CHAIN_STRUCTURE_FEATURE_SCHEMA {
        return Err(ContractError::incompatible(
            REQUEST_EVALUATOR_CONFIG_TAG,
            "evaluator schema identity does not match",
        ));
    }
    if max_added_puyos == 0
        || max_added_puyos > 3
        || max_pattern_nodes == 0
        || max_resolution_nodes == 0
        || max_candidates == 0
        || weight_count != 24
        || reserved != 0
    {
        return Err(ContractError::invalid(
            REQUEST_EVALUATOR_CONFIG_TAG,
            "invalid evaluator budget or weight layout",
        ));
    }
    let reward_indices = [0_usize, 1, 4, 5, 6, 7, 8, 9, 10, 11, 12];
    for index in 0..24_usize {
        let value = reader.f64("evaluator weight")?;
        let reward = reward_indices.contains(&index);
        if (reward && value < 0.0) || (!reward && value > 0.0) {
            return Err(ContractError::invalid(
                REQUEST_EVALUATOR_CONFIG_TAG,
                "evaluator weight sign is invalid",
            ));
        }
    }
    if reader.f64("fatal score")? >= 0.0 {
        return Err(ContractError::invalid(
            REQUEST_EVALUATOR_CONFIG_TAG,
            "fatal score must be negative",
        ));
    }
    reader.finish()
}

fn validate_schema_identities(data: &[u8]) -> ContractResult<()> {
    let mut reader = Reader::new(data, REQUEST_SCHEMA_IDENTITIES_TAG);
    let count = reader.u16("schema identity count")?;
    let reserved = reader.u16("schema reserved")?;
    if usize::from(count) != SCHEMA_IDENTITIES.len() || reserved != 0 {
        return Err(ContractError::incompatible(
            REQUEST_SCHEMA_IDENTITIES_TAG,
            "schema identity layout does not match",
        ));
    }
    for expected in SCHEMA_IDENTITIES {
        if reader.string("schema identity")? != expected {
            return Err(ContractError::incompatible(
                REQUEST_SCHEMA_IDENTITIES_TAG,
                "request schema identity does not match",
            ));
        }
    }
    let _config_digest = reader.take(32, "config digest")?;
    reader.finish()
}

fn validate_execution(data: &[u8]) -> ContractResult<()> {
    let mut reader = Reader::new(data, REQUEST_EXECUTION_TAG);
    let mode = reader.string("execution mode")?;
    let detail_flags = reader.u32("response detail flags")?;
    let max_response_bytes = reader.u32("maximum response bytes")?;
    let python_callbacks = reader.u8("Python callback flag")?;
    let scalar_fallback = reader.u8("scalar fallback requirement")?;
    let reserved = reader.u16("execution reserved")?;
    reader.finish()?;
    if mode != "oracle-1" {
        return Err(ContractError::unsupported(
            REQUEST_EXECUTION_TAG,
            "PUYO-199 supports only oracle-1 execution",
        ));
    }
    if detail_flags & !0x7 != 0
        || max_response_bytes == 0
        || usize::try_from(max_response_bytes).unwrap_or(usize::MAX) > MAX_RESPONSE_BYTES
        || python_callbacks != 0
        || scalar_fallback != 1
        || reserved != 0
    {
        return Err(ContractError::invalid(
            REQUEST_EXECUTION_TAG,
            "invalid execution boundary control",
        ));
    }
    Ok(())
}

fn validate_request(data: &[u8]) -> ContractResult<u64> {
    let envelope = parse_request_envelope(data)?;
    validate_state(envelope.sections[&REQUEST_ROOT_STATE_TAG])?;
    let pair_count = validate_known_pairs(envelope.sections[&REQUEST_KNOWN_PAIRS_TAG])?;
    let coordinates = validate_search(envelope.sections[&REQUEST_SEARCH_CONFIG_TAG])?;
    if coordinates.pair_cursor >= pair_count
        || coordinates.scenario_cursor >= u16::from(coordinates.scenarios)
    {
        return Err(ContractError::invalid(
            REQUEST_SEARCH_CONFIG_TAG,
            "pair or scenario cursor is outside its input",
        ));
    }
    validate_evaluator(envelope.sections[&REQUEST_EVALUATOR_CONFIG_TAG])?;
    validate_schema_identities(envelope.sections[&REQUEST_SCHEMA_IDENTITIES_TAG])?;
    validate_execution(envelope.sections[&REQUEST_EXECUTION_TAG])?;
    Ok(envelope.request_id)
}

fn encode_envelope(kind: u8, request_id: u64, sections: &[(u16, u16, Vec<u8>)]) -> Vec<u8> {
    let mut body = Vec::new();
    for (tag, version, payload) in sections {
        body.extend_from_slice(&tag.to_le_bytes());
        body.extend_from_slice(&version.to_le_bytes());
        body.extend_from_slice(
            &u32::try_from(payload.len())
                .expect("bounded section length")
                .to_le_bytes(),
        );
        body.extend_from_slice(payload);
        body.resize(body.len() + (8 - (payload.len() % 8)) % 8, 0);
    }
    let mut result = Vec::with_capacity(HEADER_BYTES + body.len());
    result.extend_from_slice(MAGIC);
    result.extend_from_slice(&SCHEMA_MAJOR.to_le_bytes());
    result.extend_from_slice(&SCHEMA_MINOR.to_le_bytes());
    result.push(kind);
    result.push(BYTE_ORDER_LITTLE);
    result.extend_from_slice(&0_u16.to_le_bytes());
    result.extend_from_slice(&(HEADER_BYTES as u32).to_le_bytes());
    result.extend_from_slice(
        &u32::try_from(body.len())
            .expect("bounded envelope body")
            .to_le_bytes(),
    );
    result.extend_from_slice(
        &u16::try_from(sections.len())
            .expect("bounded section count")
            .to_le_bytes(),
    );
    result.extend_from_slice(&0_u16.to_le_bytes());
    result.extend_from_slice(&request_id.to_le_bytes());
    result.extend_from_slice(&body);
    result
}

fn error_response(request_id: u64, error: &ContractError) -> Vec<u8> {
    let mut payload = Writer::default();
    payload.u16(error.code as u16);
    payload.u16(error.failing_tag);
    payload.u8(u8::from(error.retry_safe));
    payload.data.extend_from_slice(&[0, 0, 0]);
    payload.string(&error.message);
    payload.string(SOURCE_REVISION);
    payload.string(BUILD_PROFILE);
    encode_envelope(
        ERROR_KIND,
        request_id,
        &[(ERROR_DETAILS_TAG, 1, payload.data)],
    )
}

fn request_id_if_present(data: &[u8]) -> u64 {
    read_u64_at(data, 24).unwrap_or(0)
}

fn guarded_contract_response<F>(request_id: u64, operation: F) -> Vec<u8>
where
    F: FnOnce() -> ContractResult<u64>,
{
    match catch_unwind(AssertUnwindSafe(operation)) {
        Ok(Ok(validated_request_id)) => error_response(
            validated_request_id,
            &ContractError::unsupported(
                0,
                "native search kernels are reserved for PUYO-200 through PUYO-202",
            ),
        ),
        Ok(Err(error)) => error_response(request_id, &error),
        Err(_) => error_response(
            request_id,
            &ContractError::internal("panic caught at the native decision boundary"),
        ),
    }
}

fn guarded_decide(data: &[u8]) -> Vec<u8> {
    let request_id = request_id_if_present(data);
    guarded_contract_response(request_id, || validate_request(data))
}

fn cpu_features() -> String {
    let mut features = vec!["scalar"];
    #[cfg(target_arch = "x86_64")]
    {
        if std::arch::is_x86_feature_detected!("sse2") {
            features.push("sse2");
        }
        if std::arch::is_x86_feature_detected!("ssse3") {
            features.push("ssse3");
        }
        if std::arch::is_x86_feature_detected!("sse4.1") {
            features.push("sse4.1");
        }
        if std::arch::is_x86_feature_detected!("avx2") {
            features.push("avx2");
        }
    }
    features.join(",")
}

fn capabilities_payload() -> Vec<u8> {
    let strings = [
        WIRE_NAME.to_owned(),
        REQUEST_SCHEMA_DIGEST.to_owned(),
        RESULT_SCHEMA_DIGEST.to_owned(),
        env!("CARGO_PKG_VERSION").to_owned(),
        SOURCE_REVISION.to_owned(),
        COMPILER.to_owned(),
        BUILD_PROFILE.to_owned(),
        TARGET.to_owned(),
        "cp312".to_owned(),
        "scalar".to_owned(),
        cpu_features(),
        "oracle-1".to_owned(),
    ];
    let mut payload = Writer::default();
    payload.u16(ABI_VERSION);
    payload.u16(SCHEMA_MAJOR);
    payload.u16(SCHEMA_MINOR);
    payload.u16(SCHEMA_MINOR);
    payload.u32(MAX_REQUEST_BYTES as u32);
    payload.u32(MAX_RESPONSE_BYTES as u32);
    payload.u16(MAX_SECTIONS);
    payload.u8(1);
    payload.u8(1);
    payload.u8(0);
    payload.u8(1);
    payload.u16(1);
    payload.u16(strings.len() as u16);
    for value in strings {
        payload.string(&value);
    }
    payload.data
}

#[pyfunction]
fn capabilities(py: Python<'_>) -> Py<PyBytes> {
    let encoded = encode_envelope(
        CAPABILITIES_KIND,
        0,
        &[(CAPABILITIES_METADATA_TAG, 1, capabilities_payload())],
    );
    PyBytes::new(py, &encoded).unbind()
}

#[pyfunction]
fn decide(py: Python<'_>, request: &[u8]) -> Py<PyBytes> {
    let owned = request.to_vec();
    let response = py.detach(move || guarded_decide(&owned));
    PyBytes::new(py, &response).unbind()
}

#[pyfunction]
fn _round_trip_request(py: Python<'_>, request: &[u8]) -> PyResult<Py<PyBytes>> {
    let owned = request.to_vec();
    let validated = py.detach(move || {
        validate_request(&owned)
            .map(|_| owned)
            .map_err(|error| error.message)
    });
    match validated {
        Ok(value) => Ok(PyBytes::new(py, &value).unbind()),
        Err(message) => Err(PyValueError::new_err(message)),
    }
}

#[pyfunction]
fn _gil_probe(py: Python<'_>, milliseconds: u64) -> PyResult<u64> {
    if !(1..=2_000).contains(&milliseconds) {
        return Err(PyValueError::new_err(
            "GIL probe duration must be between 1 and 2000 milliseconds",
        ));
    }
    let iterations = py.detach(move || {
        let deadline = Instant::now() + Duration::from_millis(milliseconds);
        let mut iterations = 0_u64;
        let mut value = 0x9e37_79b9_7f4a_7c15_u64;
        while Instant::now() < deadline {
            value = value.rotate_left(7) ^ iterations.wrapping_mul(0x100_0000_01b3);
            iterations = iterations.wrapping_add(1);
            black_box(value);
        }
        iterations
    });
    Ok(iterations)
}

#[pymodule]
fn _puyo_deep_chain_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(capabilities, module)?)?;
    module.add_function(wrap_pyfunction!(decide, module)?)?;
    module.add_function(wrap_pyfunction!(_round_trip_request, module)?)?;
    module.add_function(wrap_pyfunction!(_gil_probe, module)?)?;
    module.add("ABI_VERSION", ABI_VERSION)?;
    module.add("SCHEMA_MAJOR", SCHEMA_MAJOR)?;
    module.add("SCHEMA_MINOR", SCHEMA_MINOR)?;
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn state_validator_rejects_overlap_and_score_regression() {
        let mut state = vec![0_u8; 87];
        state[..4].copy_from_slice(b"CSK1");
        state[4] = 1;
        state[15] = 1;
        let overlap = validate_state(&state).expect_err("overlap must fail");
        assert_eq!(overlap.code, ErrorCode::InvalidInput);

        state[15] = 0;
        state[71..79].copy_from_slice(&1_u64.to_le_bytes());
        state[79..87].copy_from_slice(&2_u64.to_le_bytes());
        let score = validate_state(&state).expect_err("score regression must fail");
        assert_eq!(score.code, ErrorCode::InvalidInput);
    }

    #[test]
    fn guarded_boundary_converts_malformed_input_to_error_envelope() {
        let response = guarded_decide(b"bad");
        assert_eq!(response[8], ERROR_KIND);
        assert_eq!(
            u16::from_le_bytes([response[40], response[41]]),
            ErrorCode::InvalidInput as u16
        );
    }

    #[test]
    fn internal_error_is_stable_and_never_resource_exhausted() {
        let error = ContractError::internal("caught");
        let response = error_response(9, &error);
        assert_eq!(read_u64_at(&response, 24).expect("request ID"), 9);
        assert_eq!(
            u16::from_le_bytes([response[40], response[41]]),
            ErrorCode::InternalPanic as u16
        );
        assert_ne!(
            u16::from_le_bytes([response[40], response[41]]),
            ErrorCode::ResourceExhausted as u16
        );
        assert_ne!(
            u16::from_le_bytes([response[40], response[41]]),
            ErrorCode::BackendUnavailable as u16
        );
    }

    #[test]
    fn guarded_boundary_catches_panics_as_internal_errors() {
        let response = guarded_contract_response(41, || panic!("probe panic"));
        assert_eq!(response[8], ERROR_KIND);
        assert_eq!(read_u64_at(&response, 24).expect("request ID"), 41);
        assert_eq!(
            u16::from_le_bytes([response[40], response[41]]),
            ErrorCode::InternalPanic as u16
        );
    }
}
