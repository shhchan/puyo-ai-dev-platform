//! Isolated PUYO-211 feasibility benchmark for reusing placed color slices.
//!
//! This binary is not linked into the native extension.  It compares the
//! current inserted-plane reconstruction with a candidate that reads the
//! already-placed child slices.  The Python investigation runner supplies the
//! frozen quiet workload in a compact binary format.

use std::env;
use std::fs;
use std::hint::black_box;
use std::path::PathBuf;
use std::time::Instant;

const MAGIC: &[u8; 8] = b"P211CP01";
const HEADER_BYTES: usize = 12;
const RECORD_BYTES: usize = 100;
const WIDTH: usize = 6;
const HEIGHT: usize = 14;
const COLUMN_LANE_BITS: usize = 16;

const fn lane_mask(height: usize) -> u128 {
    let mut result = 0_u128;
    let mut x = 0_usize;
    while x < WIDTH {
        result |= ((1_u128 << height) - 1) << (x * COLUMN_LANE_BITS);
        x += 1;
    }
    result
}

const BOARD_MASK: u128 = lane_mask(HEIGHT);

#[derive(Clone, Copy)]
struct Record {
    parent: [u128; 3],
    child: [u128; 3],
    axis_plane: usize,
    child_plane: usize,
    inserted: [u8; 2],
}

