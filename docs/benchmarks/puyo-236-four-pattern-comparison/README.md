# PUYO-236 比較証跡

結果と解釈は[日本語報告書](../../development/puyo-236-four-pattern-comparison.md)、全数値は[comparison.json](comparison.json)。今回の完了は比較評価の完了であり、採用・merge・既定変更ではない。

## 保存物

- `measurement-{none,only240,only242,both}.tar.gz`: 各条件の60 raw runと`experiment_manifest.json`。
- `measurement-processes.tar.gz`: 全240 process receipt、固定順序・4条件manifest、計測log。
- `preparation-history.tar.gz`: 最終`preparation-v5`のsmoke/48固定sample/private/cold-warm/24再現runと検証結果、および失敗・旧準備世代の原本。準備runを本計測へ流用していない。
- `native-wheels.tar.gz`: 4条件の独立release wheel。arm名のdirectoryで同名wheelを区別する。
- `source-arms.bundle`: baseline `73ab4e8` を前提に3実験armの正確なcommitを復元するgit bundle。通常runtimeへ適用しない独立patchも`eval/puyo236_patches/`にある。
- `qa-logs.tar.gz`: 独立build/依存環境・関連Python/Rust・historical SHA整合性・protocol10testsのlog。
- `audit/`: 親の独立監査script・結果（source/build/config/実.so/全raw/digest/比較集計/過去GUI lineage）。script内の原環境pathは監査時のprovenanceとして保持する。
- `artifact-index.json`: 上記24ファイルのSHA256・byte数・archive file数。全8 archive内の693 regular fileは保存時に元の各ファイルとの全bytes一致を検証した。index自身とこのREADMEは自己参照を避けて対象外。

全archiveは相対pathだけを含み、最大の単一archiveは約16MiB。過去資料と旧wheelは変更していない。

## 読み取り再検証

リポジトリrootで実行する。Python依存は計測manifestの`runtime.dependencies`が正本（numpy/pygame/gymnasium/PyYAML/Pillowなど）。下記のoffline再集計にはnative wheelの実行や新しい品質探索は不要。

```bash
python - <<'PY'
import hashlib, json
from pathlib import Path
p = Path('docs/benchmarks/puyo-236-four-pattern-comparison')
index = json.loads((p / 'artifact-index.json').read_text())
for name, expected in index['files'].items():
    data = (p / name).read_bytes()
    assert len(data) == expected['bytes'], name
    assert hashlib.sha256(data).hexdigest() == expected['sha256'], name
print('24 artifact checksums: PASS')
PY

trial_evidence="$PWD/docs/benchmarks/puyo-236-four-pattern-comparison"
trial_extract=$(mktemp -d /tmp/puyo236-verify.XXXXXX)
for trial_archive in "$trial_evidence"/measurement-*.tar.gz "$trial_evidence/preparation-history.tar.gz"; do
  tar -xzf "$trial_archive" -C "$trial_extract" || exit
done
PYTHONPATH=. python eval/puyo236_summarize.py "$trial_extract/measurement" "$trial_extract/preparation-v5"
cmp "$trial_extract/measurement/comparison.json" "$trial_evidence/comparison.json"
python -m unittest tests.test_puyo236_four_pattern_comparison
```

再集計は240 raw・全process SHA・全decision/run digest・120 repeat pair・preflight/cold-warmの整合性を再確認する。保存元rawの改変や既存attempt上書きはしない。独立したraw再計算は`audit/parent_audit.py --help`から使用できる。source/buildの監査scriptは元の隔離worktree配置を前提とするため、offline集計と混同しない。

上記復元を別directoryで実行し、比較JSONの全bytes一致を確認済み。最初の呼出では`PYTHONPATH`指定漏れによりimport前に停止したため、手順を`PYTHONPATH=.`へ訂正した。計測raw・凍結runnerは変更していない。

## 同じ実装を新規に再計測する場合

この操作は重い240runを新しく実行するため、証跡の読み取り検証には不要。採用変更でもない。

1. baseline73のobjectsを持つrepoで`git bundle verify source-arms.bundle`を確認し、bundleをunbundleして、報告書記載の4 commitごとに新規の独立worktreeを作る。既存worktreeは流用・変更しない。
2. control runnerも**`29dc88e807aafba7a1d17196d7384a441d25b318`**の独立worktreeを作る。報告書追加後のcontrol HEADは別commitなので、既存manifestへの`init`/`resume`は意図どおり拒否される。既存outputを再開する場合にも、凍結runner worktreeと元の4 source/build/configを使う。
3. 各armへ独立venvを用意し、保存済み共通依存と対応wheelをインストールする。manifest記載のwheel SHAと、wheel内・installedの実native `.so` SHAの一致を確認する。再buildする場合は新しいprovenanceとして扱い、旧wheelと同一だと見なさない。
4. 新しいoutputへ`puyo236_run_comparison.py init`を実行し、4条件manifest/source/configを再レビューする。`preflight`と旧8seedの単独再現を確認してから、同じhostで`measure`を1プロセスずつ実行する。未完了・不整合attemptは保存して停止し、上書き・無効attempt再利用をしない。
5. 完了後に`puyo236_summarize.py`と独立監査を実行する。GUI人間QAとユーザー採用判断は別に行う。

正確な測定時command・cwdに相当するarm path・thread環境・実行順はmanifestとprocess receiptに記録済み。旧環境の時間・RSSを新しい計測値へ混ぜない。
