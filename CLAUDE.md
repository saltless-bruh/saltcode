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
| 5 | `specs/known_gaps.md` | What the build knows is **not right yet** — read before every task, updated after every task |

`specs/legacy/` is the **archived** pre-v9 standalone spec set. It is history, never
authority. Never implement from it and never cite it as a requirement.

The full rule, including what to do when the code and the proposal disagree:

@.claude/rules/source-of-truth.md

---

## FOLLOW THE TASK LIST — STOP AND ASK, NEVER WORK AROUND

**Execute `specs/tasks.md` as written.** Do not reorder, skip, merge, "improve", or
silently substitute a different approach for any step.

**If a task is wrong** — it contradicts the specs or the proposal, contains an error
or flaw, references something that does not exist, or is too ambiguous to implement
one way — **stop before starting that work**. State the problem, name the conflicting
REQ id or design section, propose your fix, and **ask for the human's opinion.**

**If something blocks you** — a missing tool, an uninstalled dependency, an absent
credential, a permission or trust prompt, an unreachable service — **stop at the
block.** Do not substitute a weaker tool, skip the verification, stub a result,
disable a check, guess a value, or quietly narrow the task to the part that works.
Say what is blocked, say exactly what you need to get past it, and **ask.**

Finish everything that does not depend on the answer first. Never report a task done
when a leg of its **Done when** gate was skipped or unverified — name the leg and why.

@.claude/rules/stop-and-ask.md

---

## Repository map

```
docs/proposal/     # ← SOURCE OF TRUTH (v8 framework + v9 Pi addendum)
docs/developer_handbook.md   # implementation cookbook (LSP JSON-RPC, LanceDB, sandbox recipes)
docs/entrypoints.md          # the backend CLI contract the extension is written against (Task 7b)
specs/             # requirements.md · design.md · tasks.md · known_gaps.md  (v9, canonical)
specs/legacy/      # pre-v9 standalone spec set (archived, non-authoritative)
package.json       # Pi Package manifest · extensions/ prompts/ skills/ agents/
extensions/        # THE BRIDGE — TypeScript (stub until Task 13)
saltcode_backend/  # THE MUSCLE — Python: saltcode/ + tests/ + its own pyproject.toml
workspace/         # target projects Saltcode operates on
.claude/           # rules, commands, skills  (this configuration)
.agents/           # the same assets in Antigravity's format — a separate tool's config
```

## Build state (2026-08-11)

- **Track A is built out, with one leg still open.** v9 **Tasks 0–3, 5, 9, 10, 12, 14b,
  7b** are done and ticked. **Task 4 is not** — its box stays unticked because 4.6 is
  deferred to Task 20.1 (see G-011), so Track A carries one open integration leg.
  Task 0 (Pi Package scaffold, two-lane CI, verified on Pi 0.82.1) · Task 1 (typed
  contracts + Output-Length Enforcer) · Task 2 (Saltnitor client, local embeddings,
  connectivity probe, the source-payload privacy guard) · Task 3 (disposable sandbox +
  bubblewrap container, command allowlist, audit log, scope probe) · Task 4 (LSP/AST MCP
  server + scoped-read broker — built, **box still unticked**, 4.6 deferred to Task 20.1,
  see G-011) · Task 5 (LanceDB caches, PCD semantic tier, frozen notes, per-threshold
  calibration flags) · Task 9 (static gate, task-spec runner, diff validator) · Task 10
  (N-pass Auditor stability, anti-gaming heuristics, `apply_live`, scoped read) ·
  Task 12 (Spec Compactor) · Task 14b (measured-then-fixed threshold calibration) ·
  Task 7b (the thirteen CLI entrypoints + `docs/entrypoints.md` + the conformance suite).
  **811 backend tests pass locally, 1 skips** (G-013's honest `limits_enforced=false` on a
  systemd-less host). `ruff` + `pyright --strict` clean. Track A now also carries the
  **fourteenth entrypoint**, `contained_exec` (added by Task 13.3 for G-029).
- **Track B is under way.** **Task 7** (sub-agent definitions) is done except 7.2b/7.3/7.3b
  — the mechanism is verified through `pi-subagents@0.40.0`'s own loader, the behaviour
  needs a live model (G-028). **Task 13** (the extension) is built: the factory is
  `extensions/saltcode.ts` and the decisions live as pure functions in
  `extensions/saltcode/*.ts`, so the privacy, write-scope, budget and routing rules are
  provable without a running Pi. **13.1–13.7 and 13.9 are ticked; 13.8, 13.10 and Task 13's
  box are not** — both open steps are implemented and unit-tested, and both wait on a live
  model (G-028), which is also the only thing keeping Task 13's box unticked. Extension
  lane: `tsc --noEmit` and `biome` clean, **93 tests** (`npm run test:agents`).
- **Both open questions were answered (maintainer, 2026-08-11) and are closed.**
  **G-029** — `saltcode.tools.contained_exec` now exposes the container as a CLI
  entrypoint, so the built-in `write`/`edit`/`bash` overrides route through it and
  REQ-SEC-007 AC1 is met; the `bash rm -rf` Done-when leg is proven by a test that checks
  the host file survives, not just the exit code. **G-030** — Task 11.2 owns Saltnitor
  provider registration; the clause is struck from 13.1.
- **Next:** the rest of Track B — **{6, 8, 11}** build on the extension core, then 13b,
  then 18. Build order is at the bottom of `specs/tasks.md`.
- **Deployment (confirmed 2026-07-28):** everything — Saltcode, Saltnitor, Docker,
  the embedding endpoint — runs on **one machine, this one**. The privacy boundary
  is therefore the box: only loopback counts as local, and a LAN address is off-box.
- **Open gaps:** see `specs/known_gaps.md` — read it before starting a task.
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
