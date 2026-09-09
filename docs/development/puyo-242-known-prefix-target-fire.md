# PUYO-242 既知ツモ内の目標発火優先: 単独試行 No-Go

判定は **No-Go（既定不採用・mergeしない）**。目標発火の先送りは一部改善したが、40手runの平均時間が21.014771秒から24.440583秒へ16.3019%増加した。decision p95が1秒以内でも、現行からの時間非悪化条件を満たさない。PUYO-240/241の変更は含めていない。

## 固定した比較

| 項目 | Baseline | 候補 |
| --- | --- | --- |
| 計測commit | `73ab4e8ce066555042f1a20e1b3b59be3a2a8968` | `04919bb9d102fce0af6a963d8b7d0a3b98c467db` |
| release wheel SHA256 | `6c5dde345ec65c6ce97f5b7a17a575a9060b4cfb260a9da383d72a6456a8fde6` | `f9bb6ad5d009a6e044f555302a2f031c94ffb66896e6cd3b2fe77150b295f9f9` |
| ranking identity | `puyo.expected_chain_ranking.v2` | `puyo.expected_chain_ranking.v3` |

共通設定SHA256は `d347818c6bddb25dfa35af6bd5d4916179bd19a0f2cc7eeebfed08293be72b5f`。target10 / safe_no_threat / reference / native scenario-6 / depth16 / width250 / scenarios6 / expanded上限600,000、既存quiescence・evaluator設定を固定した。CPython3.12.3、同一WSL2 host（Intel Core Ultra 7 258V）で専用venv・専用release wheelを使い、同じseed/repeatについてbaseline→候補のfresh processを逐次実行した。他のローカル重処理とは並走していない。

seed123/126/130/133/135/138/147/151 × repeats1/2、40 placementsまたは正当なgame over。両群16run・638decisionが完全評価された。repeat2は決定論の検証であり、独立seedは8件である。

## 事前に確認した候補

固定baselineの319decision全root保存記録を読み、保持済みtarget候補でdepthが公開known_pairs長以下かを確認した。該当decision数は123:12、126:10、130:0、133:0、135:1、138:5、147:3、151:12。これは保持済み証拠の存在確認であり、保存されなかった全発火候補の不存在を証明するものではない。

| 盤面（1始まり） | 現行選択 | 既知prefix内の未選択候補 | 現行で負けた比較 |
| --- | --- | --- | --- |
| seed126 turn23 | root21、depth9、11連鎖 | root6、depth1、10連鎖、6/6 | class・coverage・支持数は同じ。terminal合計748773.39対343972.00 |
| seed135 turn34 | root11、depth5、12連鎖 | root20、depth3、12連鎖など | class・coverage・支持数は同じ。terminal合計が劣る |
| seed147 turn28 | root2、depth4、10連鎖 | root19/20、depth1、10連鎖、6/6 | terminal合計627589.33対431552.00 |

seed135の選択・近傍候補はいずれもterminal合計が約−6e12である。近いtarget候補の存在を安全な到達の証明とは扱わない。成功seed123/138/151にも近傍候補があるため、それらの後退も採否条件に含めた。

## 単一変更

`target_fire` に限り `fire.depth <= len(known_pairs)` の真偽を既存terminal scoreより先に比較する。prefix内同士、prefix外同士は既存の品質順を維持する。

- Python `ChainFireEvidence.rank_key` とnative `fire_rank_cmp`、scenario別trackerの保持、rootのbest fire・代表planを同じ規則へ揃えた。公式連鎖score/countの最大値記録は従来通り残す。
- root順位はclass → coverage → class支持数 → 既知prefix内targetの有無 → 従来のterminal合計・score/count等の順とした。勝利・forced-safety・prematureの分類と優先関係は変えていない。
- 既存depth/pathと公開current/NEXT/NEXT2由来の実入力長だけを使う。追加探索、幅・深さ・予算増、特徴抽出、候補履歴の保持、過去planへの固定は追加していない。毎decisionの再計画を維持する。
- nativeの非0 pair/scenario cursor拒否は維持する。Python/nativeのranking identityをv3へ揃え、旧v2 requestは拒否する。envelope/section layoutは変わらない。
- 固定入力を再利用するときだけ明示的なfixture移行を行った。ranking identity section以外の全payload、request_id、root、known_pairs、search/evaluator設定、execution modeの同値と前後SHAを検証した。通常runtimeへ互換fallbackは追加していない。

