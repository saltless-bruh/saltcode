---
trigger: always_on
---

# Saltcode Project Architecture Rules

All agents and tools executing in this workspace must adhere to the structural boundaries and layer definitions of Saltcode.

## 1. Cost & Capability Division (The Two Phases)

The framework splits software execution into two distinct stages:

```
                  ┌────────────────────────────────────────┐
                  │   PHASE 1: PLANNING QUINTUPLET (API)   │
                  │   Frontier model swarming (deepseek)   │
                  └───────────────────┬────────────────────┘
                                      │
                         [ Typed JSON on disk spec ]
                                      │
                  ┌───────────────────v────────────────────┐
                  │   PHASE 2: EXECUTION PAIR (LOCAL $0)   │
                  │   Local MoE task building & auditing   │
                  └────────────────────────────────────────┘
```

- **Phase 1 (Planning)**: Swarms deepseek/Pro online or local Tier B offline. Runs exactly **once** per sprint to generate a locked specification under `.saltcode/` (the Local Code-Wiki).
- **Phase 2 (Execution)**: Loops local Tier A (`Qwen3.5-9B`) or Tier B (`Qwen3.6-35B`) models at $0 API cost to write code and audit results.

## 2. Roster and Contract Outputs

All agent artifacts are represented as typed JSON files:

1. **Scout** (Flash): Scans file tree + symbols → Emits [context_report.json](file:///home/laz/Documents/AI-Driven_Coding/specs/design.md#L129-L138). Must never read raw file bodies.
2. **Architect** (Pro/Flash): Translates goals → Emits [design.md](file:///home/laz/Documents/AI-Driven_Coding/specs/design.md#L140-L148). Must propagate constraints verbatim into `## HARD CONSTRAINTS`.
3. **Planner** (Flash): Outlines dependencies → Emits [tasks.json](file:///home/laz/Documents/AI-Driven_Coding/specs/design.md#L150-L159). Must read only `design.md` (no context report).
4. **Test Intent** (Flash): Outputs tests → Emits [tests/task\_{id}\_spec.\*](file:///home/laz/Documents/AI-Driven_Coding/specs/design.md#L162). Written before code. Immutable to Builder.
5. **Evaluator** (Flash/Pro): Validates specification → Emits [evaluator_report.json](file:///home/laz/Documents/AI-Driven_Coding/specs/design.md#L164-L177). Loops up to $A \le 2$ or $P \le 3$ times before halting.
6. **Builder** (Local A/B): Implements coding task → Emits unified code diff. Has restricted scoped reads to `task.files_affected`. Protected from editing `tests/**`.
7. **Auditor** (Local A/B/Flash): Validates changes → Emits [audit_result.json](file:///home/laz/Documents/AI-Driven_Coding/specs/design.md#L179-L195). Computes stability scores over $N=3$ passes.

## 3. Privacy and Local Broker Guardrails

- **AST/Symbol Only**: Scout and online planning agents are strictly blocked from loading full file contents. They must use the LSP/AST MCP tools.
- **Write Scope Allowlist**: The Builder is strictly blocked from making edits under `tests/**`.
- **Sandbox Isolation**: Builder code is applied to a git worktree or temporary directory sandbox. Static syntax checks (tsc, pylint, cargo check, go vet) and test suites are run within the sandbox. The live directory is modified only after the Auditor issues a `pass`.
