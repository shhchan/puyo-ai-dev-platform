# PUYO-45: 戦術対応した探索 worker の戦略オーケストレーション

## 目的

連鎖構築の設置手探索は固定 worker に任せ、強化学習 manager は対戦局面ごとに使う戦略と探索予算を選ぶ。
manager の行動は設置位置ではなく6種類の `WorkerProfile` であり、選択された worker だけが22通りの合法手を探索する。

PUYO-45 では攻撃着弾 queue、lethal/incoming/deadline 予測、動的 objective、戦術シナリオ teacher、behavior cloning、段階 PPO、opponent pool、arena/UI 診断を追加した。対戦 phase の正本は `docs/development/puyo-versus-timing.md` とする。

## 構成

`agents/strategy_workers.py` は次の共通データを定義する。

* `WorkerProfile`: 戦略名と depth / width / target safety margin / danger tolerance 等の探索設定
* `TacticalContext`: lethal target、incoming、deadline、counter deficit、build safety と短期予測
* `TacticalObjective`: worker に渡す target attack、deadline、許容危険度
* `SearchProposal`: action、予測連鎖・得点・攻撃、target、incoming、理由、判断時間、展開ノード数
* `StrategyOrchestrator`: manager が選択した1つの worker を実行する

初期 worker は次の4種類である。

| profile | 実装 | 目的 |
|---|---|---|
| `build_large` | beam depth 6 / width 32 | 安全時の大連鎖構築 |
| `build_budget` | beam depth 3 / width 16 | 判断時間制約時の構築 |
| `punish` | target探索 depth 3 / width 18 | lethal target を満たす最小攻撃 |
| `counter` | deadline探索 depth 3 / width 18 | incoming + safety margin を期限内に返す |
| `fire_max` | 1手探索 | 現在の最大攻撃を即時発火 |
| `survival` | 1手探索 | 窒息リスクと盤面形状を優先 |

大連鎖と速攻は PUYO-29 の軽量 clone、重複状態排除、盤面評価、探索診断を再利用する。
PUYO-29 の depth 10 / width 48 は単独の高品質 beam baseline として残し、manager の反復学習では計算量を抑えた profile を使う。

## manager 観測

既存 flat versus checkpoint の観測形式は変更しない。`ManagerSelfPlayEnv` が profile 数に応じた専用特徴を追加する。旧4 profile manager checkpoint は、checkpoint 内の profile 数と vector 次元を使って読み込む。

* 自盤面と相手盤面、自分の current / NEXT
* 自他の予告おじゃま、得点、送受信量
* 自他の盤面危険度と即時・2手・3手の bounded attack forecast
* opponent capacity、lethal target / margin
* incoming attack、arrival deadline、counter target / deficit
* build potential / safety
* 直前 profile、継続手数、切替回数
* 直前 proposal の連鎖、攻撃、危険度、判断時間、展開ノード数

対戦環境の `info` には相手 simulator と相手側のおじゃま統計を追加した。既存 observation tensor と flat PPO checkpoint は変更していない。

## 学習

`agents/manager_ppo.py` は6 profile を離散行動とする PPO を実装する。固定6カテゴリを全 worker で counterfactual 評価した teacher dataset から behavior cloning し、その後に `safe_build`、`punish`、`counter`、`full` の curriculum PPO へ進む。通常対戦へ近づくほど tactical auxiliary reward を減衰する。

```bash
# pipeline smoke。生成 checkpoint の強さは評価対象外
python3 -m train.train_manager --config train/config/manager_smoke.yaml

# 通常学習
python3 -m train.train_manager --config train/config/manager.yaml

# CPU medium / long run。long は medium の best と optimizer を引き継ぐ
python3 -m train.train_manager --config train/config/manager_medium.yaml \
  --set run_id=<medium-run-id>
python3 -m train.train_manager --config train/config/manager_long.yaml \
  --set run_id=<long-run-id> \
  --set initial_checkpoint_path=runs/manager_ppo/<medium-run-id>/checkpoints/best.pt

# teacher dataset のみ生成
python3 -m train.generate_tactical_teacher \
  --output runs/manager_teacher/tactical_teacher.json
```

run directory には `config.yaml`、`teacher_dataset.json`、`metrics.csv`、`summary.json`、`checkpoints/latest.pt`、条件を満たした場合は `best.pt` を保存する。self-play snapshot を有効にした場合は `opponents/` と `opponent_pool.json` も保存する。
metadata には seed、git commit、worker profile、報酬・curriculum 設定、opponent 使用数を記録する。

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