主な変更は `agents/long_horizon_search.py`、`agents/deep_chain_native_search.py`、`agents/deep_chain_native.py`、`native/deep_chain_native/src/long_horizon.rs`、同 `lib.rs`。eval runnerと独立fixture・境界テストを併設した。

## 閉ループ結果

| 指標 | Baseline | 候補 |
| --- | ---: | ---: |
| target10達成（独立seed） | 3/8 | 6/8 |
| 最大実連鎖平均（独立seed） | 4.375 | 8.5 |
| premature（両repeat合計） | 4 | 2 |
| game over（両repeat合計） | 4 | 2 |
| 未発火（独立seed） | 3 | 1 |
| decision p50 秒 | 0.559886 | 0.610329 |
| decision p95 秒 | 0.716130 | 0.726974 |
| 平均run時間 秒 | 21.014771 | 24.440583 |
| expanded（全638decision） | 209126138 | 248487582 |
| evaluated（全638decision） | 193467044 | 235276824 |
| peak RSS KiB（最大process） | 522796 | 642524 |

| seed | 最大実連鎖 before→after | 主な結果 | run時間比 repeat1 / repeat2 |
| --- | --- | --- | --- |
| 123 | 10→10 | 成功維持。発火39→26手目 | 1.3727 / 1.3194 |
| 126 | 2→10 | premature1→0 | 1.3557 / 1.3512 |
| 130 | 3→3 | premature1が残る。action一致 | 1.0144 / 0.9986 |
| 133 | 0→0 | 未発火game overが残る。action一致 | 1.0147 / 1.0084 |
| 135 | 0→12 | 未発火を解消 | 1.0815 / 1.0869 |
| 138 | 10→12 | premature増加なし | 0.9877 / 0.9769 |
| 147 | 0→11 | 未発火game overを解消 | 1.3619 / 1.3560 |
| 151 | 10→10 | 成功維持 | 1.1806 / 1.1887 |

123/126/135/147/151は両repeatで時間と実expanded/evaluatedが増えた。123のexpandedは1runあたり12767315→17612514。早い発火後も40手まで再計画するため、変わったtrajectory全体を評価する必要がある。native search平均は0.268510→0.318082秒、materialization平均は0.138546→0.161467秒。全phaseのp50/p95・合計、trajectoryの発火前後内訳は `comparison.json` に保存した。

## 固定盤面と整合性

7盤面について両群各3fresh process×2sample、合計84sampleを収録した。各盤面の全counterは両群一致し、scenarioの保存内容は `selected_fire` を除き一致した。全rootのterminal best fireと代表planのpathが一致した。各群内の全root/代表内容は6sampleで一致する。

| 固定盤面 | 候補/baseline平均時間比 |
| --- | ---: |
| 123 turn30 | 1.1550 |
| 126 turn23 | 1.0503 |
| 130 turn25 | 0.9601 |
| 135 turn34 | 0.9726 |
| 138 turn31 | 0.9957 |
| 147 turn28 | 1.0605 |
| 151 turn32 | 1.0045 |

123 turn30ではmaterialization平均0.111218→0.139581秒、native search平均0.123863→0.132001秒。固定盤面にも時間増が観測されるため、時間悪化の全てをtrajectoryの変化だけに帰属させない。時間非悪化を主張する証拠にはならない。

完全状態parity mismatch、fallback、scenario accounting failure、private future counterfactual差分はいずれも0。全16 seed/condition組でrepeat action/plan/trajectory digestとrequest receiptsが一致した。receiptには実際のrequest semantic digest、全root順位、search digest、設定値semantic SHAと元YAML SHAを別々に記録した。

## QAと保存上の補正

