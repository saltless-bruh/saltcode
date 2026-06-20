# Saltcode — Requirements

Every line of code in this project is **strictly bound** by the requirements below. A requirement is satisfied only when its **acceptance criteria** are demonstrably met (by an automated test, a deterministic check, or a documented manual verification). Ambiguity is a defect: if an implementer cannot tell whether a requirement passes, the requirement — not the code — is fixed first.

**Conventions.**
- **SHALL** = mandatory. **SHOULD** = strongly recommended. **MAY** = optional.
- Acceptance criteria use EARS phrasing: *WHEN* `<trigger>`, the system SHALL `<response>`; *IF* `<condition>`, *THEN* `<behavior>`.
- **Trace** points to the source-of-truth proposal section/tradeoff.
- IDs are stable; `tasks.md` references them.

Areas: GLB (global) · ORC (orchestrator/harness) · GATE (phase-gate + router) · CACHE · MEM (memory) · PFX (prefix cache) · CON (contracts) · MCP · STAT (static gate) · MOD (model tiers) · SCT/ARC/PLN/TST/EVL/BLD/AUD/CMP (agents) · CAD (cadence) · FAIL (failure/human) · OPT (optional).

---

## GLB — Global / cross-cutting

### REQ-GLB-001 — Typed contract boundary
The framework SHALL exchange all Phase-1 outputs **only** as typed JSON files on disk under `.saltcode/` (the Local Code-Wiki), never as in-memory provider-specific objects passed across the phase boundary.
- AC1: WHEN Phase 1 completes, every artifact in §7 of design exists on disk and validates against its pydantic schema.
- AC2: IF the Phase-1 provider is swapped (DeepSeek → other API → local MoE), THEN Phase 2 SHALL run unchanged with no code edits outside `providers/`.
- **Trace:** §3 contract boundary, §5.

### REQ-GLB-002 — JSON-only, schema-valid model output (Output-Length Enforcer)
Every agent that writes a JSON contract SHALL have its model output validated; non-conforming output SHALL be repaired-or-rejected, never persisted.
- AC1: IF model output is not parseable JSON or violates the schema, THEN the enforcer SHALL attempt one bounded repair, and on repeated failure raise a typed error (no partial/garbage file written).
- AC2: WHEN an agent is configured JSON-only, prose-only responses SHALL be rejected.
- **Trace:** §3 (Output Length Enforcer), Added-in-V3.

### REQ-GLB-003 — Privacy boundary (no raw source off-box)
No raw source file body SHALL be transmitted to any network API at any point.
- AC1: WHEN Scout or Builder needs repo knowledge, it SHALL obtain it via the LSP/AST MCP (symbols/AST only).
- AC2: IF any code path would place raw file contents into an API request payload, THEN it SHALL be rejected by construction (the API client refuses bodies tagged as source).
- **Trace:** §11, Trade D.

### REQ-GLB-004 — Determinism of cacheable prefixes
Within one session, segments 1 (system prompt) and 3 (frozen notes) SHALL be byte-identical across ALL Phase-1 calls. Segment 2 (design.md) SHALL be byte-identical across all calls **within the same Evaluator pass** (Planner → Test Intent → Evaluator see the same design.md), but MAY change between passes when an Evaluator→Architect re-loop re-emits design.md.
- AC1: WHEN two Phase-1 calls occur within the same Evaluator pass, a byte-diff of their segments 1–3 SHALL be empty.
- AC2: WHEN an Evaluator→Architect re-loop occurs, segment 2 MAY differ from the prior pass (this is expected and correct).
- AC3: On a fresh project's first sprint, the Scout call has no segment 2 (design.md doesn't exist yet); this is expected.
- **Trace:** §10.

### REQ-GLB-005 — Schema versioning
Every JSON contract SHALL carry `schema_version`; readers SHALL reject unknown major versions.
- AC1: IF a contract's `schema_version` is unsupported, THEN the reader SHALL error rather than guess.

---

## ORC — Harness / orchestrator

### REQ-ORC-001 — DAG-enforced single Phase-1 per sprint
The orchestrator SHALL execute Phase-1 agents as a DAG that permits **exactly one** Phase-1 fire per sprint.
- AC1: WHEN a sprint runs, the Scout→Architect→Planner→Test Intent→Evaluator chain SHALL fire at most once except for Evaluator-routed re-loops within caps (REQ-EVL-003).
- AC2: IF code attempts a second independent Phase-1 fire in the same sprint, THEN the orchestrator SHALL refuse it.
- **Trace:** §1, §3.

