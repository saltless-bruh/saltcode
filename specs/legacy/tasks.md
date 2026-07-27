# Saltcode — Tasks

The ordered build. Each task is a step-by-step unit for an agent or engineer; every task is **bound by `requirements.md`** (see _Satisfies_) and **explained by `design.md`**. Check a box only when the **Done when** verification passes. This list is the shared human + machine ledger of progress.

**How to use.** Build top-down — later tasks depend on earlier ones (`deps`). Within a task, do the sub-steps in order. A task is _done_ only when every sub-box is checked **and** its _Done when_ check is green. Do not start a task whose `deps` are unchecked.

Legend: `- [ ]` open · `- [x]` done · **Satisfies** = REQ ids · **Done when** = the gate that proves it.

---

## Task 0 — Repository scaffold & tooling · deps: none

- [x] 0.1 Create the Python 3.11+ project (`pyproject.toml`), package layout per design §15, and a `README.md` stub.
- [x] 0.2 Add dev tooling: `ruff`, `pyright` (strict), `pytest`; pin runtime deps (`pydantic>=2`, `lancedb`, `mcp`, `openai`, `httpx`, `typer`, `rich`; optional `textual`, `networkx`).
- [x] 0.3 Add CI that runs `ruff`, `pyright --strict`, and `pytest` on every commit.
- [x] 0.4 Create the per-project workspace convention: `workspace/<project>/.saltcode/{tests/,cache/}`.
- **Satisfies:** REQ-GLB-001 (layout), REQ-MEM-002.
- **Done when:** `pyright --strict` + `ruff` + `pytest` all pass on an empty skeleton in CI.

## Task 1 — Typed contracts & the Output-Length Enforcer · deps: 0

- [x] 1.1 Implement pydantic models: `ContextReport`, `Task`/`TasksFile`, `EvaluatorReport`, `AuditResult` (with `stability` object: `n_passes`, `verdicts[]`, `stability_score`, `gac`) with `schema_version` on each.
- [x] 1.2 Implement `design_doc.py`: a parser that extracts the `## HARD CONSTRAINTS` H2 block into a string set.
- [x] 1.3 Implement `enforce.py`: validate model output against a schema → on failure, one bounded repair → else raise typed error. Add a JSON-only/length guard.
- [x] 1.4 Implement readers/writers that load/store each contract under `.saltcode/`, rejecting unknown major `schema_version`.
- [x] 1.5 Add `depends_on` acyclicity + dangling-reference validation for `tasks.json`.
- [x] 1.6 Implement a **unified-diff format validator** (`diff_validator.py`): checks that Builder output is a parseable unified diff (`--- a/` / `+++ b/` / `@@ ... @@` format) compatible with `git apply --check`. If invalid, attempts one bounded repair (extract diff from markdown fence), else treats as `impl_fail`.
- **Satisfies:** REQ-GLB-001, REQ-GLB-002, REQ-GLB-005, REQ-CON-001..006, REQ-PLN-002 (DAG check), REQ-STAT-005.
- **Done when:** unit tests prove (a) valid samples load, (b) each malformed sample is rejected with a typed error and **no file is written**, (c) a missing HARD CONSTRAINT is detectable, (d) a cyclic `depends_on` fails, (e) `stability` validates as an object with `stability_score ∈ [0.0, 1.0]`, `verdicts` length = `n_passes`, `gac ∈ int≥0`, (f) a valid unified diff passes the diff validator, (g) a raw-file-content output is rejected or repaired-then-validated.

## Task 2 — Providers, embeddings & connectivity · deps: 0

