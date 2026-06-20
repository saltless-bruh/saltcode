# Saltcode — Design

**Pi-Optimized AI-Driven Coding Framework** · derived verbatim from `Proposal_Pi_optimized_graph_ASCII_v7.md` (the source of truth).

This document is the blueprint. It tells any agent or engineer **what** to build, **how** the pieces fit, **which** technology sits **where**, and **what** the repository looks like. It does not contain implementation code — that is produced under `tasks.md`, bounded by `requirements.md`.

> Reading order for an implementer: this file → `requirements.md` (the contract every line of code is bound by) → `tasks.md` (the ordered build).

---

## 1. Goals & non-goals

**Goal.** A terminal-driven harness that turns a single English goal into shipped code through two phases: a **frontier-API planning phase that fires once per sprint** and produces typed JSON contracts on disk, then a **local-model execution phase that loops at $0 API cost**, building one task at a time against pre-written acceptance tests with a static gate and an anti-gaming faithfulness gate. (Proposal §0, §3.)

**Explicit non-goals (do not build, do not pretend to guarantee):**
- **Logical cross-task integration is NOT machine-guaranteed.** Type safety (static gate) and constraint legality (Evaluator) are enforced; *semantic composition across tasks* (e.g. task-2 returns cents, task-5 passes dollars, both typed `number`) remains the **human's** job at Sprint Complete. The system must surface this for review, never claim to verify it. (Proposal Trade B.)
- No autonomous shipping. The human reviews the diff and ships.
- No raw source leaves the box during planning (privacy boundary, §11).

---

## 2. Design decisions & assumptions (open points the proposal left to the builder)

| ID | Decision | Rationale |
|----|----------|-----------|
| DD-1 | **Harness language = Python 3.11+** | pydantic v2 (contract enforcer), LanceDB (caches), MCP Python SDK, OpenAI-compatible clients, subprocess static gate, pytest. Contracts/tasks are language-agnostic; only impl stack changes if swapped to TS/Node. |
| DD-2 | **Phase-2 local serving via Saltnitor control API** (`http://127.0.0.1:8765/v1`) | Tier A and Tier B are two router sections; "ensure Tier B resident" = `POST /v1/ensure` (VRAM-oracle-gated hot-swap). Fallback: direct `llama-server`/router at `:8080`. |
| DD-3 | **Phase-1 provider = DeepSeek V4 API** behind a provider-agnostic `LLMClient` interface | Proposal pins DeepSeek but mandates a typed-contract boundary; the interface keeps it swappable (§3 boundary). |
| DD-4 | **Builder escalation (Trade A / C4) ships as an OPTIONAL, online-only switch**, default OFF | Mirrors the Phase-1 fallback, ~free, but breaks Phase-2 offline purity. Off by default preserves air-gap; flag enables it. |
| DD-5 | **Embeddings = local model** (e.g. `bge-small`/`nomic-embed` via a local embedding endpoint) | Semantic cache + notes RAG must work offline; no API dependency for retrieval. |
| DD-6 | **Code-wiki + cache live under `workspace/<project>/.saltcode/`**, one per target repo | Keeps Saltcode source separate from the project being built; per-project caches. |

---

## 3. System architecture (layers)

```
                       HUMAN-IN-THE-LOOP (CLI / TUI)
                                │  goal input / manual resume / review+ship
                                ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                        AGENTIC OS  (THE HARNESS)                            │
│  DAG Orchestrator        Tool / MCP Broker        Phase-Gate Controller     │
│  Context Compactor       Output-Length Enforcer   Thinking-Mode Gate        │
│  Write-Path Allowlist    Retry / Loop Budget Tracker   Connectivity Check   │
└───────────────────────────────────────────────────────────────────────────┘
        │  routes task-state to active phase            │  manages memory tiers
        ▼                                               ▼
┌──────────────────────────────┐         ┌──────────────────────────────────┐
│  PHASE-GATE + COST ROUTER     │         │  MEMORY ARCHITECTURE              │
│  connectivity: online→API     │         │  Lightweight Brain                │
│              offline→local    │         │   • Atomic Notes (≤5) RAG at      │
│  cache ladder (§ below)       │         │     session-open, then FROZEN     │
│  tier policy (§5)             │         │   • Vectorized Skills             │
│                               │         │  Spec Cache  (goal+scope → tasks) │
│                               │         │  Local Code-Wiki (typed JSON)     │
└──────────────────────────────┘         └──────────────────────────────────┘
        │                                               │
   ═════╪═══════════════════ PROVIDER-AGNOSTIC CONTRACT BOUNDARY ════════════╪═
        │  All Phase-1 output = typed JSON files on disk (the Code-Wiki).      │
        │  Swap DeepSeek → any API or local MoE without touching Phase 2.      │
   ═════╪═════════════════════════════════════════════════════════════════════╪═
        ▼                                                                       ▼
┌──────────────────────────────┐                ┌──────────────────────────────┐
│ PHASE 1 — PLANNING QUINTUPLET │   spec-lock    │ PHASE 2 — EXECUTION PAIR      │
│ (frontier API, fires ONCE)    │ ─────────────► │ (local, 2-tier, loops, $0)    │
│  Scout→Architect→Planner→     │  auto gate     │  Builder → Static Gate →      │
│  Test Intent→Evaluator        │                │  Auditor → pass/retry/flag    │
└──────────────────────────────┘                └──────────────────────────────┘
                                    every 5 sprints → Spec Compactor (API, periodic)
```

