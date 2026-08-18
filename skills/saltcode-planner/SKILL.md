---
name: saltcode-planner
description: Guides the Planner agent to derive tasks.json from design.md alone, with an acyclic dependency graph, exact files_affected, and testable acceptance criteria.
---

# Saltcode Planner Skill

Use this when running the Planner phase of a Saltcode sprint (Phase 1, Step 3).

You turn a design into an ordered, buildable task list. One input, one output, no
improvisation.

## Input — `.saltcode/design.md`, and nothing else

**You do not read `context_report.json`** (REQ-PLN-001). This is deliberate and it is
checked. The constraints reach you through the design's `## HARD CONSTRAINTS` block, which
the Architect was required to fill verbatim. Reading the Scout's report as well would let
you plan against facts the design never accounted for — the design would stop being the
single thing the plan is traceable to, and the Evaluator's traceability check would be
measuring nothing.

If `design.md` is missing something you need, that is a **design gap**. Report it; do not
go looking for the answer elsewhere.

## Output — `.saltcode/tasks.json`

```json
{
  "schema_version": "1",
  "tasks": [
    {
      "id": "T1",
      "description": "One sentence: what this task makes true.",
      "files_affected": ["src/auth/session.py"],
      "acceptance_criteria": ["Specific, checkable statements."],
      "depends_on": [],
      "complexity": "low"
    }
  ]
}
```

- `id` — short and stable. Test Intent writes `tests/task_{id}_spec.*` from it.
- `files_affected` — **exact paths**. This is not documentation: it is the Builder's read
  scope and its write scope, enforced by the broker and again at `tool_call`. A path
  missing here is a file the Builder cannot open; a stray path is scope handed out for no
  reason.
- `acceptance_criteria` — what Test Intent turns into assertions. "Works correctly" is not
  a criterion. "Returns 401 when the token is expired" is.
- `depends_on` — task ids that must land first. **The graph must be acyclic**
  (REQ-PLN-002 AC1). A cycle is rejected.
- `complexity` — `low` | `med` | `high`. This is a routing decision, not a vibe: `high`
  starts the Builder on Tier B and gives it all three attempts there.

## How to size a task

One task is one coherent change a Builder can make in a single context with no carryover
(REQ-BLD-001). Concretely:

- It touches a small, named set of files.
- Its acceptance criteria can be tested without the *next* task existing.
- It leaves the tree in a state where the full suite can run — the regression gate runs
  after every task, so a task that only makes sense half-finished will fail it.

Too big is the common error. If a task's `files_affected` spans four unrelated modules, or
its criteria need "and then also", split it and add a `depends_on` edge.

Too small is a real error too: a task that changes one line and cannot be tested on its
own spends a full gate sequence — diff check, sandbox, static gate, tests, three Auditor
passes, regression — to prove almost nothing.

## Ordering

`depends_on` encodes what is *logically* required, not a preferred sequence. If two tasks
are genuinely independent, leave the edge out and let the DAG say so. Inventing edges to
express taste serialises work that did not need to be.

Order the array topologically as a courtesy, but correctness lives in `depends_on` — the
extension resolves the order from the graph.

## Escalation

Stop and report, rather than guessing, when:

- `design.md` does not say which component owns a behaviour you must assign to a file;
- two sections of the design contradict each other;
- a `## HARD CONSTRAINTS` line makes a piece of the design unbuildable as written.

All three are **design gaps** and route back to the Architect (Evaluator loop cap: 2).
Emitting a plausible-looking plan built on a guess is the failure this routing exists to
prevent.

## Common mistakes to avoid

- Reading `context_report.json`. You do not get it (REQ-PLN-001).
- Vague `files_affected` — a directory, a glob, or a guess. It is an enforcement boundary.
- Acceptance criteria that restate the description instead of being checkable.
- A dependency cycle. Rejected outright (REQ-PLN-002 AC1).
- Writing test files. That is Test Intent's job, and `tests/**` is write-blocked here.
- Inventing work the design does not call for, however obviously useful it seems.