- [x] 2.1 Define `LLMClient` interface (`chat(messages, *, thinking: bool, json_schema=None)`), provider-agnostic.
- [x] 2.2 Implement `deepseek.py` (OpenAI-compatible) for Phase 1; carry the thinking flag and prefix-cache-friendly message ordering.
- [x] 2.3 Implement `local.py` targeting Saltnitor `:8765/v1` (fallback `:8080`), addressing models by router-section id (A_STD/A_FOCUS/B).
- [x] 2.4 Implement `embeddings.py` against a local embedding endpoint (offline-capable).
- [x] 2.5 Implement `connectivity.py` (online/offline probe) used at session open.
- [x] 2.6 Add a guard so any request payload tagged as raw source is refused by API-routed providers (privacy by construction). Local providers are exempt (raw bodies stay on-box).
- **Satisfies:** REQ-GLB-001 (boundary), REQ-GLB-003 (privacy), REQ-GATE-001 (connectivity), REQ-MOD-005 (Saltnitor serving, DD-2), REQ-CACHE-003 (embeddings, DD-5).
- **Done when:** mocked tests show provider swap requires no edits outside `providers/`; the source-payload guard rejects a tagged body on API providers but allows it on local; connectivity probe returns a correct online/offline verdict.

## Task 3 — Harness primitives · deps: 1, 2

- [x] 3.1 `dag.py`: a DAG with topological execution and per-node loop caps.
- [x] 3.2 `budget.py`: shared per-task retry counter (≤3) with **Tier-A sub-cap (≤2)** + per-sprint loop counters (Architect ≤2, Planner ≤3); observable, never silently reset. On Tier-A sub-cap hit, escalate to Tier B for 1 final attempt. `spec_defect` does NOT consume a retry.
- [x] 3.3 `thinking_gate.py`: the exact policy table (Architect ON; Evaluator ON only on re-invoke; Auditor ON only on retry/gaming_suspected; else OFF).
- [x] 3.4 `write_allowlist.py`: per-role write paths; reject any diff hunk under `tests/**`.
- [x] 3.5 `ctx_compactor.py`: assemble a Builder context of exactly {one task, bodies of task.files_affected, its AST slices, its spec}. No sibling tasks, no design.md, no other files.
- [x] 3.6 `phase_gate.py`: auto-advance Phase 1→2 on Evaluator `pass`.
- [x] 3.7 `router.py`: connectivity routing + tier selection by session state (fresh→Pro, amend→Flash; offline→all Tier B).
- [x] 3.8 `sandbox.py`: create a disposable sandbox (git worktree or temp copy), apply a diff, run commands against it, discard on failure. The live working tree is NEVER modified until Auditor passes.
- [x] 3.9 `scope_probe.py`: lightweight scope fingerprinting — if `--scope` not provided, run a single `outline` MCP call on goal-mentioned paths to produce a sorted module/file list for cache key computation. This is a tool call, NOT a Phase-1 fire.
- **Satisfies:** REQ-ORC-001..007, REQ-GATE-002, REQ-FAIL-001/003, REQ-STAT-001 (sandbox), REQ-CACHE-002 (scope probe).
- **Done when:** tests prove (a) shared budget=3 with sub-cap=2 triggers Tier-B escalation at attempt 3, (b) budget=3 exhaustion flags human, (c) spec_defect does NOT decrement the retry counter, (d) the thinking flag matches policy for every (agent, state), (e) a `tests/**` hunk is rejected, (f) the phase-gate fires automatically on `pass`, (g) the sandbox applies a diff, runs a command, and leaves the live tree unmodified on failure.

## Task 4 — LSP/AST MCP server & broker · deps: 0

- [x] 4.1 Implement `lsp_ast_server.py` (MCP Python SDK) exposing `where_is`, `find_references`, `outline`; responses are symbols/AST only.
- [x] 4.2 Implement `lsp_backends.py`: drive `pyright|tsserver|rust-analyzer|gopls` per repo language via JSON-RPC.
- [x] 4.3 Bind localhost-only; assert no outbound network call carries repo content.
- [x] 4.4 Implement `broker.py`: expose only role-appropriate tools to each agent. Scout gets AST tools only (no `read_file`). Builder gets AST tools AND a **scoped `read_file(path)` tool** that the broker restricts to paths in the current `task.files_affected` — rejects reads for any other path. All other agents get no `read_file`.
- [x] 4.5 Create a **minimal test fixture repo** (`tests/fixtures/sample_project/`) with ≥1 Python file, ≥1 TS file, and a trivial LSP-resolvable symbol graph. Used by Task 4's own tests and later by Tasks 7, 9, 10, 15.
- **Satisfies:** REQ-MCP-001..003, REQ-ORC-007, REQ-GLB-003, REQ-BLD-002.
- **Done when:** integration test on the fixture repo returns correct symbols with **no raw body** in AST tool responses; a `read_file` call from Scout is rejected; a `read_file` call from Builder for a path IN `task.files_affected` succeeds; a `read_file` for a path NOT in `task.files_affected` is rejected; a network-egress assertion passes.

