# PUYO-73 Legacy Artifact Migration

`train.migrate_artifacts` は v1.0.0 以前の run / checkpoint を元の場所に触らず、
v1.3 の artifact manifest と migration report に変換する。

## Run

```bash
python3 -m train.migrate_artifacts \
  --root runs \
  --output-dir runs/legacy_migration \
  --load-smoke
```

`--root` は複数指定できる。既に `artifact_manifest.json` を持つ run は移行対象から除外する。
生成物はすべて `--output-dir` 配下に書き、元 artifact は変更しない。

## Outputs

| path | 内容 |
|---|---|
| `manifests/<asset>/artifact_manifest.json` | 元 artifact を絶対 path で参照する v1.3 manifest |
| `migration_summary.json` | asset 件数、status counts、record 詳細 |
| `migration_records.csv` | asset ごとの status、manifest path、未移行理由 |
| `migration_report.md` | 人間向け summary |

## Status

| status | 内容 |
|---|---|
| `migrated` | manifest 生成と参照検査に成功 |
| `migrated_with_issues` | manifest は生成したが、path 欠損や load smoke 失敗がある |

欠落情報は推測せず `missing_fields` に `config_path`、`summary_path`、`checkpoint_paths`、
`trainer_name` などとして記録する。`--load-smoke` 指定時は checkpoint に `torch.load` を試し、
`model_state_dict`、raw state dict、unknown dict のいずれかを記録する。

移行後の `manifests/` は `train.lineage --root runs/legacy_migration` で lineage registry の起点にできる。
