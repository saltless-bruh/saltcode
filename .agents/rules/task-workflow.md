---
trigger: always_on
---

# Saltcode Task Workflow Rules

Every task implementation must follow this lifecycle exactly.

## 1. Pre-Implementation Gate

Before writing any code, verify:
- [ ] The task's `depends_on` items are all marked done in `specs/tasks.md`.
- [ ] The task description does not contradict `specs/design.md` or `specs/requirements.md`.
- [ ] Files in `files_affected` exist (or the task explicitly creates them).
- [ ] If multiple approaches could work, stop and ask the human — do not guess.

## 2. Phase-2 Pipeline (the full gate sequence)

```
Builder → Diff Format Check → Sandbox Apply → Static Gate → Test Runner → Auditor → Apply Live
```

Each gate short-circuits on failure (counts against the shared budget). The Auditor is NEVER called on a dirty/failing diff.

### Gate details:
1. **Diff Format Check**: output must be valid unified diff (`--- a/` / `+++ b/` / `@@ @@`). Malformed → impl_fail.
2. **Sandbox Apply**: `git worktree add` or temp copy. Diff applied via `git apply`. Live tree NEVER touched.
3. **Static Gate** (on sandbox): `pyright --strict` + `ruff check` (Python), `tsc --noEmit` + `eslint` (TS), `cargo clippy` + `cargo check` (Rust), `go build` + `go vet` (Go). DIRTY → discard sandbox, retry Builder.
4. **Test Runner** (on sandbox): run `tests/task_{id}_spec.*` via project config's `test_runner_cmd`. FAIL → discard sandbox, retry Builder. If no `test_runner_cmd` configured → SKIP.
5. **Auditor**: receives clean static report + test results + diff + spec content. Runs N=3 stability passes.

### Verdicts:
- `pass` → apply diff to live repo, mark task done, update `specs/tasks.md` checkbox.
- `impl_fail` → retry Builder with error details.
- `gaming_suspected` → retry Builder with "no hardcoding" reason and the flagged patterns.
- `spec_defect` → re-run Test Intent with `audit_result.detail` as feedback (≤1, does NOT consume a retry).

### Budget:
- Shared per-task budget = **3 total** (diff-format + static + test + impl_fail + gaming).
- **Tier-A sub-cap = 2**: after 2 failures on Tier A, the 3rd attempt escalates to Tier B.
- If the 3rd attempt also fails (or task was already on Tier B) → **FLAG HUMAN**.
- `spec_defect` re-spec does NOT count against this budget.

## 3. Post-Task

After Auditor passes: apply the diff to the live working tree, check off the task in `specs/tasks.md`, and proceed to the next task in topological order.