---

## 4. Glossary & cadence (these units carry the cost + cadence model — fixed)

- **TASK** — smallest unit, exactly one object in `tasks.json`, built + audited in isolation. **HARD RULE: exactly one task per Builder context window. No carryover.** (Proposal §1.)
- **SPRINT** — one Phase-1 spec-lock + the N Phase-2 task loops it produces. **Exactly ONE Phase-1 fire per sprint (DAG-enforced).**
- **SESSION** — a continuous 2–3 hr window kept alive so the prefix cache stays warm. Holds ≥1 sprint. Multiple sprints in one warm session reuse the cached prefix → "Phase-1 once per sprint" and "keep sessions long" are compatible.

---

## 5. Tier policy (base × session modifier) + thinking-mode gate

Each agent has a capability-driven **BASE** tier; session state applies a **MODIFIER** to Architect/Evaluator only. (Proposal §2.)

| Agent | Base tier | Session-state modifier |
|-------|-----------|------------------------|
| Scout | V4 Flash | — |
| Architect | V4 Pro | amend / rerun → downgrade to Flash |
| Planner | V4 Flash | — |
| Test Intent | V4 Flash | — |
| Evaluator | V4 Flash | fresh project → upgrade to Pro |
| Spec Compactor | V4 Flash | — (always non-thinking) |
| Builder | Tier A (local) | high complexity / retry-cap → escalate to Tier B |
| Auditor | Tier A (local) | faithfulness judgment → escalate to Flash when **online + low-confidence** (offline: stays local) |

**Local tiers:** Tier A = `unsloth/Qwen3.5-9B-MTP-GGUF` UD-Q5_K_XL (in-VRAM); Tier B = `unsloth/Qwen3.6-35B-A3B-MTP-GGUF` UD-Q4_K_XL (hybrid offload).

**Thinking-mode gate (concrete policy):**
- Architect → thinking **ON** (design reasoning).
- Evaluator → thinking **ON only when re-invoked after a fail**, else OFF.
- Auditor → thinking **ON only on retry / `gaming_suspected`**, else OFF.
- All others → thinking **OFF** by default.

---

## 6. The 7 agents (+ periodic Compactor)

Full roster with I/O. "reads/writes" are the typed contracts in §7.

| # | Agent | Tier | reads | writes | Hard rules |
|---|-------|------|-------|--------|------------|
| 1 | **Scout** | Flash | `file_tree`, `LSP_symbols` (via local LSP/AST MCP) | `context_report.json` | **Never reads raw file bodies.** |
| 2 | **Architect** | Pro→Flash | `user_goal`, `context_report.json` | `design.md` (+ **HARD CONSTRAINTS** block) | Forbidden from writing tasks. MUST mirror **every** `constraint` + `anti_pattern` verbatim into the HARD CONSTRAINTS block (carry-through). |
| 3 | **Planner** | Flash | `design.md` **only** | `tasks.json` | Never reads `context_report` — constraints reach it only via design.md. |
| 4 | **Test Intent** | Flash | `tasks.json`, **project config** (`language`, `test_framework`, `test_runner_cmd`); on re-spec: also `audit_result.detail` | `tests/task_{id}_spec.*` (one per task) | Cannot write implementation code. Specs are **immutable to Builder** thereafter. On re-spec, reads the Auditor's feedback to fix the defective spec rather than guessing blindly. |
| 5 | **Evaluator** | Flash→Pro | `tasks.json`, `design.md`, `context_report.json` | `evaluator_report.json` | Runs the **four checks** (§8.3). Classifies + routes gaps with loop caps. |
| 6 | **Builder** | A→B | one task object, AST (LSP/AST MCP), **scoped file bodies** (only `task.files_affected`), `tests/task_{id}_spec.*` | code diff (unified-diff format) | **1 task / ctx, no carryover. May NOT create/edit/delete `tests/**`** (harness-enforced). May read ONLY files in `task.files_affected` via a scoped `read_file` tool — no sibling tasks, no design.md, no other files. |
| 7 | **Auditor** | A→B(→Flash) | diff, `acceptance_criteria`, static-analysis output (clean), **test-runner results** (pass/fail + output), task_spec **content** | `audit_result.json` | Cannot write code. Runs the **faithfulness gate** (§8.4). Confidence is measured via **multi-pass stability** (§8.5.1), not self-reported. |
| — | **Spec Compactor** | Flash (non-thinking) | `design.md` + completed task history | compacted `design.md` | Runs once / 5 sprints. **MUST retain every still-active HARD CONSTRAINT.** |

