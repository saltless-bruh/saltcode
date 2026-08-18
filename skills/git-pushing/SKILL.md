---
name: git-pushing
description: Stage, write a conventional commit, and push to the remote branch — deliberately as explicit steps rather than a one-shot script, because Saltcode's checkpoint discipline governs when a commit and a push are allowed at all.
risk: reviewed
source: community (Antigravity skills marketplace bulk install, 2026-06-20)
licence: undetermined
date_added: "2026-02-27"
reviewed: "2026-08-02"
---

# Git commit and push

> **Adopted with its script removed (2026-08-02).** Upstream shipped
> `scripts/smart_commit.sh`, which ran `git add .` → `git commit` → `git push -u origin
> $BRANCH` unconditionally. Nothing about it was malicious; it simply encoded the one
> workflow this project forbids. See *Why there is no script* below. Provenance and the
> full trust review are in `COOKBOOKS.md`.

## When this applies

When the human explicitly asks to commit and/or push — "push this", "commit and push",
"save to github". Not on your own initiative, and not as a tidy-up at the end of a task.

## Why there is no script

Two project rules make an unconditional stage-commit-push wrong here, and a script cannot
express either of them:

- **Pushing is never automatic.** `git push` happens only when `auto_push = true`, in
  every mode including Full Auto. A checkpoint is a *local* commit; publishing it is a
  separate decision the human owns.
- **The tree is deliberately uncommitted mid-task.** Between `apply_live` and a green
  regression gate, Saltcode leaves changes uncommitted on purpose — the commit is what a
  passing regression run *earns*. A `git add .` in that window commits an un-cleared
  apply, and sweeps in `.saltcode/` state and Test Intent's uncommitted
  `tests/task_*_spec.*` besides.

So the steps below are explicit, and each is a point at which you can notice the state is
not one you should be committing.

## The steps

1. **Look before staging.** `git status --short` and `git diff`. Stage deliberately —
   `git add <paths>` — rather than `git add .`, so nothing arrives that you did not
   intend.
2. **Check what you are about to commit.** `git diff --cached`. Watch for state files,
   uncommitted specs, and anything under `.saltcode/`.
3. **Write a conventional commit message** — `type(scope): summary`, with a body that
   says *why*, not what the diff already shows.
4. **Push only when asked**, and always with upstream set:
   `git push -u origin <branch-name>`. On a network failure, retry with backoff rather
   than switching branch or forcing.

## Never

- `git add .` on a tree you have not just read.
- `git push --force` (or `--force-with-lease`) unless the human asked for it explicitly.
- Committing to rescue a mid-task tree. If the loop left it uncommitted, that is the
  system working — use `/rollback`, not a commit.
- Pushing to any branch other than the one you were told to develop on.

## Limitations

Use this only when the request clearly matches. If the branch, the intended scope of the
commit, or whether to push at all is unclear, stop and ask.