### REQ-ORC-002 — Phase-Gate is automatic
The Phase-Gate Controller SHALL advance from Phase 1 to Phase 2 with **no human action** once the Evaluator returns `status=pass`.
- AC1: WHEN `evaluator_report.json.status == "pass"`, THEN the spec locks, the spec hash is stored (REQ-CACHE-002), and Phase 2 begins automatically.
- **Trace:** §3 (5) breakpoint.

### REQ-ORC-003 — Thinking-mode gate
The harness SHALL apply the thinking-mode policy exactly: Architect ON; Evaluator ON only when re-invoked after a fail; Auditor ON only on retry/`gaming_suspected`; all others OFF.
- AC1: WHEN an agent is invoked, the thinking flag passed to the provider SHALL equal the policy value for that agent and state.
- **Trace:** §2 thinking gate, §5.

### REQ-ORC-004 — Write-path allowlist
The harness SHALL enforce a write-path allowlist on every agent that emits file changes.
- AC1: WHEN the Builder emits a diff, any hunk that creates/edits/deletes a path under `tests/**` SHALL be rejected before application.
- AC2: WHEN any agent writes, it SHALL only write the paths its role permits (§6 table).
- **Trace:** §3, §6, Added-in-V5.

### REQ-ORC-005 — Retry / loop budget tracker
The harness SHALL track per-task and per-sprint counters and enforce the caps in CAD/FAIL.
- AC1: Counters SHALL be observable in telemetry and SHALL never be silently reset mid-task/mid-sprint.
- **Trace:** §3, §3 Phase-2 budget.

### REQ-ORC-006 — Context Compactor (runtime)
The harness SHALL compact runtime context so a Builder context window contains exactly one task plus its required inputs.
- AC1: WHEN the Builder is invoked, its context SHALL contain one task object, the task's AST slices, the bodies of ONLY the files in `task.files_affected`, and the task's spec — and SHALL NOT contain sibling tasks, design.md, or files outside `task.files_affected`. (See REQ-BLD-002.)
- **Trace:** §3, Trade B.

### REQ-ORC-007 — Tool/MCP brokering
The harness SHALL broker agent tool calls to MCP servers and SHALL expose only role-appropriate tools to each agent.
- AC1: WHEN Scout runs, it SHALL have the LSP/AST tools (`where_is`, `find_references`, `outline`) and SHALL NOT have `read_file`.
- AC2: WHEN Builder runs, it SHALL have the LSP/AST tools AND a scoped `read_file` tool restricted to paths in the current `task.files_affected`. The broker SHALL reject reads for any other path.
- AC3: All other agents SHALL NOT have `read_file`.
- AC4: No API-routed agent (Phase-1 agents online) SHALL receive raw file bodies via any tool.
- **Trace:** §11.

---

## GATE — Phase-gate + cost router

### REQ-GATE-001 — Connectivity check selects path
The router SHALL probe connectivity at session open and route Online→API / Offline→local MoE fallback.
- AC1: IF offline, THEN Phase-1 planning SHALL run on Tier B (REQ-MOD-003) and the Auditor faithfulness call SHALL stay local (REQ-AUD-002).
- **Trace:** §3, §8.8, Added-in-V3.

### REQ-GATE-002 — Tier selection by session state
The router SHALL select tiers per §5: fresh project → Architect Pro + Evaluator Pro; amend/replan → all Flash.
- AC1: WHEN the project is fresh, Architect and Evaluator SHALL be invoked at Pro; on amend/rerun they SHALL downgrade to Flash.
- **Trace:** §2, §5.

---

## CACHE — Cache ladder

### REQ-CACHE-001 — Ladder order (exact → semantic → fire)
On a new goal the Phase-Gate SHALL evaluate the cache ladder strictly top-down and stop at the first hit.
- AC1: WHEN an exact Spec-Cache hit occurs, THEN `tasks.json` SHALL be reused with **zero API calls** and the ladder SHALL stop.
- AC2: WHEN only a semantic hit occurs, THEN a cheap Flash Architect confirmation SHALL run; on "yes" reuse, on "no" fall through to fire Phase 1.
- AC3: WHEN both miss, THEN Phase 1 fires.
- **Trace:** §6.

