# ぷよぷよ・連鎖探索・戦術強化学習の数理定式化

[日本語 PDF を読む](nextgen-model.pdf)．ソースの入口は [main.tex](main.tex)．数式番号と相互参照は LaTeX が管理する．

対象は [PR #188](https://github.com/shhchan/puyo-ai-dev-platform/pull/188) の固定 commit `f8394d77578ecb66e15802fa502d8e1668223006` と，同版の [PUYO-234 設計書](https://github.com/shhchan/puyo-ai-dev-platform/blob/f8394d77578ecb66e15802fa502d8e1668223006/docs/development/puyo-234-next-generation-model-design.md)．文書作成は [PUYO-282](https://shhchan.atlassian.net/browse/PUYO-282) で追跡する．

## 内容

- 4 色・6 列・14 行の盤面，重力，合法配置，同時消去，連鎖，得点，全消し．
- 実時間対戦，相殺，攻撃到着と落下の区別，勝敗，公開観測と非公開未来．
- シナリオ補完，盤面評価，ビームサーチ，根ごとの候補保持，発火分類と順位補正．
- テンプレートと 6 戦術，固定候補順位，構造 mask，現行ルール，実行結果．
- 将来の戦術 RL：186 次元入力，mask 付き方策，部分観測・可変時間のモデル，報酬，教師初期化，PPO/GAE，自己対戦．
- 実装対応，主要記号索引，検算例，出典．

本文は記号の型・役割を導入してから式を使う．厳密な低レベル入力分岐や tie-break の対応には固定 commit の有限手続きによる定義も併用し，ゲーム規則・抽象化・将来提案を明記した．数式の正しさや定義順の全てを自動証明する文書ではない．

## 日本語 PDF の再ビルド

Python 3.10 以上と Tectonic 0.17.0 を使う．OS の TeX 環境，Docker，管理者権限は不要．初回はインターネット接続が必要で，TeX パッケージと原ノ味フォントを Tectonic が取得する．Python の追加依存はない．

Linux x86_64 では，リポジトリルートから次を実行する．公式リリースの musl バイナリを SHA-256 で検査し，この文書用の `.tools/` に配置する．

```bash
python3 docs/mathematical-model/build.py --bootstrap
```

以後は以下でよい．

```bash
python3 docs/mathematical-model/build.py
```

既存の Tectonic を使う場合は `--tectonic /path/to/tectonic` を指定する．macOS/Windows などは [Tectonic の公式導入案内](https://tectonic-typesetting.github.io/en-US/install.html)に従って 0.17.0 を用意する．キャッシュ済み資源だけでビルドする場合は `--offline` を付ける．このオプションと `--bootstrap` は併用しない．

ソース照合には基準 commit の Git object が必要である．浅い clone 等で不足する場合は，まず次を実行する．PR の head が後日更新されても，検証対象 SHA は自動更新しない．

```bash
git fetch origin f8394d77578ecb66e15802fa502d8e1668223006
```

ビルド成功時のみ `nextgen-model.pdf` を更新する．ログ，中間ファイル，文書ソースと PDF のハッシュを持つ `build-manifest.json` は `build/` に置く．`build/` と `.tools/` は Git 対象外，閲覧用 PDF はレビューできるよう追跡する．TeX 資源は bundle v33，日時は固定値，deterministic mode を用いる．同一環境で再ビルドの PDF ハッシュ一致を確認した．全 OS 間のビット一致までは保証しない．

## 検証

```bash
python3 docs/mathematical-model/verify.py
```

参照ファイルの SHA-256 を [source-map.json](source-map.json) と照合し，label の重複・参照先不足・手入力の式番号・検算例を確認する．コンパイル後は未解決参照，未収録文字，はみ出しを検査する．通常の LaTeX コンパイルによる `label/eqref/ref` の解決も実施する．これらは数学の意味的検証の代用にはならない．

本文を更新したら PDF を再ビルドし，生成された両方を同じ変更でレビューする．基準実装を変える場合は SHA，本文の実装対応，source-map を一緒に更新する．ゲーム実装と学習器のコードはこの文書では変更していない．
