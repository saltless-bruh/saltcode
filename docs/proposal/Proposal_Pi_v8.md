# **AI-DRIVEN CODING FRAMEWORK — Pi-OPTIMIZED (ASCII GRAPH, v8)**

> [!NOTE] VERSION LOG:
> - v5 targeted: Tier-1 risk list + Clarity/consistency list + Tradeoff re-survey
> - v6 pinned the local model + hardware target (RTX 3060 12GB / 32GB DDR5 / R7 7700)
> - v7 splits Phase 2 into 2 local tiers: Qwen3.5-9B-MTP default + Qwen3.6-35B-A3B-MTP escalation
> - v8 14-flaw audit + BIFAI-NET transplants (stability confidence, PCD cache, calibration)

> [!IMPORTANT] THIS DOCUMENT IS THE FRAMEWORK SOURCE OF TRUTH.
> `Proposal_Pi_v9.md` is an **addendum** covering the re-host onto the Pi Coding Agent (extension + skills + prompts + Python backend). Everything here — the 7-agent roster, the typed contracts, the five per-task gates, the Evaluator's four checks, the static-gate strength table, the cost model, Tradeoffs A–E, the BIFAI-NET transplants, and the full v1→v8 changelog — remains owned by **this** file and is carried alongside v9, not replaced by it.
>
> **v9 is not purely a re-host, so three things here are amended by it** (each marked inline where it appears): the **Phase-2 pipeline tail** gains a regression gate + checkpoint commit (§[3.6]); the **thinking gate** becomes graded rather than binary (§[2]); and the **cost model** is downgraded from measured to modeled pending verification (§[8]). Implementation-level detail (Saltnitor run profiles, failover, off-peak, RTK, prefix-cache economics) lives in `design.md`, the implementable blueprint. Read **v8** for the framework, **v9** for the hosting + the three amendments, **design.md** to build it.

---
# **[0] HAPPY PATH (read this first — plain English)**

You type a goal in the terminal.

- _PHASE 1 (frontier API, fires ONCE):_ Scout maps the repo from symbols only, Architect writes `design.md`, Planner derives `tasks.json`, Test Intent writes the acceptance tests (using project config for the right framework), Evaluator proves the plan is complete AND legal against the codebase. Output = typed JSON on disk. Spec locks. (~$0.001–0.004.)

- _PHASE 2 (local MoE, loops, $0 API):_ Builder implements ONE task against the tests (reading only `files_affected`, not the whole repo). A diff-format check rejects malformed output, a sandbox applies the diff, a millisecond static gate rejects broken code, a test runner rejects failing specs — all BEFORE the Auditor sees it. Auditor judges faithfulness via MULTI-PASS STABILITY (runs N=3 times, checks if the verdict oscillates — a behavioral confidence signal that works even on 7B models). Pass → apply diff to live repo, next task. Fail → retry (Tier-A sub-cap 2, then escalate to Tier B for 1 final attempt, total budget 3).

- You review the diff and ship. Logical cross-task integration is the one thing the machine does NOT guarantee — that check is yours (see Tradeoffs C/D).

---
# **[1] GLOSSARY (these units carry the whole cost + cadence model — fixed here)**

| **Unit** | **Definition** |
| --- | --- |
| **TASK** | Smallest unit. One object in `tasks.json`. Built + audited in isolation. **HARD RULE: exactly one task per Builder context window. No carryover.** |
| **SPRINT** | One Phase-1 spec-lock + the N Phase-2 task loops it produces. Exactly **ONE** Phase-1 fire per sprint (DAG-enforced). |
| **SESSION** | A continuous working window (target 2–3 hr) kept alive so the prefix cache stays warm. A session holds ≥1 sprint. Multiple sprints inside one warm session reuse the cached prefix → "Phase-1 once per sprint" and "keep sessions long" are **COMPATIBLE**, not contradictory. |

---
# **[2] TIER POLICY (reconciles the two old tier systems — base × modifier)**

The old doc had TWO conflicting tier systems (per-agent "[Flash/Pro]" labels AND a project-state selector). v5 collapses them: each agent has a capability-driven **BASE** tier; session state applies a **MODIFIER** to the Architect/Evaluator only.

| **Agent** | **Base tier** | **Session-state modifier** |
| --- | --- | --- |
| **Scout** | V4 Flash | — |
| **Architect** | V4 Pro | amend / rerun → downgrade to Flash |
| **Planner** | V4 Flash | — |
| **Test Intent** | V4 Flash | — |
| **Evaluator** | V4 Flash | fresh project → upgrade to Pro (see Tradeoff C2) |
| **Compactor** | V4 Flash | — (always non-thinking) |
| **Builder** | 9B (local) | high complexity / Tier-A sub-cap (2) → escalate to 35B-A3B |
| **Auditor** | 9B (local) | faithfulness judgment → **STABILITY-BASED** escalation: run N=3 passes, compute `stability_score`; if unstable + online → re-run on Flash. Offline: local verdict stands. |

> **Local tiers:** Tier A = `Qwen3.5-9B-MTP` UD-Q5_K_XL, in-VRAM · Tier B = `Qwen3.6-35B-A3B-MTP` UD-Q4_K_XL, offload.

**THINKING-MODE GATE** (concrete policy, was just a named box before):

> [!IMPORTANT] **AMENDED BY v9 — this gate is now GRADED, not binary.** Pi exposes `pi.setThinkingLevel(off|minimal|low|medium|high|xhigh)`, so "ON/OFF" below maps to: Architect → `high`; Scout/Planner/Test Intent/Compactor → `off`; Evaluator → `off`, raised to `medium`+ only when re-invoked after a fail; Auditor → `off`, raised only on retry / `gaming_suspected`. The *policy intent* below is unchanged — only its resolution improved. See `Proposal_Pi_v9.md` [1]–[2] and `design.md` §6.

- **Architect** → thinking ON (design reasoning)

- **Evaluator** → thinking ON only when re-invoked after a fail, else OFF

- **Auditor** → thinking ON only on retry / `gaming_suspected`, else OFF

- **all others** → thinking OFF by default

---
# **[3] MAIN FLOW**

## **[3.0] Harness · Phase-Gate router · Memory**

```text
                      👨‍💻 HUMAN-IN-THE-LOOP (TERMINAL / TUI)
─────────────────────────────────────────────────────────────────────────────
                                     | (1) Goal Input / Manual Resume
                                     v
+-------------------------------------------------------------------------------+
|                          ⚙️ AGENTIC OS (THE HARNESS)                          |
|  [ DAG Orchestrator ]              [ Tool/MCP Brokering ]                     |
|  [ Context Compactor ]             [ Phase-Gate Controller (auto) ]           |
|  [ Output Length Enforcer ]        [ Thinking-Mode Gate (policy in [2]) ]     |
|  [ Write-Path Allowlist ]          [ Retry/Loop Budget Tracker ]             |
+-------------------------------------------------------------------------------+
           |                                                      |
  (2) Routes Task State                                  (3) Manages Access to
      to Active Phase                                        Memory Tiers
           |                                                      |
+----------v--------------------+               +------------------v-----------+
| 🔀 PHASE-GATE + COST ROUTER   |               | 🧠 MEMORY ARCHITECTURE       |
|                               |               |                              |
| [CONNECTIVITY CHECK]          |               |  [LIGHTWEIGHT BRAIN]         |
|  Online  → DeepSeek V4 API    |               |  * Atomic Notes (≤5)         |
|  Offline → Local MoE Fallback |               |    auto-RAG at SESSION OPEN, |
|                               |               |    then FROZEN for session   |
| [CACHE LADDER — see [6]]      |               |    (keeps prefix cache-stable)|
|  1 exact Spec Cache hit?      |               |  * Vectorized Skills         |
|    → reuse, $0 API            |               |              |               |
|  2 semantic hit (≥thr)?       |               |  [SPEC CACHE]                |
|    → Architect CONFIRMS, then |               |  * key = goal_hash           |
|      reuse                    |               |    (norm goal + SCOPE        |
|  3 both miss → fire Phase 1   |               |     fingerprint) → tasks.json|
|                               |               |    (LanceDB, local)          |
| [TIER = policy in [2]]        |               |              |               |
|  Fresh  → Architect Pro,      |               |  [LOCAL CODE-WIKI]           |
|           Evaluator Pro       |               |  * context_report.json       |
|  Amend  → all Flash           |               |  * design.md (incl. HARD     |
|  Replan → all Flash           |               |    CONSTRAINTS block)        |
+----------+--------------------+               |  * tasks.json                |
           |                                    |  * tests/task_{id}_spec.*    |
           |                                    |  * evaluator_report.json     |
           |                                    +--------------+---------------+
           |                                                   |
```

