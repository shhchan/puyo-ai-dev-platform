# PUYO-236: 4条件の比較評価

PUYO-240（弱いscenario支持のguard）とPUYO-242（公開既知prefix内の目標発火優先）を、無変更・240のみ・242のみ・両方の4条件で比較する。今回は判断材料の収集であり、採用パターンはユーザーが後から選ぶ。既定実装への適用、merge、master、release、tag、promotionは行わない。

240/240 runを完了し、全9,592 decisionの厳密検証と独立raw監査が成功した。達成率はnoneの83.33%から、only240で90.00%、only242で93.33%、bothで96.67%になった。一方、平均run時間はそれぞれ+0.73%/+7.55%/+9.36%であり、bothでもseed133のgame overが残る。only242/bothには目標達成を維持しながら最大連鎖が下がる7seedもある。これは比較結果であって、どの条件も自動採用しない。

## 結果と未達条件

品質の分母はrepeat1の30固有seed。全条件でrepeat2のaction/plan/trajectory/receiptが一致したため、repeat2を独立標本として足さない。時間は全60run/条件、decision数は各2,398。全条件でseed133だけ39placementsで終了し、他は40placementsまで評価した。noneのseed147も40手目のgame overを失敗として数える。未完了run・検証失敗・再試行は0。

| 指標 | none | only240 | only242 | both |
| --- | ---: | ---: | ---: | ---: |
| target10達成（30seed） | 25/30 (83.33%) | 27/30 (90.00%) | 28/30 (93.33%) | 29/30 (96.67%) |
| 達成率の対none差 | — | +6.67 pp | +10.00 pp | +13.33 pp |
| 達成＋premature0＋game overなし | 25/30 | 27/30 | 28/30 | 29/30 |
| 最大実連鎖平均 | 9.0333 | 9.7000 | 9.8667 | 10.0333 |
| 同平均の対none相対差 | — | +7.38% | +9.23% | +11.07% |
| 最大実連鎖中央値 | 10 | 10 | 10 | 10 |
| premature回数（repeat1 / 両repeat） | 2 / 4 | 1 / 2 | 1 / 2 | 0 / 0 |
| premature発生seed数 | 2 | 1 | 1 | 0 |
| game over seed数（両repeat件数） | 2 (4) | 1 (2) | 1 (2) | 1 (2) |
| 未発火seed数 | 3 | 2 | 1 | 1 |

最大実連鎖の分布（`連鎖数:seed数`）は、none=`0:3, 2:1, 3:1, 10:16, 11:2, 12:7`、only240=`0:2, 2:1, 10:15, 11:5, 12:7`、only242=`0:1, 3:1, 10:20, 11:4, 12:3, 13:1`、both=`0:1, 10:23, 11:2, 12:3, 13:1`。平均の相対差はこの指標だけの変化であり、総合的な「品質改善率」ではない。

| 従来の絶対条件 | none | only240 | only242 | both |
| --- | --- | --- | --- | --- |
| 最大実連鎖平均 >=10 | FAIL | FAIL | FAIL | PASS |
| premature=0 | FAIL | FAIL | FAIL | PASS |
| game over=0 | FAIL | FAIL | FAIL | FAIL |
| decision p95 <=1秒（監査込み観測値） | PASS | PASS | PASS | PASS |
| 完全状態parity/private/fallback/accounting/candidate-plan | PASS | PASS | PASS | PASS |
| 新規人間GUI QA | 未実施（過去lineage確認のみ） | pending | pending | pending |
| 採用 | ユーザー選択待ち | ユーザー選択待ち | ユーザー選択待ち | ユーザー選択待ち |

時間増だけで比較を打ち切らず、全4条件を完走した。絶対条件のFAILはそのまま残し、目標30/30達成を新しい必須gateとして追加することもしていない。

### seed別の救済・退行

対noneで、既にtarget10に成功していたseedを失った候補は0。only240はseed130（3→11）・147（0→11）を救済し、最大連鎖低下も0。only242は126（2→10）・135（0→12）・147（0→11）を救済する。bothは126（2→10）・130（3→10）・135（0→12）・147（0→10）を救済する。

only242/bothの最大連鎖低下は、124・125（12→11）、137・139・143・149（12→10）、152（11→10）の7seed。すべて追加22seedに属し、目標10は維持した。逆にonly242/bothでは127（12→13）・138（10→12）・145（11→12）が増加した。only242だけ129（10→11）、only240だけ150（10→11）が増加した。

