# Saltcode

A **Pi Coding Agent package** that gives Pi spec-driven engineering discipline.

A frontier-API planning phase fires **once per sprint** and emits typed JSON
contracts on disk. A local-model execution phase then **loops at $0 API cost**,
building one task at a time against pre-written acceptance tests behind a static
gate, a test gate, an anti-gaming faithfulness gate, and a regression gate. Each
task that clears every gate becomes a **checkpoint** — a git commit you can resume
from or roll back to.

> **Status: scaffolding.** Task 0 (this structure) is done, along with the backend
> contracts, providers, harness primitives, LSP/AST MCP server and memory layer.
> The TypeScript extension is still a stub. `specs/tasks.md` is the ledger.

## Layout

```
package.json          # the Pi Package manifest (extensions · skills · prompts)
extensions/           # THE BRIDGE — TypeScript, talks to Pi              (Task 13)
agents/               # sub-agent definitions, one per Saltcode agent     (Task 7)
skills/               # the skills each sub-agent preloads                (Task 7)
prompts/              # reusable prompt templates                         (Task 13b)
mcp.json              # declares the LSP/AST server over stdio            (Task 4.6)
saltcode_backend/     # THE MUSCLE — Python, `pip install saltcode-backend`
specs/                # requirements.md · design.md · tasks.md  (canonical)
specs/legacy/         # the pre-v9 standalone spec set (archived)
docs/proposal/        # ← SOURCE OF TRUTH (v8 framework + v9 Pi addendum)
workspace/            # target projects Saltcode operates on
```

Responsibility splits three ways and the split is load-bearing: **Pi** owns the agent
loop, tool dispatch, session store and TUI; the **extension** owns orchestration,
routing, access control, state and UI; the **backend** owns everything that computes,
validates, executes code, or stores vectors. Put code in the wrong one and the
architecture quietly reverts to the pre-v9 standalone design.

## Install

Saltcode ships as two installable halves.

```bash
pip install saltcode-backend          # the Python backend
pi install npm:@laz/saltcode          # the Pi package (or: pi install -l .)
```

The two extensions bundled with the package — an MCP client and a sub-agent
runner — execute with full system permissions and **require a trust review before
install** (REQ-SEC-006). Task 13b.3 documents the verified install paths.

## Development

```bash
# backend
cd saltcode_backend && pip install -e ".[dev]"
ruff check . && pyright && pytest -q

# extension
npm install
npm run typecheck      # tsc --noEmit
npm run lint           # biome
```

CI runs both lanes on every commit.

## Read this before changing anything

`docs/proposal/Proposal_Pi_v8.md` and `Proposal_Pi_v9.md` are together the source of
truth (v9 amends v8). They flow down into `specs/requirements.md` — the binding
contract, where every line of code answers to a REQ id — then `specs/design.md`, then
`specs/tasks.md`. `specs/legacy/` is history, never authority.

The full rule is `.claude/rules/source-of-truth.md`.

## The one thing Saltcode does not promise

**Logical cross-task integration is not machine-guaranteed.** Type safety, constraint
legality, per-task correctness and integrated-suite health are all enforced. Semantic
composition across task boundaries — task 2 returns cents, task 5 passes dollars, both
typed `number`, no test covering the seam — is *your* check at Sprint Complete. The
regression gate narrows that gap. It does not close it.

## License

MIT — see [LICENSE](LICENSE).
