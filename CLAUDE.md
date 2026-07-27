# Saltcode

A **Pi Coding Agent package** that gives Pi spec-driven engineering discipline: a
frontier-API planning phase that fires **once per sprint** and emits typed JSON
contracts on disk, then a local-model execution phase that **loops at $0 API cost**,
building one task at a time against pre-written acceptance tests behind a static
gate, a test gate, an anti-gaming faithfulness gate, and a regression gate.

Ships as three parts: a **TypeScript extension** (the bridge), **skills + sub-agent
definitions + prompt templates**, and a **Python backend** (`pip install
saltcode-backend`) invoked through registered tools.

---

## THE PROPOSALS ARE THE SOURCE OF TRUTH — READ BEFORE ANY CODE

**Before starting any task, writing any code, or answering any design question,
read the relevant proposal section first.** Not the code, not your memory of the
project, not a summary. The proposal.

| Rank | Document | Role |
|------|----------|------|
| 1 | `docs/proposal/Proposal_Pi_v8.md` | Framework source of truth — agents, cadence, gates, cost, tradeoffs |
| 1 | `docs/proposal/Proposal_Pi_v9.md` | Pi re-hosting addendum — **amends v8**; where they conflict, v9 wins |
| 2 | `specs/requirements.md` | The binding contract. Every line of code is bound by a REQ id |
| 3 | `specs/design.md` | The blueprint — what to build, how it fits, which API sits where |
| 4 | `specs/tasks.md` | The ordered build. The shared human + machine progress ledger |

`specs/legacy/` is the **archived** pre-v9 standalone spec set. It is history, never
authority. Never implement from it and never cite it as a requirement.

The full rule, including what to do when the code and the proposal disagree:

@.claude/rules/source-of-truth.md

---

## Repository map

```
docs/proposal/     # ← SOURCE OF TRUTH (v8 framework + v9 Pi addendum)
docs/developer_handbook.md   # implementation cookbook (LSP JSON-RPC, LanceDB, sandbox recipes)
specs/             # requirements.md · design.md · tasks.md  (v9, canonical)
specs/legacy/      # pre-v9 standalone spec set (archived, non-authoritative)
package.json       # Pi Package manifest · extensions/ prompts/ skills/ agents/
extensions/        # THE BRIDGE — TypeScript (stub until Task 13)
saltcode_backend/  # THE MUSCLE — Python: saltcode/ + tests/ + its own pyproject.toml
workspace/         # target projects Saltcode operates on
.claude/           # rules, commands, skills  (this configuration)
.agents/           # the same assets in Antigravity's format — a separate tool's config
```

## Build state (2026-07-27)

- **Done:** legacy Tasks 0–5 — contracts + enforcer + diff validator, providers +
  embeddings + connectivity, harness primitives, LSP/AST MCP + broker, LanceDB
  memory. 43 tests green.
- **Done:** v9 **Task 0** — repo restructured as a Pi Package: backend moved to
  `saltcode_backend/` with its own `pyproject.toml`, `package.json` manifest,
  `extensions/saltcode.ts` stub, tsconfig + biome, two-lane CI, workspace convention.
  One leg of its gate is unverified: `pi install -l .` (Pi is not installed here).
- **Next:** Track A continues at Task 1 (contracts already exist — add the
  `saltcode.tools.*` entrypoints), and Track B starts at Task 7 (sub-agent
  definitions). Build order is at the bottom of `specs/tasks.md`.
- **Migration note:** under v9 several existing modules move out of the backend into
  the extension — `saltcode_backend/saltcode/harness/{dag,budget,thinking_gate,
  phase_gate,router,ctx_compactor,write_allowlist}.py` and `providers/deepseek.py`.
  Do not extend them; port them.

## Commands

Backend (from `saltcode_backend/`): `ruff check .` · `pyright` · `pytest -q`
Extension (from the repo root): `npm run typecheck` · `npm run lint`

The virtualenv lives at the repo root, so from `saltcode_backend/` use
`../.venv/bin/<tool>`. Both lanes gate CI.

## Rules

@.claude/rules/privacy-boundary.md

@.claude/rules/project-architecture.md

@.claude/rules/task-workflow.md