## **[3.1] Provider-agnostic contract boundary**

```text
  ══════════════════════════════════════════════════════════════════════════════
  ║          PROVIDER-AGNOSTIC CONTRACT BOUNDARY                               ║
  ║  All Phase 1 output = typed JSON files on disk (the LOCAL CODE-WIKI).      ║
  ║  Swap DeepSeek → any API or local MoE without touching Phase 2.            ║
  ══════════════════════════════════════════════════════════════════════════════
```

## **[3.2] Prefix cache structure**

```text
    [PREFIX CACHE STRUCTURE — applied to every Phase 1 call]
    ┌──────────────────────────────────────────────────────┐
    │ 1. System Prompt              (fixed)    → cached     │
    │ 2. design.md                  (per-pass) → cached     │
    │ 3. Atomic Notes (≤5, FROZEN)  (session)  → cached     │
    │ 4. Task delta / new input     (per call) → billed     │
    └──────────────────────────────────────────────────────┘
    Segments 1+3 are byte-stable across ALL calls in a session (notes frozen).
    Segment 2 is stable within a single Evaluator pass, but MAY change between
    passes (an Evaluator→Architect re-loop re-emits design.md — expected).
    On a fresh project's first sprint, Scout has no segment 2 (design.md doesn't
    exist yet); the cache kicks in from the Planner onwards.
```

## **[3.3] (4a) ONLINE PATH — Phase 1: Planning Quintuplet**

```text
    (4a) ONLINE PATH ─────────────────────────────────────────────────────────
           |
           |   ┌─────────────────────────────────────────────────────────┐
           |   │         PHASE 1: PLANNING QUINTUPLET (API)              │
           |   │                                                         │
           |   │  ┌──────────────────────────────────────────────────┐  │
           |   │  │ AGENT 1 — SCOUT                      [Flash]     │  │
           |   │  │  Input : file tree + LSP symbol exports only     │  │
           |   │  │          ⚠️  never reads raw file bodies          │  │
           |   │  │  Via   : LSP/AST MCP server (local) — see [3.x]   │  │
           |   │  │  Output: context_report.json                     │  │
           |   │  │          { existing_patterns, relevant_files,    │  │
           |   │  │            constraints[], anti_patterns[] }      │  │
           |   │  └───────────────────┬──────────────────────────────┘  │
           |   │                      │ context_report.json              │
           |   │                      v                                  │
           |   │  ┌──────────────────────────────────────────────────┐  │
           |   │  │ AGENT 2 — ARCHITECT          [Pro→Flash amend]  │  │
           |   │  │  Input : user goal + context_report.json         │  │
           |   │  │  Output: design.md                               │  │
           |   │  │   ▸ components, data flow, non-goals             │  │
           |   │  │   ▸ HARD CONSTRAINTS block  ── MANDATORY ──       │  │
           |   │  │     MUST mirror EVERY context_report.constraint  │  │
           |   │  │     + anti_pattern verbatim (carry-through).     │  │
           |   │  │     This is how Scout's findings survive to the  │  │
           |   │  │     Planner, which never reads context_report.   │  │
           |   │  │  ⚠️  forbidden from writing tasks                │  │
           |   │  └───────────────────┬──────────────────────────────┘  │
           |   │                      │ design.md                        │
           |   │                      v                                  │
           |   │  ┌──────────────────────────────────────────────────┐  │
           |   │  │ AGENT 3 — PLANNER                    [Flash]     │  │
           |   │  │  Input : design.md only                          │  │
           |   │  │  Output: tasks.json                              │  │
           |   │  │          [{ id, description, files_affected,     │  │
           |   │  │             acceptance_criteria, depends_on,     │  │
           |   │  │             complexity: low|med|high }]          │  │
           |   │  │  ⚠️  reads design.md only — but HARD CONSTRAINTS │  │
           |   │  │     are now GUARANTEED present in design.md, so  │  │
           |   │  │     the old "constraints lost" hole is closed.   │  │
           |   │  └───────────────────┬──────────────────────────────┘  │
           |   │                      │ tasks.json                       │
           |   │                      v                                  │
           |   │  ┌──────────────────────────────────────────────────┐  │
           |   │  │ AGENT 4 — TEST INTENT                [Flash]     │  │
           |   │  │  Input : tasks.json + PROJECT CONFIG              │  │
           |   │  │          (language, test_framework, test_runner_cmd)│  │
           |   │  │  Output: tests/task_{id}_spec.*  (one per task)  │  │
           |   │  │          Writes acceptance tests BEFORE code     │  │
           |   │  │          Builder goal = make these tests pass    │  │
           |   │  │  ⚠️  cannot write implementation code            │  │
           |   │  │  NOTE: these specs are IMMUTABLE to the Builder  │  │
           |   │  │        (write-path allowlist). A bad spec is     │  │
           |   │  │        fixed by re-running Test Intent with the  │  │
           |   │  │        Auditor's feedback (audit_result.detail), │  │
           |   │  │        NOT by the Builder editing tests. (C3)    │  │
           |   │  └───────────────────┬──────────────────────────────┘  │
           |   │                      │ tests/task_{id}_spec.*           │
           |   │                      v                                  │
           |   │  ┌──────────────────────────────────────────────────┐  │
           |   │  │ AGENT 5 — EVALUATOR        [Flash→Pro on fresh]  │  │
           |   │  │  Input : tasks.json + design.md + context_report │  │
           |   │  │  FOUR CHECKS:                                    │  │
           |   │  │   1 traceability →  every task ↦ a design stmt   │  │
           |   │  │   2 coverage     →  every design req ↦ a task    │  │
           |   │  │   3 preservation →  every context_report         │  │
           |   │  │       constraint/anti_pattern is present in the  │  │
           |   │  │       design.md HARD CONSTRAINTS block           │  │
           |   │  │       (catches Architect dropping a constraint)  │  │
           |   │  │   4 compliance   →  NO task violates any         │  │
           |   │  │       constraint/anti_pattern                    │  │
           |   │  │       (catches a plan that is internally         │  │
           |   │  │        consistent but illegal vs the codebase)   │  │
           |   │  │  Output: evaluator_report.json                   │  │
           |   │  │   { status, gaps:[{ id, type, detail, target }],│  │
           |   │  │     routing_summary }                            │  │
           |   │  │  GAP CLASSIFICATION + ROUTING (was Planner-only):│  │
           |   │  │   • design_gap          → loop to ARCHITECT      │  │
           |   │  │   • plan_gap            → loop to PLANNER         │  │
           |   │  │   • constraint_violation→ loop to PLANNER         │  │
           |   │  │       (unless it conflicts with design.md        │  │
           |   │  │        itself → ARCHITECT)                       │  │
           |   │  │  RULE: any design_gap present → Architect loop   │  │
           |   │  │        (re-emit design.md, then Planner re-runs).│  │
           |   │  │  LOOP CAPS:  Architect ≤2,  Planner ≤3 / sprint  │  │
           |   │  │        → exceed → FLAG HUMAN with gap report.    │  │
           |   │  │        No silent spinning, no silent under-cover.│  │
           |   │  └──────────────────────────────────────────────────┘  │
           |   └─────────────────────────────────────────────────────────┘
           |
```