---

## 7. Typed contracts (the provider-agnostic boundary)

All Phase-1 outputs are typed JSON on disk under `.saltcode/`, validated by pydantic and the Output-Length Enforcer (reject/repair non-conforming model output). Field sets are taken from Proposal §5.

**`context_report.json`** (Scout)
```json
{
  "schema_version": "1",
  "existing_patterns": ["string"],
  "relevant_files":   ["path/relative/to/repo"],
  "constraints":      ["string"],
  "anti_patterns":    ["string"]
}
```

**`design.md`** (Architect) — markdown, but MUST contain a machine-parseable section:
```
## HARD CONSTRAINTS
- <verbatim copy of context_report.constraints[0]>
- ...
- <verbatim copy of context_report.anti_patterns[0]>
- ...
```
The Evaluator's preservation check parses this H2 block; order is free but **every** constraint and anti_pattern string must appear verbatim. Body also carries components, data flow, non-goals.

**`tasks.json`** (Planner)
```json
[{
  "id": "T1",
  "description": "string",
  "files_affected": ["path"],
  "acceptance_criteria": ["string"],
  "depends_on": ["T0"],
  "complexity": "low|med|high"
}]
```

**`tests/task_{id}_spec.*`** (Test Intent) — acceptance tests in the target language (e.g. `task_T1_spec.ts`, `..._spec.py`). One file per task id. Written before code. Immutable to Builder.

**`evaluator_report.json`** (Evaluator)
```json
{
  "schema_version": "1",
  "status": "pass|gaps",
  "gaps": [{
    "id": "T3",
    "type": "design_gap|plan_gap|constraint_violation",
    "detail": "string",
    "target": "architect|planner"
  }],
  "routing_summary": "string"
}
```

**`audit_result.json`** (Auditor)
```json
{
  "schema_version": "1",
  "task_id": "T1",
  "status": "pass|fail",
  "reason": "pass|impl_fail|gaming_suspected|spec_defect",
  "next_action": "next_task|builder_retry|test_intent_respec|flag_human",
  "detail": "string",
  "stability": {
    "n_passes": 3,
    "verdicts": ["pass", "pass", "pass"],
    "stability_score": 1.0,
    "gac": 0
  }
}
```
**Stability-based confidence (adapted from BIFAI-NET v5.2 Stability Score / GaC).**
Instead of a single self-assessed `confidence: float` (which local models are poorly calibrated to produce), the Auditor's confidence is *measured behaviorally*. The harness runs the Auditor judgment **N times** (default N=3) with varied conditions (temperature jitter, evidence reordering) and records the verdict sequence. Two metrics:
- **`stability_score`** = `1.0 - (verdict_changes / (N - 1))`. A stable verdict (same result every pass) = 1.0; an oscillating one = 0.0.
- **`gac` (generation-at-classification)** = the pass at which the verdict first stabilized. High GaC (never stabilized) = the judgment is unreliable regardless of what the final verdict says.

Escalation policy: if `stability_score < auditor_stability_threshold` (configurable, default calibrated via the **measured-then-fixed protocol** in §8.9) AND the session is online, the judgment is re-run once on Flash. Offline, the local verdict stands. This replaces the ungrounded "confidence: 0.7" default with a behaviorally-measured signal.

**Builder diff format (not a JSON contract — validated structurally).**
The Builder SHALL emit a **unified diff** (`--- a/path` / `+++ b/path` / `@@ ... @@` hunks), compatible with `git apply --check`. The enforcer validates this before any gate runs: if the output is not a parseable unified diff (e.g. the model emitted raw file content, or prose), it is treated as `impl_fail` and counts against the retry budget. The enforcer MAY attempt one bounded repair (extracting a diff from a code-fenced block), then reject on repeated failure.

---

## 8. Workflows

### 8.1 Session open
1. Connectivity check (online/offline) → selects provider path.
2. Auto-RAG the Lightweight Brain: retrieve ≤5 Atomic Notes for the session, then **FREEZE** them for the whole session (so the prefix stays byte-stable).
3. Warm the prefix cache (§10).

### 8.2 New goal → Cache Ladder (top-down, §6 of proposal)