### REQ-CACHE-002 — Spec-cache key includes scope
The Spec-Cache key SHALL be `sha256(normalized_goal + scope_fingerprint)`.
- **At store time** (after Phase 1): `scope_fingerprint = sorted(tasks.json[*].files_affected)`.
- **At lookup time** (before Phase 1): `scope_fingerprint` comes from either (a) an explicit `--scope` CLI argument listing target files/modules, or (b) a **lightweight scope probe** — a single `outline` MCP call on the repo root or goal-mentioned paths, producing a sorted list of top-level modules/files. This probe is a tool call, NOT a Phase-1 agent fire, so it does not violate the one-Phase-1-per-sprint rule.
- AC1: WHEN two goals share text but differ in scope (e.g. login vs signup endpoint), THEN their keys SHALL differ (no false reuse).
- AC2: WHEN `--scope` is provided, it SHALL be used directly (no probe).
- AC3: WHEN neither `--scope` nor a probe result is available (e.g. empty repo), the scope fingerprint SHALL be empty and the key degrades to goal-only matching.
- **Trace:** §6, Trade C5.

### REQ-CACHE-003 — Semantic threshold is calibrated + PCD-adaptive (adapted from BIFAI-NET v5.2)
The semantic cosine threshold SHALL be calibrated via the measured-then-fixed protocol (REQ-CAL-001) and SHALL never trigger reuse without the Architect-confirmation gate. The confirmation bar SHALL be **PCD-adaptive**: the Prior Cluster Density of the query (count of cached specs within a cosine radius, normalized by cache size) modulates confidence in the match.
- AC1: IF cosine ≥ threshold but Architect confirmation = "no", THEN reuse SHALL NOT occur.
- AC2: IF PCD is above the high-density bar (calibrated per REQ-CAL-001), THEN the Architect confirmation MAY be a cheap/fast check (or skipped if PCD is very high and the project config allows).
- AC3: IF PCD is below the low-density bar, THEN the semantic hit SHALL be treated skeptically — require full Architect confirmation or fall through to Phase 1.
- AC4: The cosine threshold and PCD bars SHALL be stored in project config with a `calibrated: bool` flag.
- **Trace:** §6, Trade A (closed), BIFAI-NET v5.2 PCD.

---

## MEM — Memory / Lightweight Brain

### REQ-MEM-001 — Atomic notes frozen for the session
The Lightweight Brain SHALL auto-RAG **at most 5** Atomic Notes at session open and FREEZE them for the session.
- AC1: WHEN a session is open, the note set SHALL NOT change until the session ends (preserving prefix stability, REQ-GLB-004).
- AC2: The note count SHALL be ≤ 5.
- **Trace:** §9, §10, Added-in-V5.

### REQ-MEM-002 — Code-Wiki completeness
The Local Code-Wiki under `.saltcode/` SHALL contain all artifacts listed in design §9 after a successful sprint.
- AC1: WHEN a sprint completes, all of `context_report.json`, `design.md`, `tasks.json`, `tests/task_{id}_spec.*`, `evaluator_report.json`, and per-task `audit_result.json` SHALL exist and validate.

---

## PFX — Prefix cache

### REQ-PFX-001 — Front-loaded stable prefix
Every Phase-1 request SHALL be assembled as `[system | design.md | frozen notes | per-call delta]` in that order.
- AC1: Segments 1–3 SHALL be emitted byte-identically within a session (REQ-GLB-004); only segment 4 varies per call.
- **Trace:** §10.

---

## CON — Typed contracts

### REQ-CON-001 — context_report.json
SHALL validate: `existing_patterns[]`, `relevant_files[]`, `constraints[]`, `anti_patterns[]`, `schema_version`.
- AC1: Missing/extra required fields SHALL fail validation.
- **Trace:** §5, §7.

### REQ-CON-002 — design.md HARD CONSTRAINTS block
`design.md` SHALL contain a parseable `## HARD CONSTRAINTS` H2 block; the block SHALL contain **every** `context_report.constraints[*]` and `anti_patterns[*]` string verbatim.
- AC1: WHEN the parser reads design.md, it SHALL return the full constraint set; IF any constraint/anti_pattern string is missing, THEN the preservation check (REQ-EVL-001) SHALL fail.
- **Trace:** §7, Added-in-V5 (carry-through).

### REQ-CON-003 — tasks.json
Each task SHALL have `id`, `description`, `files_affected[]`, `acceptance_criteria[]`, `depends_on[]`, `complexity ∈ {low,med,high}`.
- AC1: Invalid `complexity` or missing fields SHALL fail validation.
- AC2: `depends_on` SHALL reference existing task ids (no dangling deps).
- **Trace:** §7.

