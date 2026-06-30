# PUYO v1.1.0-v1.6.0 release runbook

## 1. Purpose

This runbook instructs Codex how to integrate PUYO-53 through PUYO-58 into
`master`, validate each SemVer boundary, create annotated Git tags, and publish
GitHub Releases.

Read this document together with the repository root `AGENTS.md`. If they
conflict, follow `AGENTS.md` and report the conflict before changing remote
state.

## 2. Scope

The release train contains the following backward-compatible feature releases.

| Version | Jira epic | Scope | Candidate commit |
| --- | --- | --- | --- |
| `v1.1.0` | `PUYO-53` | Deterministic realtime match core | `86733a8450e5c863d6893ae720202337fd6bd03a` |
| `v1.2.0` | `PUYO-54` | Realtime AI, arena, replay, and viewer | `0ee59f026445caababd2abe73d4dbea1ef1836c5` |
| `v1.3.0` | `PUYO-55` | Artifact schema, exact resume, experiments, and lineage | `c2ce216b52d80d306ec040455828458a27b2f8ca` |
| `v1.4.0` | `PUYO-56` | Learnable search control and N-turn plans | `b39050cf763ca5e50de84540df20089928d74cc4` |
| `v1.5.0` | `PUYO-57` and `PUYO-97` | Unified GUI, visualization, and pre-PUYO-58 UI corrections | `2737093c13438ec2f5955ec603a69aba14f66b63` |
| `v1.6.0` | `PUYO-58` | Human data collection, training, promotion, audit, and safe deletion | Determine from the final merge commit on `master` |

The candidate commits are safety assertions, not permission to tag blindly.
Verify them against the remote repository during release execution.

This runbook does not:

- Rewrite existing tags.
- Force-push branches or tags.
- Merge a PR without explicit user approval.
- Transition Jira issues to the Japanese `完了` status.
- Publish multiple stable releases without validating each version separately.

## 3. Required tools and access

- Git and GitHub CLI (`gh`) authenticated for `shhchan/puyo-ai-dev-platform`.
- Atlassian MCP access to `https://shhchan.atlassian.net`.
- Jira cloud ID `46424ed5-7d42-4bff-bc2a-da4c296f8b5b`.
- Permission to create PRs, push tags, publish GitHub Releases, comment on Jira,
  and transition Jira issues.
- A clean worktree and enough disk space for temporary Git worktrees.

Use the repository's configured Python environment. Do not install or upgrade
dependencies during release execution unless the user explicitly approves it.

## 4. Safety invariants

1. Always call `getAccessibleAtlassianResources` before Jira operations.
2. Fetch remote state immediately before evaluating branches, PRs, tags, or
   releases.
3. Use the exact branch names `integration/puyo-58` and
   `integration/puyo-53-58`. Do not substitute `integration/58` or
   `integration/53-58`.
4. Preserve history with GitHub's **Create a merge commit** strategy for both
   integration PRs. Do not squash or rebase these PRs.
5. Never move, recreate, or force-push a tag already present on `origin`.
6. Build and test a release from the tag candidate commit, not from the current
   working branch.
7. Stop on failed tests, failed CI, unresolved review feedback, unexpected
   commits, ancestry failure, or a mismatch between a tag and its expected
   commit.
8. Treat PR merge, tag push, and GitHub Release publication as separate
   approval gates.
9. Create at most one Jira comment per issue in a work session. Update that
   comment rather than adding duplicates when the tool supports it.
10. Keep Jira epics at `Complete`; only a human transitions them to `完了`.

## 5. Execution model

Execute the release in phases. At every approval gate, report the exact remote
operation, target branch or tag, QA result, and known risk, then wait for user
approval. A later Codex session must begin again at Phase 0 and discover which
phases are already complete.

### Phase 0: Reconcile current state

1. Read `AGENTS.md` and this runbook completely.
2. Confirm Atlassian access with `getAccessibleAtlassianResources`.
3. Fetch all branches and tags without pruning user branches.
4. Confirm the worktree is clean. If it is dirty, do not discard or include
   unrelated changes.
5. Query Jira for `PUYO-53` through `PUYO-58`, `PUYO-85` through `PUYO-89`,
   `PUYO-97`, and `PUYO-102`. Record their current statuses and dependencies.
