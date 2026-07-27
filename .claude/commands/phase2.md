---
description: Run the Phase 2 execution loop — Builder, the five gates, regression, and a checkpoint per task.
argument-hint: "[task_id | all]"
---

# /phase2 — Execution Loop

Implements each task in `tasks.json` in `depends_on` order. Loops locally at $0 API
cost. Bound by `specs/requirements.md`; shaped by `specs/design.md` §10 and §10.1.

Target: **$ARGUMENTS** (default: all remaining tasks)

```
Builder → Diff Check → Sandbox → Static Gate → Test Runner → Auditor
   → Apply Live (uncommitted) → Regression → Checkpoint (commit) → next task
        ▲                                                    │
        └──── retry (shared budget ≤ 3, Tier-A sub-cap 2) ────┘
```

## Per task

### Step 1 — Tier selection

- `complexity ∈ {low, med}` → profile `A_STD`; use `A_FOCUS` instead when estimated
  input > `a_focus_threshold` (default 32,768 tokens), and set thinking `off` on it.
- `complexity == high` → profile `B`.
- `POST http://127.0.0.1:8765/v1/ensure {"profile":"<name>"}` before inference. Oracle
  refuses (OOM) → **FLAG HUMAN**; never crash the box.
- VRAM triangle: thinking + 256K ctx + MTP — hold any two, never all three.
  Builder and Auditor run sequentially so exactly one model is resident.

### Step 2 — Builder — `saltcode-builder`

Context is exactly: one task object + `task.files_affected` bodies (scoped read) +
AST slices + the task spec. **No** sibling tasks, **no** `design.md`, **no** files
outside scope, **no** carryover from a prior task or retry. Output: a unified diff.

### Step 3 — Diff format check — `saltcode_diff_check`

Malformed (after one bounded code-fence repair) → `impl_fail`, increment the budget,
back to Step 2. The Auditor is not called.

### Step 4 — Sandbox apply — `saltcode_sandbox_apply`

Disposable git worktree inside the security container. The live tree is untouched.

### Step 5 — Static gate — `saltcode_static_gate` (on the sandbox)

Per-language runners; record gate strength. DIRTY → discard the sandbox, log the lint
reason, increment the budget, back to Step 2.

### Step 6 — Test runner — `saltcode_test_run` (same container)

Runs `.saltcode/tests/task_{id}_spec.*` via `test_runner_cmd`. FAIL → discard the
sandbox, log the test output, increment the budget, back to Step 2. No
`test_runner_cmd` → SKIP.

### Step 7 — Auditor — `saltcode_stability`

Receives the diff + clean static report + test results + spec content. Zero-cost
heuristics (fixture-literal returns, empty/throw-only bodies, test-input-keyed
branches) then N=3 stability passes → `audit_result.json`.

Unstable **and** online → one DeepSeek Flash re-judgment, final. Offline → majority
local verdict stands.

- `pass` → Step 8.
- `impl_fail` → increment budget, back to Step 2.
- `gaming_suspected` → increment budget, back to Step 2 with the "no hardcoding"
  reason and the flagged patterns.
- `spec_defect` → re-run Test Intent with `audit_result.detail` as feedback
  (≤1, does **not** consume a retry), then back to Step 2. A second `spec_defect` on
  the same task → FLAG HUMAN.

### Step 8 — Apply live — `saltcode_apply_live`

Apply the diff to the live tree and **leave it uncommitted**. Write-path checked: any
hunk touching `tests/**` is refused.

### Step 9 — Regression gate — `saltcode_regression`

Run the project's FULL suite (`regression_cmd`) on the integrated live tree, in the
container. Not configured → SKIP; mark the checkpoint `regression: unverified`.

On failure: discard the uncommitted apply (`git reset --hard <last checkpoint>`), then

- failing tests **inside** `task.files_affected` and budget remains → Builder retry
  with the regression output (`regression_fail`, counts against the budget);
- failing tests **outside** `files_affected`, or budget exhausted → **FLAG HUMAN**.
  This is the Trade-B integration signal; it is never auto-fixed. Leave the tree at
  the last checkpoint.

### Step 10 — Checkpoint — `saltcode_checkpoint`

Git commit (message from task id + description) + append `saltcode:checkpoint
{task_id, commit_sha, gate_results, stability, regression, timestamp, sprint_id}`.
Tick the task's box in `specs/tasks.md`. **Only now** advance.

Never `git push` unless `auto_push = true`.

## Budget enforcement

- After each failure: `retry_count == 2 && tier == A` → escalate attempt 3 to Tier B.
- After each failure: `retry_count >= 3` → **FLAG HUMAN**, stop this task.
- `spec_defect` never decrements the budget.

## Cleanup

After each task — pass, flag, or abandon — remove the sandbox worktree
(`git worktree remove`). Never leave a container or worktree behind.