**Scope fingerprint resolution.** The spec-cache key includes a scope fingerprint so "rate-limit LOGIN" and "rate-limit SIGNUP" don't collide. At **store** time (after Phase 1), the fingerprint is `sorted(tasks.json[*].files_affected)` — fully determined. At **lookup** time (before Phase 1), `files_affected` doesn't exist yet. The harness resolves this by requiring the CLI to accept an **optional `--scope` list of target files/modules** alongside the goal; if omitted, the harness runs a **lightweight scope probe** — a single `outline` call to the LSP/AST MCP on the paths the user's goal mentions (or the whole repo root) — to produce a sorted list of top-level modules/files. This probe is **not** a Phase-1 fire (it's a tool call, not an agent), so the "one Phase-1 per sprint" invariant holds. The resulting fingerprint is used for both the exact and semantic lookups.

1. **Spec Cache (exact):** `key = sha256(normalized_goal + scope_fingerprint)`. Hit → reuse `tasks.json`, **zero API**. Stop.
2. **Semantic Cache (fuzzy):** `embed(goal + scope_fingerprint)`, cosine ≥ threshold → candidate. Before confirmation, compute the candidate's **Prior Cluster Density (PCD)** — the count of cached specs within a cosine radius of the query, normalized by cache size (adapted from BIFAI-NET v5.2's PCD concept). High PCD (dense neighborhood, many similar past specs) → the match is in well-trodden territory, Architect confirmation can be cheap or skipped. Low PCD (sparse neighborhood, the goal is an outlier) → even if cosine found one candidate above threshold, this is genuinely novel territory → require full Architect confirmation or fall through to Phase 1. The cosine threshold itself is set by the **measured-then-fixed protocol** (§8.9).
3. **Miss → fire Phase 1** (tier per §5).

### 8.3 Phase 1 — planning quintuplet (DAG; fires once)
`Scout → Architect → Planner → Test Intent → Evaluator`. The Evaluator runs **four checks**:
1. **traceability** — every task ↦ a design statement.
2. **coverage** — every design requirement ↦ a task.
3. **preservation** — every `context_report` constraint/anti_pattern present in the design.md HARD CONSTRAINTS block (catches Architect dropping a constraint).
4. **compliance** — no task violates any constraint/anti_pattern (catches an internally-consistent-but-illegal plan).

**Gap routing + caps:**
- `design_gap` → loop to **Architect**. Any design_gap present ⇒ Architect loop (re-emit design.md, Planner re-runs).
- `plan_gap` → loop to **Planner**.
- `constraint_violation` → loop to **Planner** (unless it conflicts with design.md itself → Architect).
- **Loop caps: Architect ≤ 2, Planner ≤ 3 per sprint.** Exceed → **FLAG HUMAN** with the gap report. No silent spinning, no silent under-coverage.

### 8.4 Phase-Gate → Phase 2
Evaluator passes (4 checks) → spec locks → spec hash stored to Spec Cache → **Phase-Gate fires automatically** (no human action).

### 8.5 Phase 2 — execution pair loop (per task, local)
```
tasks.json[current] → BUILDER (one task + AST + scoped files + task_spec) → code diff
   → DIFF FORMAT CHECK (unified-diff validator, zero cost)
       MALFORMED → counts as impl_fail, short-circuit to Builder retry
       VALID     → proceed
   → SANDBOX APPLY (git worktree / temp checkout — never the live repo)
   → STATIC ANALYSIS on the sandbox (per-lang, §12; zero VRAM, ms, HARD GATE)
       DIRTY  → revert sandbox, short-circuit BACK to Builder with lint reason
                (Auditor NOT called; counts against shared retry budget)
       CLEAN  → proceed
   → TEST RUNNER on the sandbox (per-lang: pytest / jest / cargo test / go test)
       FAIL   → revert sandbox, short-circuit BACK to Builder with test output
                (Auditor NOT called; counts against shared retry budget)
       PASS   → proceed (test results + static report forwarded to Auditor)
   → AUDITOR (receives: clean static report + test results + test content + diff)
       → faithfulness gate → audit_result.json
       pass             → apply diff to real repo, mark task done → next task
       impl_fail        → Builder retry
       gaming_suspected → Builder retry with "no hardcoding" reason
       spec_defect      → re-run TEST INTENT with audit_result.detail as
                          feedback (≤1), then resume (NOT a Builder retry)
```

**Sandbox rule.** The diff is NEVER applied to the live working tree until the Auditor returns `pass`. Static analysis and test runners always operate on a disposable sandbox (a `git worktree` or temp copy). If any gate rejects, the sandbox is discarded — the live repo is untouched.

