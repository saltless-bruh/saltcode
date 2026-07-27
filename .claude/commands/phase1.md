---
description: Run the Phase 1 Planning Quintuplet to produce a locked, validated specification from a goal.
argument-hint: "<goal>" [--scope path,path]
---

# /phase1 — Planning Workflow

Turns a goal into a complete, validated specification. **Fires exactly ONCE per
sprint** (REQ-ORC-001). Bound by `specs/requirements.md`; shaped by `specs/design.md`
§8. Read those before running this on real work.

Goal: **$ARGUMENTS**

```
[Goal] → Scout → Architect → Planner → Test Intent → Evaluator → [Spec Lock]
             ▲                                           │
             └────── design_gap / plan_gap ──────────────┘
```

Each agent runs in an **isolated context** with only its own inputs — no sibling
history, no accumulated session. Ordering is owned by this workflow, not by the model.
If an agent's skill is not yet authored, follow its row in `specs/design.md §7`
verbatim (inputs, outputs, hard rules) — do not improvise the contract.

## Step 1: Cache ladder (before firing anything)

Stop at the first hit.

1. Resolve the scope fingerprint: use `--scope` if given, else one `outline` MCP call
   (`saltcode_scope_probe`). This is a tool call, **not** a Phase-1 fire.
2. **Exact cache**: `sha256(normalized_goal + scope_fingerprint)`. Hit → reuse
   `tasks.json` with **zero API calls**, skip to `/phase2`.
3. **Semantic cache**: `embed(goal + scope)`, cosine ≥ threshold → candidate. Compute
   **PCD** (Prior Cluster Density). High PCD → cheap Architect confirmation; low PCD →
   skeptical: full confirmation or fall through. Confirmed → reuse.
4. Both miss → proceed below.

## Step 2: Scout — `saltcode-scout`

- Tools: `where_is`, `find_references`, `outline` only. **No file bodies, ever.**
- Writes `.saltcode/context_report.json` with `constraints[]` and `anti_patterns[]`.
- Validate with `saltcode_validate_contract` before continuing.

## Step 3: Architect — `saltcode-architect`

- Input: the goal + `context_report.json`. Model: Pro on a fresh project (Flash on
  amend), thinking `high`.
- Writes `.saltcode/design.md` including a `## HARD CONSTRAINTS` H2 block.
- **Verify:** every `constraints[]` and `anti_patterns[]` string appears **verbatim**
  in that block. Reject any output containing a task list.

## Step 4: Planner — `saltcode-planner`

- Input: `design.md` **only** — never `context_report.json`.
- Writes `.saltcode/tasks.json`: `id`, `description`, `files_affected[]`,
  `acceptance_criteria[]`, `depends_on[]`, `complexity ∈ {low,med,high}`.
- **Verify:** `depends_on` is acyclic with no dangling ids.

## Step 5: Test Intent — `saltcode-test-intent`

- Input: `tasks.json` + project config (`language`, `test_framework`,
  `test_runner_cmd`).
- Writes exactly one `.saltcode/tests/task_{id}_spec.*` per task, in the project's
  framework. No implementation code. Immutable to the Builder thereafter.

## Step 6: Evaluator — `saltcode-evaluator`

Run all four checks:

1. **traceability** — every task maps to a design statement.
2. **coverage** — every design requirement maps to a task.
3. **preservation** — every `context_report` constraint/anti_pattern is present in the
   HARD CONSTRAINTS block.
4. **compliance** — no task violates any constraint or anti_pattern.

Route by gap type:

- `design_gap` → back to Step 3 (Architect re-emits `design.md`, then Step 4 re-runs).
  **Max 2 Architect loops per sprint.**
- `plan_gap` / `constraint_violation` → back to Step 4 (Planner re-emits
  `tasks.json`). **Max 3 Planner loops per sprint.** A `constraint_violation` that
  conflicts with `design.md` itself routes to the Architect instead.
- Cap exceeded → **FLAG HUMAN** with `evaluator_report.json`. Do not continue.

## Step 7: Spec lock

- Store the spec hash: `sha256(normalized_goal + scope_fingerprint) → tasks.json`,
  with `scope_fingerprint = sorted(tasks.json[*].files_affected)`.
- Surface **Decision 1** (plan review) to the human.
- On approval the Phase Gate fires automatically → `/phase2`. No human action is
  needed to cross the gate itself (REQ-ORC-002).