### REQ-CON-004 — task_spec files
Test Intent SHALL emit exactly one `tests/task_{id}_spec.*` per task id, in the target language, before any Builder run for that task.
- AC1: WHEN Phase 2 starts a task, its spec file SHALL already exist.
- **Trace:** §7, Added-in-V4.

### REQ-CON-005 — evaluator_report.json
SHALL validate `status ∈ {pass,gaps}`, `gaps[]` (each `{id, type∈{design_gap,plan_gap,constraint_violation}, detail, target∈{architect,planner}}`), `routing_summary`.
- **Trace:** §7.

### REQ-CON-006 — audit_result.json
SHALL validate `task_id`, `status ∈ {pass,fail}`, `reason ∈ {pass,impl_fail,gaming_suspected,spec_defect}`, `next_action ∈ {next_task,builder_retry,test_intent_respec,flag_human}`, `detail`, `stability` (object with `n_passes ∈ int≥1`, `verdicts ∈ [string]`, `stability_score ∈ [0.0, 1.0]`, `gac ∈ int≥0`).
- AC1: `reason=pass` IFF `status=pass`; `reason=spec_defect` SHALL set `next_action=test_intent_respec`.
- AC2: `stability_score` SHALL equal `1.0 - (verdict_changes / (n_passes - 1))`.
- AC3: `verdicts` array length SHALL equal `n_passes`.
- **Trace:** §7.

---

## MCP — LSP/AST server

### REQ-MCP-001 — AST-only tools
The MCP server SHALL expose `where_is`, `find_references`, `outline` and SHALL return symbols/ASTs only.
- AC1: No tool response SHALL contain a raw file body.
- **Trace:** §11.

### REQ-MCP-002 — Local-only transport
The server SHALL bind to localhost and SHALL NOT transmit any repo content to a remote host.
- AC1: WHEN the server runs, it SHALL have no outbound network calls carrying repo data.
- **Trace:** §11, Trade D.

### REQ-MCP-003 — Per-language backend
The server SHALL drive the correct language server (`pyright|tsserver|rust-analyzer|gopls`) per repo language.
- AC1: WHEN the repo is Rust, queries SHALL resolve via rust-analyzer.

---

## STAT — Static-analysis gate

### REQ-STAT-001 — Hard gate before Auditor (sandbox)
Every Builder diff SHALL pass the per-language static gate **before** the Auditor is called. The gate SHALL operate on a **sandbox** (git worktree or temp copy with the diff applied) — never the live working tree.
- AC1: WHEN the gate is DIRTY, THEN the sandbox SHALL be discarded, the diff SHALL short-circuit back to the Builder with the lint reason, and the Auditor SHALL NOT be invoked.
- AC2: WHEN CLEAN, THEN the clean static report SHALL be passed to the test runner (REQ-STAT-004).
- AC3: The live working tree SHALL be unmodified by the static gate regardless of outcome.
- **Trace:** §3 Phase-2, §8.5.

### REQ-STAT-002 — Per-language runners + strength
The gate SHALL run the tools in design §12 and record the gate strength for the active language.
- AC1: TS→`tsc`+`eslint`; Rust→`cargo check`/`clippy`; Go→`build`+`vet`; Python→`pyright --strict`+`ruff`; JS→`eslint`.
- AC2: WHEN strength is SOFT, the Auditor escalation policy (REQ-AUD-002) SHOULD prefer Flash online.
- **Trace:** §7 (proposal), §12.

### REQ-STAT-003 — Zero VRAM, bounded time
The gate and test runner SHALL use no GPU and SHALL run as bounded subprocesses with a configurable timeout.
- AC1: The gate and test runner SHALL not load any model.

### REQ-STAT-004 — Test runner before Auditor
After the static gate passes (CLEAN), the harness SHALL run the task's acceptance test (`tests/task_{id}_spec.*`) on the sandbox using the project's configured `test_runner_cmd`.
- AC1: WHEN tests FAIL, the sandbox SHALL be discarded, the diff SHALL short-circuit back to the Builder with test output, and the Auditor SHALL NOT be invoked. This counts against the shared retry budget.
- AC2: WHEN tests PASS, the test results (pass/fail + output) SHALL be forwarded to the Auditor alongside the clean static report.
- AC3: IF no `test_runner_cmd` is configured for the project, the test-runner step SHALL be SKIPPED (the Auditor receives test content but no automated results, and must rely solely on its judgment).
- **Trace:** §8.5, §12.

