# v1.7.1 Bootstrap Dataset

PUYO-125 の dataset builder は、v1.7.0 の decision diagnostics、State Analyzer scenario、
v1.7.1 planner preview、旧 manager teacher JSON を共通 artifact へ正規化する。

## Build

PUYO-153 の scenario dataset は既定で読み込まれ、全24件が validation splitへ固定される。
追加 source は複数指定できる。

```bash
python3 -m train.v1_7_bootstrap_dataset build \
  --output runs/v1_7_bootstrap_dataset/<dataset-id> \
  --source docs/benchmarks/puyo-v1-7-0-smoke/gui_qa_replay.json
```

生成物:

- `dataset_manifest.json`: source checksum、feature contract、split条件、件数、互換性監査。
- `train.jsonl`: 現行schemaで学習に使えるsample。
- `validation.jsonl`: 固定scenarioとhash splitされたvalidation sample。
- `legacy.jsonl`: 現行featureへ安全に移行できない旧sample。
- `rejected.jsonl`: schema不正、未知tactic、不正actionなどのsample。

```bash
python3 -m train.v1_7_bootstrap_dataset validate \
  runs/v1_7_bootstrap_dataset/<dataset-id>
```

## Compatibility

現行sampleは `puyo.state_analyzer.input.v1` の own/opponentそれぞれについて、
`score_carry`、`all_clear_achieved`、`all_clear_bonus_pending`、
`all_clear_bonus_consumed` を明示的に保持する。

欠落値を `0` / `false` として補完しない。同じdecisionに保存された
`lifecycle_features` などから全欠落値を復元できる場合だけ `migrated` とし、
復元元をsampleとmanifestへ保存する。復元不能な旧sampleは `legacy`、範囲外または
盤面上不正なplacement actionは `rejected` とする。

`dataset_id` とsplitは、builder revision、source checksum、feature contract、split seed、
出力checksumから決定される。wall-clock時刻や新規計測したplanner latencyはdataset identityへ
含めない。

## Smoke Artifact

リポジトリでレビューするsmoke artifactは次のcommandで再生成する。

```bash
python3 -m train.v1_7_bootstrap_dataset build \
  --output docs/benchmarks/puyo-v1-7-1-bootstrap-dataset-smoke \
  --source docs/benchmarks/puyo-v1-7-0-smoke/gui_qa_replay.json
```

このartifactはschema・migration・splitのQA用であり、PUYO-126の本学習datasetとしては扱わない。
