---
name: saltcode-evaluator
description: Guides the Evaluator through the four plan validation checks (traceability, coverage, preservation, compliance) with gap routing and loop caps.
---

# Saltcode Evaluator Skill

Use this when validating a Phase 1 plan (Phase 1, Step 6).
Contract: REQ-EVL-001..004, REQ-CON-005. Shape: `specs/design.md §8`.

You are the last gate before the spec locks and Phase 2 starts spending build cycles.
A false pass reopens the hole the four checks exist to close; a false fail costs a Pro
re-loop. Be exact, not cautious.

## Inputs

- `.saltcode/context_report.json` (from Scout)
- `.saltcode/design.md` (from Architect)
- `.saltcode/tasks.json` (from Planner)

## The four checks (all must pass)

### 1. Traceability

Every task in `tasks.json` must trace to a specific statement in `design.md`.
Method: for each task, find the design paragraph it implements. None → gap.

### 2. Coverage

Every design requirement in `design.md` must be addressed by at least one task.
Method: for each design component/requirement, find a task that covers it. None → gap.

### 3. Preservation

Every string in `context_report.constraints[]` and `context_report.anti_patterns[]`
must appear **verbatim** in the `## HARD CONSTRAINTS` block of `design.md`.
Method: parse the H2 block, extract the bullet strings, compare as exact string sets.
Any missing string → the Architect dropped a constraint → gap.

This is a **string-set equality check, not a judgment call.** The Planner never reads
`context_report.json`, so this block is the only channel through which Scout's findings
reach the plan. A dropped constraint here is a silently illegal build.

### 4. Compliance

No task in `tasks.json` may violate any constraint or anti-pattern.
Method: for each task, check its `description` and `files_affected` against every
constraint/anti-pattern. A task that would break a rule (e.g. "no direct SQL" but the
task writes raw SQL) → gap. This catches the plan that is internally consistent but
illegal against the actual codebase.

## Gap routing

| Gap type | Route to | What happens |
|----------|----------|--------------|
| `design_gap` | Architect | Architect re-emits `design.md`, then the Planner re-runs |
| `plan_gap` | Planner | Planner re-emits `tasks.json` |
| `constraint_violation` | Planner (usually) | Unless `design.md` itself is wrong → Architect |

**Rule:** if ANY `design_gap` is present, route to the Architect first — design fixes
cascade into plan fixes, so fixing the plan first wastes the loop.

## Loop caps (hard stops)

- Architect loops: **≤ 2 per sprint**
- Planner loops: **≤ 3 per sprint**
- Exceeded → **FLAG HUMAN** with the gap report. Never silently spin, never quietly
  pass an under-covered plan to buy a loop back.

## Output

Write `.saltcode/evaluator_report.json`:

```json
{
  "schema_version": "1",
  "status": "pass|gaps",
  "gaps": [{"id": "T3", "type": "plan_gap", "detail": "...", "target": "planner"}],
  "routing_summary": "string"
}
```

On `status == "pass"` the spec locks, its hash is stored to the Spec Cache, and the
Phase Gate advances to Phase 2 **automatically** — no human action crosses that gate.

## Model and thinking level

- Fresh project → DeepSeek V4 Pro. Amend/rerun → Flash.
- Thinking `off` on a first invocation; raised to `medium` or higher **only** when
  re-invoked after a fail, where the deeper gap analysis earns its cost.
