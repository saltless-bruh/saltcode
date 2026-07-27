# Saltcode — Design

**A Pi Coding Agent package that gives Pi spec-driven engineering discipline.** Derived from `Proposal_Pi_v8.md` (the framework design) + `Proposal_Pi_v9.md` (the Pi-extension addendum) — together the source of truth — and grounded in Pi's official docs (`extensions`, `packages`, `custom-provider`, `containerization`, `prompt-templates`, `skills`, `compaction`, `session-format`, `tui`) as of the Pi `latest` channel.

This document is the blueprint. It tells any agent or engineer **what** to build, **how** the pieces fit, **which** Pi API sits **where**, and **what** the repository looks like. It contains no implementation code — that is produced under `tasks.md`, bounded by `requirements.md`.

> Reading order for an implementer: this file → `requirements.md` (the contract every line of code is bound by) → `tasks.md` (the ordered build).

---

## 0. What changed since the standalone design (the architectural shift)

Saltcode was previously a standalone Python CLI/TUI that orchestrated 7 LLM agents, called provider APIs directly, ran its own gates, and managed its own UI. Pi was optional and the human switched between tools.

**Saltcode is now a Pi Package.** Pi is the interface and the agent loop. Saltcode contributes engineering discipline on top of it.

| Concern | Old (standalone) | New (Pi extension) |
|---|---|---|
| Interface | Typer CLI + Textual TUI | Pi's native TUI + slash commands + widgets |
| Agent loop / multi-agent | Saltcode's own DAG orchestrator | Pi's agent loop + a **sub-agent extension** (`pi-subagents`): each agent runs in an isolated context, sequenced by the `/sprint` handler |
| Provider management | internal `LLMClient` | `pi.registerProvider()` (DeepSeek, Qwen, Saltnitor) |
| Model / thinking routing | internal tier router + thinking gate | `pi.setModel()` + `pi.setThinkingLevel()` (+ per-agent frontmatter) |
| Tool access control | internal broker | per-agent tool allowlist + `pi.on("tool_call")` blocking + built-in `write`/`edit`/`bash` **override** into the container |
| LSP/AST access | internal MCP consumption | declare the server in `mcp.json`, consume via an **MCP client extension** (`pi-mcp-extension`) |
| State (budget, sprint, tasks) | internal objects | `pi.appendEntry()` + `ctx.sessionManager` |
| Long-context discipline | internal context compactor | `pi.on("session_before_compact")` (preserve HARD CONSTRAINTS) |
| Heavy compute (gates, sandbox, caches, stability) | Python harness | **unchanged Python backend**, run as a **persistent daemon** (cold `pi.exec` = fallback) |
| The 7 agents | Python classes | **sub-agent definitions** (Markdown frontmatter) built on the existing skills |

**The human never leaves Pi.** Conversational mode = talk to Pi normally; the loaded skills make it behave as the Saltcode agents. Autonomous mode = `/sprint "add rate-limiting"` → the extension drives the full pipeline, pausing only at the four human decision points (INTERACTION.md).

**What did NOT change:** the 7-agent roster and roles; the typed JSON contracts; the 5-gate Phase-2 pipeline; the stability-based confidence measurement; the PCD-adaptive semantic cache; the measured-then-fixed threshold protocol; the security-containment requirements; the Evaluator's four checks; the retry budget (shared ≤3, Tier-A sub-cap 2); the `spec_defect` feedback channel; the prefix-cache structure; the cost model. Those carry over verbatim from the proposal and are restated below.

---

## 1. Goals & non-goals

**Goal.** A Pi Package (extension + skills + prompt templates + Python backend) that turns a single English goal into shipped code through two phases: a **frontier-API planning phase that fires once per sprint** and produces typed JSON contracts on disk, then a **local-model execution phase that loops at $0 API cost**, building one task at a time against pre-written acceptance tests with a static gate and an anti-gaming faithfulness gate — all expressed through Pi's native interface.

**Explicit non-goals (do not build, do not pretend to guarantee):**
- **Logical cross-task integration is NOT machine-guaranteed.** Type safety (static gate) and constraint legality (Evaluator) are enforced; *semantic composition across tasks* (e.g. task-2 returns cents, task-5 passes dollars, both typed `number`) remains the **human's** job at Sprint Complete. The package surfaces this for review; it never claims to verify it. (Proposal Trade B.)
- No autonomous shipping. The human reviews the diff and ships.
- No raw source leaves the box during planning (privacy boundary, §13).
- Saltcode does not re-implement anything Pi already provides (agent loop, tool dispatch, session persistence, TUI, provider auth). It composes them.

---

## 2. Design decisions & assumptions