6. Query open GitHub PRs and existing GitHub Releases.
7. Confirm `v1.0.0` resolves to
   `63d3639970d2ab42d3a4658bc69c692248a7d2fb`.
8. Confirm the candidate commit subjects:

```text
86733a8 Merge pull request #17 from shhchan/PUYO-53/v1-1-0-realtime-core
0ee59f0 Merge pull request #18 from shhchan/PUYO-54/v1-2-0-realtime-ai
c2ce216 Merge pull request #19 from shhchan/PUYO-55/v1-3-0-experiment-management
b39050c Merge pull request #26 from shhchan/integration/puyo-56
2737093 Merge pull request #35 from shhchan/PUYO-97/pre-puyo-58-adjustments
```

Suggested local checks:

```bash
git fetch origin --tags
git status --short --branch
git tag --sort=version:refname
git log --all --decorate --oneline -n 100
git show -s --format='%H %s' \
  86733a8450e5c863d6893ae720202337fd6bd03a \
  0ee59f026445caababd2abe73d4dbea1ef1836c5 \
  c2ce216b52d80d306ec040455828458a27b2f8ca \
  b39050cf763ca5e50de84540df20089928d74cc4 \
  2737093c13438ec2f5955ec603a69aba14f66b63
gh pr list --repo shhchan/puyo-ai-dev-platform --state open
gh release list --repo shhchan/puyo-ai-dev-platform
```

If remote history differs from this document, do not update candidate SHAs
silently. Explain the difference and propose an amended boundary.

### Phase 1: Include this runbook in the release train

This repository expects the `PUYO-102/release-runbook` PR to target
`integration/puyo-58`. If it has not been merged:

1. Confirm the branch contains only the runbook change.
2. Run documentation checks available in the repository.
3. Create a PR with this metadata:

```text
Base: integration/puyo-58
Head: PUYO-102/release-runbook
Title: [PUYO-102] v1 リリース作業手順書を整備
```

Use the required Japanese PR body format:

```markdown
## What

## Why

## QA

## References
```

4. Wait for CI and review.
5. Ask for approval before merging with **Create a merge commit**.

### Phase 2: Integrate PUYO-58

After Phase 1 is merged:

1. Confirm every PUYO-58 child issue (`PUYO-85` through `PUYO-89`) is
   `Complete` or `完了`.
2. Confirm `origin/integration/puyo-53-58` is an ancestor of
   `origin/integration/puyo-58`:

```bash
git merge-base --is-ancestor \
  origin/integration/puyo-53-58 origin/integration/puyo-58
```

3. Review the first-parent commits and full diff.
4. Create or reuse the PR:

```text
Base: integration/puyo-53-58
Head: integration/puyo-58
Title: [PUYO-58] human-in-the-loop 学習フローを統合
```

5. Ensure the PR body contains the required sections and references
   `PUYO-58`, `PUYO-85` through `PUYO-89`, and `PUYO-102`.
6. Wait for CI and review. Resolve all required feedback.
7. Ask for approval before merging with **Create a merge commit**.

After the merge, fetch again and confirm `origin/integration/puyo-53-58`
contains the merge commit and all PUYO-58 commits.

### Phase 3: Integrated release QA

Create a temporary worktree from the updated integration branch. Do not run
release QA from an older feature branch.

```bash
git worktree add /tmp/puyo-integration-53-58 origin/integration/puyo-53-58
cd /tmp/puyo-integration-53-58
python3 -m unittest discover -s tests -q
```

Also run the human-visible checks documented in
`docs/development/puyo-57-58-execution-plan.md`. At minimum:

```bash
python3 main.py
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
  python3 -m unittest tests.test_launcher tests.test_realtime_versus_ui -q
python3 -m unittest \
  tests.test_human_dataset \
  tests.test_human_training \
  tests.test_promotion_gate \
  tests.test_human_audit -q
```

Record commands, exit status, visible behavior, and generated artifact paths.
Remove temporary worktrees only after results have been recorded:

```bash
git worktree remove /tmp/puyo-integration-53-58
```

If acceptance criteria are satisfied, add one Jira session comment to
`PUYO-57` and `PUYO-58` and transition each remaining epic from `進行中` to
`Complete`. Do not transition to `完了`.

### Phase 4: Integrate into master