### REQ-STAT-005 — Diff format validation
Before the sandbox apply, the harness SHALL validate that the Builder's output is a parseable **unified diff** (`--- a/` / `+++ b/` / `@@ ... @@` format), compatible with `git apply --check`.
- AC1: IF the output is not a valid unified diff, THEN it SHALL be treated as `impl_fail` and count against the shared retry budget (the Auditor is NOT called).
- AC2: The enforcer MAY attempt one bounded repair (e.g. extracting a diff from a markdown code fence), then reject.
- **Trace:** §8.5.

---

## MOD — Local model tiers

### REQ-MOD-001 — Tier A default
Builder/Auditor SHALL run on Tier A (`Qwen3.5-9B-MTP` UD-Q5_K_XL, in-VRAM) for low/med tasks.
- AC1: WHEN `task.complexity ∈ {low,med}` and no retry-cap breach, the active model SHALL be Tier A.
- **Trace:** §5, §13.

### REQ-MOD-002 — Tier B escalation with explicit sub-cap
The active tier SHALL escalate to Tier B WHEN `task.complexity == high` (before the first attempt) OR WHEN Tier A exhausts its **sub-cap of 2 retries** within the shared budget.
- AC1: WHEN `task.complexity == high`, the first Builder attempt SHALL use Tier B directly.
- AC2: WHEN Tier A has failed 2 attempts (any combination of diff-format, static, test, impl_fail, gaming), the 3rd and final attempt (within the shared budget of 3) SHALL use Tier B. IF this also fails, the budget is exhausted → FLAG HUMAN.
- AC3: WHEN a task starts on Tier B (due to `complexity == high`), all 3 shared-budget attempts are on Tier B; there is no further escalation.
- **Trace:** §5, §13.

### REQ-MOD-003 — Offline: ALL Phase-1 agents on Tier B
WHEN offline, **all** Phase-1 agents (Scout, Architect, Planner, Test Intent, Evaluator) SHALL run on Tier B. The §5 tier table (Flash/Pro) applies to the online path only; offline overrides all to Tier B. Scout still uses the LSP/AST MCP (always local) but its LLM call goes through Tier B.
- AC1: IF offline, THEN no DeepSeek / Flash / Pro API call SHALL be made for any Phase-1 agent.
- AC2: IF offline, THEN the Auditor faithfulness call SHALL stay on the active local model (no Flash escalation, per REQ-AUD-002).
- **Trace:** §8.8, Added-in-V7.

### REQ-MOD-004 — Sequential roles, one resident
Builder and Auditor SHALL run sequentially so exactly one local model is resident; the active tier serves both.
- AC1: WHEN running Phase 2 via Saltnitor, a tier change SHALL be requested via `ensure` (REQ-MOD-005) and only one model SHALL be loaded at a time.
- **Trace:** §13.

### REQ-MOD-005 — Serving via Saltnitor ensure (DD-2)
Tier residency SHALL be requested through the Saltnitor control API (`POST /v1/ensure`), with direct llama.cpp as fallback.
- AC1: WHEN Tier B is required, the harness SHALL `ensure` profile `B` before issuing the inference; IF the oracle refuses (OOM), THEN the task SHALL FLAG HUMAN rather than crash the box.
- **Trace:** §13, DD-2.

### REQ-MOD-006 — VRAM-triangle routing (Tier A)
The harness SHALL NOT request thinking + full-256K ctx + MTP simultaneously on Tier A.
- AC1: WHEN a Tier-A profile is selected, at most two of {thinking, 256K ctx, MTP} SHALL be enabled.
- AC2: `--spec-*` (MTP) on Tier A SHALL be opt-in and disabled by default until benchmarked.
- **Trace:** §13, Trade (v7 VRAM triangle / MTP regimes).

---

## SCT — Scout

### REQ-SCT-001 — Symbols-only input
Scout SHALL read only `file_tree` + LSP symbol exports via the MCP; it SHALL NOT read raw file bodies.
- AC1: IF Scout attempts a raw-body read, THEN the broker SHALL deny it.
- **Trace:** §6, §11.

### REQ-SCT-002 — Output is context_report.json
Scout SHALL write a schema-valid `context_report.json` populating `constraints[]` and `anti_patterns[]`.
- **Trace:** §5, REQ-CON-001.

