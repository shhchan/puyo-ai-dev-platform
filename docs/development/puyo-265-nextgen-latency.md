# PUYO-265: 次世代モデルの探索時間

次世代モデルの既定 shared search を既存の strict native adapter に接続した．depth 4，width 4，scenario 1，shared/template/response quota 256/128/256 を維持し，候補生成・公開情報境界・戦術選択条件は変更していない．実 GUI の人間 QA と品質 G2 は未実施であり，PUYO-265 は In Progress，PR は draft とする．

## 原因と比較条件

従来の次世代モデルは Python shared search を既定としていた．固定 seed 55 の初回入力を cProfile で調べると，shared search 4.489 s のうち構造評価内の `bounded_quiescence` が 4.252 s を占めた．256 node の探索でも evaluator は root を含め 257 回呼ばれる．これは profiler による上乗せを含む原因診断値であり，下表の実測遅延とは別である．template matcher や worker 起動は主要因ではなかった．

`deep_chain_builder` reference は native，depth 16，width 250，6 scenario，node 上限 600000，target 10 である．次世代モデルの小さい探索予算だけを見て「同じベースだから同じ速度・品質」とは判断できない．本変更は同じ nextgen 入力・予算で backend だけを比較し，reference の品質・計算予算を次世代モデルへ導入したという主張はしない．

`eval.nextgen_latency_benchmark` の 6 盤面（空盤面 2 seed，GTR 途中，GTR 完成，縦塔，凹凸盤面）を各 3 回，キャッシュを毎回 reset して測定する．公開 envelope は実 scheduler が生成し，hidden 2 行は不明のままとする．backend ごとに fresh process を使い，同一マシンで直列実行する．worker 測定は同じ 6 盤面を各 1 回，実 `PolicyProcessExecutor` の spawn・ready・送受信を使う．direct と worker の出力 materialization/copy は同じ経路とし，CPU 時間と RSS high-water は実際の探索プロセス内で測る．RSS は process の高水位であり，1 decision の増分ではない．

実測 source は `08d79a9a0c9a5e026aa0768db0e51b7c42f003ec` の commit（正確な SHA と全 source hash は JSON を参照）．Intel Core Ultra 7 258V，CPython 3.12.3，既存 native 0.4.0 release／scalar／scenario-6 を使った．native source revision `3defcc2b8c6f42824102166aa7011527dfd4fb9e` から対象 Rust source に差分はない．全測定で `source_changed_during_run=false` を確認した．

| 固定入力の指標 | Python | native |
| --- | ---: | ---: |
| policy p50 / p95（18 件） | 2.010 / 2.519 s | 0.232 / 0.366 s |
| shared p50 / p95 | 1771.5 / 2334.3 ms | 18.4 / 32.5 ms |
| template p50 / p95 | 67.9 / 105.8 ms | 68.9 / 107.5 ms |
| response p50 / p95 | 49.2 / 95.7 ms | 52.3 / 101.0 ms |
| CPU p50 / p95 | 2.009 / 2.518 s | 0.238 / 0.366 s |
| 最大 RSS high-water | 244.2 MiB | 304.3 MiB |
| spawned worker roundtrip p50 / p95（6 件） | 2.046 / 2.436 s | 0.263 / 0.398 s |
| worker の policy 外 p50 / p95 | 9.4 / 11.5 ms | 8.1 / 10.9 ms |
| worker 起動・ready p50 / p95 | 1.039 / 1.101 s | 1.097 / 1.130 s |

policy p50 は約 8.7 倍，p95 は約 6.9 倍の短縮となった．最大 RSS は約 60.1 MiB 増えた．worker 起動は対局初期化時の ready 待ちであり，繰り返す decision の遅延に含めていない．direct の diagnostics materialization p95 は Python 3.67 ms／native 3.52 ms，入力 pickle p95 は 0.17／0.13 ms，出力 JSON p95 は 0.89／0.93 ms だった．spawn の policy 外時間は diagnostics copy と IPC／queue を含む残差であり，個々の syscall の計測ではない．

同じ固定公開入力の direct 18 件・worker 6 件で，候補 batch digest，選択，時間以外の counters が完全一致した．探索予算増加や評価関数変更による高速化ではない．

通常速度の描画なし診断は，両方とも seed 55，GTR，argmax，N=14，nextgen_smoke，random 相手，measured latency，最大 2400 tick／15 配置，replay 省略で実行した．

| 通常速度の指標 | Python | native |
| --- | ---: | ---: |
| 終了 tick / 実時間 | 2400 / 51.83 s | 1698 / 43.14 s |
| 実配置 / 採用 receipt | 10 / 10 | 15 / 15 |
| stale / timeout / fallback | 16 / 0 / 0 | 15 / 0 / 0 |
| policy p50 / p95 | 0.308 / 2.598 s | 0.278 / 0.586 s |
| shared cache hit | 16 | 0 |
| 実時間 1 s あたりの配置 | 0.193 | 0.348 |

