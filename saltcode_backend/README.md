# saltcode-backend

The Python half of [Saltcode](../README.md) — the muscle behind the Pi extension.

Everything here **computes, validates, executes code, or stores vectors**. Nothing
here orchestrates, routes, holds session state, or touches the UI; that is the
extension's job (`../extensions/saltcode.ts`). See `.claude/rules/project-architecture.md`
for the placement test.

## Install

```bash
pip install saltcode-backend        # published package
pip install -e ".[dev]"             # from this directory, for development
```

The Pi package is installed separately (`pi install npm:@laz/saltcode`); it locates
this backend on `PATH` or via a configured interpreter.

## Layout (design §17)

```
saltcode/
├── contracts/     # pydantic boundary + Output-Length Enforcer + HARD CONSTRAINTS parser
├── diffs/         # unified-diff validation and application
├── harness/       # sandbox, scope probe, command allowlist, audit log, connectivity
├── mcp/           # LSP/AST server (where_is · find_references · outline) + scoped-read broker
├── memory/        # LanceDB: spec cache, PCD semantic cache, atomic notes, skills
├── providers/     # local Saltnitor client (stability only) + embeddings
├── static_gate/   # per-language runners and the gate dispatcher      (Task 9)
├── stability/     # N-pass Auditor measurement + calibration          (Tasks 10, 14b)
└── tools/         # `python -m saltcode.tools.<name>` CLI entrypoints (Task 7b)
```

`daemon.py` (Task 19) will serve these same handlers over stdio/socket so
interpreter and library imports amortize once per session; per-call `pi.exec`
remains the supported fallback (REQ-EXT-014).

## Checks

```bash
ruff check .
pyright
pytest -q
```

All three gate CI. `pyright` runs in `--strict` mode via `pyproject.toml`; it reads
the virtualenv at the **repository root** (`venvPath = ".."`).

## Dependency notes

- **Dropped in v9:** `typer`, `rich`, `textual` — Pi owns the interface
  (REQ-EXT-008), so there is no separate CLI or TUI. Also `openai`: the Saltnitor
  client (`providers/local.py`) speaks the OpenAI-compatible HTTP API over `httpx`
  directly and never imported the SDK.
- **`networkx` is retained** even though Task 0.1's dependency list omits it.
  `contracts/tasks.py` uses it for the `depends_on` acyclicity check, which is
  backend-owned (REQ-CON-003 AC2, REQ-PLN-002 AC1). `harness/dag.py` also uses it,
  but that module migrates to the extension — when Task 3 removes it, the remaining
  use is a single DAG check that could be inlined to drop the dependency entirely.
- **`pyarrow` is declared explicitly** because `memory/` imports it directly rather
  than relying on it arriving as a LanceDB transitive dependency.

## Migration note

Several modules here are slated to move **out** of the backend and into the
TypeScript extension under v9: `harness/{dag,budget,thinking_gate,phase_gate,router,
ctx_compactor,write_allowlist}.py` and `providers/deepseek.py`. Do not extend them —
port them (Tasks 2, 3, 13).