---

## ARC — Architect

### REQ-ARC-001 — design.md only; no tasks
Architect SHALL write `design.md` and SHALL NOT write `tasks.json` or any task list.
- AC1: IF Architect output contains a task list, THEN it SHALL be rejected.
- **Trace:** §6.

### REQ-ARC-002 — Verbatim constraint carry-through
Architect's `## HARD CONSTRAINTS` block SHALL contain every `context_report` constraint and anti_pattern **verbatim**.
- AC1: A string-set equality check between `context_report.{constraints∪anti_patterns}` and the parsed HARD CONSTRAINTS block SHALL hold.
- **Trace:** §6, §7, Added-in-V5.

### REQ-ARC-003 — Tier/thinking
Architect SHALL run at Pro on fresh projects (Flash on amend) with thinking ON.
- **Trace:** §5, REQ-ORC-003, REQ-GATE-002.

---

## PLN — Planner

### REQ-PLN-001 — design.md as sole input
Planner SHALL read `design.md` only and SHALL NOT read `context_report.json`.
- **Trace:** §6.

### REQ-PLN-002 — Output is tasks.json
Planner SHALL write schema-valid `tasks.json` (REQ-CON-003), with acyclic `depends_on`.
- AC1: The dependency graph SHALL be a DAG (no cycles).
- **Trace:** §5.

---

## TST — Test Intent

### REQ-TST-001 — Specs before code, one per task, with project context
Test Intent SHALL write one `tests/task_{id}_spec.*` per task before any Builder run, and SHALL NOT write implementation code. Test Intent reads `tasks.json` AND **project config** (`language`, `test_framework`, `test_runner_cmd`) so it produces specs in the correct framework.
- AC1: Spec files SHALL use the project's configured test framework (e.g. `pytest` for Python, `jest` for TS).
- AC2: On a **re-spec** triggered by `spec_defect`, Test Intent SHALL additionally receive `audit_result.detail` from the Auditor as supplementary context explaining what was wrong with the prior spec — this is the ONLY case where Test Intent reads more than `tasks.json` + project config.
- **Trace:** §6, REQ-CON-004.

### REQ-TST-002 — Immutability
After emission, task specs SHALL be immutable to the Builder; a defective spec SHALL be fixed only by a Test-Intent re-run.
- AC1: WHEN `audit_result.reason == spec_defect`, THEN Test Intent re-runs (≤1) — the Builder SHALL NOT edit the spec.
- **Trace:** §6, Trade C3.

---

## EVL — Evaluator

### REQ-EVL-001 — Four checks
Evaluator SHALL run all four: traceability, coverage, preservation, compliance.
- AC1: WHEN a context_report constraint is absent from the HARD CONSTRAINTS block, preservation SHALL fail.
- AC2: WHEN a task violates a constraint/anti_pattern, compliance SHALL fail.
- **Trace:** §3, §8.3, Added-in-V5.

### REQ-EVL-002 — Gap classification + routing
Each gap SHALL be typed and routed: `design_gap`→Architect; `plan_gap`→Planner; `constraint_violation`→Planner unless it conflicts with design.md → Architect. Any `design_gap` present ⇒ Architect loop.
- AC1: WHEN gaps include any `design_gap`, THEN the next loop SHALL target the Architect (re-emit design.md, then Planner re-runs).
- **Trace:** §3, §5.

### REQ-EVL-003 — Loop caps then human
Loop caps SHALL be Architect ≤ 2, Planner ≤ 3 per sprint; exceeding either SHALL FLAG HUMAN with the gap report.
- AC1: WHEN a cap is exceeded, THEN the sprint SHALL halt with a human flag — no silent spinning, no silent under-coverage.
- **Trace:** §3, FAIL.

### REQ-EVL-004 — Tier/thinking
Evaluator SHALL run at Pro on fresh projects (Flash otherwise); thinking ON only when re-invoked after a fail.
- **Trace:** §5, REQ-ORC-003.

---

## BLD — Builder

### REQ-BLD-001 — One task per context, no carryover
Builder SHALL build exactly one task per context window with no state carried from a prior task.
- AC1: WHEN a task starts, the Builder context SHALL be reconstructed from disk (task + AST + spec) only.
- **Trace:** §1, §6, Trade B.

