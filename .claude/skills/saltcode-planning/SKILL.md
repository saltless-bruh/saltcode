---
name: saltcode-planning
description: Writes a structured implementation plan with small steps, exact files, and verification commands before executing any non-trivial task from specs/tasks.md.
---

# Saltcode Planning Skill

Use this when preparing to implement any non-trivial task in **this repository** —
i.e. when *building Saltcode itself*, not when running a Saltcode sprint. (The
in-sprint planner is the `saltcode-planner` agent, which produces `tasks.json` from
`design.md`.)

## 1. Before you plan

Per `.claude/rules/source-of-truth.md`, read in this order and do not skip:

1. The task in `specs/tasks.md` — sub-steps, `deps`, **Satisfies**, **Done when**.
2. Every REQ id under **Satisfies** in `specs/requirements.md`.
3. The `specs/design.md` section the task points at.
4. `docs/proposal/` if any of the above is thin, ambiguous, or silent.

Then decide which layer the work belongs to — extension, backend, or skill — and say
so in the plan. Misplacing it is the single most expensive mistake in a v9 task.

## 2. Rules

- Break the work into chunks of 2–10 minutes.
- Specify exact file paths to modify or create.
- Give the exact verification command for each step (`.venv/bin/python -m pytest -q`,
  `ruff check .`, `pyright`, `tsc --noEmit`).
- The plan's final step is always the task's **Done when** gate, quoted from
  `specs/tasks.md`, plus ticking its checkbox.
- State rollbacks and risk mitigations.
- If several approaches could work, stop and ask — do not guess.

## 3. Template

```markdown
### Goal
[The task id and what it delivers]

### Binding requirements
[REQ ids from "Satisfies", with the acceptance criteria that define "works"]

### Layer
[extension | backend | skill] — because [placement test from project-architecture.md]

### Assumptions
[Preconditions, dependencies, anything the specs left open]

### Plan
1. [Step name]
   - Files: `path/to/file`
   - Change: [the edit]
   - Verify: [exact command]

### Done when
[Quoted verbatim from specs/tasks.md]

### Risks & mitigations
- Risk: … / Mitigation: …

### Rollback plan
- Commands to revert.
```
