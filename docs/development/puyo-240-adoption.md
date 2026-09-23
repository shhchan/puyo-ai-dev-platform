# PUYO-240: only240 の採用

PUYO-236の4条件比較後、ユーザーが選択した`only240`を採用する。比較で確認した平均run時間の約+0.73%と、残る品質未達を承知した上での選択であり、品質gate達成や新しい人間GUI QAのPASSを意味しない。

既存の`deep_chain_builder`のPython/native実装に同じguardを含める。`only240`専用の選択メニューは追加しない。PUYO-241の評価weight変更、PUYO-242の公開既知prefix優先は含めない。

## 採用範囲

safe-build・6scenario・record-and-stopで全rootの6scenarioが評価済みの場合に、target支持が2/6未満のrootと、全6scenarioで最終深さの非fatal quiet継続を証明できるrootの優先順位を調整する。winning/forced-safetyがある場合や、有効な代替がない場合はguardを適用しない。fire分類・plan・探索予算・既存tie-breakは維持する。

- 起点: `73ab4e8ce066555042f1a20e1b3b59be3a2a8968`
- 選択済みの計測source: `6fb0e68c8ba20958e7d9bb40d936eb0ce4f990ce`
- 移植commit: `380d1d5097670532c9ac2bf2960e4dc4fd5b9ebb`
- 移植時のtree: `2635c82ef403375308411ee697730254716e5231`。選択sourceと全treeが一致する。
- runtime変更は`agents/long_horizon_search.py`、Python native境界/adapter、Rustの`lib.rs`/`long_horizon.rs`。survivor自身の深さを返すcodecと関連fixture/testを含め、選択sourceをそのまま移植した。
- historical corpusのSHAは`4027c48d482b21547f6b1d0b554ad949e73439de4a4e09c71a503d2ca722bed4`を維持する。新semantic期待値は独立fixtureに保存し、旧PR #129で失敗した歴史証跡の整合性を壊さない。

固定条件はreference/native/target10、safe_no_threat、depth16、width250、scenarios6、expanded cap600,000、40 placementsまたは正当なgame over。共通config SHAは`d347818c6bddb25dfa35af6bd5d4916179bd19a0f2cc7eeebfed08293be72b5f`。支持閾値・evaluator・quiescence・探索予算を変更していない。

## 検証と残る未達

専用worktree/venvで移植commitからrelease wheelをbuildし、strict source provenanceと、wheel内・インストール済みの実native `.so`のSHA一致を確認した。以下の証跡は移植commitに対するもの。採用記録・証跡の追加ではruntimeを変更していない。

- Rust: 48件成功、既存ignore 2件。fmt/clippy/既存CI対象のruffも成功。
- 既存native CIのPython main suite: 281件中280件成功、launcher checkpoint test 1件が専用venvのtorch不足でerror。元の失敗logを保持し、CPU版torch補完後に当該1件の成功を確認した。
- 続きのghost等99件、追加のlong_horizon_search 18件、全historical verifier/checksum確認は成功。CIの実行commandと各結果は保存済み。
- 重点6seedを各repeat1・fresh processで逐次実行し、旧only240のrawに対する239手のaction/plan/trajectory/最終盤面/実結果/全counters/全root receipt一致を確認した。全root順位、candidate-plan、完全状態parity、scenario accountingも検証し、fallback/parity mismatchは0。

| seed | placements | 最大実連鎖 | premature | game over |
| --- | ---: | ---: | ---: | --- |
| 123 | 40 | 10 | 0 | なし |
| 126 | 40 | 2 | 1 | なし |
| 130 | 40 | 11 | 0 | なし |
| 133 | 39 | 0 | 0 | あり |
| 135 | 40 | 0 | 0 | なし |
| 147 | 40 | 11 | 0 | なし |

この6seed確認は移植の意味的再現であり、新しい品質母集団や時間比較ではない。60/240runの再実行はしていない。約+0.73%はPUYO-236の元の固定比較における平均run時間差であり、今回の実行時間から再計算した割合ではない。

元の30固有seedではtarget10達成27/30、最大実連鎖平均9.7、repeat1のpremature 1回、game over 1seed。平均>=10・premature=0・game over=0の絶対条件は未達のまま。新しい人間GUI QAはpendingで、自動/dummy回帰や過去PUYO-235の観測を今回の人間PASSには読み替えない。

## 証跡と人間の確認

[validation.json](../benchmarks/puyo-240-adoption/validation.json)にsource/build/config、実`.so`、helper、各raw/比較結果のSHAとarchive索引を保存した。

- [reproduction.tar.gz](../benchmarks/puyo-240-adoption/reproduction.tar.gz): 新しい6 raw、manifest、6 process receipt、個別比較checks、再現summary。20ファイル、元bytes一致確認済み。
- [qa-logs.tar.gz](../benchmarks/puyo-240-adoption/qa-logs.tar.gz): build/依存・Rust/Python/verifier・初回torch不足・補完後の再確認を含む37ファイル。初回の失敗と成功した後続段階を区別する。
- 元の展開済み証跡: `/home/sion2/workspaces/puyo-240-adoption-20260911/reproduction-implementation/`、`qa/`。
- 統合後にも使える再現helper: `/home/sion2/workspaces/puyo-240-adoption-20260911/reproduce_only240.py`。対象checkout自身のvenv/release wheelと新しい出力先を要求し、旧worktree/rawへの書込は行わない。

archiveは別の空directoryへ展開する。`reproduction-implementation/manifest.json`がbuild/sourceの正本で、`reproduction-summary.json`と`seed-*-comparison.json`が旧rawとの一致結果を保持する。SHAは`validation.json`の`archives`および個別raw欄で確認できる。

人間GUI確認では、採用PRを取り込んだcheckoutでrelease wheelを再buildし、`.venv/bin/python main.py`から既存のdeep_chain_builderをreference/native/target10で使用する。この実装にはonly240が含まれる。新しい観測結果は別途記録する。

## References

- [PUYO-240](https://shhchan.atlassian.net/browse/PUYO-240)
- [PUYO-236の比較PR #132](https://github.com/shhchan/puyo-ai-dev-platform/pull/132)
- [旧実験PR #129](https://github.com/shhchan/puyo-ai-dev-platform/pull/129)（今回の移植sourceはfixture修正済みの6fb版）