**Per-task SHARED retry budget.** Diff-format failures + static bounce-backs + test failures + `impl_fail` + gaming retries share a single budget. **Tier-A sub-cap = 2**; if exhausted, the harness escalates to Tier B for **1 final attempt** (the 3rd), still within the budget. If the 3rd attempt also fails (or the task started on Tier B due to `complexity == high`), the budget is exhausted → **FLAG HUMAN**. `spec_defect` → Test Intent re-spec **≤1** (with Auditor feedback), then resume. `spec_defect` does **not** consume a retry.

**spec_defect feedback.** When the Auditor returns `spec_defect`, the re-invoked Test Intent receives the original `tasks.json` task object **plus** `audit_result.detail` as supplementary context explaining what was wrong with the prior spec. This is the ONLY case where Test Intent reads more than `tasks.json`.

#### 8.5.1 Auditor stability measurement (adapted from BIFAI-NET v5.2)
The Auditor runs its faithfulness judgment **N=3 times** per diff with varied conditions: temperature jitter (e.g. 0.3 / 0.5 / 0.7), evidence reordering, and minor prompt rephrasing. Each pass independently produces a verdict (`pass`, `impl_fail`, `gaming_suspected`, `spec_defect`). The harness records the verdict sequence and computes:
```
stability_score = 1.0 - (verdict_changes / (N - 1))
gac = first pass index where the verdict stabilizes (i.e. all subsequent passes agree)
```
If `stability_score >= threshold` → the majority verdict is the final verdict. If `stability_score < threshold` AND online → re-run the judgment once on Flash (the single Flash verdict is final). If `stability_score < threshold` AND offline → the majority local verdict stands (accept the uncertainty). The threshold itself is set by the **measured-then-fixed protocol** (§8.9), not guessed.

**Cost.** N=3 passes on the local Auditor (Tier A, ~9B) adds ~2–3 seconds per task — negligible against the Builder's generation time. The Flash escalation fires only on genuinely unstable verdicts (expected <15% of tasks in practice). This is structurally cheaper than self-assessed confidence, which either never escalates (threshold too high) or always does (threshold too low) because local models are poorly calibrated at self-assessment.

### 8.6 Sprint complete
Human audits output — **especially logical cross-task integration (Trade B)**. Arch change needed → Phase-Gate checks the Cache Ladder first (exact/confirmed-semantic hit → reuse, zero API; miss → new Phase 1). Good → ship.

### 8.7 Spec Compactor (every 5 sprints)
Reads design.md + completed task history; strips completed decisions, resolved constraints, old context; writes a compacted design.md.

**"Still-active" rule (deterministic).** The Compactor SHALL **never** strip any line from the `## HARD CONSTRAINTS` block. HARD CONSTRAINTS are removed only by an explicit human edit, never by the Compactor. The Compactor strips everything *else* that is no longer relevant: completed task descriptions, resolved design discussions, obsolete component notes. This is the safe default — the worst case is a slightly larger prefix, which is far better than a stripped constraint triggering an Evaluator preservation failure and a costly Architect re-loop.

### 8.8 Offline path
Connectivity = offline → **all** Phase-1 agents (Scout, Architect, Planner, Test Intent, Evaluator) run on **Tier B (Qwen3.6-35B-A3B)** instead of the DeepSeek API. Scout still uses the LSP/AST MCP (which is always local), but its LLM call goes through Tier B. Auditor faithfulness stays on the active local model + heuristics (no Flash escalation). Same contracts; slower wall-clock, not weaker. The tier table in §5 lists Flash/Pro for these agents — those are the *online* tiers; offline overrides them all to Tier B.

### 8.9 Threshold calibration: measured-then-fixed protocol (adapted from BIFAI-NET v5.2)

Several thresholds in the framework — the Auditor stability threshold, the semantic-cache cosine threshold, the PCD density bar — could be guessed (e.g. "default 0.7"), but guessed thresholds are either too loose (never fires) or too tight (always fires) because they aren't grounded in the actual data distribution. BIFAI-NET v5.2's `sim_floor` protocol solves this: **measure the threshold from benchmark data, then fix it for the project.**

**Protocol (run once per project, or when the model / embedding changes):**
1. Prepare a **calibration set** of known-outcome examples:
   - For the Auditor: a set of diffs with known verdicts (known-good implementations, known-gaming diffs, known-broken diffs). Can be synthetic or collected from early sprints.
   - For the semantic cache: a set of goal pairs with known match/non-match labels.
2. Run the measurement:
   - For the Auditor: run the N-pass stability measurement (§8.5.1) on each calibration diff. Record the stability-score distribution for correct-verdict diffs vs. wrong-verdict diffs. Set the threshold at the natural separation (e.g. the point that maximizes the F1 of "stable = trustworthy").
   - For the semantic cache: compute cosine similarities between all goal pairs. Set the threshold just above the highest cosine observed between non-matching pairs (so no false reuse by construction). Compute PCD at this threshold; set the PCD bar at the density below which Architect confirmation is mandatory.
