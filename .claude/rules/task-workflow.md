# Saltcode Task Workflow Rules

Every task implementation follows this lifecycle exactly.

## 1. Pre-implementation gate

Before writing any code, verify — in this order:

- [ ] You have read the task's **Satisfies** REQ ids in `specs/requirements.md` and
      the `specs/design.md` section it points at. If either is thin or silent, read
      `docs/proposal/`. See `.claude/rules/source-of-truth.md`.
- [ ] **You have read `specs/known_gaps.md`.** Every gap whose **Closed by** names
      this task is part of the work. Every open gap touching a file you are about
      to change is context you need before you change it.
- [ ] The task's `depends_on` items are all checked in `specs/tasks.md`.
- [ ] The task does not contradict `specs/design.md` or `specs/requirements.md`.
      If it does, that is a spec defect — surface it, do not code around it.
- [ ] The work belongs in the layer the task names (extension / backend / skill).
      Do not add orchestration to the backend or compute to the extension.
- [ ] Files in `files_affected` exist, or the task explicitly creates them.
- [ ] If several approaches could work, stop and ask the human — do not guess.

## 2. Phase-2 pipeline (the full gate sequence)

```
Builder → Diff Check → Sandbox Apply → Static Gate → Test Runner → Auditor
   → Apply Live (uncommitted) → Regression Gate → Checkpoint (commit + snapshot)
```

Every gate short-circuits on failure and counts against the shared budget. **The
Auditor is never called on a malformed, dirty, or failing diff.**

### Gate details

1. **Diff format check** (`saltcode_diff_check`) — output must be a parseable unified
   diff (`git apply --check`). One bounded repair from a code fence is allowed, then
   malformed → `impl_fail`.
2. **Sandbox apply** (`saltcode_sandbox_apply`) — disposable git worktree, inside the
   security container. The live tree is never touched here.
3. **Static gate** (`saltcode_static_gate`, on the sandbox) — `pyright --strict` +
   `ruff` (Python, MEDIUM) · `tsc` + `eslint` (TS, HARD) · `cargo check` + `clippy`
   (Rust, HARD) · `go build` + `vet` (Go, HARD) · `eslint` (JS, SOFT). DIRTY →
   discard sandbox, retry Builder with the lint reason.
4. **Test runner** (`saltcode_test_run`, same container) — runs
   `tests/task_{id}_spec.*` via `test_runner_cmd`. FAIL → discard sandbox, retry
   Builder with the test output. No `test_runner_cmd` configured → SKIP.
5. **Auditor** (`saltcode_stability`) — receives the clean static report, test
   results, spec content, and the diff. Runs N=3 passes with varied conditions;
   computes `stability_score` and `gac`. Unstable **and** online → one DeepSeek Flash
   re-judgment, which is final. Offline → majority local verdict stands.

### After the Auditor passes

6. **Apply live** (`saltcode_apply_live`) — apply the diff to the live tree, **leave
   it uncommitted**. Write-path checked: no `tests/**`.
7. **Regression gate** (`saltcode_regression`) — run the project's FULL suite
   (`regression_cmd`) on the integrated live tree, in the container. No
   `regression_cmd` configured → SKIP, and the checkpoint is marked
   `regression: unverified`.
8. **Checkpoint** (`saltcode_checkpoint`) — git commit + append
   `saltcode:checkpoint {task_id, commit_sha, gate_results, stability, regression,
   timestamp, sprint_id}`. Only now check the task's box in `specs/tasks.md` and
   advance.

**The loop never advances past a task that has no checkpoint.** Pushing stays
explicit: no automatic `git push` unless `auto_push = true`, in any mode.

### Verdicts

- `pass` → apply live → regression → checkpoint → next task.
- `impl_fail` → retry Builder with the error detail.
- `gaming_suspected` → retry Builder with the "no hardcoding" reason and the flagged
  patterns.
- `spec_defect` → re-run Test Intent with `audit_result.detail` as feedback (≤1, does
  **not** consume a retry). A second `spec_defect` on the same task → FLAG HUMAN.
- `regression_fail` → **if** the failing tests are inside `task.files_affected` and
  budget remains → Builder retry with the regression output (counts against the
  budget). **Else** → FLAG HUMAN. Failures outside `files_affected` are the Trade-B
  integration signal and are never auto-fixed. Discard the uncommitted apply
  (`git reset --hard <last checkpoint>`); leave the tree at the last checkpoint.

### Budget

- Shared per-task budget = **3 total** (diff-format + static + test + `impl_fail` +
  gaming + `regression_fail`).
- **Tier-A sub-cap = 2**: after 2 failures on Tier A the 3rd attempt escalates to
  Tier B. If the task started on Tier B (`complexity == high`), all 3 are on Tier B.
- 3rd attempt fails → **FLAG HUMAN**. No silent spinning, ever.
- `spec_defect` does not consume a retry.
- Counters live in session state (`pi.appendEntry`), are observable, and are never
  silently reset mid-task or mid-sprint.

## 3. Run modes

`auto_mode` in `saltcode.toml [checkpoint]` — `off` (default) | `hybrid` | `full`.

- **`off`** — one sprint, the four decision points as normal.
- **`hybrid`** — approve the plan once (Decision 1), auto-advance the whole list
  through checkpoints, then a **mandatory cumulative-diff review** at the end. The
  safe default for real work.
- **`full`** — unattended; surfaces only on a stop condition. This **trades away the
  integration review**: a semantic-composition bug no test covers commits silently.
  Only appropriate with genuinely strong regression coverage.

Decision 1 gates the launch in every mode. FLAG HUMAN always interrupts.

## 4. Resume and rollback

- On session start, replay checkpoint entries, take the latest for the sprint, and
  verify `git HEAD == commit_sha`. Match → resume at the next task; finished work is
  never re-run. Mismatch → FLAG HUMAN to reconcile, never continue blindly.
- `/rollback last` or `/rollback <task_id>` → `git reset --hard <checkpoint_sha>`,
  rewind state, log to the audit log, and warn that later uncommitted work is lost.

## 5. Post-task

After the checkpoint lands: tick the box in `specs/tasks.md` and move to the next task
in topological order. Remove the sandbox worktree. At the end of the list, run
`/sprint-complete`.

**Update `specs/known_gaps.md` in the same change**, both directions:

- **Tick** `- [x]` every gap this task actually closed, dated, saying how it was
  verified. Closed means *gone* — not worked around, not moved elsewhere.
- **Add** an entry for every gap noticed and not closed: unverified gate legs,
  capabilities deferred to a later task, spec or doc drift, limitations accepted
  for now. Include the ones that belong to somebody else's task — that is the
  point of the list. Give each a severity and name the task that closes it.

Never delete an entry. A ticked gap is the record that it was real and is gone.
