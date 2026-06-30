# PUYO-72 Model Lineage Registry

`train.lineage` は `artifact_manifest.json`、`suite_manifest.json`、`human_session_manifest.json` を走査し、run、checkpoint、
experiment suite、opponent snapshot、arena result の関係をローカル registry として出力する。

## Build

```bash
python3 -m train.lineage \
  --root runs \
  --root runs/experiment_suites \
  --output runs/lineage_registry.json \
  --markdown runs/lineage_report.md
```

`--root` は directory または manifest file を複数指定できる。directory の場合は配下の
`artifact_manifest.json` と `suite_manifest.json` を再帰的に探す。
manifest がない v1.3.0 以前の run についても、`summary.json`、`metadata.json`、
`config.yaml`、`checkpoints/*.pt` から legacy run / checkpoint node を復元する。

## Nodes

| node type | 内容 |
|---|---|
| `suite` | experiment suite manifest |
| `run` | trainer run。summary metrics、seed、git commit、trainer 名を保持 |
| `checkpoint` | manifest 内 checkpoint。role、path、SHA-256 を保持 |
| `external_checkpoint` | parent として宣言されたが registry 内で未解決の checkpoint |
| `opponent_snapshot` | opponent pool 内 snapshot |
| `arena_result` | manifest に含まれる arena summary / result artifact |
| `human_dataset_session` | 匿名化された人間対戦 trajectory session |
| `dataset_model` | session 収集に使用した human / policy / checkpoint |
| `environment` | session の environment format と git version |

## Edges

| edge type | 内容 |
|---|---|
| `includes` | suite -> run |
| `produces` | run -> checkpoint |
| `resume` | parent checkpoint -> derived run |
| `uses_opponent` | run -> opponent snapshot |
| `evaluates` | run -> arena result |
| `advances_to` | legacy run 内の periodic checkpoint -> 後続 checkpoint |
| `records` | environment -> human dataset session |
| `generated_with` | model/checkpoint -> human dataset session |
| `trains` | human dataset session -> derived training run |

`train.lineage.ancestors(registry, node_id)` と `descendants(registry, node_id)` で、任意 node の祖先・子孫を取得できる。

## Validation

`validate_registry` は以下を検出する。

- node path が存在しない `missing_path`
- edge の source / target が registry に存在しない参照不整合

Markdown report は run 数、checkpoint 数、edge 数、issue 数、run の主要 metric、checkpoint 一覧を出力する。