## Task 5 — Memory: LanceDB caches, notes, skills · deps: 1, 2

- [x] 5.1 `lancedb_store.py`: open/create tables for spec cache, semantic cache, notes, skills.
- [x] 5.2 `spec_cache.py`: key = `sha256(normalized_goal + scope_fingerprint)`. At **store** time: `scope_fingerprint = sorted(tasks.json[*].files_affected)`. At **lookup** time: scope comes from `--scope` CLI arg or the scope probe (Task 3.9). If neither is available (empty repo), scope is empty and key degrades to goal-only.
- [x] 5.3 `semantic_cache.py`: embed(goal+scope_fingerprint), cosine ≥ configurable threshold → candidate (no reuse without Architect confirmation). Compute **Prior Cluster Density (PCD)** for each query: count of cached specs within a cosine radius, normalized by cache size. High PCD → cheap confirmation bar; low PCD → skeptical (full confirmation or fall-through). PCD bars calibrated via the measured-then-fixed protocol (Task 5.6).
- [x] 5.4 `atomic_notes.py`: auto-RAG ≤5 notes at session open, then FREEZE for the session.
- [x] 5.5 `skills.py`: vectorized skills retrieval.
- [x] 5.6 Add `calibrated: bool` flag to all threshold configs (cosine, PCD bars, stability). Uncalibrated thresholds use conservative defaults (cosine 0.85, stability 0.5) and log a warning at session open. Store calibration artifacts in `.saltcode/calibration/`.
- **Satisfies:** REQ-CACHE-002/003, REQ-MEM-001, REQ-GLB-004 (notes freeze), REQ-CAL-001.
- **Done when:** tests prove (a) same-goal/different-scope keys differ, (b) goal-only fallback works when scope is empty, (c) notes set is ≤5 and immutable for a session, (d) semantic candidate never reused without the confirm gate (wired in Task 8), (e) PCD is computed correctly (high density for common goals, low for outliers), (f) uncalibrated thresholds are marked as such and log a warning.

## Task 6 — Prefix cache assembly · deps: 1, 5

- [ ] 6.1 `prefix_cache.py`: assemble `[system | design.md | frozen notes | delta]`; guarantee segments 1–3 byte-stable within a session.
- **Satisfies:** REQ-PFX-001, REQ-GLB-004.
- **Done when:** a byte-diff of segments 1–3 across two calls in one session is empty.

## Task 7 — Phase-1 agents · deps: 1, 2, 3, 4, 6

- [ ] 7.1 `agents/base.py`: agent runtime (system prompt, tier, thinking flag, contract enforcement via Task 1).
- [ ] 7.2 `scout.py`: file_tree + LSP symbols → `context_report.json`; deny raw-body reads.
- [ ] 7.3 `architect.py`: goal + context_report → `design.md` with verbatim HARD CONSTRAINTS carry-through; reject any task list; Pro/Flash + thinking ON.
- [ ] 7.4 `planner.py`: design.md only → `tasks.json` (acyclic).
- [ ] 7.5 `test_intent.py`: tasks.json + **project config** (`language`, `test_framework`, `test_runner_cmd`) → one `tests/task_{id}_spec.*` per task in the correct framework; no impl code. On re-spec, additionally receive `audit_result.detail` as feedback.
- [ ] 7.6 `evaluator.py`: four checks (traceability, coverage, preservation, compliance) → typed gaps + routing; thinking ON only on re-invoke.
- [ ] 7.7 Author the per-agent system prompts under `agents/prompts/`.
- **Satisfies:** REQ-SCT-001/002, REQ-ARC-001..003, REQ-PLN-001/002, REQ-TST-001/002, REQ-EVL-001/002/004, REQ-CON-001..005, REQ-ORC-003.
- **Done when:** on a fixture repo+goal, Phase 1 emits all valid contracts; a dropped constraint makes preservation fail; a constraint-violating task makes compliance fail; Architect output containing tasks is rejected.

