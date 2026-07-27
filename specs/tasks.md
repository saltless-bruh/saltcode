# Saltcode — Tasks

The ordered build. Each task is a step-by-step unit for an agent or engineer; every task is **bound by `requirements.md`** (see *Satisfies*) and **explained by `design.md`**. Check a box only when the **Done when** verification passes. This list is the shared human + machine ledger of progress.

**v9 restructure.** Saltcode is now a Pi Package. Tasks split into two tracks:
- **Backend (Python)** — the heavy lifting (contracts, gates, sandbox, caches, stability). Mostly unchanged from the standalone build; each capability gains a thin CLI entrypoint in `saltcode.tools.*` called by the extension via `pi.exec`.
- **Extension (TypeScript)** — the bridge (provider registration, model/thinking routing, tool registration, access-control via `tool_call`, per-phase tool gating, state via `appendEntry`, compaction hook, native TUI, slash commands, the autonomous loop). This **replaces** the old Typer CLI + Textual TUI.

Each task is tagged `[KEEP]` (carries over, minor edits), `[CHANGED]` (same goal, new home/mechanism), `[REPLACED]` (rebuilt for Pi), or `[NEW]`.

Legend: `- [ ]` open · `- [x]` done · **Satisfies** = REQ ids · **Done when** = the gate that proves it.

---

# Track A — Python backend (`pip install saltcode-backend`)

> Most of this code already exists (the maintainer is at ~Task 6 of the standalone build). The v9 work here is (a) keep the modules, (b) remove the in-backend agent-LLM client and orchestration primitives that moved to the extension, and (c) add a `saltcode.tools.*` CLI entrypoint per capability.

## Task 0 — Scaffold: Pi Package + Python backend  ·  deps: none  ·  [CHANGED]
- [x] 0.1 Create the **Python backend** (`saltcode_backend/pyproject.toml`), package layout per design §17, `README` stub. Dev tooling: `ruff`, `pyright --strict`, `pytest`; runtime deps `pydantic>=2`, `lancedb`, `mcp`, `httpx` (drop `typer`/`rich`/`textual` — Pi owns the UI; keep `openai`/`httpx` only for the local Saltnitor client used by stability).
- [x] 0.2 Create the **Pi Package** root: `package.json` with `"keywords":["pi-package"]` and `"pi":{ "extensions":["./extensions"], "skills":["./skills"], "prompts":["./prompts"] }`; declare `@earendil-works/pi-coding-agent`, `@earendil-works/pi-ai`, `@earendil-works/pi-agent-core`, `@earendil-works/pi-tui`, `typebox` in `peerDependencies` as `"*"`. Add `extensions/` and `prompts/` stubs; `skills/` already populated.
- [x] 0.3 TS tooling: `tsconfig.json`, a linter/formatter (e.g. biome), and a type-check step (`tsc --noEmit`). Pi loads `.ts` via jiti — no build step needed at runtime, but type-checking gates CI.
- [x] 0.4 CI: run `ruff` + `pyright --strict` + `pytest` (backend) and `tsc --noEmit` + lint (extension) on every commit.
- [x] 0.5 Per-project workspace convention: `workspace/<project>/.saltcode/{tests/,cache/,calibration/}`.
- **Satisfies:** REQ-EXT-001, REQ-GLB-001 (layout), REQ-MEM-002.
- **Done when:** backend CI green on an empty skeleton; `pi install -l .` loads the package (extension + skills + prompts visible in `pi config`); `tsc --noEmit` passes on the extension stub.
- **Verification (2026-07-27):** backend green (`ruff` clean, `pyright --strict` 0 errors, 43 tests pass) ✓ · `tsc --noEmit` clean and `biome check` clean ✓ · **`pi install -l .` NOT verified** — Pi is not installed on this machine. Re-run that leg before treating Task 0 as fully closed. Deviations: `skills/` is an empty placeholder (Task 7.1 populates it, contrary to "already populated"), and `networkx` is retained as a backend dependency (see `saltcode_backend/README.md` → Dependency notes).

## Task 1 — Typed contracts & Output-Length Enforcer (+ entrypoints)  ·  deps: 0  ·  [KEEP]
- [ ] 1.1 pydantic models: `ContextReport`, `Task`/`TasksFile`, `EvaluatorReport`, `AuditResult` (with `stability`), `schema_version` on each.
- [ ] 1.2 `design_doc.py`: parse the `## HARD CONSTRAINTS` H2 block into a string set.
- [ ] 1.3 `enforce.py`: validate model output → one bounded repair → else typed error; JSON-only/length guard.
- [ ] 1.4 Readers/writers under `.saltcode/`, rejecting unknown major `schema_version`.
- [ ] 1.5 `depends_on` acyclicity + dangling-reference validation.
- [ ] 1.6 Unified-diff validator (`git apply --check`; one fenced-diff repair, else `impl_fail`).
- [ ] 1.7 **Entrypoints:** `saltcode.tools.validate_contract`, `saltcode.tools.diff_check` (read args, print result + exit code).
- **Satisfies:** REQ-GLB-001/002/005, REQ-CON-001..006, REQ-PLN-002 (DAG), REQ-STAT-005, REQ-EXT-004.
- **Done when:** unit tests prove valid samples load; each malformed sample is rejected with a typed error and **no file written**; a missing HARD CONSTRAINT is detectable; a cyclic `depends_on` fails; `stability` validates; a valid unified diff passes and raw-file output is rejected/repaired; both entrypoints return correct exit codes when run via subprocess.

