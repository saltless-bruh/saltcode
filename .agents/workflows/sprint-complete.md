---
description: Finalize a sprint — present the diff for human review, surface the integration warning, and check for compaction.
---

# /sprint-complete — Sprint Finalization

Run this after all tasks in `tasks.json` are done (or flagged).

## Step 1: Diff Summary
- Run `git diff HEAD~<n_tasks> HEAD` (or the range since sprint start).
- Present a summary of files changed, lines added/removed, and tasks completed vs flagged.

## Step 2: Integration Warning (mandatory)
Display this notice — it is a hard requirement (REQ-FAIL-004):

> ⚠️ **Logical cross-task integration is YOUR check.**
> The framework verified type safety (static gate), constraint legality (Evaluator),
> and per-task correctness (tests + Auditor), but it does NOT verify that tasks compose
> correctly across boundaries (e.g. task-2 returns cents, task-5 expects dollars — both
> typed `number`). Review the diff with this in mind before shipping.

## Step 3: Compaction Check
- Count sprints since last compaction (stored in `.saltcode/meta.json`).
- If `sprints_since_compaction >= 5` → run the Spec Compactor (`saltcode-compactor` skill).
- Reset the counter.

## Step 4: Ship Decision
- Present the diff for human review.
- Human says "ship" → commit/push.
- Human says "needs changes" → start a new sprint with the feedback as the goal.
