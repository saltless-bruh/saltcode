# Saltcode — Requirements

Every line of code in this project is **strictly bound** by the requirements below. A requirement is satisfied only when its **acceptance criteria** are demonstrably met (by an automated test, a deterministic check, or a documented manual verification). Ambiguity is a defect: if an implementer cannot tell whether a requirement passes, the requirement — not the code — is fixed first.

**Architecture note (v9).** Saltcode is now a **Pi Package** (TypeScript extension + skills + prompt templates + a Python backend invoked via `pi.exec`). Where earlier versions said "the harness," responsibility now splits: orchestration, routing, access control, state, and UI belong to **the extension**; contracts, gates, sandbox, caches, and stability belong to **the backend**; the agent loop, tool dispatch, session store, and provider auth belong to **Pi**. Requirement IDs are unchanged; `tasks.md` references them.

**Conventions.**
- **SHALL** = mandatory. **SHOULD** = strongly recommended. **MAY** = optional.
- Acceptance criteria use EARS phrasing: *WHEN* `<trigger>`, the system SHALL `<response>`; *IF* `<condition>`, *THEN* `<behavior>`.
- **Trace** points to the source-of-truth proposal/design section.
- IDs are stable; `tasks.md` references them.

Areas: GLB (global) · EXT (Pi extension surface) · ORC (orchestration) · GATE (routing) · CACHE · MEM · PFX (prefix cache) · CON (contracts) · MCP · STAT (static gate) · MOD (model tiers) · SCT/ARC/PLN/TST/EVL/BLD/AUD/CMP (agents) · CAD (cadence) · FAIL · SEC · CAL · OPT · CKP (checkpoints).

---

## GLB — Global / cross-cutting

### REQ-GLB-001 — Typed contract boundary
Saltcode SHALL exchange all Phase-1 outputs **only** as typed JSON files on disk under `.saltcode/` (the Local Code-Wiki), never as in-memory provider-specific objects passed across the phase boundary.
- AC1: WHEN Phase 1 completes, every artifact in design §9 exists on disk and validates against its pydantic schema.
- AC2: IF the Phase-1 provider is swapped (DeepSeek → other provider → Saltnitor), THEN Phase 2 SHALL run unchanged with no edits to the backend's contracts or gates and no extension edits beyond `pi.registerProvider` config.
- **Trace:** design §3, §9.

### REQ-GLB-002 — JSON-only, schema-valid model output (Output-Length Enforcer)
Every agent that writes a JSON contract SHALL have its model output validated; non-conforming output SHALL be repaired-or-rejected, never persisted.
- AC1: IF model output is not parseable JSON or violates the schema, THEN `saltcode_validate_contract` SHALL attempt one bounded repair and, on repeated failure, raise a typed error (no partial/garbage file written).
- AC2: WHEN an agent is configured JSON-only, prose-only responses SHALL be rejected.
- **Trace:** design §9.

### REQ-GLB-003 — Privacy boundary (no raw source off-box)
No raw source file body SHALL be transmitted to any network provider at any point.
- AC1: WHEN Scout or Builder needs repo knowledge, it SHALL obtain it via the LSP/AST MCP (symbols/AST only) or, for the Builder only, the scoped `read_file` (local).
- AC2: IF any code path would place raw file contents into a provider request, THEN it SHALL be rejected by construction (the backend refuses bodies tagged as source; API-routed agents never receive raw bodies).
- **Trace:** design §13, Trade D.

### REQ-GLB-004 — Determinism of cacheable prefixes
Within one session, segments 1 (system prompt) and 3 (frozen notes) SHALL be byte-identical across ALL Phase-1 calls. Segment 2 (design.md) SHALL be byte-identical across all calls **within the same Evaluator pass**, but MAY change between passes when an Evaluator→Architect re-loop re-emits design.md.
- AC1: WHEN two Phase-1 calls occur within the same Evaluator pass, a byte-diff of their segments 1–3 SHALL be empty.
- AC2: WHEN an Evaluator→Architect re-loop occurs, segment 2 MAY differ from the prior pass (expected).
- AC3: On a fresh project's first sprint, the Scout call has no segment 2 (expected).
- **Trace:** design §15.

### REQ-GLB-005 — Schema versioning
Every JSON contract SHALL carry `schema_version`; readers SHALL reject unknown major versions.
- AC1: IF a contract's `schema_version` is unsupported, THEN the reader SHALL error rather than guess.

---

## EXT — Pi extension surface (NEW in v9)

### REQ-EXT-001 — Distributable Pi Package
Saltcode SHALL ship as a Pi Package installable via `pi install` (npm or git), declaring its resources in `package.json` under the `pi` key (`extensions`, `skills`, `prompts`) with the `pi-package` keyword.
- AC1: WHEN `pi install npm:@laz/saltcode` (or the git equivalent) runs, the extension, all skills, and all prompt templates SHALL load.
- AC2: The five Pi runtime packages (`@earendil-works/pi-coding-agent`, `@earendil-works/pi-ai`, `@earendil-works/pi-agent-core`, `@earendil-works/pi-tui`, `typebox`) SHALL be declared in `peerDependencies` with `"*"` and SHALL NOT be bundled.
- **Trace:** design §5.1, §17; Pi packages doc.

### REQ-EXT-002 — Provider registration
The extension SHALL register Phase-1 and fallback providers via `pi.registerProvider(name, config)`: DeepSeek V4 (online), an optional Qwen reasoning provider, and Saltnitor (`baseUrl http://127.0.0.1:8765/v1`, `api "openai-completions"`, models = router sections `A_STD`/`A_FOCUS`/`B`).
- AC1: WHEN the extension factory completes, the registered providers SHALL appear in `pi --list-models` / the model registry.
- AC2: Saltnitor model discovery MAY use an async factory that fetches `/v1/models`; IF Saltnitor is unreachable at startup, THEN registration SHALL degrade gracefully (the offline path remains available via configured Tier-B models).
- **Trace:** design §5.4, DD-2; Pi custom-provider doc.

### REQ-EXT-003 — Per-agent model + thinking routing
The extension SHALL set the active model (`pi.setModel`) and thinking level (`pi.setThinkingLevel`) per agent per the design §6 table before that agent's turn.
- AC1: WHEN an agent is invoked, the active model and thinking level SHALL equal the policy value for that agent and session state.
- AC2: IF `pi.setModel` returns `false` (no API key), THEN the extension SHALL surface the error and fall back per policy (e.g. offline Tier B) rather than proceeding silently.
- AC3: Thinking levels SHALL use Pi's scale (`off|minimal|low|medium|high|xhigh`); structured-JSON agents SHALL use `off`; the Architect SHALL use `high`; the Evaluator/Auditor SHALL raise the level only on re-invocation / retry per design §6.
- **Trace:** design §5.5, §6, DD-3.

### REQ-EXT-004 — Backend bridge via registered tools
The extension SHALL expose each backend capability as a tool registered with `pi.registerTool` whose `execute` invokes the backend through `pi.exec("python", ["-m", "saltcode.tools.<name>", ...])`. Tool `parameters` SHALL be defined with TypeBox; string enums SHALL use `StringEnum` from `@earendil-works/pi-ai`.
- AC1: WHEN a registered Saltcode tool runs, it SHALL call the corresponding `saltcode.tools.*` entrypoint and return its result as a Pi tool result.
- AC2: The registered tool set SHALL cover validate-contract, scope-probe, cache-lookup, diff-check, sandbox-apply, static-gate, test-run, stability, apply-live, compact-spec, calibrate, and scoped-read (design §5.3).
- **Trace:** design §5.3, DD-1.

