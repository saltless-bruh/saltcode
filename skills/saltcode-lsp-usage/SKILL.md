---
name: saltcode-lsp-usage
description: How to use the LSP/AST tools — query symbols and outlines before ever asking for a file body, stay inside task.files_affected, and never emit raw source into a response.
---

# Using the LSP/AST tools

Preloaded for **Scout** and **Builder** — the two agents that touch the repository. The
rules differ between you in exactly one respect, stated below.

## Symbols first, bodies last (or never)

The tools answer structural questions without moving source text:

| Tool | Answers |
|---|---|
| `outline(path)` | what a file declares — classes, functions, exports, signatures |
| `where_is(symbol)` | where a name is defined |
| `find_references(symbol)` | everywhere it is used |
| `file_tree` | what exists, and the config files that reveal conventions |

Reach for a body only when the structure genuinely cannot answer the question. Most of the
time it can:

- *"Does this class have a `validate` method?"* → `outline`, not a read.
- *"Who calls this?"* → `find_references`, not a grep through bodies.
- *"Where does this type come from?"* → `where_is`.
- *"What conventions does this project follow?"* → `file_tree` plus a few `outline`s.

This is not only a privacy rule; it is usually the better tool. An outline of a 900-line
module is a dozen lines and tells you the shape. Reading the module costs the context you
need for the actual work and buries the shape in detail.

## The line between you

**Scout: you have no body-reading tool at all.** `saltcode_read_scoped` is absent from your
allowlist, blocked at `pi.on("tool_call")`, and refused by the backend broker. Three
layers, because you are API-routed: anything you hold can reach a network provider, and
raw source must not (REQ-MCP-001, REQ-SCT-001). Do not look for a way around it — `bash
cat`, a shell redirect, a tool that "just previews" a file. There is no supported route,
and attempting one is a finding, not a workaround.

If a question truly needs a body, report that you could not answer it from symbols. An
honest gap is usable; a guess dressed as an observation is not.

**Builder: you may read bodies, for `task.files_affected` and nothing else.** You run on a
local model, which is the entire reason the capability exists for you. `saltcode_read_scoped`
enforces the path list; the broker re-checks it.

Even so, lead with `outline`. You are working one task with a bounded context, and the
files in scope are often larger than the change.

## Never emit raw source

Whatever you read, do not paste file contents into your response, your artifact, or a
prose explanation. Refer to symbols and paths — `saltcode/memory/spec_cache.py::lookup_spec`
— not to pasted blocks.

The Builder's unified diff is the sole exception, and it is not really one: a diff is the
change, restricted to files already in scope, and it is what the pipeline exists to
consume.

This matters because output travels further than input. A body you read locally stays
local; a body you quote into an artifact can be read by a later agent, summarised into a
prompt, or carried into a network-routed call — and by then nobody can tell where it came
from.

## When a tool cannot answer

Say so plainly. An unreachable language server, a file the outline returns nothing for, a
symbol with no definition — those are conditions to report, not to route around by reading
something adjacent and inferring. The AST fallback covers some languages better than
others (only Python is verified end to end today), so a thin answer may mean the tooling is
thin, not that the code is simple.

## Satisfies

REQ-EXT-016 AC2 · REQ-MCP-001/002 (never transmits repo content off-box) · REQ-SCT-001
(Scout is symbol-only) · REQ-BLD-002 (Builder scoped to `files_affected`).
