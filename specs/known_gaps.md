# Saltcode — Known Gaps

Every gap noticed **during implementation** that the task at hand did not close.
This is the running list of what the build knows is missing, unproven, or
deferred — so it is dealt with deliberately instead of rediscovered.

`tasks.md` is what we intend to build. **This file is what we know is not right yet.**

---

## How to use this file

**Before starting any task** (after the reads in `.claude/rules/source-of-truth.md`,
before writing code): read this file. Any gap whose **Closed by** names your task
is part of your work. Any gap in a file you are about to touch is context you need.

**When finishing a task:**

1. **Tick** `- [x]` every gap your change actually closed, and add the date plus
   how it was verified. A gap is closed when it no longer exists — not when it
   was worked around, and not when it moved somewhere else.
2. **Add** a new entry for every gap you noticed and did not close. Include the
   ones that are somebody else's task; that is the point of the list.
3. Never delete an entry. A ticked gap is the record that it was real and is
   gone. If a gap turns out to be a misreading, tick it and say so.

**What counts as a gap.** Something noticed while implementing that is not right
yet: an unverified gate leg, a capability deferred to a later task, spec or doc
drift, a limitation accepted for now. **What does not:** ordinary unstarted work
that already has a task — that is `tasks.md`, not a gap.

**Status key** — `- [ ]` open · `- [x]` closed (with a dated note saying how).

**Severity** — **HIGH**: can produce a wrong result or a false green. **MED**:
will block or mislead a later task. **LOW**: drift, cosmetics, docs.

---

## Open

### - [ ] G-001 — The firejail containment backend is written but never executed
**Severity:** LOW (given the confirmed single-machine deployment) ·
**Noticed:** Task 3.1 · **Closed by:** whoever first runs on a host with firejail
· **Where:** `saltcode_backend/saltcode/harness/sandbox.py` (`_firejail_argv`)

`bwrap` and Docker are both present on the build machine, so firejail — the third
fallback in REQ-SEC-005's order — has never run. Its argv is constructed from the
documented flags and is **unproven**: the flag set may be wrong, and the
`--rlimit-as` memory ceiling is an address-space limit, not the cgroup limit
REQ-SEC-001 asks for.

*Why it matters:* a containment backend that fails at exec is a Phase-2 outage;
one that runs but does not contain is worse. It is marked untested in the source.

### - [ ] G-002 — The Docker containment path has never run a container
**Severity:** MED · **Noticed:** Task 3.1 · **Closed by:** Task 9 or Task 20 ·
**Where:** `saltcode_backend/saltcode/harness/sandbox.py` (`_docker_argv`)

The Docker daemon is reachable here and the argv is built, but no container has
actually been launched through it — `bwrap` wins detection, and Docker needs a
`sandbox_image` that is deliberately not defaulted (the right image depends on
the target project's toolchain). So the volume mounts, `--read-only` + `--tmpfs`
combination, and limit flags are all unverified in practice.

*Why it matters:* Docker is the fallback when `bwrap` is unavailable — exactly
the situation where nobody wants to discover the fallback is broken.

### - [ ] G-007 — `design.md` §17's project tree is missing backend modules
**Severity:** LOW · **Noticed:** Tasks 2 and 3 · **Closed by:** Task 15.3 ·
**Where:** `specs/design.md` §17

The tree lists only `providers/embeddings.py` under `providers/`, omitting
`base.py`, `local.py` and `guard.py`; `harness/` omits `audit_log.py` and
`command_allowlist.py` as separate entries. Each of those is required by a task
step or a REQ, so the code is right and the illustration is stale.

*Why it matters:* only that a stale tree invites someone to "clean up" a module
that a requirement mandates. It is illustrative, not normative.

### - [ ] G-009 — Only the Python language server is verified; TS, Rust and Go are not
**Severity:** MED · **Noticed:** Task 4.2 · **Closed by:** Task 15.2 ·
**Where:** `saltcode_backend/saltcode/mcp/lsp_backends.py` (`LSP_COMMANDS`)

`pyright-langserver` ships in the backend's own environment, so the Python LSP
path is exercised end to end. The other three are dispatch-only: nothing here
has ever spoken LSP to `typescript-language-server`, `rust-analyzer` or `gopls`.
`/home/laz/.cargo/bin/rust-analyzer` is a **rustup shim for an uninstalled
component** — on `PATH`, exits 1 — so `command -v` reports it as present.

*Why it matters:* REQ-MCP-003 AC1 names Rust specifically. Each server has its
own handshake quirks — the Python path needed `didOpen` before `documentSymbol`
would answer at all — so the others are likely to need their own fixes, and the
AST fallback will quietly cover for them until someone looks. Task 15.2 already
plans per-language fixture variants; installing the servers there closes this.
Cheapest first step: `rustup component add rust-analyzer`.

### - [ ] G-010 — The egress guard does not cover the language-server subprocess
**Severity:** LOW · **Noticed:** Task 4.3 · **Closed by:** Task 20.3 ·
**Where:** `saltcode_backend/saltcode/mcp/lsp_ast_server.py` (`install_egress_guard`)

The guard patches `socket.connect`/`connect_ex` in **our** process. A language
server is a separate process holding the same source, and nothing here stops it
opening a socket of its own.

*Why it matters:* REQ-MCP-002 says the server "SHALL NOT transmit any repo
content to a remote host", and the language server is part of that server's
trust boundary in practice. The exposure is small — these are mainstream tools
run from a local install — but it is real, and the honest fix is to run the LSP
inside the Task-3 container rather than to widen the socket patch.

### - [ ] G-011 — `mcp.json` sits where no MCP client extension will look
**Severity:** HIGH · **Noticed:** Task 4.6 · **Closed by:** Task 20.1 ·
**Where:** `mcp.json`, `specs/design.md` §17

Both candidate extensions' sources were read (2026-07-28, tarballs only, nothing
installed). **Neither reads a repo-root `mcp.json`:**

| | config paths it actually reads |
|---|---|
| `pi-mcp-extension` 1.5.0 | `<cwd>/.pi/mcp.json`, `~/.pi/agent/mcp.json` |
| `pi-mcp-adapter` 2.15.0 | `<cwd>/.mcp.json`, `<cwd>/.pi/mcp.json`, plus globals |

So the file as authored — and the location design §17's tree shows — is read by
nothing. `<cwd>/.pi/mcp.json` is the one path **both** accept, which also keeps
REQ-EXT-015 AC1 substitutability intact. Moving it needs a design §17 amendment,
so it waits on the adoption decision.

Two further schema facts, both confirmed in source:

* The key names are right — `mcpServers` → `{command, args, env, transport,
  lifecycle}` — but `lifecycle` defaults to `"lazy"`, so `"eager"` must stay
  explicit for the server to start with the session.
* **`env` does no `${VAR}` interpolation** (`pi-mcp-extension/src/config.ts`:
  *"No ${VAR} interpolation — set vars in your shell environment instead"*).
  The current `"SALTCODE_WORKSPACE": "${workspaceFolder}"` would be passed
  through **literally**, pointing the server at a directory named
  `${workspaceFolder}`. It must be a real path or be dropped so the server's
  `os.path.abspath(".")` default applies.

A third fact bears on *which* extension: `pi-mcp-extension` — the one named in
task 4.6 and design §13 — declares peer dependencies on the **pre-rename**
`@mariozechner/pi-*` scope, while this project targets `@earendil-works/*`
(REQ-EXT-001 AC2). It may not load on Pi 0.82.1 at all.

**Deferred by the maintainer on 2026-07-28**: adopt nothing yet. Task 20.1 does
adoption, wiring and verification in one pass, when Task 13.3's `tool_call`
handler exists to verify the gating against.

*Why it matters:* a manifest the client silently ignores looks exactly like one
that works, right up until the tools never appear. Adoption is also a **trust
decision** (REQ-SEC-006) — both run with full system permissions.

### - [x] G-013 — `bwrap` reports containment on a host where the limits do not bind
**Severity:** MED · **Noticed:** Task 5 (baseline run) · **Closed:** 2026-08-02 ·
**Where:** `saltcode_backend/saltcode/harness/sandbox.py`

**Closed by applying the fix this entry already described** (maintainer-approved
2026-08-02). `_limit_wrapper` now calls a new `_systemd_run_usable()` — the same cached
functional probe `_bwrap_usable` uses for G-C07 — and on failure returns **no prefix with
`limits_enforced=False`** instead of a prefix that cannot start.

**The effect was larger than "the ceilings are now reported honestly."** On this
systemd-less container the old code returned a `systemd-run` prefix that died at exec with
*"Failed to connect to bus"*, so **every contained command failed before its payload ran**
— which is why 14 tests failed here and passed on CI. With the probe in place those **14
now pass locally**: the containment path works on any host without systemd (Docker, most
CI runners, this build container) rather than failing wholesale.

`ContainerLimits`' memory-cap test now skips honestly when `limits_enforced` is false
instead of failing, and still asserts the cap on CI where systemd exists. Verified by
`test_the_limit_wrapper_probes_capability_rather_than_presence` (prefix and
`limits_enforced` must agree, whatever the host) and
`test_an_unusable_systemd_run_yields_no_prefix_and_says_so`.

*Original entry:*