通常速度の throughput は約 1.8 倍となり，native は 15 配置の打切り条件に到達した．Python の通常 p50 が固定 corpus より短いのは stale retry のキャッシュを含むためである．native にキャッシュを広げずに改善を確認した．最初の 6 採用 action は両方とも `2,12,0,14,16,19` で，その後は相手の進行と脅威が異なり分岐した．単発消しや連鎖品質の改善としては評価しない．

## 契約と検証

- `NextgenTacticManagerPolicy()` と GUI の既定は native．`backend="python"` または GUI CLI の `--nextgen-backend python` で明示的に従来経路を実行できる．`auto` は受け付けない．
- native の release build／ABI／schema 検証と既存の 1-call adapter を使う．利用不能や不整合時は例外を返し，Python に暗黙 fallback しない．backend identity・native provenance・fallback=false は各 decision に残す．backend diagnostics の canonical は strict native 実行を表し，nextgen の品質校正を意味しない．
- shared search の独立 quota と node accounting，候補の公開情報由来，未知の future isolation，全公開 snapshot digest の採用検証，receipt・trajectory・replay の既存契約を維持する．native に Python 専用キャッシュを広げていないため，native の stale retry は fresh search である．
- 旧 `eval.nextgen_gate_benchmark` は従来の Python 経路を明示指定して再現性を維持する．既存 G2 記録を native の評価実績として扱わない．
- 回帰は 3 実行群（114 件，36 件，59 件）がすべて成功した．重複実行を含む件数である．nextgen latency/shared/policy/public snapshot/contracts/trajectory/response，native backend／ABI，GUI/gates/template phase，realtime UI／replay を含む．missing native，ABI mismatch，auto 拒否，実 native spawn，候補・選択・quota parity，native の private future isolation と実行時 failure を検証した．
- 別途，native の通常速度 2 配置・149 tick の GUI replay を約 29.9 MB に制限して保存した．2 件とも採用され fallback は 0 件，134 tick の receipt diagnostics を検証し，実入力を再生した最終 state hash が保存値 `23c657991c608458d5bca03ea30d037e78335276a1d546a84449e965b8b05dfd` と一致した．

## 再実行と人間 QA

既存の release native 拡張が導入済みの環境で実行する．build が必要なら [native build script](../../scripts/build_deep_chain_native.sh) の既定手順を利用する．比較中は他の CPU 負荷を実行せず，backend を直列にする．

```bash
python -m eval.nextgen_latency_benchmark --backend python --output /tmp/puyo265-python.json
python -m eval.nextgen_latency_benchmark --backend native --output /tmp/puyo265-native.json
python -m eval.nextgen_latency_benchmark --backend python --worker --repeats 1 --output /tmp/puyo265-worker-python.json
python -m eval.nextgen_latency_benchmark --backend native --worker --repeats 1 --output /tmp/puyo265-worker-native.json
python -m eval.nextgen_realtime_diagnostic --mode normal --seed 55 --placements 15 --max-ticks 2400 --backend python --omit-replay --output /tmp/puyo265-normal-python
python -m eval.nextgen_realtime_diagnostic --mode normal --seed 55 --placements 15 --max-ticks 2400 --backend native --omit-replay --output /tmp/puyo265-normal-native
```

`--omit-replay` は report と ledger を保存し，巨大な per-tick GUI replay を保持・保存しない．省略時の既存 replay 保存経路は維持する．通常対局の進行は wall-clock に依存するため，Python/native の全試合の公開入力は同一にはならない．固定 corpus の候補 parity と通常速度の採用 throughput を分けて評価する．

人間 QA は launcher で次世代モデル，GTR，argmax，seed 55，N=14，nextgen_smoke を指定し，通常速度で操作する．待ち時間，stale 後の再判断，戦術表示と ledger の要求／実行 action を確認する．CLI では `python -m eval.realtime_versus_ui --policy-a nextgen_tactic_manager --policy-b random --nextgen-backend native` を使える．

PUYO-266 は定型後の自由構築，単発消し，target 10，相手との品質比較を担当する．本変更では `minimum_chain_count=2`，selector の発火閾値 6，探索予算を変更していない．高速化だけで 10 連鎖能力の回復や G2 PASS は主張しない．

## References

- [PUYO-265](https://shhchan.atlassian.net/browse/PUYO-265)
- [PUYO-266](https://shhchan.atlassian.net/browse/PUYO-266)
- [PUYO-264 の公開 snapshot／採用契約](puyo-264-realtime-template.md)
- [実測データ](../benchmarks/puyo-265-latency/summary.json)
