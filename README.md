# ぷよぷよ AI 開発基盤作成プロジェクト

ぷよぷよ AI 開発のためのローカル実験基盤です．  
現時点では，**1Pゲームコア（操作・落下・連鎖・得点・描画・デバッグ表示）まで実装済み**で，  
**対戦システム（おじゃま送受信・相殺など）は未実装**です．

## セットアップ

```bash
pip install -r requirements.txt
```

## 実行方法

通常モード:

```bash
python3 main.py
```

デバッグモード（13/14段表示 + 接地カウンタHUD）:

```bash
python3 main.py --debug
```

短縮オプション:

```bash
python3 main.py -d
```

## 操作方法

| 入力 | 動作 |
|---|---|
| `A` | 左移動（DAS対応） |
| `D` | 右移動（DAS対応） |
| `S` | ソフトドロップ（長押し連続） |
| `←` | 左回転 |
| `→` | 右回転 |
| `Space` / `Enter` | ゲーム開始（ready -> countdown） |
| `Q` / `Esc` | 終了 |

補足:

- 左右同時押しは相殺されます．
- `右(or左)+下` 同時押しでは，横移動可能な間は横意図が優先されます．

## デバッグモードで増える表示

- オフスクリーン段（13/14段）の可視化
- `OFFSCREEN (13-14)` ラベル
- 接地デバッグHUD:
  - `Ground F`: 接地フレーム累計
  - `Ground C`: 接地回数

## テスト実行

```bash
python3 -m unittest discover -s tests -q
```

## ドキュメント

- ゲームシステム詳細仕様（現行実装準拠）:  
  [docs/puyo_base_game/game_system_spec.md](docs/puyo_base_game/game_system_spec.md)

## ディレクトリ概要

- `main.py`: ゲームループ/CLI
- `src/core/`: ゲームロジック（状態遷移・盤面・定数）
- `src/input_handler.py`: 入力処理（DAS/ホールド）
- `src/ui/renderer.py`: 描画
- `tests/`: 単体テスト
- `docs/puyo_base_game/`: 仕様書