### REQ-BLD-002 — Inputs are task + AST + scoped files + spec
Builder SHALL read the current task object, AST via LSP/AST MCP, the **full bodies of ONLY the files in `task.files_affected`** via the scoped `read_file` tool (REQ-ORC-007 AC2), and the task spec — NOT full files outside `task.files_affected`, NOT design.md, NOT sibling tasks.
- AC1: IF the Builder requests a file not in `task.files_affected`, THEN the broker SHALL reject the read.
- **Trace:** §6, §3 Phase-2, §11.

### REQ-BLD-003 — tests/** not writable
Builder diffs SHALL never create/edit/delete anything under `tests/**`.
- AC1: WHEN a diff touches `tests/**`, THEN it SHALL be rejected by the write-path allowlist before application (REQ-ORC-004).
- **Trace:** §6, Added-in-V5 (anti-gaming).

---

## AUD — Auditor

### REQ-AUD-001 — Faithfulness gate (heuristics + judgment)
Auditor SHALL run zero-cost heuristics then a judgment pass on every clean diff. Heuristics SHALL flag: return literals matching test fixtures; empty/throw-only bodies under test; branches keyed on known test inputs.
- AC1: WHEN a heuristic flag fires, THEN the verdict SHALL be `gaming_suspected` unless judgment clears it.
- **Trace:** §3 Phase-2, Added-in-V5.

### REQ-AUD-002 — Escalation policy (stability-based, adapted from BIFAI-NET v5.2)
The Auditor SHALL measure confidence via **multi-pass stability**, not self-assessment. The harness SHALL run the Auditor judgment N times (default N=3) with varied conditions (temperature jitter, evidence reordering) and compute `stability_score = 1.0 - (verdict_changes / (N-1))` and `gac` (pass at which the verdict first stabilized). The escalation threshold SHALL be set by the measured-then-fixed protocol (REQ-AUD-005), not guessed.
- AC1: IF offline, THEN no Flash call SHALL be made for the faithfulness judgment, even if stability is low.
- AC2: IF online AND `stability_score < auditor_stability_threshold`, THEN the judgment SHALL be re-run once on Flash and the Flash verdict SHALL be final.
- AC3: IF online AND `stability_score >= auditor_stability_threshold`, THEN the majority local verdict stands (no Flash call).
- AC4: The `auditor_stability_threshold` SHALL be configurable in project config and SHALL be calibrated via REQ-AUD-005.
- AC5: Each stability pass SHALL use a distinct condition (e.g. temperature 0.3/0.5/0.7, or evidence field reordering) so verdicts are not trivially identical.
- **Trace:** §3 Phase-2, §8.5.1, Trade C1, BIFAI-NET v5.2 Stability Score.

### REQ-AUD-003 — Typed verdict + routing
Auditor SHALL emit `audit_result.json` (REQ-CON-006). `impl_fail`→Builder retry; `gaming_suspected`→Builder retry with "no hardcoding" reason; `spec_defect`→Test Intent re-spec (not a Builder retry); `pass`→next task.
- AC1: `spec_defect` SHALL NOT consume a Builder retry.
- **Trace:** §3 Phase-2, §5.

### REQ-AUD-004 — Cannot write code
Auditor SHALL only judge; it SHALL NOT emit code diffs.
- **Trace:** §6.

### REQ-AUD-005 — Stability threshold calibration (measured-then-fixed protocol)
The `auditor_stability_threshold` SHALL be calibrated from benchmark data (known-good and known-gaming diffs), not guessed. The calibration produces a threshold that maximizes the F1 of "stable verdict = trustworthy verdict" on the calibration set.
- AC1: WHEN no calibration data exists (first sprint), the threshold SHALL default to a conservative value (0.5) and the config SHALL mark it as `calibrated: false`.
- AC2: WHEN calibration data is available, the calibration SHALL be run and the threshold SHALL be updated and marked `calibrated: true`.
- AC3: The calibration set, measurement date, and distribution SHALL be documented in `.saltcode/calibration/`.
- **Trace:** §8.9, BIFAI-NET v5.2 sim_floor protocol.

---

## CMP — Spec Compactor

