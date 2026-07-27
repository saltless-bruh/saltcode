---
name: saltcode-builder
description: Guides the Builder agent to implement a single task using scoped file reads, producing a valid unified diff without editing tests.
---

# Saltcode Builder Skill

Use this when implementing a single task in Phase 2 of the Saltcode workflow.
Contract: REQ-BLD-001/002/003, REQ-ORC-006. Shape: `specs/design.md §7`, §10.

The Builder runs in an **isolated context** on a local Saltnitor model. One task per
context, no carryover — enforced by construction, not by discipline.

## Context Reconstruction (mandatory on every task)

1. Read the current task object from `.saltcode/tasks.json`.
2. Read the task's spec file: `.saltcode/tests/task_{id}_spec.*`.
3. Use the LSP tools (`outline`, `where_is`, `find_references`) for symbol context.
4. Read the full body of files listed in `task.files_affected` ONLY, via
   `saltcode_read_scoped`.
5. **Do NOT read**: `design.md`, sibling tasks, `context_report.json`, or any file
   outside `files_affected`. The `tool_call` handler blocks it anyway.
6. **Do NOT carry over** state, notes, or context from a prior task or retry.

## Output Format

Your output MUST be a valid **unified diff**:

```diff
--- a/saltcode/contracts/context_report.py
+++ b/saltcode/contracts/context_report.py
@@ -1,3 +1,5 @@
+from pydantic import BaseModel
+
 class ContextReport(BaseModel):
     schema_version: str
```

If your output is not parseable by `git apply --check`, one bounded repair is
attempted (extracting a diff from a code fence) and then it is rejected as
`impl_fail` before any further gate runs.

Your diff is applied to a **disposable sandbox worktree inside a security
container** — never the live tree. It reaches the live tree only after the Auditor
returns `pass`, and it is committed only after the regression gate also passes.

## Forbidden Actions

- **No edits to anything under `tests/`**: the write-path allowlist rejects any hunk
  touching test files, at `tool_call` and again in the backend. You make tests pass by
  writing correct implementation code, never by weakening the tests.
- **No file reads outside `task.files_affected`**: the broker denies the read.
- **No hardcoding test fixtures**: if your code returns literal values matching test
  expectations without real logic, the Auditor flags `gaming_suspected`.
- **No commands outside the allowlist**: only `pytest`, `jest`, `cargo test`,
  `go test`, `pyright`, `ruff`, `tsc`, `eslint`, `cargo check`, `cargo clippy`,
  `go build`, `go vet`, `git apply` execute — and only inside the container. Pi's
  built-in `write`/`edit`/`bash` are overridden to route there too.

## What "Making the Tests Pass" Means

The test spec describes desired behavior. Your implementation must satisfy that
behavior through genuine logic — computing the right answer, not memorizing it. If you
find yourself writing `if input == "test_value": return "expected_output"`, stop.
That's gaming, and the faithfulness gate is built to catch exactly it.

## Retry Reasons You May Receive

Each of these consumes one slot of the shared per-task budget of 3:

- **malformed diff** — your output wasn't a parseable unified diff.
- **static gate DIRTY** — type or lint errors; the reason text is the compiler output.
- **test FAIL** — the task spec failed; the reason text is the test output.
- **`impl_fail`** — the Auditor judged the implementation incorrect.
- **`gaming_suspected`** — rewrite with genuine logic; the flagged patterns are named.
- **`regression_fail`** — your task's spec passed but you broke the project's full
  suite *within your own `files_affected`*. Fix the regression without abandoning the
  task's acceptance criteria. (Regressions outside your scope are never routed to you
  — they go to the human.)

`spec_defect` is not your problem and does not consume your budget: the spec itself
is re-written by Test Intent, then you retry against the corrected spec.

## Tier Routing (handled for you)

- `task.complexity ∈ {low, med}` → Tier A (Qwen3.5-9B, in-VRAM, fast).
- `task.complexity == high` → Tier B (Qwen3.6-35B-A3B, stronger) from attempt 1.
- Two failures on Tier A → the 3rd attempt escalates to Tier B. A third failure flags
  the human; there is no fourth attempt.
- Large inputs (> ~32K tokens) switch the profile to `A_FOCUS` (256K context) with
  thinking `off` — the VRAM triangle forbids thinking plus long context together.