On a container with no cgroup v2 (`/sys/fs/cgroup/cgroup.controllers` absent) and
no running systemd, `sandbox_apply --check-containment` answers
`{"backend":"bwrap","verdict":"contained"}` — but 9 of the Task 3 containment
tests fail, including `test_timeout_kills_the_container`,
`test_no_network_inside_the_container` and
`test_the_host_filesystem_is_read_only_outside_the_sandbox`. The functional probe
added for G-C07 checks that a namespace can be created; it does not check that the
**resource limits** REQ-SEC-001 mandates actually bind, and `systemd-run` cannot
create a scope without systemd.

*Why it matters:* this is G-C07 one level deeper. Reporting `contained` when the
memory, CPU and time ceilings are inert claims containment that does not exist —
the same class of false green G-C07 closed for namespace creation. The probe should
assert a limit binds (e.g. a small allocation under a small cap is killed), or the
verdict should distinguish "isolated" from "isolated **and** limited".

**Sharpened 2026-07-29 (Task 9).** The cause is more specific, and worse, than "the
limits do not bind". `sandbox.py::_limit_wrapper` decides whether to apply cgroup limits
with `shutil.which("systemd-run")` — presence, not capability. Here the binary exists, so
the wrapper returns the `systemd-run --user --scope` prefix and reports
`limits_enforced=True`; but with no systemd bus that prefix fails at exec with
`Failed to connect to bus: No medium found`, so **every contained command dies before its
payload runs** while detection still reports `contained`. That is precisely the "trust
`which` over capability" antipattern **G-C07 closed for bwrap detection**, surviving
untouched in the limit wrapper.

**Concrete fix available:** give `_limit_wrapper` the same cached functional probe G-C07
gave backend detection — run `systemd-run --user --scope --quiet -- true` once and cache
the result — and on failure return no prefix with `limits_enforced=False` rather than a
prefix that cannot start. `ContainedResult.limits_enforced` already exists to express
exactly that outcome, and is currently never false. This would make the containment path
work on any host without systemd (Docker, most CI runners, this build container) instead
of failing wholesale, while still reporting honestly that the ceilings are not enforced.
**Left unapplied because `sandbox.py` is Task 3's module and Task 9 does not own it**
(`.claude/rules/stop-and-ask.md`: adjacent breakage is reported, not fixed).

Task 9 adds 6 more tests in the same position (the `@needs_container` ones in
`tests/test_task_9_gate.py`), failing here for this reason and verified on CI.

*Status here:* these 9 failures are **pre-existing and unrelated to Task 5** —
recorded before any Task 5 change and byte-identical after.

**Sharpened by contrast, 2026-07-29.** With G-016 fixed, CI run 30434177585 ran the
identical suite on a GitHub runner (cgroup v2 and systemd both present) and reported
**325 passed, 0 failed, 0 skipped** — every one of the 9 passes there. Two things
follow. The tests are sound, and Task 3's containment legs are now verified in CI
rather than only on the maintainer's box. And the gap is now stated as sharply as it
can be: the same `--check-containment` probe answers `contained` in **both**
environments, while the resource limits bind in only one of them.

### - [ ] G-014 — The real embedding path is still unexercised
**Severity:** MED · **Noticed:** Task 5 · **Closed by:** whoever first runs with
Saltnitor up (Task 14b or Task 15.2) · **Where:**
`saltcode_backend/saltcode/memory/*`, `providers/embeddings.py`

Every Task 5 test drives embeddings through an in-process double or a loopback
HTTP stub returning 3-dimensional orthogonal vectors. No test has embedded
anything with a real `bge-small`/`nomic-embed`, because no embedding endpoint and
no Saltnitor exist on this box (design §12's offline path is a mode, but the
*endpoint* is still local infrastructure that has to be running).

*Why it matters:* the orthogonal-axis double makes cosine similarity exactly 1.0
or 0.0, which is what lets the PCD assertions be hand-computed — but it means the
default `semantic_cosine_threshold` of 0.85 has never been exercised against a
realistic similarity distribution, where the interesting cases sit between 0.7 and
0.95. That is precisely what Task 14b's calibration is for; until then the
semantic tier's behaviour on real vectors is modelled, not measured.

### - [ ] G-015 — PCD scans the whole semantic cache on every lookup
**Severity:** LOW · **Noticed:** Task 5.3 · **Closed by:** unassigned ·
**Where:** `saltcode_backend/saltcode/memory/semantic_cache.py`

`lookup_semantic_result` issues `.limit(total_specs)` because PCD's denominator is
the entire cache — a top-k search cannot answer "what fraction lies within the
radius". Cost is O(cache size) per lookup.

*Why it matters:* nothing today; the cache holds one row per sprint. Worth knowing
before anyone points this at a long-lived multi-project store, where a sampled or
incrementally-maintained density estimate would be needed instead.