3. **Fix** the thresholds in the project config. Document the calibration set, measurement date, and the distribution plots.

**Fallback for the first sprint** (no calibration data yet): use conservative defaults (stability threshold 0.5, cosine threshold 0.85) and run the calibration after the first 3–5 sprints produce enough data. The config marks uncalibrated thresholds explicitly so the human knows they're provisional.

**Why this matters for small models.** A 7B model asked "how confident are you, 0–1?" will produce a number, but that number has no grounding — it's token prediction, not probability estimation. Stability measurement (did the verdict change across passes?) is a behavioral signal that works regardless of model size. The measured-then-fixed protocol ensures the threshold that interprets this signal is grounded in real data, not intuition.

---

- **Lightweight Brain** — Atomic Notes (≤5), auto-RAG at session open then **frozen**; Vectorized Skills. (Backed by LanceDB.)
- **Spec Cache** — `key = sha256(normalized_goal + scope_fingerprint)` → `tasks.json`. LanceDB, local.
- **Semantic Cache** — embeddings of `goal + scope`, cosine threshold (calibrated via §8.9), **PCD-adaptive** confirmation bar (dense neighborhood → cheap confirmation; sparse → skeptical or skip to Phase 1), Architect-confirmed before reuse.
- **Local Code-Wiki** (under `.saltcode/`) — `context_report.json`, `design.md` (incl. HARD CONSTRAINTS), `tasks.json`, `tests/task_{id}_spec.*`, `evaluator_report.json`, `audit_result.json`.

---

## 10. Prefix cache structure (every Phase-1 call)

