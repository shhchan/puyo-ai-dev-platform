---
name: pr-to-english
description: Translate an existing GitHub pull request title and description from Japanese to English for pre-merge record keeping, and optionally merge it when the user explicitly requests that in the same prompt. Use only when the user explicitly invokes `$pr-to-english` or explicitly names this skill; do not use implicitly from general PR, GitHub, translation, or merge-preparation requests.
---

# PR to English

## Overview

Rewrite an already-reviewed GitHub PR title and description in English before merge. Keep the PR's meaning, scope, QA notes, references, links, issue keys, file paths, and factual claims aligned with the original content.

## Preconditions

- Use this skill only after explicit invocation.
- Require an existing GitHub PR. Use the PR selector supplied by the user when present; otherwise use the current branch's PR.
- Require GitHub CLI authentication. If `gh pr view` or `gh api` fails due to auth or repository context, report the failure and stop.
- If a GitHub CLI command fails with `error connecting to api.github.com` in a sandboxed run, treat it as a network/sandbox restriction, rerun the same command with the required approval, and only stop if the approved rerun also fails.

## Workflow

1. Inspect the PR with `gh pr view`.
   - Prefer: `gh pr view <selector> --json number,title,body,url,state,baseRefName,headRefName`
   - Omit `<selector>` only when the current branch unambiguously maps to the target PR.
   - Record the PR number and `owner/repo` from the PR URL or repository context; use them for REST API updates.
2. Translate only the PR title and body to English.
   - Preserve the existing section structure unless the original body is malformed.
   - Preserve Jira keys, GitHub links, commit hashes, branch names, file paths, commands, code spans, check results, and dates exactly unless they are prose that needs translation.
   - Do not add new claims, remove caveats, invent QA, or broaden/narrow the described scope.
   - If the original content is ambiguous, choose a literal English rendering rather than clarifying by inventing detail.
   - Render the PR title as concise, natural English suitable for a pull request title. Avoid awkward literal translations.
   - Format the PR title in natural English Title Case, but do not mechanically capitalize every word.
     - Capitalize the first and last word of the title.
     - Capitalize nouns, pronouns, verbs, adjectives, adverbs, and subordinating conjunctions.
     - Lowercase articles, coordinating conjunctions, and short prepositions when they appear inside the title, including: `a`, `an`, `the`, `and`, `but`, `or`, `for`, `nor`, `as`, `at`, `by`, `in`, `of`, `on`, `per`, `to`, `vs`, `via`.
     - Capitalize those same small words when they are the first or last word of the title.
     - Keep `to` lowercase inside the title; do not change it to `To`.
     - Preserve the original spelling and capitalization of technical terms, proper nouns, acronyms, issue keys, branch names, package names, and code symbols, such as `Jira`, `GitHub`, `Codex CLI`, `MCP`, `PR`, `API`, `PUYO-13`, `AGENTS.md`, and `pnpm`.
     - Do not Title Case text enclosed in backticks, including code, file paths, commands, and identifiers.
     - For titles containing colons, dashes, or slashes, choose readable, natural English instead of applying punctuation-specific capitalization mechanically.
   - Title examples:
     - `Codex に Jira 操作ルールを追加` -> `Add Jira Operation Rules for Codex`
     - `PR description を英語に変換する skill を追加` -> `Add Skill to Convert PR Description to English`
     - `PUYO-13 の作業開始コメントを追加` -> `Add Work-Start Comment for PUYO-13`
     - `Codex CLI の PR 更新ルールを整理` -> `Organize PR Update Rules for Codex CLI`
     - `Jira から Complete への遷移を記録` -> `Record Transition from Jira to Complete`
3. Update the PR title/body with GitHub REST API via `gh api`.
   - Put the translated body in a temporary file outside the repository, such as under `/tmp`, so repo-tracked files are not changed.
   - Prefer REST API PATCH over `gh pr edit` to avoid GitHub CLI GraphQL queries that can fail on Projects classic `projectCards` deprecation.
   - Use:
     ```bash
     gh api repos/<owner>/<repo>/pulls/<number> \
       -X PATCH \
       -f title="<english title>" \
       -F body=@<tmp-file> \
       --jq '{number,title,body,html_url}'
     ```
   - This PATCH request must only send `title` and `body`.
   - Do not use `gh pr edit` by default. If `gh pr edit` is attempted and fails with `repository.pullRequest.projectCards` or `Projects (classic) is being deprecated`, do not retry `gh pr edit`; switch to the REST API PATCH command above.
4. Verify the update.
   - Prefer `gh api repos/<owner>/<repo>/pulls/<number> --jq '{number,title,body,html_url}'` so verification also avoids the `projectCards` GraphQL path.
   - `gh pr view <selector> --json number,title,body,url,state,baseRefName,headRefName` is acceptable when it succeeds.
   - Confirm the title and body now reflect the translated English content.
   - If no merge was requested, report the PR URL and summarize that only title/body were changed.
5. Merge the PR only when the user explicitly requests merging in the same prompt.
   - An explicit request such as “translate it with `pr-to-english` and merge the PR” authorizes the merge after the translation update and verification.
   - If the user only requests translation, stop after verification and do not merge.
   - Before merging, inspect the PR state, draft status, and mergeability with `gh api` and use the current PR head SHA as the expected head.
   - If the PR is a draft, call `POST repos/<owner>/<repo>/pulls/<number>/ready_for_review` before merging, then re-fetch the PR and confirm that it is no longer a draft.
   - Do not merge a closed, already merged, conflicted, or otherwise non-mergeable PR. If the PR cannot be made ready or is not mergeable, report the reason and stop.
   - Use the GitHub REST API merge endpoint and preserve the repository's configured merge policy. Do not force a merge method unless the user specifies one or the repository requires it.
   - Verify the merge result and report the merge commit or the reason it was not merged.

## Strict Boundaries

- Do not merge the PR unless the user explicitly requests the merge in the same prompt as the skill invocation.
- Do not approve, request changes, or submit reviews.
- Do not change labels, reviewers, assignees, milestones, projects, base branch, head branch, or branch contents. A merge is allowed only under the explicit condition above; changing a draft PR to ready for review is also allowed only as the required step immediately before that explicitly requested merge.
- Do not edit issue descriptions or Jira tickets.
- Do not use this skill at PR creation time unless the user explicitly requests pre-merge English conversion.