全条件共通の失敗はseed133の未発火game over。その他の残存失敗はnoneで126/130の早発火・135の未発火・147のgame over、only240で126の早発火・135の未発火、only242で130の早発火。bothの未達は133のみ。

| 比較（左→右） | 目標救済seed | 目標成功喪失seed | 達成率差 | 最大連鎖平均差 | 平均run時間差 |
| --- | --- | --- | ---: | ---: | ---: |
| none→only240 | 130,147 | なし | +6.67 pp | +0.6667 | +0.73% |
| none→only242 | 126,135,147 | なし | +10.00 pp | +0.8333 | +7.55% |
| none→both | 126,130,135,147 | なし | +13.33 pp | +1.0000 | +9.36% |
| only240→only242 | 126,135 | 130 | +3.33 pp | +0.1667 | +6.77% |
| only240→both | 126,135 | なし | +6.67 pp | +0.3333 | +8.56% |
| only242→both | 130 | なし | +3.33 pp | +0.1667 | +1.68% |

bothはonly242に対し130を救済する一方、129/147が11→10になる。only240に対しては、前述7seedに加え130/147/150が11→10になる。したがって成功率と最大連鎖の両方を全seedで同時に改善する変更ではない。全30seed・全6比較の個別値、発火placement、repeat別時間差/比、node差は[comparison.json](../benchmarks/puyo-236-four-pattern-comparison/comparison.json)に保存した。

### 旧8seedと追加22seed

旧8seedは過去の小規模試行で選定済みの123,126,130,133,135,138,147,151。全体の達成率増加はこの層で発生した。追加22seedは全条件22/22成功のため、今回の追加範囲で達成率の改善を確認したとは言わない。

| 層・指標 | none | only240 | only242 | both |
| --- | ---: | ---: | ---: | ---: |
| 旧8: target10達成率 | 37.50% | 62.50% | 75.00% | 87.50% |
| 旧8: 最大連鎖平均 | 4.375 | 6.750 | 8.500 | 9.250 |
| 旧8: 平均run秒（各16run） | 19.582 | 20.528 | 22.467 | 23.609 |
| 追加22: target10達成率 | 100% | 100% | 100% | 100% |
| 追加22: 最大連鎖平均 | 10.7273 | 10.7727 | 10.3636 | 10.3182 |
| 追加22: 平均run秒（各44run） | 23.095 | 22.972 | 24.327 | 24.458 |

追加22の最大連鎖平均は対noneでonly240 +0.42%、only242 -3.39%、both -3.81%。結果はこの固定30seed内の記述比較であり、母集団への有意差・汎化や総合順位を主張しない。

## 時間・探索量・RSS

計測区間は2026-09-10 13:08:56–14:44:38 UTC（22:08:56–23:44:38 JST）、95分42秒。rawのtimerは変更せず、以下は監査receipt作成を含む観測値。p95は`(N-1)*0.95`位置の線形補間。

| 時間指標（秒） | none | only240 | only242 | both |
| --- | ---: | ---: | ---: | ---: |
| run平均 | 22.1580 | 22.3202 | 23.8312 | 24.2317 |
| run p50 | 22.5260 | 22.6315 | 24.4276 | 24.6466 |
| run p95 | 25.4175 | 25.1019 | 26.6950 | 26.8740 |
| run最大 | 26.5947 | 25.3748 | 27.9762 | 28.2033 |
| decision平均 | 0.495020 | 0.498376 | 0.535153 | 0.544648 |
| decision p50 | 0.556018 | 0.556850 | 0.576147 | 0.577256 |
| decision p95 | 0.687686 | 0.680054 | 0.688579 | 0.688065 |
| decision最大 | 0.829658 | 0.825409 | 0.792770 | 0.831678 |
| receipt監査平均/decision | 0.017635 | 0.017824 | 0.018424 | 0.018529 |
| receipt監査p95/decision | 0.028795 | 0.027392 | 0.031806 | 0.030969 |
| 監査差引run平均（参考推定） | 21.4532 | 21.6078 | 23.0949 | 23.4911 |
| 監査差引decision p95（参考推定） | 0.669229 | 0.661130 | 0.669130 | 0.669257 |

