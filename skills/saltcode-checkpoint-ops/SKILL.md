---
name: saltcode-checkpoint-ops
description: Working with Saltcode checkpoints — inspecting them with /checkpoints, rewinding with /rollback, and reading a regression failure correctly instead of auto-fixing it.
---

# Checkpoint operations

A checkpoint is the only boundary Saltcode treats as "this task is really done". It is a
git commit plus a `saltcode:checkpoint` session entry recording `{task_id, commit_sha,
gate_results, stability, regression, timestamp, sprint_id}`.

**The loop never advances past a task that has no checkpoint.** Everything below follows
from that.

## The order, and why it is that order

```
Auditor pass → apply_live (UNCOMMITTED) → regression gate → commit + checkpoint
```

The tree stays uncommitted between `apply_live` and a green regression run. That gap is
deliberate: the per-task tests prove the task, and the full suite proves the task did not
break anything else. Committing before the second check would make a broken integration
look exactly like a finished task in the history.

So an uncommitted working tree mid-sprint is **normal**, not a mistake to tidy up. Do not
commit it by hand to "clean things up" — that fabricates a checkpoint the gates never
granted.

## `/checkpoints`

Lists the checkpoints for the sprint: task id, commit sha, gate results, stability score,
and the regression verdict.

Read two fields carefully:

- **`regression: unverified`** means no `regression_cmd` is configured, so the gate was
  **skipped**, not passed. The checkpoint is real; the integration claim behind it is not.
- **`stability`** is the measured score over N passes, not a self-report. A checkpoint with
  a low score passed on a majority verdict the Auditor was not consistent about.

On session start the extension replays these entries, takes the latest for the sprint, and
verifies `git HEAD == commit_sha`. A match resumes at the next task and never re-runs
finished work. **A mismatch is FLAG HUMAN** — someone moved HEAD outside the loop, and
guessing which side is right would either redo completed work or skip it.

## `/rollback`

`/rollback last` or `/rollback <task_id>` → `git reset --hard <checkpoint_sha>`, rewind the
checkpoint and task state to that point, and log to `.saltcode/audit_log.jsonl`.

Before running it, know:

- It is a **hard reset**. Uncommitted work after that checkpoint is destroyed — including
  an `apply_live` that has not reached its regression gate.
- It rewinds *state*, not just the tree. Budget counters and task position move back with
  it, which is the point: a rewound task gets its retries back.
- After a rollback you may resume, skip the task, or abort. Resuming re-runs the task from
  the Builder.

Rollback is the intended response to a bad task, and it is cheap. Reaching for it is not a
failure — it is the mechanism that makes auto-advance safe enough to use.

## Reading a regression failure

This is the part that is easy to get wrong, because the obvious response is the wrong one.

When the full suite fails after `apply_live`, the question is **where** the failures are:

- **Inside `task.files_affected`, and budget remains** → the task broke its own work. A
  Builder retry with the regression output is correct, and it counts against the shared
  budget of 3.
- **Outside `task.files_affected`** → **FLAG HUMAN. Never auto-fix.**

That second case is Saltcode's Trade-B signal, and it is the most valuable output the whole
system produces. Type safety, constraint legality and per-task correctness are all
machine-enforced; *semantic composition across tasks* is not, and cannot be. The canonical
shape is task 2 returning cents while task 5 passes dollars — both typed `number`, both
tasks individually correct, the composition wrong.

A failure outside the task's scope is the system telling you it found the class of bug it
explicitly does not guarantee it can find. Handing that to a Builder to "fix" replaces a
precise signal with a guess by the agent least equipped to make it — it can see one task,
and the bug is between two.

Either way, the uncommitted apply is discarded (`git reset --hard <last checkpoint>`) and
the tree is left at the last checkpoint. Nothing half-integrated survives.

## Pushing

Never automatic. `git push` happens only when `auto_push = true`, **in any mode**,
including Full Auto. A checkpoint is a local commit; publishing it is a separate decision.

## Satisfies

REQ-EXT-016 AC3 · REQ-CHK-001 (checkpoint contents) · REQ-ORC-004/005 (resume and rollback)
· REQ-FAIL-001 (shared retry budget) · REQ-FAIL-004 (regression failures outside scope
flag the human) · Trade B (design §10.1, §19).
