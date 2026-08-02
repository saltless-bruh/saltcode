---
name: saltcode-scout
description: Guides the Scout agent to map repository structure via LSP symbols without reading file bodies, producing context_report.json with constraints and anti-patterns.
---

# Saltcode Scout Skill

Use this when running the Scout phase of a Saltcode sprint (Phase 1, Step 1).

## Allowed Tools
- `where_is(symbol)` — locates a symbol's definition site.
- `find_references(symbol)` — lists all call/usage sites.
- `outline(path)` — returns declarations, classes, functions, exports.
- `file_tree` — lists project structure.

## Forbidden Tools
- **`read_file` is DENIED.** The broker will reject it. Do not attempt workarounds (e.g. `cat`, `bash` read). You operate on symbols only.

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

## How to Find Constraints
- Look for patterns in the outline: do all modules follow a certain structure?
- Check for enforced conventions: type hints, decorator patterns, test naming.
- Scan the file tree for `.eslintrc`, `pyproject.toml [tool.ruff]`, `tsconfig.json strict:true` — these imply project conventions.
- If a pattern is clearly enforced across 3+ files, it's a constraint.
- If something is conspicuously absent (e.g. no raw SQL, no `print()` statements), it's an anti-pattern.

## Common Mistakes to Avoid
- Do NOT produce constraints you can't back with evidence from the symbol graph.
- Do NOT invent patterns — only report what you observe.
- Do NOT read file bodies "just to check" — use `outline` instead.
