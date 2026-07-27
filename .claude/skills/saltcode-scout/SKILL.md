---
name: saltcode-scout
description: Guides the Scout agent to map repository structure via LSP symbols without reading file bodies, producing context_report.json with constraints and anti-patterns.
---

# Saltcode Scout Skill

Use this when running the Scout phase of a Saltcode sprint (Phase 1, Step 1).
Contract: REQ-SCT-001/002, REQ-CON-001. Shape: `specs/design.md §7`.

Scout runs in an **isolated context** on DeepSeek V4 Flash with thinking `off`.
Its context contains the goal and its own tool results — nothing else.

## Allowed Tools

Served by the local LSP/AST MCP server and bridged into Pi as `mcp_saltcode-lsp_*`:

- `where_is(symbol)` — locates a symbol's definition site.
- `find_references(symbol)` — lists all call/usage sites.
- `outline(path)` — returns declarations, classes, functions, exports.
- `file_tree` — lists project structure.

## Forbidden Tools

- **Scoped read (`saltcode_read_scoped`) is DENIED to Scout** — it is absent from
  Scout's tool allowlist *and* blocked by the extension's `tool_call` handler.
- Do not attempt workarounds (`cat`, `bash`, `read`). Scout runs on a **network
  provider**, so a raw file body reaching it would breach the privacy boundary
  (REQ-GLB-003). Symbols only. If you cannot answer from symbols, say so.

## What to Produce

Write `.saltcode/context_report.json` matching this schema:

```json
{
  "schema_version": "1",
  "existing_patterns": ["e.g. 'uses pydantic v2 for all models'"],
  "relevant_files": ["saltcode/contracts/tasks.py", "..."],
  "constraints": ["e.g. 'all database access goes through the repository layer'"],
  "anti_patterns": ["e.g. 'no direct SQL in route handlers'"]
}
```

Validate with `saltcode_validate_contract` before finishing. Malformed output gets
one bounded repair, then a typed error — nothing partial is ever written.

**These strings are load-bearing.** The Architect must copy every `constraints[]` and
`anti_patterns[]` entry **verbatim** into `## HARD CONSTRAINTS`, and the Evaluator's
preservation check compares them as exact string sets. Write them as you want them to
appear downstream: self-contained, unambiguous, no trailing punctuation drift.

## How to Find Constraints

- Look for patterns in the outline: do all modules follow a certain structure?
- Check for enforced conventions: type hints, decorator patterns, test naming.
- Scan the file tree for `.eslintrc`, `pyproject.toml [tool.ruff]`,
  `tsconfig.json strict:true` — these imply project conventions.
- If a pattern is clearly enforced across 3+ files, it's a constraint.
- If something is conspicuously absent (no raw SQL, no `print()`), it's an
  anti-pattern.

## Common Mistakes to Avoid

- Do NOT produce constraints you can't back with evidence from the symbol graph.
- Do NOT invent patterns — only report what you observe.
- Do NOT read file bodies "just to check" — use `outline` instead.
- Do NOT write a design or a task list. Scout observes; it does not plan.
