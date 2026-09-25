# PUYO-270 公開範囲の生存例外

## 境界

`nextgen_survival.py` は定型 catalog，長期目標，sampled future，`fatal_rate` を参照しない．現行の 6 戦術と feature registry は維持する．PUYO-272 の template 制約をこの経路へ渡してはならない．PUYO-268 は batch 内の固定順位と `apply_envelope` を保持して通常戦術を改良する．

公開 current/NEXT/NEXT2 の最大 3 手について，root の legal/reachable mask，消去と落下後の終端判定，次 spawn の窒息点と合法配置，公開予告のおじゃま相殺・着弾・残り packet を調べる．後続手は幾何学上の配置であり，実時間の移動到達性は次の request で再確認する．公開 timing の区間と端数おじゃまの全分布について継続がある場合だけ witness とする．分布ごとの継続は異なってよく，診断の `witness` は代表例である．plan は実行 queue にしない．

隠し行は既存の公開盤面推定を使い，`public_estimate`/`partial` とする．未観測の visible cell は `unknown` である．`visible_exact` は公開盤面上の幾何学的計算を表し，将来操作や公開 cadence の実時間保証ではない．全候補が死亡する完全盤面の結果だけを，この有限 horizon と公開 timing モデル内の `unavoidable` と呼ぶ．未公開ツモや攻撃について生存保証しない．

## 予算と通常局面

各 request の response quota の先頭から最大 128 nodes を使い，残りを既存 response provider が使う．shared/template quota は変更しない．配置・消去の遷移とおじゃま分布の各評価は実行前に 1 node を消費する．root ごとの round robin により，全 root の第 1 手評価を後続探索より先に行う．quota 0 や不足は死亡ではなく `cutoff` である．configured inference が timeout を越える場合は `deadline_unreachable` とする．

中央列の visible height，occupied/unknown hidden central cells，known horizon の最大増加 `2 × H`，全公開 incoming amount の和が 12 未満なら，中央列は horizon 中に窒息点へ到達できない．消去や重力は中央列の高さを増やさないため，この保守的上界で probe を省略する．hidden の unknown を 0 と数えず，visible central unknown では省略しない．この高さは探索の起動条件だけであり，着手の安全性は実遷移で比較する．低い無予告盤面は追加探索 0 node，既存の戦術順位を維持する．

## 候補，選択，receipt

すべての合法 root は従来から `build_main` に存在するため，安全な単発を短期攻撃の火力閾値で失わない．同じ root の候補へ生存証拠を添付する．少なくとも 1 つの死亡 root と 1 つの witness がある危機だけで，構築戦術の固定順位を「非発火 witness，発火 witness，未証明，死亡」の順へ変更する．発火・相殺・counter・短期攻撃戦術は witness 同士の既存順位を保ち，安全な通常発火を抑止しない．同区分内は既存順位を保つ．第 7 戦術を増やさない．

共通 `apply_envelope` は rule/将来 RL の選択後にも適用され，未証明または死亡 root を witness がある `build_main` へ置き換える．安全な非発火がある場合，構築戦術や生存目的で新たな発火を要求しない．通常戦術が既に選んだ安全な発火は維持する．必要発火は `legitimate_survival_exception`，非発火は `survival_safe_nonfire` と記録する．選択を実際に置き換えた場合は rule provenance とし，RL の log probability を実行行動へ流用しない．安全な RL 選択を維持するときは元の learned provenance を保持する．

scheduler は引き続き実盤面と到達性を採用時に確認する．receipt の requested/executed/outcome と，`survival_adopted` または `survival_not_adopted_<outcome>` を合わせて追跡する．失われた witness を fallback の成功と数えない．timeout より先に完成結果が得られない非同期経路は従来の attempt 記録であり，架空の候補や receipt は作らない．fallback 性能修正は本件の範囲外である．危機後は新しい公開盤面から再評価し，生存発火の固定継続状態は持たない．

## batch v3 と後方読み込み

strict evidence allowlist を拡張したため `puyo.nextgen.candidate_batch.v3` を出力する．v1/v2 reader が新 payload を旧意味として読むことは許さない．新 codec と trajectory manifest は v1/v2/v3 を読み，旧 fixture の round trip を保つ．request/selection/feature schema，6 戦術 registry，native ABI は変更しない．

| evidence | 意味 |
| --- | --- |
| `survival_safe` | witness は 1，死亡は 0，unknown/cutoff/deadline は missing |
| `survival_status` | 1=witness，2=fatal，3=unknown，4=cutoff，5=deadline_unreachable |
| `survival_root_chain` | current の消去連鎖数．未評価は missing |
| `survival_depth` | 代表 witness の公開手数．未証明は missing |

probe の root coverage，代表継続，unreachable roots，node 数は `search.survival` に保存する．候補と選択と receipt は既存の `Diagnostics`/trajectory/replay に保存し，候補不足・固定順位・採用失敗を区別する．

## 検証

`python -m unittest tests.test_nextgen_survival tests.test_nextgen_contracts tests.test_nextgen_shared_search tests.test_nextgen_tactic_manager tests.test_nextgen_trajectory tests.test_nextgen_response_search`

PUYO-271 の `near_choke_single_clear` を再利用する．この盤面自体には安全な横置きもあるので，root 7/9 だけが到達可能なケースで必要単発を，全 root または 0/7/9 が到達可能なケースで安全な非発火を検証する．消去しても死亡，既知 NEXT だけで予告到来後に回復，回避不能，到達不能，quota/timeout，hidden/visible unknown，実採用・実消去・fallback，危機後の構築復帰を追加した．合成盤面は元のユーザー GUI replay の再現とは主張しない．

paired seed の観測は [測定記録](../benchmarks/puyo-270-survival/README.md) を参照．正式 G2 は PUYO-266，GUI cadence は PUYO-269/273 の所有範囲である．
