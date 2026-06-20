---
trigger: always_on
---

# Saltcode Privacy Boundary

This rule is non-negotiable. No exception, no override.

## Raw Source Never Leaves the Box

No raw file body shall be transmitted to any network API at any point. This applies to all Phase-1 agents (Scout, Architect, Planner, Test Intent, Evaluator) when running via the DeepSeek API online.

## How Agents Get Repo Knowledge

- **Scout**: LSP/AST MCP tools only (`where_is`, `find_references`, `outline`). Returns symbols/AST, never file content. The broker DENIES `read_file` for Scout.
- **Architect / Planner / Test Intent / Evaluator**: receive typed JSON contracts (context_report, design.md, tasks.json) — never raw source.
- **Builder** (runs locally): MAY read file bodies, but ONLY for paths listed in `task.files_affected`. The broker DENIES reads for any other path. Since the Builder runs on a local model, file bodies never leave the box.
- **Auditor** (runs locally): receives the diff (which the Builder generated locally) + test results + spec content. No raw source leaves the box.

## If You're Unsure

If any code path would place raw file contents into a request payload destined for a network API, **stop and refuse**. This is a hard invariant.
