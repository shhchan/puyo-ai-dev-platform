# PUYO-274 デスクトップ再開とレビュー引継ぎ

デスクトップ側の再開結果は [2026-09-28 実行表](puyo-274-handoff/execution-desktop-20260928.md) と [環境記録](puyo-274-handoff/desktop-environment-20260928.json) を参照する．以下はノート PC からの引継ぎ時点の記録であり，PR/SHA/QA の現在値は新しい実行表を優先する．先読み表示は 2026-10-08 に人間 GUI QA を実施した．最新の両側 frame 機械 gate は通過し，窒息・正式品質・修正後の人間 QA は未解決である．

2026-10-08 の Sprint 14 再開状況は [実行表](puyo-274-handoff/sprint14-execution-20261008.md) に追記した．以下の旧 PR 番号・旧観測値より，新しい実行表と Jira/GitHub の現在値を優先する．

最上段の結合 GUI 機械 QA は [8 run の raw／集計](../benchmarks/puyo-274-sprint14-combined-20261008/README.md)で確認できる．PUYO-266 の新しい公開推定を含むが，seed 127／128 の窒息と正式 G2 FAIL は残り，修正後の人間 GUI QA は結果待ちである．

記録日：2026-09-28．窓口は [PUYO-274](https://shhchan.atlassian.net/browse/PUYO-274)，親は PUYO-228．今回はノート PC 上の資料保存・レビュー整備まで．残る生存品質・先読み表示・性能・正式品質はデスクトップで再開する．本資料の完成だけで PUYO-274/266/269/273 を COMPLETE にしない．

## 1. 保存した成果と最新値

[レビュー manifest](puyo-274-handoff/review-manifest.json) と [今回の実行表](puyo-274-handoff/execution-20260928.md) は親が確認した PR/base/head・状態を記録する．起動時は GitHub/Jira の現在値を優先する．[09-25 実行表](puyo-274-handoff/execution-20260925.md) と [09-27 実行表](puyo-274-handoff/execution-20260927.md) は原本をそのまま保存した履歴で，過去の進行中記述・絶対パス・commit 除外指示を現在の実行命令として使わない．

既存 stack [#167](https://github.com/shhchan/puyo-ai-dev-platform/pull/167) のレビュー順は #162→#161→#163→#164→#165→#166→#168→#169→#170→[#171](https://github.com/shhchan/puyo-ai-dev-platform/pull/171)．資料 PR #171 を最上段へ接続した．manifest は開始時 snapshot，最新値は GitHub/Jira で read-back する．統合先は `integration/puyo-228-v1-8-0`．開始時の統合 SHA は `c0c77d944ff3ed276a4b44a40a779cd72bc0977a`，本資料の起点は #170 の `d3acb77d9542e66209618deb36eaae6d2bcaca9d`．merge/release の許可は含まない．

| 所有先 | 保存結果と残条件 |
| --- | --- |
| PUYO-268/#165 | 公開 prefix 修正後，固定 55/123/124 の最大実連鎖 10/10/11，初回 GTR 3/3．人間は 268 OK．一般品質の正式条件をこの観察だけで補完しない． |
| PUYO-273/#168 | live clock/実 lock の比較一致．最終 GUI frame gate 未達．人間目視の配置は問題なしとの報告．N=3 preview 消失の原因・回帰元は未確定． |
| PUYO-269/#169 | 合成 held 不一致・余分な横発火 0．最終 frame p95/p99(ms)：軽量 19.3/22.3，片側 30.9/70.9，両側 71.6/97.3，human 31.1/64.3．片側 activation_unreachable_fallback 1 件．人間は若干改善したがカクつくと報告． |
| PUYO-266/#170 | 正式 60 run の平均最大連鎖 8.8667，10 連鎖以上 25/30 seed，premature 6，窒息 10，repeat 一致 30/30，decision p95 0.8349 秒．全入力 replay 実 lock 不一致 0．品質 FAIL/G2 BLOCKED．失敗 seed 126/128/132/135/144． |

frame/input の p95 ≤ 25 ms/p99 ≤ 50 ms，実人間の意図した配置，残る G0/G1・脅威 fixture・known-solution gap 等の受入条件は保持する．PUYO-256〜258 の本学習は開始しない．

### CI の再確認

既存 #164/#168/#169 の CI 失敗は，継承された `native/deep_chain_native/src/long_horizon.rs` の `collapsible_if` と `selected_template.rs` の `too_many_arguments` による Clippy failure．その run の後続 Rust/Python tests は未実施で，成功とは扱わない．引継ぎ最上段で親がこの 2 件を別 commit で修正した．remote CI の Rust fmt/Clippy/units と frozen corpus は成功したが，続く Python 境界 step が継承された Ruff I001 4 件で停止した．agents/deep_chain_native.py，deep_chain_native_search.py，deep_chain_search_backend.py，long_horizon_search.py の import block だけを整形し，Ruff 0.16.0 で確認した．runtime の挙動，探索重み，quota は変更しない．古い下段 head の red check は過去 run として残り，最上段 head の remote CI を再検証する必要がある．本資料の作成時点では CI PASS を宣言しない．

## 2. 2026-09-28 の人間観察

- PUYO-264 は OK，PUYO-268 は OK．PUYO-266 の連鎖品質は暫定許容するが，上部まで積み上げても生存用単発消しをせず自滅する場面が残る．下記 daa/random 条件は旧 seed 55/GTR の機械失敗とは別条件．
- PUYO-273 は目視上配置に問題なし．`o` で切り替える N=3 の将来配置 preview が nextgen で見えないとの報告．操作中の組ぷよの落下位置 ghost と区別する．
- PUYO-269 はノート PC で若干改善したがまだカクつく．ノート PC のスペックが原因という見立ては仮説．デスクトップでの改善・gate 合格を先取りしない．

daa/random 元 run の replay，速度，source/config/native SHA は未提供・未確認．新規再現との同一性は保証できない．gtr seed 123 元 GUI raw も未保存，セカンド逆発火の seed は不明．先読み消失の正確な run 条件も不明．human/random と通常/0.25x/低速 `n` の結果を混合しない．daa 初回下 2 段 L 字の細部は対象外．

### 2026-10-08 の依頼者 GUI QA

デスクトップ `puyo-desktop-274` で 1P nextgen／2P human を起動し，`o` による 3 手先読み配置の表示・非表示，現在組の落下位置 ghost との区別を確認した．移動・回転は意図する位置へ置けそうな操作感との報告．これは人間の目視確認であり，frame/input 25/50 ms gate の代用ではない．

別の対局では対戦 seed 127，1P policy seed 58，土台 daa，softmax 温度 1.0，速度 x1.0 を使用．人間側が最初の 2 手で全消しを取り，おじゃま送付後に 1P が窒息した．元 replay／結果 JSON は未保存で，正確な human 入力列は不明．新規の固定入力再現は元対局と区別する．`softmax`／`1.0` は現在の launcher 既定値ではなく，明示選択した条件である．

## 3. デスクトップで取得・起動する

Linux x86_64，CPython 3.12 を起点にする．native build script がこの組合せを要求する．別 OS/CPU では未対応条件を記録して環境方針を確認する．ノート PC の wheel/venv はコピーしない．

```bash
git clone https://github.com/shhchan/puyo-ai-dev-platform.git
cd puyo-ai-dev-platform
git fetch origin
gh pr view 171 --json state,headRefName,headRefOid,baseRefName
gh pr list --state open --json number,title,headRefName,baseRefName,isDraft
```

Jira と stack を再取得し，引継ぎ資料を含む現在の最上段 branch/head を確認してから専用 worktree を作る．資料 PR が未 merge の間は最上段 head を起点にする．全成果が統合済みなら最新統合 SHA を起点にする．以下の `CURRENT_VERIFIED_HEAD` は実際に確認した SHA に置換する．既存 branch/worktree を上書きしない．

```bash
git worktree add -b PUYO-274/desktop-resume ../puyo-desktop-274 CURRENT_VERIFIED_HEAD
cd ../puyo-desktop-274
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bash scripts/build_deep_chain_native.sh
.venv/bin/python main.py
```

build script は `requirements-native.txt`，Rust toolchain，locked release wheel，ABI/schema check を扱う．desktop で source/config/native SHA，host/CPU/RAM，表示環境，速度，予算を保存する．GUI，native build，重い探索，正式 G2 を並走させない．

`main.py` は launcher を開く入口で，policy/seed の引数を直接受け取らない．launcher の対戦・観戦設定で 1P=`nextgen_tactic_manager`，2P=`random`，共通 seed=`59`，有効な土台=`daa`，1P policy seed=`55`，2P policy seed=`59` を設定する．直接 CLI で同条件を指定する入口は次の通り．引数名は `eval/realtime_versus_ui.py` と launcher の引数生成から静的確認済みで，今回 GUI を起動していない．

```bash
.venv/bin/python -m eval.realtime_versus_ui \
  --policy-a nextgen_tactic_manager --policy-b random \
  --seed 59 --nextgen-templates daa --seed-a 55 --seed-b 59
```

共通 seed 59 と policy seed 55/59 を混同しない．元 run の速度は不明なので，選んだ速度を新規条件として記録する．profile/backend/予算も記録し，人間報告と同一だったと断定しない．

`o` の ON/OFF は `eval/realtime_versus_ui.py` の `plan_overlay_enabled` を切り替え，`plan_overlay()` は diagnostics の `plan` を返し，`src/ui/versus_renderer.py` が `steps` を描画する．これは既存経路の静的確認であり，nextgen の N=3 plan 生成や表示の正常性を確認した結果ではない．PUYO-82/188 の既存契約，adopted plan/diagnostics，active pair ghost を照合して原因を調査する．

## 4. 再開の担当境界

1. 親が Jira/PR/stack の現在値，依存，起点 SHA，PR base，共有 API，CPU/GUI 予算を実行表に固定する．短い状態確認は親が担当する．
2. 生存品質は PUYO-266 と既存 Complete の PUYO-270 を読み，公開入力・候補不足/順位・戦術・定型解除・選択理由・receipt・実 lock・合法 mask を分けて反例を保存する．合法で有効な小消しがある回避可能な自滅へ狭く対処する．seed 特例・単発消し一律優先・根拠のない重み変更は避ける．
3. N=3 preview は表示と adopted plan の境界で調査する．原因が 273 の回帰かどうかは未確定．未来の配置保証を表示仕様に追加しない．
4. 269/273 は軽量・片側・両側・1P nextgen/2P human の条件を分け，各 host 内で同じ source/config/native/公開入力/予算を固定する．event→state→draw，frame/input，scheduler accept/finish，tick catch-up，CPU/RSS を保存する．ノート PC と desktop の値を同条件 A/B としない．
5. 生存と 3 定型，固定 55/123/124，失敗 126/128/132/135/144 の最小回帰から始め，正式 G2 は PUYO-266 の規定条件と未達 gate を保持する．既存証跡を再利用し，必要性のない 60 run の再実行を開始しない．

共有 `agents/nextgen_shared_search.py`，selector，template phase，scheduler に触れる子は直列化する．独立性が確認できた担当範囲だけ並列化する．子は指定 worktree の調査・実装・必要な検証・限定 commit/push・指定 base PR・担当 Jira の 1 セッション 1 コメントまで．子の再帰委任は禁止．親は差分・組合せ QA・stack を管理し，reviewer を指定せず，merge/release・共有済み履歴の書換えをしない．

## 5. 移送 inventory と欠損

[artifact inventory](puyo-274-handoff/artifact-inventory.json) に原本 SHA-256，サイズ，tracked 証跡，対象外データを保存した．AGENTS.md は root の承認済み変更と同一，ローカル依頼メモは上部 notice の後に元本文を byte identical で保持，2 実行表は完全コピー．原本は削除しない．

最新 #170 の証跡は Git で取得できる．特に [正式統合 G2](../benchmarks/puyo-266-safe-build/integrated-g2-20260927/)，[269 統合 GUI](../benchmarks/puyo-269-followup/integrated/)，[273 timed placement](../benchmarks/puyo-273-timed-placement/) を参照する．archival 実行表の `/tmp/puyo266-native-g2-20260927/` は [旧正式 G2](../benchmarks/puyo-266-safe-build/asset-quality-20260927/native-g2/) に対応する．その raw 64 本は tracked コピーと一致，269 の正式・統合 raw は tracked gzip 展開内容と一致する．`/tmp` の PR 本文・試行ログを全部移す必要はない．

`/tmp/puyo273/rejected-old-native/` は古い native による無効測定で gate 外．273 timed の初期試行 raw の一部も未保存だが，正式・最終比較は Git 管理済み．元人間 run の未提供・未確認情報と既知の欠損は第 2 節の通りで，代替データを元 run と偽らない．外部 Ama clone は元 URL/revision から取得し，clone 全体や `.git` をこの repo へ含めない．

root の ignored `human_datasets/` は 25 files/685,235,815 B，`runs/` は 518 files/1,630,572,088 B．これは今回と無関係な過去のユーザーデータ・記録であり，cache ではない．更新日時範囲は inventory に記録した．今回の Git 移送へ含めず，原本は保持する．別途移送が必要なら対象を指定する．worktree の collection audit は原本を保持し，移送対象外の履歴として inventory に日時・サイズを記録した．`runs/nextgen-gui/*.nextgen_config.json` は 54 本を metadata と構造で確認し，重複を除いた 8 種類を [launch-configs](puyo-274-handoff/launch-configs/) へ完全コピーした．template catalog のみで，seed/argv/source/速度がないため daa-only 設定があっても人間の報告 run と同定できない．原パス・mtime・SHA-256 は inventory に記録した．資格情報・user config・Codex rollout JSONL も含めない．venv，native binary，target，cache は desktop で再生成する．

## 6. 次セッションへの依頼文

```text
PUYO-274 を窓口に，desktop-handoff 文書と最新 Jira/PR/stack #167 を読んで，
生存品質，N=3 先読み配置 preview，GUI cadence の残作業を実装・検証・PR まで再開してください．
AGENTS.md と codex_autonomous_workflow.md に従う親オーケストレーターとして，
現在の最上段 head または統合済み最新 SHA，担当境界，依存，排他資源を先に固定してください．
重い原因究明はチケット別の高能力モデル/high，明確な実装は標準コードモデル/medium の子へ委任し，
実設定と理由を記録してください．短い状態確認は親が担当し，子の再帰委任は禁止です．
266/269/273 の既存受入責務と270の生存契約を読み，重複実装・seed特例・gate緩和を避けてください．
daa/random seed59，policy seed55/59 の人間失敗は元raw未提供・未確認なので新規再現として保存してください．
N=3 preview とactive pair drop ghostを分け，o切替とadopted plan/diagnosticsを確認してください．
性能はdesktop内の同条件で評価し，ノートPCスペック原因を仮説のまま扱ってください．
品質FAIL/G2 BLOCKEDと未達25/50ms gateを保持し，256〜258の本学習は開始しないでください．
親がPR差分・組合せQA・stackを確認し，一覧と残条件を提示してください．reviewer指定，merge/releaseはしません．
```

## 7. 2026-10-08 Sprint 14 実行更新

2026-10-08 時点の担当・検証・PR 順序は [Sprint 14 実行表](puyo-274-handoff/sprint14-execution-20261008.md) を参照する．最上段は `PUYO-274/sprint14-closeout`／runtime 測定 source `3724eb9af7ad23fd730d0a2ed8e6c6668fc3367f`，draft [PR #182](https://github.com/shhchan/puyo-ai-dev-platform/pull/182) である．PUYO-266 の公開情報に基づく着弾おじゃま回復後，新規固定 human 入力 seed 127 は 60 実 lock まで窒息しなかった．元の人間対局とは同一視しない．追加の適格発火順位修正で固定 GTR126 は実 10 連鎖・小発火 0／40 配置非窒息となった．GUI 機械 QA はこの最上段の [8 run](../benchmarks/puyo-274-sprint14-eligible-fire-20261008/README.md) ですべて事前 gate を通過したが，正式 G2 FAIL と変更後の実人間 GUI QA は残る．PUYO-264／268 は Complete，266／273／269／274 は draft／In Progress のままである．

実人間 GUI QA は次で launcher を開き，1P=`nextgen_tactic_manager`，2P=`human`，速度 x1.0 を選ぶ．`o` の 3 手先読み表示／非表示，現在組の落下位置との区別，下押し＋横移動／回転で意図した位置へ置けるか，カクつき，対戦 seed／1P policy seed を記録する．この確認は最上段 head `3724eb9` 以降に実施したかを区別する．

```bash
cd /home/sion2000114/workspaces/dev/puyo-s14-274
/home/sion2000114/workspaces/dev/puyo-desktop-274/.venv/bin/python main.py
```

## 8. 2026-10-09 中断後の最新状態

最新の runtime 測定 source は `32280fda0a4f980b9bbdf1d07b3cbf09bad7fbee`，最上段 branch は `PUYO-274/sprint14-closeout` である．PUYO-266 の有限代替証明で固定 seed128 は 40 配置まで窒息を回避したが，最大 1 連鎖／premature 1 のため正式 G2 は FAIL のまま．正常 55／123／124／126 は各 40 配置・最大 10 連鎖・premature／窒息 0．新規固定 human127 は 60 実 lock，両者非窒息，おじゃま 30→0 を保存 replay で確認した．元の手動対局とは同一視しない．詳細は [実行表](puyo-274-handoff/sprint14-execution-20261008.md) と [PUYO-266 証跡](../benchmarks/puyo-266-safe-build/sprint14-human-20261008/README.md)を参照する．

最上段の GUI 機械 QA は[初回 7／8 通過](../benchmarks/puyo-274-sprint14-bounded-20261009/README.md)，[同 source 再測 8／8 通過](../benchmarks/puyo-274-sprint14-bounded-repeat-20261009/README.md)．初回 minimal-two frame p95 は 25.59 ms で固定 25 ms gate を超えたため，安定達成とは扱わない．PUYO-264／268 は Complete，266／273／269／274 は draft／In Progress のままである．変更後の実人間 GUI QA は依頼者の結果待ち．上の起動コマンドは同じだが，結果には今回の最上段 head を添える．