#[inline(always)]
fn color_plane(color_bits: &[u128; 3], plane_index: usize) -> u128 {
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

#[inline(never)]
fn current_inserted_planes(record: &Record) -> [u128; 2] {
    let axis_bit = 1_u128 << record.inserted[0];
    let child_bit = 1_u128 << record.inserted[1];
    let axis_plane = color_plane(black_box(&record.parent), record.axis_plane);
    let child_plane = if record.axis_plane == record.child_plane {
        axis_plane
    } else {
        color_plane(black_box(&record.parent), record.child_plane)
    };
    let axis_inserted_plane = axis_plane
        | axis_bit
        | if record.axis_plane == record.child_plane {
            child_bit
        } else {
            0
        };
    [axis_inserted_plane, child_plane | child_bit]
}

#[inline(never)]
fn reused_child_planes(record: &Record) -> [u128; 2] {
    let axis_plane = color_plane(black_box(&record.child), record.axis_plane);
    let child_plane = if record.axis_plane == record.child_plane {
        axis_plane & !(1_u128 << record.inserted[0])
    } else {
        color_plane(black_box(&record.child), record.child_plane)
    };
    [axis_plane, child_plane]
}

#[cfg(target_arch = "x86_64")]
#[inline(always)]
fn cycle_start() -> u64 {
    unsafe {
        core::arch::x86_64::_mm_lfence();
        let value = core::arch::x86_64::_rdtsc();
        core::arch::x86_64::_mm_lfence();
        value
    }
}

#[cfg(target_arch = "x86_64")]
#[inline(always)]
fn cycle_end() -> u64 {
    unsafe {
        core::arch::x86_64::_mm_lfence();
        let value = core::arch::x86_64::_rdtsc();
        core::arch::x86_64::_mm_lfence();
        value
    }
}

#[cfg(not(target_arch = "x86_64"))]
compile_error!("PUYO-211 candidate benchmark requires x86_64 RDTSC evidence");

fn read_u64(bytes: &[u8], offset: usize) -> u64 {
    u64::from_le_bytes(
        bytes[offset..offset + 8]
            .try_into()
            .expect("u64 record field"),
    )
}

fn read_u128(bytes: &[u8], offset: usize) -> u128 {
    u128::from(read_u64(bytes, offset)) | (u128::from(read_u64(bytes, offset + 8)) << 64)
}

fn read_workload(path: &PathBuf) -> Vec<Record> {
    let payload = fs::read(path).expect("candidate workload is readable");
    assert!(
        payload.len() >= HEADER_BYTES,
        "candidate workload header is truncated"
    );
    assert_eq!(&payload[..8], MAGIC, "candidate workload magic changed");
    let count = u32::from_le_bytes(payload[8..12].try_into().expect("record count")) as usize;
    assert_eq!(payload.len(), HEADER_BYTES + count * RECORD_BYTES);
    let mut records = Vec::with_capacity(count);
    for index in 0..count {
        let start = HEADER_BYTES + index * RECORD_BYTES;
        let bytes = &payload[start..start + RECORD_BYTES];
        let parent = [
            read_u128(bytes, 0),
            read_u128(bytes, 16),
            read_u128(bytes, 32),
        ];
        let child = [
            read_u128(bytes, 48),
            read_u128(bytes, 64),
            read_u128(bytes, 80),
        ];
        records.push(Record {
            parent,
            child,
            axis_plane: usize::from(bytes[96]),
            child_plane: usize::from(bytes[97]),
            inserted: [bytes[98], bytes[99]],
        });
    }
    records
}

fn checksum(value: [u128; 2]) -> u64 {
    let combined = value[0] ^ value[1].rotate_left(29);
    combined as u64 ^ (combined >> 64) as u64
}

fn measure(
    records: &[Record],
    repeats: u32,
    implementation: fn(&Record) -> [u128; 2],
) -> (u128, u64, u64) {
    let started_cycles = cycle_start();
    let started = Instant::now();
    let mut digest = 0_u64;
    for _ in 0..repeats {
        for record in records {
            digest ^= checksum(implementation(black_box(record)));
        }
    }
    let elapsed_ns = started.elapsed().as_nanos();
    let cycles = cycle_end().wrapping_sub(started_cycles);
    (elapsed_ns, cycles, black_box(digest))
}

fn semantic_mismatches(records: &[Record]) -> usize {
    records
        .iter()
        .filter(|record| current_inserted_planes(record) != reused_child_planes(record))
        .count()
}

fn parse_usize(value: Option<String>, name: &str) -> usize {
    value
        .unwrap_or_else(|| panic!("missing {name}"))
        .parse()
        .unwrap_or_else(|_| panic!("invalid {name}"))
}

fn main() {
    let mut arguments = env::args().skip(1);
    let mut input = None;
    let mut samples = 40_usize;
    let mut warmup = 5_usize;
    let mut repeats = 128_u32;
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "--input" => input = arguments.next().map(PathBuf::from),
            "--samples" => samples = parse_usize(arguments.next(), "samples"),
            "--warmup" => warmup = parse_usize(arguments.next(), "warmup"),
            "--repeats" => repeats = parse_usize(arguments.next(), "repeats") as u32,
            _ => panic!("unknown argument: {argument}"),
        }
    }
    let input = input.expect("--input is required");
    let records = read_workload(&input);
    assert!(!records.is_empty(), "candidate workload is empty");
    let mismatches = semantic_mismatches(&records);
    println!(
        "metadata\t1\t{}\t{}\t{}\t{}",
        records.len(),
        repeats,
        warmup,
        mismatches
    );
    for _ in 0..warmup {
        black_box(measure(&records, repeats, current_inserted_planes));
        black_box(measure(&records, repeats, reused_child_planes));
    }
    for sample in 0..samples {
        let baseline_first = sample % 2 == 0;
        let (baseline, candidate) = if baseline_first {
            (
                measure(&records, repeats, current_inserted_planes),
                measure(&records, repeats, reused_child_planes),
            )
        } else {
            let candidate = measure(&records, repeats, reused_child_planes);
            let baseline = measure(&records, repeats, current_inserted_planes);
            (baseline, candidate)
        };
        assert_eq!(baseline.2, candidate.2, "candidate checksum differs");
        println!(
            "sample\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
            sample,
            if baseline_first { "AB" } else { "BA" },
            baseline.0,
            baseline.1,
            candidate.0,
            candidate.1,
            baseline.2,
        );
    }
}