```
1. System Prompt              (fixed)    → cached   (always stable)
2. design.md                  (session)  → cached   (stable across sprints; may change
                                                      within a sprint on Evaluator→Architect
                                                      re-loops — see note below)
3. Atomic Notes (≤5, FROZEN)  (session)  → cached   (always stable within session)
4. Task delta / new input     (per call) → billed
```
**Stability guarantee.** Segments 1 and 3 are byte-identical across ALL Phase-1 calls within a session (system prompt is fixed; notes are frozen at session open). Segment 2 (design.md) is byte-identical **across sprints** within a session (the Architect doesn't run between sprints unless the cache misses). However, **within a single sprint's Phase-1 fire**, an Evaluator→Architect re-loop changes design.md, so segment 2 shifts between passes. This is unavoidable by design (the re-loop exists to fix the design), and it means the DeepSeek prefix-cache savings are partially lost during re-loops — the cost model (§16) already accounts for re-loop variance.

**On a fresh project's first sprint,** design.md does not exist before the Architect creates it. The Scout call has no segment 2 (empty/absent); the Planner call onwards has segment 2. This is expected: the Scout's prefix is shorter, and the cache savings kick in from the Planner onwards.

The enforcer must guarantee that segments 1 and 3 are emitted byte-identically, and that segment 2 is emitted byte-identically between calls *within the same Evaluator pass* (i.e. Planner → Test Intent → Evaluator use the same design.md).

---

## 11. LSP/AST MCP server + tool broker (the crown jewel — local, AST-only + scoped reads)

Replaces any doc-research tool. A **local MCP server** exposing:
- **AST tools:** `where_is` · `find_references` · `outline`
- **Scoped file-read:** `read_file(path)` — returns the full body of a file, but the **broker restricts which paths each agent may read**:
  - **Scout:** denied (AST-only; privacy boundary for API-routed agents).
  - **Builder:** allowed ONLY for paths in the current `task.files_affected` list. The broker rejects reads for any other path. This gives the Builder enough context to write correct code (imports, existing logic, patterns) while enforcing context hygiene (no sibling tasks, no design.md, no unrelated files). Since the Builder runs **locally**, raw file bodies never leave the box.
  - **All other agents:** denied.
- **Backends:** a language server per repo — `pyright | tsserver | rust-analyzer | gopls`
- Returns **symbols / ASTs only** for AST tools, runs on `localhost`, and **NEVER transmits source off-box** → the privacy boundary is enforced by the tool and the broker, not merely stated.

---

## 12. Static-analysis gate + test runner (the fire alarm)

**Static gate.** Zero VRAM, millisecond execution, hard gate. Operates on a **sandbox** (git worktree or temp copy of the repo with the Builder's diff applied) — never the live working tree. If the gate rejects, the sandbox is discarded; the live repo is untouched. Strength varies (Proposal §7):

| Lang / stack | Static gate | Strength | Note |
|--------------|-------------|----------|------|
| TypeScript | `tsc` + `eslint` | HARD | type errors block the diff |
| Rust | `cargo check`/`clippy` | HARD | borrow + type guarantees |
| Go | `build` + `vet` | HARD | compiler-backed |
| Python | `pyright` (strict) + `ruff` | MEDIUM | run pyright in STRICT mode; mypy optional |
| JS (no TS) | `eslint` only | SOFT | no type layer → Auditor carries the weight |

Rule of thumb: softer gate ⇒ the Auditor's faithfulness judgment matters more ⇒ prefer the Flash-escalated Auditor on SOFT-gate languages.

**Test runner.** After the static gate passes (CLEAN), the harness runs the task's acceptance test (`tests/task_{id}_spec.*`) on the sandbox using the project's configured test command (`pytest`, `jest`, `cargo test`, `go test`, etc. — set in project config). If the tests fail, the sandbox is discarded and the failure short-circuits back to the Builder with the test output (counts against the shared retry budget). If the tests pass, the results are forwarded to the Auditor alongside the clean static report. The test runner is a subprocess, zero VRAM, bounded time (configurable timeout).

---

## 13. Local model serving (Phase 2)

| Tier | Profile | Model / quant | Run profile | Fires when |
|------|---------|---------------|-------------|------------|
| **A** | **A_STD** (default) | `Qwen3.5-9B-MTP` UD-Q5_K_XL, fully in VRAM | `-ngl 99 -fa on -c 65536 --cache-type-k q8_0 --cache-type-v q8_0` | low/med complexity, default |
| **A** | **A_FOCUS** (long ctx) | same model | `-ngl 99 -fa on -c 262144 --cache-type-k q8_0 --cache-type-v q8_0 --ubatch-size 512` | tasks with large context needs (many files, long specs) |
| **B** | **B** (escalation) | `Qwen3.6-35B-A3B-MTP` UD-Q4_K_XL, hybrid offload | `-ngl 99 -ot ".ffn_.*_exps.=CPU" -fa on` (~21GB total, ~12–24 t/s) | `task.complexity == high` OR Tier A retry sub-cap (2) hit |

**VRAM triangle (Tier A).** Thinking + full-256K ctx + MTP all draw on 12GB — hold any **two**, not all three. Route per task: hard reasoning → thinking ON + A_STD (modest ctx); large context → A_FOCUS (256K, thinking OFF); throughput → MTP + A_STD. The harness selects A_STD or A_FOCUS based on task context size (threshold: configurable, default 32K tokens input).

**MTP regimes.** Tier B (Qwen3.6) MTP is stable (~1.4–2.2×); Tier A (Qwen3.5-dense) MTP is finicky — `--spec-type mtp --spec-draft-n-max 3` is **opt-in only** via config flag, default OFF until benchmarked on the user's build. Drop if no gain or segfault.

**Serving (DD-2).** Saltcode addresses the three profiles as **router sections** `A_STD` / `A_FOCUS` / `B` via Saltnitor's control API; tier/profile selection = `POST :8765/v1/ensure {"profile":"<name>"}` (VRAM-oracle-gated), inference via `:8765/v1/chat/completions`. Builder + Auditor run **sequentially → one model resident**; the active tier serves both.

---

## 14. Technology map (what sits where)

| Concern | Technology |
|---------|-----------|
| Language / runtime | Python 3.11+ |
| Typed contracts + Output-Length Enforcer | pydantic v2 (validate → repair → reject non-JSON/over-length model output) |
| Vector store (Spec Cache, Semantic Cache, Notes, Skills) | LanceDB (local) |
| Embeddings | local model (bge-small / nomic-embed) via local endpoint |
| Phase-1 LLM | DeepSeek V4 API (OpenAI-compatible) behind `LLMClient` |
| Phase-2 LLM | Saltnitor control API `:8765` (Tier A/B) / direct llama.cpp router `:8080` |
| LSP/AST MCP | MCP Python SDK server + language servers (pyright/tsserver/rust-analyzer/gopls) |
| Static gate | subprocess adapters: tsc, eslint, cargo check/clippy, go build/vet, pyright, ruff |
| DAG orchestration | internal DAG (topo + caps) or networkx |
| Diff application | `git apply` / unified-diff apply, constrained by write-path allowlist |
| CLI / TUI | Typer + Rich (CLI); Textual (optional TUI) |
| Config | pydantic-settings + TOML; **project config** includes `language`, `test_framework` (pytest/jest/cargo-test/go-test), `test_runner_cmd`, `static_gate_cmd` overrides |
| Framework tests | pytest |
| Adapted techniques | Stability Score + GaC (Auditor confidence), PCD (semantic cache quality), measured-then-fixed threshold protocol — all from BIFAI-NET v5.2 (NCA-driven network forensics proposal, same author) |

---

## 15. Project tree

```
saltcode/
├── pyproject.toml
├── README.md
├── design.md  requirements.md  tasks.md          # these specs
├── saltcode/
│   ├── cli.py                       # Typer entrypoint: goal / resume / review
│   ├── config.py                    # settings: models, thresholds, paths, flags
│   ├── contracts/                   # typed JSON boundary (pydantic)
│   │   ├── context_report.py  tasks.py  evaluator_report.py  audit_result.py
│   │   ├── design_doc.py            # design.md parse + HARD CONSTRAINTS extractor
│   │   └── enforce.py               # Output-Length Enforcer (validate/repair/reject)
│   ├── harness/
│   │   ├── orchestrator.py          # top-level sprint/session driver
│   │   ├── dag.py                   # DAG primitive: topo order + loop caps
│   │   ├── phase_gate.py            # auto phase-gate controller
│   │   ├── router.py                # connectivity + cache-ladder + tier selection
│   │   ├── budget.py                # shared per-task retry budget + loop counters
│   │   ├── thinking_gate.py         # thinking-mode policy (§5)
│   │   ├── write_allowlist.py       # tests/** protection, write-path enforcement
│   │   ├── ctx_compactor.py         # runtime context compaction
│   │   └── connectivity.py          # online/offline probe
│   ├── providers/
│   │   ├── base.py                  # LLMClient interface (chat, thinking flag)
│   │   ├── deepseek.py  local.py  embeddings.py
│   ├── memory/
│   │   ├── lancedb_store.py  spec_cache.py  semantic_cache.py
│   │   ├── atomic_notes.py          # session RAG + freeze
│   │   ├── skills.py  prefix_cache.py
│   ├── mcp/
│   │   ├── lsp_ast_server.py        # MCP: where_is/find_references/outline
│   │   ├── lsp_backends.py  broker.py
│   ├── agents/
│   │   ├── base.py  scout.py  architect.py  planner.py  test_intent.py
│   │   ├── evaluator.py  builder.py  auditor.py  spec_compactor.py
│   │   └── prompts/                 # system prompt per agent
│   ├── static_gate/
│   │   ├── gate.py                  # dispatch by language → clean|dirty(+reason)
│   │   └── runners.py               # tsc/eslint/cargo/go/pyright/ruff adapters
│   ├── phases/
│   │   ├── phase1.py  phase2.py  sprint.py  session.py
│   ├── diffs/apply.py               # apply Builder diff within allowlist
│   └── telemetry/logging.py
├── workspace/                       # per target project
│   └── <target-repo>/
│       └── .saltcode/               # the Local Code-Wiki + caches
│           ├── context_report.json  design.md  tasks.json
│           ├── tests/task_{id}_spec.*
│           ├── evaluator_report.json  audit_result.json
│           └── cache/               # LanceDB (spec, semantic, notes, skills)
└── tests/                           # Saltcode's OWN pytest suite
```

---

## 16. Cost model (sanity bound, Proposal §8)

Phase 1 fires once/sprint; Phase 2 loops locally at $0 API. Flash-dominant sprint ≈ **$0.001**; fresh-project sprint (Architect+Evaluator on Pro) ≈ **$0.002–0.004**; worst case (2 Architect re-loops on Pro) **< $0.02**. Weekly ≈ $0.005–0.05, monthly ≈ $0.02–0.20, yearly ≈ $0.25–2.00. **Watch (not agent count):** Phase-1 firing mid-sprint, Scout reading raw bodies, design.md bloat (run Compactor), cold caches, Evaluator re-looping to Architect (design too vague), faithfulness escalations spiking (Builder gaming).

---

## 17. Known limitations & human responsibilities (carried from Proposal Trade A–D)

- **Trade B (STANDS):** logical cross-task integration is the human's job at Sprint Complete; the system surfaces it, never verifies it.
- **Trade A/C4 (OPEN, DD-4):** Builder code generation does not escalate by default; optional online-only Builder escalation.
- **Trade C1:** anti-gaming Flash escalation breaks strict $0/air-gap on those calls; offline keeps it local + heuristics (higher gaming risk, by choice).
- **Trade C2:** stricter Evaluator → more loops + variable cost; Evaluator is a heavier single point of failure (consider a confidence threshold → human flag for low-confidence verdicts).
- **Trade C3:** spec immutability shifts burden to Test Intent; `spec_defect` → Test Intent re-spec (≤1).
- **Trade C5:** scope-fingerprint cache key lowers hit rate (safer, accepted).
- **Trade D (operational):** internet required for online Phase 1; never send raw source (enforced by the LSP/AST MCP); prefix cache TTL is session-scoped (keep sessions ≥2 hr); 5/7 agents depend on the API, mitigated by the typed boundary + local Tier-B fallback.
