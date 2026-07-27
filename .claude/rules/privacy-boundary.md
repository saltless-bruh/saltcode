# Saltcode Privacy Boundary

This rule is non-negotiable. No exception, no override.
(REQ-GLB-003, REQ-MCP-001/002, REQ-SCT-001, REQ-BLD-002, REQ-ORC-007.)

## Raw Source Never Leaves the Box

No raw file body shall be transmitted to any network provider at any point. This
applies to every API-routed agent — Scout, Architect, Planner, Test Intent, Evaluator
— whichever provider they resolve to (DeepSeek, Qwen, or any future registration).

## How Agents Get Repo Knowledge

- **Scout**: LSP/AST tools only (`where_is`, `find_references`, `outline`), served by
  the local MCP server and bridged into Pi as `mcp_saltcode-lsp_*`. Symbols and ASTs,
  never file content. Scoped read is **denied** to Scout.
- **Architect / Planner / Test Intent / Evaluator**: receive typed JSON contracts
  (`context_report.json`, `design.md`, `tasks.json`) — never raw source.
- **Builder** (runs locally): MAY read file bodies, but ONLY for paths listed in
  `task.files_affected`, via `saltcode_read_scoped`. Any other path is blocked. The
  Builder runs on a local Saltnitor model, so bodies never leave the box.
- **Auditor** (runs locally): receives the diff (generated locally) + test results +
  spec content. Its N-pass judgment runs against local Saltnitor. The only online
  Auditor call is the Flash re-judgment on an unstable verdict — and it carries the
  diff and evidence the Builder already produced, never a raw file read.

## Enforced in three places (defense in depth)

1. **Extension** — `pi.on("tool_call")` blocks out-of-scope scoped reads before the
   call runs. MCP-bridged tools are ordinary Pi tools, so they pass through this
   handler too; there is no side door around it.
2. **Sub-agent definitions** — each agent's tool allowlist omits what it must not
   hold (Scout has no scoped read at all), so the capability is absent by construction.
3. **Backend** — `mcp/broker.py` re-checks `task.files_affected` and refuses; the
   provider layer refuses any payload tagged as source on a network-routed call.

A hole in one layer must not become a leak. If you weaken one, say so out loud.

## If You're Unsure

If any code path would place raw file contents into a request payload destined for a
network provider, **stop and refuse**. This is a hard invariant. It is not traded
away for latency, quality, convenience, or a passing test.
