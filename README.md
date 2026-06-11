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

Ama を参考にした連鎖構築用ビームサーチも利用できます．未知の将来ツモは代表シナリオで補い，
探索深さ・幅・シナリオ数を変更できます．

```bash
python3 -m eval.chain_search \
  --policies random greedy beam \
  --games 3 \
  --max-steps 40 \
  --beam-depth 10 \
  --beam-width 48 \
  --beam-scenarios 1 \
  --beam-minimum-chain 6

python3 -m eval.arena \
  --policy-a beam \
  --policy-b greedy \
  --games 10 \
  --beam-depth 10 \
  --beam-width 48 \
  --beam-minimum-chain 6
```

設計と評価結果は [docs/development/puyo-beam-search.md](docs/development/puyo-beam-search.md) に記録しています．

対戦 PPO の短時間 smoke run:

```bash
python3 -m train.train_versus --set total_timesteps=512 --set num_envs=2 --set num_steps=64
```

通常学習:

```bash
python3 -m train.train_versus --config train/config/versus.yaml
```

ログは `runs/versus_ppo/metrics.csv` と TensorBoard（利用可能な場合）に出力され，チェックポイントは
`runs/versus_ppo/<run_id>/checkpoints/latest.pt` に保存されます．
各 run directory には `config.yaml`，`metadata.json`，`summary.json` も保存されます．
チェックポイントの実力確認は次のように実行できます．

```bash
python3 -m eval.arena --policy-a checkpoint --checkpoint-a runs/versus_ppo/<run_id>/checkpoints/latest.pt --policy-b random --games 50
```

学習の進行は CSV または TensorBoard で確認できます．

```bash
tail -f runs/versus_ppo/<run_id>/metrics.csv
tensorboard --logdir runs/versus_ppo
```

Phase 2.1 の長時間学習は段階的 config を使います．smoke で artifact 出力を確認してから medium / long へ進めます．

```bash
python3 -m train.train_versus --config train/config/versus_long_smoke.yaml
python3 -m train.train_versus --config train/config/versus_long_medium.yaml
python3 -m train.train_versus --config train/config/versus_long.yaml
```

評価レポートは arena の per-match CSV と summary CSV/Markdown に出力できます．

```bash
python3 -m eval.arena \
  --policy-a checkpoint \
  --checkpoint-a runs/versus_long/<run_id>/checkpoints/best.pt \
  --policy-b greedy \
  --games 50 \
  --csv runs/versus_long/<run_id>/arena_greedy_matches.csv \
  --summary-csv runs/versus_long/<run_id>/arena_greedy_summary.csv \
  --markdown runs/versus_long/<run_id>/arena_greedy.md
```

1局のプレイ内容をテキストで観戦する場合:

```bash
python3 -m eval.spectate --policy-a checkpoint --checkpoint-a runs/versus_ppo/<run_id>/checkpoints/latest.pt --policy-b random --max-steps 30 --delay 0.2
```

盤面は左右に `player_0` / `player_1` を表示し，`.` は空，`R/B/G/Y/P` は色ぷよ，`O` はおじゃまぷよです．

## グラフィカル対戦・観戦 UI

`VersusPuyoEnv` の確定盤面を直接描画する Pygame UI で，AI 対 AI の観戦と人間対 AI の対戦ができます．
左右の盤面，操作中の組ぷよ，NEXT/NEXT2，スコア，最大連鎖数，予告おじゃま，得点繰越，方策名，勝敗を表示します．
操作中の組ぷよはフィールド上部の予告おじゃま表示より上に、現在の列位置と回転状態を反映して描画されます．
スコアは各フィールド下部，予告おじゃまは各フィールド上部に表示されます．予告おじゃまは
子ぷよ（1），大ぷよ（6），岩ぷよ（30），星ぷよ（180），月ぷよ（360），王冠ぷよ（720），彗星ぷよ（1440）へ分解して表示します．
落下・消去・連鎖・おじゃまの演出情報はヘッドレス環境の1手結果から生成されるため，表示用にゲームを再計算しません．

checkpoint AI 対 greedy の観戦:

```bash
python3 -m eval.versus_ui \
  --policy-a checkpoint \
  --checkpoint-a runs/versus_ppo/<run_id>/checkpoints/latest.pt \
  --policy-b greedy \
  --seed 123
```

人間対 checkpoint AI:

```bash
python3 -m eval.versus_ui \
  --policy-a human \
  --policy-b checkpoint \
  --checkpoint-b runs/versus_ppo/<run_id>/checkpoints/latest.pt \
  --seed 123
```

`--policy-a` / `--policy-b` には `checkpoint`，`beam`，`greedy`，`random`，`human` を指定できます．
checkpoint を選んだ側には対応する `--checkpoint-a` / `--checkpoint-b` が必要です．
方策の乱数seedは `--seed-a` / `--seed-b` で個別指定できます．未指定時は対戦環境の
`--seed` を基準にプレイヤーごとの既定値を使用します．beam方策は
`--beam-depth-a` / `--beam-depth-b`，`--beam-width-a` / `--beam-width-b`，
`--beam-scenarios-a` / `--beam-scenarios-b`，`--beam-minimum-chain-a` /
`--beam-minimum-chain-b` で探索設定を分離できます．個別指定がない項目は従来の共通オプションを使用します．
checkpoint方策では `--device-a` / `--device-b` と `--deterministic-a` / `--stochastic-a`
（B側も同様）を個別指定できます．

```bash
python3 -m eval.versus_ui \
  --policy-a beam --policy-b beam \
  --seed-a 101 --seed-b 202 \
  --beam-depth-a 8 --beam-depth-b 10 \
  --beam-width-a 32 --beam-width-b 48
```

| 入力 | 動作 |
|---|---|
| `F1` | キーバインド設定を開く |
| `P` | 一時停止 / 再開 |
| `N` | 1手ステップ |
| `[` / `]`，`-` / `+` | 再生速度変更（0.25x～4x） |
| `R` | 同じ seed でリセット |
| `Esc` / `X` | 終了 |
| `A` / `D` または `←` / `→` | 人間プレイヤーの設置列変更 |
| `Q` / `E` または `↑` / `↓` | 人間プレイヤーの回転変更 |
| `S` / `Enter` / `Space` | 人間プレイヤーの1手を確定 |

キーバインド設定では `↑` / `↓` で操作を選択し，`Enter` を押してから新しいキーを入力します．
`Backspace` で既定値へ戻し，`Esc` で設定画面を閉じます．変更は即時保存され，次回起動時にも引き継がれます．
設定ファイルは通常 `~/.config/puyo_ai_dev_platform/versus_ui_keybindings.json` に保存されます．
保存先を変更する場合は `--keybindings /path/to/keys.json` を指定してください．

起動時の速度と一時停止は `--speed 2`，`--start-paused` のように指定できます．
同じ `--seed` と action 列ではヘッドレス実行と同じスコア・連鎖・勝敗になります．

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
