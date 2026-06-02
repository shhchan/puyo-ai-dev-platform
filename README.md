# ぷよぷよ AI 開発基盤作成プロジェクト

ぷよぷよ AI 開発のためのローカル実験基盤です．  
現時点では，**1Pゲームコア（操作・落下・連鎖・得点・描画・デバッグ表示）**に加えて，
**ヘッドレス対戦RL環境・自己対戦PPO・arena評価**まで実装済みです．

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

## ヘッドレスシミュレータ

強化学習向けに，描画・入力・アニメーション待ちなしで1手を同期実行できます．

```python
from src.core.constants import Direction
from src.core.headless import HeadlessPuyoSimulator

sim = HeadlessPuyoSimulator(seed=123)
result = sim.step((2, Direction.UP))
print(result.score_delta, result.chain_count, result.game_over)
```

Phase 0 の実装メモ:  
[docs/development/puyo-phase0-core-audit.md](docs/development/puyo-phase0-core-audit.md)

## Phase 1: 1人用RL環境 + フラットPPO

Gymnasium 準拠の1人用環境は `puyo_env.single_env.SinglePuyoEnv` です．
1アクションは「軸ぷよ列 × 回転」の設置位置で，動的な `action_mask` により不正設置を除外します．

ランダム方策の最小確認:

```python
import random
from puyo_env.actions import choose_random_legal_action
from puyo_env.single_env import SinglePuyoEnv

env = SinglePuyoEnv(seed=123)
obs, info = env.reset(seed=123)
terminated = truncated = False
while not (terminated or truncated):
    action = choose_random_legal_action(info["action_mask"].tolist(), random.Random(0))
    obs, reward, terminated, truncated, info = env.step(action)
```

フラット PPO 学習:

```bash
python3 -m train.train_flat --config train/config/flat.yaml
```

短時間の smoke run:

```bash
python3 -m train.train_flat --set total_timesteps=512 --set num_envs=2 --set num_steps=64
```

ログは `runs/flat_ppo/metrics.csv` と TensorBoard（利用可能な場合）に出力され，チェックポイントは
`runs/flat_ppo/puyo_flat_ppo.pt` に保存されます．

## Phase 2: 対戦RL環境 + 自己対戦

PettingZoo ParallelEnv 互換の対戦環境は `puyo_env.versus_env.VersusPuyoEnv` です．
1 joint step で両プレイヤーが1手ずつ設置し，同一 seed のツモ，action mask，相手盤面観測，予約おじゃま，相殺，おじゃま落下を UI なしで処理します．

対戦環境の最小確認:

```python
from puyo_env.versus_env import VersusPuyoEnv
from selfplay.policies import FirstLegalPolicy

env = VersusPuyoEnv(seed=123, max_steps=10)
obs, infos = env.reset(seed=123)
policy = FirstLegalPolicy()
while env.agents:
    actions = {agent: policy.select_action(obs[agent], infos[agent]) for agent in env.agents}
    obs, rewards, terminations, truncations, infos = env.step(actions)
```

arena で方策の挙動確認:

```bash
python3 -m eval.arena --policy-a greedy --policy-b random --games 20 --max-steps 200
```

対戦 PPO の短時間 smoke run:

```bash
python3 -m train.train_versus --set total_timesteps=512 --set num_envs=2 --set num_steps=64
```

通常学習:

```bash
python3 -m train.train_versus --config train/config/versus.yaml
```

ログは `runs/versus_ppo/metrics.csv` と TensorBoard（利用可能な場合）に出力され，チェックポイントは
`runs/versus_ppo/puyo_versus_ppo.pt` に保存されます．
チェックポイントの実力確認は次のように実行できます．

```bash
python3 -m eval.arena --policy-a checkpoint --checkpoint-a runs/versus_ppo/puyo_versus_ppo.pt --policy-b random --games 50
```

学習の進行は CSV または TensorBoard で確認できます．

```bash
tail -f runs/versus_ppo/metrics.csv
tensorboard --logdir runs/versus_ppo
```

1局のプレイ内容をテキストで観戦する場合:

```bash
python3 -m eval.spectate --policy-a checkpoint --checkpoint-a runs/versus_ppo/puyo_versus_ppo.pt --policy-b random --max-steps 30 --delay 0.2
```

盤面は左右に `player_0` / `player_1` を表示し，`.` は空，`R/B/G/Y/P` は色ぷよ，`O` はおじゃまぷよです．

## 開発ワークフロー（VSCode x Codex x Jira）

- セットアップ手順: [docs/development/vscode_codex_jira_setup.md](docs/development/vscode_codex_jira_setup.md)
- Codex運用ルール: [docs/development/codex_jira_operating_rules.md](docs/development/codex_jira_operating_rules.md)
- VSCode MCP用サーバー定義: `mcp.json`
- VSCode推奨拡張: `.vscode/extensions.json`

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