## Task 8 — Cache ladder + Phase-Gate wiring · deps: 5, 7

- [ ] 8.1 Implement the ladder: exact spec-cache → semantic (with **PCD-adaptive** Architect confirmation: high PCD → cheap/skip, low PCD → full confirmation or fall-through) → fire Phase 1; stop at first hit.
- [ ] 8.2 On Evaluator `pass`: lock spec, store spec hash, auto-fire Phase-Gate.
- [ ] 8.3 Wire Evaluator loop routing + caps (A≤2/P≤3) → human flag on breach.
- **Satisfies:** REQ-CACHE-001, REQ-ORC-002, REQ-EVL-002/003, REQ-FAIL-003.
- **Done when:** exact hit reuses tasks.json with zero API calls; semantic "no" falls through to Phase 1; exceeding a loop cap halts with a human flag + report.

## Task 9 — Static-analysis gate, test runner & diff validator · deps: 3

- [ ] 9.1 `runners.py`: subprocess adapters for `tsc`, `eslint`, `cargo check`/`clippy`, `go build`/`vet`, `pyright --strict`, `ruff`. Each runs on a sandbox (git worktree / temp copy), never the live tree.
- [ ] 9.2 `gate.py`: dispatch by language → `clean | dirty(reason)`; record gate strength (HARD/MEDIUM/SOFT); zero GPU, bounded timeout (configurable).
- [ ] 9.3 `test_runner.py`: after static gate CLEAN, run the task's spec file (`tests/task_{id}_spec.*`) on the sandbox using project config's `test_runner_cmd`. Returns `pass(output) | fail(output)`. If `test_runner_cmd` is not configured, SKIP (Auditor judges without automated results).
- [ ] 9.4 Wire the diff validator (Task 1.6) as the first gate check: malformed diff → `impl_fail`, short-circuit before sandbox apply.
- **Satisfies:** REQ-STAT-001..005.
- **Done when:** (a) a broken diff per language returns `dirty` with a usable reason; (b) a clean diff returns `clean` + strength; (c) the gate loads no model; (d) a malformed (non-unified-diff) output is rejected before sandbox apply; (e) a failing test spec returns `fail` with test output; (f) the live working tree is unmodified after both dirty and clean paths; (g) a missing `test_runner_cmd` gracefully skips the test step.

## Task 10 — Phase-2 agents (Builder, Auditor) + loop · deps: 3, 4, 7, 9

- [ ] 10.1 `builder.py`: one task + AST + **scoped file bodies** (only `task.files_affected`) + spec → diff (unified format); reconstruct context from disk each task; never carry over; respect write allowlist.
- [ ] 10.2 `diffs/apply.py`: apply a diff via `git apply` on a **sandbox** (never the live tree until Auditor passes), rejected first by the write-path allowlist (no `tests/**`).
- [ ] 10.3 `auditor.py`: heuristics (fixture-literal returns, empty/throw-only bodies, test-input-keyed branches) + **multi-pass stability judgment** (N=3 passes with temperature jitter / evidence reordering; compute `stability_score` and `gac`); escalate to Flash when online + `stability_score < threshold`; offline stays local. Emit `audit_result.json` with `stability` object.
- [ ] 10.4 Implement the Phase-2 loop:
  - Diff format check → malformed = `impl_fail` (short-circuit, counts against budget)
  - Sandbox apply → Static gate (dirty → discard sandbox, short-circuit to Builder)
  - Test runner on sandbox (fail → discard sandbox, short-circuit to Builder)
  - Auditor receives: clean static report + test results + test content + diff
  - Auditor verdict: `pass` → apply diff to live repo, mark done, next task
  - `impl_fail` → Builder retry (Tier-A sub-cap: after 2 failures on Tier A, escalate 3rd attempt to Tier B)
  - `gaming_suspected` → Builder retry with "no hardcoding" reason (same sub-cap)
  - `spec_defect` → re-run Test Intent with `audit_result.detail` as feedback (≤1, does NOT consume a retry)
  - Shared budget ≤ 3 → FLAG HUMAN
