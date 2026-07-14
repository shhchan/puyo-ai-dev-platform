# v1.7.2 response readiness と checkpoint migration

`prepare_response` は `counter_or_return` と異なり、攻撃を今すぐ発火する tactic ではない。
incoming または opponent forecast から正の `required_response_attack` が得られる場合だけ候補となり、
期限内の response capacity、incoming coverage、既存 trigger の維持を評価する。
即時発火は objective miss (`immediate_fire`) とし、相殺と余剰返しは引き続き
`counter_or_return`、本線発火は `fire_main` が担当する。
active incoming がある decision では learned arbitration の候補を、相殺可能なら
`counter_or_return`、不可能なら `survive` に限定し、この責務分離を checkpoint 重みより優先する。

planner request は `planner-schema-v2`、tactic registry は `tactic-schema-v2` / `v1.7.2`
を使用する。v1.7.1 checkpoint の特徴量順序と tensor shape は変更しないため、重みはそのまま
利用できるが、schema metadata を暗黙に読み替えてはならない。次のコマンドで別ファイルへ明示的に
移行する。

```bash
python -m train.migrate_v1_7_1_checkpoint \
  runs/v1_7_manager/puyo-128-bootstrap-round-1-seed1129/checkpoints/bootstrap.pt \
  runs/v1_7_manager/puyo-158-v1-7-2/checkpoints/bootstrap.pt
```

出力 checkpoint の `schema_migration` には source/target model・tactic・planner schema、
意味変更、`weights_changed: false`、source state hash を記録する。source が v1.7.1 の既知 schema
でない場合や feature contract が変形している場合は移行を拒否する。
