# **AI-DRIVEN CODING FRAMEWORK — Pi-EXTENSION ADDENDUM (ASCII GRAPH, v9)**

> [!NOTE] VERSION LOG:
> - v5 targeted: Tier-1 risk list + Clarity/consistency list + Tradeoff re-survey
> - v6 pinned the local model + hardware target (RTX 3060 12GB / 32GB DDR5 / R7 7700)
> - v7 splits Phase 2 into 2 local tiers: Qwen3.5-9B-MTP default + Qwen3.6-35B-A3B-MTP escalation
> - v8 14-flaw audit + BIFAI-NET transplants (stability confidence, PCD cache, calibration)
> - v9 RE-HOSTS Saltcode as a Pi Coding Agent PACKAGE (extension + skills + prompts + Python backend). The "harness" is no longer standalone — Pi is the interface and the agent loop; Saltcode adds discipline on top. Every control point now maps to a real Pi API (grounded in pi.dev/docs/latest).
    
> THIS DOCUMENT IS AN ADDENDUM to `Proposal_Pi_v8.md`, not a replacement. The source of truth is: v8 (full framework design) + this file (v9, Pi-extension hosting). Read v8 first for the framework internals it still owns in full: the 7-agent roster + roles, the typed-contract field maps, the Evaluator's four checks, the static-gate strength table, the cost model, Tradeoffs A–E, the BIFAI-NET transplants, and the v1–v8 changelog (14 fixes + 3 transplants + the v6→v8 restorations). This file (v9) covers ONLY the shift to a Pi Package: the host/bridge/backend architecture, the harness→Pi-API map, the reframed main flow, the v9 changelog, and the v9 tradeoff additions. Sections marked "unchanged from v8" below are pointers, by design — v8 is carried alongside, not duplicated here.
>
> **Two exceptions to "read v8 for the framework":**
> - **The Phase-2 pipeline is AMENDED by v9,** not merely re-hosted. v8 [3.6] ends at `apply diff to LIVE repo → next task`; v9 inserts a **regression gate + checkpoint commit** after `apply_live` (see *[ADDED in V9 — checkpoint system]*, and `design.md` §10.1). Read v8 for the five per-task gates; read v9/`design.md` for what happens after they pass.
> - **Implementation-level detail restored by the v9 audit lives in `design.md`, not here** — the Saltnitor run-profile table + flags (§6), provider failover (§5.4), off-peak scheduling (§18), RTK compression (§5.2), and the prefix-cache economics (§15). v9 records the *decisions*; `design.md` is the implementable blueprint.

---
# **[0] HAPPY PATH (read this first — plain English)**

You are in Pi (the terminal agent). Saltcode is installed as a Pi Package. Two ways to drive it:

- **CONVERSATIONAL — just talk.** The loaded Saltcode skills make Pi behave as the 7 agents; you steer every step.
    
- **AUTONOMOUS — type /sprint "add rate-limiting".** The Saltcode extension drives the whole pipeline and pauses ONLY at the four human decisions.
    

Under the hood, `/sprint` runs:

- _PHASE 1 (frontier API via Pi providers, fires ONCE):_ Scout maps the repo from symbols only, Architect writes `design.md`, Planner derives `tasks.json`, Test Intent writes acceptance tests, Evaluator proves the plan is complete AND legal. Output = typed JSON on disk. Spec locks. (~$0.001–0.004.)
    
- _PHASE 2 (local MoE via Saltnitor provider, loops, $0 API):_ Builder implements ONE task against the tests (reading only files_affected). A diff-format check, a sandboxed apply, a millisecond static gate, and a test runner all reject bad code BEFORE the Auditor sees it. Auditor judges faithfulness via MULTI-PASS STABILITY (N=3; a behavioral confidence signal that works on 7B models). Pass → apply diff to live repo, next task. Fail → retry (Tier-A sub-cap 2, then escalate to Tier B; total budget 3).
    
- You review the diff and ship. Logical cross-task integration is the one thing the machine does NOT guarantee — that check is yours (Trade offs C/D).
    

The human never leaves Pi. No second tool, no separate CLI/TUI.

---
# **[0.5] ARCHITECTURE — SALTCODE AS A PI EXTENSION (the v9 shift)**

**Pi (the HOST)** owns: the agent loop, tool dispatch, the session store (JSONL), the native TUI, provider auth, conversation compaction, the model registry, and thinking control. Saltcode does **NOT** re-implement any of these.

