# PUYO-84 UI regression QA

PUYO-84 validates that the integrated launcher, realtime match view, plan overlay, replay diagnostics, and lineage viewer remain usable after the PUYO-57 UI work.

## Automated smoke

Run the full test suite:

```bash
python3 -m unittest discover -s tests -q
```

Run the integrated dummy-video UI smoke and inspect the JSON report:

```bash
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -m eval.ui_regression_smoke \
  --result-json /tmp/puyo-84-ui-regression-smoke.json \
  --viewer-json /tmp/puyo-84-model-viewer-report.json \
  --viewer-markdown /tmp/puyo-84-model-viewer-report.md

python3 -m json.tool /tmp/puyo-84-ui-regression-smoke.json
```

Expected result:

- `schema_version` is `puyo.ui_regression_smoke.v1`.
- `passed` is `true`.
- `launcher_navigation` renders the launcher home screen and validates play, spectate, arena, training, and models command generation.
- `realtime_match_plan_overlay` advances ticks, records policy decisions, keeps plan overlay enabled, and reports `average_frame_ms` at or below `max_frame_ms`.
- `model_viewer_replay_lineage` writes the JSON and Markdown viewer reports.

## Manual GUI checklist

Launch the unified UI:

```bash
python3 main.py
```

Verify:

- Home navigation reaches `対戦`, `観戦`, `評価`, `学習`, and `モデル`.
- `Enter` opens a workflow, `Esc` returns to home, and `Q` exits from home.
- Settings mode opens from each workflow, arrow keys move focus, left/right changes values, and paging reaches later realtime fields.
- Invalid checkpoint or missing training config paths show an error before a process starts.
- `Run` starts one job, a second job is blocked while it is running, and `Stop` terminates the running job.
- Long command text and path values are truncated or wrapped without overlapping adjacent UI text.

Launch realtime spectate directly:

```bash
python3 -m eval.realtime_versus_ui \
  --policy-a first --policy-b random \
  --seed 57 --max-ticks 600 --start-paused
```

Verify:

- The first frame appears paused at tick 0.
- `P` toggles pause, `N` steps while paused, `R` resets to the same seed, and `Esc` or `X` exits.
- `[` / `]` or `-` / `+` changes speed without layout overlap.
- `O` toggles plan overlay for both players.
- Realtime diagnostics show input, plan, event, and deadline labels for both players.

Open the model / replay / lineage viewer from the launcher or run:

```bash
python3 -m eval.model_viewer \
  --replay tests/fixtures/realtime_replay_seed123.json \
  --lineage-root docs/benchmarks \
  --max-frames 1 \
  --report-json /tmp/puyo-84-model-viewer-report.json \
  --report-markdown /tmp/puyo-84-model-viewer-report.md
```

Verify:

- Replay timeline displays seed, tick, hash, and plan ids.
- Left/right seeks replay entries, space toggles playback, `+` / `-` changes playback stride, and `b` toggles bookmarks.
- Lineage panel renders runs, checkpoints, nodes, edges, and selected node details.
- Generated reports exist at the requested JSON and Markdown paths.
