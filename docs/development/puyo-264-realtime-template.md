# PUYO-264: 通常速度の定型採用と診断

## 再現した原因

起点 `9a7a1328c6458e75acbf3943cca0ecb2c4a6e1b9`，seed 55，GTR のみ，argmax，N=14，`nextgen_smoke`，相手 random，measured latency で確認した．GUI と同じ `RealtimeVersusMatchController`／spawn worker を使い，描画なしで最大 60 Hz の対局と，worker 完了まで待ってから 1 tick 進む低速ステップを比較した．これは人間による通常 GUI QA ではない．

初回の公開盤面・current・NEXT・NEXT2 は一致した．修正前の通常速度では，5 回とも相手の盤面／公開 event が探索中に変わり，snapshot の不一致で stale になった．要求 action は `2,4,2,18,12` だが実行 action は全て `7`（3 列目縦置き）だった．最短入力を選ぶ fallback が原因であり，候補自体が常に縦積みを選んだわけではない．初回は request tick 0，completion tick 109，探索約 1.8 s だった．低速ステップでは初回 0→1 tick で採用され，最初の 5 要求 `2,12,0,14,16` をそのまま実行した．通常速度だけ計算中に相手が進むことが違いを生んでいた．

## 修正の契約

- snapshot が変わった通常の探索結果は，`stale_snapshot_retry`，`executed_action=null`，`activation_tick=null` の receipt を残し，次 tick に新しい公開入力を要求する．配置 fallback と phase 手数消費を行わない．timeout，policy error，authoritative root が到達不能な場合の既存 fallback は維持する．
- worker ごとに Python shared search の結果を 1 件だけ保持する．比較対象は backend request の全入力であり，telemetry 用の request ID だけを除く．盤面，公開ツモ，seed，探索設定，evaluator，quota が変わると失効する．別 backend／独自 backend には適用しない．reset 時も破棄する．
- cache は候補 batch ではない．新しい request identity，candidate ID，batch digest，reachable mask を使って batch を作り直す．template，response，rule selector を新しい相手状態・攻撃 packet・timing で実行する．採用には引き続き **全公開 snapshot digest の一致**が必要である．
- counters はその証拠を生成した全 node 数を新要求の固定 quota に計上する．`search.shared_reuse` が元の backend request ID を示し，`stage_elapsed_ms.shared` は今回の再利用時間，backend timing は元の探索時間と明示する．未使用 quota を別探索へ移さない．
- 初期 GTR 選択時に最高静的評価の色割当が node quota の後回しになっていたため，nextgen policy は `prioritize_static_binding=True` を指定する．各形の最高静的評価の割当を，既存の active preferred binding と同様に 1 binding unit で先に評価する．node quota，template／variant の順序，他の matcher 利用者の既定列挙は維持する．複数 template 間の quota 配分を改善したという主張ではない．

## 再実行

```bash
python -m eval.nextgen_realtime_diagnostic \
  --mode normal --seed 55 --placements 15 \
  --output /tmp/puyo264-normal
python -m eval.nextgen_realtime_diagnostic \
  --mode step --seed 55 --placements 15 \
  --output /tmp/puyo264-step
python -m eval.nextgen_realtime_diagnostic \
  --mode normal --opponent human --seed 55 --placements 15 \
  --output /tmp/puyo264-human-normal
```

`human` は無入力の相手であり，random 対局とは別条件である．seed は環境／自分が 55，相手 policy が 10055 と固定される．既存 output directory には書き込まない．生成物は `/tmp` の専用 directory に出力し，Git に追加しない．GUI 形式の replay は controller diagnostics を tick ごとに含むため，上記の 11〜15 配置でも **1 対局約 0.5〜1.4 GB**，3 条件を合わせて約 3 GB の容量を使った．長い実行ではさらに増えるため，出力先の空き容量を確認する．出力は次の 3 ファイルである．

| ファイル | 見る項目 |
| --- | --- |
| `report.json` | 全 request の公開入力，receipt outcome，要求／実行 action，phase，候補順位，stage 時間，quota，cache 出典，実際の配置後の GTR 必須マス進捗 |
| `ledger.json` | GUI と同じ authoritative receipt，phase の消費手数と終了理由，戦術履歴 |
| `replay.json` | GUI と同じ replay 形式，実行 input と最終 state hash |

