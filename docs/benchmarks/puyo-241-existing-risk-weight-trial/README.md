# PUYO-241 危険度重み試行の証跡

**No-Go。既定設定は維持。** 危険度減点だけを2倍にした1条件を、固定した基準版と
8seed×2 repeatsずつ交互・直列で比較した。target10達成3/8→6/8、平均最大実連鎖4.375→9.25に対し、
premature4→6（両repeat合計）、既存成功seed138/151の退行、trajectory平均時間+13.49%を確認した。

- [詳細報告](../../development/puyo-241-existing-risk-weight-trial.md)
- [比較結果](comparison.json)
- [全raw圧縮archive](raw-evidence.tar.gz)（約6.2MiB）

archiveにはbaseline/danger2の32run、2manifest、固定盤面6ファイル、固定request入力、
事前診断319盤面/6,315即時合法子盤面、比較結果、test log、交互実行logを含む。
過去の全root診断は変更していない。事前診断と固定request入力にその元ファイル名/SHA256を残した。
各runのnative source/buildは同一の `73ab4e8ce066555042f1a20e1b3b59be3a2a8968`、
runnerは `1eadf7bf9e00f1ccade13acfe730b29724888860`。実効config全項目とsemantic checksumは
各manifest、全decisionのrequest receipt/backend diagnosticsにある。

| ファイル | SHA256 |
| --- | --- |
| raw-evidence.tar.gz | `9ba6e1c99d8538b8f8dfab4c62cd785aecae3d15462772dfedfca87c7d6dc082` |
| comparison.json | `4351111ab02960e537f7d1df8c75f7e4771b2513c12d4679028f319801474e37` |

archiveを新規ディレクトリへ展開し、`eval/puyo241_summarize.py <展開先>`を実行すると、
manifest/identity・設定伝播・全root/action対応・完全評価・scenario accounting・repeat digest・
固定盤面/private境界を再検証して同じ`comparison.json`を生成できる。
再集計は追加のnative探索やwheel差替えを行わない。個々のrawとmanifestのSHA256は
`comparison.json.source_sha256`で照合できる。

関連37 testsと最終試行固有3 testsは成功。全1,278 decisionでparity/fallback/accounting異常0、
全16repeat組が一致。GUI再QAとPUYO-236最終60runは未実施。本試行をbaseline合格と扱わない。
