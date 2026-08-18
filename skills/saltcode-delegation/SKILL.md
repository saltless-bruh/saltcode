---
name: saltcode-delegation
description: How to spawn Saltcode's sub-agents — which agent owns which job, why dependent work runs serially, and how to write a task prompt for a context that starts empty.
---

# Delegating to Saltcode's sub-agents

Each Saltcode agent runs in its own context window with its own model, thinking level and
tool allowlist. That isolation is what makes "one task per context, no carryover" true by
construction (REQ-BLD-001, REQ-ORC-006) rather than by pruning a shared session.

It also means **a spawned agent knows nothing you have not written down.**

## Which agent owns what

| Need | Agent | Gets | Produces |
|---|---|---|---|
| What does this repo look like? | **Scout** | file tree, LSP symbols | `context_report.json` |
| What should we build? | **Architect** | goal + `context_report.json` | `design.md` |
| In what pieces? | **Planner** | `design.md` only | `tasks.json` |
| How will we know it works? | **Test Intent** | `tasks.json` + project config | `tests/task_{id}_spec.*` |
| Is the plan safe to build? | **Evaluator** | all three artifacts | `evaluator_report.json` |
| Implement one task | **Builder** | one task + scoped bodies + its spec | a unified diff |

**The Auditor is not on this list.** Its judgment is the backend `saltcode_stability` tool,
run N=3 times in one process — not a spawned agent (design §5.6a). Spawning "an auditor"
means you have misread the pipeline.

Pick by the artifact you need. If no agent's output is the thing you want, the work is
probably the extension's or the human's, not a delegation.

## Serial when dependent — and most of Phase 1 is

Phase 1 is a chain: Scout → Architect → Planner → Test Intent → Evaluator. Each consumes
the previous artifact, so they run **one at a time, awaiting and validating each result
before the next spawn**.

Two consequences worth being explicit about:

- **The command handler owns the ordering, not a model.** `/sprint` sequences the spawns.
  Letting an LLM decide what to run next reintroduces exactly the accumulating,
  self-directed session the isolation was meant to remove.
- **Validate before advancing.** Each artifact is typed JSON checked by
  `saltcode_validate_contract`. Spawning the Planner on a `design.md` that failed
  validation wastes a full agent run to produce a plan built on a malformed input.

Parallelism is only correct where there is genuinely no dependency — and in Phase 1 there
essentially isn't any. Phase 2's Builder is serial for a different reason: one Saltnitor
model is resident at a time (REQ-MOD-005).

## Writing a prompt for an empty context

This is where delegation actually fails. The sub-agent has no sibling history, no memory of
your reasoning, and no view of the conversation that led here (REQ-EXT-012 AC1). Anything
it needs is either in the prompt or read from disk by a tool it holds.

A complete task prompt states:

1. **The goal, in full.** Not "continue with the auth work" — that references a
   conversation the agent never saw.
2. **Where its inputs are**, as paths. It reads them itself; do not paste file contents in.
3. **What artifact to produce, and where.**
4. **Any constraint that is not already in its skill.** The agent's own contract is
   preloaded — do not restate it. Do state anything sprint-specific.
5. **What to do when blocked**, if it differs from its default (stop and report).

Two failure modes, both common:

- **Under-specified** — "fix the failing test." Which test, in what repo state, against
  which task? The agent guesses, and a guess from an empty context is nearly random.
- **Over-specified** — pasting the design, the context report and three file bodies into
  the prompt "so it has everything." That defeats the isolation, blows the context budget,
  and for API-routed agents can put raw source into a network payload. Give paths, not
  contents.

The test for a prompt: could a competent stranger, handed only this text and the tools
named, produce the artifact? If not, it is incomplete — and the agent cannot ask you.

## After a spawn

Check the artifact, not the agent's narration. An agent that reports success and emitted a
malformed contract has failed; the validator is what says so. Route the result according to
the pipeline — an Evaluator `gaps` verdict goes back to the Architect or Planner under
their loop caps (2 and 3), and exceeding a cap is FLAG HUMAN, never another loop.

## Satisfies

REQ-EXT-016 AC3 · REQ-EXT-012 (isolated context per agent) · REQ-ORC-002 (serial Phase-1
spawning in dependency order) · REQ-EVL-002/003 (loop caps).