- **Satisfies:** REQ-BLD-001..003, REQ-AUD-001..004, REQ-STAT-001/004/005, REQ-FAIL-001/002, REQ-TST-002, REQ-ORC-004/006, REQ-MOD-002.
- **Done when:** (a) a hardcoded-to-fixtures diff is caught as `gaming_suspected`; (b) a `tests/**` edit is rejected; (c) a dirty diff never reaches the Auditor; (d) a failing test never reaches the Auditor; (e) a `spec_defect` triggers re-spec with Auditor feedback (≤1) without consuming a retry; (f) budget exhaustion flags human; (g) 2 failures on Tier A → 3rd attempt on Tier B; (h) the live working tree is unmodified until Auditor passes; (i) 3 stable passes (same verdict) produce `stability_score=1.0`; (j) oscillating verdicts produce low stability and trigger Flash re-judgment online.

## Task 11 — Local serving integration (Tier A/B via Saltnitor) · deps: 2, 10

- [ ] 11.1 Map three profiles to router-section ids: `A_STD` (default, 64K ctx), `A_FOCUS` (256K ctx for large-context tasks), `B` (escalation). Before a profile's first call, `POST /v1/ensure {profile}`; on oracle OOM refusal → flag human (don't crash).
- [ ] 11.2 Enforce sequential Builder/Auditor (one resident); select A_STD vs A_FOCUS based on task input token count (threshold: configurable, default 32K); escalate to B when `complexity==high` OR Tier-A sub-cap (2) hit.
- [ ] 11.3 Enforce the VRAM triangle: A_FOCUS disables thinking; A_STD + thinking enabled for reasoning tasks; MTP (`--spec-*`) opt-in only via config flag, default OFF.
- [ ] 11.4 Offline path: route ALL Phase-1 agents to profile `B`; keep Auditor faithfulness local (no Flash calls offline).
- **Satisfies:** REQ-MOD-001..006, REQ-AUD-002, REQ-GATE-001.
- **Done when:** (a) a high-complexity task ensures `B` before inference; (b) an oracle refusal yields a human flag; (c) offline planning uses `B` and makes no API call; (d) A_FOCUS is selected for tasks with >32K tokens and thinking is OFF; (e) 2 Tier-A failures → 3rd attempt on B.

## Task 12 — Spec Compactor (periodic) · deps: 7

- [ ] 12.1 `spec_compactor.py`: every 5 sprints, strip completed/resolved content OUTSIDE the `## HARD CONSTRAINTS` block; the HARD CONSTRAINTS block is **never modified** by the Compactor (only by explicit human edit). Write compacted design.md (Flash, non-thinking).
- **Satisfies:** REQ-CMP-001.
- **Done when:** (a) post-compaction, the `## HARD CONSTRAINTS` block is byte-identical to the pre-compaction version; (b) completed task descriptions and obsolete context outside the block are stripped; (c) the Evaluator preservation check still passes.

## Task 13 — CLI / TUI · deps: 8, 10, 11

- [ ] 13.1 `cli.py` (Typer + Rich): `goal` (start a sprint), `resume` (manual resume), `review` (show diff + ship).
- [ ] 13.2 Surface cold-cache warnings and all human flags (cap breaches, OOM refusals, integration notice).
- [ ] 13.3 At Sprint Complete, present the diff and **explicitly state logical cross-task integration is the human's check**.
- [ ] 13.4 (Optional) Textual TUI mirroring the CLI surfaces.
- **Satisfies:** REQ-FAIL-003/004, REQ-CAD-003.
- **Done when:** a full sprint is drivable from the CLI; the integration notice appears at Sprint Complete; every human flag is visible.

