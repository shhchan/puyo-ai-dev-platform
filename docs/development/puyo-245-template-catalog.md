# 次世代 template catalog matcher

`agents/template_catalog.py` は `puyo.template_catalog.v1` の UTF-8 YAML を厳密に読み込む．`load_template_catalog(path)` または `TemplateCatalog.from_dict(payload)` が未知 key，重複 ID，未定義 symbol，矛盾した色関係，盤外・重複 cell，不正な温度・手数・transform を拒否する．`semantic_digest` は補完した `commit_turns` と正規化した色同値類を含む JSON の SHA-256 である．[人工 fixture](../../tests/fixtures/nextgen/template_catalog.yaml) は記法の検証専用であり，実形状の根拠ではない．

`match_templates(catalog, visible_board, known_pieces, node_budget=..., binding_budget=..., reachable_mask=...)` は公開 wire 色の上端からの行列を受け取る．通常色は engine の `NORMAL_PUYO_COLORS` と `PUBLIC_CELL_TO_COLOR` で変換し，`Enum.value` を wire 番号として扱わない．`known_pieces` は current/NEXT/NEXT2 の最大 3 組に限る．`reachable_mask` は root action の到達可能性であり，公開上部が未知のときの 1 手 witness には必須である．任意コードによる述語や未来の未公開組は受け取らない．

戻り値 `MatchResult.candidates` は各 variant・transform・評価した色割当の `TemplateCandidate` を含む．`progress` と `complete` は現在の安定盤面での値である．`fit_status` は `fit`，`no_fit`，`unknown` のいずれかで，`witness_actions` は再生可能な action index 列，`witness_candidate_id` はその安定 digest である．`known_prefix_length`，`coverage_nodes`，`cutoff`，`score_source` を併記する．`MatchResult` に全体の `coverage_nodes`，`static_bindings`，`static_cutoff`，`binding_trials`，`static_trials` がある．`binding_budget` と `static_binding_cap`（既定 4096）は成立した割当の件数ではなく，棄却された色も含む class/color 制約検査回数を上限にする．各検査予算の入力上限は 100000，engine node 予算の上限も 100000 とする．制約検査中に上限へ達した候補は静的 best-so-far を保持し，fit を `unknown` とする．読込時にも 100000 回の検査上限で実現可能な色割当を確認し，矛盾または複雑さによる未確定を区別して設定エラーとする．

公開盤面の hidden/ghost 行が `None` の場合，matcher は未知セルを既知の空きとはみなさない．可視行が重力に対して安定し，到達可能な root が既知の可視行へ着地し，連鎖消去が起きない場合だけ 1 手の fit を証明する．上部未知セルが消去や継続の結果に影響する場合は `unknown` とする．全 14 行が既知の人工 fixture では最大 3 組の合法 prefix を engine で探索し，各手の安定盤面で既存の充足条件を保ち，symbol の充足数が厳密に増える場合だけ fit とする．完成形は全条件を保つ合法継続で fit となる．

`TemplateSelector(catalog, seed)` は専用 RNG を所有する．`select_initial(result)` は fit の低さや探索不足にかかわらず有効 template を 1 件選ぶ．`reselect(result)` は `fit` の候補だけを対象にし，なければ `candidate=None` と `free_build_no_proven_fit` を返す．`argmax` は score 降順，同点を template/variant/transform/binding 辞書順にする．`softmax` は専用 RNG の 1 回の draw ごとに位置と選択確率を返す．caller は catalog/seed を対局開始時に固定する．

既存の `agents/chain_styles.py` には任意注入の `PublicTemplateCatalogProvider` と `PublicTemplateStyleInput` を追加した．入力は `PublicSnapshot` と digest，catalog digest，root mask，予算を明示する．既存 `ChainStyleEvaluator` の provider injection を使い，legacy simulator から hidden state を読み出す経路は設けない．この provider は既定 registry に登録されず，従来の chain style の動作は変わらない．