| ID | Decision | Rationale |
|----|----------|-----------|
| DD-1 | **Two languages, one bridge.** The extension is **TypeScript** (Pi's requirement); the heavy backend is **Python 3.11+**; the bridge is **`pi.exec("python", ["-m", "saltcode.tools.<name>", ...])`**. | Pi loads TypeScript extensions via jiti. The existing pydantic/LanceDB/sandbox code is Python and does not change — it gets thin CLI entrypoints the extension calls. |
| DD-2 | **Providers are registered, not wrapped.** DeepSeek V4, Qwen, and Saltnitor are declared via `pi.registerProvider()`. Saltnitor (`http://127.0.0.1:8765/v1`) is `api: "openai-completions"`. | The docs' async-factory example registers a local OpenAI-compatible endpoint exactly like Saltnitor. `pi.registerProvider` replaces the internal `LLMClient`; provider auth, retries, and streaming become Pi's job. |
| DD-3 | **Model + thinking switching is per-agent, via Pi.** Each agent turn calls `pi.setModel(model)` and `pi.setThinkingLevel(level)` from a `before_agent_start` / command handler. | Pi exposes `setModel` (returns `false` if no API key) and `setThinkingLevel` with levels `off|minimal|low|medium|high|xhigh`. This replaces the internal tier router AND upgrades the binary thinking gate to graded levels. |
| DD-4 | **Access control is `tool_call` blocking + active-tool gating.** The write-path allowlist (no `tests/**`), command allowlist, and scoped file-read broker are enforced in a `pi.on("tool_call")` handler (returns `{ block: true, reason }`) plus `pi.setActiveTools()` per phase. | Pi's `tool_call` event is mutable and blockable. This is the documented mechanism for "permission gates" and "path protection" and replaces the internal broker. |
| DD-5 | **Sprint/budget/task state lives in the session.** Persisted with `pi.appendEntry(customType, data)`; rebuilt at `session_start` from `ctx.sessionManager.getEntries()`. | `appendEntry` state does not enter the LLM context but survives restarts/resume — exactly what budget counters and task progress need. |
| DD-6 | **HARD CONSTRAINTS survive compaction via the hook.** A `pi.on("session_before_compact")` handler ensures the `## HARD CONSTRAINTS` block is preserved in any summary. The periodic **Spec Compactor** (a backend tool, §16) governs the on-disk `design.md`. | Pi owns conversation compaction; Saltcode owns the on-disk spec. Both must never drop a constraint. |
| DD-7 | **Builder escalation (Trade A / C4) ships as an OPTIONAL, online-only flag**, default OFF. | Mirrors the Phase-1 fallback, ~free, but breaks Phase-2 offline purity. Off by default preserves the air-gap; a Pi flag (`pi.registerFlag`) enables it. |
| DD-8 | **Embeddings = local model** (`bge-small`/`nomic-embed` via a local endpoint), invoked by the Python backend. | Semantic cache + notes RAG must work offline; no API dependency for retrieval. |
| DD-9 | **Per-project state lives under `<repo>/.saltcode/`** (the Local Code-Wiki + caches); Pi session files live under Pi's own session store. | Keeps Saltcode artifacts beside the target repo and separate from Pi's session JSONL. |
| DD-10 | **Orchestration commands are registered commands; workflows are prompt templates.** `/sprint`, `/review`, `/status`, `/cost` are `pi.registerCommand()` handlers (they run logic). `phase1.md`, `phase2.md`, `review.md`, `sprint.md` are prompt templates (reusable instruction blocks) the user can invoke in interactive mode and that the `/sprint` handler composes. | Prompt templates only *expand into text*; they cannot set models or pause. The autonomous loop needs a code handler. Both coexist (Pi supports both). |
| DD-11 | **Multi-agent = a sub-agent extension dependency, not `sendUserMessage`.** Each agent runs in an isolated context via a sub-agent extension (`pi-subagents`); agents are Markdown definitions (model, thinking, tools). | Pi has no built-in sub-agents but the ecosystem does. Isolated context makes "one task per context" true by construction, instead of pruning one accumulating session — and improves per-agent prefix caching. |
| DD-12 | **LSP/AST consumed via an MCP client extension, not a hand-rolled bridge.** Declare the server in `mcp.json`; `pi-mcp-extension` bridges its tools as native Pi tools. | Pi ships no MCP client. Bridged tools are ordinary Pi tools, so `tool_call` gating + the scoped-read broker still apply. |
| DD-13 | **Pi's built-in `write`/`edit`/`bash` are overridden into the container.** Same-name tool registration replaces them with contained versions. | `setActiveTools` gating alone leaves a live built-in as an uncontained side door (esp. interactive mode). Overriding closes it in all modes. |
| DD-14 | **The Python backend runs as a persistent daemon.** One long-lived process over stdio/socket; per-call `pi.exec` is the fallback. | Cold `pi.exec` per gate pays Python + pydantic/LanceDB import cost (~0.5–2s) every call; ~8 calls/task × retries is minutes of pure startup. A daemon amortizes imports once. |
| DD-15 | **Dependency extensions are bundled + versioned, not forked.** `pi-mcp-extension` + `pi-subagents` go in `dependencies` + `bundledDependencies`, referenced via `node_modules/` paths; updates ride `pi update --extensions` (npm semver) or an explicit git re-pin. All Saltcode customization lives in *our* layer (skills, `agents/*.md`, prompts, `mcp.json`), so upstream stays pristine and updatable. A git submodule of each is kept only as an audit/patch escape hatch; internal fixes are sent upstream (PR-first). | Forking kills updates and duplicates code; Pi's model is compose-and-configure. Customizing in our layer preserves clean `pi update` while giving full behavioral control. |
| DD-16 | **Agent skills are preloaded into sub-agent prompts, not left to auto-discovery.** Each `agents/*.md` preloads its Saltcode skill directly (sub-agent extension feature). Three MCP/sub-agent-specific skills are added: `saltcode-lsp-usage`, `saltcode-delegation`, `saltcode-checkpoint-ops`. | Pi only injects a skill into an agent's prompt if that agent holds the `read` tool — but Saltcode agents are deliberately locked down (Scout is AST-only). Preloading guarantees the skill is present regardless of toolset. |

---

## 3. System architecture (layers)

```
                         HUMAN  ── talks to Pi (interactive) or runs /sprint (autonomous)
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                                   Pi (HOST)                                    │
│   agent loop · tool dispatch · session store (JSONL) · native TUI · provider  │
│   auth · compaction · model registry · thinking control                        │
└──────────────────────────────────────────────────────────────────────────────┘
                                   │  Pi Package: `pi install npm:@laz/saltcode`
        ┌──────────────────────────┼───────────────────────────────────────────┐
        ▼                          ▼                                            ▼
┌──────────────────┐   ┌──────────────────────────────────┐      ┌────────────────────────┐
│ skills/  (md)    │   │ extensions/saltcode.ts  (THE BRIDGE)│      │ prompts/  (md)         │
│ scout, architect,│   │  pi.on(session_start)  init state   │      │ sprint.md  phase1.md   │
│ planner, test-   │   │  pi.on(before_agent_start) prefix +  │      │ phase2.md  review.md   │
│ intent,evaluator,│   │     per-agent system prompt          │      │  (instruction blocks   │
│ builder, auditor,│   │  pi.setModel / pi.setThinkingLevel   │      │   for interactive +    │
│ compactor +      │   │  pi.registerProvider (DS/Qwen/Salt)  │      │   /sprint composition) │
│ 6 community      │   │  pi.registerTool (backend tools)     │      └────────────────────────┘
│ cookbooks        │   │  pi.on(tool_call) allowlist/broker    │
└──────────────────┘   │  pi.setActiveTools (per-phase gating) │
                       │  pi.registerCommand (/sprint /review  │
                       │     /status /cost)                    │
                       │  ctx.ui.setWidget/setStatus (TUI)     │
                       │  pi.appendEntry (budget/task state)   │
                       │  pi.on(session_before_compact)        │
                       │     preserve HARD CONSTRAINTS         │
                       │  spawn sub-agents · override built- │
                       │    in write/edit/bash → container    │
                       │  depends on MCP-client + sub-agent    │
                       │    extensions (mcp.json, agents/)     │
                       └───────────────────┬──────────────────┘
                                           │  backend daemon (stdio/socket); pi.exec fallback → python -m saltcode.tools.<x>
                                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                  saltcode-backend  (Python, `pip install`)                     │
│  contracts/ (pydantic + enforcer)   memory/ (LanceDB caches, notes, PCD)       │
│  static_gate/ (subprocess runners)  harness/ (budget, sandbox, scope probe)    │
│  mcp/ (LSP/AST broker)              stability/ (N-pass measurement)            │
│  tools/  ← standalone CLI entrypoints, one per capability (called via pi.exec) │
└──────────────────────────────────────────────────────────────────────────────┘
        │
   ═════╪═══════════════════ PROVIDER-AGNOSTIC CONTRACT BOUNDARY ═══════════════════
        │  All Phase-1 output = typed JSON files on disk (the Code-Wiki) under .saltcode/
        │  Swap DeepSeek → any provider or local MoE without touching Phase 2.
   ═════╪══════════════════════════════════════════════════════════════════════════
```

The extension is the **bridge**: it knows *which model, which thinking level, which tools, which prompt* each agent needs and *when to pause for the human*. The Python backend is the **muscle**: it validates contracts, runs gates inside containers, manages LanceDB, and computes stability. Pi is the **host**: it runs the loop, dispatches tools, and persists the session.

---

## 4. Glossary & cadence (carries the cost + cadence model — fixed)

- **TASK** — smallest unit, exactly one object in `tasks.json`, built + audited in isolation. **HARD RULE: exactly one task per Builder context window. No carryover.** In Pi terms, a task is built within a bounded agent turn whose context the extension restricts to that task's inputs.
- **SPRINT** — one Phase-1 spec-lock + the N Phase-2 task loops it produces. **Exactly ONE Phase-1 fire per sprint** (enforced by the `/sprint` command handler and Phase-Gate state).
- **SESSION** — a Pi session, kept alive so the prefix cache stays warm. Holds ≥1 sprint. Multiple sprints in one warm session reuse the cached prefix → "Phase-1 once per sprint" and "keep sessions long" are compatible.

---

## 5. The Pi integration surface (grounded in Pi's docs)

This is the heart of the new design: every Pi API Saltcode uses, with the exact method and event names from Pi's docs.

### 5.1 Extension shape, imports, locations

The extension is a default-export factory (`export default function (pi: ExtensionAPI)`), optionally async for one-time startup work (e.g. discovering Saltnitor's models). Imports:

| Import | Use in Saltcode |
|---|---|
| `@earendil-works/pi-coding-agent` | `ExtensionAPI`, `ExtensionContext`, event/type helpers, `isToolCallEventType` |
| `typebox` (`Type`) | tool `parameters` schemas |
| `@earendil-works/pi-ai` (`StringEnum`) | string-enum tool params (Google-API compatible) |
| `@earendil-works/pi-tui` | custom TUI components / `AutocompleteItem` |
| `node:*` builtins | `pi.exec` is preferred, but `node:path`/`node:fs` are available |

Distributed as a Pi Package; installed with `pi install npm:@laz/saltcode` (or `git:`). The five Pi runtime packages above go in `peerDependencies` with `"*"` and are **not** bundled.

### 5.2 Event hooks used

| Pi event | Saltcode use |
|---|---|
| `session_start` | Init sprint/budget state by replaying `ctx.sessionManager.getEntries()` for Saltcode `custom` entries; probe connectivity (online/offline); load project config; register Saltnitor models if reachable. Do **not** start long-lived resources here unless needed; pair with `session_shutdown`. |
| `resources_discover` | Contribute Saltcode skill/prompt paths if not already bundled (returns `{ skillPaths, promptPaths }`). |
| `before_agent_start` | Inject the **active agent's system prompt** and the **stable prefix** (system + `design.md` + frozen notes) by returning `{ systemPrompt, message }`. Reads `event.systemPromptOptions` to respect user config. This is where the prefix-cache structure (§15) is assembled. |
| `tool_call` | **Enforce access control** (DD-4): block `tests/**` writes, block non-allowlisted commands, reject scoped-read of paths outside `task.files_affected`. Returns `{ block: true, reason }`. `event.input` is mutable for argument patching. Use `isToolCallEventType`. |
| `tool_result` | Optionally compress large gate/test output (RTK) before it re-enters context on a retry. |

**RTK integration (optional).** If `rtk` is on `$PATH`, large subprocess output (static-gate errors, test-runner failures, sandbox logs) is compressed 60–90% before it re-enters Pi's context on a Builder retry, leaving more room for the Builder to reason about the fix. This is handled **entirely in the backend** (`subprocess_util.py` auto-detects `rtk` via `shutil.which("rtk")` and prefixes commands with it; if absent, output passes through uncompressed) — the extension and its `tool_result` handler do not need to know about RTK.
| `model_select` / `thinking_level_select` | Update the status widget when the active model or thinking level changes. |
| `session_before_compact` | Preserve the `## HARD CONSTRAINTS` block when Pi compacts the conversation (DD-6): return a `{ compaction: { summary, ... } }` that always retains constraints, or `{ cancel: true }` when unsafe. |
| `session_shutdown` | Flush/close any session-scoped backend resources. |

### 5.3 Registered tools (the bridge to the backend)

Each backend capability is a registered tool whose `execute` shells out via `pi.exec`. Parameters use TypeBox. Example shape (illustrative, not code to ship):

```
pi.registerTool({
  name: "saltcode_static_gate",
  label: "Static Gate",
  description: "Run the per-language static gate on the sandboxed diff inside a security container.",
  parameters: Type.Object({ task_id: Type.String(), language: StringEnum([...]) }),
  async execute(id, params, signal, onUpdate, ctx) {
    const r = await pi.exec("python", ["-m", "saltcode.tools.static_gate",
                                        "--task", params.task_id], { signal });
    return { content: [{ type: "text", text: r.stdout }], details: { code: r.code } };
  },
});
```

Registered tools (one CLI entrypoint each in `saltcode.tools.*`):

| Tool | Backend entrypoint | Purpose |
|---|---|---|
| `saltcode_validate_contract` | `validate_contract` | pydantic validate + Output-Length Enforcer (repair-or-reject) |
| `saltcode_scope_probe` | `scope_probe` | single `outline` LSP call → scope fingerprint (not a Phase-1 fire) |
| `saltcode_cache_lookup` | `cache_lookup` | exact → semantic (PCD-adaptive) ladder; returns cached `tasks.json` or `miss` |
| `saltcode_diff_check` | `diff_check` | unified-diff validity (`git apply --check`) |
| `saltcode_sandbox_apply` | `sandbox_apply` | create disposable sandbox (git worktree), apply diff inside container |
| `saltcode_static_gate` | `static_gate` | per-language static analysis in container |
| `saltcode_test_run` | `test_run` | run task spec in the **same** container |
| `saltcode_stability` | `compute_stability` | N-pass Auditor stability score + GaC |
| `saltcode_apply_live` | `apply_live` | apply a *passed* diff to the live tree (write-path checked) |
| `saltcode_compact_spec` | `compact_spec` | periodic Spec Compactor (never strips HARD CONSTRAINTS) |
| `saltcode_calibrate` | `calibrate` | measured-then-fixed threshold calibration |
| `saltcode_read_scoped` | `read_scoped` | scoped file read for the Builder (broker also enforced in `tool_call`) |

LSP/AST queries (`where_is`, `find_references`, `outline`) are exposed by the backend's MCP server (Task 4). Pi has **no built-in MCP client**, so Saltcode does NOT hand-roll a bridge: it declares the server in `mcp.json` and depends on an **MCP client extension** (e.g. `pi-mcp-extension`), which auto-discovers the server's tools and registers them as native Pi tools named `mcp_saltcode-lsp_<tool>`. Because those are ordinary registered Pi tools, they still pass through our `pi.on("tool_call")` handler and the per-agent tool allowlist — so gating and the scoped-read broker work unchanged (§13, DD-4).

### 5.4 Registered providers

`pi.registerProvider(name, config)` for each Phase-1 / fallback provider. Config fields used: `name`, `baseUrl`, `apiKey` (`$ENV` interpolation), `api` (`"anthropic-messages"` or `"openai-completions"` etc.), `models[]` (each `{ id, name, reasoning, input, cost, contextWindow, maxTokens }`).

- **DeepSeek V4** (Flash/Pro) — online Phase-1 provider.
- **Qwen** (e.g. Qwen3.7-Max) — optional Phase-1 reasoning provider (mixed fleet).
- **Saltnitor** — `baseUrl: http://127.0.0.1:8765/v1`, `api: "openai-completions"`, models = router sections `A_STD` / `A_FOCUS` / `B`. An **async factory** can fetch `/v1/models` to register them dynamically.

**Provider failover (REQ-EXT-010).** If `pi.setModel()` returns `false` (no API key or provider unreachable) for the configured model, the extension falls through a priority chain, evaluated **per-turn** (a provider down for one turn may return for the next):
1. Try the configured model for this agent role (e.g. `deepseek/v4-flash` for Scout).
2. If unreachable → try the next provider in `saltcode.toml`'s `[providers.fallback]` list (e.g. `qwen/qwen3.6-plus`).
3. If all cloud providers fail → if Saltnitor is reachable, use Tier B (offline mode for this turn).
4. If nothing is reachable → **FLAG HUMAN** ("no provider available") and pause the sprint.

Every fallback event is written to the audit log.

### 5.5 Per-agent model + thinking routing

Resolved from the model registry and applied before each agent turn:

```
const m = ctx.modelRegistry.find("deepseek", "v4-pro");
if (m) await pi.setModel(m);          // false ⇒ no API key ⇒ surface + fall back
pi.setThinkingLevel("high");           // off|minimal|low|medium|high|xhigh
```

The graded thinking levels replace the old binary ON/OFF (see §8). Offline, all Phase-1 agents resolve to a Saltnitor model (Tier B); the Auditor stays local.

### 5.6 Per-agent tool access + built-in override (broker)

Each Saltcode agent runs as an isolated **sub-agent** (§5.9); its allowed tools are declared in the sub-agent definition's frontmatter (Scout: AST tools only, no scoped read; Builder: AST + `saltcode_read_scoped` limited to `task.files_affected`; others: none). For the top-level session, `pi.setActiveTools(names)` narrows the toolset the same way.

**Containment of Pi's built-ins (closes the interactive-mode hole).** Pi ships built-in `read`/`write`/`edit`/`bash`. Left live, an agent (or the human in interactive mode) could mutate the tree or run commands *outside* the sandbox, bypassing containment. Saltcode therefore **overrides** the mutating built-ins by registering same-named tools (`write`/`edit`/`bash`) whose execution routes through the security container (§14) — the documented override pattern, and the same move Gondolin uses to route built-ins into its VM. Read stays available (it feeds the scoped-read broker). This makes containment hold in **all** modes, including interactive; the tradeoff is that interactive `write`/`edit`/`bash` are contained versions rather than raw Pi built-ins (acceptable — that is the guarantee). `pi.on("tool_call")` remains the hard backstop that blocks any disallowed call (out-of-scope read, `tests/**` write, non-allowlisted command) even if a tool is nominally active.

### 5.6a Sub-agent orchestration (isolated context per agent)

Pi has **no built-in sub-agents**, so Saltcode depends on a **sub-agent extension** (e.g. `pi-subagents`) that spawns each agent in a fully **isolated context window** with its own model, thinking level, and tool allowlist, defined as a Markdown file with frontmatter. This is what makes the "one task per context, no carryover" invariant (REQ-BLD-001/ORC-006) true *by construction* rather than by pruning one shared session: Scout, Architect, Planner, Test Intent, Evaluator, and Builder each run as a spawned sub-agent that sees only its inputs. (The Auditor's judgment is not a sub-agent — its N-pass stability runs in the backend against Saltnitor, §11.5, to avoid N process spawns per task.) The eight existing skills become these sub-agent definitions (§7). Isolation also *helps* the prefix cache: each agent type has a stable system prompt + fixed toolset, so its cacheable prefix is byte-stable across sprints (§15). **Skill delivery (DD-16):** each agent's skill is **preloaded** into its sub-agent system prompt (a feature of the sub-agent extension), *not* left to Pi's description-match auto-loading — because Pi only injects a skill into an agent's prompt if that agent holds the `read` tool, and Saltcode agents are locked down (Scout is AST-only). Three new skills target the adopted extensions: `saltcode-lsp-usage` (query symbols/outline before bodies, stay in scope, never emit raw source), `saltcode-delegation` (which agent to spawn, serial-when-dependent, write complete zero-context task prompts), and `saltcode-checkpoint-ops` (`/checkpoints`, `/rollback`, reading regression failures).

### 5.7 State persistence

`pi.appendEntry("saltcode:sprint", {...})`, `pi.appendEntry("saltcode:budget", {...})`, `pi.appendEntry("saltcode:task", {...})`. On `session_start`, replay `ctx.sessionManager.getEntries()`, keep the latest entry per `customType`. This state never enters the LLM context.

### 5.8 TUI surface

`ctx.ui.setStatus("saltcode", "...")` (footer), `ctx.ui.setWidget("saltcode", [lines])` (above editor: phase, task deck, gate pipeline, cost), `ctx.ui.notify(msg, "info"|"error")`, `ctx.ui.confirm(title, msg)` for the four human decisions. Richer dashboards use `ctx.ui.custom()` and are guarded by `ctx.mode === "tui"`. The TUI is a **window** into the autonomous run, never a second interface (INTERACTION.md).

### 5.9 Commands and the autonomous loop

`pi.registerCommand("sprint", { handler })` is the autonomous orchestrator. Its handler:
1. resolves scope (`saltcode_scope_probe` or `--scope`), runs the cache ladder (`saltcode_cache_lookup`);
2. on miss, drives Phase 1 by **spawning each agent as an isolated sub-agent** (the sub-agent extension's spawn tool, §5.6a) in dependency order — Scout → Architect → Planner → Test Intent → Evaluator — each with its own model/thinking/tools, awaiting and validating each result before the next. (This replaces the old "`pi.sendUserMessage` into one accumulating session," which violated one-task-per-context.) Serial spawning enforces the DAG; the command handler, not the LLM, owns ordering and the one-fire-per-sprint rule.
3. surfaces **Decision 1** (plan review) via `ctx.ui.confirm`;
4. runs the Phase-2 loop per task (spawn the Builder sub-agent → gates in §5.3 → Auditor stability in the backend), surfacing **Decision 2** (intervene) only on `FLAG HUMAN`;
5. surfaces **Decision 3** (review diff) and **Decision 4** (ship).

`/review`, `/status`, `/cost` are commands that read state and render it. Interactive mode needs no command: the loaded sub-agent definitions + skills let the human (or the parent LLM) spawn any agent ad hoc, and the `/phase1`, `/phase2`, `/review` prompt templates are available for manual runs.

---

## 6. Tier policy → model + thinking routing

Each agent has a capability-driven **BASE** model; session state applies a **MODIFIER** to Architect/Evaluator only (Proposal §2). Online tiers map to providers; offline overrides all Phase-1 agents to a Saltnitor (Tier B) model.

| Agent | Base (online) | Session modifier | Thinking level |
|-------|---------------|------------------|----------------|
| Scout | DeepSeek V4 Flash | — | `off` |
| Architect | DeepSeek V4 Pro | amend/rerun → Flash | `high` |
| Planner | DeepSeek V4 Flash | — | `off` |
| Test Intent | DeepSeek V4 Flash | — | `off` |
| Evaluator | DeepSeek V4 Flash | fresh project → Pro | `off`, `medium`+ only when re-invoked after a fail |
| Spec Compactor | DeepSeek V4 Flash | — | `off` (always) |
| Builder | Tier A (Saltnitor `A_STD`/`A_FOCUS`) | high complexity / retry sub-cap → Tier B | per task; not `high` together with 256K + MTP (VRAM triangle) |
| Auditor | Tier A (Saltnitor) | online + unstable → DeepSeek Flash re-judgment | `off`, `medium`+ only on retry/`gaming_suspected` |

**Local tiers (Saltnitor router sections):**

| Profile | Model / quant | Run flags | Fires when |
|---------|---------------|-----------|------------|
| **A_STD** (default) | `Qwen3.5-9B-MTP` UD-Q5_K_XL, fully in VRAM | `-ngl 99 -fa on -c 65536 --cache-type-k q8_0 --cache-type-v q8_0` | low/med complexity, default |
| **A_FOCUS** (long ctx) | same model | `-ngl 99 -fa on -c 262144 --cache-type-k q8_0 --cache-type-v q8_0 --ubatch-size 512` | task input > 32K tokens (threshold configurable); thinking OFF on this profile |
| **B** (escalation) | `Qwen3.6-35B-A3B-MTP` UD-Q4_K_XL, hybrid offload | `-ngl 99 -ot ".ffn_.*_exps.=CPU" -fa on` (~21GB total, ~12–24 t/s) | `task.complexity == high` OR Tier-A sub-cap (2) hit |

**VRAM triangle (Tier A, 12GB).** Thinking + full-256K ctx + MTP all draw on the same 12GB — hold any **two**, not all three. The extension selects the profile per task:
- Hard reasoning task → thinking ON + `A_STD` (modest ctx, room for KV compute)
- Large context task (input > 32K tokens) → `A_FOCUS` (256K ctx, thinking OFF)
- Throughput task → MTP + `A_STD` (thinking OFF)

**A_FOCUS selection rule.** The extension selects `A_FOCUS` instead of `A_STD` when the estimated input token count for the Builder exceeds the `a_focus_threshold` (configurable, default 32,768 tokens). The estimate is computed from `len(task_object) + len(scoped_files) + len(ast_symbols) + len(task_spec)`.

**MTP regimes.** Tier B (Qwen3.6) MTP is stable (~1.4–2.2× speedup); Tier A (Qwen3.5-dense) MTP is finicky — `--spec-type mtp --spec-draft-n-max 3` is **opt-in only** via a config flag (`mtp_enabled`, default `false`), disabled until benchmarked on the user's build. Drop if no gain or segfault. The extension reads this flag before setting the profile.

These profiles correspond to the `router.ini` sections that Saltnitor serves. The extension calls `POST :8765/v1/ensure {"profile":"A_STD"}` (or `A_FOCUS` or `B`) before each Builder/Auditor turn (REQ-MOD-005), and infers via `:8765/v1/chat/completions`. If Saltnitor is unreachable, fallback is direct llama.cpp at `:8080`.

**Project config (`saltcode.toml`, local section):**
```toml
[local]
mtp_enabled = false              # opt-in: --spec-type mtp --spec-draft-n-max 3 (Tier A only, off until benchmarked)
a_focus_threshold = 32768        # tokens; above this → A_FOCUS (256K ctx, thinking OFF)
```

**Thinking-level mapping (replaces binary gate, DD-3):** the old "thinking ON/OFF" becomes graded — `off` for structured-JSON agents (Scout/Planner/Test Intent), `high` for the Architect's design reasoning, and `medium`+ *only on re-invocation* for the Evaluator and *only on retry/gaming* for the Auditor. `pi.setThinkingLevel` clamps to model capability automatically.

---

## 7. The 7 agents (now skills + extension routing) (+ periodic Compactor)

The agent *logic* (how to act, in what order) now lives in **sub-agent definitions** — Markdown files with frontmatter (model, thinking level, allowed tools) consumed by the sub-agent extension (§5.6a), each **preloading** its Saltcode skill directly (DD-16). The `/sprint` handler owns the sequence; each agent is spawned in an isolated context. I/O contracts (§9) are unchanged. (The Auditor's N-pass judgment is a backend tool, not a spawned agent — §11.5.)

| # | Agent | Skill | Model / thinking | reads | writes | Hard rules (enforced where) |
|---|-------|-------|------------------|-------|--------|------------------------------|
| 1 | **Scout** | `saltcode-scout` | Flash / `off` | `file_tree`, LSP symbols (AST tools) | `context_report.json` | Never reads raw file bodies → `tool_call` blocks scoped read for Scout. |
| 2 | **Architect** | `saltcode-architect` | Pro→Flash / `high` | `user_goal`, `context_report.json` | `design.md` (+**HARD CONSTRAINTS**) | No task list (enforced by `saltcode_validate_contract`). Mirrors every constraint + anti_pattern **verbatim**. |
| 3 | **Planner** | `saltcode-planner` | Flash / `off` | `design.md` **only** | `tasks.json` | Never reads `context_report` — constraints reach it via design.md. |
| 4 | **Test Intent** | `saltcode-test-intent` | Flash / `off` | `tasks.json` + **project config**; on re-spec also `audit_result.detail` | `tests/task_{id}_spec.*` | No impl code; specs immutable to Builder (`tool_call` blocks `tests/**` writes). |
| 5 | **Evaluator** | `saltcode-evaluator` | Flash→Pro / `off`(→`medium` on re-invoke) | `tasks.json`, `design.md`, `context_report.json` | `evaluator_report.json` | Four checks (§11); classifies + routes gaps with loop caps. |
| 6 | **Builder** | `saltcode-builder` | Tier A→B | one task, AST, **scoped file bodies** (`task.files_affected`), `tests/task_{id}_spec.*` | unified diff | 1 task/ctx, no carryover; may NOT write `tests/**`; scoped read enforced by broker + `tool_call`. |
| 7 | **Auditor** | `saltcode-auditor` | Tier A→B(→Flash) | diff, criteria, static output (clean), **test results**, spec content | `audit_result.json` | Cannot write code; confidence is **measured** (multi-pass stability, §11.5). |
| — | **Spec Compactor** | `saltcode-compactor` | Flash / `off` | `design.md` + completed history | compacted `design.md` | Runs once / 5 sprints; **never** strips an active HARD CONSTRAINT. |

Six community cookbooks (`python-pro`, `mcp-builder`, `test-driven-development`, `systematic-debugging`, `agent-tool-builder`, `git-pushing`) ship alongside as supporting skills for *building* Saltcode (COOKBOOKS.md).

---

## 8. Phase 1 — planning (DAG; fires once)

`Scout → Architect → Planner → Test Intent → Evaluator`, sequenced by the `/sprint` handler (and available manually via the `phase1.md` prompt template). Each step: switch model/thinking (§6), inject the agent skill + prefix (`before_agent_start`), run, validate output with `saltcode_validate_contract`.

**Evaluator's four checks:** (1) **traceability** — every task ↦ a design statement; (2) **coverage** — every design requirement ↦ a task; (3) **preservation** — every `context_report` constraint/anti_pattern present in the design.md HARD CONSTRAINTS block; (4) **compliance** — no task violates any constraint/anti_pattern.

**Gap routing + caps:** `design_gap` → Architect loop (re-emit design.md, Planner re-runs); `plan_gap` → Planner; `constraint_violation` → Planner (unless it conflicts with design.md → Architect). **Loop caps: Architect ≤ 2, Planner ≤ 3 / sprint.** Exceed → **FLAG HUMAN** (`ctx.ui.confirm`/`notify`) with the gap report. No silent spinning.

**Phase-Gate.** On `evaluator_report.status == "pass"`: spec locks, the spec hash is stored to the Spec Cache, and Phase 2 begins automatically (the handler advances; no human action). One Phase-1 fire per sprint is enforced by sprint state (`pi.appendEntry`).

---

## 9. Typed contracts (the provider-agnostic boundary) — UNCHANGED

All Phase-1 outputs are typed JSON on disk under `.saltcode/`, validated by pydantic and the Output-Length Enforcer (`saltcode_validate_contract`). Field sets from Proposal §5.

**`context_report.json`** (Scout): `{ schema_version, existing_patterns[], relevant_files[], constraints[], anti_patterns[] }`.

**`design.md`** (Architect) — markdown with a machine-parseable `## HARD CONSTRAINTS` H2 block containing **every** `context_report.constraints[*]` and `anti_patterns[*]` string **verbatim** (order free). Body carries components, data flow, non-goals.

**`tasks.json`** (Planner): `[{ id, description, files_affected[], acceptance_criteria[], depends_on[], complexity: low|med|high }]`.

**`tests/task_{id}_spec.*`** (Test Intent) — acceptance tests in the target language, one file per task id, written before code, immutable to Builder.

**`evaluator_report.json`** (Evaluator): `{ schema_version, status: pass|gaps, gaps:[{ id, type: design_gap|plan_gap|constraint_violation, detail, target: architect|planner }], routing_summary }`.

**`audit_result.json`** (Auditor): `{ schema_version, task_id, status: pass|fail, reason: pass|impl_fail|gaming_suspected|spec_defect, next_action: next_task|builder_retry|test_intent_respec|flag_human, detail, stability: { n_passes, verdicts[], stability_score, gac } }`.

**Stability-based confidence (BIFAI-NET v5.2).** Instead of a self-assessed `confidence: float`, the harness runs the Auditor judgment **N=3 times** (temperature jitter, evidence reordering) and computes `stability_score = 1.0 - (verdict_changes / (N-1))` and `gac` (pass at which the verdict first stabilized). If `stability_score < auditor_stability_threshold` (calibrated, §11.9) **and** online → re-run once on DeepSeek Flash; offline → majority local verdict stands. Stability is a behavioral signal that works on 7B models; the threshold is grounded by the measured-then-fixed protocol, not guessed.

**Builder diff format (structural, not JSON).** The Builder emits a **unified diff** (`--- a/` / `+++ b/` / `@@`), validated by `saltcode_diff_check` (`git apply --check`) before any gate. Non-diff output ⇒ `impl_fail` (one bounded repair from a code fence is allowed, then reject).

---

## 10. Phase 2 — execution loop (per task, local; via registered tools)

```
tasks.json[current] → BUILDER (one task + AST + scoped files + spec) → unified diff
   → saltcode_diff_check        MALFORMED → impl_fail, short-circuit to Builder
   → saltcode_sandbox_apply     (git worktree inside a security container — never live tree)
   → saltcode_static_gate       DIRTY → discard sandbox, short-circuit to Builder (lint reason)
   → saltcode_test_run          FAIL  → discard sandbox, short-circuit to Builder (test output)
   → AUDITOR (clean static report + test results + spec content + diff)
        + saltcode_stability     → audit_result.json
        pass             → saltcode_apply_live (diff → live tree), mark done, next task
        impl_fail        → Builder retry
        gaming_suspected → Builder retry with "no hardcoding" reason
        spec_defect      → re-run TEST INTENT with audit_result.detail (≤1; not a Builder retry)
```

**Sandbox rule.** The diff is NEVER applied to the live tree until the Auditor returns `pass`. Static analysis and test runner always operate on a disposable sandbox (git worktree / temp copy) **inside a security container** (§14). On any rejection the container + sandbox are destroyed; the live repo and host are untouched.

**Per-task SHARED retry budget.** Diff-format + static + test + `impl_fail` + gaming retries share one budget. **Tier-A sub-cap = 2**; the 3rd attempt escalates to Tier B (within the shared budget of 3). If the 3rd fails (or the task started on Tier B due to `complexity == high`) → **FLAG HUMAN**. `spec_defect` → Test Intent re-spec ≤1 (with feedback); does **not** consume a retry. Budget counters live in `pi.appendEntry` state and are never silently reset.

### 10.1 Checkpoints & auto-advance (the two run modes)

A **checkpoint** is a durable, verified task boundary: the point at which a task is not merely "Auditor-passed" but *confirmed finished and robust* on the integrated live tree. Checkpoints turn the Phase-2 loop into a safely resumable, roll-back-able auto-advance run.

**Robustness bar (what makes a checkpoint).** A task reaches a checkpoint only when ALL hold:
1. the five per-task gates passed (diff-check → sandbox → static → task-spec test → Auditor `pass` with `stability ≥ threshold`) — §10, unchanged;
2. the diff was applied to the live tree (`saltcode_apply_live`, now leaving the tree **uncommitted** until step 4);
3. the **regression gate** passed — the project's FULL existing test suite runs green on the integrated live tree (`regression_cmd`, default = the full `test_runner_cmd` with no task filter). If no full suite is configured the gate is SKIPPED and the checkpoint is marked `regression: unverified` (robustness is correspondingly weaker — the human review carries the difference);
4. a **git commit** is made (the checkpoint) and a snapshot is persisted: `pi.appendEntry("saltcode:checkpoint", { task_id, commit_sha, gate_results, stability, regression, timestamp, sprint_id })`.

Per task the loop becomes: gates (sandbox) → `apply_live` (uncommitted) → regression gate → **pass** ⇒ commit + snapshot (checkpoint) ⇒ next task; **fail** ⇒ discard the uncommitted apply (`git reset --hard <last checkpoint>`), route the failure (below). **The loop NEVER advances past a task that has no checkpoint.**

**Regression-fail routing (Trade B in practice).** A regression failure means the task passed its own spec but broke the integrated tree. IF the failing tests are within the task's `files_affected` (files the Builder is scoped to, REQ-BLD-002) AND budget remains → Builder retry with the regression output as feedback (`regression_fail`, counts against the shared budget). IF they touch files **outside** `files_affected`, or the budget is exhausted → **FLAG HUMAN**: this is exactly the cross-task integration signal the design refuses to auto-fix (Trade B), because resolving it may require widening scope or re-planning — a human decision. The tree is left at the last good checkpoint while flagged.

**Two run modes** (set by `auto_mode`; default `off` = today's per-sprint four-decision flow):

| Mode | Behavior | Human is pulled in | Integration review |
|------|----------|--------------------|--------------------|
| **`off`** (default) | one sprint, stop at Sprint Complete | Decisions 1–4 as today | at Sprint Complete (Trade B) |
| **Hybrid** | approve the plan once, then auto-advance the whole task list through checkpoints | Decision 1 at launch; FLAG HUMAN on any stop; **mandatory cumulative-diff review at end** | preserved — deferred to end-of-run, with rollback to any checkpoint |
| **Full Auto** | approve the plan once, then run the whole list unattended | Decision 1 at launch; **only** on a stop condition (budget / regression / FLAG HUMAN) | **traded away** — no end review (see below) |

Both modes: Decision 1 (approve the plan) still gates the *launch* — you never auto-build an unreviewed plan. Decision 2 (FLAG HUMAN) always interrupts. Checkpoints are **local** git commits; **pushing stays explicit** (`auto_push` default `false`) even in Full Auto, so unreviewed code never reaches a shared remote automatically.

**The honest cost of Full Auto.** Full Auto removes the integration review, so any semantic-composition bug that **no test covers** — the canonical Trade-B failure ("task 2 returns cents, task 5 passes dollars, both typed `number`") — is committed silently. Full Auto is therefore appropriate only when the suite genuinely encodes integration (strong regression coverage) or the backlog is low-integration-risk. **Hybrid is the safe default for real work:** the same hands-off run, but the cumulative diff still meets your eyes and any checkpoint is one `git reset` away. The checkpoint system's honest value is **resumability + rollback + supervised auto-advance**, not unattended shipping.

**Resume.** On `session_start`, the extension replays the checkpoint entries, finds the latest checkpoint for the active sprint, and verifies `git HEAD == commit_sha`. Match → resume the loop at the next task (finished work is never re-run). Mismatch (the tree was changed outside Saltcode) → FLAG HUMAN to reconcile rather than continue blindly.

**Rollback.** `/rollback last` or `/rollback <task_id>` → `git reset --hard <checkpoint_sha>`, rewind the checkpoint/task state to that point, log to `audit_log.jsonl` (warns that later uncommitted work is lost). After a rollback the human may resume, `saltcode skip` the task, or abort.

**Scope.** Auto-advance operates over the current sprint's `tasks.json` (respecting `depends_on` order). Chaining across *sprints* (auto-firing a new Phase 1 for the next goal) is out of scope — it would re-open "one Phase-1 per sprint" and cost gating — and is left as a future extension.

**Config (`saltcode.toml`):**
```toml
[checkpoint]
auto_mode = "off"          # off | hybrid | full
regression_cmd = ""        # full test suite; empty → regression gate SKIPPED (weaker robustness)
auto_push = false          # never push unreviewed commits automatically, any mode
```

---

## 11. Workflows (detail)

### 11.1 Session open (`session_start`)
1. Replay `ctx.sessionManager.getEntries()` → rebuild sprint/budget/task state.
2. Connectivity probe (backend) → online/offline; register Saltnitor models if reachable.
3. Auto-RAG ≤5 Atomic Notes, then **FREEZE** for the session (prefix stability).
4. Stable prefix is assembled lazily in `before_agent_start`.

### 11.2 New goal → Cache Ladder
**Scope fingerprint.** Store time (after Phase 1): `sorted(tasks.json[*].files_affected)`. Lookup time (before Phase 1): `--scope` arg, else `saltcode_scope_probe` (single `outline` LSP call — a tool call, **not** a Phase-1 fire), else empty (goal-only).
1. **Spec Cache (exact):** `key = sha256(normalized_goal + scope_fingerprint)`. Hit → reuse `tasks.json`, **zero API**. Stop.
2. **Semantic Cache (fuzzy):** `embed(goal + scope)`, cosine ≥ threshold → candidate. Compute **PCD** (count of cached specs within a cosine radius / cache size). High PCD → cheap/skipped Architect confirmation; low PCD → full confirmation or fall through. Threshold + bars calibrated (§11.9).
3. **Miss → fire Phase 1.**

### 11.3 Phase 1 — see §8.

### 11.4 Phase-Gate → Phase 2 — see §8 (automatic on `pass`).

### 11.5 Auditor stability measurement — see §9 (stability paragraph). Implemented by `saltcode_stability`.

### 11.6 Sprint complete
Human reviews the diff (Decision 3) — **especially logical cross-task integration (Trade B)** — then ships (Decision 4). Arch change → re-enter the cache ladder first (reuse on hit; new Phase 1 on miss). The review surface always carries the "logical integration is YOUR check" notice.

### 11.7 Spec Compactor (every 5 sprints)
`saltcode_compact_spec` strips completed/resolved content (task descriptions, resolved discussions, obsolete notes) but **never** any line of the `## HARD CONSTRAINTS` block. Constraints are removed only by explicit human edit. This prevents a compactor→Evaluator→Architect oscillation. (Distinct from Pi conversation compaction in §11.8.)

### 11.8 Conversation compaction (`session_before_compact`)
When Pi compacts the running conversation (reasons: `manual`/`threshold`/`overflow`), the handler ensures the active HARD CONSTRAINTS remain in the produced summary (return a custom `compaction` summary), or cancels when preservation can't be guaranteed. The on-disk `design.md` is the durable source; conversation compaction must not lose constraints mid-sprint.

### 11.9 Threshold calibration (measured-then-fixed, BIFAI-NET v5.2)
`saltcode_calibrate` measures thresholds (Auditor stability, semantic cosine, PCD bars) from a calibration set, then fixes them in project config. First sprint uses conservative defaults (`stability 0.5`, `cosine 0.85`, PCD = full confirmation) marked `calibrated: false`; the extension surfaces a warning at session open. Re-run on model/embedding change. Artifacts stored in `.saltcode/calibration/`.

---

## 12. Offline path
Offline → **all** Phase-1 agents (Scout, Architect, Planner, Test Intent, Evaluator) resolve to a **Tier B** Saltnitor model instead of DeepSeek. Scout still uses the LSP/AST tools (always local). The Auditor faithfulness call stays on the active local model + heuristics (no Flash escalation). Same contracts; slower wall-clock, not weaker. The §6 online tiers apply only online; offline overrides all to Tier B.

---

## 13. LSP/AST MCP server + tool broker (crown jewel — local, AST-only + scoped reads)

A **local MCP server** (Python backend, Task 4) exposes AST tools `where_is` · `find_references` · `outline` (symbols/AST only) and a scoped `read_file(path)`. The **broker** restricts paths per agent:
- **Scout:** denied scoped read (AST-only; privacy boundary for API-routed agents).
- **Builder:** scoped read allowed ONLY for paths in the current `task.files_affected`; any other path is rejected. Runs locally, so raw bodies never leave the box.
- **All others:** denied.

Backends: `pyright | tsserver | rust-analyzer | gopls`, localhost-only, never transmits source off-box. **Consumption path (corrected):** Pi ships no MCP client, so the LSP/AST server is declared in `mcp.json` and consumed via an **MCP client extension** (e.g. `pi-mcp-extension`, stdio transport: `{ "command": "python", "args": ["-m","saltcode.mcp.lsp_ast_server"], "transport": "stdio", "lifecycle": "eager" }`). It registers the queries as native Pi tools (`mcp_saltcode-lsp_*`). The broker is then enforced **twice**, exactly as before: the per-agent tool allowlist (sub-agent frontmatter / `pi.setActiveTools`) removes the tool from agents that shouldn't have it, and `pi.on("tool_call")` hard-blocks any out-of-scope `read_file` path (defense in depth, DD-4). The scope state (`task.files_affected`) lives in our extension, so the block decision stays ours even though the MCP client owns the transport.

---

## 14. Static gate + test runner + security containment (the fire alarm)

**Static gate** (per language). Zero VRAM, millisecond, hard gate, inside a **security container** on a disposable sandbox:

| Lang / stack | Static gate | Strength | Note |
|--------------|-------------|----------|------|
| TypeScript | `tsc` + `eslint` | HARD | type errors block the diff |
| Rust | `cargo check`/`clippy` | HARD | borrow + type guarantees |
| Go | `build` + `vet` | HARD | compiler-backed |
| Python | `pyright --strict` + `ruff` | MEDIUM | strict pyright; mypy optional |
| JS (no TS) | `eslint` | SOFT | no type layer → Auditor carries the weight |

Softer gate ⇒ the Auditor's faithfulness judgment matters more ⇒ prefer the Flash-escalated Auditor on SOFT-gate languages.

**Test runner.** After CLEAN, run `tests/task_{id}_spec.*` in the **same** container via the project's `test_runner_cmd` (`pytest`/`jest`/`cargo test`/`go test`). FAIL → discard sandbox, short-circuit to Builder (counts against budget). PASS → forward results to the Auditor. No `test_runner_cmd` → SKIP. Bounded timeout (default 60s; container killed on timeout).

**Containment (Pi's model: real isolation comes from the OS/VM boundary).** Pi ships no built-in sandbox; it documents the **Gondolin** pattern (host Pi + tool execution routed into an isolated boundary). Saltcode follows this pattern via its **own backend containment**: the gate/test/sandbox tools call `pi.exec("python", ["-m", "saltcode.tools.<x>"])`, and the Python backend spawns a security container — **bubblewrap (`bwrap`) primary; Docker / firejail fallback** — with: read-only host filesystem outside the worktree, no network (`--unshare-net`/`--network none`), no `$HOME`/SSH/credentials, isolated PID namespace, cgroup memory/CPU/time limits, automatic cleanup. If no backend is available the backend **refuses to run Phase 2** (no uncontained fallback). For extra defense, the whole Pi process MAY additionally run under Gondolin or Docker (operator's choice), but the primary containment is Saltcode's own.

**Command allowlist.** Only whitelisted commands run inside the container: `pytest`, `jest`, `cargo test`, `go test`, `pyright`, `ruff`, `tsc`, `eslint`, `cargo check`, `cargo clippy`, `go build`, `go vet`, `git apply`. Enforced in two places: the `pi.on("tool_call")` handler refuses non-allowlisted commands, and the backend refuses them again. Configurable per project. **Crucially, Pi's built-in `write`/`edit`/`bash` are overridden** (§5.6) so *all* mutation and command execution — from agents and from the human in interactive mode — routes through this container; otherwise a live built-in would be an uncontained side door around the whole gate pipeline.

**Audit log.** Every executed command is logged to `.saltcode/audit_log.jsonl` (command line, cwd, container id, exit code, timestamp, SHA-256 of stdout+stderr), append-only per sprint.

**Dry-run.** A Pi flag (`pi.registerFlag("dry-run")`) walks the whole pipeline with every subprocess replaced by a logged no-op; no subprocess executes and no file outside `.saltcode/` is modified.

---

## 15. Memory + prefix cache

- **Lightweight Brain** — Atomic Notes (≤5), auto-RAG at session open then **frozen**; Vectorized Skills. Backed by LanceDB (backend).
- **Spec Cache** — `key = sha256(normalized_goal + scope_fingerprint)` → `tasks.json`. LanceDB, local.
- **Semantic Cache** — `embed(goal + scope)`, calibrated cosine threshold, **PCD-adaptive** confirmation bar, Architect-confirmed before reuse.
- **Local Code-Wiki** (`.saltcode/`) — `context_report.json`, `design.md` (incl. HARD CONSTRAINTS), `tasks.json`, `tests/task_{id}_spec.*`, `evaluator_report.json`, `audit_result.json`.

**Prefix cache structure (assembled in `before_agent_start`):**
```
1. System Prompt (active agent)  (fixed per agent) → cached
2. design.md                     (session)         → cached (may change on Evaluator→Architect re-loop)
3. Atomic Notes (≤5, FROZEN)     (session)         → cached
4. Per-call delta / new input    (per call)        → billed
```
**Economics (modeled — verify before trusting).** DeepSeek's automatic context caching gives ~98% off on repeated prefix tokens ($0.0028/M cached vs $0.14/M uncached for V4 Flash). The prefix structure above is shaped to maximize this: segments 1+3 are byte-stable and segment 2 is stable within an Evaluator pass. At a 75% hit rate the effective input price would drop to ~$0.037/M — a 74% discount. **Caveat:** prefix caching is provider-side and keyed on the *serialized request prefix*, which Pi (not the extension) assembles — it injects tool schemas ahead of/around our content, and `getSystemPrompt()` does not reflect the final payload. So the 74% figure is a **model, not a measurement**: it must be validated empirically via `pi.on("before_provider_request")` (inspect the real payload) before the cost estimates in §18 are relied on. The **sub-agent model helps here** — each agent type is its own session with a fixed system prompt and fixed toolset, so its prefix is byte-stable across sprints (per-agent-type cache hits), instead of one session whose tool block shifts as active tools change per agent. (COST_OPTIMIZATION.md.)

Segments 1+3 are byte-stable across calls in a session; segment 2 is stable across sprints and within an Evaluator pass, but may change between passes (a re-loop re-emits design.md — expected). On a fresh project's first sprint, the Scout call has no segment 2. `before_provider_request` MAY be used to verify cache-prefix stability during debugging. The extension must emit segments 1+3 byte-identically and segment 2 byte-identically within a single Evaluator pass.

---

## 16. Technology map (what sits where)

| Concern | Technology |
|---------|-----------|
| Host agent | Pi (`@earendil-works/pi-coding-agent`) |
| Multi-agent (isolated context) | **sub-agent extension** dependency (e.g. `pi-subagents`) — agents as Markdown frontmatter |
| LSP/AST consumption | **MCP client extension** dependency (e.g. `pi-mcp-extension`) via `mcp.json` |
| Extension (bridge) | TypeScript (jiti-loaded), `typebox`, `@earendil-works/pi-ai`, `@earendil-works/pi-tui` |
| Backend language / runtime | Python 3.11+ |
| Bridge | **persistent backend daemon** over stdio/socket (imports amortized once); `pi.exec("python", ["-m", "saltcode.tools.<x>", ...])` = per-call fallback |
| Typed contracts + Output-Length Enforcer | pydantic v2 (validate → repair → reject) |
| Vector store (Spec/Semantic cache, Notes, Skills) | LanceDB (local) |
| Embeddings | local model (bge-small / nomic-embed) via local endpoint |
| Phase-1 LLM | `pi.registerProvider` → DeepSeek V4 (+ optional Qwen) |
| Phase-2 LLM | `pi.registerProvider` → Saltnitor `:8765` (Tier A/B); direct llama.cpp `:8080` fallback |
| Model / thinking routing | `pi.setModel` + `pi.setThinkingLevel` |
| Access control | `pi.on("tool_call")` (block) + `pi.setActiveTools` |
| State | `pi.appendEntry` + `ctx.sessionManager` |
| Conversation compaction | `pi.on("session_before_compact")` |
| LSP/AST MCP | MCP Python SDK server + pyright/tsserver/rust-analyzer/gopls; **consumed via an MCP client extension** (`mcp.json`, stdio), tools bridged as `mcp_saltcode-lsp_*` and gated by `tool_call` |
| Static gate | subprocess adapters in container: tsc, eslint, cargo check/clippy, go build/vet, pyright, ruff |
| Security containment | bubblewrap primary; Docker/firejail fallback; Pi Gondolin pattern; command allowlist + audit log |
| Diff application | `git apply` constrained by write-path allowlist |
| Interface | Pi native TUI (`ctx.ui.*`), slash commands, prompt templates |
| Config | pydantic-settings + TOML; **project config**: `language`, `test_framework`, `test_runner_cmd`, `static_gate_cmd`, allowlist overrides, thresholds (`calibrated` flags) |
| Backend tests | pytest |
| Adapted techniques | Stability Score + GaC, PCD, measured-then-fixed protocol — all from BIFAI-NET v5.2 |

---

## 17. Project tree (Pi Package + Python backend)

```
saltcode/                                  # the Pi Package (npm/git installable)
├── package.json                           # { keywords:["pi-package"], pi:{extensions,skills,prompts,agents} }
│                                          #   peerDependencies: pi-coding-agent, pi-ai, pi-tui, typebox, pi-agent-core ("*")
│                                          #   dependencies + bundledDependencies: pi-mcp-extension, pi-subagents
│                                          #     → pi manifest also references node_modules/<dep>/... resources
│                                          #     → updates via `pi update --extensions` (semver) or git re-pin
├── vendor/                                # OPTIONAL git submodules of the two deps (audit/patch escape hatch only)
├── README.md
├── design.md  requirements.md  tasks.md   # these specs
├── mcp.json                               # declares the LSP/AST server (stdio) for the MCP client extension
├── extensions/
│   └── saltcode.ts                        # THE BRIDGE (single dir or split into index.ts + modules)
│       # session_start/shutdown · before_agent_start (prefix + agent prompt)
│       # registerProvider (DeepSeek/Qwen/Saltnitor) · setModel/setThinkingLevel
│       # registerTool (saltcode_* backend tools) · on(tool_call) allowlist/broker
│       # OVERRIDE built-in write/edit/bash → container · setActiveTools (top-level)
│       # registerCommand (/sprint /review /status /cost) · spawn sub-agents (Phase-1/Builder)
│       # ctx.ui widgets · appendEntry state · on(session_before_compact)
│       # registerFlag(dry-run, builder-escalation) · backend-daemon client
├── agents/                                # sub-agent definitions (frontmatter: model, thinking, tools; each PRELOADS its skill)
│   └── scout.md  architect.md  planner.md  test-intent.md  evaluator.md  builder.md
├── skills/                                # 8 custom + 6 cookbooks + 3 new (MCP/sub-agent/checkpoint); preloaded per agent
│   ├── saltcode-scout/SKILL.md   saltcode-architect/SKILL.md   saltcode-planner/SKILL.md
│   ├── saltcode-test-intent/SKILL.md  saltcode-evaluator/SKILL.md  saltcode-builder/SKILL.md
│   ├── saltcode-auditor/SKILL.md  saltcode-compactor/SKILL.md
│   ├── saltcode-lsp-usage/SKILL.md  saltcode-delegation/SKILL.md  saltcode-checkpoint-ops/SKILL.md   # NEW
│   ├── python-pro/SKILL.md  mcp-builder/SKILL.md  test-driven-development/SKILL.md
│   └── systematic-debugging/SKILL.md  agent-tool-builder/SKILL.md  git-pushing/SKILL.md
├── prompts/                               # prompt templates (interactive + /sprint composition)
│   └── sprint.md  phase1.md  phase2.md  review.md
└── saltcode_backend/                      # `pip install saltcode-backend` (heavy lifting, unchanged)
    ├── pyproject.toml
    ├── saltcode/
    │   ├── daemon.py                      # persistent worker (stdio/socket); imports amortized once
    │   ├── contracts/                     # pydantic boundary + enforcer
    │   │   ├── context_report.py  tasks.py  evaluator_report.py  audit_result.py
    │   │   ├── design_doc.py              # HARD CONSTRAINTS extractor
    │   │   └── enforce.py                 # Output-Length Enforcer + diff validator
    │   ├── providers/embeddings.py        # local embedding endpoint (offline-capable)
    │   ├── memory/                        # lancedb_store, spec_cache, semantic_cache (PCD), atomic_notes, skills
    │   ├── harness/                       # budget, sandbox, scope_probe, command_allowlist, audit_log, connectivity
    │   ├── mcp/                           # lsp_ast_server, lsp_backends, broker
    │   ├── static_gate/                   # gate, runners
    │   ├── stability/                     # N-pass measurement, calibration
    │   └── tools/                         # CLI entrypoints called via pi.exec (one per capability)
    │       ├── validate_contract.py  scope_probe.py  cache_lookup.py  diff_check.py
    │       ├── sandbox_apply.py  static_gate.py  test_run.py  compute_stability.py
    │       ├── apply_live.py  compact_spec.py  calibrate.py  read_scoped.py
    └── tests/                             # backend pytest suite + fixture repos

workspace/<target-repo>/.saltcode/         # per-project Code-Wiki + caches
    ├── context_report.json  design.md  tasks.json
    ├── tests/task_{id}_spec.*
    ├── evaluator_report.json  audit_result.json
    ├── audit_log.jsonl  calibration/  cache/   # LanceDB (spec, semantic, notes, skills)
```

---

## 18. Cost model (sanity bound; COST_ESTIMATE.md / COST_OPTIMIZATION.md)

Phase 1 fires once/sprint; Phase 2 loops locally at $0 API. Flash-dominant sprint ≈ **$0.001**; fresh-project sprint (Architect+Evaluator on Pro) ≈ **$0.002–0.004**; worst case (2 Architect re-loops on Pro) **< $0.02**. A full Saltcode-complexity build is **$0.28–$1.18** optimized *if the prefix-cache lever lands as modeled* — that discount is provider-side and Pi controls the serialized prefix, so it must be **measured** (via `before_provider_request`, §15) before these figures are trusted; treat them as an upper-bound target, not a guarantee. **Watch (not agent count):** Phase-1 firing mid-sprint, Scout reading raw bodies, design.md bloat (run Compactor), cold caches, Evaluator re-looping to Architect, faithfulness escalations spiking, and sub-agent/process spawn startup on Phase-1. Pi's per-turn cost is visible via `message_end` usage and `ctx.getContextUsage()`; the extension surfaces it in `/cost`.

**Off-peak scheduling (optional, REQ-EXT-011).** DeepSeek offers ~50% off during 16:30–00:30 UTC (23:30–07:30 Vietnam time). A `prefer_off_peak` config flag (default `false`), when enabled, queues non-interactive work (Phase-1 planning, Spec Compactor, calibration) into the off-peak window; Phase 2 (Builder + Auditor) stays interactive/immediate regardless. The `/sprint` handler checks the flag and current UTC time: if `prefer_off_peak && !is_off_peak()` it prompts ("Phase 1 would cost ~50% less in N hours — run now or schedule?") rather than deferring silently.

---

## 19. Known limitations & human responsibilities (Proposal Trade A–D + Pi-specific)

- **Trade B (STANDS):** logical cross-task integration is the human's job at Sprint Complete; the package surfaces it, never verifies it.
- **Trade A/C4 (OPEN, DD-7):** Builder code generation does not escalate by default; optional online-only Builder escalation behind a Pi flag.
- **Trade C1:** anti-gaming Flash escalation breaks strict $0/air-gap on those calls; offline keeps it local + heuristics (higher gaming risk, by choice).
- **Trade C2:** stricter Evaluator → more loops + variable cost; the Evaluator is a heavier single point of failure (consider a confidence threshold → human flag for low-confidence verdicts).
- **Trade C3:** spec immutability shifts burden to Test Intent; `spec_defect` → Test Intent re-spec (≤1) with feedback.
- **Trade C5:** scope-fingerprint cache key lowers hit rate (safer, accepted).
- **Trade D (operational):** internet required for online Phase 1; never send raw source (enforced by the LSP/AST MCP + `tool_call`); prefix cache TTL is session-scoped (keep sessions long); 5/7 agents depend on the API, mitigated by the typed boundary + local Tier-B fallback.
- **Pi-specific:** Pi packages run with full system permissions — installation requires trust (`project_trust`); the extension executes arbitrary code, so the package and its backend must be reviewed before install. Real isolation for executing generated code is Saltcode's own OS-level containment (§14), not anything Pi guarantees by default.
- **Third-party extension dependencies (NEW):** Saltcode composes two community extensions — an **MCP client** (`pi-mcp-extension`) and a **sub-agent** extension (`pi-subagents`). This is the idiomatic path (Pi ships neither natively) and cuts build scope, but it adds supply-chain surface and version coupling. **Strategy (DD-15):** bundle them (`dependencies` + `bundledDependencies`) and take updates via `pi update --extensions` / explicit git re-pin — *not* a hard fork, which would kill updates. All Saltcode behavior lives in our own layer (skills, `agents/*.md`, prompts, `mcp.json`), so upstream stays pristine. Both are pinned to a reviewed version, depend on a *capability contract* (MCP-over-stdio bridging; isolated-context spawn with per-agent model/thinking/tools) so they're substitutable, and are kept as optional git submodules under `vendor/` purely as an audit/patch escape hatch; any internal fix is sent upstream first (PR). Both require trust review like any Pi package.
- **Auto-advance (Full Auto) vs Trade B (§10.1):** the checkpoint system's regression gate narrows Trade B but does not close it. In Full Auto there is no integration review, so a test-invisible composition bug ships silently — use Full Auto only with strong regression coverage or a low-integration-risk backlog; use Hybrid (deferred review + rollback) for real work. A regression failure outside a task's scope is treated as the Trade-B signal → FLAG HUMAN, not auto-fixed.
