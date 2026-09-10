# Parent independent review — PUYO-236 four patterns

This file is owned by the parent agent. It records required checks, not successful results.

## Before the 240-run measurement

- [x] Four immutable, clean source HEADs; baseline matches 73ab4e8; source tree differences limited to intended experimental features and necessary validation fixtures.
- [x] only240 and only242 runtime algorithm equivalence to the prior measured trials; no historical artifact rewriting.
- [x] both composition preserves 240 guard eligibility and ranking precedence, winning/forced protection, 242 tracker/representative semantics, and unique request/evidence identity.
- [x] Same effective configuration, evaluator file and semantic digest, runtime dependency versions, native release mode and machine; isolated wheel/build/venv per arm.
- [x] Frozen runner/manifest hashes and balanced sequential order; failed attempts are not valid quality or latency samples; restart does not silently overwrite evidence.
- [x] Related Python/Rust and combined-case tests; prior eight-seed individual-arm action/quality reproduction; separate GUI automated scope and human-pending status.

## After measurement

- [x] Exactly 60 valid identities per arm, each seed123–152 repeated twice, 40 placements or verified legitimate game over. No duplicates, missing or imputed results.
- [x] Raw run SHA/identity/source/build/config checks, request/result identities, deterministic repeat action/plan/trajectory digests, parity/private/fallback and search accounting diagnostics.
- [x] Independent recomputation of the four-arm scoreboard from raw output, using 30 unique seeds for quality and both repeats for latency, with denominators explicit.
- [x] Target success, clean success, mean/median maximum actual chain, premature counts, game-over/no-fire and per-seed improvements/regressions are separate metrics. Mean-chain percentage is not labelled a general quality percentage.
- [x] Decision latency and complete-run elapsed time reported separately, paired comparisons with baseline, p95 method stated, actual nodes/placements/RSS and temporal order available.
- [x] Previous selected eight seeds separated from the 22 additional seeds. Two repeats do not double the independent sample count; no population-level certainty from the fixed 30-seed set.
- [x] Absolute gates retained as measured PASS/FAIL/pending. Adoption stays undecided regardless of scores. New human GUI QA remains pending unless actually observed.
- [ ] Japanese evidence-only PR targets integration, no reviewers; inspect actual diff, CI scope/results and artifact checksums. Do not merge or translate as an adopted PR.
- [ ] Parent consolidates one Jira session comment and final body/handoff, preserves all prior artifacts/user files, records actual completion separately from user choice/adoption.

Premeasurement source/build correspondence evidence: `parent-preparation-v3-manifest-audit.json` (all4 pass). Parent read raw fixed evidence in v2 and protocol corrections in bcc8fdb. Final v3 preflight and old-eight-seed reproduction remain pending; checked items above do not authorize starting the full measurement by themselves.

Superseding completion: `parent-preparation-v5-manifest-audit.json` and `parent-preparation-v5-result-audit.json` both PASS. Final runner29dc88e reviewed.24 reproduction runs/957 decisions independently verified;48 fixed cases,120 private counterfactual seed checks and4cold/warm diagnostics passed. Parent authorized full240-run launch at13:08UTC; adoption remains undecided and new human GUI QA pending.

Postmeasurement completion: `parent-measurement-raw-audit.json`, `parent-postmeasurement-manifest-audit.json` and `parent-final-comparison-audit.json` PASS. Full240 runs/9592 decisions independently audited, all120 repeat pairs agree, summary quality/timing/nodes/RSS/per-seed results match,240 process command/raw-SHA/schedule receipts agree with the balanced declaration and are non-overlapping at saved second resolution. Full measurement exited0 without retries. PR/CI and final Jira review are still pending at this entry.
