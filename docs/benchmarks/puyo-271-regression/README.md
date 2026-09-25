# PUYO-271：変更前の反例 corpus と baseline

## 固定した範囲

source は `c0c77d944ff3ed276a4b44a40a779cd72bc0977a`，統合先は `integration/puyo-228-v1-8-0`．`tests/fixtures/puyo_271_regression_cases.json` は PUYO-267 の調査で得た盤面を合成 fixture として明示する．ユーザーの元 seed，GUI replay，profile receipt は提供されていないため，この fixture や seed 55/123/124 の対局を元の GUI 事象の完全再現とは扱わない．

変更前の実測は `before/` の raw gzip 6 件，元 runner の `declaration.json`／`summary.json`，PUYO-271 の `regression_analysis.json`／`regression_manifest.json` に固定した．`synthetic_results.json` は合成 fixture の authoritative simulator／既存 matcher による結果である．`regression_manifest.json` は source／config／wheel SHA，host，affinity，thread 環境，run 順，cache 条件，raw checksum を保存する．host／affinity／thread は raw runner が埋め込まなかったため，同一 host で測定直後の解析時に採取した．各 run 中の affinity 変化までは raw から証明できない．run ごとの完全な公開入力と decision／receipt は raw gzip に残す．

| 条件 | 固定値 |
| --- | --- |
| seed／repeat／配置 | 55，123，124 × 各 1 回，最大 40 配置 |
| 対局 | `SafeNoThreatMatch`，相手は非活動，公開 current／NEXT／NEXT2，configured 0 inference ticks |
| deep_chain_builder | `reference`，native，target 10／quality floor 10，depth 16／width 250／6 future／最大 600000 expanded nodes |
| nextgen | `nextgen_safe_build`，native，shared 600000／template 128／response 256 の独立 quota，GTR → 自由構築 |
| 実行 | 1 process 内で policy instance を run ごとに新規生成し，nextgen 55/123/124 → deep_chain 55/123/124 の順に直列実行 |

両 policy は同じ `SafeNoThreatMatch(seed)` を使うが，内部 adapter の公開情報処理は同一ではない．nextgen は hidden 行を除き，deep_chain の既存 adapter は own ghost 行を参照する．future の public seed derivation も異なる．この制約は元 declaration にも残しており，action の逐手一致や純粋な model 差とは解釈しない．

## 合成反例

ペルシャ式の A=1／B=2／C=3 binding で，底からの A `(1,1),(2,1)` と，さらに `(1,2)` を持つ L 字は，配置前の静的 satisfied=2／conflicts=0 が同じである．公開 current `(1,2)` を 0 始まり `axis_x=2, UP` に置くと，前者は chain 0，後者は chain 1 で土台 A を消す．L 字には静かな合法代替が 14 件，そのうち現在の selected binding の静的進捗を損なわない代替が 2 件ある．固定 binding の matcher は両方 `fit` を返すため，ここでは候補なしと順位／採用の問題を混同しない．これは「L 字一般を禁止」ではなく，選択された binding の基礎を理由なく消す候補の反例である．

窒息 fixture は 3 列目を 11 段まで埋め，上 3 個を A にする．同じ current `(1,2)` の `axis_x=2, UP` は必要な 1 連鎖で生存し，`DOWN` は chain 0 で game over となる．したがって生存に必要な単発消しを一律に拒否できない．GTR／だぁ積み／ペルシャ式の各 2 手 fixture は合法 witness のまま chain 0 で完成する．公開 current 既知／未知／node budget 0 も別 probe とし，未知や cutoff を no-fit 証明に読み替えない．

## 変更前実測

| policy | seed 55／123／124 の最大実連鎖 | premature | game over | decision p50／p95（件数） |
| --- | --- | ---: | ---: | ---: |
| nextgen | 12／10／10 | 0 | 0 | 0.706／0.885 s（120） |
| deep_chain_builder | 1／10／10 | 1 | 1（seed 55，33 配置） | 0.544／0.642 s（113） |

