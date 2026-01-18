# ぷよぷよ基本ゲーム 実装計画

## ゴール
Python と Pygame を使用して、基本的なぷよぷよの動作（落下、操作、消去、連鎖）を実装する。
これを将来的なAI開発の基盤とする。

## ユーザーレビュー事項
- **ライブラリ**: `pygame` を使用します。
- **入力**: キーコンフィグ可能な設計にします。デフォルトは矢印キー等ですが、`Action` Enumを定義し、キーマッピングを変更可能にします。
- **フィールド**: 可視領域は幅6x高さ12ですが、内部的には高さ14(インデックス0-13)を持ちます。
  - 13段目(インデックス12): 隠し列。連鎖には関与するが、敗北判定には特定の条件が必要。
  - 14段目(インデックス13): 画面外。ここに置かれたぷよは落下しません。
- **色**: 基本5色（赤・青・緑・黄・紫）＋おじゃま・壁・空気。使用する色数（3～5色）を設定可能にします。

## 変更内容

### 新規作成

#### [NEW] [requirements.txt](file:///home/sion2000114/workspaces/dev/puyo_ai_dev_platform/requirements.txt)
- `pygame`

#### [NEW] [src/core/constants.py](file:///home/sion2000114/workspaces/dev/puyo_ai_dev_platform/src/core/constants.py)
- 定数: 
  - `GRID_WIDTH=6`
  - `GRID_HEIGHT=14` (可視12 + 隠し1 + ゴースト1)
  - `VISIBLE_HEIGHT=12`
  - `PUYO_TYPES` (Color, Ojama, Wall, Empty)

#### [NEW] [src/core/puyo.py](file:///home/sion2000114/workspaces/dev/puyo_ai_dev_platform/src/core/puyo.py)
- `Puyo` クラス
- `PuyoColor` Enum (RED, BLUE, GREEN, YELLOW, PURPLE, OJAMA, WALL, EMPTY)

#### [NEW] [src/core/field.py](file:///home/sion2000114/workspaces/dev/puyo_ai_dev_platform/src/core/field.py)
- `Field` クラス
  - `place_puyo()`: 13段目（インデックス12）までは通常配置。14段目（インデックス13）は特殊処理。
  - `check_vanish()`: 消去判定。
  - `drop_puyo()`: 重力処理。14段目のぷよは対象外。

#### [NEW] [src/core/game.py](file:///home/sion2000114/workspaces/dev/puyo_ai_dev_platform/src/core/game.py)
- `GameState` クラス: ゲームループ管理
  - Cycle:
    1. ツモ落下操作 (Input Handling)
    2. 接地判定 (Ground Check)
    3. 消去判定 & 連鎖処理 (Vanish & Chain)
    4. おじゃまぷよ落下 (Ojama Drop)
    5. 敗北判定 (Game Over Check)
    6. 次のツモ生成 (Next Puyo Generation)

#### [NEW] [src/input_handler.py](file:///home/sion2000114/workspaces/dev/puyo_ai_dev_platform/src/input_handler.py)
- キー入力とゲームアクション(`LEFT`, `RIGHT`, `DOWN`, `ROTATE_LEFT`, `ROTATE_RIGHT`)のマッピング管理。

#### [NEW] [src/ui/renderer.py](file:///home/sion2000114/workspaces/dev/puyo_ai_dev_platform/src/ui/renderer.py)
- Pygame を使った描画クラス。13段目以降の表示/非表示を扱えるようにする。

#### [NEW] [main.py](file:///home/sion2000114/workspaces/dev/puyo_ai_dev_platform/main.py)
- メインループ

## 検証計画
- アプリケーションを起動し、基本的なぷよぷよの操作（移動、回転、設置、消去、連鎖落下）が期待通り動くか確認する。
