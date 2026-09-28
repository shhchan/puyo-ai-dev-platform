# PUYO-272 選択定型を保持する backend 契約

## 入出力

`agents.selected_template.SelectedTemplate` を `LongHorizonBackendRequest.selected_template`，`NativeDecisionRequest.selected_template`，または `run_compact_long_horizon_search(..., selected_template=...)` へ渡す．省略時は従来の無制約探索であり，wire，root 順位，plan，counters の互換性を保つ．production の catalog/phase/candidate/selector 接続は PUYO-268 が所有する．

```python
from agents.selected_template import SelectedTemplate

selected = SelectedTemplate(
    template_id="persian",
    variant_id="central_step_flat_tail",
    transform="identity",
    binding=(("A", 1), ("B", 2), ("C", 3)),
    required_cells=((0, 0, 1), (1, 0, 1), (2, 0, 1)),
    forbidden_cells=((0, 1, 1),),
)
```

この例は catalog 全体ではなく，横 3 を対象とする小さな合成制約である．座標は 0 始まり・底基準，色は compact ID の red=1/blue=2/green=3/yellow=4/purple=5．binding は単射で，必須セルの色は既に割当済みの実色とする．variant/transform/binding を途中で選び直さない．鏡映の座標変換と catalog の記号展開は呼出側が行う．backend は identity をそのまま保持する．

- `required_cells`：空欄または指定色だけを許す．一度満たしたセルは，後続手の消去・落下後にも同じ座標・同じ色で残す．全必須セルが満たされると完成 witness となる．
- `forbidden_cells`：指定座標の指定色を禁止する．隣接同色を禁じる場合は隣接座標を列挙する．色 0 の明示指定だけを「全色の占有禁止」とする．catalog の `.` は展開せず，列挙されないセルは無制約である．L 字の一般禁止はない．
- 開始盤面が既に制約に反する場合は，評価した root を `violated` とする．不足セルは違反ではない．初期矛盾を途中の消去で修復する探索は行わず，PUYO-268 が新しい binding/phase を決める．

固定された土台を消去する発火もこの制約では違反となる．**完成しただけでは制約を自動解除しない**．完成後に自由構築へ移す場合，PUYO-268 は新しい公開 request で保持対象を縮小した `SelectedTemplate` を渡すか，`None` にする．同じ request の途中で解除して，完成前の枝と併合する動作はない．保持対象の周囲での自由構築や，保持対象を壊さない発火は許される．

## 結果と不確実性

`LongHorizonSearchResult.selected_template` は schema，semantic digest，全 legal root の `roots` を返す．`compatible_ranked_roots` は既存の長期評価順から，制約に整合する代表継続を持つ root を取り出す．`ranked_roots` は従来どおり全 legal root を含むので，制約を消費する呼出側は `compatible_ranked_roots` を使う．全失敗なら空 tuple であり，無制約 root へ暗黙に戻さない．

| root 項目 | 意味 |
| --- | --- |
| `checks` / `rejected` | 遷移後の制約検査数／違反枝数．`checks` 合計は generated nodes に一致する． |
| `root_violation` | current の設置・消去・落下後，または初期盤面が固定制約に反することの証明．後続で候補が尽きたことを root 違反の証明に昇格しない． |
| `status` | `violated`，`known_complete`，`cutoff`，`unknown`．優先順位はこの順で，完成 witness は後から予算が尽きても保持する． |
| `reason` | `resolved_constraint_violation`，`public_prefix_completion`，`request_node_quota`，`sampled_completion_only`，`no_completion_witness`． |
| `known_witness` | current/NEXT/NEXT2 の公開 prefix 内だけで完成した代表 action 列．最短，次いで action 列の辞書順． |
| `sampled_witness` | 未公開のサンプル未来に依存する完成例．公開完成の証明ではなく，status は `unknown` または `cutoff` に留める． |
| `known_scenario_id` / `sampled_scenario_id` | 公開 prefix は全 scenario で共通なので前者は常に `None`．後者は sampled witness を生成した `scenario_sequences` の ID で，witness がなければ `None`．同じ最短 action 列なら最小 ID を選ぶ． |
| `completion_source` | `public` / `sampled` / `None`． |
| `compatible` | 整合する代表継続が beam に保持されたこと．到達性・生存・horizon 全体の実現可能性を保証しない． |