## Task 2 — Backend providers (Saltnitor client + embeddings) & connectivity  ·  deps: 0  ·  [CHANGED]
> Agent-facing provider management (DeepSeek/Qwen/Saltnitor) moves to the extension's `pi.registerProvider` (Task 11/13). The backend keeps only what its own tools need.
- [ ] 2.1 `providers/local.py`: a thin OpenAI-compatible client to Saltnitor `:8765/v1` (fallback `:8080`), addressing router sections `A_STD`/`A_FOCUS`/`B`. **Used only by the stability module** (N local Auditor passes) — not for agent turns.
- [ ] 2.2 `providers/embeddings.py`: local embedding endpoint (offline-capable).
- [ ] 2.3 `connectivity.py`: online/offline probe (entrypoint `saltcode.tools.connectivity`).
- [ ] 2.4 Source-payload guard: the backend refuses bodies tagged as source for any outbound call (privacy by construction); local calls are exempt.
- **Satisfies:** REQ-GLB-003 (privacy), REQ-GATE-001 (connectivity), REQ-CACHE-003 (embeddings, DD-8), REQ-MOD-005 (local client).
- **Done when:** the embedding client works offline; the connectivity probe returns a correct verdict via subprocess; the source-payload guard rejects a tagged body.

## Task 3 — Backend harness primitives (sandbox, scope probe, allowlist, audit)  ·  deps: 1, 2  ·  [CHANGED]
> Budget tracking, thinking gate, write-path allowlist, phase-gate, router, and prefix assembly move to the **extension** (Task 13). The OS-level / compute primitives stay here.
- [ ] 3.1 `sandbox.py`: create a disposable sandbox (git worktree), run commands inside a **security container** (auto-detect bwrap → Docker → firejail; refuse if none). Container: read-only host fs, writable workspace only, no network, isolated PID, cgroup mem/CPU/time limits, auto-cleanup. Discard container + sandbox on failure. Entrypoint `saltcode.tools.sandbox_apply`.
- [ ] 3.2 `scope_probe.py`: if `--scope` absent, one `outline` MCP call → sorted module/file list (a tool call, NOT a Phase-1 fire). Entrypoint `saltcode.tools.scope_probe`.
- [ ] 3.3 `command_allowlist.py`: only execute whitelisted commands inside the container; refuse + log others. Configurable. (Mirrored at the `tool_call` layer in the extension.)
- [ ] 3.4 `audit_log.py`: append-only JSONL of every executed command (cmd, cwd, container id, exit code, timestamp, stdout/stderr hash) → `.saltcode/audit_log.jsonl`.
- **Satisfies:** REQ-STAT-001 (sandbox), REQ-CACHE-002 (scope probe), REQ-SEC-001/002/003/005.
- **Done when:** the sandbox applies a diff and runs a command inside a container with no network and read-only host fs; a malicious `os.system('rm -rf ~/')` does NOT affect the host; a non-allowlisted command is refused + logged; if no containment backend is found the backend refuses Phase 2; the scope probe returns a sorted module list via subprocess.

## Task 4 — LSP/AST MCP server & scoped-read broker  ·  deps: 0  ·  [KEEP]
- [ ] 4.1 `lsp_ast_server.py` (MCP Python SDK): `where_is`, `find_references`, `outline`; symbols/AST only.
- [ ] 4.2 `lsp_backends.py`: drive `pyright|tsserver|rust-analyzer|gopls` per repo language.
- [ ] 4.3 Localhost-only; assert no outbound call carries repo content.
- [ ] 4.4 `broker.py`: scoped `read_file(path)` restricted to the current `task.files_affected`; rejects other paths; denied to Scout. (The extension enforces the same via `setActiveTools` + `tool_call` — defense in depth.)
- [ ] 4.5 Minimal fixture repo (`tests/fixtures/sample_project/`) with ≥1 Python + ≥1 TS file and a trivial symbol graph.
- [ ] 4.6 **Consumption path:** author `mcp.json` declaring the server over stdio (`{ "command":"python", "args":["-m","saltcode.mcp.lsp_ast_server"], "transport":"stdio", "lifecycle":"eager" }`); consume via an MCP client extension (`pi-mcp-extension`) — do NOT hand-roll a bridge. Verify tools register as `mcp_saltcode-lsp_*` and that the extension's `tool_call` handler still gates them (scope block on out-of-range `read_file`).
- **Satisfies:** REQ-MCP-001..004, REQ-EXT-013, REQ-ORC-007, REQ-GLB-003, REQ-BLD-002.
- **Done when:** integration test returns correct symbols with **no raw body** in AST responses; a Scout scoped-read is rejected; a Builder scoped-read inside `task.files_affected` succeeds and outside is rejected; a network-egress assertion passes; the server is reachable through the MCP client extension as `mcp_saltcode-lsp_*` and out-of-scope reads are blocked at `tool_call`.

