# PUYO-199 native extension build and boundary contract

- Status: implemented foundation; production adoption remains blocked
- Decision source: [PUYO-198 native boundary ADR](puyo-198-deep-chain-native-boundary.md)
- Native module: `_puyo_deep_chain_native`
- Rust crate: `native/deep_chain_native`
- Python adapter: `agents.deep_chain_native.NativeDeepChainBackend`
- Wire contract: `puyo.deep_chain_native.envelope.v1`

## Scope and handoff

PUYO-199 implements the reproducible wheel, capability negotiation, binary
request/result framing, full request validation, strict Python adapter, stable
error mapping, scalar CPU baseline, and GIL-detached decision entrypoint. It
does not port transition, evaluator, quiescence, beam, or aggregation logic.

A valid `decide()` request therefore returns the typed
`UNSUPPORTED_CONFIG` error. This is intentional: PUYO-200 through PUYO-202 add
the kernels inside this crate and must not create a second extension, state
schema, binding, or per-node FFI path. No partial result is returned as a
success.

The production `deep_chain_builder` remains on its existing Python path. This
task adds no backend selector and changes no action semantics.

## Supported build

| Item | Locked value |
| --- | --- |
| OS / architecture | Linux x86_64 |
| Python | CPython 3.12, version-specific `cp312` ABI |
| Wheel | `manylinux_2_28_x86_64` |
| Rust | 1.98.0, edition 2024 |
| PyO3 | exactly 0.29.0, `extension-module` |
| maturin | exactly 1.14.1 |
| portable linker | Zig 0.15.2 through `ziglang` |
| Runtime thread mode | `oracle-1`, one native worker |
| CPU path | portable scalar baseline; runtime features are reported only |

`rust-toolchain.toml` uses the minimal profile plus `rustfmt` and `clippy`.
`Cargo.lock` locks the full Rust graph. The release profile uses optimization
level 3, fat LTO, one codegen unit, no incremental build, checked overflow,
unwind panics, and line tables. It never sets `target-cpu=native`.

Zig supplies the glibc 2.28 link baseline. A native build on a newer host is
not relabeled without verification: maturin audits the resulting library and
fails the command if the requested manylinux contract is not met.
The build command remaps Rust-owned checkout, Cargo, and rustup paths and fixes
`SOURCE_DATE_EPOCH` to the source commit time. Zig's retained libunwind and libc
line tables can still encode the local Zig installation path, so the wheel
SHA-256 is recorded as per-build provenance rather than asserted to be
bit-for-bit identical across checkout roots.

## Clean build and install

From a clean checkout, run one command:

```bash
./scripts/build_deep_chain_native.sh
```

The script:

1. verifies Linux x86_64 and CPython 3.12;
2. creates `.venv` when it is absent;
3. bootstraps rustup when needed, then lets `rust-toolchain.toml` install the
   exact toolchain and components;
4. installs the pinned maturin and Zig build requirements;
5. builds with `--release --locked --zig --compatibility manylinux_2_28`;
6. installs the single `cp312-cp312-manylinux_2_28_x86_64` wheel;
7. imports the extension, verifies its ABI constants, and prints the wheel
   SHA-256.

Set `PUYO_NATIVE_PYTHON` only when the CPython 3.12 interpreter is in a
different virtual environment. The script does not accept a debug build as a
canonical artifact.

## Public call boundary

The extension exposes two production-facing calls:

```text
capabilities() -> bytes  # kind 4 capability envelope, cached by the adapter
decide(request: bytes) -> bytes  # kind 2 success or kind 3 error envelope
```

`NativeDeepChainBackend` decodes and validates capabilities at construction.
It then encodes exactly one request and invokes exactly one native `decide`
call for a decision. There is no callback, Python object handle, simulator
pointer, or private future-queue field in the request.

`_round_trip_request` and `_gil_probe` are underscore-prefixed QA probes. They
are not search APIs. The former validates all request sections in Rust and
returns the unchanged canonical bytes; the latter proves that a blocking
native computation can run while another Python thread advances.

## Envelope framing

Every integer is little endian. The fixed header remains the 32-byte layout
accepted in PUYO-198:

| Offset | Type | Field | v1 rule |
| ---: | --- | --- | --- |
| 0 | `u8[4]` | magic | `PDCN` |
| 4 | `u16` | schema major | 1 |
| 6 | `u16` | schema minor | 0 |
| 8 | `u8` | kind | 1 request, 2 success, 3 error, 4 capabilities |
| 9 | `u8` | byte order | 1, little endian |
| 10 | `u16` | flags | zero |
| 12 | `u32` | header bytes | 32 |
| 16 | `u32` | body bytes | excludes header |
| 20 | `u16` | section count | at most 64 |
| 22 | `u16` | reserved | zero |
| 24 | `u64` | request ID | echoed unchanged |

The body contains 8-byte-aligned `tag: u16`, `version: u16`, `length: u32`
TLVs. Padding is mandatory zero. Duplicate singleton tags, truncation,
trailing bytes, non-zero padding, checked-arithmetic overflow, unknown
required tags, or unsupported versions fail closed.

### Request tags