`cutoff` は request 全体の node quota が尽き，公開完成が未証明であることを示す保守的な区分である．beam 幅による枝刈りや候補消失を「完成不能」と呼ばない．代表継続と completion witness は異なる枝になり得る．`known_complete` は witness 末端での完成を意味し，current の採用直後に完成しているとは限らない．phase の完了は新しい公開盤面で確かめる．実行 action として有効なのは current の root だけであり，後続手は次の公開盤面で再確認する．結果は geometric compact state の評価であり，リアルタイム到達性や未観測セルの保証ではない．呼出側は公開セルの既知性と実到達 mask を保持すること．

selected-template 入力は current を含む 1〜3 組に限定し，4 組以上の private future を拒否する．サンプル未来は従来の deterministic scenario generator だけが作る．観測境界で公開入力であることを検証する責任は従来どおり呼出側にある．

## beam，予算，TT/cache

制約検査は各 root と後続 node の実遷移後，structure evaluator，fire 記録，TT，beam pruning より前に行う．違反枝は同じ node quota を消費したうえで除く．selector の後段で追加探索しない．幅 1 の反例では，無制約探索が root 0 だけ残す局面で，制約探索は root 3 と公開完成 `(3, 0)` を残す．後段 filter では得られない継続である．

既存 root survivor quota と scenario accounting を変更しない．`oracle-1` は 6 scenario を 1 thread で順次実行する mode であり，scenario 数を 1 へ減らさない．`scenario-6` の speculative jobs/reruns は既存契約を維持し，採用済み logical counters と telemetry を区別する．

TT は request/scenario/depth 内で作り直す．その範囲では選択定型の digest/binding は一定である．さらに「満たした必須セルを以後失わない」条件により，採用済み node の進捗は現在の完全盤面から一意に導出できる．従って既存の完全盤面・root・scenario・cursor・depth キーは，この制約の進捗も同値にする．制約の自動解除，非単調な進捗，request を跨ぐ TT 再利用を追加する場合は，digest と追加状態を明示キーに含める必要がある．

`SelectedTemplate` は frozen/tuple 化され，canonical binary の SHA-256 を持つ．native の `request_sha256` は追加 section を含む．既存 `SharedSearchCache` は request dataclass 全体の等値比較なので，制約・binding・config の変更は cache miss になる．request ID だけは従来どおり telemetry として無視される．

## schema/ABI と安全境界

optional section を付けるときだけ required TLV `0x8007`（request）と `0x8307`（result）を追加する．section version=1，内側 ABI=1，schema=`puyo.selected_template.v1`．古い native は unknown required section として拒否し，新しい adapter は必須結果の欠落・digest 不一致・不正 witness/accounting を拒否する．無制約の envelope v1，request/result schema digest，base ABI は変更しない．batch v3 と trajectory schema に変更はない．新結果の deterministic digest は制約 identity・証拠を含め，計測時刻を含めない．

`template_check_ns` は制約判定本体の合計 wall time で，タイマー自体の費用を含む．検索結果の semantic digest/counters から分離し，backend timing に保存する．native では採用した scenario の合計であり，捨てた speculative 作業の時間を合算した CPU time ではない．

PUYO-270 の生存経路へ本制約を渡してはならない．`forced_safety` と selected-template の同時指定は拒否する．生存例外は無制約で探索し，既存 `apply_envelope` の優先を維持する．この backend は戦術順位，phase，scheduler receipt を変更しない．

## 検証と限界

`tests/test_selected_template_search.py` は PUYO-271 の元 corpus を読み，横 3 と L 字の破壊的 root，明示禁止色／空欄，色置換・鏡映，完成前後の土台保持，公開／sampled 完成，quota，幅 1 の beam 内保持，cache/TT，Python/native 両 mode の全 root 順位・plan・counters・証拠を検証する．既存 native corpus の順位比較も実施する．

[測定記録](../benchmarks/puyo-272-template-search/README.md) は同一公開入力の backend 単体比較である．実ゲームの品質，GUI 応答，正式 G2 の判定ではない．PUYO-268 の production 接続，PUYO-271 後半の統合比較，PUYO-266 の正式 G2 は別途必要である．
