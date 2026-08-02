---
name: saltcode-evaluator
description: Guides the Evaluator through the four plan validation checks (traceability, coverage, preservation, compliance) with gap routing and loop caps.
---

# Saltcode Evaluator Skill

Use this when validating a Phase 1 plan (Step 5).

## Inputs
- `.saltcode/context_report.json` (from Scout)
- `.saltcode/design.md` (from Architect)
- `.saltcode/tasks.json` (from Planner)

## The Four Checks (all must pass)

### 1. Traceability
Every task in `tasks.json` must trace to a specific statement in `design.md`.
- Method: for each task, find the design paragraph it implements. If none → gap.

### 2. Coverage
Every design requirement in `design.md` must be addressed by at least one task.
- Method: for each design component/requirement, find a task that covers it. If none → gap.

### 3. Preservation
Every string in `context_report.constraints[]` and `context_report.anti_patterns[]` must appear **verbatim** in the `## HARD CONSTRAINTS` block of `design.md`.
- Method: parse the H2 block, extract the bullet strings, compare as exact string sets. If any context_report string is missing → the Architect dropped a constraint → gap.

### 4. Compliance
No task in `tasks.json` may violate any constraint or anti-pattern.
- Method: for each task, check its `description` and `files_affected` against every constraint/anti-pattern. If a task would break a rule (e.g. "no direct SQL" but the task writes raw SQL) → gap.

## Gap Routing

| Gap Type | Route To | What Happens |
|----------|----------|-------------|
| `design_gap` | Architect | Architect re-emits design.md, then Planner re-runs |
| `plan_gap` | Planner | Planner re-emits tasks.json |
| `constraint_violation` | Planner (usually) | Unless design.md itself is wrong → Architect |

**Rule:** if ANY `design_gap` is present, route to the Architect first (design fixes may cascade to plan fixes).

## Loop Caps (hard stops)
- Architect loops: **≤ 2 per sprint**
- Planner loops: **≤ 3 per sprint**
- Exceeded → **FLAG HUMAN** with the gap report. Do not silently spin.

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

## Thinking Mode
- Fresh invocation: thinking OFF.
- Re-invoked after a fail: thinking ON (deeper reasoning on gap analysis).