## Task 5 — Memory: LanceDB caches, notes, skills (+ cache_lookup entrypoint)  ·  deps: 1, 2  ·  [KEEP]
- [ ] 5.1 `lancedb_store.py`: tables for spec cache, semantic cache, notes, skills.
- [ ] 5.2 `spec_cache.py`: key = `sha256(normalized_goal + scope_fingerprint)`; store-time vs lookup-time scope per REQ-CACHE-002.
- [ ] 5.3 `semantic_cache.py`: `embed(goal+scope)`, cosine ≥ threshold → candidate; **PCD** (count within cosine radius / cache size); high PCD → cheap confirm, low → skeptical. Bars calibrated (Task 14b).
- [ ] 5.4 `atomic_notes.py`: auto-RAG ≤5 notes at session open, then FREEZE.
- [ ] 5.5 `skills.py`: vectorized skills retrieval (backend's own note/skill store — distinct from Pi skill loading).
- [ ] 5.6 `calibrated: bool` on all threshold configs; conservative defaults + warning when uncalibrated; artifacts in `.saltcode/calibration/`.
- [ ] 5.7 **Entrypoint:** `saltcode.tools.cache_lookup` (runs the exact→semantic ladder, returns cached `tasks.json` or `miss`).
- **Satisfies:** REQ-CACHE-002/003, REQ-MEM-001, REQ-GLB-004 (notes freeze), REQ-CAL-001.
- **Done when:** same-goal/different-scope keys differ; goal-only fallback works; notes ≤5 and immutable per session; PCD computed correctly; uncalibrated thresholds warn; the `cache_lookup` entrypoint returns a hit/miss verdict via subprocess.

## Task 9 — Static gate, test runner & diff validator (+ entrypoints)  ·  deps: 3  ·  [KEEP]
- [ ] 9.1 `runners.py`: subprocess adapters for `tsc`, `eslint`, `cargo check`/`clippy`, `go build`/`vet`, `pyright --strict`, `ruff` — each inside the container on the sandbox.
- [ ] 9.2 `gate.py`: dispatch by language → `clean | dirty(reason)`; record strength (HARD/MEDIUM/SOFT); zero GPU; bounded timeout; via command allowlist. Entrypoint `saltcode.tools.static_gate`.
- [ ] 9.3 `test_runner.py`: after CLEAN, run the task spec inside the **same** container via `test_runner_cmd`; `pass(output)|fail(output)`; killed on timeout; SKIP if unconfigured. Entrypoint `saltcode.tools.test_run`.
- [ ] 9.4 Wire the diff validator (1.6) as the first check (entrypoint `saltcode.tools.diff_check`).
- **Satisfies:** REQ-STAT-001..005, REQ-SEC-001/002.
- **Done when:** a broken diff per language returns `dirty`; a clean diff returns `clean`+strength; no model loaded; malformed output rejected before sandbox; a failing test returns `fail`; live tree unmodified; missing `test_runner_cmd` skips; destructive side effects are contained.

## Task 10 — Builder/Auditor backend: stability, apply-live, scoped-read  ·  deps: 3, 4, 9  ·  [CHANGED]
> The Builder and Auditor **agent logic** is now in skills (Task 7). The backend keeps the deterministic pieces.
- [ ] 10.1 `stability/measure.py`: run the Auditor judgment **N=3** times against the local Saltnitor client (temperature jitter / evidence reordering); compute `stability_score = 1-(verdict_changes/(N-1))` and `gac`; emit the `stability` object. (Online Flash re-judgment on instability is orchestrated by the **extension**, Task 13.) Entrypoint `saltcode.tools.compute_stability`.
- [ ] 10.2 Auditor heuristics (fixture-literal returns, empty/throw-only bodies, test-input-keyed branches) → folded into the stability/audit pipeline; produce `audit_result.json`.
- [ ] 10.3 `diffs/apply.py`: apply a **passed** diff to the **live tree** via `git apply` (write-path checked: no `tests/**`). Entrypoint `saltcode.tools.apply_live`. Sandbox apply is in Task 3.1.
- [ ] 10.4 `tools/read_scoped.py`: the Builder's scoped file read (broker-enforced) — entrypoint `saltcode.tools.read_scoped`.
- **Satisfies:** REQ-AUD-001/002/003/005, REQ-BLD-002/003, REQ-STAT-001/004/005, REQ-FAIL-001 (sub-cap math), REQ-CON-006.
- **Done when:** 3 stable passes → `stability_score=1.0`; oscillating verdicts → low stability (flagged for the extension to escalate online); a hardcoded-to-fixtures diff → `gaming_suspected`; `apply_live` refuses a `tests/**` hunk; a passed diff lands on the live tree; `read_scoped` honors `task.files_affected`.

## Task 12 — Spec Compactor (+ entrypoint)  ·  deps: 1  ·  [KEEP]
- [ ] 12.1 `spec_compactor.py`: every 5 sprints, strip completed/resolved content OUTSIDE `## HARD CONSTRAINTS`; never modify that block. Entrypoint `saltcode.tools.compact_spec`. (Runs on Flash/thinking-off — the extension sets the model; the backend rewrites the file.)
- **Satisfies:** REQ-CMP-001.
- **Done when:** post-compaction the `## HARD CONSTRAINTS` block is byte-identical; obsolete content outside it is stripped; the Evaluator preservation check still passes.

## Task 14b — Threshold calibration (+ entrypoint)  ·  deps: 10  ·  [KEEP]
- [ ] 14b.1 `stability/calibrate.py` (entrypoint `saltcode.tools.calibrate`): measured-then-fixed protocol — accept a calibration set, run measurements, compute thresholds, store in `.saltcode/calibration/`.
- [ ] 14b.2 Auditor: N-pass stability on each calibration diff; plot correct vs wrong distributions; threshold = max-F1.
- [ ] 14b.3 Semantic: cosine distributions for match/non-match; threshold just above max non-match; compute PCD; set density bars.
- [ ] 14b.4 Mark calibrated thresholds `calibrated: true`; uncalibrated warn at session open.
- **Satisfies:** REQ-AUD-005, REQ-CAL-001, REQ-CACHE-003 (AC4).
- **Done when:** calibration produces a documented threshold + distribution; the calibrated threshold differs from the default; uncalibrated thresholds warn; re-running after a model change yields a different threshold.

## Task 7b — Backend CLI entrypoints consolidation  ·  deps: 1,2,3,5,9,10,12,14b  ·  [NEW]
- [ ] 7b.1 Ensure every capability has a standalone `saltcode.tools.<name>` module runnable as `python -m saltcode.tools.<name> [args]`, with a stable JSON-on-stdout contract and exit codes: `validate_contract`, `diff_check`, `scope_probe`, `cache_lookup`, `sandbox_apply`, `static_gate`, `test_run`, `compute_stability`, `apply_live`, `compact_spec`, `calibrate`, `read_scoped`, `connectivity`.
- [ ] 7b.2 Document each entrypoint's args + output schema (the extension's tool definitions depend on these contracts).
- **Satisfies:** REQ-EXT-004.
- **Done when:** each entrypoint runs via subprocess with documented args, prints valid JSON, and returns correct exit codes; a contract-conformance test exercises all of them.

