# PUYO-277 固定対戦 gate の証跡

**v3 の事前固定 gate は達成**．元 56 条件は PASS 52／回避不能除外 4／FAIL 0，追加観測 16 条件は全 PASS．回避不能を応答成功へ加えない．旧 v1／v2 と targeted の raw・未達結果は不変で保持する．これは公開 challenge の bounded gate であり，とこぷよ品質，G2 全体，人間 QA，GUI measured latency SLA の合格とは独立である．

## v1 の実行 identity

- fixture 登録: `00a8fe1`，pattern ID `0，1，32768，65535` × 7 ケース × attack/no_attack．
- 正式実行: `7091afd`，[manifest](formal-v1/manifest.json) の semantic SHA-256 は `c047f98a23a8dfc91eb48a96cd82efe31177e6c78d8a74c2f94b46e9ec67cb63`．
- corpus SHA-256: `568a066c7f50dc3ca9e3aa6bdcc284df5e20f3f39ef689a398c61641c34b52eb`．PUYO-275 の color mapping と実 current/NEXT/NEXT2 を照合した．
- native backend，thread 設定は各 1，1 process．shared/template/response quota `256/128/4000`，configured timeout/action deadline 各 120 tick．最大 3 resolution／1,800 tick．
- 56 条件を 210.16 秒で完走した．160 decision 全件 activated，quota 超過 0，実 lock 不一致 0，deadline 逸脱 0．全 56 replay の全 tick hash／event と最終 hash を検証した．
- collector 修正: `b64e28d`．既存 replay のみを再監査し，policy 呼出し 0，追加 tick 0．[再監査 summary](public-audit-v1/summary.json) に元 manifest と監査実装の checksum を保持する．

## v1 公開再監査の判定

| ケース | PASS | FAIL | 回避不能除外 |
| --- | ---: | ---: | ---: |
| 相殺境界 | 8 | 0 | 0 |
| 着弾後回復 | 7 | 1 | 0 |
| 独立副砲 | 8 | 0 | 0 |
| キー未設置の本線部分 | 8 | 0 | 0 |
| 副砲なし | 8 | 0 | 0 |
| 本線温存 | 4 | 4 | 0 |
| 回避不能 | 4 | 0 | 4 |

攻撃なし対照 28 条件はすべて PASS だった．短い固定盤面の正常配置回帰であり，長期の本線構築能力の保証ではない．search 全体の quota cutoff／partial と，必要な公開 root と実行を確認した肯定的証拠を分けて保存している．

## v1 公開再監査で残った 5 条件

| 条件 | 原因と証拠 |
| --- | --- |
| `preserve_mainline-0-attack` | 最終 tick 169 が arrival 240 より前で，3 resolution の観測窓が不足した．本線は保持されたが，攻撃を相殺・着弾まで観測できていない． |
| `preserve_mainline-1-attack` | 同上．最終 tick 175． |
| `preserve_mainline-32768-attack` | 同上．最終 tick 159． |
| `post_arrival_recovery-65535-attack` | 最初に副砲を発火し，1 個を相殺して tick 90 に 29 個が実着弾した．その後の 2 resolution は発火せず，着弾後発火という登録条件を満たさなかった．全 3 resolution は生存した．公開 counter 候補 `[0,1]` も記録されているが，その候補が実際に採用されたとは扱わない． |
| `preserve_mainline-65535-attack` | decision 3，request tick 108 で action 4 を選択し，3 連鎖で本線を消費した．同じ公開 request に reachable な action 3／5／6 があり，いずれも副砲の 1 連鎖で本線を保持する幾何学的候補だった．選択された手の実 lock と実 3 連鎖・残存盤面は一致した．過剰火力を選ぶ policy 順位の改善候補であり，eval 側で手を差し替えていない． |

v1 の時点では追加観測窓は未実行だった．その後，親が製品修正範囲と回帰条件を割り当て，以下の v2／v3 を実施した．元の観測窓・script・pattern・予算を変更して既存結果を置き換えていない．

## 保存物と検証上の訂正

[正式 v1 summary](formal-v1/summary.json) は旧 collector の PASS 40／FAIL 16 を保持している．着弾演出中の `None` 盤面を即座に不一致とした 8 条件と，上端飽和で一部しか落ちなかった回避不能 4 条件を，再監査で分類し直した．後者は全 22 合法 root の致死証明と実窒息を確認し，応答成功に加えず除外した．再監査によって新しい配置・探索・攻撃を実行したわけではない．

各 `formal-v1/<case_id>-<condition>` に `report.json.gz` と `replay.json.gz` がある．`public-audit-v1/<case_id>-<condition>.json` は元 report／replay digest，元判定，新判定，最初に盤面が公開された tick を保持する．`legacy-rule-smoke` は，初期の quota cutoff を一律 FAIL としていた native paired 調査の元結果であり，正式結果と区別する．

再現・監査コマンドは [開発文書](../../development/puyo-277-attack-response-gate.md) を参照する．

## 限定相殺順位修正後の v2（旧結果を保持）

commit `cb2ecde` で全 72 条件を実行し，443.23 秒で完走した．元 3 resolution の [formal-v2](formal-v2/summary.json) は PASS 49／FAIL 3／回避不能除外 4．事前登録 `b9e277b` の 8 resolution [extended-v2](extended-v2/summary.json) は PASS 13／FAIL 3 だった．無攻撃対照は元 28＋追加 8 条件すべて PASS．全 72 replay は既存 engine の event／全 tick hash／最終 hash を照合済みである．manifest，raw report／replay，実行 log，[件数と回帰集計](v2-statistics.json) を保存した．