### REQ-EXT-005 — Access control via tool_call interception + active-tool gating
The extension SHALL enforce all access control through a `pi.on("tool_call")` handler (returning `{ block: true, reason }`) and `pi.setActiveTools` per phase, NOT through a standalone broker process.
- AC1: WHEN the Builder emits a diff (or any tool would write under `tests/**`), the `tool_call` handler SHALL block it with a reason.
- AC2: WHEN a tool would run a command not on the allowlist, the handler SHALL block it.
- AC3: WHEN the Builder's scoped `read_file` targets a path not in the current `task.files_affected`, the handler SHALL block it; for Scout the scoped read SHALL be blocked entirely.
- AC4: `pi.setActiveTools` (top-level session) and each sub-agent definition's tool allowlist (REQ-EXT-012) SHALL further restrict each agent to role-appropriate tools (defense in depth).
- AC5: Pi's built-in mutating tools (`write`, `edit`, `bash`) SHALL be **overridden** by same-named tools that route execution through the security container, so no live built-in bypasses containment in any mode (see REQ-SEC-007).
- **Trace:** design §5.6, §13, §14, DD-4, DD-13.

### REQ-EXT-006 — Session state persistence
The extension SHALL persist sprint, budget, and per-task state with `pi.appendEntry(customType, data)` and rebuild it on `session_start` from `ctx.sessionManager.getEntries()`. This state SHALL NOT enter the LLM context.
- AC1: WHEN a session is resumed/reloaded, the extension SHALL reconstruct the latest sprint/budget/task state from custom entries.
- AC2: Budget counters SHALL be observable and SHALL never be silently reset mid-task/mid-sprint.
- **Trace:** design §5.7, DD-5.

### REQ-EXT-007 — HARD CONSTRAINTS survive conversation compaction
The extension SHALL handle `pi.on("session_before_compact")` so that the active `## HARD CONSTRAINTS` block is preserved in any Pi-produced conversation summary, or the compaction is cancelled when preservation cannot be guaranteed.
- AC1: WHEN Pi compacts the conversation (reason `manual`/`threshold`/`overflow`), the resulting summary SHALL still contain every active HARD CONSTRAINT.
- AC2: This requirement is distinct from the on-disk Spec Compactor (REQ-CMP-001); both SHALL preserve HARD CONSTRAINTS.
- **Trace:** design §11.8, DD-6; Pi compaction doc.

### REQ-EXT-008 — Native TUI surface (no separate UI)
The extension SHALL present progress and the four human decisions through Pi's native UI (`ctx.ui.setWidget`, `ctx.ui.setStatus`, `ctx.ui.notify`, `ctx.ui.confirm`, optionally `ctx.ui.custom` guarded by `ctx.mode === "tui"`). It SHALL NOT ship a separate CLI/TUI binary.
- AC1: WHEN a sprint runs, a widget SHALL show phase, current task, gate pipeline, and cost.
- AC2: Each of the four human decisions SHALL be surfaced as a Pi confirm/notify, not a separate process prompt.
- **Trace:** design §5.8, INTERACTION.md.

### REQ-EXT-009 — Orchestration commands + prompt templates
The extension SHALL register `/sprint`, `/review`, `/status`, and `/cost` via `pi.registerCommand`; the autonomous `/sprint` handler SHALL drive the pipeline using `pi.sendUserMessage` and pause only at the four decision points. Reusable workflow instructions SHALL ship as prompt templates (`sprint.md`, `phase1.md`, `phase2.md`, `review.md`) usable in interactive mode.
- AC1: WHEN `/sprint "<goal>"` runs, the pipeline SHALL execute end-to-end, pausing at Decisions 1–4 only.
- AC2: WHEN the user converses normally (no command), the loaded skills SHALL make Pi behave as the Saltcode agents (interactive mode).
- AC3: `/status` and `/cost` SHALL render current sprint state and accumulated cost from extension state and Pi usage.
- **Trace:** design §5.9, DD-10; Pi prompt-templates doc.

### REQ-EXT-010 — Provider failover chain
The extension SHALL implement a per-turn provider fallback chain: configured model → fallback list → Saltnitor (Tier B) → FLAG HUMAN.
- AC1: IF `pi.setModel()` returns `false`, the extension SHALL try the next provider in the fallback chain before flagging human.
- AC2: Every fallback event SHALL be logged (audit log).
- AC3: The fallback chain SHALL be configurable in `saltcode.toml` under `[providers.fallback]`.
- AC4: The chain SHALL be evaluated per-turn (a provider down for one turn MAY be retried the next).
- **Trace:** design §5.4.

### REQ-EXT-011 — Off-peak scheduling (optional)
The extension MAY support a `prefer_off_peak` flag that defers Phase-1 planning to off-peak hours for cost savings.
- AC1: IF the flag is `false` (default), Phase 1 SHALL fire immediately.
- AC2: IF the flag is `true` and the current time is outside the off-peak window, the extension SHALL prompt the human before proceeding ("run now at full price, or schedule for off-peak?") rather than deferring silently.
- AC3: Phase 2 (interactive) SHALL never be deferred regardless of the flag.
- **Trace:** COST_OPTIMIZATION.md lever 2; design §18.

### REQ-EXT-012 — Sub-agent orchestration (isolated context)
Each Phase-1 agent (Scout, Architect, Planner, Test Intent, Evaluator) and the Builder SHALL run in an isolated context window via a **sub-agent extension** (e.g. `pi-subagents`), each defined by a Markdown definition (model, thinking level, allowed tools). The Auditor's N-pass judgment SHALL remain a backend tool, NOT a spawned sub-agent.
- AC1: WHEN an agent runs, its context SHALL contain only its inputs (no sibling-agent or prior-task history) — satisfying REQ-BLD-001/REQ-ORC-006 by construction.
- AC2: The `/sprint` handler SHALL spawn Phase-1 agents serially in dependency order and enforce one Phase-1 fire per sprint (ordering is owned by code, not the LLM).
- AC3: Each agent's model, thinking level, and tool allowlist SHALL come from its definition + the §6 routing table.
- **Trace:** design §5.6a, §5.9, §7, DD-11.

### REQ-EXT-013 — MCP consumption via a client extension
The LSP/AST server SHALL be consumed through an **MCP client extension** (e.g. `pi-mcp-extension`) declared in `mcp.json` (stdio transport), NOT a hand-rolled bridge; Pi ships no native MCP client.
- AC1: The bridged tools SHALL appear as native Pi tools (`mcp_saltcode-lsp_*`) and SHALL therefore pass through `pi.on("tool_call")` and the per-agent allowlist.
- AC2: The scoped-read broker decision (`task.files_affected`) SHALL be enforced in Saltcode's `tool_call` handler, since the scope state lives in the extension.
- AC3: No bridged tool response SHALL contain a raw file body except the Builder's in-scope scoped read (REQ-MCP-001/004).
- **Trace:** design §5.3, §13, §16, DD-12.