## **[3.x] LSP/AST MCP server + tool broker**

```text
           |   [3.x] LSP/AST MCP SERVER + TOOL BROKER
           |   ┌─────────────────────────────────────────────────────────┐
           |   │  v5 uses a LOCAL LSP/AST MCP server:                    │
           |   │    tools:  where_is · find_references · outline         │
           |   │    backend: language server per repo                     │
           |   │            (pyright | tsserver | rust-analyzer | gopls)  │
           |   │  • returns symbols / ASTs only, runs on localhost        │
           |   │  • NEVER transmits source off-box → privacy boundary     │
           |   │    enforced by the tool, not just stated.                │
           |   │                                                          │
           |   │  TOOL BROKER (v8): also exposes a SCOPED read_file tool: │
           |   │    Scout  → read_file DENIED (AST-only; API-routed)     │
           |   │    Builder→ read_file ALLOWED for task.files_affected    │
           |   │              only (broker rejects other paths). Gives    │
           |   │              the Builder enough context (imports,        │
           |   │              existing logic) without design.md, sibling  │
           |   │              tasks, or unrelated files. Stays on-box.    │
           |   │    Others → read_file DENIED                            │
           |   └─────────────────────────────────────────────────────────┘
```

## **[3.4] (4b) OFFLINE PATH — local fallback**

```text
    (4b) OFFLINE PATH ────────────────────────────────────────────────────────
           |
           |   ┌─────────────────────────────────────────────────────────┐
           |   │  LOCAL FALLBACK  (offline Phase-1 planning)             │
           |   │  Model: Tier-B escalation model →                       │
           |   │    unsloth/Qwen3.6-35B-A3B-MTP (UD-Q4_K_XL)             │
           |   │  ALL Phase-1 agents (Scout, Architect, Planner, Test    │
           |   │  Intent, Evaluator) run on Tier B. The §2 tier table    │
           |   │  (Flash/Pro) applies to the ONLINE path only; offline   │
           |   │  overrides all to Tier B. Scout still uses the LSP/AST  │
           |   │  MCP (always local), but its LLM call goes through      │
           |   │  Tier B. Auditor faithfulness stays on active local     │
           |   │  model + heuristics (no Flash escalation). Same output  │
           |   │  contracts. Slower wall-clock, not weaker.              │
           |   └─────────────────────────────────────────────────────────┘
```

## **[3.5] (5) BREAKPOINT — spec locks, Phase-Gate fires**

```text
  ══════════════════════════════════════════════════════════════════════════════
  ║  (5) BREAKPOINT HIT                                                        ║
  ║      Spec locked. Evaluator passed (4 checks). Contracts in LOCAL CODE-WIKI║
  ║      Spec hash (goal + scope fingerprint) stored → LanceDB Spec Cache.     ║
  ║      Phase-Gate fires automatically. No human action required.             ║
  ══════════════════════════════════════════════════════════════════════════════
```

## **[3.6] Phase 2: Execution Pair (local — 2 tiers)**

> [!IMPORTANT] **AMENDED BY v9 — the tail of this loop changed.** The five per-task gates below (diff-format → sandbox → static → test → Auditor) are current and unchanged. But where this diagram ends — `apply diff to LIVE repo → mark task done → next task` — **v9 inserts two steps**: `apply_live` now leaves the tree **uncommitted**, a **regression gate** (full existing suite) runs on the integrated tree, and only then is a **checkpoint** (git commit + state snapshot) written. That checkpoint is what makes auto-advance resumable and roll-back-able. See `Proposal_Pi_v9.md` *[ADDED in V9 — checkpoint system]* and `design.md` §10.1 (REQ-CKP-001..009). **Read this section for the gates; read v9/design.md for what happens after they pass.**