---

# Track B — TypeScript extension (the bridge) + skills/prompts

## Task 7 — Sub-agent definitions + per-agent routing (built on the skills)  ·  deps: 0  ·  [CHANGED]
> The 8 custom skills + 6 cookbooks already exist (COOKBOOKS.md). This task wires them into Pi as **sub-agent definitions** (isolated context per agent) and defines the routing table.
- [ ] 7.1 Place the 8 `saltcode-*` skills + 6 community cookbooks under `skills/` (SKILL.md folders) per design §17; verify they load via `pi config`.
- [ ] 7.2 Author `agents/*.md` sub-agent definitions (for the sub-agent extension, e.g. `pi-subagents`) for Scout, Architect, Planner, Test Intent, Evaluator, Builder — each with frontmatter: model, thinking level, tool allowlist (per design §6), and its Saltcode skill **preloaded directly** into the prompt (do NOT rely on Pi's read-tool auto-discovery — locked-down agents lack `read`). The Auditor is NOT a sub-agent (its N-pass judgment is the backend `compute_stability` tool).
- [ ] 7.3 Verify each agent's behavior matches its contract in an isolated spawn (Scout AST-only; Architect HARD CONSTRAINTS carry-through; Planner design.md-only; Test Intent project-config + framework; Evaluator four checks; Builder scoped/one-task).
- [ ] 7.4 Author the 3 new skills (SKILL.md, valid `name`/`description`): `saltcode-lsp-usage` (symbols/outline before bodies, stay in `files_affected`, never emit raw source — preload for Scout + Builder), `saltcode-delegation` (agent selection, serial-when-dependent, complete zero-context task prompts), `saltcode-checkpoint-ops` (`/checkpoints`, `/rollback`, reading regression failures).
- **Satisfies:** REQ-SCT/ARC/PLN/TST/EVL/BLD/AUD (behavioral), REQ-EXT-003, REQ-EXT-012, REQ-EXT-016.
- **Done when:** all 14 base skills + 3 new skills load; each of the 6 sub-agent definitions spawns in an isolated context with its skill present in-prompt even when it lacks `read`, producing the expected behavior on the fixture repo with only its allowed tools; the routing table resolves a model + thinking level for every (agent, state).

## Task 6 — Prefix assembly in `before_agent_start`  ·  deps: 5, 7, 13  ·  [CHANGED]
- [ ] 6.1 In `before_agent_start`, assemble `[system(active agent) | design.md | frozen notes | per-call delta]`; return `{ systemPrompt, message }`. Read `event.systemPromptOptions` to respect user config. Pull frozen notes + design.md from the backend/Code-Wiki.
- [ ] 6.2 Guarantee segments 1+3 byte-stable within a session and segment 2 byte-stable within an Evaluator pass.
- [ ] 6.3 (Debug) optionally inspect `before_provider_request` payloads to verify cache-prefix stability.
- **Satisfies:** REQ-PFX-001, REQ-GLB-004.
- **Done when:** a byte-diff of segments 1–3 across two calls in one session is empty; segment 2 is stable within a pass and may change across a re-loop.

## Task 11 — Saltnitor + DeepSeek/Qwen as registered providers  ·  deps: 2, 13  ·  [CHANGED]
- [ ] 11.1 `pi.registerProvider("deepseek", { api:"openai-completions"|… , models:[v4-flash, v4-pro], apiKey:"$DEEPSEEK_API_KEY" })` and optional Qwen provider.
- [ ] 11.2 `pi.registerProvider("saltnitor", { baseUrl:"http://127.0.0.1:8765/v1", api:"openai-completions", models:[A_STD, A_FOCUS, B] })`; prefer an **async factory** that fetches `/v1/models`; degrade gracefully if unreachable.
- [ ] 11.3 Before a Tier-B turn, `ensure` the Saltnitor router section (`POST /v1/ensure {profile:"B"}`); on oracle OOM refusal → FLAG HUMAN (don't crash). Enforce sequential Builder/Auditor (one resident). Select A_STD vs A_FOCUS by task input token count (default 32K). VRAM triangle: A_FOCUS disables high-thinking; MTP opt-in.
- [ ] 11.4 Offline: route ALL Phase-1 agents to a Tier-B Saltnitor model via `pi.setModel`; keep Auditor faithfulness local.
- [ ] 11.5 Read `mtp_enabled` and `a_focus_threshold` from `saltcode.toml [local]`; apply to profile selection (A_STD vs A_FOCUS, MTP on/off) in the extension's model-routing handler. Implement the per-turn provider failover chain (configured → `[providers.fallback]` → Saltnitor Tier B → FLAG HUMAN), logging each fallback.
- **Satisfies:** REQ-EXT-002, REQ-EXT-010, REQ-MOD-001, REQ-MOD-001b, REQ-MOD-002..006, REQ-AUD-002 (offline), REQ-GATE-001.
- **Done when:** providers appear in `pi --list-models`; a high-complexity task ensures `B` before inference; an oracle refusal yields a human flag; offline planning uses `B` and makes no API call; A_FOCUS is selected >32K with thinking off.

## Task 8 — Cache ladder + Phase-Gate orchestration (extension)  ·  deps: 5, 7, 13  ·  [CHANGED]
- [ ] 8.1 In the `/sprint` handler: resolve scope (`saltcode_scope_probe` or `--scope`), run `saltcode_cache_lookup` (exact → semantic with **PCD-adaptive** Architect confirmation) → reuse or fire Phase 1; stop at first hit.
- [ ] 8.2 On Evaluator `pass`: lock spec, store spec hash, advance to Phase 2 automatically; persist sprint state (`pi.appendEntry`).
- [ ] 8.3 Evaluator loop routing + caps (A≤2 / P≤3) → FLAG HUMAN on breach (`ctx.ui`).
- **Satisfies:** REQ-CACHE-001, REQ-ORC-001/002, REQ-EVL-002/003, REQ-FAIL-003.
- **Done when:** exact hit reuses `tasks.json` with zero API; semantic "no" falls through to Phase 1; exceeding a loop cap halts with a human flag + report; a second Phase-1 fire in one sprint is refused.

## Task 13 — The TypeScript extension (the bridge)  ·  deps: 7b, 7  ·  [REPLACED]
> Replaces the old Typer CLI + Textual TUI entirely. This is the core integration piece.
- [ ] 13.1 **Factory + lifecycle:** `export default function (pi)`; `session_start` (replay `ctx.sessionManager.getEntries()` → rebuild sprint/budget/task state; probe connectivity; register Saltnitor models); `session_shutdown` cleanup; `resources_discover` contributes skill/prompt paths if not bundled.
- [ ] 13.2 **Tool registration (bridge):** `pi.registerTool` for each `saltcode_*` capability (TypeBox params; `StringEnum` for enums) whose `execute` talks to the **backend daemon** (Task 19) over stdio/socket, falling back to `pi.exec("python", ["-m","saltcode.tools.<x>", …])` if the daemon is down.
- [ ] 13.3 **Access control + built-in override:** `pi.on("tool_call")` blocks `tests/**` writes, non-allowlisted commands, and out-of-scope scoped reads (`{ block:true, reason }`, `isToolCallEventType`); **override the built-in `write`/`edit`/`bash`** with same-named tools that route through the container (Task 3.1) so no live built-in bypasses containment in interactive mode; per-agent tool access comes from the sub-agent definitions (Task 7) + `pi.setActiveTools` at the top level.
- [ ] 13.4 **Model/thinking routing:** per agent, `ctx.modelRegistry.find(...)` → `pi.setModel` (handle `false` → fallback chain, REQ-EXT-010) + `pi.setThinkingLevel(level)` per design §6; update status on `model_select`/`thinking_level_select`.
- [ ] 13.5 **State:** `pi.appendEntry` for sprint/budget/task; budget tracker (shared ≤3, Tier-A sub-cap 2, `spec_defect` free) + loop counters (A≤2/P≤3) implemented here, observable, never silently reset.
- [ ] 13.6 **Compaction:** `pi.on("session_before_compact")` preserves the active `## HARD CONSTRAINTS` in any summary (or cancels).
- [ ] 13.7 **TUI:** `ctx.ui.setWidget` (phase, task deck, gate pipeline, cost), `ctx.ui.setStatus`, `ctx.ui.notify`, `ctx.ui.confirm` for the four decisions; richer dashboard via `ctx.ui.custom` guarded by `ctx.mode==="tui"`.
- [ ] 13.8 **Commands + sub-agent orchestration:** `pi.registerCommand` for `/sprint` (autonomous loop that **spawns each Phase-1 agent as an isolated sub-agent** in dependency order via the sub-agent extension, awaiting + validating each result — NOT `sendUserMessage` into one session), `/review`, `/status`, `/cost`. Pausing only at Decisions 1–4.
- [ ] 13.9 **Flags:** `pi.registerFlag("dry-run")` (no subprocess executes; nothing outside `.saltcode/` is written) and `pi.registerFlag("builder-escalation")` (default OFF; Task 16).
- [ ] 13.10 **Phase-2 loop** (extension side): per task, spawn the **Builder sub-agent** → `saltcode_diff_check` → `saltcode_sandbox_apply` → `saltcode_static_gate` (dirty → short-circuit) → `saltcode_test_run` (fail → short-circuit) → `saltcode_stability` (backend N-pass Auditor); on instability + online, re-run the judgment once on DeepSeek Flash; `pass` → `saltcode_apply_live`, next task; route `impl_fail`/`gaming_suspected`/`spec_defect` per REQ-AUD-003; FLAG HUMAN on budget exhaustion.
- **Satisfies:** REQ-EXT-003..015, REQ-ORC-002..007, REQ-FAIL-001..004, REQ-SEC-004/006/007, REQ-AUD-002 (escalation), REQ-CAD-003.
- **Done when:** `/sprint` drives a full sprint pausing only at the four decisions; each Phase-1 agent runs in an isolated sub-agent context; a `tests/**` write and a non-allowlisted command are blocked at `tool_call`; a built-in `bash rm -rf` in interactive mode is contained (routed to the container, host untouched); budget exhaustion flags human; 2 Tier-A failures → 3rd on Tier B; the live tree is unmodified until Auditor `pass`; `--dry-run` runs nothing and writes nothing outside `.saltcode/`; state survives `/resume`.

## Task 13b — Package as a Pi Package (manifest + prompts + publish)  ·  deps: 13  ·  [NEW]
- [ ] 13b.1 Author prompt templates `prompts/{sprint,phase1,phase2,review}.md` (frontmatter `description` + `argument-hint`; `$@`/`$1` args) as reusable instruction blocks for interactive mode and `/sprint` composition.
- [ ] 13b.2 Finalize `package.json` `pi` manifest + `peerDependencies`; `pi-package` keyword; optional gallery `image`/`video`.
- [ ] 13b.3 Verify install paths: `pi install npm:@laz/saltcode` and `pi install git:github.com/laz/saltcode`; project-local `pi install -l .`; document the separate `pip install saltcode-backend` step + how the extension locates the Python module/daemon (PATH / configured interpreter).
- [ ] 13b.5 Bundle the **dependency extensions** in `package.json` (`dependencies` + `bundledDependencies`: `pi-mcp-extension` + `pi-subagents`) and reference their resources via `node_modules/` paths in the `pi` manifest; pin to a reviewed version. Document the update path (`pi update --extensions` for semver, explicit git re-pin otherwise), the capability contract (REQ-EXT-015) for substitutes, and the optional `vendor/` git submodules as an audit/patch-only fallback (PR-first upstream). Ship `mcp.json`, `agents/*.md`, and the 3 new skills; note the trust review both deps require.
- [ ] 13b.4 (Optional) publish to npm; list in the Pi package gallery.
- **Satisfies:** REQ-EXT-001, REQ-EXT-009 (templates).
- **Done when:** a clean machine can `pip install saltcode-backend` then `pi install …`, and `/sprint` runs end-to-end; `/phase1`/`/phase2`/`/review` expand in interactive mode.

---

# Track C — Integration & quality

## Task 14 — End-to-end sprint & session orchestration  ·  deps: 8, 10, 11, 12, 13  ·  [CHANGED]
- [ ] 14.1 Interactive mode: plain conversation with skills loaded behaves as the Saltcode agents on the fixture repo.
- [ ] 14.2 Autonomous mode: `/sprint "<goal>"` runs one Phase-1 fire → Phase-Gate → Phase-2 loop → Sprint Complete, pausing only at Decisions 1–4.
- [ ] 14.3 Offline variant completes on Tier B; a cached goal reuses `tasks.json` with zero API.
- **Satisfies:** REQ-ORC-001, REQ-CAD-001/002/003, REQ-GATE-001, REQ-MOD-003, REQ-EXT-008/009.
- **Done when:** the online fixture goes goal→shipped diff with one Phase-1 fire; the offline variant completes on Tier B; a cached goal reuses `tasks.json` with zero API; the four decisions appear as Pi confirms.

## Task 15 — Test suite & docs (backend + extension)  ·  deps: all  ·  [KEEP]
- [ ] 15.1 Backend `pytest` coverage mapped to requirement IDs (traceability matrix); extension tests (tool-call blocking, routing, state replay) via Pi's test/SDK harness or scripted `pi -p` runs.
- [ ] 15.2 Extend the fixture repo with per-language variants (TS, Rust, Go, Python, JS) to exercise all static-gate strengths + test-runner paths.
- [ ] 15.3 README: install (both `pip` backend + `pi install` package), configure (models/thresholds/flags, **project config** `language`/`test_framework`/`test_runner_cmd`), run a sprint, interactive vs autonomous, offline mode, Saltnitor wiring, security/trust notes.
- **Satisfies:** every REQ (each ≥1 mapped test or documented manual check).
- **Done when:** the traceability matrix shows every requirement covered; backend CI (`pytest`/`ruff`/`pyright`) and extension CI (`tsc`/lint) are green.

## Task 16 — (Optional) Builder escalation — online-only, default OFF  ·  deps: 11, 13  ·  [KEEP]
- [ ] 16.1 Behind the default-OFF `builder-escalation` flag (online-only): rebuild a 3×-failed task once on DeepSeek Flash before flagging human.
- [ ] 16.2 Assert unreachable offline and when the flag is OFF.
- **Satisfies:** REQ-OPT-001.
- **Done when:** with the flag OFF or offline, no Flash Builder call occurs and budget exhaustion flags human; with the flag ON + online, a 3×-failed task gets exactly one Flash rebuild attempt.

## Task 17 — Checkpoint backend: regression runner + commit/snapshot + rollback  ·  deps: 9, 10  ·  [NEW]
- [ ] 17.1 `regression.py` (or extend `test_run`): run the FULL suite (`regression_cmd`) on the live tree inside the container; `pass | fail(output)`; SKIP if unconfigured. Entrypoint `saltcode.tools.regression`.
- [ ] 17.2 `checkpoint.py`: git commit (message from task id + description) + write the checkpoint record. Entrypoint `saltcode.tools.checkpoint`.
- [ ] 17.3 `rollback.py`: `git reset --hard <sha>` + report; refuse (or require force) if uncommitted non-Saltcode changes are present. Entrypoint `saltcode.tools.rollback`.
- **Satisfies:** REQ-CKP-001, REQ-CKP-002, REQ-CKP-009, REQ-SEC-001 (regression in container).
- **Done when:** the full suite runs green / fails correctly on the live tree via subprocess; a checkpoint commit + record is produced; rollback resets to a named sha and refuses on a dirty non-Saltcode tree; correct exit codes throughout.

## Task 18 — Auto-advance loop + checkpoint state + run modes (extension)  ·  deps: 13, 17  ·  [NEW]
- [ ] 18.1 Wrap the Phase-2 loop (13.10): after `saltcode_apply_live`, call `saltcode_regression`; on pass → `saltcode_checkpoint` + `pi.appendEntry("saltcode:checkpoint", …)` → next task; on fail → discard the uncommitted apply (reset to last checkpoint) and route per REQ-CKP-003. Never advance past an un-checkpointed task.
- [ ] 18.2 Implement `auto_mode` (`off`/`hybrid`/`full`) from `saltcode.toml [checkpoint]` + a `--auto` run flag (`pi.registerFlag`); gate the launch on Decision 1 in ALL modes; FLAG HUMAN always interrupts.
- [ ] 18.3 Hybrid: on list completion, surface the cumulative diff (Decision 3) with the Trade-B notice. Full Auto: surface ONLY on a stop condition; finish silently otherwise; never `git push` unless `auto_push`.
- [ ] 18.4 Resume: on `session_start`, find the latest checkpoint, verify `git HEAD == commit_sha`, resume at the next task, or FLAG HUMAN on mismatch.
- [ ] 18.5 Commands: `pi.registerCommand("checkpoints")` (list) and `pi.registerCommand("rollback")` (→ `saltcode_rollback` + rewind state + audit log entry).
- **Satisfies:** REQ-CKP-003, REQ-CKP-004, REQ-CKP-005, REQ-CKP-006, REQ-CKP-007, REQ-CKP-008, REQ-CKP-009, REQ-EXT-006 (resume).
- **Done when:** a Hybrid run auto-advances the whole list and ends with a cumulative-diff review, and rollback works; a Full Auto run finishes unattended when all gates pass and surfaces only on a stop; an interrupted run resumes at the correct task; a regression failure outside `files_affected` flags human; no auto-push occurs when `auto_push=false`.

## Task 19 — Persistent backend daemon (efficiency)  ·  deps: 7b  ·  [NEW]
- [ ] 19.1 `saltcode/daemon.py`: a long-lived process that imports pydantic/LanceDB/etc. ONCE and serves tool requests over stdio (JSON lines) or a unix socket; dispatches to the same `saltcode.tools.*` handlers.
- [ ] 19.2 Extension-side daemon client: start on `session_start`, stop on `session_shutdown`; per-request framing + timeouts; **fallback** to per-call `pi.exec` if the daemon is unreachable (correctness preserved).
- [ ] 19.3 Benchmark: Phase-2 per-task wall-clock with daemon vs cold `pi.exec` (expect large reduction from amortized imports).
- **Satisfies:** REQ-EXT-014.
- **Done when:** a Phase-2 task completes with no per-gate interpreter/import cost; killing the daemon mid-run transparently falls back to `pi.exec`; the benchmark shows the daemon is materially faster.

## Task 20 — Dependency integration & config (MCP + sub-agents)  ·  deps: 4, 7, 13  ·  [NEW]
- [ ] 20.1 Wire the MCP client extension: ship `mcp.json`, confirm `mcp_saltcode-lsp_*` tools register and are gated by our `tool_call` handler (out-of-scope read blocked) and per-agent allowlists.
- [ ] 20.2 Wire the sub-agent extension: confirm each `agents/*.md` spawns in an isolated context with only its allowed tools and its configured model/thinking; confirm the `/sprint` handler drives them serially.
- [ ] 20.3 Prove the invariants hold **through** the dependencies: one-task-per-context (isolation), no raw body off-box (MCP AST-only), `tests/**` write blocked, non-allowlisted command blocked, built-in `bash`/`write`/`edit` contained.
- [ ] 20.4 Capability-contract doc + substitution test: swap in an alternative MCP client / sub-agent extension meeting the contract and re-run 20.1–20.3.
- **Satisfies:** REQ-EXT-012, REQ-EXT-013, REQ-EXT-015, REQ-MCP-004, REQ-SEC-007, REQ-ORC-006/007.
- **Done when:** the LSP/AST tools work via the MCP client extension with gating intact; all six sub-agents run isolated with correct tools/models; the security + isolation invariants pass through both dependencies; a substitute extension passes the same suite.

---

### Build order (critical path)

```
Backend:   0 → 1 → 2 → 3 → {4,5} → 9 → 10 → 12 → 14b → 7b → {17, 19}
Extension: 0 → 7 → 13 → {6, 8, 11} → 13b → 18
Integrate: → 20 → 14 → 15   then optional 16
```

`7b` (backend entrypoints) must finish before `13.2` (tool registration) can be exercised end-to-end, though both tracks scaffold in parallel after Task 0. `6`, `8`, `11` build on the extension core (`13`). Task 14b (calibration) runs after the first few sprints produce data. The checkpoint system layers on last: `17` (backend regression/commit/rollback tools) extends `9`+`10`, and `18` (the auto-advance loop) wraps the extension's Phase-2 loop (`13.10`) and consumes `17` — so both land after the base pipeline is proven end-to-end. `19` (backend daemon) is a drop-in efficiency layer over `7b` (the extension talks to it, else falls back to `pi.exec`), and `20` (dependency integration) gates real end-to-end runs since the MCP client + sub-agent extensions carry the LSP/AST tools and the isolated agents — so `20` precedes the `14` end-to-end tests.