比較対象には `manager_rule`、6固定 worker、PUYO-29 の `beam`、`greedy`、既存 `checkpoint` を指定できる。per-match CSV は生成・送信・相殺・受信おじゃま、target/incoming、missed lethal、failed counter、profile と切替理由を保存する。paired summary は policy A score の95%信頼区間を出力する。

PUYO-51 の比較行列は専用 CLI で同じ50 seedを先後入れ替え、合計100局ずつ実行する。`previous_manager`、rule manager、固定6 worker、greedy、PUYO-29標準 beam（depth 10 / width 48）の結果を個別CSVと集約JSON/CSVへ保存する。

```bash
python3 -m eval.manager_benchmark \
  --checkpoint runs/manager_ppo/<long-run-id>/checkpoints/best.pt \
  --previous-checkpoint runs/manager_ppo/puyo28-validation/checkpoints/best.pt \
  --output-dir runs/manager_ppo/<long-run-id>/arena \
  --games 50 \
  --workers 8
```

## UI での確認

学習・定量評価の正本は headless 経路だが、完成した manager の実力と戦略切替は `eval.versus_ui` で確認する。
左右どちらにも manager checkpoint を指定でき、方策名の横に現在の profile、side panel に incoming deadline、target attack、選択理由が表示される。

```bash
python3 -m eval.versus_ui \
  --policy-a human \
  --policy-b manager \
  --checkpoint-b runs/manager_ppo/<run_id>/checkpoints/best.pt \
  --seed 123
```

## 初期 smoke 結果

### PUYO-45 implementation smoke

2026-06-12 に `manager_smoke.yaml` を実行し、teacher 6件、behavior cloning、curriculum PPO、checkpoint 保存・再読込、戦術シナリオ評価、paired arena、診断CSVを確認した。

* 固定6シナリオ: 6/6、正解率100%。
* 学習中の平均 manager 判断時間: 20.29ms/手。
* PUYO-28 validation checkpoint: 10 seed・先後入れ替え20局で19勝1敗、score rate 0.95、95% CI `[0.852, 1.000]`。
* rule manager: 10 seed・先後入れ替え20局で11勝9敗、score rate 0.55、95% CI `[0.326, 0.774]`。

これは8 step の配線・回帰確認であり、強さの最終判定には使わない。50 seed・100局、medium/long 学習、PUYO-29 標準 beam 比較、未達時の ablation は PUYO-51 で実行する。

### PUYO-51 medium/long 実測

2026-06-13 に CPU 8 worker で medium `9,984` step と、medium best を引き継いだ long `99,328` step を実行した。long は `2,378` episode、実行時間3時間42分。checkpoint 選定の早期固定を避ける修正後、今回は snapshot screening を経て `PUYO-51-long-seed1/checkpoints/latest.pt` を採用した。

50 seedを先後入れ替えた100局、最大100手の結果は次のとおり。CI は policy A score rate の95%信頼区間。

| opponent | score rate | 95% CI | max chain | sent | canceled | decision ms |
|---|---:|---:|---:|---:|---:|---:|
| PUYO-28 checkpoint | 0.94 | [0.893, 0.987] | 4.98 | 158.3 | 1.5 | 1287.9 |
| rule manager | 0.50 | [0.402, 0.598] | 2.73 | 28.5 | 9.8 | 686.4 |
| worker large | 0.81 | [0.733, 0.887] | 2.90 | 33.8 | 2.2 | 710.0 |
| worker quick | 0.34 | [0.247, 0.433] | 2.06 | 18.1 | 5.8 | 779.1 |
| worker punish | 0.61 | [0.514, 0.706] | 3.41 | 90.6 | 4.3 | 903.9 |
| worker counter | 0.95 | [0.907, 0.993] | 4.98 | 150.6 | 1.4 | 1040.0 |
| worker fire | 0.94 | [0.893, 0.987] | 4.98 | 156.9 | 1.4 | 1050.7 |
| worker survival | 0.84 | [0.768, 0.912] | 4.80 | 157.4 | 2.7 | 985.6 |
| greedy | 0.88 | [0.816, 0.944] | 2.94 | 45.9 | 3.1 | 746.0 |
| PUYO-29 beam | 0.81 | [0.733, 0.887] | 2.87 | 37.2 | 1.6 | 751.6 |

PUYO-29 beam に対する55%基準を上回ったため、追加学習 ablation は発動しない。beam 戦では `build_large=1,259`、`punish=854`、`survival=244` で単一 profile 固定ではない。一方、`worker_quick` には34%であり、速攻対応と平均0.75秒以上の判断時間は次の改善対象とする。詳細値は `docs/benchmarks/puyo-51-arena-summary.csv` と `puyo-51-summary.json` に保存した。

### PUYO-28 baseline smoke

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