```text
           v
    ┌─────────────────────────────────────────────────────────────────────┐
    │              PHASE 2: EXECUTION PAIR (LOCAL — 2 TIERS)              │
    │  HW TARGET: RTX 3060 12GB + 32GB DDR5 + Ryzen 7 7700               │
    │                                                                     │
    │  ── TIER A · DEFAULT (low/med complexity) — FULLY IN VRAM ───────── │
    │  Model: unsloth/Qwen3.5-9B-MTP-GGUF    quant = UD-Q5_K_XL          │
    │   hybrid DeltaNet (only 8/32 layers grow KV) · 256K ctx · MTP      │
    │                                                                     │
    │   THREE ROUTER PROFILES (v8):                                      │
    │     A_STD  (default): -ngl 99 -fa on -c 65536 --cache-type-k q8_0  │
    │            --cache-type-v q8_0                                      │
    │     A_FOCUS (long ctx): same model, -c 262144 --ubatch-size 512    │
    │            (for tasks with large context needs; thinking OFF)       │
    │   Harness selects A_STD vs A_FOCUS based on task input token count │
    │   (threshold configurable, default 32K tokens).                    │
    │   MTP: --spec-type mtp --spec-draft-n-max 3 → OPT-IN only via     │
    │        config flag, default OFF until benchmarked on user's build.  │
    │                                                                     │
    │   VRAM TRIANGLE (hold any two, not all three on 12GB):             │
    │     • brain power  → thinking ON + A_STD (modest ctx)              │
    │     • longer focus → A_FOCUS (256K, thinking OFF)                  │
    │     • throughput   → MTP + A_STD                                   │
    │                                                                     │
    │  ── TIER B · ESCALATION (high complexity / Tier-A sub-cap hit) ──── │
    │  Model: unsloth/Qwen3.6-35B-A3B-MTP-GGUF   quant = UD-Q4_K_XL      │
    │   MoE 35B/3B active · agentic-coding (SWE-bench Verified 73.4) ·   │
    │   strong MCP/tool use · Thinking Preservation · STABLE MTP         │
    │   RUN (hybrid): -ngl 99 -ot ".ffn_.*_exps.=CPU" -fa on            │
    │        --spec-type mtp --spec-draft-n-max 3                        │
    │      → experts in RAM (~21GB total, ~12–24 t/s)                   │
    │   FIRES WHEN: task.complexity == high  OR  Tier A sub-cap (2) hit. │
    │   BUDGET: Tier-A sub-cap = 2 retries. On 3rd attempt → Tier B.    │
    │           If 3rd also fails → FLAG HUMAN. Total shared budget = 3. │
    │   (picked over Qwen3-Coder-30B-A3B: newer gen, higher SWE-bench,   │
    │    benchmarked MCP/tool use, stable MTP. Swap back = one line.)    │
    │                                                                     │
    │  ROLES: Builder + Auditor run SEQUENTIALLY → one model resident.    │
    │     Whichever tier is active serves both. Auditor's hard           │
    │     faithfulness call still escalates to Flash online; offline it  │
    │     stays on the active local model + heuristics.                  │
    │                                                                     │
    │  tasks.json ──► [AGENT 6 — BUILDER]                                 │
    │                  Input : ONE task object (hard limit per ctx window) │
    │                          + AST via LSP/AST MCP                      │
    │                          + SCOPED file bodies (only files in         │
    │                            task.files_affected — see [3.x])         │
    │                          + tests/task_{id}_spec.* (target to pass)  │
    │                  Output: unified diff (validated format)            │
    │                  Rule  : max 1 task per context. no carryover.      │
    │                  WRITE-SCOPE ALLOWLIST (anti-gaming invariant):     │
    │                     ✅ may write implementation files               │
    │                     ⛔ may NOT create/edit/delete tests/**          │
    │                        → cannot "pass" by weakening or deleting     │
    │                          the spec. Enforced by the harness.         │
    │                     |                                               │
    │                     v                                               │
    │                 [LSP/AST MCP server]  (was "notebooklm-mcp")        │
    │                 retrieves symbols/ASTs only · localhost · no upload │
    │                 [DIFF FORMAT CHECK]                                  │
    │                  Is output a valid unified diff? (git apply --check) │
    │                  MALFORMED → impl_fail, short-circuit to Builder.   │
    │                     |                                               │
    │                     v                                               │
    │                 [SANDBOX APPLY — git worktree / temp copy]          │
    │                  Diff applied to sandbox, NEVER the live tree.      │
    │                     |                                               │
    │                     v                                               │
    │                 [STATIC ANALYSIS — THE FIRE ALARM]                  │
    │                  tsc / eslint / cargo check / ruff (per lang, [7])  │
    │                  Zero VRAM. Millisecond execution. Hard gate.       │
    │                  DIRTY  → discard sandbox. SHORT-CIRCUIT to Builder │
    │                           with the lint reason. Auditor is NOT      │
    │                           called. Counts against SHARED budget.     │
    │                  CLEAN  → proceed to test runner.                   │
    │                     |  (clean only)                                 │
    │                     v                                               │
    │                 [TEST RUNNER — on sandbox]                          │
    │                  Runs task spec (pytest/jest/cargo test/go test)    │
    │                  per project config's test_runner_cmd.              │
    │                  FAIL  → discard sandbox. Short-circuit to Builder  │
    │                          with test output. Counts against budget.   │
    │                  PASS  → results forwarded to Auditor alongside     │
    │                          the clean static report.                   │
    │                  (if no test_runner_cmd configured → SKIP)          │
    │                     |  (pass only)                                  │
    │                     v                                               │
    │  code diff ──► [AGENT 7 — AUDITOR]                                  │
    │                  Input : diff + acceptance_criteria                 │
    │                          + static analysis output (clean)           │
    │                          + TEST RUNNER RESULTS (pass + output)      │
    │                          + tests/task_{id}_spec.* CONTENT           │
    │                  ┌─ FAITHFULNESS GATE ───────────────────────────┐ │
    │                  │ (a) heuristics (zero cost): flag if diff has   │ │
    │                  │     - return literals matching test fixtures   │ │
    │                  │     - empty / throw-only bodies under test     │ │
    │                  │     - branches keyed on known test inputs      │ │
    │                  │ (b) MULTI-PASS STABILITY (adapted from        │ │
    │                  │     BIFAI-NET v5.2 Stability Score/GaC):      │ │
    │                  │     Run judgment N=3 times with varied         │ │
    │                  │     conditions (temperature jitter, evidence   │ │
    │                  │     reordering). Compute:                      │ │
    │                  │       stability_score = 1-(changes/(N-1))      │ │
    │                  │       gac = pass where verdict first stabilizes│ │
    │                  │     Stable (same verdict 3/3) = trustworthy.   │ │
    │                  │     Oscillating = unreliable → escalate.       │ │
    │                  │     online + unstable → re-run on Flash;       │ │
    │                  │     offline → majority local verdict stands.   │ │
    │                  │     Threshold set by MEASURED-THEN-FIXED       │ │
    │                  │     calibration protocol [11], NOT guessed.    │ │
    │                  └────────────────────────────────────────────────┘ │
    │                  Output: { status, reason, next_action,            │
    │                            stability: {n_passes, verdicts[],       │
    │                              stability_score, gac} }               │
    │                    reason ∈ {                                       │
    │                       pass,                                         │
    │                       impl_fail,        → Builder retry             │
    │                       gaming_suspected,  → Builder retry w/ "no     │
    │                                            hardcoding" reason       │
    │                       spec_defect }      → re-run TEST INTENT       │
    │                                            (NOT a Builder retry)    │
    │                  ⚠️  cannot write code, can only judge              │
    │                     |                                               │
    │            ┌────────┴────────┐                                      │
    │          PASS              FAIL                                     │
    │            │                 │  per-task SHARED budget:             │
    │            v                 │  diff-format + static + test + impl  │
    │    apply diff to LIVE repo  │  + gaming retries ≤ 3 total          │
    │    (sandbox → real only     │  TIER-A SUB-CAP = 2: after 2 fails   │
    │     after Auditor passes)   │    on Tier A, 3rd attempt escalates  │
    │    mark task done           │    to Tier B. If 3rd also fails →    │
    │    next task in tasks.json  │    FLAG HUMAN. (no silent looping)   │
    │                              │  spec_defect → Test Intent re-spec   │
    │                              │    with audit_result.detail as       │
    │                              │    feedback (≤1); NOT a retry.       │
    │                              │  budget exhausted → FLAG HUMAN       │
    │                              └──► targeted reason ──► Builder retry │
    └─────────────────────────────────────────────────────────────────────┘
```

## **[3.7] (6) Sprint complete → periodic Spec Compactor**

```text
  (6) SPRINT COMPLETE
      Human audits output (esp. LOGICAL cross-task integration — Trade B).
      If arch change needed  → Phase-Gate checks Cache Ladder [6] first.
        exact / confirmed-semantic hit → reuse prior spec (zero API cost).
        miss → new Phase 1 call (tier per policy [2]).
      If good → ship.

           |
           | Every 5 sprints (periodic, not per-session)
           v
    ┌─────────────────────────────────────────────────────────────────────┐
    │  🗜️ SPEC COMPACTOR                    [V4 Flash, non-thinking]      │
    │  Reads : design.md + completed task history                         │
    │  Strips: completed decisions, resolved discussions, old context     │
    │  ⚠️  NEVER strips anything from the ## HARD CONSTRAINTS block.      │
    │     HARD CONSTRAINTS are removed only by explicit human edit.       │
    │     (prevents compactor→Evaluator→Architect oscillation loop)       │
    │  Writes: compacted design.md (prefix stays lean across weeks)       │
    │  Runs once per 5 sprints. Cost ≈ one small Flash call.             │
    └─────────────────────────────────────────────────────────────────────┘
```

---
# **[4] AGENT ROSTER — FULL 7-AGENT SWARM (tiers per Tier Policy [2])**

**PHASE 1 — PLANNING QUINTUPLET (Frontier API)**

| **#** | **Agent** | **Tier** | **Flow** |
| --- | --- | --- | --- |
| 1 | **SCOUT** | Flash | file tree + LSP exports → `context_report.json` |
| 2 | **ARCHITECT** | Pro (Flash amend) | goal + context_report → `design.md` (+HARD CONSTRAINTS) |
| 3 | **PLANNER** | Flash | `design.md` only → `tasks.json` |
| 4 | **TEST INTENT** | Flash | `tasks.json` + project config → task_spec files (immutable) |
| 5 | **EVALUATOR** | Flash (Pro fresh) | tasks + design + context → classified gap report / pass |

**PHASE 2 — EXECUTION PAIR (Local, 2-tier)**

| **#** | **Agent** | **Tier** | **Flow** |
| --- | --- | --- | --- |
| 6 | **BUILDER** | Qwen3.5-9B (→35B-A3B) | one task + AST + scoped files + spec → unified diff |
| 7 | **AUDITOR** | Qwen3.5-9B (→35B-A3B) | diff + criteria + test results + spec → stability verdict |

> **Tier A:** `Qwen3.5-9B-MTP` UD-Q5_K_XL (in-VRAM, default) · **Tier B:** `Qwen3.6-35B-A3B-MTP` UD-Q4_K_XL (offload; high-complexity / sub-cap).
> Faithfulness → **STABILITY-BASED**: N=3 passes; unstable + online → Flash.