1. Confirm `master` still points at or descends from `v1.0.0` and contains no
   unexpected release-train commits.
2. Confirm Phase 3 passed and Jira epic state is consistent.
3. Create or reuse this PR:

```text
Base: master
Head: integration/puyo-53-58
Title: [PUYO-58] v1.1.0-v1.6.0 を master に統合
```

4. In `What`, summarize all six minor versions.
5. In `QA`, list the complete test and visible QA commands and results.
6. In `References`, link `PUYO-53` through `PUYO-58` and the integration PR.
7. Wait for CI, required review, and resolution of all blocking feedback.
8. Ask for explicit user approval before merging.
9. Merge using **Create a merge commit**. Do not squash or rebase.
10. Fetch `master` and record the resulting full merge commit SHA. This commit
    is the candidate for `v1.6.0`.

### Phase 5: Verify release boundaries on master

Verify that all stable boundaries are ancestors of the new `origin/master`:

```bash
git merge-base --is-ancestor \
  86733a8450e5c863d6893ae720202337fd6bd03a origin/master
git merge-base --is-ancestor \
  0ee59f026445caababd2abe73d4dbea1ef1836c5 origin/master
git merge-base --is-ancestor \
  c2ce216b52d80d306ec040455828458a27b2f8ca origin/master
git merge-base --is-ancestor \
  b39050cf763ca5e50de84540df20089928d74cc4 origin/master
git merge-base --is-ancestor \
  2737093c13438ec2f5955ec603a69aba14f66b63 origin/master
```

Verify the `v1.6.0` candidate is the merge commit of the Phase 4 PR and that
the integration branch is its parent or ancestor. Stop if any check fails.

### Phase 6: Validate each version snapshot

Validate versions in ascending order. Use a separate worktree for each
candidate:

```bash
git worktree add /tmp/puyo-v1.1.0 \
  86733a8450e5c863d6893ae720202337fd6bd03a
cd /tmp/puyo-v1.1.0
python3 -m unittest discover -s tests -q
```

Repeat for every candidate and remove each worktree after recording results.
In addition to the complete suite, inspect the corresponding Jira epic's
acceptance criteria and run its documented benchmark or visible smoke check.

| Version | Required targeted areas |
| --- | --- |
| `v1.1.0` | Realtime headless core, action planner, replay determinism |
| `v1.2.0` | Realtime AI, arena, paired evaluation, realtime viewer |
| `v1.3.0` | Artifact schema, checkpoint restore, experiment suite, lineage |
| `v1.4.0` | Search objectives, strategy workers, N-turn plans, benchmark suite |
| `v1.5.0` | Launcher, settings, model viewer, plan overlay, UI regression smoke |
| `v1.6.0` | Human dataset, training, promotion gate, audit, deletion safety |

Do not tag a snapshot with failing tests. Decide whether a failure is an
environment problem or a product defect, provide evidence, and stop.

### Phase 7: Create and push annotated tags

Before creating any tag, verify it does not exist locally or remotely:

```bash
git ls-remote --exit-code --tags origin refs/tags/v1.1.0
```

Exit code `2` means the exact ref was not found. Any returned ref requires
inspection; never overwrite it.

Create annotated tags locally only after Phase 6 passes:

```bash
git tag -a v1.1.0 86733a8450e5c863d6893ae720202337fd6bd03a \
  -m "Release v1.1.0"
git tag -a v1.2.0 0ee59f026445caababd2abe73d4dbea1ef1836c5 \
  -m "Release v1.2.0"
git tag -a v1.3.0 c2ce216b52d80d306ec040455828458a27b2f8ca \
  -m "Release v1.3.0"
git tag -a v1.4.0 b39050cf763ca5e50de84540df20089928d74cc4 \
  -m "Release v1.4.0"
git tag -a v1.5.0 2737093c13438ec2f5955ec603a69aba14f66b63 \
  -m "Release v1.5.0"
git tag -a v1.6.0 "${V1_6_MASTER_MERGE_SHA}" -m "Release v1.6.0"
```

Inspect every local tag before pushing:

```bash
git show --no-patch --decorate v1.1.0
git rev-list -n 1 v1.1.0
```

The default staged-release policy is one stable version per approval cycle:

1. Present the tag, full commit SHA, test result, and release notes.
2. Ask for approval to push that single tag.
3. Push only that tag: `git push origin refs/tags/v1.1.0`.
4. Verify the remote tag resolves to the intended commit.
5. Continue to Phase 8 for the same version before advancing.

Do not use `git push --tags`, because it can publish unrelated local tags.

If a local tag is wrong and has not been pushed, delete it locally and recreate
it. If a wrong tag has been pushed, stop and ask the user to choose a recovery
policy; do not move it automatically.

### Phase 8: Publish GitHub Releases

Create one GitHub Release from each pushed tag in ascending order. Use Japanese
release notes with this structure:

```markdown
## What

## Highlights

## QA

## Compatibility

## References
```

Each release must include:

- The Jira epic and important child issues.
- The previous-to-current tag comparison.
- Exact QA commands and results.
- Known limitations and migration notes.
- A statement that fixes increment the patch component, for example from
  `v1.6.0` to `v1.6.1`, while incompatible public API changes require the next
  major version.

Example command after preparing a reviewed notes file:

```bash
gh release create v1.1.0 \
  --repo shhchan/puyo-ai-dev-platform \
  --verify-tag \
  --title "puyo_ai_dev_platform v1.1.0" \
  --notes-file /tmp/puyo-v1.1.0-release-notes.md
```

GitHub may select Latest automatically from publication date and version. It is
acceptable for each successively published version to be Latest temporarily.
Publish `v1.6.0` last with `--latest` and verify that it is Latest. Verify the
release page and tag after every publication, then report and wait for approval
before moving to the next version.

## 6. Jira release bookkeeping

The epics currently use version-like summaries and labels, while their Jira
`Fix Version` fields may be empty. Before changing Jira release metadata:

1. Query project versions and confirm whether `1.1.0` through `1.6.0` or
   `v1.1.0` through `v1.6.0` already exist.
2. Do not create duplicate Jira versions with a different prefix.
3. Ask for approval before creating project versions or setting `Fix Version`.
4. Set a version's release state only after its GitHub Release is published.
5. Add or update one session comment on the corresponding epic containing:
   - Implemented scope.
   - Merge and tag SHA.
   - GitHub Release URL.
   - Test and visible QA results.
   - Remaining work or known limitations.
6. Transition Codex-owned work to `Complete`, never `完了`.

## 7. Failure and rollback policy

### Before tag push

- Fix the release branch through a new Jira ticket and PR.
- Repeat integration QA and affected snapshot QA.
- Recalculate only boundaries affected by the new commit.

### After tag push but before GitHub Release

- Do not move the tag.
- Stop and ask whether to publish with a known limitation or create a patch
  release.

### After GitHub Release

- Do not delete or replace a stable release automatically.
- Fix a defect in `vX.Y.1` when backward compatible.
- Use the next minor version for backward-compatible features.
- Use `v2.0.0` for incompatible public API or persisted-data changes.
- If deployment rollback is needed, deploy the previous stable tag and record
  the rollback in Jira and the release notes.

## 8. Completion criteria

The release train is complete only when:

- The PUYO-58 integration PR is merged into `integration/puyo-53-58` with a
  merge commit.
- The final integration PR is merged into `master` with a merge commit.
- Full automated tests and required human-visible QA pass.
- `v1.1.0` through `v1.6.0` are annotated tags at verified boundaries.
- Six corresponding GitHub Releases exist and `v1.6.0` is Latest.
- Jira comments contain merge, tag, release, and QA evidence.
- Applicable Jira work is `Complete`, not `完了`.
- The worktree is clean and temporary worktrees are removed.

The final report must list every PR URL, merge SHA, tag SHA, GitHub Release URL,
Jira status, QA command/result, and any residual risk.

## 9. Suggested prompt for a future Codex session

```text
AGENTS.md と docs/development/puyo-v1-release-runbook.md をすべて読み、
現在の remote・Jira・PR・tag・GitHub Release の状態を再確認してください。
完了済みフェーズを判定し、次の未完了フェーズだけを実施してください。
PR merge、tag push、GitHub Release 公開の直前では、対象と検証結果を提示して
私の承認を待ってください。履歴の squash/rebase、既存 tag の移動、Jira の
「完了」への遷移は行わないでください。
```
