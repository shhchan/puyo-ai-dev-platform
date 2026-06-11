# PUYO-28: 探索 worker の戦略オーケストレーション

## 目的

連鎖構築の設置手探索は固定 worker に任せ、強化学習 manager は対戦局面ごとに使う戦略と探索予算を選ぶ。
manager の行動は設置位置ではなく4種類の `WorkerProfile` であり、選択された worker だけが22通りの合法手を探索する。

## 構成

`agents/strategy_workers.py` は次の共通データを定義する。

* `WorkerProfile`: 戦略名と depth / width / scenarios / minimum chain 等の探索設定
* `SearchProposal`: action、予測連鎖・得点・攻撃、危険度、判断時間、展開ノード数
* `StrategyOrchestrator`: manager が選択した1つの worker を実行する

初期 worker は次の4種類である。

| profile | 実装 | 目的 |
|---|---|---|
| `large_chain` | beam depth 6 / width 32 | 6連鎖未満の早期発火を抑えて構築 |
| `quick_attack` | beam depth 3 / width 16 | 小連鎖と早期のおじゃま送信 |
| `fire` | 1手探索 | 現在の最大得点・攻撃を即時実行 |
| `survival` | 1手探索 | 窒息リスクと盤面形状を優先 |

大連鎖と速攻は PUYO-29 の軽量 clone、重複状態排除、盤面評価、探索診断を再利用する。
PUYO-29 の depth 10 / width 48 は単独の高品質 beam baseline として残し、manager の反復学習では計算量を抑えた profile を使う。

## manager 観測

既存 versus checkpoint の観測形式は変更しない。`ManagerSelfPlayEnv` が次の専用特徴を追加する。

* 自盤面と相手盤面、自分の current / NEXT
* 自他の予告おじゃま、得点、送受信量
* 自他の盤面危険度と1手発火可能量
* 直前 profile、継続手数、切替回数
* 直前 proposal の連鎖、攻撃、危険度、判断時間、展開ノード数

対戦環境の `info` には相手 simulator と相手側のおじゃま統計を追加した。既存 observation tensor と flat PPO checkpoint は変更していない。

## 学習

`agents/manager_ppo.py` は4 profile を離散行動とする PPO を実装する。対戦報酬に加え、不要な切替と判断時間へ小さいペナルティを与える。

```bash
# pipeline smoke。生成 checkpoint の強さは評価対象外
python3 -m train.train_manager --config train/config/manager_smoke.yaml

# 通常学習
python3 -m train.train_manager --config train/config/manager.yaml
```

run directory には `config.yaml`、`metrics.csv`、`summary.json`、`checkpoints/latest.pt`、条件を満たした場合は `best.pt` を保存する。
metrics には勝敗・得点・最大連鎖のほか、profile 使用数、切替回数、平均判断時間、平均展開ノード数を記録する。

## 評価

arena の `--paired-sides` は各 seed を先後入れ替えで2局実行する。per-match CSV には各方策の判断時間、展開ノード数、切替回数、profile 使用数を保存する。

```bash
python3 -m eval.arena \
  --policy-a manager \
  --checkpoint-a runs/manager_ppo/<run_id>/checkpoints/best.pt \
  --policy-b worker_large \
  --games 30 \
  --paired-sides \
  --csv runs/manager_ppo/<run_id>/arena_worker_large.csv \
  --summary-csv runs/manager_ppo/<run_id>/arena_worker_large_summary.csv
```

比較対象には `manager_rule`、4固定 worker、PUYO-29 の `beam`、`greedy`、既存 `checkpoint` を指定できる。

## UI での確認

学習・定量評価の正本は headless 経路だが、完成した manager の実力と戦略切替は `eval.versus_ui` で確認する。
左右どちらにも manager checkpoint を指定でき、方策名の横に現在の profile が表示される。描画、入力、キーバインド、連鎖演出は既存実装をそのまま利用する。

```bash
python3 -m eval.versus_ui \
  --policy-a human \
  --policy-b manager \
  --checkpoint-b runs/manager_ppo/<run_id>/checkpoints/best.pt \
  --seed 123
```

## 初期 smoke 結果

2026-06-12 に `manager_smoke.yaml` を8 step実行し、checkpoint 保存・再読込、profile ログ、paired arena、dummy SDL UI 起動を確認した。
2 episode の平均勝率は0.5、平均切替回数は2.5、平均 worker 判断時間は15.36 msだった。この値は配線確認用の極小学習結果であり、方策の強さを示すものではない。

標準 profile では64 step、4 episode、各 episode 最大16手の検証学習も実施した。学習中は4 profile が使用されたが、deterministic checkpoint は `fire` のみを選択した。
seed 1〜3を先後入れ替えで評価した結果は次の通り。

| opponent | games | manager wins | losses | mean decision | mean expanded nodes |
|---|---:|---:|---:|---:|---:|
| `worker_large` | 6 | 6 | 0 | 2.7 ms | 22 |
| `greedy` | 6 | 0 | 6 | 2.9 ms | 22 |

16手の短期対戦では即時発火が低速な大連鎖構築を上回ったが、greedy には全敗した。64 stepでは routing が `fire` へ収束し、局面に応じた切替を学べていない。
この検証により、固定 worker より常に強いとは判断できず、通常学習では episode 長と学習 step を増やし、entropy、切替コスト、profile 別使用率を確認する必要がある。

## 今後の評価

通常学習では固定 seed と先後入れ替えを使い、固定 worker、rule router、flat PPO、PUYO-29 beam と比較する。
改善しない場合は profile 使用率、切替頻度、局面特徴別の選択、判断時間ペナルティを ablation し、worker の不足か routing 学習の不足かを切り分ける。
