---
description: Finalize a sprint — present the cumulative diff, surface the integration warning, check compaction.
---

# /sprint-complete — Sprint Finalization

Run after every task in `tasks.json` is checkpointed or flagged.

## Step 1 — Cumulative diff

- Diff from the sprint's first checkpoint to HEAD (`git diff <first_checkpoint>^..HEAD`),
  or the equivalent range since sprint start.
- Report: files changed, lines added/removed, tasks completed vs flagged, and any
  checkpoint marked `regression: unverified`.
- List the checkpoints so the human can `/rollback <task_id>` to any of them.

## Step 2 — Integration warning (mandatory)

Display this notice verbatim. It is a hard requirement (REQ-FAIL-004, Trade B):

> ⚠️ **Logical cross-task integration is YOUR check.**
> Saltcode verified type safety (static gate), constraint legality (Evaluator),
> per-task correctness (spec tests + Auditor), and integrated-suite health
> (regression gate). It does **not** verify that tasks compose *semantically* across
> boundaries — e.g. task 2 returns cents, task 5 passes dollars, both typed `number`,
> and no test covers the seam. The regression gate narrows this gap; it does not
> close it. Review the diff with this in mind before shipping.

In `full` auto mode this review was traded away by configuration — say so explicitly
rather than implying the run was reviewed.

## Step 3 — Compaction check

- Read `sprints_since_compaction` from `.saltcode/meta.json`.
- `>= 5` → run the Spec Compactor (`saltcode_compact_spec`, Flash / thinking `off`).
  It strips completed and resolved content **outside** `## HARD CONSTRAINTS` only;
  that block must come out byte-identical.
- Reset the counter.

## Step 4 — Ship decision

- Present the diff for human review (Decision 3), then the ship decision (Decision 4).
- "Ship" → commit any remainder and push. **Pushing is always explicit** — never
  automatic unless `auto_push = true`.
- "Needs changes" → start a new sprint with the feedback as the goal. The cache ladder
  runs first; an exact or confirmed-semantic hit reuses the prior spec at zero API cost.
