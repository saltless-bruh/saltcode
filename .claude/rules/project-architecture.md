# Saltcode Project Architecture Rules

Structural boundaries every agent and tool in this workspace must respect.
Blueprint: `specs/design.md`. Contract: `specs/requirements.md`.

## 1. Three parts, one product (v9)

Saltcode is a **Pi Package**. Responsibility splits three ways — put code in the
right one, or the architecture silently reverts to the standalone design.

```
┌─────────────────────────────────────────────────────────────┐
│ Pi (the host)                                               │
│   agent loop · tool dispatch · session store · TUI ·        │
│   provider auth · conversation compaction                   │
├─────────────────────────────────────────────────────────────┤
│ extensions/saltcode.ts — THE BRIDGE (TypeScript)            │
│   registerProvider · setModel / setThinkingLevel ·          │
│   registerTool (saltcode_*) · on("tool_call") access ctrl · │
│   built-in write/edit/bash OVERRIDE into the container ·    │
│   appendEntry state + budget · before_agent_start prefix ·  │
│   session_before_compact · registerCommand (/sprint …) ·    │
│   sub-agent spawning · ctx.ui widgets · the Phase-2 loop    │
├─────────────────────────────────────────────────────────────┤
│ saltcode_backend/ — THE MUSCLE (Python, pip-installed)      │
│   contracts + enforcer · sandbox + container · static gate ·│
│   test runner · LSP/AST MCP server · LanceDB caches ·       │
│   N-pass stability · regression + checkpoint + rollback     │
│   exposed as saltcode.tools.* CLI entrypoints + a daemon    │
└─────────────────────────────────────────────────────────────┘
        + agents/*.md (sub-agent defs) · skills/ · prompts/ · mcp.json
```

**Placement test.** Does it orchestrate, route, gate a tool call, hold session state,
or touch the UI? → **extension**. Does it compute, validate, execute code, or store
vectors? → **backend**. Does it tell a model how to behave? → **skill / sub-agent
definition**. If you cannot answer, re-read `specs/design.md §5`.

Two capabilities Pi does not ship are taken as **bundled dependencies**, not forks:
an MCP client extension (`pi-mcp-extension`) and a sub-agent extension
(`pi-subagents`). Depend on the documented capability contract, never on their
internals (REQ-EXT-015).

## 2. Cost & capability division (the two phases)

```
        ┌──────────────────────────────────────────┐
        │  PHASE 1 — PLANNING QUINTUPLET (API)     │
        │  fires exactly ONCE per sprint            │
        └────────────────────┬─────────────────────┘
                  [ typed JSON on disk — .saltcode/ ]
        ┌────────────────────v─────────────────────┐
        │  PHASE 2 — EXECUTION PAIR (LOCAL, $0)    │
        │  loops per task, gates, checkpoints       │
        └──────────────────────────────────────────┘
```

- **Phase 1**: DeepSeek Pro/Flash online, Saltnitor Tier B offline. One fire per
  sprint, enforced by the `/sprint` handler + sprint state (REQ-ORC-001).
- **Phase 2**: Saltnitor Tier A (`Qwen3.5-9B`) → Tier B (`Qwen3.6-35B-A3B`) at $0 API.
- Between them sits the **provider-agnostic contract boundary**: swapping the Phase-1
  provider must require no backend or gate edits — only `pi.registerProvider` config
  (REQ-GLB-001 AC2).

## 3. Roster and contract outputs (`specs/design.md §7`, §9)

Each agent runs in an **isolated sub-agent context** with its own model, thinking
level, and tool allowlist, and its skill **preloaded** into the prompt (Pi only
auto-injects skills for agents holding `read`, and these agents are locked down).

1. **Scout** (Flash / `off`) — file tree + LSP symbols → `context_report.json`.
   Never reads raw file bodies.
2. **Architect** (Pro→Flash / `high`) — goal + context_report → `design.md`.
   Must mirror every `constraint` and `anti_pattern` **verbatim** into
   `## HARD CONSTRAINTS`. Forbidden from writing a task list.
3. **Planner** (Flash / `off`) — `design.md` only → `tasks.json`, acyclic
   `depends_on`. Never reads `context_report.json`.
4. **Test Intent** (Flash / `off`) — `tasks.json` + project config → one
   `tests/task_{id}_spec.*` per task, before code, immutable to the Builder.
5. **Evaluator** (Flash→Pro) — the four checks (traceability, coverage, preservation,
   compliance) → `evaluator_report.json`. Loop caps Architect ≤2, Planner ≤3, then
   FLAG HUMAN.
6. **Builder** (Tier A→B) — one task + AST + scoped bodies + spec → unified diff.
   Scoped to `task.files_affected`. May not touch `tests/**`.
7. **Auditor** (Tier A→B→Flash) — diff + criteria + clean static report + test results
   + spec content → `audit_result.json`. Confidence is **measured** over N=3 passes
   (`stability_score = 1.0 - verdict_changes/(N-1)`), never self-reported. Not a
   spawned sub-agent — its judgment is the backend `saltcode_stability` tool.
8. **Spec Compactor** (Flash / `off`, every 5 sprints) — never strips an active
   HARD CONSTRAINT.

All artifacts are typed JSON on disk under `.saltcode/`, validated by pydantic and
the Output-Length Enforcer. Every contract carries `schema_version`; readers reject
unknown major versions.

## 4. Guardrails

- **AST/symbol only** — Scout and every API-routed agent are blocked from full file
  bodies. See `.claude/rules/privacy-boundary.md`.
- **Write-scope allowlist** — no agent creates, edits, or deletes anything under
  `tests/**`. Enforced at `tool_call` and again in the backend.
- **Command allowlist** — only `pytest`, `jest`, `cargo test`, `go test`, `pyright`,
  `ruff`, `tsc`, `eslint`, `cargo check`, `cargo clippy`, `go build`, `go vet`,
  `git apply` execute inside the container. Everything else is refused and logged.
- **Security containment** — every subprocess that runs Builder-generated code runs
  inside bubblewrap (→ Docker → firejail): read-only host fs, no network, no `$HOME`
  or credentials, isolated PID namespace, memory/CPU/time limits, auto-cleanup. **If
  no containment backend exists, Phase 2 refuses to run** — there is no uncontained
  fallback. Pi's built-in `write`/`edit`/`bash` are overridden so interactive use is
  contained too (REQ-SEC-001..007).
- **Sandbox isolation** — diffs land in a disposable git worktree. The live tree is
  modified only after the Auditor returns `pass`.
- **Checkpoint discipline** — after `apply_live` the tree stays **uncommitted** until
  the regression gate passes; only then does the commit + `saltcode:checkpoint`
  snapshot happen. The loop never advances past an un-checkpointed task, and never
  pushes unless `auto_push = true`.
- **Audit log** — every executed command appends to `.saltcode/audit_log.jsonl`
  (command, cwd, container id, exit code, timestamp, SHA-256 of stdout+stderr).

## 5. The permanent non-goal (Trade B)

**Logical cross-task integration is not machine-guaranteed.** Type safety (static
gate), constraint legality (Evaluator), per-task correctness (spec tests + Auditor)
and integrated-suite health (regression gate) are all enforced. *Semantic composition
across tasks* — task 2 returns cents, task 5 passes dollars, both typed `number` —
is the **human's** check. Surface it at Sprint Complete; never claim to verify it.
The regression gate narrows this gap. It does not close it.
