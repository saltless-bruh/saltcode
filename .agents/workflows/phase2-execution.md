---
description: Run the Phase 2 Execution Loop — Builder, sandbox gates, and Auditor for each task until all are done.
---

# /phase2 — Execution Loop Workflow

Implements each task from `tasks.json` in topological order. Loops at $0 API cost (local models).

```
[Start Task] → Builder → Diff Check → Sandbox → Static Gate → Test Runner → Auditor → [Apply Live]
                  ▲                                                              │
                  └──────────── retry (budget ≤ 3, Tier-A sub-cap 2) ────────────┘
```

## For Each Task (in `depends_on` order):

### Step 1: Tier Selection
- `complexity == low|med` → ensure profile `A_STD` (or `A_FOCUS` if estimated input > 32K tokens).
- `complexity == high` → ensure profile `B`.
- `POST http://127.0.0.1:8765/v1/ensure {"profile":"<name>"}`. If oracle refuses (OOM) → FLAG HUMAN.

### Step 2: Builder
- Invoke `saltcode-builder` skill.
- Context: one task object + scoped files (task.files_affected) + AST + task_spec.
- Output: unified diff.

### Step 3: Diff Format Check
- Validate output is a parseable unified diff (`git apply --check`).
- MALFORMED → `impl_fail`, increment retry counter, go to Step 2.

### Step 4: Sandbox Apply
- `git worktree add .sandbox-<task_id>` (or temp copy).
- `git apply` the diff inside the sandbox.

### Step 5: Static Gate (on sandbox)
- Run per-language static checkers.
- DIRTY → discard sandbox, log lint errors, increment retry counter, go to Step 2.

### Step 6: Test Runner (on sandbox)
- Run `.saltcode/tests/task_{id}_spec.*` via `test_runner_cmd`.
- FAIL → discard sandbox, log test output, increment retry counter, go to Step 2.
- SKIP if no `test_runner_cmd` configured.

### Step 7: Auditor
- Invoke `saltcode-auditor` skill.
- Receives: diff + clean static report + test results + spec content.
- Runs N=3 stability passes → `audit_result.json`.
- Verdicts:
  - `pass` → apply diff to live repo (`git apply` on real working tree), check off task.
  - `impl_fail` → increment retry counter, go to Step 2.
  - `gaming_suspected` → increment retry counter, go to Step 2 with "no hardcoding" instruction.
  - `spec_defect` → invoke `saltcode-test-intent` skill with `audit_result.detail` as feedback (≤1 re-spec, does NOT increment retry counter), then go to Step 2.

### Budget Enforcement
- After each failure, check: `retry_count >= 2 AND tier == A` → escalate to Tier B for attempt 3.
- After each failure, check: `retry_count >= 3` → **FLAG HUMAN**. Do not continue this task.

### Cleanup
- After each task (pass or flag), remove the sandbox: `git worktree remove .sandbox-<task_id>`.