## Task 14 — End-to-end sprint & session orchestration · deps: 8, 10, 11, 12, 13

- [ ] 14.1 `session.py`: session lifecycle (connectivity probe, notes freeze, warm prefix, ≥1 sprint).
- [ ] 14.2 `sprint.py`: sprint = one Phase-1 spec-lock + N Phase-2 task loops; enforce one Phase-1 fire/sprint.
- [ ] 14.3 `orchestrator.py`: tie session → cache ladder → Phase 1 → Phase-Gate → Phase 2 → Sprint Complete; offline variant.
- **Satisfies:** REQ-ORC-001, REQ-CAD-001/002/003, REQ-GATE-001, REQ-MOD-003.
- **Done when:** an online fixture project goes goal→shipped diff with one Phase-1 fire; the offline variant completes on Tier B; a cached goal reuses tasks.json with zero API.

## Task 14b — Threshold calibration (measured-then-fixed protocol) · deps: 10, 14

- [ ] 14b.1 `calibration.py`: implement the measured-then-fixed protocol (design §8.9). Accept a calibration set (known-good diffs, known-gaming diffs for the Auditor; known-match/non-match goal pairs for the semantic cache), run the measurements, compute optimal thresholds, and store results in `.saltcode/calibration/`.
- [ ] 14b.2 For the Auditor: run N-pass stability on each calibration diff, plot stability-score distributions for correct-verdict vs. wrong-verdict diffs, compute the threshold that maximizes F1.
- [ ] 14b.3 For the semantic cache: compute cosine distributions between matching and non-matching goal pairs; set threshold just above max non-match cosine. Compute PCD at this threshold; set the PCD density bars.
- [ ] 14b.4 Mark calibrated thresholds as `calibrated: true` in config; ensure uncalibrated thresholds log a warning at session open.
- **Satisfies:** REQ-AUD-005, REQ-CAL-001, REQ-CACHE-003 (AC4).
- **Done when:** (a) calibration on a fixture set produces a documented threshold + distribution plot stored in `.saltcode/calibration/`; (b) the calibrated threshold differs from the default; (c) uncalibrated thresholds are marked and warn at session open; (d) re-running calibration after model change produces a different threshold.

## Task 15 — Framework test suite & docs · deps: all

- [ ] 15.1 Author `pytest` coverage that maps tests → requirement IDs (a requirements-traceability matrix).
- [ ] 15.2 Extend the fixture repo from Task 4.5 with per-language variants (TS, Rust, Go, Python, JS) to exercise all static-gate strengths and test-runner paths.
- [ ] 15.3 Write the README: install, configure (models/thresholds/flags, **project config with `language`/`test_framework`/`test_runner_cmd`**), run a sprint, offline mode, Saltnitor wiring.
- **Satisfies:** every REQ (each must have ≥1 mapped test or documented manual check).
- **Done when:** the traceability matrix shows every requirement covered; `pytest`, `ruff`, `pyright --strict` are green in CI.

## Task 16 — (Optional) Builder escalation — online-only, default OFF · deps: 10, 11

- [ ] 16.1 Behind a default-OFF online-only flag: rebuild a 3×-failed task once on Flash before flagging human.
- [ ] 16.2 Assert it is unreachable offline and when the flag is OFF.
- **Satisfies:** REQ-OPT-001.
- **Done when:** with the flag OFF or offline, no Flash Builder call occurs and budget exhaustion flags human; with the flag ON + online, a 3×-failed task gets exactly one Flash rebuild attempt.

---

### Build order (critical path)

`0 → 1 → 2 → 3 → {4,5} → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 → 14b → 15` then optional `16`.
Tasks 4 and 5 may proceed in parallel after 2; Task 14b (calibration) runs after the first few sprints produce data; everything else is linear on the path above.