差引参考run平均の対none差は+0.72%/+7.65%/+9.50%。これは監査のない製品を再計測した値ではなく、タイミングやallocationへの間接影響までは取り除かない。各seedの両repeat平均run時間差は、only240で中央値-0.0664秒・範囲[-1.1094,+6.0839]秒、only242で+1.1337秒・[-4.0778,+6.6658]秒、bothで+1.0824秒・[-0.8682,+7.9995]秒。全seed一様な時間増を意味しない。

主表の時間増率は全60runの平均の比。repeat1/2別にはonly240 +1.04%/+0.42%、only242 +7.81%/+7.29%、both +9.66%/+9.06%だった。特にonly240の小差を、統計的に確定した一般的な性能劣化とは断言しない。

| phase平均 / p95（ミリ秒） | none | only240 | only242 | both |
| --- | ---: | ---: | ---: | ---: |
| native search | 289.460 / 440.063 | 291.216 / 431.890 | 318.668 / 440.014 | 324.842 / 440.891 |
| native aggregation | 0.077 / 0.132 | 0.080 / 0.144 | 0.081 / 0.148 | 0.083 / 0.154 |
| native serialization | 0.460 / 0.739 | 0.456 / 0.715 | 0.480 / 0.825 | 0.491 / 0.804 |
| Python materialization | 138.578 / 192.040 | 139.460 / 190.910 | 147.093 / 196.036 | 149.667 / 194.996 |
| backend total | 428.902 / 613.176 | 431.543 / 608.974 | 466.657 / 614.945 | 475.418 / 617.049 |

phaseには包含関係があるため、p95同士やbackend totalと内訳を足さない。細かなflow/phaseのp50・最大・総量もcomparison.jsonに残す。

| 探索量・メモリ（各60run） | none | only240 | only242 | both |
| --- | ---: | ---: | ---: | ---: |
| 実expanded nodes合計 | 888,966,236 | 902,911,900 | 970,131,576 | 992,608,496 |
| 実evaluated nodes合計 | 841,359,778 | 853,799,094 | 929,285,096 | 951,283,270 |
| peak RSS最大（MiB） | 173.254 | 173.449 | 173.438 | 173.441 |

予算は同じでも順位変更後の実trajectoryと探索量が異なるため、run時間差はrank comparator単体の費用ではない。RSSはfresh process全体のhigh-water markであり、新環境に揃えた4条件内の比較に限る。旧環境のRSSとの比は出さない。

canonicalの同一初期盤面cold/warm診断（各1組、品質run外）はnone 0.5607/0.5049秒、only240 0.5621/0.5527秒、only242 0.5723/0.5457秒、both 0.5577/0.5656秒で、各組とprivate counterfactualのdigestが一致した。少数診断であり高速化率の推定には使わない。実trajectory初手/以降のdecision p95はnone 0.5766/0.6880秒、only240 0.5799/0.6811秒、only242 0.5874/0.6891秒、both 0.6119/0.6891秒。これらを同一盤面のcold/warmと混同しない。

## 条件とソース

全条件は `73ab4e8ce066555042f1a20e1b3b59be3a2a8968` を起点とする。controlブランチでは評価script・独立patch・証跡だけを追加し、通常のruntimeファイルは変更しない。

| 条件 | 計測ソース | 規則 |
| --- | --- | --- |
| none | `73ab4e8ce066555042f1a20e1b3b59be3a2a8968` | 既存順位 |
| only240 | `6fb0e68c8ba20958e7d9bb40d936eb0ce4f990ce` | 既存min2/6 weak-support guard |
| only242 | `f9f9b3b7bcc526b9b990edf7a9ca9ebd926699f8` | 公開known_pairs内targetをterminal scoreの前で比較 |
| both | `429f3b2204ece3a51e3b92d0c36099f5ebdb0897` | 同じ2案の合成 |

only240のruntimeは旧計測`2e1fe2b`、only242のruntimeは旧計測`04919bb`と同一。240の過去corpusの期待値変更は取り込まず、旧corpusを固定SHAのまま保持して独立fixtureで新しいsemantic期待値を検証する。only240/bothで過去に失敗した3件の証跡整合性testも成功した。

