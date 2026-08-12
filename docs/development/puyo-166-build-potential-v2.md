# PUYO-166: BuildPotential v2

## 目的

BuildPotential v2 は、静かな盤面に残る連鎖構築余地を、特定の定型や名称へ寄せずに評価する。
従来の v1 が返していた「単一列へ 1〜3 個追加したときの最良発火」だけでなく、次を構造化した
diagnostics として扱う。

- 予測連鎖ポテンシャル
- 発火までの追加ぷよ数、最短 turn 数、使用列数
- 複数列を含む代替発火
- 発火点の同一性、同等性、追加 2 ぷよ以内の回復可能性
- 継続手の柔軟性
- 危険域までの余裕

これは盤面だけを入力とする決定的な指標であり、current / NEXT の色や乱数には依存しない。

## schema と互換性

`puyo.build_potential.v1` は旧 evaluator の互換契約である。探索は単一列、追加 3 ぷよまでで、
JSON は `chain_count`、`required_puyos`、`trigger`、`trigger_color` の 4 項目をそのまま返す。

`puyo.build_potential.v2` は、上記の旧項目を残したまま次の項目を追加する。

- `evaluation_status` と `exists`
- `predicted_chain_count` と 0〜1 の `predicted_chain_potential`
- `ignition_cost`
- `trigger_alternatives`、`trigger_equivalence`、`trigger_recoverability`
- 0〜1 の `continuation_flexibility` と `danger_margin`
- 探索 budget、実消費 node 数、完走可否、打ち切り理由を持つ `search`

StateAnalyzer の diagnostics は v2 を `own.build_potential` と
`opponent.build_potential` に JSON-safe な辞書として載せる。一方、学習済み manager が読む
AnalyzerInput と 77 次元の特徴量順序・tensor shape は変更しない。したがって既存重みの配列を
reshape したり、v2 の値を暗黙に 77 次元へ差し込んだりしない。

## status の意味

| status | 意味 |
|---|---|
| `available` | budget 内を完走し、発火候補を発見した |
| `not_found` | budget 対象を完走し、候補がなかった。評価済みのゼロである |
| `budget_exhausted` | node 上限で打ち切った。候補があれば暫定値を利用できるが、候補なしはゼロを証明しない |
| `not_evaluated` | decision の probe 対象外、または decision probe budget を超えた |
| `legacy_partial` | 盤面なしで、非ゼロの v1 値だけを v2 へ投影した |
| `unknown` | 盤面なしで v1 のゼロを受け取り、「探索済みゼロ」か「旧実装で未探索」か判定できない |

`budget_exhausted` は探索を実施した状態なので `evaluated` である。ただし
`predicted_chain_potential` が `None` なら未知として扱い、0 へ暗黙変換しない。

## 決定性と計算量上限

探索は追加 1〜4 ぷよについて、列の組合せと通常色を固定順で列挙する。配置は各列の現在高から
重力に従って積み、どれか 1 個を除いても発火する上位集合は代替候補から除外する。次の count
budget を入力に含める。

列の組合せは左右反転した組を 1 orbit として扱う。pattern / resolution budget は orbit 全体を
処理できる場合だけ消費するため、打ち切り位置が左側または右側の候補だけを優遇しない。

- `max_added_puyos`
- `max_pattern_nodes`
- `max_resolution_nodes`
- `max_alternatives`
- `max_continuation_actions`
- `max_recovery_puyos`

継続手は中心側から左右反転で同値な列ペアを 1 action として数える。したがって小さい
`max_continuation_actions` で打ち切っても、左右どちらか一方だけを柔軟性評価へ含めない。

cache key は schema、budget、盤面 fingerprint から成る。cache の有効・無効にかかわらず、同じ
盤面は decision 内で 1 evaluation と数えるため、cache 設定が probe budget や探索結果を変えない。
budget 到達時は wall clock ではなく count で停止し、再現可能な `truncation_reason` を残す。
planner の beam probe 上限は search control 適用後の
`probe_width * depth * scenarios`、response probe 上限は `probe_width` とし、発火保持を行う場合だけ
root 1 回を加える。response は発火保持以外の基礎価値で上位候補を先に決め、その候補だけを probe
するため、合法手数が `candidate_count` を超えても BuildPotential 評価数は増えない。

## 発火点の同等性と回復可能性

発火比較は次の順で判定する。

1. 色、anchor、仮想配置が同一で、連鎖数を維持する `exact`
2. 連鎖数を維持し、必要ぷよ数が悪化しない `equivalent`
3. 連鎖数を維持し、追加ぷよ数の悪化が recovery budget 内の `recoverable`
4. 完走済み探索で上記がない `lost`
5. 片方が未評価、または打ち切りのため喪失を証明できない `unknown`

root に発火余地がない場合は `not_applicable` とし、構築手を不必要に罰しない。
`trigger_preserved` は実際に保持を評価できた場合だけ true とする。policy が `ignore` のときは
比較不要という意味をこの field に混ぜず false とし、recoverability は `unknown` のままにする。

## scoring の分離

v2 の候補値は `value_breakdown` に次の独立項として保存し、合計を `total` とする。

- `actual_chain`
- `actual_score`
- `chain_shape`
- `future_potential`
- `danger`
- `trigger_preservation`
- `premature_fire`

`chain_shape_weight` は対称な盤面形状項だけに、`future_potential_weight` は将来ポテンシャルだけに
掛ける。各 weight の 0 はその項を正確に無効化する。shape は色連結、到達可能な発火、隣接列の
凹凸、高低差から計算し、左右どちらかの型、GTR などの名前付き定型、固定の組み方を優遇しない。

既存の左右非対称 heuristic を含む評価は `scoring_mode=legacy` の内側に隔離する。legacy は
BuildPotential v1 を既定とし、追加された v2 weight を変えても従来の action と candidate value を
変えない。v2 は構築 tactic から明示的に選択し、既存の `fire_main` の即時発火経路には適用しない。

## v1 からの移行

盤面 snapshot がある場合は v1 の値を推測で補完せず、指定 budget で v2 を再計算する。盤面がない
artifact は、v1 が非ゼロなら `legacy_partial` として旧発火情報だけを保持し、v1 がゼロなら
`unknown` とする。どちらもポテンシャル、柔軟性、危険余裕を捏造しない。

checkpoint / dataset の tensor shape は維持し、metadata に BuildPotential schema と明示的な
migration 履歴を記録する。旧 metadata を暗黙に v2 と見なしてはならない。

旧 checkpoint は専用 CLI で別ファイルへ移行する。出力先は既定で上書きせず、移行後に schema、
state hash、77 次元 feature contract、全 tensor shape を検証する。

```bash
python -m train.migrate_build_potential_v2_checkpoint SOURCE.pt OUTPUT.pt
```

## QA

```bash
python -m pytest -q
python -m eval.v1_7_build_potential_benchmark verify
```

このテストは v1 の exact JSON、1〜4 ぷよ・複数列探索、値域と node 上限、cache on/off の一致、
代替発火と回復判定、v1 migration、weight 0 と value breakdown、legacy action/value 固定値、
StateAnalyzer の JSON/schema を検証する。
