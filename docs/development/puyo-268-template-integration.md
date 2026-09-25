# PUYO-268 定型保持の production 接続

## 固定するもの

phase は選択時の template / variant / transform / binding を保持する．matcher の `preferred_key` は node/binding quota が 0 でも静的に評価し，他の binding へ差し替えない．未知セルは一致・空の証明に使わない．固定 binding の明白な矛盾は `fixed_binding_conflict`，未探索は `unknown`/cutoff とする．矛盾時は phase を `no_compatible_candidate` で閉じる．再選択は従来どおり公開された発火・応答の解決後に行う．

`compile_selected_template` は catalog を PUYO-272 の `SelectedTemplate` へ変換する．`required_cells` は記号セル，`forbidden_cells` は明示禁止色と明示 empty のみである．`.` と `*` は無制約のまま．GTR の `A=C` のような同色 label は `A=C` にまとめて backend の単射 binding へ渡す．phase の元 binding と compiled constraint は `search.selected_template` に両方保存する．色置換・鏡映・guard は semantic digest/cache identity に含まれる．

backend v1 では「任意色で occupied」を表せないため，その条件を持つ独自 catalog の compile は明示的に拒否する．production の 3 定型は該当しない．無条件に支持セルを捨てたり，任意の色へ固定したりしない．

## 形と色別 guard

座標は左から x=0，底から y=0．以下は上の段から表示している．`!A` はその座標の A だけを禁止し，他色・空を許す．記号セルと異なる色の配置は既存 required 条件で拒否するので，同じ禁止を重複して列挙しない．

```text
GTR                 だぁ積み               ペルシャ
 y3 !A . .           y2 !A !A !B           y2  .  . B B . .
 y2  A B .           y1  A  A  B           y1 !A  B C B !C !C
 y1  A A B           y0  A  B  B           y0  A  A A C  C  C
 y0  B B C
```

| 定型 | 色条件 | 明示 guard と理由 |
| --- | --- | --- |
| GTR | A≠B，B≠C，A=C は可能 | (0,3) の A は左の A 3 連結に接し，完成時に 4 消しを作る．B の離れたセルを同色で結ぶ可能性は一律禁止しない． |
| だぁ積み | A≠B | (0,2)/(1,2) の A と (2,2) の B は，それぞれの L 字 3 連結に 4 個目を追加する．L 字自体はこの定型の必須形であり，禁止していない． |
| ペルシャ | A/B/C は相異なる | (0,1) の A は報告された L 字を検出する．(4,1)/(5,1) の C は右の横 3 への同色接続を防ぐ．他色の支持や尾を許す． |

guard は宣言された範囲の局所条件であり，全盤面の将来消去を静的に証明するものではない．範囲外からの接続，落下，消去による破壊は backend の各実遷移後の必須セル保持で拒否する．mirror は pattern 幅で required/guard を同じように反転する．

PUYO-271 の凍結 fixture/before は変更していない．`replay_fixtures` は guard なしの旧評価を `before_static_conflicts` へ，現行評価を `static_conflicts` へ保存する．横 2 と報告 L 字は旧評価ではともに 0，新評価では 0/1 になる．元 fixture の current(A,B) を x=2 に縦置きする実消去結果も維持する．全ペルシャ binding ではこの上側 B は C の必須位置にも反するため，正常な補充候補の統合テストには current(A,C) を用いる．

## 候補順位と完成境界

matcher は最初の進捗 root で止めず，予算内で同じ深さの候補を比較する．matcher の `_tail_score` は診断用の中間配置 witness に残るが，production の `build_template` 順位には使わない．

shared backend へ phase が有効な間だけ compiled constraint を渡す．`build_template` は複数整合 root を候補とし，現在の 1 手で公開完成する root，公開 prefix で完成 witness を持つ root，それ以外の整合 root の順に並べる．各区分内では matcher が同じ公開 root 層で評価した必須セルの増加数を比較し，同じ進捗なら backend の将来連鎖順位を使う．進捗 0 の支持・尾も候補から除かない．matcher quota の範囲外の root には進捗を捏造せず，0 と同順位に置く．全 root の進捗は `root_progress` に保存する．sampled 完成は最後の区分に留め，公開完成の証明へ昇格させない．unknown/cutoff は未完成不能の証明ではなく，中間候補として利用できる．candidate の実行 plan は current root のみであり，後続 witness は実行 queue にしない．

例外は **`root_violation=False` かつ `known_witness==(root,)`** の現在完成 root だけである．完成後も制約を保持したまま探索した未来で代表継続が消えて `compatible=False` でも，現在の完成手を phase 終端候補として使う．`completion_boundary_roots` に列挙し，将来継続は unknown と明記する．sampled 完成，2 手目以降の完成，root 違反にはこの例外を使わない．backend 内で制約を途中解除しない．

候補や未来 witness が存在しただけでは phase を閉じない．scheduler が実際に採用し，更新された公開盤面で全セルが完成していることを次 request の reconcile で確認してから `completed` にする．完成後の保持範囲は **全解除 (`selected_template=None`)** とし，自由構築・発火へ移る．未完成の場合も 14 番目の実採用は許し，15 番目は定型戦術を無効にして全解除する．timeout/stale/fallback は手数を消費しない．

未観測セルを含む backend 盤面は公開推定である．raw backend の known witness はその推定盤面に条件づけられた公開 prefix 証拠であり，`public_board_complete=False` を併記し，戦術の `known_witness` フラグは false に保つ．

## 生存・採用・証跡

PUYO-270 の probe/response quota と `apply_envelope` を維持する．到達 mask と生存区分は定型の完成区分より優先する．定型制約を生存 probe に渡さず，必要な単発は全 template root が違反でも採用できる．通常の発火・応答戦術は既存 phase 終了条件を維持する．provider/共有 quota 不足では `build_template` を available にしない．無制約 `build_main` を定型整合の証拠として表示しない．

`search.selected_template` に元 key，compiled required/forbidden，digest，全 root の known/sample witness・status・reason を保存する．scheduler replay payload と ledger metadata が同じ trace を保持する．receipt は requested/executed/outcome に加えて `template_adopted`/`template_not_adopted_<outcome>` を記録し，270 の survival reason を保持する．trajectory の既存 DecisionRecord schema は変更せず，拡張探索 trace は replay/ledger metadata に保存する．

[検証と測定](../benchmarks/puyo-268-template-integration/README.md) を参照．統合 3 seed の品質比較は PUYO-271 後半，人間の GUI QA は未実施，正式 G2 は PUYO-266 の範囲である．
