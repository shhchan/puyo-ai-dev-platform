# Sprint 15 単独品質：未達の保存

単独品質は BLOCKED，Jira は In Progress，PR #186 は draft のままとする．正式 120 run は完走しておらず，平均最大実連鎖 ≥ 10 を確認したとは扱わない．cohort／閾値／分類を変更せず，本学習も開始しない．

測定 source は正式 v3 が `b6046e87942c18d6e4e3927b1c3678b8ff894946`，修正後の targeted と counterfactual が `e441b8f426dfe72898dfba973a2d86dead688826`．本ディレクトリを保存する後続 commit は測定 source と区別する．実 native extension SHA-256 は `35735a1a6bce0a45a64a46453cd4302f34099fb626ef4fc226a61c32bb2c52ca`．原本配ぷよは再配布しない．

## 正式 v3 の中断

全 120 identity の宣言に対し final は 14 件．reference 6779 repeat1 は中断 progress／failure を保持した．全 final の検証済み集計と元ファイル SHA は `formal-v3/summary.json`．品質未達 4 final と中断 progress／failure の raw をこの Git 証拠に含め，全 14 final の原物は worktree の永続 `runs/puyo-266-formal-v3/` に残す．v1／v2 も削除しない．未実行 identity と中断を成功扱いしない．

| 対象（両 repeat） | 配置 | 最大実連鎖 | 小発火分類 | 窒息 |
| --- | ---: | ---: | --- | --- |
| nextgen 6779，v3 | 39 | 1 | unjustified 1，necessary_survival 1 | unknown |
| reference 4519，v3 | 42 | 2 | necessary_survival 2 | unknown |

nextgen の tick1527 は quiet root0 の公開 witness `[0,5,7]` がある一方，runtime は先頭 witness のみを検証し root0 を unknown として小発火 root1 を採用した．`legitimate_survival_exception` という runtime 理由名だけを必要性の証明とは扱わない．

修正は request 内の占有幾何 control cache と，元順位に沿う quiet 代替証明．公開境界と 128 node 上限を維持し，末端 prefilter も事前課金する．固定 request は 116/128 node で root0 を採用し，実 lock／replay も一致した．詳細は隣の `quiet-prefix-regression/`．

## 修正後 targeted 4 run

正式再開前に，既に観測した失敗の nextgen 6779 と reference 4519 を両 repeat で検査した．新しい代表集合への選び直しではなく，正式品質の代替にもしない．全 4 final の raw・manifest・集計は `targeted/`．全 worker exit0，source 不変，integrity issue 0，実 lock と全 tick replay 一致，policy ごとの repeat digest 一致．

| 対象（両 repeat） | 配置 | 最大実連鎖 | 小発火分類 | 窒息 |
| --- | ---: | ---: | --- | --- |
| nextgen 6779 | 46 | 10 | 0 | なし |
| reference 4519 | 42 | 2 | necessary_survival 2 | unknown |

nextgen は 40 手 checkpoint でも最大 10 連鎖．reference の digest は v3 と同じで，unknown が残るため正式 v4 は init／実行とも行っていない．

## reference の読取調査と棄却

`reference-known-prefix.json` は全 42 決定の選択代表 path を，公開 current／NEXT／NEXT2 の範囲だけ再計算した結果．最初の不達は 31 手目 tick1560 の `[15,19,13,...]` の NEXT action19．compact 幾何配置は valid／非致死だが，fresh-spawn control では到達できない．32 手目 `[8,11,0,...]` の 3 手目も不達だった．29／30 手目の選択 known3 はすべて到達可能で，未知 4 手目の仮定を事実として否定していない．

同じ tick1560 の次順位 root17／path `[17,8,11,...]` は known3 が到達可能（`reference-candidate-prefix.json`）．この 1 決定だけ root17 へ置き換え，その後を元の reference で継続した counterfactual は，実 lock が x4／DOWN，全 replay hash が一致した．しかし合計 42 配置，tick2124，最大 2 連鎖，小発火 2，死亡という結果は改善しなかった．raw は `reference-root17-counterfactual.json.gz`，実行 script／stdout も保存する．この介入は診断であり，同じ policy の正式評価に数えない．

そのため known3 フィルタを製品に追加していない．未知 future に依存した目標連鎖評価，fatal な構造評価と fire class 優先順位の関係には追加調査の余地があるが，閾値・重み・順位を変更する根拠や一般的な小修正は確認できていない．最終局面に即時の非致死代替がないことも，対局全体の不可避性を証明しないため unknown を維持する．

## 検証と再確認

関連 shared search／survival／response／tactic／attack gate／single gate の 107 tests が成功（既存 skip 2）．最後の prefilter 課金追加後に専用 5 tests が成功．Ruff／diff check も成功．targeted 4 run と counterfactual は実 lock／replay を別途確認した．

repository root で保存ファイルの完全性を確認する：

```bash
python3 - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('docs/benchmarks/puyo-266-single-quality/sprint15-blocked-20261011')
for entry in json.loads((root / 'checksums.json').read_text()):
    data = (root / entry['path']).read_bytes()
    assert len(data) == entry['bytes']
    assert hashlib.sha256(data).hexdigest() == entry['sha256']
print('all archived checksums match')
PY
```

期待は全 checksum 一致．正式再開には残る reference unknown の扱いを変更せず原因を解消する根拠，親の source／排他枠確定，新 manifest で同じ 120 identity の再測定が必要．この保存作業は GUI の人間 QA を代替しない．