`source_changed_during_run=false` と同じ source hash を確認する．同じ組への replan は複数の採用 receipt を持ち得るため，採用件数と配置手数を混同しない．`adopted_piece_ids` と `phase_after.remaining_decisions` で対応を見る．配置直後が split-pair の落下中なら，次の control 盤面を `settled_control` として観測する．最後がまだ落下中の場合は `lock` と明示する．

対戦相手が通常速度で進む以上，通常／低速の対局全体が同一公開入力になるとは限らない．最初の公開入力と，同じ own 盤面・公開ツモの要求を対応させる．途中から攻撃が異なる場合は，その差を候補能力の差として扱わない．

## 検証と限界

`tests.test_nextgen_realtime_diagnostic` の固定 GTR fixture は必須マス進捗 0.875 から始める．既定 `nextgen_smoke` の template quota 128 で `build_template` を実採用し，進捗 1.0 に達した後の要求が `build_main` を実採用することを確認する．旧 source では従来列挙に witness がなかったが，PUYO-268 の選択定型接続後の現在の fixture では opt-in なしでも witness `(0,)` がある．この歴史的な制限を現在の仕様として要求する assertion は外し，完成と実採用の確認を維持する．これは固定盤面の能力確認であり，全 seed の GTR 完成率ではない．

`tests.test_nextgen_tactic_manager` は連続する相手更新を未配置の stale として記録し，安定後に 1 組を 1 回だけ消費すること，新しい自分への脅威で cache hit 後も counter を選び直すことを確認する．rule selector の cancel／counter／short attack／fire_main／build_main の条件は固定 batch の単体テストで検証する．単体の候補・選択の成立と，対局 ledger の実採用は区別する．

`tests.test_nextgen_shared_search` は cached／fresh batch の意味的同値性，新 request／candidate ID，公開情報と reachable mask の再反映，backend 入力の変更による失効を確認する．`tests.test_template_phase` の 14／15 手境界，stale／timeout／retry と重複消費防止，公開情報境界，trajectory，replay，GUI 契約も回帰対象とする．

人間による通常 GUI QA は 2026-09-28 に依頼者が OK と報告した．2026-10-08 の nextgen／human 対戦では `o` の先読みと ghost の区別ができ，移動・回転で意図した位置へ置けそうとの報告もあった．人間の観測を同一公開入力の機械比較とは区別する．

PUYO-266 は規定 cohort の 30 seed × 2 repeat を再測定済みであり，「未実測」のままではない．[統合 G2 証跡](../benchmarks/puyo-266-safe-build/integrated-g2-20260927/README.md) の結果は quality FAIL／G2 BLOCKED で，G0／G1／必須脅威 fixture／公開既知解 gap は未確認である．本件の A/C は G2 PASS を明記していないが，正式 gate の未確認条件を測定完了だけで満たした扱いにはしない．正式 G2 の完了判断は PUYO-266 に残し，PUYO-264 は In Progress を維持する．複数 catalog の初期 quota 配分は本件の対象外である．

## 2026-10-08 デスクトップ監査

[監査記録](../benchmarks/puyo-264-desktop-audit/README.md) は A/C ごとに証拠と限界を対応させる．旧 3 条件の raw は Git に保存されておらず，PR #158 と Jira コメント 10715／10764 の検証済み記録を参照した．新しい通常速度対局や G2 は実行していない．

保存済み統合 G2 の 60 ledger を読み取り，選択 candidate ID，activated receipt，要求／実行 root を照合した．seed 123／repeat 1 の ledger index 30 は `fire_main`，requested／executed action 16，実 10 連鎖であり，旧固定 batch の選択テストとは別の実採用証拠になる．この対局は無脅威 fixture で，脅威対応の能力全体を証明しない．品質 FAIL と個別戦術の実採用を区別する．

## References

- [PUYO-264](https://shhchan.atlassian.net/browse/PUYO-264)
- [PUYO-263](https://shhchan.atlassian.net/browse/PUYO-263)
- [PUYO-255](https://shhchan.atlassian.net/browse/PUYO-255)
- [定型 phase](puyo-247-template-phase.md)
