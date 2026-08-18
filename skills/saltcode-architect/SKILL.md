---
name: saltcode-architect
description: Guides the Architect agent to turn a goal plus context_report.json into design.md, mirroring every constraint and anti-pattern verbatim into a HARD CONSTRAINTS block, and never writing a task list.
---

# Saltcode Architect Skill

Use this when running the Architect phase of a Saltcode sprint (Phase 1, Step 2).

You own **one** job: turn the goal and the Scout's findings into a design. You do not
plan the work — that is the Planner's job, and taking it is the single most common way
this agent goes wrong.

## Inputs

- `user_goal` — the sprint goal, as the human wrote it.
- `.saltcode/context_report.json` — the Scout's output.

You receive **no raw source**. If you find yourself wanting a file body, the design is
being written at the wrong altitude: describe the shape the code must take, not the code.

## Output — `.saltcode/design.md`

Markdown, with one machine-parseable section:

```markdown
# Design: <goal restated in one line>

## Components
What is built or changed, and what each piece is responsible for.

## Data Flow
How the pieces talk. Name the types crossing each boundary.

## Non-Goals
What this sprint deliberately does not do. Be specific — this is what stops scope creep
in the Planner.

## HARD CONSTRAINTS
- <every context_report.constraints[*] string, verbatim>
- <every context_report.anti_patterns[*] string, verbatim>
```

## The two hard rules

### 1. Never write a task list (REQ-ARC-001)

No `## Tasks`, no numbered build order, no "step 1 / step 2", no `tasks.json`. The
Planner reads `design.md` and derives the task list from it; a task list here means the
plan is written twice by two agents that cannot see each other, and they will disagree.

`saltcode_validate_contract` rejects Architect output containing a task list (REQ-ARC-001
AC1). This is checked, not trusted.

### 2. Carry every constraint through **verbatim** (REQ-ARC-002)

Every string in `context_report.constraints` and `context_report.anti_patterns` appears in
`## HARD CONSTRAINTS` **character for character**. Order does not matter. Nothing else
about them may change.

Do **not**:
- reword, tighten, or "clarify" a constraint;
- merge two constraints that look similar;
- drop one you judge irrelevant to this goal;
- add a constraint the Scout did not report.

A string-set equality check between `constraints ∪ anti_patterns` and the parsed block
must hold (REQ-ARC-002 AC1). A paraphrased constraint fails that check — and worse, a
paraphrase that *passes* review is a constraint silently weakened for every sprint that
follows, because this block is what the Spec Compactor is forbidden to strip and what
every later agent treats as binding.

If a constraint seems wrong, say so in **Non-Goals** or in prose. Still copy it verbatim.

## How to write the design body

- Start from the goal, not from the codebase. The `context_report` tells you what exists;
  the goal tells you what should.
- Prefer naming existing components (`relevant_files`, `existing_patterns`) over inventing
  parallel ones. A design that ignores the patterns the Scout found produces a plan that
  fights the codebase.
- Make every boundary explicit enough that the Planner can name `files_affected` from it.
  If a section leaves the Planner guessing which file changes, it is too vague.
- State the types crossing each boundary. Trade B is real: nothing downstream verifies
  that task 2's "cents" matches task 5's "dollars", so the design is where that agreement
  is written down.

## Escalation

If the goal and the `context_report` contradict each other — the goal asks for something a
constraint forbids — **stop and report it**. Do not resolve it by quietly dropping the
constraint or by narrowing the goal. Emit no `design.md` and say what conflicts. An
invented resolution is worse than a blocked sprint, because it looks like a plan.

## Common mistakes to avoid

- Writing a task list. (Rejected — REQ-ARC-001.)
- Paraphrasing a constraint into something "cleaner". (Rejected — REQ-ARC-002.)
- Leaving `## HARD CONSTRAINTS` out because the Scout found none. Emit the heading with an
  empty list; a missing block and an empty block are different facts.
- Designing at file-and-function granularity. That is the Planner's and Builder's level.
- Asking for file bodies. You do not get them, by design.
