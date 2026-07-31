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

### - [ ] G-006 — The entrypoint exit-code scheme is a convention, not a contract
**Severity:** LOW · **Noticed:** Task 1.7 · **Closed by:** Task 7b.2 ·
**Where:** `saltcode_backend/saltcode/tools/_cli.py`

`0` positive · `1` negative verdict · `2` usage · `3` internal is not fixed by
any requirement. It is documented in `_cli.py` and followed by all four
entrypoints so far.

*Why it matters:* the extension's tool wrappers will branch on these codes
(REQ-EXT-004), so 7b.2 must publish the scheme as a contract before Task 13.2
depends on it.

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

### - [ ] G-013 — `bwrap` reports containment on a host where the limits do not bind
**Severity:** MED · **Noticed:** Task 5 (baseline run) · **Closed by:** Task 9 or
Task 20.3 · **Where:** `saltcode_backend/saltcode/harness/sandbox.py`

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

### - [ ] G-020 — `gac` has no defined index base
**Severity:** LOW · **Noticed:** Task 10.1 · **Closed by:** a future requirements
amendment (not yet written — this gap is OPEN) ·
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

### - [ ] G-021 — A heuristic flag overrides a `spec_defect` judgment
**Severity:** MED · **Noticed:** Task 10.2 · **Closed by:** a future requirements
amendment (not yet written — this gap is OPEN) ·
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

### - [ ] G-022 — The PCD density bars have no specified estimator
**Severity:** LOW · **Noticed:** Task 14b.3 · **Closed by:** a future requirements
amendment (not yet written — this gap is OPEN) ·
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

---

## Closed

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