全 seed・repeat の配置数，未完了理由，selection tactic，shared-search 順位との差，実採用 receipt は `before/regression_analysis.json` に独立列として保存する．nextgen の 120 件のうち shared-search 先頭以外の採用は 44 件，receipt の非採用・要求／実行 action 不一致は 0 件だった．shared-search 順位差はそのまま戦術選択ミスではない．候補不足の確定には公開 oracle が要るため，対局の `candidate_gap` は null と理由を記録した．既存 runner は比較可能な score ledger を出力しないため，score も null とする．合成盤面は形状の反例であり，3 seed の対局にペルシャ式や窒息が生じたという主張ではない．

GUI frame／input／event cadence は headless 実測から算出できない．`regression_analysis.json` は null とし，PUYO-269 の trace に依存する．PUYO-264 の通常速度／低速差，人間 GUI QA もこの前半では未検証である．

## 再実行と後半への引渡し

次の軽量確認は native 再 build を必要としない．

```bash
python -m eval.puyo_271_regression fixtures --output /tmp/puyo271-synthetic-results.json
python -m eval.puyo_271_regression verify --run-dir docs/benchmarks/puyo-271-regression/before
python -m unittest tests.test_puyo_271_regression
```

改善後は 270／272／268／269 の統合 head と release native wheel を固定し，専用の空 directory で同じ runner を実行する．CPU 測定は他の重い job と直列化し，source／config／wheel／host／affinity／thread／cache を baseline manifest と照合する．

```bash
python -m eval.nextgen_safe_build_diagnostic \
  --output /tmp/puyo271-after --profile nextgen_safe_build --repeats 1
python -m eval.puyo_271_regression analyze \
  --run-dir /tmp/puyo271-after --phase after \
  --wheel dist/native/puyo_deep_chain_native-0.4.0-cp312-cp312-manylinux_2_28_x86_64.whl
python -m eval.puyo_271_regression verify --run-dir /tmp/puyo271-after
```

上の wheel 名は変更前に使った package 名・ABI の例であり，改善後はその source SHA から作った実際の release wheel path を指定する．

`--phase after` の結果は全 seed の max actual chain，premature，game over，decision p50／p95，shared ranking，receipt を同じ列に出す．未完了は分母から消さず理由を保存する．後半では合成 fixture を同じ公開入力・予算で再実行し，270 の生存例外，272 の制約あり／なしと Python/native parity，268 の選択 binding／候補順位／実採用，269 の GUI cadence を個別に接続する．片側／両側 GUI の予定条件は fixture の `gui_scenarios` に保存した．PUYO-269 の測定・人間 QA の起動条件と trace を別 artifact として比較する．人間確認には次の既存 GUI 経路を使い，seed 55，native，GTR，argmax，N=14，safe-build を固定する．両側条件では `--policy-b nextgen_tactic_manager` にする．

```bash
python -m eval.realtime_versus_ui --seed 55 \
  --policy-a nextgen_tactic_manager --policy-b random \
  --nextgen-templates gtr --nextgen-selection-mode argmax \
  --nextgen-seed 55 --nextgen-commit-turns 14 \
  --nextgen-profile nextgen_safe_build --nextgen-backend native
```

この 3 seed×1 回は診断 sample であり，正式 G2 30 seed×2 repeat の実施／合格ではない．正式 G2 と残る 10 連鎖級品質・人間 GUI QA は PUYO-266，通常速度の採用確認は PUYO-264 に残る．PUYO-271 は改善後比較まで In Progress とする．

## 参照

- [PUYO-271](https://shhchan.atlassian.net/browse/PUYO-271)
- [PUYO-267 の調査記録](https://shhchan.atlassian.net/browse/PUYO-267)
- [PUYO-255 の gate 証跡](../puyo-255-gates/README.md)
- [PUYO-266 の safe-build 証跡](../puyo-266-safe-build/README.md)