| Tag | Section | Required v1 content |
| ---: | --- | --- |
| `0x8001` | root state | exact 87-byte `CompactSearchState.to_bytes()` |
| `0x8002` | known pairs | bounded count and contract-local color IDs 1 through 5 |
| `0x8003` | search | budgets, seed, pair/scenario cursors, profile and search modes |
| `0x8004` | evaluator | three versions, four budgets, 24 ordered `f64` weights, fatal score |
| `0x8005` | identities | action/state/sampling/ranking/terminal/diagnostic/result versions and config SHA-256 |
| `0x8006` | execution | `oracle-1`, result limits, scalar requirement, callback flag fixed false |

State validation covers all six non-overlapping 84-bit planes, both hidden
rows, OJAMA, all-clear/game-over flags, checked `u64` score and
last-chain-end score, and the lifecycle ordering constraint. Pair IDs never
reuse Python enum ordinals.

### Reserved success tags

| Tag | Section |
| ---: | --- |
| `0x8301` | selected action, ranked roots, completion state, deterministic digest |
| `0x8302` | bounded search counters |
| `0x8303` | root/scenario evidence |
| `0x8304` | representative action paths and predicted states |
| `0x8305` | bounded diagnostics |
| `0x8306` | source/build/target/thread/SIMD provenance |

The Python result decoder already rejects action IDs outside the locked 0–21
layout, duplicate ranked roots, malformed flags, missing sections, and a
mismatched request ID. The three variable-record sections start with
`schema_version: u16`, zero `reserved: u16`, `record_count: u32`, and
`body_bytes: u32`; their bounded record bodies are filled by PUYO-202. Later
tasks must fill these sections without changing their ownership or adding
node-level calls.

## Capabilities and provenance

The machine-readable adapter result includes:

- ABI version and supported envelope minor range;
- request and result schema SHA-256 identities;
- maximum request, response, and section counts;
- crate version, Git source revision, rustc version, build profile, target,
  and Python ABI;
- detected CPU features, selected SIMD path, and scalar fallback availability;
- GIL detach support, thread modes, parallel capability, and maximum threads.
- an external wheel SHA-256 provenance hook populated by build/evidence tooling.

The current selected path is always `scalar`, even when AVX2 or other features
are detected. PUYO-200 may add runtime dispatch only if scalar and optimized
paths are differential-identical and the selected path remains in provenance.

A source build records `PUYO_NATIVE_SOURCE_REVISION` when supplied; otherwise
the build script obtains the Git revision and appends `-dirty` for a modified
tree. Distributed source without either records `unknown` rather than
inventing provenance.

## Errors and fallback

| Stable code | Python exception | Retry/fallback rule |
| --- | --- | --- |
| `INCOMPATIBLE_SCHEMA` | `IncompatibleSchemaError` | explicit native raises; noncanonical auto may record and retry |
| `INVALID_INPUT` | `InvalidNativeInputError` | always raises; never fallback |
| `UNSUPPORTED_CONFIG` | `UnsupportedNativeConfigError` | explicit native raises; noncanonical auto may record and retry |
| `RESOURCE_EXHAUSTED` | `NativeResourceExhaustedError` | QA raises; later product auto may record and retry |
| `INTERNAL_PANIC` | `NativeInternalPanicError` | quarantines native instance; no partial success |
| `BACKEND_UNAVAILABLE` | `NativeBackendUnavailableError` | import/build/ABI/CPU failure remains explicit |

The native entrypoint owns its input bytes before detaching, calls no Python
API while detached, catches Rust unwinds, and creates the one response object
only after reattaching. Canonical mode rejects a missing extension, schema
mismatch, unsupported Python ABI, absent scalar path, absent GIL detach,
missing `oracle-1`, or any non-release build. `native_fallback_allowed`
records the PUYO-198 policy but performs no fallback and is always false for
canonical runs.

## Frozen evidence and verification

[`round_trip_manifest.json`](../benchmarks/puyo-199-native-extension-contract/round_trip_manifest.json)
binds every PUYO-198 state/scenario case to its canonical request size and
SHA-256. Python and Rust round-trip the exact request bytes, so state, known
pairs, search/profile cursors, evaluator weights, schema identities, and
config digest are all covered.

Run focused QA:

```bash
cargo fmt --manifest-path native/deep_chain_native/Cargo.toml -- --check
cargo clippy --locked --manifest-path native/deep_chain_native/Cargo.toml -- -D warnings
cargo test --locked --manifest-path native/deep_chain_native/Cargo.toml
.venv/bin/python -m unittest tests.test_deep_chain_native
.venv/bin/ruff check agents/deep_chain_native.py tests/test_deep_chain_native.py
```

The GitHub workflow repeats a clean CPython 3.12 release-wheel build and these
checks on Ubuntu 24.04. Related Python search and builder tests remain required
before merging.

## License and attribution

The new Rust/Python boundary implementation is original project code under
the MIT license stored with the crate. PyO3 and maturin are available under
MIT or Apache-2.0 terms; Zig and the `ziglang` redistribution are MIT licensed.
Their source is used as build/runtime dependencies and no dependency source is
copied into this repository.

Ama remains the representation inspiration recorded by the existing compact
search design and PUYO-198 ADR. No Ama source code was copied or translated in
PUYO-199.
