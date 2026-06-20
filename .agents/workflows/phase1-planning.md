---
description: Run the Phase 1 Planning Quintuplet to generate a locked specification from a user goal.
---

# /phase1 — Planning Workflow

Generates a complete, validated specification from a user goal. Fires ONCE per sprint.

```
[Goal Input] → Scout → Architect → Planner → Test Intent → Evaluator → [Spec Lock]
                          ▲                                     │
                          └──── design_gap / plan_gap ──────────┘
```

## Step 1: Cache Ladder (check before firing Phase 1)
Before running any agent, check if this goal is already cached:
1. Compute scope fingerprint: use `--scope` CLI arg, or run a lightweight `outline` MCP call on the repo root.
2. **Exact cache**: `sha256(normalized_goal + scope_fingerprint)` → look up in `.saltcode/cache/`. Hit → reuse `tasks.json`, SKIP Phase 1 entirely.
3. **Semantic cache**: embed(goal + scope), cosine ≥ threshold. Compute PCD (Prior Cluster Density). High PCD → cheap Architect confirmation. Low PCD → skeptical. Confirmed hit → reuse.
4. Both miss → proceed with Phase 1 below.

## Step 2: Scout
- Invoke the `saltcode-scout` skill.
- Produce `.saltcode/context_report.json`.

## Step 3: Architect
- Invoke the `saltcode-architect` skill.
- Input: user goal + context_report.
- Produce `.saltcode/design.md` with `## HARD CONSTRAINTS` block (verbatim carry-through).
- **Verify**: every `context_report.constraints[]` and `anti_patterns[]` string appears in the block.

## Step 4: Planner
- Invoke the `saltcode-planner` skill.
- Input: design.md ONLY.
- Produce `.saltcode/tasks.json` (acyclic DAG).

## Step 5: Test Intent
- Invoke the `saltcode-test-intent` skill.
- Input: tasks.json + project config (language, test_framework, test_runner_cmd).
- Produce one `tests/task_{id}_spec.*` per task.

## Step 6: Evaluator
- Invoke the `saltcode-evaluator` skill.
- Run the four checks (traceability, coverage, preservation, compliance).
- If `status == "pass"` → proceed to Spec Lock.
- If gaps found → route per gap type:
  - `design_gap` → go to Step 3 (Architect re-emits design.md, then Step 4 re-runs). **Max 2 Architect loops.**
  - `plan_gap` or `constraint_violation` → go to Step 4 (Planner re-emits tasks.json). **Max 3 Planner loops.**
  - Loops exceeded → **FLAG HUMAN** with `evaluator_report.json`. Do not continue.

## Step 7: Spec Lock
- Store spec hash to the Spec Cache: `sha256(normalized_goal + scope_fingerprint) → tasks.json`.
- Phase-Gate fires automatically → proceed to `/phase2`.