### REQ-EXT-014 — Persistent backend daemon
The heavy Python backend SHALL run as a persistent daemon (stdio/socket) so interpreter + library imports amortize once; per-call `pi.exec` SHALL be a supported fallback.
- AC1: WHEN Phase 2 runs, backend tool invocations SHALL NOT each pay a fresh Python-interpreter + pydantic/LanceDB import cost.
- AC2: IF the daemon is unavailable, the extension SHALL fall back to per-call `pi.exec` (correctness preserved, slower).
- AC3: The daemon SHALL be started/stopped with the session (`session_start`/`session_shutdown`).
- **Trace:** design §16, §17, DD-14.

### REQ-EXT-015 — Third-party extension dependency strategy
The package SHALL depend on an MCP client extension and a sub-agent extension via **bundling** (`dependencies` + `bundledDependencies`, referenced through `node_modules/` paths), NOT a hard fork, and SHALL depend on a documented **capability contract** (MCP-over-stdio tool bridging; isolated-context spawn with per-agent model/thinking/tools) rather than a single implementation.
- AC1: The reference implementations (`pi-mcp-extension`, `pi-subagents`) SHALL be substitutable by any extension meeting the contract.
- AC2: Updates SHALL be taken via `pi update --extensions` (npm semver) or an explicit git re-pin; the specs SHALL NOT modify the extensions' source in-place.
- AC3: Any needed internal fix SHALL be sent upstream (PR-first); an optional git submodule under `vendor/` MAY be kept solely as an audit/patch escape hatch.
- AC4: The dependencies SHALL be documented as requiring trust review before install (REQ-SEC-006), and the README SHALL state the capability contract so a substitute can be validated.
- **Trace:** design §19, §17, DD-15; Pi packages doc.

### REQ-EXT-016 — Agent skills preloaded (not auto-discovered)
Each sub-agent definition SHALL **preload** its Saltcode skill directly into the agent's system prompt (via the sub-agent extension), rather than relying on Pi's description-match auto-loading. Three skills targeting the adopted extensions SHALL ship: `saltcode-lsp-usage`, `saltcode-delegation`, `saltcode-checkpoint-ops`.
- AC1: WHEN an agent runs with a restricted toolset (e.g. Scout, AST-only, no `read`), its skill SHALL still be present in its prompt (Pi otherwise injects skills only when the `read` tool is held).
- AC2: `saltcode-lsp-usage` SHALL cover querying symbols/outline before bodies, staying within `files_affected`, and never emitting raw source; it SHALL be preloaded for Scout and Builder.
- AC3: `saltcode-delegation` SHALL cover agent selection, serial-when-dependent spawning, and writing complete zero-context task prompts; `saltcode-checkpoint-ops` SHALL cover `/checkpoints`, `/rollback`, and reading regression failures.
- AC4: Skills SHALL be valid per Pi's Agent Skills standard (SKILL.md, `name`, `description`).
- **Trace:** design §5.6a, §7, DD-16; Pi skills doc.

---

## ORC — Orchestration

### REQ-ORC-001 — DAG-enforced single Phase-1 per sprint
The extension SHALL run Phase-1 agents as an ordered chain that permits **exactly one** Phase-1 fire per sprint.
- AC1: WHEN a sprint runs, the Scout→Architect→Planner→Test Intent→Evaluator chain SHALL fire at most once except for Evaluator-routed re-loops within caps (REQ-EVL-003).
- AC2: IF a second independent Phase-1 fire is attempted in the same sprint, THEN the extension SHALL refuse it (sprint state in `pi.appendEntry`).
- **Trace:** design §4, §8.

### REQ-ORC-002 — Phase-Gate is automatic
The extension SHALL advance from Phase 1 to Phase 2 with **no human action** once the Evaluator returns `status=pass`.
- AC1: WHEN `evaluator_report.json.status == "pass"`, THEN the spec locks, the spec hash is stored (REQ-CACHE-002), and Phase 2 begins automatically.
- **Trace:** design §8.

### REQ-ORC-003 — Thinking-mode policy
The extension SHALL apply the thinking-level policy exactly (REQ-EXT-003 AC3): Architect `high`; Evaluator raised only when re-invoked after a fail; Auditor raised only on retry/`gaming_suspected`; all others `off`.
- AC1: WHEN an agent is invoked, the thinking level set via `pi.setThinkingLevel` SHALL equal the policy value for that agent and state.
- **Trace:** design §6, §5.5.

### REQ-ORC-004 — Write-path allowlist
The extension SHALL enforce a write-path allowlist on every agent that emits file changes.
- AC1: WHEN the Builder emits a diff, any hunk that creates/edits/deletes a path under `tests/**` SHALL be blocked before application (`tool_call`, REQ-EXT-005).
- AC2: WHEN any agent writes, it SHALL only write the paths its role permits (design §7).
- **Trace:** design §5.6, §7.

### REQ-ORC-005 — Retry / loop budget tracker
The extension SHALL track per-task and per-sprint counters and enforce the caps in CAD/FAIL, persisting them as session state.
- AC1: Counters SHALL be observable and SHALL never be silently reset mid-task/mid-sprint.
- **Trace:** design §10, REQ-EXT-006.

### REQ-ORC-006 — One-task Builder context
The extension SHALL constrain a Builder turn's context to exactly one task plus its required inputs.
- AC1: WHEN the Builder is invoked, its context SHALL contain one task object, the task's AST slices, the bodies of ONLY the files in `task.files_affected`, and the task's spec — and SHALL NOT contain sibling tasks, design.md, or files outside `task.files_affected`. (See REQ-BLD-002.)
- AC2: Enforcement is primarily **sub-agent isolation** (REQ-EXT-012) — the Builder is spawned in a fresh context — reinforced by prefix assembly in `before_agent_start`, the sub-agent tool allowlist / `pi.setActiveTools`, and scoped-read blocking (`tool_call`).
- **Trace:** design §5.6a, §5, §7, Trade B.