### REQ-CMP-001 — Periodic compaction with deterministic constraint retention
The Spec Compactor SHALL run once per 5 sprints, stripping completed/resolved content (task descriptions, resolved design discussions, old context) while **never stripping any line from the `## HARD CONSTRAINTS` block**. HARD CONSTRAINTS are removed only by an explicit human edit, never by the Compactor.
- AC1: WHEN compaction completes, the `## HARD CONSTRAINTS` block SHALL be byte-identical to the pre-compaction version.
- AC2: The compactor SHALL run non-thinking at Flash.
- AC3: The compactor SHALL strip only content OUTSIDE the `## HARD CONSTRAINTS` block that relates to completed/obsolete work.
- **Trace:** §3, §6, Added-in-V4.

---

## CAD — Cadence rules

### REQ-CAD-001 — One Phase-1 fire per sprint. (= REQ-ORC-001.) **Trace:** §1.
### REQ-CAD-002 — One task per Builder context. (= REQ-BLD-001.) **Trace:** §1.
### REQ-CAD-003 — Session warmth
A session SHALL keep the prefix warm across its sprints; the harness SHALL warn WHEN a cold start would discard cached prefix tokens.
- AC1: WHEN a session has been idle past the prefix TTL, THEN the harness SHALL surface a cold-cache warning.
- **Trace:** §1, Trade D.

---

## FAIL — Failure handling & human flagging

### REQ-FAIL-001 — Shared per-task retry budget ≤ 3 with Tier-A sub-cap
Diff-format failures + static bounce-backs + test failures + `impl_fail` + gaming retries SHALL share a single per-task budget capped at 3. **Tier-A sub-cap = 2**: after 2 failures on Tier A, the 3rd attempt escalates to Tier B (REQ-MOD-002). If the task started on Tier B (complexity == high), all 3 attempts are on Tier B.
- AC1: WHEN the combined count reaches 3 (regardless of tier), THEN the task SHALL FLAG HUMAN (no further automatic retries).
- AC2: WHEN the count reaches 2 on Tier A, the next attempt SHALL use Tier B.
- AC3: `spec_defect` SHALL NOT consume a retry in this budget.
- **Trace:** §3 Phase-2.

### REQ-FAIL-002 — spec_defect re-spec ≤ 1 with Auditor feedback
A `spec_defect` SHALL trigger at most one Test-Intent re-spec, then resume. The re-invoked Test Intent SHALL receive `audit_result.detail` as supplementary context (REQ-TST-001 AC2) so the re-spec is targeted, not blind.
- AC1: WHEN a second `spec_defect` would occur for the same task, THEN the task SHALL FLAG HUMAN.
- AC2: The Auditor's `detail` field SHALL describe the specific defect (e.g. "spec tests a private API that doesn't exist") so Test Intent can fix it.
- **Trace:** §3 Phase-2, Trade C3.

### REQ-FAIL-003 — No silent failure
The harness SHALL never spin silently or under-cover silently; every cap breach SHALL produce an explicit human flag with a report.
- **Trace:** §3 (Evaluator), Phase-2 budget.

### REQ-FAIL-004 — Human integration review surfaced
At Sprint Complete the system SHALL present the diff for human review and SHALL explicitly state that logical cross-task integration is unverified.
- AC1: WHEN a sprint completes, the review surface SHALL include a "logical integration is YOUR check" notice.
- **Trace:** Trade B.

---

## CAL — Threshold calibration (adapted from BIFAI-NET v5.2)

### REQ-CAL-001 — Measured-then-fixed protocol for all configurable thresholds
All configurable thresholds (Auditor stability threshold, semantic-cache cosine threshold, PCD density bars) SHALL be calibrated from benchmark data via the measured-then-fixed protocol (design §8.9) rather than guessed.
- AC1: Each threshold SHALL have a `calibrated: bool` flag in project config.
- AC2: Uncalibrated thresholds SHALL use conservative defaults and the system SHALL log a warning at session open.
- AC3: Calibration data (the benchmark set, measurement date, distribution, and chosen threshold) SHALL be stored in `.saltcode/calibration/` for reproducibility.
- AC4: Calibration SHALL be re-run when the model or embedding changes.
- **Trace:** §8.9, BIFAI-NET v5.2 sim_floor protocol.

---

## OPT — Optional features

### REQ-OPT-001 — Builder escalation (online-only, default OFF)
The framework MAY provide an online-only Builder escalation that rebuilds a 3×-failed task once on Flash before flagging human; it SHALL default OFF and SHALL be unavailable offline.
- AC1: IF the flag is OFF or the session is offline, THEN no Flash Builder call SHALL occur and budget-exhaustion SHALL FLAG HUMAN as in REQ-FAIL-001.
- **Trace:** Trade A, C4, DD-4.