**PERIODIC**

| **#** | **Agent** | **Tier** | **Flow** |
| --- | --- | --- | --- |
| — | **SPEC COMPACTOR** | Flash | every 5 sprints → compacted `design.md` |

---
# **[5] TYPED CONTRACT MAP — WHAT EACH AGENT READS AND WRITES**

| **Agent** | **Reads** | **Writes** |
| --- | --- | --- |
| **Scout** | `[ file_tree, LSP_symbols ]` (via local LSP/AST MCP) | `context_report.json { …, constraints[], anti_patterns[] }` |
| **Architect** | `[ user_goal, context_report.json ]` | `design.md` ── MUST include HARD CONSTRAINTS block mirroring context_report constraints + anti_patterns **verbatim** (carry-through) |
| **Planner** | `[ design.md ]` ← still never reads context_report | `tasks.json` — but constraints are now guaranteed present in `design.md` |
| **Test Intent** | `[ tasks.json, PROJECT CONFIG (language, test_framework, test_runner_cmd) ]`<br>ON RE-SPEC: also reads `audit_result.detail` (the Auditor's feedback) | `tests/task_{id}_spec.*` ← immutable to Builder thereafter |
| **Evaluator** | `[ tasks.json, design.md, context_report.json ]` ← now reads context_report | `evaluator_report.json` — gaps typed `{design_gap\|plan_gap\|constraint_violation}` → design_gap ⇒ Architect ; else ⇒ Planner ; caps A≤2 / P≤3 |
| **Builder** | `[ tasks.json[current], AST, SCOPED FILE BODIES (only files in task.files_affected — broker-enforced), task_spec ]` | `unified diff` ← one task per ctx window; `tests/**` NOT writable |
| **Auditor** | `[ unified diff, acceptance_criteria, static_analysis (clean), TEST RUNNER RESULTS (pass + output), task_spec CONTENT ]` | `audit_result.json` — reason `{pass\|impl_fail\|gaming_suspected\|spec_defect}` + stability `{ n_passes, verdicts[], stability_score, gac }`; `spec_defect` ⇒ Test Intent re-run with detail feedback (not a retry) |

---
# **[6] CACHE LOOKUP LADDER (was: two undefined caches — order now fixed)**

On a new goal, the Phase-Gate runs this ladder **TOP-DOWN**:

**1. SPEC CACHE (exact)**

- `key = sha256( normalized_goal + SCOPE_FINGERPRINT )`

- **SCOPE RESOLUTION (v8 fix — the chicken-and-egg):**
    - At **STORE** time (after Phase 1): `scope = sorted(tasks.json[*].files_affected)`.
    - At **LOOKUP** time (before Phase 1): scope comes from either:
        - **(a)** explicit `--scope` CLI arg (target files/modules), or
        - **(b)** a **LIGHTWEIGHT SCOPE PROBE** — a single `outline` MCP call on the repo root or goal-mentioned paths → sorted top-level modules. This is a **TOOL CALL, not a Phase-1 fire**, so it does not break the "one Phase-1 per sprint" invariant.
        - **(c)** if neither is available (empty repo): scope is empty, key degrades to goal-only matching.

- **hit** → reuse `tasks.json`, **ZERO API**. ── stop ──
- **miss** → ↓

**2. SEMANTIC CACHE (fuzzy)**

- `embed(goal + scope_fingerprint)`, cosine ≥ threshold.

- Before Architect confirmation, compute **PRIOR CLUSTER DENSITY (PCD)** (adapted from BIFAI-NET v5.2):
    - `PCD` = count of cached specs within a cosine radius of the query, normalized by cache size.
    - **HIGH PCD** (dense neighborhood) → match is in well-trodden territory → Architect confirmation can be cheap/fast (or skipped if very high).
    - **LOW PCD** (sparse, goal is an outlier) → even if cosine found a candidate, this is genuinely novel → require full Architect confirmation or fall through to Phase 1.

- Cosine threshold + PCD bars set by the **MEASURED-THEN-FIXED** protocol **[11]**.
- **candidate** → Architect CONFIRMS (PCD-adaptive bar) → reuse ; **no** → ↓

**3. MISS** → fire Phase 1 (tier per policy **[2]**).

> **WHY scope is in the key:** goal-string similarity alone collides on "rate-limit the **LOGIN** endpoint" vs "rate-limit the **SIGNUP** endpoint". Adding scope makes false reuse far less likely (cost: a few true hits lost — Trade C5).

---
# **[7] STATIC-GATE STRENGTH BY LANGUAGE (the fire-alarm is not equally loud)**

This framework's safety rails are **STRONGEST** in typed + LSP ecosystems and **SOFTER** in dynamic ones. "Daily driver for ANY session" must account for this:

| **Lang / stack** | **Static gate** | **Strength** | **Note** |
| --- | --- | --- | --- |
| **TypeScript** | `tsc` + `eslint` | **HARD** | type errors block the diff |
| **Rust** | `cargo check`/`clippy` | **HARD** | borrow + type guarantees |
| **Go** | `build` + `vet` | **HARD** | compiler-backed |
| **Python** | `pyright`(strict) + `ruff` | **MEDIUM** | run pyright in STRICT mode; mypy optional/slower |
| **JS (no TS)** | `eslint` only | **SOFT** | no type layer → Auditor carries most of the weight |

> **Rule of thumb:** the SOFTER the static gate, the MORE the Auditor's faithfulness judgment matters → prefer the Flash-escalated Auditor on SOFT-gate languages.

---
# **[8] COST PROFILE — MARATHON SESSION REALITY (corrected: Flash vs Pro)**

Phase 1 fires **ONCE** per sprint (DAG-enforced). Phase 2 loops N× locally ($0 API). The old headline assumed the FLASH path everywhere. But fresh projects route the **ARCHITECT** (and Evaluator) to **PRO** — so there are TWO real per-sprint costs:

> [!WARNING] **QUALIFIED BY v9 — these figures are a MODEL, not a measurement.** Every number below leans on the cached-input rate ($0.0028/M) landing as designed. That discount is **provider-side** and keyed on the *serialized request prefix* — which, under the Pi re-host, **Pi assembles, not Saltcode** (it injects tool schemas around our content). So the cache-hit rate must be **verified empirically** via `pi.on("before_provider_request")` before these costs are trusted. Treat them as an **upper-bound target**. See `Proposal_Pi_v9.md` *[REVISED in V9]* and `design.md` §15.

**── A. FLASH-DOMINANT SPRINT (amend / rerun / replan) ──**

| **Component** | **Rate** | **Cost** |
| --- | --- | --- |
| ~13,000 cached input tokens | @ $0.0028/M | $0.000036 |
| ~2,000 delta input tokens | @ $0.14/M | $0.00028 |
| ~3,000 output tokens | @ $0.28/M | $0.00084 |
| | **Total** | **≈ $0.001 / sprint** |

**── B. FRESH-PROJECT SPRINT (Architect + Evaluator on Pro) ──**

V4 Pro ≈ $0.435/M input · $0.87/M output (post-2026-05-31 steady-state, = the old 75% promo made permanent; was 4× higher before). Pro is **~3.1× Flash per token — NOT 12×** (the 12× was vs pre-discount Pro). Only the Pro-routed agents' **OUTPUT** is upcharged; prefix stays cached:

| **Component** | **Cost** |
| --- | --- |
| Flash baseline | ≈ $0.001 |
| + Pro upcharge on Architect + Evaluator output | ≈ $0.001 – 0.003 |
| **Total** | **≈ $0.002 – $0.004 / sprint** |

> [!WARNING] v5 adds cost VARIANCE: a stricter Evaluator can trigger Architect re-loops (capped A≤2). Worst-case fresh sprint with 2 Architect re-runs on Pro is still well under **$0.02**. (Trade C2)

| **Cadence** | **Cost** |
| --- | --- |
| Weekly (5 sprints, mixed) | ≈ $0.005 – $0.05 |
| Monthly | ≈ $0.02 – $0.20 |
| Yearly (marathon, daily sessions) | ≈ $0.25 – $2.00 |

*(Ranges widened vs v4 to reflect Pro fresh-project sprints + replan variance.)*

> [!WARNING] **COST ESCALATION TRIGGERS (watch these, not agent count):**
> - Phase 1 firing mid-sprint for clarification → fix Planner / spec quality
> - Scout reading raw file bodies → enforce LSP/AST MCP only
> - `design.md` bloating after week 3+ → run Spec Compactor
> - Cold cache starts on long sessions → keep sessions ≥2hrs
> - Evaluator re-looping to Architect repeatedly → `design.md` too vague (NEW)
> - Faithfulness escalations to Flash spiking → 7B Builder is gaming (NEW)

---
# **[9] FULL CHANGELOG V1 → V8**

**[REMOVED in V2]**

- Saltnitor (manual swap alerts) → automated Phase-Gate — ⚠️ *see **[RESTORED in V6 → V8]** below: the automated form returned in v8*

- Local 14B model (VRAM/RAM split) → DeepSeek V4 API — ⚠️ *see **[RESTORED in V6 → V8]**: local models returned in v6/v7*

- Manual model loading at breakpoints → eliminated — *(NOT restored; residency is machine-managed — this is the distinction that made local viable again)*

- 7-agent flat swarm (one model) → 2-phase split swarm

- Global Infinite Brain (unlimited RAG) → Lightweight Brain (3-5 notes)

**[ADDED in V3]**

- Connectivity Check → offline fallback path

- Semantic Cache Check (LanceDB) → avoids redundant API calls

- Model Tier Selector (Pro/Flash) → routes by task complexity

- Prefix Cache Structure → stable context front-loaded

- Output Length Enforcer → JSON schema, no prose output

- Thinking-Mode Gate → thinking only on hard calls

- Spec Cache (LanceDB) → `goal_hash` → `tasks.json` store

- Local MoE Fallback node → DeepSeek-V2-Lite offline path

**[ADDED in V4]**

- Agent 4: **Test Intent** → writes specs before code (MASAI)

- Agent 5: **Evaluator** → gaps check before Phase 2 (MAAD)

- Spec Compactor (periodic) → prevents prefix bloat long-term

- Provider-Agnostic Contract Boundary → typed files = swap any API freely

- Evaluator → Planner feedback loop → replans without hitting Architect

- Builder hard limit: 1 task/ctx window → prevents 7B context drift

- Auditor wraps static analysis output → targeted fix reason, not raw lint

- Cost Profile section → marathon session reality check

**[ADDED in V5]** ← targets the Tier-1 risk list + Clarity/consistency list

**TIER-1 RISK FIXES:**

- **LSP/AST MCP server REPLACES notebooklm-mcp** → local symbol/AST extraction (`where_is`/`find_references`/`outline` + language server); fixes the privacy boundary AND the tool-category mismatch (notebooklm-mcp uploads to Google).

- Builder write-path allowlist **EXCLUDES `tests/**`** → cannot game by editing specs.

- Auditor **FAITHFULNESS GATE** → heuristics + judgment (Flash-escalated online) → guards against hardcoded/no-op/test-fixture-keyed "passing" code.

- Typed Auditor reasons `{impl_fail | gaming_suspected | spec_defect}`; `spec_defect` routes to Test Intent (not a Builder retry).

- Architect **HARD CONSTRAINTS carry-through** → Scout's constraints/anti_patterns survive to the Planner (which still reads `design.md` only).

- Evaluator now reads `context_report.json` → **+preservation +compliance** checks (4 total) → a plan can no longer be internally-consistent-but-illegal.

- Evaluator **GAP CLASSIFICATION + ROUTING** → design_gap⇒Architect, else⇒Planner, with **LOOP CAPS (A≤2 / P≤3)** then human → no infinite loop, no silent gaps.

**CLARITY / CONSISTENCY FIXES:**

- Unified **TIER POLICY** (base × session modifier) → reconciles the two old systems.

- **CACHE LADDER** → exact (goal+scope hash) → semantic (Architect-confirmed) → fire.

- Atomic Notes → auto-RAG at session open, **FROZEN** for session (prefix-stable).

- Static-analysis DIRTY path → short-circuit to Builder; shared retry budget.

- **GLOSSARY** (task/sprint/session) → resolves "Phase-1 once" vs "long sessions".

- Cost profile split FLASH vs PRO sprint; Pro corrected to ~3.1× (not 12×).

- Language static-gate strength table → makes the "any language" assumption explicit.

- Happy-Path narrative at top.

**[ADDED in V6]** ← pins the local model + hardware target

- Phase-2 model = `unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF` (UD-Q4_K_XL). Replaces deepseek-coder-7B → MoE 30.5B/3.3B active, native tool-calling (matters: Builder/Auditor must drive the LSP/AST MCP + static gate).

- **HARDWARE TARGET fixed:** RTX 3060 12GB + 32GB DDR5 + Ryzen 7 7700. Run hybrid: `-ngl 99 -ot ".ffn_.*_exps.=CPU" -c 16384 -t 8` → ~12–20 t/s.

- **QUANT CHOICE = K-quant** (UD-Q4_K_XL / Q4_K_M), **NOT IQ4_XS**: i-quants decode slower on CPU and the experts run on CPU here → K-quant gives more t/s.

- **NO-OFFLOAD FALLBACK** = `Qwen2.5-Coder-14B-Instruct` Q4_K_M (fits fully in VRAM).

- **OFFLINE Phase-1 fallback** now uses the SAME 30B-A3B (was weak V2-Lite) → one local model backs both phases AND clears the strict 4-check Evaluator.

**[ADDED in V7]** ← Phase 2 becomes a 2-tier local stack

- **TIER A** (default, low/med complexity) = `unsloth/Qwen3.5-9B-MTP-GGUF`, UD-Q5_K_XL, **FULLY IN VRAM** (no offload). Hybrid DeltaNet → only 8/32 layers grow KV, so 256K context is feasible on 12GB *with* KV-quant (q8_0) + flash-attn (`-fa on`).

- **TWO MODES on Tier A** ("build choice"): brain-power (thinking ON, modest ctx) vs longer-focus (extend `-c` toward 256K). Can't max thinking+256K+MTP at once.

- **TIER B** (escalation) = `unsloth/Qwen3.6-35B-A3B-MTP-GGUF`, UD-Q4_K_XL, hybrid offload. Fires only when `task.complexity == high` OR Tier A hits its retry cap. Chosen over Qwen3-Coder-30B-A3B: newer gen, higher agentic-coding scores (SWE-bench Verified 73.4), benchmarked MCP/tool use, STABLE llama.cpp MTP.

- **MTP caveat recorded:** Qwen3.6 MTP is stable (~1.4–2.2×); Qwen3.5-dense MTP is finicky → benchmark Tier A's `--spec-*` flags, drop them if no gain/segfault.

- Offline Phase-1 fallback upgraded 30B-A3B → Tier-B 35B-A3B (stronger planner).

- Tier Policy + roster updated; old single-model Phase-2 (Qwen3-Coder-30B-A3B) retained only as the one-line swap-back option for Tier B.

**[ADDED in V8]** ← 14-flaw structural audit + BIFAI-NET transplants

**STRUCTURAL FIXES (from audit against spec files):**

- **SPEC-CACHE CHICKEN-AND-EGG FIXED.** `scope_fingerprint` at LOOKUP time now comes from a lightweight scope probe (single `outline` MCP call) or `--scope` CLI arg, not from `tasks.json` (which doesn't exist yet). The probe is a tool call, not a Phase-1 fire, so the one-Phase-1-per-sprint invariant holds.

- **TEST RUNNER ADDED to Phase-2 loop.** Pipeline was Builder→Static Gate→Auditor, but nobody ran the tests. Now: Builder→Diff Format Check→Sandbox→Static→Test Runner→Auditor. Tests run on a disposable sandbox, never the live tree. Test failures short-circuit to Builder (count against shared budget), not the Auditor.

- **SANDBOX RULE.** The diff is NEVER applied to the live working tree until the Auditor returns `pass`. Static gate and test runner operate on a git worktree / temp copy.

- **TIER-A SUB-CAP = 2** within shared budget of 3. Previously "Tier A retry-cap → escalate" contradicted "budget ≤3 → FLAG HUMAN" (escalation never fired). Now: 2 failures on Tier A → 3rd attempt on Tier B → if that also fails → FLAG HUMAN.

- **DIFF FORMAT VALIDATION.** Builder must emit a valid unified diff (`git apply --check`). Malformed output is treated as `impl_fail`, short-circuited before sandbox apply.

- **BUILDER GETS SCOPED FILE-READ.** Was AST-only (insufficient for correct code). Now: Builder may read ONLY files in `task.files_affected` via a broker-enforced scoped `read_file` tool. No `design.md`, no sibling tasks, no other files.

- **SPEC_DEFECT FEEDBACK.** Re-spec was blind (same input → same bad spec). Now: Test Intent receives `audit_result.detail` on re-spec, explaining what was wrong.

- **TEST INTENT GETS PROJECT CONFIG** (`language`, `test_framework`, `test_runner_cmd`) so it writes specs in the correct framework, not guessing pytest vs jest.

- **PREFIX CACHE stability scoped correctly:** segments 1+3 always stable; segment 2 (`design.md`) stable per Evaluator pass, may change on re-loops (expected).

- **COMPACTOR never strips HARD CONSTRAINTS block.** Only human edits remove constraints. Prevents compactor→Evaluator→Architect oscillation.

- **OFFLINE path** explicitly covers ALL agents (incl. Scout) on Tier B.

- **THREE TIER-A PROFILES:** `A_STD` (64K ctx), `A_FOCUS` (256K ctx), `B`. Replaces the ambiguous `-c 131072` with distinct router sections matching Saltnitor.

**BIFAI-NET TRANSPLANTS (adapted from BIFAI-NET v5.2 — NCA-driven network forensics):**

- **STABILITY-BASED CONFIDENCE** replaces self-assessed `confidence: float`. Auditor runs N=3 passes with temperature jitter / evidence reordering; computes `stability_score = 1-(verdict_changes/(N-1))` and GaC. Unstable + online → Flash re-judgment. Local models are poorly calibrated at self-assessing confidence; **stability is a behavioral signal that works regardless of model size**.

- **PCD (Prior Cluster Density)** for semantic cache. Dense neighborhood around a query → high reuse confidence (cheap confirmation). Sparse → skeptical (full confirm or fall through to Phase 1). Adds a continuous quality signal to the binary cosine threshold.

- **MEASURED-THEN-FIXED THRESHOLD PROTOCOL** (new **§11**). All configurable thresholds (Auditor stability, semantic cosine, PCD bars) calibrated from benchmark data rather than guessed. First sprint uses conservative defaults marked uncalibrated.

**[RESTORED in V6 → V8]** ← *(added by the v9-era lineage audit: three V2 removals came back and the changelog never said so)*

> [!IMPORTANT] The `[REMOVED in V2]` block above was accurate **in V2** and has been silently contradicted since. Recording the reversals here so the record stops lying to a reader who starts at the top.

- **LOCAL MODELS — restored in V6, expanded in V7.** V2 removed the local 14B ("→ DeepSeek V4 API") and went all-cloud. V6 pinned local back (Qwen3-Coder-30B-A3B + a fixed hardware target); V7 split it into the 2-tier stack (Tier A in-VRAM / Tier B offload). Local is now the **default** for all of Phase 2, and the whole offline path. **Status: reversed.**

- **SALTNITOR — restored in V8** (as the serving/router layer, not the V1 form). V2 removed it because it required *manual swap alerts*, which broke autonomous operation. It re-entered in V8 via the **THREE TIER-A PROFILES** entry above — "distinct router sections matching Saltnitor" — where the profiles (`A_STD` / `A_FOCUS` / `B`) **are** Saltnitor `router.ini` sections. **Status: reversed** — but note the changelog never announced it, so V8 shipped with a tombstone contradicting its own live usage. Fixed here.

- **MANUAL MODEL LOADING — NOT restored, and that is the point.** V2's real objection was never *local models*; it was **the human being in the residency loop**. Saltnitor returns only because residency is now machine-managed (`POST /v1/ensure {profile}` + a VRAM oracle that refuses an OOM rather than crashing the box). **The idea was never wrong — it was premature.** Local + automated ⇒ viable; local + manual ⇒ correctly killed in V2.

> **Reading of the arc:** V1 local+manual → V2 all-cloud+automated → V6/V7 local restored (automated) → V8 Saltnitor restored as the router → V9 Saltnitor becomes a registered Pi provider. This is a **pendulum that landed on the synthesis**, not thrash: keep the automation V2 bought, regain the local/$0/air-gap properties V2 sold, by making a machine — not a human — manage model residency.

**[KEPT throughout V1 → V8]**

| **Component** | **Status** |
| --- | --- |
| DAG Orchestrator | ✅ |
| Context Compactor | ✅ |
| Local CODE-WIKI (SDD) | ✅ `design.md` + `tasks.json` |
| LSP-Sync (now a real LSP/AST MCP) | ✅ AST-only, LOCAL (crown jewel) |
| Static Analysis Fire Alarm | ✅ hard gate, zero VRAM |
| LanceDB Embeddings | ✅ RAG + Spec Cache |

---
# **[10] TRADEOFF NOTES (re-surveyed after the v5 → v8 updates)**

## **── A. RESOLVED / MITIGATED BY V5 → V8 ──**

- ✔ **Semantic-cache false positives → CLOSED.** Key now includes scope fingerprint + a Flash Architect-confirmation gate before any fuzzy reuse. v8 adds **PCD** (Prior Cluster Density) for an adaptive confirmation bar. *(was a Tradeoff)*

- ✔ **"Internally-consistent but wrong-vs-codebase" plan → CLOSED** via Evaluator checks 3 & 4 (constraint preservation + compliance) reading `context_report`.

- ✔ **Auditor confidence measurement → CLOSED (v8).** Self-assessed `confidence: float` replaced by stability-based measurement (N=3 passes, behavioral signal). Works reliably even on 7B models. Threshold calibrated via measured-then-fixed **[11]**.

- ◑ **"Weakest model does the most code-shaped work, cannot escalate" → PARTIAL.** The Auditor's JUDGMENT can now escalate to Flash (stability-gated), and gaming is gated. But the Builder's CODE GENERATION still does not escalate by default. **OPEN DECISION:** add a "Builder escalation" (rebuild a 3×-failed task once on Flash before flagging human)? It mirrors the Phase-1 fallback and is ~free. Recommended, but it breaks Phase-2 offline purity (see **C4**) → make it an online-only switch.

## **── B. STILL STANDS (unchanged by v5) ──**

> Builder sees no `design.md` and no sibling tasks → great for context hygiene, but **LOGICAL cross-task integration is still not machine-guaranteed**. v5 adds type-level safety (static gate) + constraint legality (Evaluator), **NOT** logical composition (task-2 returns cents, task-5 passes dollars; both are `number`). This check remains the **HUMAN's** job at Sprint Complete. State it; don't hide it.

## **── C. NEW SAME-TIER TRADEOFFS INTRODUCED BY V5 (watch these) ──**

- **C1 ANTI-GAMING vs PHASE-2 OFFLINE PURITY.** The faithfulness gate escalates to Flash online when the stability measurement is low (oscillating verdicts) — so Phase 2 is no longer strictly $0/air-gapped on those calls. *Mitigation:* offline mode keeps the faithfulness call on the local model + heuristics only (majority verdict stands), accepting higher gaming risk.

- **C2 STRICTER EVALUATOR = MORE LOOPS + HIGHER/VARIABLE PHASE-1 COST.** Two extra checks and an Architect route mean a fresh, constraint-dense project can trigger Pro re-runs. Loop caps bound it (A≤2), but per-sprint cost variance is now real (still < $0.02 worst case). Also: the **EVALUATOR is now a heavier single point of failure** — a Flash Evaluator doing semantic compliance can itself false-fail (wasted replans) or false-pass (reopens the hole it closed). v5 mitigates by upgrading Evaluator to Pro on fresh projects. v8 adds stability-based measurement to the Auditor (Phase 2) — a similar approach could apply to the Evaluator if false-pass/false-fail rates are observed.

- **C3 SPEC IMMUTABILITY shifts burden onto TEST INTENT.** Builder can no longer "fix" a bad spec (good — kills gaming), but a genuinely faulty/over-constrained spec now HARD-BLOCKS (3× fail) instead of silently degrading. v5 adds the `spec_defect` Auditor reason → routes to a Test Intent re-spec (≤1). v8 adds a **FEEDBACK CHANNEL**: re-invoked Test Intent receives `audit_result.detail` so it knows WHAT was wrong with the prior spec (not a blind re-run).

- **C4 BUILDER-ESCALATION** (if adopted, see **A**) re-introduces API cost/dependency into the "free local" phase — same tension as C1. Gate it behind an online flag.

- **C5 SCOPE-FINGERPRINT CACHE KEY** lowers the cache HIT RATE (safer, but the same goal at slightly different scope no longer matches) → marginally higher cost. Accepted as the direct price of closing the false-positive tradeoff.

## **── D. PRE-EXISTING OPERATIONAL CAVEATS (kept) ──**

- ⚠️ **INTERNET REQUIRED** for online Phase 1 (DeepSeek API).

- ⚠️ **Send only design docs to API — never raw source.** v5 enforces this in the LSP/AST MCP tool itself, not just by convention.

- ⚠️ **Prefix cache TTL is session-scoped** — cold starts lose cached tokens. → Keep sessions long (2–3hr sprints). Batch tasks per Phase 1 call.

- ⚠️ **Provider risk:** 5 of 7 agents depend on DeepSeek API availability. → *Mitigation:* the typed contract boundary makes the provider substitutable; the local Tier-B Qwen3.6-35B-A3B fallback (v7) covers full offline operation.

- ✔ **OFFLINE FALLBACK — RESOLVED (v6→v7).** The old caveat (weak ~2.4B V2-Lite vs the strict 4-check Evaluator) is gone: offline Phase-1 planning now runs the Tier-B Qwen3.6-35B-A3B, which clears the Evaluator comfortably. *Residual:* offline planning runs at ~12–24 t/s, so air-gapped sprints are **slower, not weaker** — budget more wall-clock, not more human-flagged failures.

- ✔ **HW THROUGHPUT — IMPROVED in v7.** The default Builder is now the in-VRAM Qwen3.5-9B-MTP (no offload, faster, MTP-accelerated), so the common low/med task path is quick; the ~12–24 t/s offload cost is paid only on the rare Tier-B escalation. This is exactly the latency profile the 2-tier split buys.

- ⚠️ **NEW (v7) — TWO MTP REGIMES TO MANAGE.** Tier A (Qwen3.5-dense) MTP is the finicky DeltaNet path; Tier B (Qwen3.6) MTP is stable. Keep one recent llama.cpp build that supports both, and treat Tier A's `--spec-*` flags as **opt-in-after-benchmark**.

- ⚠️ **NEW (v7) — VRAM TRIANGLE on Tier A.** thinking + full-256K-ctx + MTP all draw on the same 12GB. You can hold any **two** comfortably, not all three. Route per task: hard→thinking, context-heavy→long ctx, throughput→MTP. Don't expect all at once.

## **── E. NEW TRADEOFFS FROM V8 ──**

- **E1 STABILITY MEASUREMENT COST.** N=3 Auditor passes per task adds ~2–3s on Tier A (9B, local). Negligible vs Builder generation time. If N=3 feels too slow on Tier B (35B offload), reduce to N=2 (still detects the most obvious oscillation). Flash escalation fires only on genuinely unstable verdicts (<15% expected).

- **E2 PCD AS SEMANTIC-CACHE SIGNAL.** PCD adds a density check but introduces a tuning surface (the PCD density bars). Miscalibrated → either too aggressive (skips confirmation → false reuse) or too conservative (always confirms → no benefit). Mitigated by the measured-then-fixed protocol **[11]**.

- **E3 SCOPED FILE-READ FOR BUILDER.** Giving the Builder actual file bodies (within `files_affected`) improves impl quality but increases context size. On Tier A with `-c 65536`, a task touching 5 large files could approach the ctx limit. *Mitigated:* the harness selects `A_FOCUS` (262K) when input token count exceeds the configurable threshold (default 32K).

---
# **[11] MEASURED-THEN-FIXED THRESHOLD CALIBRATION (adapted from BIFAI-NET v5.2)**

Several thresholds in the framework could be guessed ("default 0.7"), but **guessed thresholds are either too loose (never fires) or too tight (always fires)** because they're not grounded in the data. This protocol (adapted from BIFAI-NET v5.2's `sim_floor` measurement) fixes that: **MEASURE from benchmark data, then FIX.**

**PROTOCOL** (run once per project, or when model / embedding changes):

**1. Prepare a CALIBRATION SET of known-outcome examples:**

- **Auditor:** known-good diffs + known-gaming diffs + known-broken diffs.
- **Semantic:** goal pairs with known match/non-match labels.
- *(Can be synthetic or collected from early sprints.)*

**2. Run the measurement:**

- **Auditor:** run N-pass stability on each calibration diff. Record the `stability_score` distribution for correct-verdict vs wrong-verdict diffs. Set threshold at the natural separation (max F1 of "stable = trustworthy").
- **Semantic:** compute cosine similarities between all goal pairs. Set the cosine threshold just above the max cosine observed between non-matching pairs (no false reuse by construction). Compute PCD at that threshold; set PCD density bars at the point below which Architect confirmation is mandatory.

**3. FIX the thresholds in project config.** Document the calibration set, measurement date, and the distribution in `.saltcode/calibration/`.

**FIRST SPRINT (no data yet):** use conservative defaults:

| **Threshold** | **Default** |
| --- | --- |
| `auditor_stability_threshold` | `0.5` |
| `semantic_cosine_threshold` | `0.85` |
| PCD bars | require full confirmation (max skepticism) |

Config marks uncalibrated thresholds as `calibrated: false`; the harness logs a warning at session open so you know they're provisional.

> [!NOTE] **WHY THIS MATTERS FOR SMALL MODELS:** a 7B model asked "how confident are you?" produces a number with no grounding — it's token prediction, not probability estimation. **Stability measurement** (did the verdict change across passes?) works regardless of model size. The measured-then-fixed protocol ensures the threshold that *interprets* this signal is grounded in real data, not intuition.