### REQ-ORC-007 — Tool brokering (per-agent tool exposure)
The extension SHALL expose only role-appropriate tools to each agent (via the sub-agent definition's tool allowlist, REQ-EXT-012, plus `tool_call` as backstop).
- AC1: WHEN Scout runs, it SHALL have the LSP/AST tools (`where_is`, `find_references`, `outline`) and SHALL NOT have the scoped `read_file`.
- AC2: WHEN Builder runs, it SHALL have the LSP/AST tools AND the scoped `read_file` restricted to `task.files_affected` (REQ-EXT-005 AC3).
- AC3: All other agents SHALL NOT have `read_file`.
- AC4: No API-routed agent SHALL receive raw file bodies via any tool.
- **Trace:** design §13, §5.6.

---

## GATE — Routing

### REQ-GATE-001 — Connectivity check selects path
The extension/backend SHALL probe connectivity at session open and route Online→provider / Offline→Saltnitor Tier-B fallback.
- AC1: IF offline, THEN Phase-1 planning SHALL run on Tier B (REQ-MOD-003) and the Auditor faithfulness call SHALL stay local (REQ-AUD-002).
- **Trace:** design §11.1, §12.

### REQ-GATE-002 — Tier selection by session state
The extension SHALL select models per design §6: fresh project → Architect Pro + Evaluator Pro; amend/replan → Flash; offline → all Phase-1 on Tier B.
- AC1: WHEN the project is fresh, Architect and Evaluator SHALL be invoked on Pro; on amend/rerun they SHALL downgrade to Flash.
- **Trace:** design §6.

---

## CACHE — Cache ladder

### REQ-CACHE-001 — Ladder order (exact → semantic → fire)
On a new goal the extension SHALL evaluate the cache ladder strictly top-down (via `saltcode_cache_lookup`) and stop at the first hit.
- AC1: WHEN an exact Spec-Cache hit occurs, THEN `tasks.json` SHALL be reused with **zero API calls** and the ladder SHALL stop.
- AC2: WHEN only a semantic hit occurs, THEN a cheap Architect confirmation SHALL run (PCD-adaptive); on "yes" reuse, on "no" fall through to fire Phase 1.
- AC3: WHEN both miss, THEN Phase 1 fires.
- **Trace:** design §11.2.

### REQ-CACHE-002 — Spec-cache key includes scope
The Spec-Cache key SHALL be `sha256(normalized_goal + scope_fingerprint)`.
- **At store time** (after Phase 1): `scope_fingerprint = sorted(tasks.json[*].files_affected)`.
- **At lookup time** (before Phase 1): from (a) an explicit `--scope` argument, or (b) `saltcode_scope_probe` — a cheap enumeration of the workspace's source modules (a filesystem walk honouring the ignore set; no LSP session and no model call) producing a sorted module/file list. This probe is a tool call, NOT a Phase-1 fire. The probe SHALL NOT take the goal as an input: the goal is hashed into the key separately, so filtering the fingerprint by it would make two phrasings of one goal key differently for an identical tree.
  > *Amended 2026-07-28 (maintainer decision).* This clause previously read "a single `outline` MCP call producing a sorted module/file list", which cannot be implemented as written: `outline(file_path)` takes one file and returns that file's symbols, so no single call yields a repo-wide module list. Two readings of the old text gave different code **and** different cache keys, which is the ambiguity-is-a-defect case at the top of this document. The stated intent — cheap, a tool call, not a Phase-1 fire — is unchanged; only the mechanism description was wrong. The same phrasing appears in `docs/proposal/Proposal_Pi_v8.md` (SPEC-CACHE CHICKEN-AND-EGG FIXED) and has **not** been edited there; the proposal's intent sentence still holds.
- AC1: WHEN two goals share text but differ in scope, THEN their keys SHALL differ.
- AC2: WHEN `--scope` is provided, it SHALL be used directly (no probe).
- AC3: WHEN neither is available (empty repo), the fingerprint SHALL be empty and the key degrades to goal-only.
- **Trace:** design §11.2, Trade C5.

### REQ-CACHE-003 — Semantic threshold is calibrated + PCD-adaptive (BIFAI-NET v5.2)
The semantic cosine threshold SHALL be calibrated via the measured-then-fixed protocol (REQ-CAL-001) and SHALL never trigger reuse without the Architect-confirmation gate. The confirmation bar SHALL be **PCD-adaptive**.
- AC1: IF cosine ≥ threshold but Architect confirmation = "no", THEN reuse SHALL NOT occur.
- AC2: IF PCD is above the high-density bar, THEN the Architect confirmation MAY be cheap/skipped (if config allows).
- AC3: IF PCD is below the low-density bar, THEN require full confirmation or fall through to Phase 1.
- AC4: The cosine threshold and PCD bars SHALL be stored in project config with a `calibrated: bool` flag.
- AC5: WHEN no calibration data exists (first sprint), `semantic_cosine_threshold` SHALL default to `0.85` and PCD bars SHALL require full Architect confirmation (max skepticism), marked `calibrated: false` (mirrors REQ-AUD-005 AC1 for the Auditor's 0.5 default).
- **Trace:** design §11.2, §11.9, §15; v8 [11].

---

## MEM — Memory / Lightweight Brain

### REQ-MEM-001 — Atomic notes frozen for the session
The Lightweight Brain SHALL auto-RAG **at most 5** Atomic Notes at session open and FREEZE them for the session.
- AC1: WHEN a session is open, the note set SHALL NOT change until the session ends (REQ-GLB-004).
- AC2: The note count SHALL be ≤ 5.
- **Trace:** design §15.

### REQ-MEM-002 — Code-Wiki completeness
The Local Code-Wiki under `.saltcode/` SHALL contain all artifacts in design §15 after a successful sprint.
- AC1: WHEN a sprint completes, all of `context_report.json`, `design.md`, `tasks.json`, `tests/task_{id}_spec.*`, `evaluator_report.json`, and per-task `audit_result.json` SHALL exist and validate.

---

## PFX — Prefix cache

### REQ-PFX-001 — Front-loaded stable prefix
Every Phase-1 turn SHALL be assembled (in `before_agent_start`) as `[system | design.md | frozen notes | per-call delta]` in that order.
- AC1: Segments 1–3 SHALL be emitted byte-identically within a session (REQ-GLB-004); only segment 4 varies per call.
- **Trace:** design §15.

---

## CON — Typed contracts

### REQ-CON-001 — context_report.json
SHALL validate: `existing_patterns[]`, `relevant_files[]`, `constraints[]`, `anti_patterns[]`, `schema_version`.
- AC1: Missing/extra required fields SHALL fail validation.
- **Trace:** design §9.

### REQ-CON-002 — design.md HARD CONSTRAINTS block
`design.md` SHALL contain a parseable `## HARD CONSTRAINTS` H2 block containing **every** `context_report.constraints[*]` and `anti_patterns[*]` string verbatim.
- AC1: WHEN the parser reads design.md, it SHALL return the full constraint set; IF any string is missing, THEN the preservation check (REQ-EVL-001) SHALL fail.
- **Trace:** design §9.

### REQ-CON-003 — tasks.json
Each task SHALL have `id`, `description`, `files_affected[]`, `acceptance_criteria[]`, `depends_on[]`, `complexity ∈ {low,med,high}`.
- AC1: Invalid `complexity` or missing fields SHALL fail validation.
- AC2: `depends_on` SHALL reference existing task ids (no dangling deps).
- **Trace:** design §9.

### REQ-CON-004 — task_spec files
Test Intent SHALL emit exactly one `tests/task_{id}_spec.*` per task id, in the target language, before any Builder run for that task.
- AC1: WHEN Phase 2 starts a task, its spec file SHALL already exist.
- **Trace:** design §9.

### REQ-CON-005 — evaluator_report.json
SHALL validate `status ∈ {pass,gaps}`, `gaps[]` (each `{id, type∈{design_gap,plan_gap,constraint_violation}, detail, target∈{architect,planner}}`), `routing_summary`.
- **Trace:** design §9.

### REQ-CON-006 — audit_result.json
SHALL validate `task_id`, `status ∈ {pass,fail}`, `reason ∈ {pass,impl_fail,gaming_suspected,spec_defect}`, `next_action ∈ {next_task,builder_retry,test_intent_respec,flag_human}`, `detail`, `stability` (`n_passes ∈ int≥1`, `verdicts ∈ [string]`, `stability_score ∈ [0.0,1.0]`, `gac ∈ int≥0`).
- AC1: `reason=pass` IFF `status=pass`; `reason=spec_defect` SHALL set `next_action=test_intent_respec`.
- AC2: `stability_score` SHALL equal `1.0 - (verdict_changes / (n_passes - 1))`.
- AC3: `verdicts` length SHALL equal `n_passes`.
- **Trace:** design §9.

---

## MCP — LSP/AST server

### REQ-MCP-001 — AST-only tools
The MCP server SHALL expose `where_is`, `find_references`, `outline` and SHALL return symbols/ASTs only.
- AC1: No tool response SHALL contain a raw file body.
- **Trace:** design §13.

### REQ-MCP-002 — Local-only transport
The server SHALL bind to localhost and SHALL NOT transmit any repo content to a remote host.
- AC1: WHEN the server runs, it SHALL have no outbound network calls carrying repo data.
- **Trace:** design §13, Trade D.

### REQ-MCP-003 — Per-language backend
The server SHALL drive the correct language server (`pyright|tsserver|rust-analyzer|gopls`) per repo language.
- AC1: WHEN the repo is Rust, queries SHALL resolve via rust-analyzer.

### REQ-MCP-004 — Consumption via an MCP client extension
The MCP server SHALL be consumed through an MCP client extension declared in `mcp.json` (stdio), which registers its tools as native Pi tools; Saltcode SHALL NOT hand-roll an MCP bridge.
- AC1: WHEN Pi starts, the LSP/AST tools SHALL be registered as `mcp_saltcode-lsp_*` and callable by agents that hold them.
- AC2: The bridged tools SHALL be gated by `pi.on("tool_call")` and per-agent allowlists exactly as native tools (REQ-EXT-005/013).
- **Trace:** design §13, §16, DD-12.

---

## STAT — Static-analysis gate

### REQ-STAT-001 — Hard gate before Auditor (sandbox)
Every Builder diff SHALL pass the per-language static gate **before** the Auditor is called. The gate SHALL operate on a sandbox (git worktree / temp copy with the diff applied) inside a security container — never the live working tree.
- AC1: WHEN the gate is DIRTY, THEN the sandbox SHALL be discarded, the diff SHALL short-circuit back to the Builder with the lint reason, and the Auditor SHALL NOT be invoked.
- AC2: WHEN CLEAN, THEN the clean static report SHALL be passed to the test runner (REQ-STAT-004).
- AC3: The live working tree SHALL be unmodified by the static gate regardless of outcome.
- **Trace:** design §10, §14.

### REQ-STAT-002 — Per-language runners + strength
The gate SHALL run the tools in design §14 and record gate strength for the active language.
- AC1: TS→`tsc`+`eslint`; Rust→`cargo check`/`clippy`; Go→`build`+`vet`; Python→`pyright --strict`+`ruff`; JS→`eslint`.
- AC2: WHEN strength is SOFT, the Auditor escalation (REQ-AUD-002) SHOULD prefer Flash online.
- **Trace:** design §14.

### REQ-STAT-003 — Zero VRAM, bounded time
The gate and test runner SHALL use no GPU and SHALL run as bounded subprocesses with a configurable timeout.
- AC1: The gate and test runner SHALL not load any model.

### REQ-STAT-004 — Test runner before Auditor
After the static gate passes (CLEAN), the backend SHALL run the task's acceptance test on the sandbox using `test_runner_cmd`.
- AC1: WHEN tests FAIL, the sandbox SHALL be discarded, the diff SHALL short-circuit back to the Builder with test output, and the Auditor SHALL NOT be invoked. Counts against the shared budget.
- AC2: WHEN tests PASS, the results SHALL be forwarded to the Auditor alongside the clean static report.
- AC3: IF no `test_runner_cmd` is configured, the test-runner step SHALL be SKIPPED.
- **Trace:** design §10, §14.

### REQ-STAT-005 — Diff format validation
Before the sandbox apply, `saltcode_diff_check` SHALL validate that the Builder's output is a parseable unified diff (`git apply --check`).
- AC1: IF the output is not a valid unified diff, THEN it SHALL be treated as `impl_fail` and count against the shared budget (the Auditor is NOT called).
- AC2: One bounded repair (extracting a diff from a markdown fence) MAY be attempted, then reject.
- **Trace:** design §9, §10.

---

## MOD — Local model tiers

### REQ-MOD-001 — Tier A default
Builder/Auditor SHALL run on Tier A (Saltnitor `Qwen3.5-9B-MTP` UD-Q5_K_XL, in-VRAM) for low/med tasks.
- AC1: WHEN `task.complexity ∈ {low,med}` and no retry-cap breach, the active model SHALL be a Tier-A Saltnitor model.
- **Trace:** design §6.

### REQ-MOD-001b — A_FOCUS profile selection
The extension SHALL select profile `A_FOCUS` (256K ctx, thinking OFF) instead of `A_STD` (64K ctx) WHEN the estimated input token count for the current Builder/Auditor turn exceeds `a_focus_threshold` (configurable, default 32,768 tokens).
- AC1: WHEN estimated input ≤ threshold, the extension SHALL use `A_STD`.
- AC2: WHEN estimated input > threshold, the extension SHALL use `A_FOCUS` and set thinking to `off` (the VRAM triangle forbids thinking + 256K ctx simultaneously).
- AC3: The threshold SHALL be configurable in `saltcode.toml`.
- **Trace:** design §6 (VRAM triangle), §15 (local tiers).

### REQ-MOD-002 — Tier B escalation with explicit sub-cap
The active tier SHALL escalate to Tier B WHEN `task.complexity == high` (before the first attempt) OR WHEN Tier A exhausts its **sub-cap of 2 retries** within the shared budget.
- AC1: WHEN `task.complexity == high`, the first Builder attempt SHALL use Tier B.
- AC2: WHEN Tier A has failed 2 attempts, the 3rd (within the shared budget of 3) SHALL use Tier B. IF it also fails → FLAG HUMAN.
- AC3: WHEN a task starts on Tier B (complexity high), all 3 attempts are on Tier B; no further escalation.
- **Trace:** design §6, §10.

### REQ-MOD-003 — Offline: ALL Phase-1 agents on Tier B
WHEN offline, **all** Phase-1 agents SHALL resolve (via `pi.setModel`) to a Tier-B Saltnitor model. The design §6 online tiers apply only online.
- AC1: IF offline, THEN no DeepSeek/provider API call SHALL be made for any Phase-1 agent.
- AC2: IF offline, THEN the Auditor faithfulness call SHALL stay local (no Flash escalation, REQ-AUD-002).
- **Trace:** design §12.

### REQ-MOD-004 — Sequential roles, one resident
Builder and Auditor SHALL run sequentially so exactly one local model is resident; the active tier serves both.
- AC1: WHEN running Phase 2 via Saltnitor, a tier change SHALL be requested via `ensure` (REQ-MOD-005) and only one model SHALL be loaded at a time.
- **Trace:** design §14.

### REQ-MOD-005 — Serving via Saltnitor ensure (DD-2)
Tier residency SHALL be requested through the Saltnitor control API (`POST /v1/ensure`), with direct llama.cpp as fallback. The extension addresses tiers as Saltnitor models (router sections); the backend or extension SHALL `ensure` the section before inference.
- AC1: WHEN Tier B is required, profile `B` SHALL be ensured before inference; IF the oracle refuses (OOM), THEN the task SHALL FLAG HUMAN rather than crash the box.
- **Trace:** design §14, DD-2.

### REQ-MOD-006 — VRAM-triangle routing (Tier A)
The extension SHALL NOT request thinking + 256K ctx + MTP simultaneously on Tier A (12GB VRAM).
- AC1: WHEN `A_FOCUS` is selected (256K ctx), thinking SHALL be set to `off`.
- AC2: WHEN thinking is `high` or above, the profile SHALL be `A_STD` (64K ctx).
- AC3: MTP (`--spec-type mtp`) SHALL be opt-in via config flag (`mtp_enabled`, default `false`) and SHALL NOT be enabled on `A_FOCUS`.
- **Trace:** design §6 (VRAM triangle).

---

## SCT — Scout

### REQ-SCT-001 — Symbols-only input
Scout SHALL read only `file_tree` + LSP symbol exports; it SHALL NOT read raw file bodies.
- AC1: IF Scout attempts a scoped read, THEN the `tool_call` handler SHALL block it.
- **Trace:** design §7, §13.

### REQ-SCT-002 — Output is context_report.json
Scout SHALL write a schema-valid `context_report.json` populating `constraints[]` and `anti_patterns[]`.
- **Trace:** REQ-CON-001.

---

## ARC — Architect

### REQ-ARC-001 — design.md only; no tasks
Architect SHALL write `design.md` and SHALL NOT write `tasks.json` or any task list.
- AC1: IF Architect output contains a task list, THEN it SHALL be rejected.
- **Trace:** design §7.

### REQ-ARC-002 — Verbatim constraint carry-through
Architect's `## HARD CONSTRAINTS` block SHALL contain every `context_report` constraint and anti_pattern **verbatim**.
- AC1: A string-set equality check between `context_report.{constraints∪anti_patterns}` and the parsed HARD CONSTRAINTS block SHALL hold.
- **Trace:** design §7, §9.

### REQ-ARC-003 — Model/thinking
Architect SHALL run on Pro on fresh projects (Flash on amend) with thinking `high`.
- **Trace:** design §6, REQ-EXT-003.

---

## PLN — Planner

### REQ-PLN-001 — design.md as sole input
Planner SHALL read `design.md` only and SHALL NOT read `context_report.json`.
- **Trace:** design §7.

### REQ-PLN-002 — Output is tasks.json
Planner SHALL write schema-valid `tasks.json`, with acyclic `depends_on`.
- AC1: The dependency graph SHALL be a DAG (no cycles).
- **Trace:** design §9.

---

## TST — Test Intent

### REQ-TST-001 — Specs before code, one per task, with project context
Test Intent SHALL write one `tests/task_{id}_spec.*` per task before any Builder run, and SHALL NOT write implementation code. It reads `tasks.json` AND **project config** (`language`, `test_framework`, `test_runner_cmd`).
- AC1: Spec files SHALL use the project's configured test framework.
- AC2: On a re-spec triggered by `spec_defect`, Test Intent SHALL additionally receive `audit_result.detail` — the ONLY case where it reads more than `tasks.json` + project config.
- **Trace:** design §7, REQ-CON-004.

### REQ-TST-002 — Immutability
After emission, task specs SHALL be immutable to the Builder; a defective spec SHALL be fixed only by a Test-Intent re-run.
- AC1: WHEN `audit_result.reason == spec_defect`, THEN Test Intent re-runs (≤1) — the Builder SHALL NOT edit the spec (blocked by REQ-EXT-005).
- **Trace:** design §7, Trade C3.

---

## EVL — Evaluator

### REQ-EVL-001 — Four checks
Evaluator SHALL run all four: traceability, coverage, preservation, compliance.
- AC1: WHEN a context_report constraint is absent from the HARD CONSTRAINTS block, preservation SHALL fail.
- AC2: WHEN a task violates a constraint/anti_pattern, compliance SHALL fail.
- **Trace:** design §8.

### REQ-EVL-002 — Gap classification + routing
Each gap SHALL be typed and routed: `design_gap`→Architect; `plan_gap`→Planner; `constraint_violation`→Planner unless it conflicts with design.md → Architect. Any `design_gap` present ⇒ Architect loop.
- AC1: WHEN gaps include any `design_gap`, THEN the next loop SHALL target the Architect.
- **Trace:** design §8.

### REQ-EVL-003 — Loop caps then human
Loop caps SHALL be Architect ≤ 2, Planner ≤ 3 per sprint; exceeding either SHALL FLAG HUMAN with the gap report.
- AC1: WHEN a cap is exceeded, THEN the sprint SHALL halt with a human flag — no silent spinning.
- **Trace:** design §8, FAIL.

### REQ-EVL-004 — Model/thinking
Evaluator SHALL run on Pro on fresh projects (Flash otherwise); thinking raised only when re-invoked after a fail.
- **Trace:** design §6, REQ-EXT-003.

---

## BLD — Builder

### REQ-BLD-001 — One task per context, no carryover
Builder SHALL build exactly one task per turn with no state carried from a prior task.
- AC1: WHEN a task starts, the Builder context SHALL be reconstructed from disk (task + AST + spec) only.
- **Trace:** design §7, Trade B.

### REQ-BLD-002 — Inputs are task + AST + scoped files + spec
Builder SHALL read the current task object, AST via LSP/AST MCP, the **full bodies of ONLY the files in `task.files_affected`** via the scoped `read_file` tool, and the task spec — NOT files outside `task.files_affected`, NOT design.md, NOT sibling tasks.
- AC1: IF the Builder requests a file not in `task.files_affected`, THEN the `tool_call` handler SHALL block the read.
- **Trace:** design §7, §13.

### REQ-BLD-003 — tests/** not writable
Builder diffs SHALL never create/edit/delete anything under `tests/**`.
- AC1: WHEN a diff touches `tests/**`, THEN it SHALL be blocked before application (REQ-EXT-005 / REQ-ORC-004).
- **Trace:** design §7.

---

## AUD — Auditor

### REQ-AUD-001 — Faithfulness gate (heuristics + judgment)
Auditor SHALL run zero-cost heuristics then a judgment pass on every clean diff. Heuristics SHALL flag: return literals matching test fixtures; empty/throw-only bodies under test; branches keyed on known test inputs.
- AC1: WHEN a heuristic flag fires, THEN the verdict SHALL be `gaming_suspected` unless judgment clears it.
- **Trace:** design §9, §10.

### REQ-AUD-002 — Escalation policy (stability-based, BIFAI-NET v5.2)
The Auditor SHALL measure confidence via **multi-pass stability**, not self-assessment. `saltcode_stability` SHALL run the judgment N times (default N=3) with varied conditions (temperature jitter, evidence reordering) and compute `stability_score = 1.0 - (verdict_changes / (N-1))` and `gac`. The escalation threshold SHALL be set by the measured-then-fixed protocol (REQ-AUD-005), not guessed.
- AC1: IF offline, THEN no Flash call SHALL be made for the faithfulness judgment, even if stability is low.
- AC2: IF online AND `stability_score < auditor_stability_threshold`, THEN the judgment SHALL be re-run once on DeepSeek Flash and that verdict SHALL be final.
- AC3: IF online AND `stability_score >= auditor_stability_threshold`, THEN the majority local verdict stands.
- AC4: The `auditor_stability_threshold` SHALL be configurable and calibrated via REQ-AUD-005.
- AC5: Each stability pass SHALL use a distinct condition so verdicts are not trivially identical.
- **Trace:** design §9, §11.5, Trade C1.

### REQ-AUD-003 — Typed verdict + routing
Auditor SHALL emit `audit_result.json` (REQ-CON-006). `impl_fail`→Builder retry; `gaming_suspected`→Builder retry with "no hardcoding" reason; `spec_defect`→Test Intent re-spec (not a Builder retry); `pass`→next task.
- AC1: `spec_defect` SHALL NOT consume a Builder retry.
- **Trace:** design §10.

### REQ-AUD-004 — Cannot write code
Auditor SHALL only judge; it SHALL NOT emit code diffs.
- **Trace:** design §7.

### REQ-AUD-005 — Stability threshold calibration (measured-then-fixed protocol)
The `auditor_stability_threshold` SHALL be calibrated from benchmark data (known-good and known-gaming diffs), not guessed. The calibration produces a threshold that maximizes the F1 of "stable verdict = trustworthy verdict" on the calibration set.
- AC1: WHEN no calibration data exists (first sprint), the threshold SHALL default to 0.5 and the config SHALL mark it `calibrated: false`.
- AC2: WHEN calibration data is available, the calibration SHALL be run and the threshold updated and marked `calibrated: true`.
- AC3: The calibration set, measurement date, and distribution SHALL be documented in `.saltcode/calibration/`.
- **Trace:** design §11.9, BIFAI-NET v5.2 sim_floor protocol.

---

## CMP — Spec Compactor

### REQ-CMP-001 — Periodic compaction with deterministic constraint retention
`saltcode_compact_spec` SHALL run once per 5 sprints, stripping completed/resolved content while **never stripping any line from the `## HARD CONSTRAINTS` block**. HARD CONSTRAINTS are removed only by an explicit human edit.
- AC1: WHEN compaction completes, the `## HARD CONSTRAINTS` block SHALL be byte-identical to the pre-compaction version.
- AC2: The compactor SHALL run on Flash with thinking `off`.
- AC3: The compactor SHALL strip only content OUTSIDE the `## HARD CONSTRAINTS` block that relates to completed/obsolete work.
- **Trace:** design §11.7. (See also REQ-EXT-007 for conversation compaction.)

---

## CAD — Cadence rules

### REQ-CAD-001 — One Phase-1 fire per sprint. (= REQ-ORC-001.) **Trace:** design §4.
### REQ-CAD-002 — One task per Builder context. (= REQ-BLD-001.) **Trace:** design §4.
### REQ-CAD-003 — Session warmth
A Pi session SHALL keep the prefix warm across its sprints; the extension SHALL warn WHEN a cold start would discard cached prefix tokens.
- AC1: WHEN a session has been idle past the prefix TTL, THEN the extension SHALL surface a cold-cache warning (`ctx.ui.notify`).
- **Trace:** design §4, Trade D.

---

## FAIL — Failure handling & human flagging

### REQ-FAIL-001 — Shared per-task retry budget ≤ 3 with Tier-A sub-cap
Diff-format failures + static bounce-backs + test failures + `impl_fail` + gaming retries SHALL share a single per-task budget capped at 3. **Tier-A sub-cap = 2**: after 2 failures on Tier A, the 3rd attempt escalates to Tier B (REQ-MOD-002). If the task started on Tier B (complexity high), all 3 attempts are on Tier B.
- AC1: WHEN the combined count reaches 3 (regardless of tier), THEN the task SHALL FLAG HUMAN.
- AC2: WHEN the count reaches 2 on Tier A, the next attempt SHALL use Tier B.
- AC3: `spec_defect` SHALL NOT consume a retry.
- **Trace:** design §10.

### REQ-FAIL-002 — spec_defect re-spec ≤ 1 with Auditor feedback
A `spec_defect` SHALL trigger at most one Test-Intent re-spec, then resume. The re-invoked Test Intent SHALL receive `audit_result.detail` (REQ-TST-001 AC2).
- AC1: WHEN a second `spec_defect` would occur for the same task, THEN the task SHALL FLAG HUMAN.
- AC2: The Auditor's `detail` SHALL describe the specific defect.
- **Trace:** design §10, Trade C3.

### REQ-FAIL-003 — No silent failure
The extension SHALL never spin silently or under-cover silently; every cap breach SHALL produce an explicit human flag (`ctx.ui.notify`/`confirm`) with a report.
- **Trace:** design §8, §10.

### REQ-FAIL-004 — Human integration review surfaced
At Sprint Complete the extension SHALL present the diff for human review and SHALL explicitly state that logical cross-task integration is unverified.
- AC1: WHEN a sprint completes, the review surface SHALL include a "logical integration is YOUR check" notice.
- **Trace:** Trade B.

---

## SEC — Security containment

### REQ-SEC-001 — All code execution inside a security container
Every subprocess that executes Builder-generated code (static analysis, test runner, any tool on the sandbox) SHALL run inside a security container (bubblewrap, firejail, or Docker), spawned by the backend (invoked via `pi.exec`), with:
- Read-only host filesystem (outside the sandbox worktree).
- No network access (`--unshare-net` / `--network none`).
- No access to `$HOME`, `~/.ssh`, `~/.gnupg`, dotfiles, or credentials.
- Isolated PID namespace (`--unshare-pid`).
- Cgroup memory limit (default 2GB) and CPU limit (default 2 cores).
- Configurable time limit (default 60s); container killed on timeout.
- Automatic cleanup (`--die-with-parent` / `--rm`).
- AC1: WHEN the test runner executes code containing `os.system('rm -rf ~/')` or any destructive side effect, THEN the host filesystem SHALL be unmodified.
- AC2: WHEN executed code attempts network access, THEN the connection SHALL be refused.
- AC3: WHEN the container exceeds memory/time limits, THEN it SHALL be killed and treated as a test failure (counts against budget).
- **Trace:** design §10, §14. Pi guidance: real isolation comes from the OS/VM boundary (Gondolin pattern).

### REQ-SEC-002 — Command allowlist
Saltcode SHALL only execute whitelisted commands inside the container: `pytest`, `jest`, `cargo test`, `go test`, `pyright`, `ruff`, `tsc`, `eslint`, `cargo check`, `cargo clippy`, `go build`, `go vet`, `git apply`. Enforced in both the `pi.on("tool_call")` handler and the backend.
- AC1: IF a non-allowlisted command is attempted, THEN it SHALL be refused and logged (at the `tool_call` layer and the backend).
- AC2: The allowlist SHALL be configurable in project config.
- **Trace:** design §14, REQ-EXT-005.

### REQ-SEC-003 — Audit log
Every command Saltcode executes SHALL be logged in `.saltcode/audit_log.jsonl` with: full command line, working directory, container ID, exit code, timestamp, and SHA-256 hash of stdout+stderr.
- AC1: The audit log SHALL be append-only during a sprint.
- AC2: The log SHALL be human-readable (JSON lines).
- **Trace:** design §14.

### REQ-SEC-004 — Dry-run mode
The extension SHALL register a `--dry-run` flag (`pi.registerFlag`) that walks the entire pipeline but replaces all backend subprocess calls with logged no-ops.
- AC1: In dry-run mode, NO subprocess SHALL execute; every command that *would* run is logged with its full command line.
- AC2: In dry-run mode, NO file outside `.saltcode/` SHALL be modified.
- AC3: Dry-run output SHALL be reviewable (surfaced in Pi + stored in `.saltcode/dry_run_log.txt`).
- **Trace:** design §14.

### REQ-SEC-005 — Containment backend selection
The backend SHALL auto-detect the containment backend in order: bubblewrap (`bwrap`) → Docker → firejail. If none is available, the backend SHALL refuse to run Phase 2 (with a clear error) rather than falling back to uncontained execution.
- AC1: IF no containment backend is found, THEN Phase 2 SHALL exit with an error naming what to install (bubblewrap/Docker/firejail).
- AC2: The backend SHALL be overridable in config (`sandbox_backend: bwrap|docker|firejail`).
- **Trace:** design §14.

### REQ-SEC-006 — Package trust (Pi-specific, NEW)
Because Pi packages run with full system permissions, project-local Saltcode configuration SHALL only be honored for trusted projects.
- AC1: WHEN entering an untrusted project, the extension SHALL check `ctx.isProjectTrusted()` before reading project-local config or enabling Phase 2.
- AC2: The package and its Python backend SHALL be documented as requiring source review before install.
- **Trace:** design §19; Pi security/packages docs.

### REQ-SEC-007 — Built-in mutating tools contained in all modes (NEW)
Pi's built-in `write`, `edit`, and `bash` SHALL be overridden (same-name registration) with versions that execute inside the security container, so containment holds for both autonomous agents and the interactive human.
- AC1: WHEN the human (interactive mode) or any agent invokes `write`/`edit`/`bash`, execution SHALL route through the container (REQ-SEC-001), NOT the raw host.
- AC2: WHEN a diff/command would violate the write-path or command allowlist, it SHALL be blocked (REQ-EXT-005) regardless of which tool (built-in override or `saltcode_*`) was used.
- AC3: The built-in `read` MAY remain, feeding the scoped-read broker (REQ-EXT-005 AC3).
- **Trace:** design §5.6, §14, DD-13.

---

## CAL — Threshold calibration

### REQ-CAL-001 — Measured-then-fixed protocol for all configurable thresholds
All configurable thresholds (Auditor stability, semantic cosine, PCD density bars) SHALL be calibrated from benchmark data via the measured-then-fixed protocol (design §11.9), not guessed.
- AC1: Each threshold SHALL have a `calibrated: bool` flag in project config.
- AC2: Uncalibrated thresholds SHALL use conservative defaults and the extension SHALL log a warning at session open.
- AC3: Calibration data (set, date, distribution, chosen threshold) SHALL be stored in `.saltcode/calibration/`.
- AC4: Calibration SHALL be re-run when the model or embedding changes.
- **Trace:** design §11.9, BIFAI-NET v5.2 sim_floor protocol.

---

## OPT — Optional features

### REQ-OPT-001 — Builder escalation (online-only, default OFF)
Saltcode MAY provide an online-only Builder escalation that rebuilds a 3×-failed task once on Flash before flagging human; it SHALL default OFF (a `pi.registerFlag` flag) and SHALL be unavailable offline.
- AC1: IF the flag is OFF or the session is offline, THEN no Flash Builder call SHALL occur and budget-exhaustion SHALL FLAG HUMAN as in REQ-FAIL-001.
- **Trace:** Trade A, C4, DD-7.

---

## CKP — Checkpoints & auto-advance

### REQ-CKP-001 — Checkpoint on robust pass
A task SHALL produce a checkpoint (a git commit + a persisted snapshot) only when it meets the robustness bar: the five per-task gates pass, the diff is applied to the live tree, AND the regression gate passes (or is SKIPPED when no full suite is configured).
- AC1: WHEN a task meets the bar, the extension SHALL create a git commit and append a `saltcode:checkpoint` entry `{task_id, commit_sha, gate_results, stability, regression, timestamp, sprint_id}`.
- AC2: WHEN a task does NOT meet the bar, NO checkpoint SHALL be created and the loop SHALL NOT advance to the next task.
- **Trace:** design §10.1.

### REQ-CKP-002 — Regression gate
After `apply_live` and before the checkpoint commit, the backend SHALL run the full existing test suite (`regression_cmd`) on the integrated live tree inside a security container.
- AC1: WHEN `regression_cmd` is configured and the suite passes, the checkpoint MAY be committed.
- AC2: WHEN the suite fails, the extension SHALL discard the uncommitted apply (reset to the last checkpoint) and route the failure (REQ-CKP-003).
- AC3: WHEN no `regression_cmd` is configured, the gate SHALL be SKIPPED and the checkpoint SHALL be marked `regression: unverified`.
- **Trace:** design §10.1, REQ-SEC-001.

### REQ-CKP-003 — Regression-fail routing (Trade B)
A regression failure SHALL route as: IF the failing tests are within the task's `files_affected` AND budget remains → Builder retry (`regression_fail`, shared budget); ELSE → FLAG HUMAN.
- AC1: WHEN the failing tests touch files outside `files_affected`, the task SHALL FLAG HUMAN (NOT auto-retry).
- AC2: `regression_fail` retries SHALL count against the shared per-task budget (REQ-FAIL-001).
- AC3: WHEN flagged, the live tree SHALL be left at the last checkpoint.
- **Trace:** design §10.1, Trade B.

### REQ-CKP-004 — Auto-advance modes
The extension SHALL support `auto_mode ∈ {off, hybrid, full}` (default `off`). `off` preserves the four-decision per-sprint flow. `hybrid` and `full` auto-advance the current `tasks.json` through checkpoints after a single plan approval (Decision 1).
- AC1: IN `off`, behavior SHALL be unchanged from the four-decision model.
- AC2: IN `hybrid`/`full`, Decision 1 (plan approval) SHALL still gate the launch; Decision 2 (FLAG HUMAN) SHALL always interrupt.
- AC3: The mode SHALL be settable via `saltcode.toml [checkpoint]` and overridable per run (e.g. `/sprint … --auto=hybrid`).
- **Trace:** design §10.1.

### REQ-CKP-005 — Hybrid mandatory end review
IN `hybrid` mode, WHEN the task list completes, the extension SHALL present the cumulative diff for human review (Decision 3) before the run is considered accepted.
- AC1: WHEN a `hybrid` run completes, a cumulative-diff review SHALL be surfaced.
- AC2: The review surface SHALL retain the "logical integration is YOUR check" notice (REQ-FAIL-004).
- **Trace:** design §10.1, Trade B.

### REQ-CKP-006 — Full Auto surfaces only stop conditions
IN `full` mode, the extension SHALL NOT prompt for per-task or end review; it SHALL surface to the human ONLY on a stop condition (budget exhaustion, un-routable regression, or FLAG HUMAN).
- AC1: WHEN a `full` run completes with no stop condition, it SHALL finish without a review prompt.
- AC2: `full` mode SHALL be documented as trading away the integration review (test-invisible integration bugs commit silently).
- **Trace:** design §10.1, Trade B.

### REQ-CKP-007 — Push stays explicit
Checkpoints SHALL be local git commits; the extension SHALL NOT push automatically unless `auto_push == true` (default `false`), in ANY mode.
- AC1: WHEN `auto_push` is false, no `git push` SHALL occur automatically, even in `full` mode.
- **Trace:** design §10.1.

### REQ-CKP-008 — Resume from last checkpoint
On `session_start`, the extension SHALL resume an interrupted run at the task following the latest checkpoint, after verifying `git HEAD == checkpoint.commit_sha`.
- AC1: WHEN HEAD matches the latest checkpoint, the loop SHALL resume at the next task (finished tasks SHALL NOT be re-run).
- AC2: WHEN HEAD does NOT match (tree changed outside Saltcode), the extension SHALL FLAG HUMAN to reconcile rather than continue.
- **Trace:** design §10.1, REQ-EXT-006.

### REQ-CKP-009 — Rollback
The extension SHALL provide `/rollback last` and `/rollback <task_id>` → `git reset --hard <checkpoint_sha>` + rewind checkpoint/task state, logged to the audit log.
- AC1: WHEN rollback runs, the tree and Saltcode state SHALL return to the named checkpoint and the action SHALL be logged.
- AC2: The command SHALL warn that later uncommitted work is lost.
- **Trace:** design §10.1, REQ-SEC-003.