bothのroot比較順は、guardによるclass比較値 → coverage → class支持数 → 既知prefix内target → 既存terminal合計・score/count・dispersion・action。weak targetが既知prefix内でも、全6scenarioで最終深さの非fatal quiet継続を証明した代替を追い越さない。winning/forced-safety保護、terminal分類、深さ16に保持された実survivor自身の深さ・値を要求する条件は維持する。242のfire/tracker保持・root代表planの規則も同じにする。

単独版のranking identityは過去と同じ。両方版には専用の`puyo.expected_chain_ranking.v4`（guard適用evidenceは`v4.guarded`）を使い、Python codecとRustが旧v2/v3 requestを拒否する。固定fixtureに限りidentity sectionを明示移行し、それ以外のsection bytes・request IDが不変であることと、前後SHAを保存する。

## 固定した計測方法

target10 / safe_no_threat / reference / native scenario-6 / depth16 / width250 / scenarios6 / expanded cap600,000。evaluatorとquiescenceは据え置きで、241のweight変更や追加探索は含めない。共通設定SHAは`d347818c6bddb25dfa35af6bd5d4916179bd19a0f2cc7eeebfed08293be72b5f`。

各条件seed123–152×repeats1/2、各runは40 placementsまたは正当なgame overまで評価する。合計240run、独立seedは30。repeat2は決定論と時間の変動確認であり、独立seed数を60に増やさない。旧試行で選んだ8seedと、新たな22seedの結果を別途示す。

4条件の専用worktree・venv・release wheelを新規作成する。同一host上でfresh processを1runずつ逐次実行し、seed/repeatごとに条件順を回転させる。各条件は各順序位置に15回ずつ現れる。実際の開始・終了時刻、全順序、source tree・runtime files・設定・runner・wheel・native extension・依存package・hostのSHA/metadataをmanifestとprocess receiptへ保存する。

全条件に同じ必要依存packageをインストールし、未使用のtorch等の訓練依存は含めない。今回のbaseline wheelは固定baselineソースから専用環境で再buildしたものなので、旧wheelのSHAとは異なる。旧8seedのaction/plan/trajectory/品質/実node数の再現を別出力で確認する。旧試行の時間やRSSは今回の4条件の分母に流用しない。

canonical runnerのdecision/run timerはそのまま保存する。今回追加するreceipt作成・全root evidence hashの時間を各decisionで別計測し、監査込みの観測時間、監査費用、差引参考値を区別する。差引値は新しい製品実測値ではなく参考推定である。全探索objectをrun終了まで保持する方式は使わず、各decisionで小さいreceiptへ変換する。

全decisionでPython/Rustの全root順位、scenario accounting、選択candidateと代表plan、完全状態parity、fallback0、探索予算を厳密照合する。正常な代表の実state bytes SHAから、探索側の`compact-`付き指紋とplan側の24桁指紋を別々に検証する。探索代表がない場合はunavailable・best fireなし・quiet survivorなしの根拠を要求し、既存の1手root-only recoveryを再構成してplanの全stepと照合する。各seedのaction/plan/trajectory/receiptはrepeat間で一致を要求する。公開境界は30seedのsentinel監査と実policy counterfactualで検証する。cold/warm同一初期盤面はcanonical診断をfresh preflight processの最初に実行し、trajectoryの初手/以降とは分ける。

未完了や検証失敗が発生したrunはraw/process receiptを保存して停止する。既存attemptは上書きせず、成功したrunでもmanifest・raw SHA・process receiptが一致しないresumeは拒否する。正当なranking ±Infinityは保存時だけ文字列とし、NaNは拒否する。

## 事前QAと人間確認の範囲

関連Pythonテストはnone65、only24067、only24272、both74件が成功。候補3条件のRustは48/49/49件成功、既存ignoreは各2件。fmt/clippyも成功。新しい比較protocolは専用CI workflowで検証する。

protocolの10件は、240runの順序均衡、strict JSONの±Infinity/NaN、fixture移行の全bytes保存、固有seedを分母にした集計、candidate/plan・parity・accountingの破損拒否、2種類のrequest adapter、JSON保存後のinit/resume、実native binaryとwheel内binaryの一致、正当なgame overのroot-only recovery、実際の正常代表とplanの指紋形式を検証する。

最初の固定preflightは、NativeDecisionRequestの`state/config_digest`とBackendRequestの`root_state/evaluator_config_sha256`の型の違いで停止した。失敗processと、その準備世代のcold/warm結果を保存した。明示adapterを追加し、search YAML SHA・evaluator YAML SHA・evaluator semantic SHAを別々に照合するtestを追加して、新しい準備出力で全条件をやり直した。全品質run開始前の準備失敗であり、本測定の品質・時間には含めない。