両観測窓に共通の未達は `preserve_mainline-1`，`preserve_mainline-32768` の本線消費と，`post_arrival_recovery-65535` の着弾後発火欠如である．元 v1 と公開再監査を新結果で置き換えない．PUYO-277 は依然未達である．

preserve の 2 条件は初期の公開 request に，合法 action 0 による 1 連鎖・相殺 1（全予告量）・本線保持の候補がある．発火終了推定区間は `[32,183]`，攻撃 arrival は 240 で，公開時間推定上の余裕がある．現在の rule selector は threat が immediate でないため build を選ぶ．加えて，同じ解決境界から得た fire_end_upper と deadline_lower の比較が十分相殺の判定を妨げる．後続の準備後には本線を使う発火を選び，実相殺は成功したが登録形状を消費した．

post-arrival は初期の cancel `[2]` の fatal_rate 0 が sampled_future 由来なのに対し，公開 counter `[0,1]` は最初の 28 個着弾，NEXT の 1 連鎖，残り 1 個の全 6 列落下で非致死となる条件付き witness を持つ．selector は両者の 0 を同等として cancel を優先する．実際には先に 1 連鎖を使い，29 個着弾後は 8 resolution まで発火しなかった．公開 counter は未採用の条件付き候補であり，実際の採用成功や hidden rows 全般の保証とは扱わない．

正式 v2 の後に selector の狭い改善範囲が承認された．v3 を実行する場合も同じ 56＋16 条件，fixture，閾値を用い，新 head／manifest／出力で記録する．

## selector 修正後の targeted actual 検証

製品 commit `8ff8131` の限定修正について，残る 3 ケースの attack 条件だけを既定の追加観測窓（8 resolutions）で実 controller により確認した．[targeted-v3](targeted-v3/targeted-summary.json) は 3 件とも PASS，48.74 秒．preserve 1／32768 は初手 action 0 で tick 96 に 1 連鎖・全量相殺を行い，8 resolutions まで登録本線を保持した．post-arrival 65535 は初手 counter action 0 で tick 44 の実着弾を受け，NEXT の tick 158 に発火し，残りの実着弾後も生存した．24 decisions の receipt，実 lock，deadline，quota の異常は 0，全 3 replay の event／tick hash／最終 hash は一致した．

この 3 件は原因に対する targeted 検証であり，全 cohort の正式 PASS ではない．元 v1／v2 の全 raw と失敗は保持した．親による 276 の統合後，clean head で改めて同一 56＋16 条件を正式 v3 として freeze／実行する．

## 正式 v3：固定 gate 達成

276 確定 head `30405d8` を履歴保持で取り込んだ `27b9c6e8da616c553c41402b63be9794707c071b` に固定し，1 worker／各 thread 1 の detached 実行を行った．472.32 秒で全 72 条件を完走し，PID の終了も確認した．[最終監査](v3-final-audit.json)，[実行 log](v3-execution.log)，[完了記録](v3-execution-state.json) を保存している．

| 集合 | PASS | 回避不能除外 | FAIL | decisions | 無攻撃対照 PASS |
| --- | ---: | ---: | ---: | ---: | ---: |
| [元 56 条件](formal-v3/summary.json) | 52 | 4 | 0 | 160 | 28 |
| [追加 16 条件](extended-v3/summary.json) | 16 | 0 | 0 | 128 | 8 |

[formal manifest](formal-v3/manifest.json) の semantic SHA-256 は `03d92102e8f82c474f672f3313bef57c6835cdcee331dcff86b90e399bebb3dc`，[extended manifest](extended-v3/manifest.json) は `356a4416c930d20ba71f7efaa9d2e2284f3e103461b62391f101a552d940f675`．原本 checksum は旧登録と同じ `568a066c7f50dc3ca9e3aa6bdcc284df5e20f3f39ef689a398c61641c34b52eb` で，復旧後の read-only cache `/home/sion2000114/.cache/puyo-s15/haipuyo.txt` を使用した．fixture，script，quota，120 tick request／lock deadline，判定閾値は変更していない．

全 288 decisions が activated．quota 超過，実 lock 不一致，deadline 逸脱，公開 root の未確認はすべて 0 件．全 72 replay について実行中の検証に加え，保存済み入力を policy 呼出しなしの既存 engine で再生し直し，全 event／tick hash／最終 hash を再確認した．manifest の head／全 source checksum／native identity／実 policy 設定と，全 paired 初期 hash／provider／script の一致も確認した．

| A/C | 証拠と境界 |
| --- | --- |
| 相殺・着弾後・副砲有無・本線温存・回避不能を分離する | 事前登録 7 classes × 4 IDs × paired の元 56 条件を同じ窓で全件再実行．回避不能 4 は除外し，残 52 は実 receipt／lock／解決を確認した． |
| 準備済み／発火可能を区別する | 各 report の public trace は独立小連鎖，キー未設置の本線部分，conditional／cutoff を保持し，全連鎖の元 cell 消費と残形を追跡する． |
| paired で予告・相殺・着弾・発火・期限・窒息・replay を比較する | 元 28 pairs と追加 8 pairs の summary，raw report／replay，全 72 の再生照合が一致．対戦の平均最大 10 連鎖は閾値ではない． |
| privacy／quota／fallback／正常配置を維持する | merge 後の対象 59 tests，ruff，無攻撃 36 条件が PASS．製品に pattern ID／fixture annotation／未公開 future／相手 private state を渡さない． |

CPU の最長 decision は元集合 3.098 秒，追加集合 4.549 秒だった．これは configured tick deadline の gate であり，GUI の measured latency SLA 達成を表さない．短い正常配置回帰を PUYO-266 の長期とこぷよ品質へ読み替えない．source 全 65,536 IDs や自然対戦一般の性能を保証しない．