**Saltcode is a Pi PACKAGE with three parts:**

```text
Pi (terminal agent — the thing you talk to)
│
├── saltcode  (Pi Package: `pi install npm:@laz/saltcode`)
│   ├── extensions/saltcode.ts   ── THE BRIDGE (TypeScript)
│   ├── skills/      ── the 7 agents + compactor (+6 cookbooks), already written
│   └── prompts/                 ── /sprint /phase1 /phase2 /review templates
│
└── saltcode-backend  (Python, `pip install saltcode-backend`)
    ├── contracts · memory(LanceDB) · static_gate · sandbox · stability · MCP
    └── tools/  = CLI entrypoints, one per capability, called via pi.exec()
```

**THE HARNESS → Pi API MAP (every old control point, grounded in Pi's docs):**

| **Old "harness" component**            | **New Pi mechanism**                                                                                                                                    |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Provider management (LLMClient)**    | `pi.registerProvider(DeepSeek/Qwen/Saltnitor)`                                                                                                          |
| **Model Tier Selector**                | `pi.setModel(model)` (false ⇒ no API key)                                                                                                               |
| **Thinking-Mode Gate (ON/OFF)**        | `pi.setThinkingLevel(off\|minimal\|low\|medium\|high\|xhigh)` ── now GRADED, not binary                                                                 |
| **Tool/MCP Broker**                    | per-agent tool allowlist (sub-agent frontmatter) + `pi.on("tool_call")` block `{ block:true,reason }` + OVERRIDE built-in write/edit/bash → container   |
| **Write-Path Allowlist (no tests/**)** | `pi.on("tool_call")` block                                                                                                                              |
| **Command Allowlist**                  | `pi.on("tool_call")` block + backend re-check                                                                                                           |
| **LSP/AST MCP consumption**            | `mcp.json` + an MCP CLIENT EXTENSION (`pi-mcp-extension`) — Pi ships no MCP client; bridged tools stay native Pi tools, so `tool_call` still gates them |
| **DAG Orchestrator + multi-agent**     | a SUB-AGENT EXTENSION (`pi-subagents`): each agent runs in an ISOLATED context; `/sprint` handler spawns them serially (NOT `sendUserMessage`)          |
| **Phase-Gate Controller**              | the `/sprint` handler (auto-advance on pass)                                                                                                            |
| **Retry/Loop Budget Tracker**          | extension state via `pi.appendEntry()`                                                                                                                  |
| **Per-agent clean context**            | sub-agent isolation (one task per context, free) + `before_agent_start` (assemble prefix + prompt)                                                      |
| **Prefix Cache assembly**              | `before_agent_start` returns `{ systemPrompt, message }`                                                                                                |
| **Conversation compaction**            | `pi.on("session_before_compact")` preserve HARD CONSTRAINTS                                                                                             |
| **Heavy backend calls**                | PERSISTENT DAEMON (stdio/socket); cold `pi.exec` = fallback (amortize pydantic/LanceDB imports)                                                         |
| **CLI + TUI (Typer/Rich/Textual)**     | Pi native TUI: `ctx.ui.setWidget`/`setStatus`/`notify`/`confirm` + slash commands                                                                       |
| **Connectivity + state persistence**   | `session_start` replays `ctx.sessionManager` entries; backend connectivity probe                                                                        |
**WHY THIS IS BETTER, NOT JUST DIFFERENT:**

- Pi already solved provider auth, streaming, retries, session persistence, and a good terminal UI. Re-using them shrinks Saltcode to its actual value: spec-driven discipline, typed gates, security containment, stability auditing.
    
- Thinking is now graded (off…xhigh), so the Architect can run "high" while the JSON agents run "off" — finer than the old binary gate.
    
- `tool_call` blocking is the documented mechanism for permission gates and path protection — exactly the write-path/command allowlist + scoped-read broker.
    
- Tool `params` are TypeBox schemas (not hand-rolled JSON Schema), with StringEnum for Google-API-compatible enums.
    
- Compose, don't build: Pi ships neither MCP nor sub-agents, but its ECOSYSTEM does (pi-mcp-extension, pi-subagents). Depending on them — against a capability contract — is more idiomatic and far less code than hand-rolling an MCP bridge or a multi-agent orchestrator, and sub-agent isolation gives "one task per context" for free while IMPROVING per-agent prefix caching.
    

**WHAT STAYS THE SAME (carried verbatim from v8, see [4]–[7], [10], [11]):**

The 7-agent roster + roles, the typed JSON contracts, the 5-gate Phase-2 pipeline, the stability-based confidence (BIFAI-NET), the PCD-adaptive semantic cache, the measured-then-fixed threshold protocol, the security-containment requirements, the Evaluator's four checks, the retry budget (shared ≤3, Tier-A sub-cap 2), the spec_defect feedback channel, the prefix-cache structure, and the cost model.

---
# **[1]–[2] GLOSSARY & TIER POLICY (unchanged from v8, with a thinking-level upgrade)**

- **GLOSSARY (TASK / SPRINT / SESSION):** unchanged from v8 §1. A SESSION is now a Pi session, kept warm so the prefix cache survives across its sprints.
    
- **TIER POLICY (base × session modifier):** unchanged from v8 §2, but the thinking gate maps to Pi's graded levels:
    

Plaintext

```
    Scout / Planner / Test Intent / Compactor → thinking "off"
    Architect                                 → thinking "high"
    Evaluator                                 → "off", raised to "medium"+ only 
												when re-invoked after a fail
    Auditor                                   → "off", raised to "medium"+ only 
											    on retry / gaming_suspected
```

- Online tiers map to Pi providers (DeepSeek Flash/Pro). Offline overrides ALL Phase-1 agents to a Tier-B Saltnitor model via `pi.setModel`.
    

---
# **[3] MAIN FLOW (re-framed: Pi host + Saltcode extension + backend)**

```
                      👨‍💻 HUMAN  ── talks to Pi, or runs /sprint
─────────────────────────────────────────────────────────────────────────────
                                     | (1) Goal / conversation / manual resume
                                     v
+-------------------------------------------------------------------------------+
|                                  Pi (HOST)                                    |
|  agent loop · tool dispatch · session JSONL · native TUI · provider auth ·    |
|  model registry · thinking control · conversation compaction                  |
+-------------------------------------------------------------------------------+
                                     |  Pi Package
                                     v
+-------------------------------------------------------------------------------+
|              SALTCODE EXTENSION  (extensions/saltcode.ts) — THE BRIDGE        |
|  registerProvider(DS/Qwen/Saltnitor)     on(tool_call) → allowlist + broker   |
|  setModel / setThinkingLevel (per agent) override built-ins → container       |
|  before_agent_start → prefix + prompt    appendEntry → budget/sprint/task     |
|                                                        state                  |
|  registerTool → backend DAEMON           on(session_before_compact) → keep HC |
|  registerCommand /sprint /review         ctx.ui widgets + the 4 confirms      |
|         /status /cost                    spawn sub-agents (isolated ctx)      |
|  deps: MCP-client ext (mcp.json) · sub-agent ext (agents/*.md)                |
+-------------------------------------------------------------------------------+
           |                                                      |
   (2) spawns Phase-1 / Builder                         (3) calls backend tools
       sub-agents (isolated ctx,                            via DAEMON
       model+thinking per agent)                            (pi.exec = fallback)
           |                                                      |
+----------v--------------------+              +------------------v-------------+
| 🔀 CACHE LADDER + ROUTING     |              | 🐍 SALTCODE BACKEND (Python)   |
| (in the /sprint handler)      |              |  contracts (pydantic+enforcer) |
|  connectivity → online/offline|              |  memory (LanceDB: spec/semantic|
|  1 exact spec-cache hit?      |              |    /notes/skills, PCD)         |
|  2 semantic hit (PCD-adaptive |              |  static_gate + test_runner     |
|     Architect confirm)?       |              |  sandbox(bwrap/Docker/firejail)|
|  3 miss → fire Phase 1        |              |  stability (N-pass measure)    |
|  (saltcode_scope_probe /      |              |  LSP/AST MCP + scoped-read     |
|   saltcode_cache_lookup)      |              |  tools/ = CLI entrypoints      |
+----------+--------------------+              +------------------+-------------+
           |                                                      |
  ══════════════════════════════════════════════════════════════════════════════
  ║          PROVIDER-AGNOSTIC CONTRACT BOUNDARY  (unchanged)                  ║
  ║  All Phase-1 output = typed JSON files on disk under .saltcode/.           ║
  ║  Swap DeepSeek → any provider or local MoE without touching Phase 2.       ║
  ══════════════════════════════════════════════════════════════════════════════
           |
   [PREFIX CACHE — assembled in before_agent_start, structure unchanged]
     1. System Prompt (active agent)  (fixed)    → cached
     2. design.md                     (session)  → cached (may shift on re-loop)
     3. Atomic Notes (≤5, FROZEN)     (session)  → cached
     4. Per-call delta / new input    (per call) → billed
           |
           v
   PHASE 1 — PLANNING QUINTUPLET (unchanged §4 roster; now skills + extension routing)
     Scout → Architect → Planner → Test Intent → Evaluator
       Evaluator FOUR CHECKS (traceability, coverage, preservation, compliance)
       Gap routing: design_gap→Architect, plan_gap/constraint_violation→Planner
       Loop caps: Architect ≤2, Planner ≤3 → exceed → FLAG HUMAN (ctx.ui)
     LSP/AST MCP (local, AST-only) + scoped read_file broker — enforced TWICE:
       pi.setActiveTools removes the tool from agents that shouldn't have it, and
       pi.on("tool_call") hard-blocks any out-of-scope path (defense in depth).
           |
   (5) BREAKPOINT — Evaluator passes → spec locks → spec hash → Spec Cache →
       Phase-Gate fires automatically (the /sprint handler advances; no human action)
           |
           v
   PHASE 2 — EXECUTION PAIR (local, 2 tiers; pipeline unchanged §3/v8)
     tasks.json[current] → BUILDER (one task + AST + scoped files + spec) → diff
       → saltcode_diff_check     malformed → impl_fail, short-circuit
       → saltcode_sandbox_apply  (git worktree INSIDE a security container)
       → saltcode_static_gate    dirty → discard sandbox, short-circuit (budget)
       → saltcode_test_run       fail  → discard sandbox, short-circuit (budget)
       → AUDITOR + saltcode_stability (N=3; unstable+online → 1 Flash re-judgment)
           pass             → saltcode_apply_live → next task
           impl_fail        → Builder retry
           gaming_suspected → Builder retry w/ "no hardcoding"
           spec_defect      → re-run TEST INTENT w/ audit_result.detail (≤1; not a retry)
       SHARED budget ≤3, Tier-A sub-cap 2 → FLAG HUMAN on exhaustion
           |
   (6) SPRINT COMPLETE — human reviews the diff (esp. LOGICAL cross-task integration,
       Trade B), then ships. The review surface carries the "integration is YOUR
       check" notice. Arch change → re-enter the cache ladder first.
           |
           | Every 5 sprints (periodic)
           v
   🗜️ SPEC COMPACTOR (saltcode_compact_spec, Flash/thinking-off) — strips
      completed/resolved content, NEVER the ## HARD CONSTRAINTS block.
      (Distinct from Pi conversation compaction, handled in session_before_compact.)
```

---
# **[4]–[7] ROSTER · CONTRACTS · CACHE LADDER · STATIC-GATE STRENGTH**

> UNCHANGED from v8. The 7-agent roster and I/O (§4–§5), the typed contract map (`context_report.json`, `design.md`+HARD CONSTRAINTS, `tasks.json`, `task_spec`, `evaluator_report.json`, `audit_result.json`+stability), the cache lookup ladder (exact → semantic with PCD-adaptive confirmation → fire), and the per-language static-gate strength table (TS/Rust/Go HARD, Python MEDIUM, JS SOFT) all carry over verbatim. The only change is mechanical: the agents are now SKILLS, and the per-agent model/thinking/tool routing is applied by the extension.

---
# **[8] COST PROFILE (unchanged from v8; Pi surfaces it natively)**

> Phase 1 fires once/sprint; Phase 2 loops at $0 API. Flash-dominant sprint ≈ $0.001; fresh-project sprint (Architect+Evaluator on Pro) ≈ $0.002–0.004; worst case (2 Architect re-loops) < $0.02. A full Saltcode-complexity build is $0.28–$1.18 optimized (prefix caching dominates, ~74% off at 75% hit). Pi exposes per-turn usage (`message_end` / `ctx.getContextUsage`); the extension surfaces it in `/cost`. Cost-escalation triggers unchanged (Phase-1 mid-sprint, Scout reading raw bodies, `design.md` bloat, cold caches, Evaluator re-loops, faithfulness spikes).

---
# **[9] CHANGELOG — V9 (standalone harness → Pi extension)**

**[RE-HOSTED in V9]**

> [!WARNING] Entries below marked ⚠️ were **superseded or amended within v9 itself** by the architecture review — the corrections are in **[REVISED in V9]**. They are annotated in place rather than silently left standing, because an un-annotated stale entry is exactly the defect the v8 `[RESTORED in V6 → V8]` group had to fix six versions late. **Read the ⚠️ note before trusting the entry.**

- Saltcode is a Pi PACKAGE (extension + skills + prompt templates + Python backend), installed via `pi install`. Pi is the interface and the agent loop; the standalone Typer CLI + Textual TUI are REMOVED.
    
- LLMClient (provider management) → `pi.registerProvider`(DeepSeek/Qwen/Saltnitor). Saltnitor is API:"openai-completions" at 127.0.0.1:8765/v1; an async factory can fetch /v1/models. Provider auth/streaming/retries are now Pi's job.
    
- Model Tier Selector → `pi.setModel(model)` (returns false if no API key → the extension falls back per policy).
    
- Thinking-Mode Gate UPGRADED from binary ON/OFF to Pi's GRADED levels (off|minimal|low|medium|high|xhigh) via `pi.setThinkingLevel`. Architect "high"; JSON agents "off"; Evaluator/Auditor raised only on re-invoke/retry.
    
- Tool/MCP Broker + Write-Path Allowlist + Command Allowlist → a single `pi.on("tool_call")` handler that blocks `{ block:true, reason }` (`tests/` writes, non-allowlisted commands, out-of-scope scoped reads), plus `pi.setActiveTools` per agent for defense in depth. `event.input` is mutable (arg patching); `isToolCallEventType` narrows built-in tools. — ⚠️ ***AMENDED** — see **[REVISED in V9]**: per-agent tool access now comes from the sub-agent definition's allowlist, and Pi's built-in `write`/`edit`/`bash` are **overridden** into the container (gating alone left an uncontained side door in interactive mode).*
    
- DAG Orchestrator + Phase-Gate → the `/sprint` command handler (`pi.registerCommand`) drives turns and auto-advances on Evaluator pass. — ⚠️ *the original v9 draft drove turns via `pi.sendUserMessage` into one session; **SUPERSEDED** — see **[REVISED in V9]** below: agents are now spawned as isolated sub-agents.*
    
- Retry/Loop Budget Tracker + sprint/task state → `pi.appendEntry(customType,data)`, replayed from `ctx.sessionManager.getEntries()` at `session_start`. State does NOT enter the LLM context and survives `/resume`.
    
- Context Compactor (per-agent context) → `before_agent_start` assembles the prefix (system + `design.md` + frozen notes) and injects the active agent's prompt, while `pi.setActiveTools` scopes the toolset to one task's needs.
    
- Conversation compaction → `pi.on("session_before_compact")` preserves the active HARD CONSTRAINTS block in any Pi summary (distinct from the on-disk Spec Compactor, which still runs every 5 sprints and never strips constraints).
    
- CLI/TUI → Pi native TUI (`ctx.ui.setWidget`/`setStatus`/`notify`/`confirm`). The four human decisions are Pi confirms; the "supervised" dashboard uses `ctx.ui.custom` guarded by `ctx.mode==="tui"`.
    
- The 7 agents are now SKILLS (markdown; already written) + extension routing. 8 custom Saltcode skills + 6 community cookbooks (python-pro, mcp-builder, test-driven-development, systematic-debugging, agent-tool-builder, git-pushing). — ⚠️ ***AMENDED** — see **[REVISED in V9]**: the skills are now wrapped as **sub-agent definitions** (`agents/*.md`, model/thinking/tools in frontmatter) and **preloaded** into each agent's prompt; +3 new skills (`saltcode-lsp-usage`, `saltcode-delegation`, `saltcode-checkpoint-ops`).*
    
- Tool parameter schemas are TypeBox (Type.Object), with StringEnum from @earendil-works/pi-ai for Google-API-compatible enums (NOT hand-rolled JSON Schema, which the v8-era brief assumed).
    
- Security containment follows Pi's documented guidance ("real isolation comes from the OS/VM boundary"; Gondolin pattern). Saltcode keeps its OWN OS-level containment (bwrap primary; Docker/firejail fallback) invoked by the backend via `pi.exec`; the gate/test/sandbox tools route execution into the container. The whole Pi process MAY additionally run under Gondolin/Docker for defense in depth, but Saltcode's own containment is primary.
    
- *NEW operational caveat:* Pi packages run with FULL system permissions; install requires project trust (`project_trust` / `ctx.isProjectTrusted()`), and the package + Python backend must be reviewed before install.
    
- *Distribution:* `pi install npm:@laz/saltcode` or `git:…`; project-local with `-l`; the Python backend is a separate `pip install saltcode-backend`. Pi runtime packages go in `peerDependencies` as "*" and are not bundled.
    

**[REVISED in V9 — architecture-review fixes]**

- MULTI-AGENT is now a SUB-AGENT EXTENSION dependency (pi-subagents), not `sendUserMessage` into one session. Each agent (Scout/Architect/Planner/Test Intent/Evaluator/Builder) runs in an ISOLATED context → "one task per context" holds by construction; the Auditor's N-pass judgment stays a backend tool.
    
- LSP/AST is CONSUMED via an MCP CLIENT EXTENSION (pi-mcp-extension) declared in `mcp.json` — Pi ships no MCP client, so the earlier "Pi consumes the MCP server" was wrong. Bridged tools are native Pi tools, so `tool_call` gating + the scoped-read broker still apply.
    
- Pi's built-in `write`/`edit`/`bash` are OVERRIDDEN into the security container (same-name registration), closing the interactive-mode side door around containment.
    
- The Python backend runs as a PERSISTENT DAEMON (stdio/socket); cold `pi.exec` per gate (Python + pydantic/LanceDB import each call) is now only a fallback.
    
- The 74%-prefix-cache saving is downgraded to a MODEL, not a measurement: Pi controls the serialized payload prefix, so it must be verified via `before_provider_request` before the cost figures are trusted. Sub-agent isolation helps (per-agent-type stable prefixes).
    
- Dependencies are BUNDLED + VERSIONED (`dependencies` + `bundledDependencies`), not forked: updates ride `pi update --extensions` (semver) or an explicit git re-pin, and all Saltcode customization lives in OUR layer (skills, `agents/*.md`, prompts, `mcp.json`) so upstream stays pristine. They're substitutable against a capability contract; an optional `vendor/` submodule is an audit/patch escape hatch only (PR-first upstream). Both require trust review like any Pi package.
    
- Agent skills are PRELOADED into each sub-agent's prompt (not left to Pi's read-tool auto-discovery, which locked-down agents can't trigger). Three new skills target the adopted extensions: `saltcode-lsp-usage`, `saltcode-delegation`, `saltcode-checkpoint-ops`.
    

**[ADDED in V9 — checkpoint system + auto-advance]**

- The Phase-2 loop can auto-advance the whole `tasks.json` through per-task CHECKPOINTS. A checkpoint = a git commit + a state snapshot (`pi.appendEntry` "saltcode:checkpoint"), created only on a ROBUST pass: the 5 per-task gates + `apply_live` + a full-suite REGRESSION gate on the integrated live tree. The loop never advances past an un-checkpointed task.
    
- Two run modes (`auto_mode`, default off): HYBRID runs the list unattended but ends with a mandatory cumulative-diff review + rollback to any checkpoint; FULL AUTO runs unattended and surfaces ONLY on a stop condition (budget / un-routable regression / FLAG HUMAN), trading away the integration review. Decision 1 (approve the plan) still gates the launch in both; pushing stays explicit (`auto_push` default false).
    
- Resumable: `session_start` matches the latest checkpoint's commit_sha to git HEAD and resumes at the next task (mismatch → FLAG HUMAN). Rollback: `/rollback` → `git reset --hard` + rewind state, logged. (design §10.1, REQ-CKP-*.)
    

**[KEEP from V8 — framework internals unchanged]**

- 7-agent roster + roles (Scout/Architect/Planner/Test Intent/Evaluator/Builder/Auditor + Compactor) ✅
    
- Typed JSON contracts + Output-Length Enforcer ✅
    
- 5-gate Phase-2 pipeline (diff format → sandbox → static → test → auditor) ✅
    
- Stability-based confidence (N=3, behavioral) + measured-then-fixed protocol ✅
    
- PCD-adaptive semantic cache + scope-fingerprint spec-cache key ✅
    
- Evaluator four checks + gap routing + loop caps (A≤2/P≤3) ✅
    
- Retry budget (shared ≤3, Tier-A sub-cap 2) + spec_defect feedback ✅
    
- Prefix-cache structure + Atomic Notes frozen for the session ✅
    
- 2-tier local serving (Tier A Qwen3.5-9B / Tier B Qwen3.6-35B-A3B) via Saltnitor ✅
    
- LSP/AST MCP (local, AST-only) crown jewel + scoped read broker ✅
    
- Cost model + optimization levers (prefix caching dominates) ✅
    

---
# **[10] TRADEOFF NOTES (v8 set carried forward; v9 additions)**

> All v8 tradeoffs (A–E) STAND unchanged: logical cross-task integration is the human's job (Trade B); anti-gaming Flash escalation breaks $0/air-gap on those calls (C1); stricter Evaluator → more loops + variable cost (C2); spec immutability shifts burden to Test Intent (C3); scope-fingerprint key lowers hit rate (C5); internet required for online Phase 1, never send raw source, keep sessions long, 5/7 agents depend on the API mitigated by the typed boundary + local Tier-B (D).

── **NEW IN V9** ──

- **V9-1 PI AS A DEPENDENCY.** Saltcode now depends on Pi's extension API surface. Mitigation: the API used is small, documented, and stable; the backend (the hard part) is framework-agnostic Python callable via `pi.exec` from any host, so a future re-host is cheap.
    
- **V9-2 TRUST / PERMISSIONS.** Pi packages run with full system permissions; a malicious package is dangerous. Mitigation: the extension is open + small; the backend is reviewable; project trust is checked before Phase 2; the real execution risk (generated code) is contained by Saltcode's own OS-level sandbox regardless of Pi's permissions.
    
- **V9-3 TWO COMPACTIONS TO KEEP STRAIGHT.** Pi compacts the CONVERSATION (`session_before_compact`); Saltcode compacts the on-disk DESIGN (Spec Compactor). Both must preserve HARD CONSTRAINTS. Mitigation: the on-disk `design.md` is the durable source of truth; the conversation hook only needs to avoid losing constraints mid-sprint.
    
- **V9-4 STABILITY LOOP COST.** N local Auditor passes per task run against Saltnitor directly from the backend (fast, self-contained), not as N Pi round-trips. Online Flash re-judgment on instability is one extra Pi turn, orchestrated by the extension. Negligible vs Builder generation time (Trade E1 stands).
    
- **V9-5 AUTO-ADVANCE vs TRADE B.** Auto-advancing through checkpoints does NOT make logical cross-task integration safe — it is the same Trade B. The regression gate (full suite per checkpoint) NARROWS it (catches anything a test covers) but cannot close it; a test-invisible composition bug still commits. HYBRID keeps the integration review (deferred to end-of-run) + rollback and is the safe default; FULL AUTO removes it and is appropriate only with strong regression coverage or a low-integration-risk backlog. A regression failure on files OUTSIDE the task's scope is treated AS the Trade-B signal → FLAG HUMAN, not auto-fixed (the Builder is scoped out of those files anyway).
    
- **V9-6 THIRD-PARTY EXTENSION DEPENDENCIES.** Composing pi-mcp-extension + pi-subagents is idiomatic and cuts build scope, but adds supply-chain surface and version coupling. Mitigation: depend on a CAPABILITY CONTRACT (MCP-over-stdio tool bridging; isolated-context spawn with per-agent model/thinking/tools), keep the implementations substitutable, and vendor one into the package if a dependency stalls. The hard part (the Python backend) stays framework-agnostic, so the blast radius of any single extension change is small.
    
- **V9-7 PROCESS-SPAWN OVERHEAD.** Sub-agents run as isolated pi processes/sessions; each spawn has startup cost. Fine for Phase-1 cadence (5 spawns/sprint). The HOT path (Auditor N-pass) deliberately stays in the backend daemon, not N subagent spawns; and the daemon removes the OTHER per-call cost (gate imports).
    

---
# **[11] MEASURED-THEN-FIXED THRESHOLD CALIBRATION (unchanged from v8 §11)**

> Unchanged. All configurable thresholds (Auditor stability, semantic cosine, PCD density bars) are MEASURED from a calibration set then FIXED in project config; first sprint uses conservative defaults marked `calibrated:false` (the extension warns at session open). Implemented by `saltcode_calibrate`; artifacts in `.saltcode/calibration/`. The protocol and rationale (small models can't self-assess confidence; stability is a behavioral signal) carry over verbatim.