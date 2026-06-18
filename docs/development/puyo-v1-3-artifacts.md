# PUYO-55 v1.3.0 Artifact Contract

PUYO-55 では trainer ごとの保存形式を共通 schema に寄せ、後続の exact resume、
experiment suite、lineage graph で同じ情報を読めるようにする。

## Checkpoint schema

各 trainer は既存 checkpoint payload を維持したまま、次の top-level key を追加する。

| key | 内容 |
|---|---|
| `artifact_schema_version` | checkpoint schema version。現在は `puyo.checkpoint.v1` |
| `checkpoint_schema` | trainer 名、run id、checkpoint 種別、step、seed、git commit、config digest、親 checkpoint、resume contract |

`checkpoint_schema.resume_contract` は `model_state_dict` と `optimizer_state_dict` の key 名、
optimizer state の有無、乱数状態の有無、環境進捗の summary を持つ。PUYO-69 時点では
乱数状態と環境内部状態の完全保存は未実装で、PUYO-70 の exact resume で追加する。

## Run manifest

run directory を持つ trainer は完了時に `artifact_manifest.json` を出力する。
manifest schema version は `puyo.artifact_manifest.v1`。

| section | 内容 |
|---|---|
| `run` | run id、trainer 名、seed、git commit、config digest、親 checkpoint |
| `artifacts` | config、metadata、metrics、summary、teacher dataset、opponent pool など |
| `checkpoints` | latest、best、periodic、behavior cloned、opponent snapshot など |
| `extra` | trainer 固有の選定条件や legacy marker |

各 artifact/checkpoint entry は role、type、run directory からの相対 path、存在有無、byte size、
SHA-256 を持つ。`train.artifacts.validate_artifact_manifest` で、欠損と hash drift を検出できる。

## Current integration

| trainer | manifest | checkpoint schema |
|---|---|---|
| `agents.flat_ppo` | `runs/flat_ppo/artifact_manifest.json` | latest checkpoint |
| `agents.versus_ppo` | `<log_dir>/<run_id>/artifact_manifest.json` | latest / best / periodic |
| `agents.manager_ppo` | `<log_dir>/<run_id>/artifact_manifest.json` | latest / best / behavior cloned / opponent snapshot |

CLI は `manifest: ...` を出力する。既存 checkpoint loader は追加 key を無視できるため、
既存の `model_state_dict` / `policy_type` / profile metadata の互換性は維持する。
