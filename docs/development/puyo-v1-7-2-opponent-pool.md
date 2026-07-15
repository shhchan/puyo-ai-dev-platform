# v1.7.2 Mixed-opponent pool

PUYO-129 は、外側の相手カテゴリ比率を固定し、そのカテゴリ内だけで ELO 補正する。これにより強さが近い一種類の相手へ抽選が崩壊しても、large-builder exposure は失われない。

既定 manifest は `train/config/v1_7_2_opponent_pool.json` で、20 pair ごとの quota は次の通り。

| stratum | quota | pairs |
|---|---:|---:|
| large_builder | 30% | 6 |
| rush | 20% | 4 |
| counter_survival | 20% | 4 |
| rule_manager | 15% | 3 |
| historical | 15% | 3 |

各 pair は同じ opponent と seed で learner の `player_0` / `player_1` を入れ替える。seed は `base_seed + pair_index` で、同じ manifest、pair 数、seed、target ELO から同じ schedule を生成する。stratum 内は batch 枠が opponent 数以上なら各 opponent を最低 1 pair 含め、残枠だけを target ELO との距離で配分する。

```bash
python -m train.build_v1_7_opponent_pool \
  --config train/config/v1_7_2_opponent_pool.json \
  --pairs 20 \
  --seed 172129 \
  --output runs/v1_7_opponent_pool/opponent_schedule.json
```

artifact には入力 manifest、manifest SHA256、全 match の opponent/side/seed、stratum と opponent の集計、fallback evidence を保存する。checkpoint は path、SHA256、checkpoint schema をすべて照合する。missing、hash mismatch、schema mismatch、unreadable のいずれかなら相手を黙って除外せず、stratum ごとの決定的な rule/worker fallback に置換して理由を記録する。

## 前提 QA の扱い

- PUYO-132 は evaluator と artifact 生成を完了したが、safe-build training gate は未達だった。
- PUYO-157 は全探索 budget が quality gate 未達で、fallback budget は depth 3 / width 24 / probe width 8 となった。
- PUYO-158 は response scenario 6/6 と checkpoint schema migration を完了した。

したがって pool は大連鎖相手を 30% で維持する一方、この artifact だけを PUYO-130 開始許可には使わない。PUYO-113 の stop/go 条件どおり、safe-build gate 未達が解消するまで mixed-opponent RL は開始しない。