第2準備世代では4条件の固定44sample・30seedずつのprivate/cold-warm確認が成功したが、次段階へ移る際の宣言比較で停止した。JSON保存時に順序集計の整数keyが文字列へ変わる差を修正し、保存後の再initをテストした。また親の独立確認で、native拡張のSHA欄がpackage wrapperの`__init__.py`を指していたことを検出した。実拡張submoduleの`.so`とwheel内の同binaryのSHA一致を必須にし、wrapper SHAは別欄へ分離した。旧準備出力は保存し、第3準備世代の新manifestで実際のinit2回・4arm smokeを通した。

第3準備世代の再現評価では、9run成功後のnone seed133・39手目を追加validatorが拒否した。これは完全状態parity成功・fallback0の正当game overで、探索survivorがないときの既存root-only recoveryを常にnative代表必須とした監査側の誤りだった。独立した過去入力fixtureで既存recoveryの全stepを再構成し、recovery欠落・action/state/prediction変更・quiet候補の代表欠落を拒否するtestを追加した。game over自体は品質上の失敗として残す。

第4準備世代では正常代表の終端照合が、探索側とplan側の指紋表記の差でsmokeを拒否した。実state bytes SHAから両形式を明示生成して別欄へ保存・検証するよう修正し、実探索の正常代表でtestを追加した。これらの準備失敗も原本を保持した。

runner `29dc88e` の第5準備世代では、全4条件のinitを2回実行した後、smoke、12固定盤面ずつ（合計48sample）、30seedずつのprivate counterfactual、cold/warm、旧8seed×単独3条件の24run再現がすべて成功した。action・plan・trajectory・最終盤面・品質・正当な終了理由・実node accountingは旧結果と一致し、独立監査もPASS。fixed requestではseed130/turn7のaction2→18とseed147/turn7の15→0が240/bothだけで発生し、seed126/turn22の21→6が242/bothだけで発生した。seed135/turn10・11はguard非発動を確認した（turnは0始まり）。この準備runの時間は本計測へ流用しない。

既存PUYO-235の人間観測はruntime `c1267a9`、seed187/reference/native/target10。関連runtimeソースは今回noneの基点まで差分がなく、過去GUI証跡verifierもissues0で再確認した。これは過去観測のlineage確認であり、今回の新しいwheel上の人間観測を意味しない。only240/only242/bothの新しい人間GUI確認はpending。自動・dummy回帰成功を人間QAのPASSに読み替えない。

## 証跡と確認手順

[証跡索引・復元/再集計手順](../benchmarks/puyo-236-four-pattern-comparison/README.md)に4条件別raw、全240process receipt、全準備世代、4wheel、正確なsource-arm bundle、QA log、独立監査を保存した。archive作成時に全693 fileの元bytes一致を確認した。計測前後のsource/build/config/runner不変、全raw/全digest/120repeat pair/集計一致について親の独立監査もPASS。

凍結runnerは`29dc88e807aafba7a1d17196d7384a441d25b318`。証跡・報告書追加後のcontrol HEADで既存outputへinit/resumeすると、commit不一致で意図どおり拒否される。既存測定の再開には凍結runnerの別worktreeを使う。offline再集計は新たな探索・採用変更をせずに実施できる。

## ユーザー選択後

ユーザーが4条件のうち採用したいパターンを選び、その後に採用修正を依頼する。対象実装と未達gate・新規人間QAの扱いをその依頼で確定し、対応するJira/作業ブランチで採用変更と必要な検証を行う。今回の比較PRや保存patchだけで既定採用は起きない。

## References

- [PUYO-236](https://shhchan.atlassian.net/browse/PUYO-236)
- [PUYO-240](https://shhchan.atlassian.net/browse/PUYO-240) / [実験PR #129](https://github.com/shhchan/puyo-ai-dev-platform/pull/129)
- [PUYO-242](https://shhchan.atlassian.net/browse/PUYO-242) / [実験PR #131](https://github.com/shhchan/puyo-ai-dev-platform/pull/131)
- [PUYO-235](https://shhchan.atlassian.net/browse/PUYO-235)
