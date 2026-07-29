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

### - [ ] G-003 — Uncommitted task specs will not exist in the sandbox
**Severity:** HIGH · **Noticed:** Task 3.1 · **Closed by:** Task 9.3 ·
**Where:** `saltcode_backend/saltcode/harness/sandbox.py` (`disposable_sandbox`)

The sandbox is a git worktree checked out at `HEAD`. Test Intent writes
`tests/task_{id}_spec.*` to the **live tree**, uncommitted (REQ-CON-004), so
those files are simply absent from the worktree. The test runner will find
nothing to run.

*Why it matters:* left unhandled this is a false green — `pytest` exits 0 having
collected no tests, the gate reads PASS, and an unimplemented task ships. Task 9.3
must decide how the spec reaches the sandbox (copy it in, commit it, or overlay
it) and must treat "no tests collected" as a failure, not a pass.

### - [ ] G-005 — The Auditor cannot vary temperature across stability passes
**Severity:** MED · **Noticed:** Task 2.1 · **Closed by:** Task 10.1 ·
**Where:** `saltcode_backend/saltcode/providers/local.py` (`LocalClient.chat`)

REQ-AUD-002 AC5 requires each of the N=3 stability passes to use a distinct
condition (temperature jitter, evidence reordering). `chat()` fixes temperature
at `0.7 if thinking else 0.0` and exposes no parameter for it.

*Why it matters:* without a distinct condition per pass the verdicts are
trivially identical, `stability_score` is always `1.0`, and the confidence
measure becomes decorative — the exact self-reported-confidence failure that
BIFAI-NET's measured stability replaced. Deliberately left to Task 10.1 rather
than folded into Task 2.

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

### - [ ] G-012 — `mcp` is unpinned, and `mcp` 2.0 breaks the LSP/AST server
**Severity:** HIGH · **Noticed:** Task 5 (baseline run) · **Closed by:** Task 20.1
(or a pyproject pin) · **Where:** `saltcode_backend/pyproject.toml`,
`saltcode_backend/saltcode/mcp/lsp_ast_server.py`

`pyproject.toml` declares `mcp>=0.1.0` with no upper bound. A fresh
`pip install -e ".[dev]"` today resolves **mcp 2.0.0**, which removed
`mcp.server.fastmcp` (renamed to `mcp.server.mcpserver`). The import at
`lsp_ast_server.py:9` then fails, which **breaks collection of
`tests/test_task_4.py` and `tests/test_task_4_broker.py`** and adds 10
`pyright --strict` errors. Pinning `mcp<2` in the local venv restores the API
Task 4 was verified against and the suite collects again.

*Why it matters:* CI runs `pip install -e ".[dev]"` on a clean runner every time,
and the run for PR #2 confirms it installed `mcp-2.0.0`. The floor silently
re-resolves on every fresh install, so the next person to set up a dev environment
will think they broke Task 4. Not fixed here because the pin is outside Task 5's
scope and the alternative — porting `lsp_ast_server.py` to the 2.0 API — belongs to
whoever owns the MCP server. **Recommendation: pin `mcp>=1,<2`.**

*Correction (2026-07-29):* this entry first said CI was red **because of** this. It
is not — the job dies earlier, at the containment step (**G-016**), and never
reaches `pyright` or `pytest`. This is the *second* blocker to a green CI run, not
the first. Both must be fixed for the backend lane to pass.

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

*Status here:* these 9 failures are **pre-existing and unrelated to Task 5** —
recorded before any Task 5 change and byte-identical after. Task 3's legs were
genuinely verified on the maintainer's box on 2026-07-28; they simply cannot be
re-verified in this container.

### - [ ] G-016 — CI cannot run the containment suite: GitHub runners restrict userns
**Severity:** HIGH · **Noticed:** Task 5 (PR #2 CI run) · **Closed by:** Task 0.4 /
Task 3 CI wiring · **Where:** `.github/workflows/ci.yml`

**The backend CI lane has failed on every push to `main` since at least
2026-07-27** — 10 consecutive runs — and it is not any current task's doing. The
workflow's third step, `python -m saltcode.tools.sandbox_apply --check-containment`,
exits 3:

> `bwrap: installed but cannot create a namespace (unprivileged user namespaces may
> be restricted); docker: installed, but [sandbox] image is not configured
> (SALTCODE_SANDBOX_IMAGE); firejail: not installed`

The job dies there, **before `ruff`, `pyright` or `pytest` run at all**, so the CI
signal for every task since Task 3 has been meaningless. Verified identical on base
commit `5b6eb62e` (run 30334400417) and on PR #2 (run 30433261210).

Nothing here is a code defect — detection is doing exactly what G-C07 made it do,
correctly refusing to claim a containment it cannot deliver. The defect is in the
**CI wiring**: the workflow installs bubblewrap and assumes it will work, but
Ubuntu 24.04 runners apply an AppArmor restriction on unprivileged user namespaces.

Candidate fixes, for whoever owns this (each needs a deliberate choice, so none was
applied here):

* `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` before the
  containment step — the narrow, well-known unblock for Ubuntu 24.04 runners.
* Configure `SALTCODE_SANDBOX_IMAGE` and let the Docker backend win, which would
  also close **G-002** (the Docker path has still never launched a container).
* Split the workflow so the non-containment lanes report independently — but note
  Task 3 deliberately made CI *fail* rather than skip, so the security tests could
  not pass by not running. That intent must be preserved.

*Why it matters:* a lane that has been red for days is a lane nobody reads. Every
"CI green" claim in this ledger since Task 3 rests on local runs, not on CI —
and G-012 is sitting behind this one, invisible, because the job never gets far
enough to hit it.

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