### - [ ] G-017 — Tool-failure exit codes are mapped only for pyright and ruff
**Severity:** MED · **Noticed:** Task 9 (PR #2 review) · **Closed by:** Task 15.2 ·
**Where:** `saltcode_backend/saltcode/static_gate/runners.py`
(`TOOL_FAILURE_EXIT_CODES`)

A static-analysis tool that fails on its *own* configuration must not be reported as
`dirty`, because `dirty` routes to the Builder and spends the shared per-task budget of
3 (REQ-FAIL-001) rewriting code that was never wrong. `pyright` (2 fatal, 3 unreadable
config, 4 bad CLI args) and `ruff` (2 abnormal termination) are mapped, each verified by
running the real binary rather than read from documentation.

**`tsc`, `eslint`, `cargo` and `go` are not mapped.** Their conventions were not
exercised on this host — no Node, Rust or Go toolchain is installed — so rather than
guess, unmapped codes fall through to `dirty`, which is the pre-existing behaviour.

*Why it matters:* a malformed `.eslintrc` or a `cargo` panic still presents to the
Builder as "your code is broken", ending in FLAG HUMAN with a misleading reason. Task
15.2 already plans per-language fixture variants; verifying each tool's failure codes
there closes this. The cheap check is the one used here — run the tool against a
deliberately broken config and read `$?`.

### - [ ] G-018 — The Rust container path binds a toolchain nobody has run
**Severity:** MED · **Noticed:** Task 9 (PR #2 review) · **Closed by:** Task 15.2 ·
**Where:** `saltcode_backend/saltcode/static_gate/toolchain.py` (`rustup_home`)

`~/.cargo/bin/cargo` is a rustup **proxy**, not the compiler; the real toolchain lives
under `RUSTUP_HOME` (default `~/.rustup`). Binding only the proxy's prefix gave the
container a `cargo` that starts and then finds no toolchain — and Rust is a HARD gate
(design §14), so it failed at the strongest link. `resolve_tool` now adds the resolved
rustup home as an extra read-only bind.

**The fix is unverified end to end.** No Rust toolchain exists on this host (G-009
records `rust-analyzer` as a rustup shim for an *uninstalled* component), so the tests
construct a fake rustup layout and assert the bind list, not a real `cargo check` inside
a container.

*Why it matters:* the bind-list computation is proven, but whether a real contained
`cargo check` succeeds is not. Rust's HARD gate is the one whose `clean` verdict the
Auditor is told to trust most (REQ-STAT-002 AC2), so it is the worst one to have
unexercised. Same fixture work as G-009 and G-017.

**Extended, 2026-07-30 (PR #2 review round 3).** Two more holes in the same fix, both
now closed in code and both still unexercised end to end for the same reason. (1) The
proxy match ran against the raw `program` string, so a path-qualified `test_runner_cmd`
argv[0] — `/opt/rust/bin/cargo` — missed `RUST_PROXY_PROGRAMS` and silently lost the
toolchain bind; identity now matches on `Path(program).name`. (2) `RUSTUP_HOME` was
never passed into the container. The bind makes the toolchain reachable, not findable:
`CONTAINER_ENV` replaces the environment wholesale and container `HOME` is
`/tmp/saltcode-home`, so the proxy's `$HOME/.rustup` fallback resolves to nothing inside
the container **even when the host uses rustup's default location**. It is now forwarded
via `ToolchainBinding.extra_env` whenever a rustup home was resolved. That this fix
needed two follow-up rounds is itself the argument for the real-toolchain fixture: every
round found a defect that a single contained `cargo check` would have surfaced at once.

### - [ ] G-019 — Obsolete-section detection is a word match, not a reading
**Severity:** LOW · **Noticed:** Task 12 (PR #2 review round 3) · **Closed by:** Task 13.9 ·
**Where:** `saltcode_backend/saltcode/contracts/spec_compactor.py`
(`OBSOLETE_HEADING_MARKERS`, `_is_obsolete_heading`)

The deterministic strip decides a section is obsolete by looking for one of seven marker
words in its heading. Round 3 found that a plain substring test deleted `## Unresolved
Issues`, `## Undone Items` and `## Uncompleted Work` — the markers are substrings of
their own negations — and matching is now whole-word. That closes the class of failure
that was actually occurring, but not the class of failure that exists: a negation spelled
as a separate word still matches. `## Not Done Yet`, `## Never Superseded`, `## Changelog
Policy (do not remove)` all contain a marker as a whole word and would be stripped with
their subsections.

*Why it matters:* less than it looks, because of how this code is reached. REQ-CMP-001
AC2 puts the real compaction on Flash and passes it through `--proposed`, where the
structural strip does not run at all; the deterministic path is the offline fallback. And
the invariant this module exists to hold — the `## HARD CONSTRAINTS` block, byte-identical
— is independent of it, spliced and re-verified either way. So the exposure is losing a
*non-constraint* section in an offline compaction, recoverable from git. Not worth a
sentence classifier; the honest fix is for the extension to show the removal list at
Decision 4 before the write lands (Task 13.9's cumulative review), so a wrong strip is
seen rather than merely reversible.

### - [x] G-020 — `gac` has no defined index base
**Severity:** LOW · **Noticed:** Task 10.1 · **Closed:** 2026-08-02 ·

**Closed by the amendment it asked for.** REQ-CON-006 **AC4** now fixes `gac` as 1-based
and bounds it to `[1, n_passes]`, with a never-settling run reporting its final pass —
the reading the code already documented, now ratified rather than merely implemented.
`StabilityInfo.gac` tightened from `ge=0` to `ge=1` plus an upper-bound check, so the
contract rejects both readings it used to admit. Verified by
`test_gac_is_one_based_and_bounded_by_n_passes`.

*Original entry:* ·
**Where:** `saltcode_backend/saltcode/stability/measure.py` (`compute_gac`),
`saltcode/contracts/audit_result.py` (`StabilityInfo.gac`)

The specs define `gac` only as "pass where verdict first stabilizes" (proposal v8 line
397; design §9 repeats the phrase). Neither says whether passes are counted from 0 or 1,
nor what the value is for a run that never settles. `StabilityInfo` constrains it to
`>= 0` only, which admits both readings — and unlike `stability_score`, whose formula the
contract re-derives and validates, nothing cross-checks `gac` at all.

Implemented as the smallest reading consistent with the surrounding design: **1-based**,
matching how `n_passes` counts, with a never-settling run stabilising on its final pass
(a length-1 suffix is trivially constant). `[pass, impl_fail, pass]` therefore yields 3.
Pinned by `test_gac_is_the_pass_the_verdict_last_settled_on` so a change is deliberate.

*Why it matters:* little today, because nothing routes on `gac` — the escalation reads
`stability_score`. It matters when Task 14b calibrates against a labelled set and when a
human reads `audit_result.json`: an off-by-one in a field nobody validates is the kind of
thing that survives for a year. The fix is one sentence in `specs/requirements.md`
REQ-CON-006, not a code change.

### - [x] G-021 — A heuristic flag overrides a `spec_defect` judgment
**Severity:** MED · **Noticed:** Task 10.2 · **Closed:** 2026-08-02 ·

**Closed, and it turned out not to need a change of intent at all.** Grilling the
documents showed the proposal had *already* adjudicated: v8 line 499 — "`spec_defect` ⇒
Test Intent re-run with detail feedback (**not a retry**)" — and line 656 say the same, and
REQ-AUD-003 AC1 states it outright. So REQ-AUD-001 AC1's unqualified "unless judgment
clears it" was not a competing policy but an **internal contradiction** between two
requirements, which the proposal settles. AC1 now carries the carve-out explicitly, and
`resolve_reason` lets `spec_defect` win over a fired flag while still reporting what fired.
The carve-out is narrow: `impl_fail` still loses to the flag, since both route to a Builder
retry and the gaming reason carries more detail. Verified by
`test_a_spec_defect_judgment_wins_over_a_heuristic_flag` and
`test_a_heuristic_flag_still_beats_impl_fail`.

*Original entry:* ·
**Where:** `saltcode_backend/saltcode/stability/audit.py` (`resolve_reason`)

REQ-AUD-001 AC1 reads: "WHEN a heuristic flag fires, THEN the verdict SHALL be
`gaming_suspected` **unless judgment clears it**." Read literally — and it is implemented
literally — "clears it" means the judgment answered `pass`; any other verdict leaves
`gaming_suspected` standing.

For `impl_fail` that costs nothing: both route to a Builder retry, and the gaming reason
carries strictly more detail. For **`spec_defect` it costs a retry.** REQ-AUD-003 AC1 and
REQ-FAIL-001 AC3 make `spec_defect` route to a Test-Intent re-spec and explicitly *not*
consume a Builder retry; overriding it with `gaming_suspected` spends one of the three
against a task whose spec the Auditor believes is wrong, and sends the Builder to fix
code that may be correct.

*Recommendation:* `spec_defect` should win over a heuristic flag — a flag says the code
looks like it games the tests, and `spec_defect` says those tests should not be trusted,
which is the more fundamental claim. That is a change to REQ-AUD-001 AC1's wording, so it
belongs in the proposal → requirements, not in the code. Until then the code follows the
requirement as written and says so in the emitted `detail`.

### - [x] G-022 — The PCD density bars have no specified estimator
**Severity:** LOW · **Noticed:** Task 14b.3 · **Closed:** 2026-08-02 ·

**Closed by the amendment it asked for.** REQ-CACHE-003 **AC6** now names the estimator —
quartiles of the PCD distribution observed over the calibration set, low bar at the 0.25
quantile and high at the 0.75, each query held out of its own corpus — which is what the
code already did. The amendment also records the known limitation rather than burying it:
quartiles are dominated by a handful of values on a small set, and AC5's inertness only
protects the bars *before* they are measured, not after.

*Original entry:* ·
**Where:** `saltcode_backend/saltcode/stability/calibrate.py`
(`PCD_LOW_QUANTILE`, `PCD_HIGH_QUANTILE`)

Design §11.9 lists the PCD bars among the things `saltcode_calibrate` measures and says
nothing about how. REQ-CACHE-003 AC2/AC3 describe only the *consequences* of a PCD
landing above the high bar or below the low one. So unlike the Auditor bar (max-F1,
REQ-AUD-005) and the cosine bar (just above the highest non-match), the density bars have
no rule to implement.

Implemented as the **quartiles** of the PCD distribution observed over the calibration
set — "dense" and "sparse" relative to what this project's own cache actually looks like,
which is the smallest reading consistent with what PCD is for. Each query is held out of
its own corpus, matching how runtime PCD sees a goal that is not yet cached.

*Why it matters:* less than the other two bars, because REQ-CACHE-003 AC5 keeps the PCD
bars **inert while uncalibrated** — full Architect confirmation regardless of where they
sit — so a bad estimator cannot cause a false reuse before it has been measured. After
calibration it can: a high bar set too low earns a *cheap* confirmation for a
neighbourhood that is not really dense. Quartiles are also a poor fit for a small
calibration set, where they are dominated by a handful of values. The fix is a sentence
in REQ-CACHE-003 or design §11.9 naming the estimator, not a code change.

### - [x] G-023 — DD-16's "preload the skill into the sub-agent prompt" is not a feature that exists
**Severity:** HIGH · **Noticed:** Task 7.0 · **Closed by:** Task 7.2 · **Closed:** 2026-08-02

**Resolution (maintainer, 2026-08-02): inline the skill into the agent body.** The
definition body becomes the child system prompt verbatim under `systemPromptMode: replace`,
so the skill is present with no tool call and no `read` — DD-16's intent by construction
rather than by an extension feature that does not exist. `skills/saltcode-*/SKILL.md`
remains the single hand-authored source; `scripts/sync_agent_skills.py` splices it between
markers and `--check` fails on drift, guarded by a test that hand-edits a block and asserts
the guard catches it. Verified across all six definitions.

*Original entry:* · **Where:** `specs/design.md` DD-16 and §5.9, `specs/tasks.md`
task 7.2, `docs/subagent_contract.md` §4

Design DD-16 says each agent's skill is "**preloaded** into its sub-agent system prompt
(a feature of the sub-agent extension)", and task 7.2 requires the skill "preloaded
directly into the prompt (do NOT rely on Pi's read-tool auto-discovery — locked-down
agents lack `read`)". **`pi-subagents@0.40.0` has no such feature.** Its `skills:`
frontmatter resolves to `buildSkillInjection` (`src/agents/skills.ts:671-690`), which
appends a *manifest* — name, description, and a `<location>` file path — under the
instruction "Use the read tool to load a skill's file". That is the read-tool dependency
DD-16 was written to avoid, reproduced by the mechanism DD-16 named as the cure.

The obvious escape is closed: granting `read` to the locked-down agents is forbidden by
`.claude/rules/privacy-boundary.md` and REQ-MCP-001, and Scout specifically must hold no
file-body capability at all.

*Why it matters:* it is invisible in exactly the way that costs a debugging session. A
definition carrying `skills: saltcode-scout` loads without error, spawns without error,
and produces an agent that never sees its skill — because the one tool it would need to
fetch it is the one tool it must never have. Every behavioural REQ under Task 7's
**Satisfies** (REQ-SCT/ARC/PLN/TST/EVL/BLD) rides on the skill actually being in the
prompt, so all six agents silently lose their contract.

*Available resolution:* the definition's Markdown **body** becomes the child system
prompt verbatim (`agents.ts:1541`, with `systemPromptMode: replace`) — no tool call, no
`read`. So preloading is achievable by putting the skill's content in the agent body
instead of naming it in `skills:`. The cost is one source of truth becoming two
(`skills/saltcode-*/SKILL.md` for Pi's catalogue and interactive use, the agent body for
the spawn), which needs a generator or a drift test. **Raised to the maintainer
2026-08-02; authoring of `agents/*.md` is stopped until it is answered**
(`.claude/rules/stop-and-ask.md`).

### - [x] G-024 — A repo-root `agents/` directory is read by nothing until it is declared
**Severity:** MED · **Noticed:** Task 7.0 · **Closed:** 2026-08-02 · **Where:**
`package.json`, `specs/design.md` §17, `docs/subagent_contract.md` §2

**Closed, both halves, and the second one is now proven rather than argued.** The skills
half was corrected on 2026-08-02 (see below — my claim that the `pi config` leg could not
pass was wrong). The agents half is now verified against the real loader: with
`pi-subagents@0.40.0` installed, `discoverAgents()` returns all six definitions with
**`source: "package"`**, which is only reachable through the `pi.subagents.agents` key
added in 7.2. Design §17's tree is unchanged and the definitions are discoverable.

Pinned by `test/subagent-contract.test.mjs`, which asserts `source === "package"` for each
— so a future edit that breaks the declaration fails CI instead of making the agents
silently invisible.

*Original entry:*

Design §17's tree puts the sub-agent definitions at `agents/` in the repo root.
`pi-subagents` discovers project agents from `<root>/.agents/` and `<root>/.pi/agents/`
only; a bare `agents/` is scanned by no default path. This is the same class of finding
as **G-011** (`mcp.json` sitting where no MCP client looks), found the same way.

Unlike G-011 it has a clean fix that preserves the design: package-scope discovery reads
the package's own `package.json` and accepts `"pi": {"subagents": {"agents": ["./agents"]}}`,
resolved against the package root. Saltcode already ships as a Pi package with a `pi`
key, so this is one addition and no relocation.

*Why it matters:* the failure is silent — `loadAgentsFromDir` skips unreadable or
frontmatter-less files without diagnostics, so "no agents found" and "agents in the
wrong place" look identical. Also note package roots are collected from `node_modules`,
so the declared path is live for an *installed* Saltcode but not for a working copy
developed in place; Task 7.3's spawn verification needs an install, a link, or
`.pi/agents/`.

**Confirmed empirically for `skills/` too, 2026-08-02 (Task 7.1b).** `pi` 0.82.1 ships at
`node_modules/.bin/pi`, so this is testable here after all. `pi config -l --approve`
reports its project resource root as **`Project (/home/user/saltcode/.agents/)`** and
lists the 13 skills under `.agents/skills/`. The four skills authored into the repo-root
`skills/` by 7.1b are **not** listed, even though `package.json` declares
`"pi": {"skills": ["./skills"]}` — because that declaration is *package* scope, resolved
when Saltcode is installed under `node_modules`, and a working copy is not.

So the same split applies to all three resource kinds: `skills/`, `prompts/` and
`agents/` are correct **for the shipped package** and are invisible to a working copy that
has not been registered, which sees `.agents/` instead.

**Corrected 2026-08-02 (Task 7.4).** The sentence that stood here — that Task 7's
"Skills are visible in `pi config`" leg *"cannot pass from a bare working copy"* — was
**wrong, and the error was mine**. The step is not "written down nowhere": it is Task 0's
own Done-when and verification, `pi install -l .`, recorded on 2026-07-28. I had never run
it; I ran `pi config -l` against an unregistered tree and drew a conclusion from the wrong
command. Running `pi install -l . --approve` (the `--approve` is REQ-SEC-006 AC1's trust
check working as specified) and re-checking shows the project package with its extension
and **all eleven `saltcode-*` skills** listed. `.pi/` is gitignored, so registering leaves
the tree clean.

What survives of this gap is only the narrow, true part: a repo-root `agents/` is read by
no *default* discovery path, so the `pi.subagents.agents` declaration added in 7.2 is
load-bearing. That half is unverified — `pi-subagents` is not installed (REQ-SEC-006 trust
review outstanding), so nothing has yet read that key. The skills half is now verified and
closed.

### - [x] G-025 — Two of design §17's six cookbooks cannot be shipped as they stand
**Severity:** MED · **Noticed:** Task 7.1 · **Closed by:** Task 7.1c ·
**Closed:** 2026-08-02 · **Where:** `COOKBOOKS.md`, `skills/git-pushing/`,
`skills/python-pro/`, `specs/design.md` §17

**Resolution (maintainer, 2026-08-02).** Both ship, neither as-is-and-unexamined.
`git-pushing` is adopted **with `scripts/` removed** — the commit-message guidance is worth
keeping and the script was the entire hazard. Its body was rewritten so it no longer
invokes the deleted file (a dangling `bash …/smart_commit.sh` fails at the point of use
with no explanation, which is worse than the script) and now states *why* the steps are
explicit: `auto_push` and the uncommitted-until-regression boundary are project rules a
one-shot script cannot express. `python-pro` ships with its licence recorded in
`COOKBOOKS.md` as **undetermined** rather than assumed — it is prose-only, benign, and not
redistributed beyond this repo, and the honest record is what lets a later licensing
decision see exactly what is and is not known.

**Verified:** `pi install -l . --approve` then `pi config -l` renders **all 17** skills
under the project package — 8 `saltcode-*` agent skills, the 3 new ones, and all 6
cookbooks, which is design §17's set exactly. (The TUI viewport shows ~13 rows at a time,
so this needed scrolling to several depths and taking the union; a single frame is not
evidence of absence.)

---

*Original entry:*

The 7.1 trust review found no malicious content in any of the eight third-party skills.
It did find that two of the six cookbooks design §17 names cannot be adopted as-is, so
7.1c ("place all 14 skills under `skills/`") cannot be completed literally.

**`git-pushing`** ships `scripts/smart_commit.sh`, which runs `git add .` → `git commit`
→ `git push -u origin $BRANCH` unconditionally. That contradicts
`.claude/rules/project-architecture.md` twice over: *"no automatic `git push` unless
`auto_push = true`, in any mode"*, and the checkpoint rule that the tree stays
**uncommitted** until the regression gate passes. A `git add .` mid-task would commit the
un-cleared apply along with `.saltcode/` state and Test Intent's uncommitted
`tests/task_*_spec.*`. Its own frontmatter already declares `risk: critical`.

**`python-pro`** has no determinable licence — it is prose-only and entirely benign, but
task 7.1 says to *"prefer a bespoke one over a stale or unlicensed import"*, so shipping
it would contradict the instruction governing its own adoption.

*Why it matters:* design §17's tree names both, so declining them is a deviation from the
spec and must be a decision rather than an omission. Three routes for `git-pushing`: drop
it (the capability is already covered by this repo's own git rules), replace it with a
bespoke skill that honours `auto_push` and the checkpoint boundary, or adopt it with the
script removed and the prose kept. For `python-pro`: confirm the upstream licence, or
replace it. **Neither is shipped under `skills/` until this is answered**; both remain in
`.agents/skills/` as working copies.

*Also recorded:* five of the eight carried `risk: unknown`, meaning no trust review had
ever been completed on any of them. That is now done and written down in `COOKBOOKS.md`.
Two further skills (`agent-evaluation`, `ai-agents-architect`) exist locally but are not
in design §17 and are not adopted.

### - [ ] G-026 — The thirteen entrypoints disagree on the exit code for an unreadable file
**Severity:** LOW · **Noticed:** Task 7b (PR #2 review round 5) · **Closed by:**
unassigned · **Where:** `saltcode/tools/{compact_spec,read_scoped}.py` vs
`{apply_live,calibrate,compute_stability}.py`, `docs/entrypoints.md`

A path that is *absent or not a file* is `2` everywhere — that part is uniform. But an
`OSError` raised while reading a file that **does** exist splits the roster:
`compact_spec` and `read_scoped` report `3`; `apply_live`, `calibrate` and
`compute_stability` report `2`.

Both readings are defensible, which is why it drifted. `read_scoped` is arguably right at
`3` — the path came from the broker, not from a caller's typo, so a failure there really
is environmental. `apply_live` is arguably right at `2` — its `--in` is a caller-supplied
argument like any other.

*Why it matters:* less than it looks, because both paths emit the standard envelope with
`ok: false` and a `detail`, so nothing is lost — only the error *class* differs. It is
recorded because **the Task 7b conformance suite should have caught this and did not**:
its probe is an unknown flag, which exercises parse → fail → emit → exit uniformly, and
nothing in it opens an existing-but-unreadable file. `docs/entrypoints.md` now states the
divergence rather than implying a uniformity that does not hold. The fix is to pick one
rule, apply it to all five, and add the missing conformance probe — but the entrypoints
belong to Tasks 9/10/12/14b, so Task 7b reports it rather than editing them
(`.claude/rules/stop-and-ask.md`).

*Update (Task 17, 2026-08-18):* the roster is now seventeen. `checkpoint --in` is the only
new entrypoint that reads a caller-supplied file, and it reports `2` — following
`apply_live`, whose `--in` it mirrors exactly. So the split is 2 modules at `3` against 4
at `2`; the majority reading is "a caller-supplied path is a usage error", which is the
rule to standardise on when someone picks one.

### - [x] G-027 — The Builder skill sends the Builder to the wrong directory for its spec
**Severity:** MED · **Noticed:** Task 7.2 · **Closed:** 2026-08-02 ·

**Fixed 2026-08-02** in both copies (`skills/` and `.agents/skills/`) and re-synced into
the Builder's prompt. **The maintainer also set a standing rule:** when an older asset is
inlined into a prompt authored by the current task, factual errors in it that *every*
source document contradicts are fixed, not merely reported. `stop-and-ask.md`'s
"adjacent breakage is reported" governs judgment calls and things merely noticed — not
text the current change is knowingly shipping wrong.

*Original entry:* · **Where:** `skills/saltcode-builder/SKILL.md` line 13,
`.agents/skills/saltcode-builder/SKILL.md`

The skill instructs: *"Read the task's spec file: `.saltcode/tests/task_{id}_spec.*`"*.

Every other authority says `tests/task_{id}_spec.*`, at the repository root — design §9
and §7's roster row, REQ-CON-004, and the implementation itself:
`static_gate/test_runner.py` globs `tests_dir / f"task_{task_id}_spec.*"` and reports
*"no `tests/task_{task_id}_spec.*` exists in the live tree"* on a miss. Test Intent writes
there, and G-003's resolution copies from there into the sandbox.

So the `.saltcode/` prefix is simply wrong, and it is carried-over text from the
pre-v9 standalone build.

*Why it matters more now:* until Task 7.2 this string sat in a skill nothing loaded. It is
now **inlined into the Builder's system prompt** (DD-16 / G-023), so it is live
instruction to the agent. A Builder that looks in `.saltcode/tests/` finds nothing, and its
most likely recovery — proceeding without reading the spec — is the one that makes the
task-spec gate meaningless while still going green on everything upstream of it.

*Not fixed here:* the skill is a Task 7.1 asset and 7.2 does not own it
(`.claude/rules/stop-and-ask.md`: adjacent breakage is reported, not fixed). The fix is
deleting `.saltcode/` from that one line, in both copies, and re-running
`scripts/sync_agent_skills.py`.

### - [ ] G-028 — Task 7.3/7.3b's *behavioural* legs cannot run without providers
**Severity:** MED · **Noticed:** Task 7.3 · **Closed by:** whoever first runs with a
DeepSeek key and Saltnitor up, plus Task 13.3 · **Where:** `specs/tasks.md` 7.3, 7.3b

`pi-subagents@0.40.0` is adopted and the definitions resolve correctly, so the
*mechanism* half of both steps is verified — see `test/subagent-contract.test.mjs`. Two
legs are not, and neither can be from this box:

1. **"producing the expected behavior on the fixture repo"** (7.3) needs a live model.
   There is no DeepSeek key and no Saltnitor here, so no agent has actually run.
2. **Three of 7.3b's five adversarial attempts are behavioural** — Architect asked to
   emit tasks, Planner handed `context_report.json`, Test Intent asked to write
   implementation code. Each needs a model to make the attempt, and the *blocking* for the
   Planner and Test Intent cases lives in `pi.on("tool_call")`, which is **Task 13.3** and
   does not exist yet.

What **is** proven, and is the part 7.3b calls out as the real bar — *"a definition that
merely says 'do not' without a mechanism blocking it is a finding, not a pass"*: Scout's
allowlist contains no `read`, no `saltcode_read_scoped` and no shell, so "Scout asked for
a file body" fails by absent capability; and the Builder holds no `write` tool at all, so
it cannot touch `tests/**` — or anything else — outside its diff.

*Why it matters:* the tool-level boundaries are the ones that hold by construction and
they are verified. The behavioural ones are currently held by prompt text alone, which is
exactly the distinction 7.3b exists to draw. Both boxes stay unticked until the remaining
legs run.

**Half of blocker 2 is gone, 2026-08-11 (Task 13.3).** `pi.on("tool_call")` now exists and
is tested: `decideAccess` blocks a `tests/**` write, a non-allowlisted command, an
out-of-scope scoped read, and any scoped read by Scout — 15 assertions in
`test/access-control.test.mjs`, written as attempts rather than examples. So the *blocking*
half of 7.3b's Planner and Test-Intent cases is in place. What still cannot run is the
*attempt*: making the Planner ask for `context_report.json` needs a model, and there is
still no DeepSeek key and no Saltnitor here. **Blocker 1 is untouched and both boxes stay
unticked.** Whoever first runs with providers up can close this in one pass — the
mechanism is now entirely in place on both sides.

### - [ ] G-008 — `skills/` is an empty placeholder
**Severity:** MED · **Noticed:** Task 0.2 · **Closed by:** Task 7.1c ·
**Where:** `skills/`

Task 0's gate was narrowed because it could not assert skills are visible in
`pi config` — `skills/` is empty until Task 7.1c fills it. Same for `prompts/`
(a stub until Task 13b.1). Both assertions were relocated to the Done-when of
the tasks that produce them.

*Why it matters:* nothing breaks today, but the package is not installable-and-
complete until both land, so `pi install` currently registers an extension with
no skills behind it.

### - [x] G-029 — Nothing can run a command *inside* the container, so the built-in override refuses instead
**Severity:** HIGH · **Noticed:** Task 13.3 · **Closed:** 2026-08-11 ·
**Where:** `extensions/saltcode/{builtins,contained}.ts`,
`saltcode_backend/saltcode/tools/contained_exec.py`, `docs/entrypoints.md`

**Closed by option 1, which the maintainer took.** There is now a **fourteenth
entrypoint**, `saltcode.tools.contained_exec`: argv in, `{stdout, stderr, code}` out, the
REQ-SEC-002 allowlist re-checked backend-side, every call and every refusal audit-logged.
It is on the Task 7b roster, so the conformance suite holds it to all fourteen invariants
without anyone remembering to. Two modes — **exec** for `bash`, and **copy-in** for
`write`/`edit` whose `cp <staged> <target>` argv is built by the module rather than the
caller, so a one-entry allowlist authorises exactly that and REQ-SEC-002 is untouched.
The extension's overrides route through it; they refuse only where no containment backend
exists at all, which is REQ-SEC-005's rule and the same condition that stops Phase 2.

**REQ-SEC-007 AC1 is now met, and Task 13's Done-when leg is proven rather than argued:**
`test_a_bash_rm_rf_in_interactive_mode_leaves_the_host_untouched` asserts both halves —
the command is refused before a container is built, *and* the file it named still exists
afterwards. Asserting only the exit code would have passed even if the deletion had
happened. Sixteen tests in `tests/test_task_13_contained_exec.py` plus seven in
`test/contained.test.mjs`; backend 811 passed, 1 skipped.

**One defect found while testing, and it is worth recording.** The first design took the
argv as a repeatable `--arg`. `--arg -rf` parses as an *option*, not a value, so
`rm -rf /` came back as a **usage error** rather than a refusal — worse than it sounds,
because the command was not refused, it was not *understood*, and the caller saw the wrong
reason entirely. The channel is now a JSON array (`--argv-json`), which has no such
ambiguity, and the exact shape that broke is pinned on both sides.

**One thing this deliberately does not claim.** `--sandbox` is the container's writable
root, and in interactive mode the extension passes the *project* directory — a `write` the
human asked for has to land in the project. Every other guarantee is unchanged (no
network, no `$HOME`, no credentials, PID namespace, memory/CPU/time ceilings,
auto-cleanup), so what interactive mode gives up against Phase 2 is the read-only
*project*, not containment. Stated in the module docstring and in `docs/entrypoints.md`
rather than left for someone to discover.

*Original entry:*

REQ-SEC-007 AC1: when the human or any agent invokes `write`/`edit`/`bash`, execution
SHALL route through the security container, not the raw host. REQ-SEC-001 puts container
spawning in the backend, invoked via `pi.exec`. **The two do not meet.** The backend's
thirteen CLI entrypoints each run one *specific* contained job — `static_gate` runs the
linters on a sandbox, `test_run` runs one task spec, `sandbox_apply` runs `git apply` —
and none accepts an arbitrary argv. `run_in_container()` is exactly the primitive needed
and has no CLI surface, so the extension cannot reach it.

Task 13.3 says "route through the container (Task 3.1)", which reads as an assumption
that the channel already exists. It exists as a Python function; what is missing is its
exposure, and the roster that would have exposed it is Task 7b's — which enumerated
design §5.3's twelve tools plus `connectivity` and had no reason to notice a fourteenth.

**What is implemented.** The overrides are registered on Pi's own
`create{Bash,Write,Edit}ToolDefinition`, so schemas, result shapes and renderers stay the
built-ins'. The REQ-SEC-002 allowlist and the `tests/**` check run inside them, and both
run again at `tool_call`. With no `ContainedExec` injected, every mutating call **refuses
with a reason**. Nothing runs uncontained — that half of DD-13's hole is closed, and
refusing is REQ-SEC-005's own posture when containment is unavailable.

**What is not.** Refusing removes the capability rather than containing it, so AC1 is not
met and interactive `write`/`edit`/`bash` are unusable rather than safe. Task 13's
Done-when leg *"a built-in `bash rm -rf` in interactive mode is contained (routed to the
container, host untouched)"* is unsatisfied, and 13.3's box is unticked.

**The options, with a recommendation:**

1. **Add a `contained_exec` entrypoint** (argv in, `{stdout, stderr, code}` out, allowlist
   re-checked backend-side, audit-logged), extend `docs/entrypoints.md` and the Task 7b
   conformance suite, and inject it here. Makes AC1 true as written; the seam is already
   in place, so the extension side is a few lines. Cost: it is backend scope opened inside
   Task 13, and it hands the extension a general "run this contained" primitive whose
   safety rests entirely on the allowlist. **Recommended** — the allowlist is already the
   thing REQ-SEC-002 relies on everywhere else, so this adds no new trust assumption.
2. **Keep the refusal as the shipped behaviour** and amend REQ-SEC-007 AC1 to say the
   mutating built-ins are *disabled* rather than contained. Honest, zero new code, and it
   makes the spec match reality — but it removes interactive `write`/`edit`/`bash`
   entirely, which is a real usability loss the requirement was written to avoid.
3. **Route only the commands the existing entrypoints already own** (`pytest` → `test_run`,
   `ruff`/`pyright` → `static_gate`, `git apply` → `sandbox_apply`). **Rejected, recorded
   so it is not re-proposed:** those entrypoints take their own arguments, not an argv, so
   `bash "pytest -k foo"` cannot be expressed. It would silently run something other than
   what was asked.

*Why it matters:* this is the interactive-mode hole DD-13 exists to close, and it is
currently closed by subtraction. Anyone reading "Saltcode contains the built-ins" would
reasonably expect option 1's behaviour.

### - [ ] G-031 — The Auditor's local tier is never `ensure`d
**Severity:** MED · **Noticed:** Task 11.3 · **Closed by:** whoever owns
`compute_stability`'s model selection (Task 10.1's module; naturally Task 14 or 20.3) ·
**Where:** `saltcode_backend/saltcode/tools/compute_stability.py`,
`extensions/saltcode.ts`

REQ-MOD-004 wants exactly one local model resident, and REQ-MOD-005 says the tier is made
resident through `POST /v1/ensure` **before inference**. The extension now does that for
the Builder. It cannot do it for the Auditor: the N-pass judgment runs inside the backend's
`compute_stability`, which picks its own router section from `--model`, so the extension
never sees that turn and does not know which section to ensure.

Ensuring a profile the extension is *not* about to use would be worse than not ensuring —
it would evict whatever is resident, for a turn that then asks for something else.

*Why it matters:* the failure is not a wrong answer, it is a stall. Saltnitor loads the
section on demand, so today the Auditor's first pass pays a cold load; if the Builder left
Tier B resident and the Auditor asks for Tier A, the box thrashes between them for three
passes. On a 12GB card that is the difference between an audit that takes seconds and one
that takes minutes, and nothing in the pipeline reports it as anything but slowness.

*The fix, and why it belongs to the backend:* `compute_stability` should ensure its own
section before the first pass and report which one it used, exactly as it already reports
`escalation`. That is where the model choice lives. Raised rather than worked around,
because the alternative — having the extension guess the Auditor's section — encodes the
same choice in two places, which is the failure DD-16 already cost this project once.

### - [ ] G-032 — design §10.1 and REQ-CKP-002 AC3 disagree about an unset `regression_cmd`
**Severity:** LOW · **Noticed:** Task 17.1 · **Closed by:** a `design.md` §10.1 amendment
(spec-owner work, not code) · **Where:** `specs/design.md` §10.1,
`specs/requirements.md` REQ-CKP-002 AC3, `saltcode/checkpoint/regression.py`

design §10.1 describes the regression gate as running "`regression_cmd`, **default = the
full `test_runner_cmd` with no task filter**" — and then, four paragraphs later, its
config block says `regression_cmd = ""  # empty → regression gate SKIPPED`. The two
readings give different code: one derives a command, the other runs nothing.

**Resolved by the hierarchy, not by invention.** REQ-CKP-002 AC3 is unambiguous — *"WHEN
no `regression_cmd` is configured, the gate SHALL be SKIPPED"* — and `requirements.md`
outranks `design.md` (`.claude/rules/source-of-truth.md`). Task 17 implements the SKIP and
says so in the module docstring and in `docs/entrypoints.md`.

The parenthetical is also not implementable as written: "with no task filter" means
stripping a filter out of an arbitrary command string, which for `pytest -q
tests/task_T1_spec.py` is obvious and for `npm test -- --grep T1` is guesswork. Deriving
it would be exactly the invented behaviour the rules forbid.

*Why it matters:* only as drift — the code is right and the requirement is right, but a
reader who starts from design §10.1 will expect a fallback that does not exist. The fix is
one sentence in `design.md`: strike the parenthetical, or restate it as advice about what
to *put* in `regression_cmd` rather than as a default the system applies.

### - [ ] G-033 — Regression-fail routing rests on a text-scraping heuristic
**Severity:** MED · **Noticed:** Task 17.1 · **Closed by:** Task 18.1 (which must honour
`attributable`), or a later task that makes runners report failures structurally ·
**Where:** `saltcode_backend/saltcode/checkpoint/regression.py::extract_failing_files`

REQ-CKP-003 splits a regression failure two ways on a question of fact: are the failing
tests inside `task.files_affected`? Inside → a Builder retry. Outside → FLAG HUMAN, because
that is the Trade-B cross-task signal. The backend answers that question by **regexing the
runner's stdout** — `FAILED tests/x.py::t`, `FAIL src/y.test.ts`, `z_test.go:12:`, a Rust
panic line. Five patterns, four ecosystems, and any runner with a different output shape
falls through all of them.

The failure mode is contained by design rather than by luck: an empty tuple is documented
as **unknown**, `RegressionResult.attributable` says which case you are in, and the
`detail` string spells out that an unattributable failure must be flagged rather than
retried. So the unsafe direction — auto-retrying a Builder against a breakage it cannot
see — requires the *caller* to read an empty list as "nothing outside scope".

*Why it matters:* the guard only holds if Task 18.1 actually branches on `attributable`.
If it branches on `failing_files ⊆ files_affected` alone, an empty list satisfies the
subset test vacuously and every unparseable failure silently becomes a Builder retry —
the exact Trade-B violation REQ-CKP-003 AC1 exists to prevent. Recorded here so the task
that writes that branch reads it first.

### - [ ] G-034 — The regression gate's container has the live tree as its writable root
**Severity:** MED · **Noticed:** Task 17.1 · **Closed by:** unassigned (accepted for now) ·
**Where:** `saltcode_backend/saltcode/checkpoint/regression.py::run_regression`

Every other gate runs on a disposable worktree, so a test that writes files damages a copy.
The regression gate cannot: the question it answers is whether the *integrated* tree is
healthy, and a sandbox copy is not the integrated tree. So `sandbox=workspace_path` — the
project directory is the container's writable root, and a full suite with a destructive
fixture can modify the live tree.

Every other containment guarantee still holds: no network, no `$HOME`, no credentials,
isolated PID namespace, memory/CPU/time ceilings, allowlisted command. What is given up is
the read-only *project*, which is the same tradeoff `contained_exec` makes for interactive
mode and is stated in both module docstrings rather than assumed.

*Why it matters:* it is bounded — the tree is uncommitted at this point and the loop's
response to a regression failure is `git reset --hard` to the last checkpoint anyway, so
damage inside tracked files is already undone by the specified routing. Untracked files a
runaway suite creates are not. Worth revisiting if a copy-on-write overlay ever becomes
cheap enough to make the integrated tree and a disposable root the same thing.

### - [ ] G-035 — A checkpoint commit never records `.saltcode/`
**Severity:** LOW · **Noticed:** Task 17.2 · **Closed by:** unassigned (a deliberate
default, recorded so it is visible) · **Where:**
`saltcode_backend/saltcode/checkpoint/checkpoint.py::STAGE_PATHSPEC`

`write_checkpoint` stages `.` with `:(exclude).saltcode`, so the sprint artifacts
(`context_report.json`, `design.md`, `tasks.json`), the audit log and the checkpoint ledger
never enter the project's git history.

Taken deliberately, and the second reason is load-bearing: a ledger under version control is
rewound by the very `git reset --hard` that reads it. The first end-to-end run of Task 17
proved that — `git add -A` swept the ledger into the T2 commit, the rollback reset past it,
and the whole checkpoint history vanished. `rollback` now also snapshots the ledger before
resetting, so the fix holds even where some other tool tracks `.saltcode/`.

*Why it matters:* someone expecting `git log` to show which plan a commit was built from
will not find it. The plan lives in `.saltcode/` on disk and in the session store, not in
history. If a project wants the artifacts versioned it commits them by hand — which is the
right way round, since committing them on every checkpoint would put a churning directory
in every diff a human reviews.

### - [ ] G-036 — The cached-prefix cost model has still never been measured
**Severity:** MED · **Noticed:** Task 6.3 · **Closed by:** the first live sprint against a
real provider (Task 14 end-to-end, or 20.4) · **Where:**
`extensions/saltcode/prefix.ts::observePayloadPrefix`, `extensions/saltcode.ts`
(`before_provider_request`), `specs/design.md` §15

design §15 says its own numbers are provisional, in its own words: prefix caching is
provider-side and keyed on the **serialized request prefix**, which Pi assembles — it
injects tool schemas around our content, and `getSystemPrompt()` does not reflect the final
payload — so *"the 74% figure is a **model, not a measurement**: it must be validated
empirically via `pi.on("before_provider_request")`"*.

Task 6 built the instrument that validation needs — `observePayloadPrefix` hashes the
cacheable head of the real payload, the `--prefix-debug` flag turns it on, and
`.saltcode/prefix_debug.jsonl` records every reading. It is unit-tested against both
provider payload shapes and the unrecognised one. **It has never produced a reading**,
because there is no provider on this box.

*Why it matters:* a byte-stable `systemPrompt` from this extension is *necessary* for a
cache hit and nowhere near sufficient. If Pi puts anything variable ahead of segments 1–3 —
a tool block that reorders, a timestamp, a session id — the provider's cache misses on
every turn and Task 6 buys nothing, while every gate and test in this repo still passes.
The instrument's `changed: true` on a turn `decidePrefix` reported as `reused` is exactly
that signal, and until someone runs it the §18 cost estimates rest on an assumption.

*What to do with it:* run one sprint with `--prefix-debug`, read the jsonl, and either
confirm §15's model or amend §15 and §18 with what was actually observed. This is cheap —
one flag on a run that was going to happen anyway — and it is the difference between a
documented economics claim and a guess.

### - [ ] G-037 — Task 6's byte-diff is proven at the function, not in a live session
**Severity:** LOW · **Noticed:** Task 6.2 · **Closed by:** Task 14 (end-to-end), same live
model that closes G-028 · **Where:** `extensions/saltcode/prefix.ts`,
`test/prefix.test.mjs`

REQ-GLB-004 AC1's byte-diff is asserted against `decidePrefix`, which is where the
guarantee is actually implemented — including the adversarial case where all three sources
are mutated between calls and the output must not move. What is *not* exercised is the
handler that feeds it: `before_agent_start` → `decidePrefix` → `{ systemPrompt }`, running
inside a real Pi session across two real turns.

*Why it matters:* less than G-036, and it is listed separately for that reason. The handler
is a typechecked pass-through with no branching beyond the reuse check, so the plausible
failure is not in its logic — it is that `before_agent_start` fires somewhere other than
where this assumes, or that Pi's chaining of multiple extensions' `systemPrompt` returns
(the API notes they are chained) puts another extension's bytes inside our frozen prefix.
Neither is visible without a running Pi. Same family as **G-028**.

### - [x] G-030 — Two tasks both claim Saltnitor provider registration
**Severity:** LOW · **Noticed:** Task 13.1 · **Closed:** 2026-08-11 ·
**Where:** `specs/tasks.md` 13.1 and 11.2

**Closed by the adjudication it asked for.** The maintainer took the recommendation:
**Task 11.2 owns Saltnitor provider registration**, the clause is struck from 13.1, and
11.2 now says so explicitly so the ownership is visible from the task that does the work
rather than only from a gap entry. 13.1's box is ticked; `session_start` will call into
Task 11's module when that task lands.

*Original entry:*

Task 13.1 lists "register Saltnitor models" among `session_start`'s duties. Task 11.2 is
`pi.registerProvider("saltnitor", {baseUrl, api, models})` with an async `/v1/models`
factory and graceful degradation — the same work, specified in more detail, in a task
that `deps: 2, 13`.

13.1's other duties are done (state replay, the connectivity probe, `session_shutdown`,
`resources_discover`). Provider registration is not, because doing it here would be Task
11's work inside Task 13, and `.claude/rules/stop-and-ask.md` forbids expanding scope.
13.1's box therefore stays unticked over one clause.

*Recommendation:* treat 11.2 as the owner and strike the clause from 13.1 — the detail
lives in 11, and `session_start` calling into a Task-11 module is the natural shape. That
is a one-line tasks.md edit, so it is asked rather than taken.

*Why it matters:* only bookkeeping — but an unticked box with no stated reason is exactly
what the ledger discipline exists to prevent, and "someone will do it in 11" is not
visible from 13.

---

## Closed

### - [x] G-006 — The entrypoint exit-code scheme is a convention, not a contract
**Severity:** LOW · **Noticed:** Task 1.7 · **Closed by:** Task 7b.2 ·
**Closed:** 2026-07-31 ·
**Where:** `saltcode_backend/saltcode/tools/_cli.py`, `docs/entrypoints.md`

The 0/1/2/3 scheme was documented in one module docstring and re-implemented by each
entrypoint. Nothing checked that the thirteen agreed, and no spec fixed the numbers.

**Closed by** `docs/entrypoints.md` (the contract, including what is deliberately *not*
promised) plus `tests/test_task_7b_entrypoints.py`, which parametrises every invariant
over the roster and cross-checks that roster against `saltcode/tools/*.py` in both
directions — so a fourteenth entrypoint is held to the contract automatically.

**It found a real defect on its first run**, which is the best evidence the gap was worth
recording. 35 of 112 failed against shipped code: every entrypoint returned exit **2** on
an unknown flag with **empty stdout**, because `argparse` writes its usage message to
stderr and `exit_code_for` translated the code without emitting a payload. The extension's
`JSON.parse("")` throws on that, so a mistyped flag surfaced as an unhandled error rather
than a tool result. Each task's own tests had asserted the exit code and not the payload —
precisely the half-checked contract this gap described. Fixed once in `_cli.parse_cli`.

### - [x] G-005 — The Auditor cannot vary temperature across stability passes
**Severity:** MED · **Noticed:** Task 2.1 · **Closed by:** Task 10.1 ·
**Closed:** 2026-07-31 ·
**Where:** `saltcode_backend/saltcode/providers/local.py` (`LocalClient.chat`)

REQ-AUD-002 AC5 requires each of the N=3 stability passes to use a distinct
condition. `chat()` fixed temperature at `0.7 if thinking else 0.0` and exposed no
parameter, so the passes were byte-identical requests whose verdicts could only
agree: `stability_score` was 1.0 by construction and the confidence measure was
decorative — exactly the self-reported-confidence failure BIFAI-NET's measured
stability replaced.

**Closed by** giving `chat` an optional `temperature`, and by varying a *second*
condition that no server can silently neutralise. Temperature alone would not have
closed it: a server is free to ignore, clamp or pin the sampling parameter — llama.cpp
started with a fixed `--temp`, a router section that overrides it — and nothing in the
response says so, which yields a perfect score and is worse than no score. Evidence
reordering changes the request bytes, so it survives that.

**Verified** by `tests/test_task_10_stability.py::test_the_passes_differ_in_temperature_and_in_prompt_bytes`,
which reads the captured request payloads and asserts three distinct temperatures *and*
three distinct prompts, and by `test_every_pass_runs_under_a_distinct_condition`. An
out-of-range temperature is refused rather than forwarded
(`test_an_out_of_range_temperature_is_refused_not_forwarded`), since silent clamping is
the failure mode that would put the gap back without reporting it.

### - [x] G-003 — Uncommitted task specs will not exist in the sandbox
**Closed 2026-07-29** · Noticed Task 3.1 · Closed by Task 9.3

The sandbox is a git worktree checked out at `HEAD`, but Test Intent writes
`tests/task_{id}_spec.*` to the live tree and leaves it uncommitted (REQ-CON-004),
so the spec was simply absent from the worktree and the runner would collect
nothing — `pytest` exits 0, the gate reads PASS, and an unimplemented task ships.

**Resolution (maintainer decision, 2026-07-29):** `test_runner.copy_spec_into_sandbox`
copies just `tests/task_{id}_spec.*` into the sandbox before the run. Narrow, leaves
git history untouched so the checkpoint commit stays the only task boundary, and
matches the existing precedent of `apply_diff` writing its patch file in. Rejected:
committing the specs (Test Intent emits all of them up front, so that commits every
task's spec before any task passes) and bind-mounting the live `tests/` (exposes every
task's spec to every run). Since REQ-BLD-003 bars the Builder from `tests/**`, copying
is the only route that needs no commit.

**The second half — "no tests collected" must be a failure — is closed too**, and was
the sharper half. Exit codes alone cannot detect it: `pytest` signals it with **5**,
but `cargo test` prints `running 0 tests` and `go test` prints `[no test files]` and
*both exit 0*, so an exit-code check would have passed an empty run in two of the four
frameworks. `looks_like_no_tests` matches the marker phrases as well as pytest's exit 5,
and `TestRunReport.blocks_auditor` puts `no_tests` on the short-circuit path beside
`fail`. Verified by `test_the_uncommitted_spec_reaches_the_sandbox`,
`test_empty_runs_are_detected_across_frameworks` (6 framework phrasings),
`test_a_missing_spec_is_a_failure_not_a_skip`, and end to end on CI by
`test_an_empty_spec_file_is_a_failure_not_a_pass`.

### - [x] G-016 — CI cannot run the containment suite: GitHub runners restrict userns
**Closed 2026-07-29** · Noticed Task 5 (PR #2 CI run)

The backend CI job had failed on every push to `main` since at least 2026-07-27,
dying at `sandbox_apply --check-containment` with exit 3 before `ruff`, `pyright` or
`pytest` ran at all — so the CI signal for every task since Task 3 was empty.
`ubuntu-latest` is now Ubuntu 24.04, whose AppArmor profile blocks unprivileged user
namespaces, so an installed `bwrap` could not create one and detection correctly
refused (G-C07 working as designed).

**Resolution (maintainer-approved, 2026-07-29):** `.github/workflows/ci.yml` sets
`kernel.apparmor_restrict_unprivileged_userns=0` before the containment probe. This
does not weaken the test — bwrap still creates a real namespace with the same bind
layout, and REQ-SEC-005's refuse-if-no-backend path is untouched, so removing the
step returns the job to failing closed, which is what Task 3 chose over skipping.

**Verified: run 30434177585 — `325 passed in 67.19s`, 0 failed, 0 skipped, both
lanes green.** A second, smaller defect surfaced only once the lane could run:
`test_rm_rf_home_does_not_affect_the_host` asserted
`TOOLCHAIN.relative_to(home)` unconditionally, which raises `ValueError` on any host
whose `sys.prefix` sits outside `$HOME` — true of every GitHub runner
(`/opt/hostedtoolcache/...` vs `/home/runner`). The tolerated entry is now admitted
only when the toolchain really is under `$HOME`, making the assertion *stricter* on
CI; REQ-SEC-001 AC1's canary and readable-contents checks are unchanged.

### - [x] G-012 — `mcp` is unpinned, and `mcp` 2.0 breaks the LSP/AST server
**Closed 2026-07-29** · Noticed Task 5 (baseline run)

`pyproject.toml` declared `mcp>=0.1.0` with no upper bound, so a fresh install
resolved **mcp 2.0.0**, which removed `mcp.server.fastmcp` and broke collection of
`tests/test_task_4.py` and `tests/test_task_4_broker.py` (plus 10 `pyright --strict`
errors). It was invisible in CI because the job died earlier still (G-016).

**Resolution:** pinned to `mcp>=1,<2`; a clean resolution now selects 1.29.0. The
bound is load-bearing, not caution — lift it only alongside a port to the 2.0 API,
which belongs to whoever owns the MCP server. Verified by run 30434177585 collecting
and passing both Task 4 modules.

*Note:* this entry originally claimed CI was red **because of** this. It was not —
the containment step (G-016) failed first. Both had to be fixed for a green lane.

### - [x] G-004 — Exact spec-cache hits will require `--scope`
**Closed 2026-07-29** (as a deliberate decision) · Noticed Task 3.2

Store time hashes `sorted(tasks.json[*].files_affected)`; lookup time hashes the
repo's module list. Those are different sets, so the two keys rarely match and the
exact tier mostly misses unless the caller passes `--scope` (REQ-CACHE-002 AC2).

**Resolution (maintainer, 2026-07-29):** accepted as specified. REQ-CACHE-002 is
unambiguous and Trade C5 explicitly buys this lower hit rate to avoid reusing the
wrong plan — goal text alone collides on "rate-limit the LOGIN endpoint" vs
"rate-limit the SIGNUP endpoint". The behaviour is unchanged; what changed is that
it is no longer *invisible*: `lookup_spec_result` and the `cache_lookup` entrypoint
now report `scope_fingerprint`, `scope_source ∈ {provided, probed, empty}` and the
computed `key`, so a miss can be explained and `/sprint` (Task 8.1) can reuse a
`--scope` between sprints. An unexplained miss is indistinguishable from a cache
that is not wired up, which is what made this worth closing rather than leaving.
Verified by `test_trade_c5_the_two_fingerprints_differ_by_design` (which asserts
both the miss and the `--scope` recovery), `test_a_miss_explains_itself`, and
`test_a_miss_reports_the_key_and_fingerprint_it_used` via subprocess.

### - [x] G-C01 — Privacy guard: was a LAN host inside or outside the boundary?
**Closed 2026-07-28** · Noticed Task 2.4

`classify_destination` treats only loopback as on-box, so a Saltnitor at a LAN
address counts as network and refuses source-tagged payloads. The specs say raw
bodies "never leave the box" but never adjudicated another machine on the LAN.

**Resolution (maintainer):** every component — Saltcode, Saltnitor, Docker, the
embedding endpoint — runs on **this one machine**. The box *is* the boundary, so
the fail-closed loopback-only rule is exactly right. No LAN exemption is wanted;
do not add one. Recorded in the Task 2 ledger note.

### - [x] G-C02 — The scope probe's mechanism was not implementable as specified
**Closed 2026-07-28** · Noticed Task 3.2

Task 3.2, REQ-CACHE-002 and design §11.2 all described "a single `outline` MCP
call producing a sorted module/file list", but `outline(file_path)` takes one
file and returns that file's symbols. Two readings gave different code *and*
different cache keys.

**Resolution:** requirement fixed before code, per "ambiguity is a defect".
Maintainer chose module enumeration; the proposal's intent (a tool call, not a
Phase-1 fire) was unchanged and is preserved verbatim. Corrected in
`docs/proposal/Proposal_Pi_v8.md` with an inline note explaining what was wrong
and why, then flowed down to requirements → design → tasks. Verified by
`tests/test_task_3_scope_probe.py` (20 tests).

### - [x] G-C03 — The goal string polluted the cache key
**Closed 2026-07-28** · Noticed Task 3.2

The pre-existing `run_scope_probe(goal, workspace)` token-matched filenames out
of the goal, so *"fix auth.py"* and *"fix authentication"* produced different
fingerprints — and therefore different Spec-Cache keys — for an identical tree.

**Resolution:** the goal is no longer an input; it is hashed into the key
separately. Both production callers (`spec_cache`, `semantic_cache`) updated.
Verified by `test_the_goal_is_not_an_input`.

### - [x] G-C04 — Uncontained execution paths in the backend
**Closed 2026-07-28** · Noticed Task 3.1

`run_command_in_sandbox` ran `subprocess.run(..., shell=True)` **directly on the
host**, and `apply_diff` fell back to `patch -p1`, which is not on the
REQ-SEC-002 allowlist.

**Resolution:** both removed rather than wrapped — REQ-SEC-001 admits no
uncontained path and REQ-SEC-005 forbids an uncontained fallback. `run_in_container`
is now the only execution API. Side benefit: the sandbox and `diff_check` now
agree on what "applies" means, where `patch` had been lenient about a missing
trailing newline that `git apply` rejects.

### - [x] G-C05 — Whole-root bind left the network reachable
**Closed 2026-07-28** · Noticed Task 3.1

`bwrap --unshare-all --ro-bind / /` still resolved `example.com`: the unix
sockets under `/run` come along with a whole-root bind, and unix sockets cross
network namespaces. A Docker socket there would have been a host escape.

**Resolution:** an explicit read-only bind allowlist (`/usr`, `/etc`, `/opt`,
plus whichever of `/bin`,`/lib*` are real directories vs. symlinks); `/run`,
`/home`, `/root`, `/var` are never bound. Verified by
`test_no_network_inside_the_container` (a real `socket.create_connection` is
refused with `Network is unreachable`).

### - [x] G-C06 — The cgroup memory limit did not bind
**Closed 2026-07-28** · Noticed Task 3.1

`systemd-run -p MemoryMax=64M` allowed a 300 MB allocation: cgroup v2 pushes the
overage to swap instead of killing.

**Resolution:** `MemorySwapMax=0` is set alongside `MemoryMax`, making the cap a
real OOM kill. Verified by `test_memory_limit_kills_a_runaway_allocation`.

### - [x] G-C07 — Backend detection trusted `which` over capability
**Closed 2026-07-28** · Noticed Task 3.1

Ubuntu 24.04 ships `bwrap` but restricts unprivileged user namespaces, so the
binary can exist and fail at the first exec — detection would have reported
containment that does not exist.

**Resolution:** detection runs a cached functional probe using the *real* bind
layout. Verified by `test_an_installed_but_broken_bwrap_is_not_a_backend`.

### - [x] G-C08 — Diff extraction silently corrupted valid patches
**Closed 2026-07-28** · Noticed Task 1.6

`extract_diff_from_fences` called `.strip()`, deleting a trailing space-only
context line — the unified-diff representation of a blank final source line —
which left the last hunk one line short and made `git apply` reject valid
patches as `corrupt patch at line N`. Most files end in a blank line, so this
was the common case.

**Resolution:** extraction is byte-preserving from the first diff marker onward.
Would also have broken sandbox apply (3.1) and `apply_live` (10.3). Verified by
`test_diff_check_preserves_trailing_blank_context_line`.

### - [x] G-C09 — `validate_diff` never ran `git apply --check`
**Closed 2026-07-28** · Noticed Task 1.6

Task 1.6 and REQ-STAT-005 both name `git apply --check`; the implementation did
a structural parse only, so a well-formed diff that could not apply passed the
gate.

**Resolution:** added `diff_validator.git_apply_check`, reported as `skipped` —
never as a pass — when git is absent or the target is not a repository.

### - [x] G-C10 — Task 0's gate was unsatisfiable as written
**Closed 2026-07-28** · Noticed Task 0

The gate required skills and prompts visible in `pi config`, which Task 0 cannot
produce: 0.2 creates only a `prompts/` stub and `skills/` is filled by Task 7.1.

**Resolution:** gate narrowed with maintainer approval; the two assertions moved
to the Done-when of the tasks that produce them (7.1c and 13b.1). The remaining
shortfall is tracked as **G-008**.

### - [x] G-C11 — Task 7 opened on a false premise
**Closed 2026-07-28** · Noticed during the Task 7 review

Task 7 stated "the 8 custom skills + 6 cookbooks already exist". An audit found
4 of the 8 `saltcode-*` skills exist; `architect`, `planner`, `test-intent` and
`compactor` do not, nor do the 3 new v9 skills, nor `agents/`.

**Resolution:** premise corrected in `tasks.md`; sourcing and authoring the gap
is now part of Task 7 (steps 7.0, 7.1, 7.1b, 7.1c) rather than a precondition of
it. The missing skills themselves are Task 7's work, not a gap.