- Python106tests成功。短いqueue1/2/3、depthの1始まり・known境界、target限定、winner/forced/premature、tracker保持・代表、全root Python/native parity、非0cursor拒否、旧v2request拒否を含む。
- Rust49tests成功、既存2件ignore。clippyと変更対象ruffが成功。
- GitHub CI [34327859760](https://github.com/shhchan/puyo-ai-dev-platform/actions/runs/34327859760) が成功（本番コードの最終変更commit3256bf2）。以後の04919bbは計測harnessの設定SHA照合修正だけで、本番コードは同じ。
- 旧head8daabbeのCIはcall-count fixtureの旧期待値1件で失敗した。新期待値は独立fixtureへ置き、過去の `eval/deep_chain_native_corpus.json` と過去のartifact/checksumを変更していない。同corpus SHAは `4027c48d482b21547f6b1d0b554ad949e73439de4a4e09c71a503d2ca722bed4` のまま。
- 最初のharnessは元YAML SHAをparsed semantic SHAと誤比較し、両群合計32attemptが初decision前に `policy_error` となった。`failed-harness-v1/` に全raw・manifest・logを保存した。有効な品質・時間の集計には含めていない。修正後に両環境の1decision smokeとreceipt validationを通し、不完了runはrawを保存して即停止するguardを追加してから全32runをやり直した。
- 有効32runの後、最初のfixed processは保存時の `-inf` ranking sentinelでstrict JSONエラーとなり、fixed rawは未保存だった。`run.log` に失敗を残し、保存時だけ±Infinityを文字列へ写す `fixed_capture.py` を使って6固定processを収録した。NaNは拒否する。adapter SHAは全fixed rawへ保存し、検索・計測区間・04919bbのsource/wheel/manifestは変えていない。
- 計測後のe6022a0では同じsentinel表現をrunner本体にも反映し、以後の再実行を可能にした。strict JSONとNaN拒否の確認ログを保存した。この保存処理修正は計測commitと区別する。

## 証跡と再確認

[比較JSON](../benchmarks/puyo-242-known-prefix-target-fire/comparison.json)、[追加検証](../benchmarks/puyo-242-known-prefix-target-fire/additional-verification.json)、[全証跡archive](../benchmarks/puyo-242-known-prefix-target-fire/evidence.tar.gz) と [SHA256](../benchmarks/puyo-242-known-prefix-target-fire/evidence.sha256) を収録した。archiveには有効32raw、fixed6raw、失敗32raw、設定・host・manifest、事前診断、実wheel2本、計測sourceの部分snapshot、測定/検証scriptと全logを含む。

元の作業証跡は `/home/sion2/workspaces/puyo-small-quality-20260909/puyo-242-evidence`。baseline全root事前資料は同rootの `puyo-240-evidence/seed-{seed}-all-roots.json.gz` で、今回の事前診断へ各source SHAを保存している。元資料は上書きしていない。

```bash
sha256sum -c docs/benchmarks/puyo-242-known-prefix-target-fire/evidence.sha256
# archiveを任意の新しい作業ディレクトリへ展開し、展開先をEVIDENCEとする。
PYTHONPATH=. .venv/bin/python eval/puyo242_summarize.py "$EVIDENCE"
.venv/bin/python "$EVIDENCE/verify_additional.py"
```

計測の再実行はbaseline commitと候補の計測commit04919bbを別worktree/venvへ固定し、archiveの実wheel・設定checksumを照合する。`eval/puyo242_run_all.py` は同一seed/repeatの逐次workerを実行する。04919bbの固定保存を再現するときはarchiveの `run_fixed.py` / `fixed_capture.py` を使用する。新規runner本体の保存処理はe6022a0以降で修正済みである。

新規の人間GUI観測は未実施。PUYO-236の30seeds×2repeats、統合採用、master/release/tag/promotionは行っていない。局所品質改善を最終baseline合格とは扱わない。

## References

- [PUYO-242](https://shhchan.atlassian.net/browse/PUYO-242)
- [PUYO-233](https://shhchan.atlassian.net/browse/PUYO-233)
- [PUYO-236](https://shhchan.atlassian.net/browse/PUYO-236)
- [PUYO-232 safe-build contract](puyo-232-safe-build-target-contract.md)
