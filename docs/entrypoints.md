# Backend CLI entrypoints — the contract (task 7b.2, REQ-EXT-004)

Every backend capability is a standalone module runnable as:

```shell
python -m saltcode.tools.<name> [args]
```

The extension calls these with `pi.exec` and registers each as a Pi tool (`saltcode_*`).
That makes this document a **contract, not a description**: the extension's tool
definitions are written against the argument names and JSON keys below, and a change here
is a change to the bridge.

Three invariants hold for all fourteen. They are asserted mechanically for every
entrypoint in `tests/test_task_7b_entrypoints.py`, which is what closes **G-006** —
before it, the exit-code scheme was a convention that happened to hold.

1. **stdout is exactly one JSON object.** Nothing else — no progress lines, no banner.
   A tool that wants to say something to a human writes it to stderr.
2. **stderr is diagnostics only** and is never part of the contract. A caller that
   parses it is relying on something free to change.
3. **The exit code classifies the outcome**, so the extension can short-circuit the
   Phase-2 gate sequence without parsing the payload.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Ran; the verdict is **positive** (valid / clean / pass / hit). |
| `1` | Ran; the verdict is **negative** (invalid / dirty / fail / miss / refused). **This is a result, not a crash** — stdout still holds valid JSON. |
| `2` | **Usage error**: missing or invalid arguments — including a path that does not exist or is not a file. |
| `3` | **Internal error**: I/O failure, an unreachable service, an unexpected exception. |

**Where the boundary between `2` and `3` actually falls.** A path the caller named that
is *absent or not a file* is always `2` — the argument itself was wrong. An `OSError`
raised while reading a file that *does* exist (a permission bit, a bad encoding, a device
error) is **not uniform across the thirteen**: `compact_spec` and `read_scoped` report it
as `3`, while `apply_live`, `calibrate` and `compute_stability` report it as `2`. Treat
"existing file that would not read" as **either** `2` or `3` and route on the payload, not
the code. Both still emit the standard envelope with `ok: false`, so nothing is
ambiguous except which of the two error classes it lands in. Making this uniform is
**G-026**; it is called out here rather than smoothed over because a contract that hides a
known inconsistency is worse than one that names it.

The 0/1 split is the one worth being careful about. A cache miss, a dirty gate, a failing
test and an out-of-scope read are all *expected* outcomes of a working system, and the
loop routes on them; reporting any of them as a crash would make a normal Phase-2 lap
look like a broken installation. Conversely an unreachable Saltnitor is **never** exit 1,
because "the Auditor is unsure" and "the Auditor never ran" are different facts.

`--help` exits **0**, not 2. `argparse` raises `SystemExit(0)` for help and `SystemExit(2)`
for a parse error; every entrypoint routes both through `_cli.exit_code_for`, so a caller
shelling out to discover a tool's interface does not read success as failure.

## Common envelope

Every payload carries at least:

```jsonc
{
  "tool": "<name>",        // the module's own name, so a log line identifies itself
  "ok": true               // whether the tool considers this a positive outcome
}
```

A failure envelope (`_cli.fail`) adds `error` (the exception class name) and `detail`
(a human-readable sentence). Most tools also carry a `verdict` string, which is the
routing signal in words where the exit code is the same in numbers.

---

## The fourteen

### `validate_contract` — task 1.7
Validate a typed contract against its pydantic model and the Output-Length Enforcer.

| Argument | Required | Meaning |
|---|---|---|
| `--in PATH \| -` | yes | The JSON to validate, or stdin. |
| `--kind KIND` | yes | `context_report`, `design`, `tasks`, `evaluator_report`, `audit_result`. |

Payload: `{tool, ok, kind, valid, errors[], detail}` · `0` valid · `1` invalid.

### `diff_check` — tasks 1.6, 9.4
The first gate: is the Builder's output a parseable unified diff (REQ-STAT-005)?

| Argument | Required | Meaning |
|---|---|---|
| `--in PATH \| -` | yes | The Builder's raw output. |
| `--repo PATH` | no | Also run `git apply --check` against this tree. |

Payload: `{tool, ok, verdict, repaired, git_apply, detail, diff}` · `0` parseable · `1` malformed.
`repaired` reports whether REQ-STAT-005 AC2's single bounded repair (unwrapping a code
fence) was spent.

### `scope_probe` — task 3.2
The lookup-time scope fingerprint for the spec-cache key (REQ-CACHE-002). A filesystem
walk — **not** a Phase-1 fire and not an LSP session.

| Argument | Required | Meaning |
|---|---|---|
| `--repo PATH` | no | Workspace (default cwd). |
| `--scope PATH` | no | Repeatable. Used **directly, with no probe** (AC2). |
| `--language L` | no | Override the language from project config. |

Payload: `{tool, ok, verdict, scope[], count, detail}` · always `0` — an empty repo
yielding an empty fingerprint is REQ-CACHE-002 AC3 working, not a failure.

### `cache_lookup` — task 5.7
The cache ladder, strictly top-down, stopping at the first hit (REQ-CACHE-001).

| Argument | Required | Meaning |
|---|---|---|
| `--repo PATH` | no | Workspace (default cwd). |
| `--goal TEXT` | yes | The sprint goal. |
| `--scope PATH` | no | Repeatable; skips the probe. |
| `--offline` | no | Exact tier only; the semantic tier reports `unavailable`. |

Payload: `{tool, ok, verdict, key, scope_fingerprint, scope_source, thresholds, tasks?, similarity?, pcd?, confirmation?, detail}`.
`verdict` is `exact_hit` (`0`), `semantic_candidate` (`0`), or `miss` (`1`).

**A `semantic_candidate` is not authorisation to reuse.** REQ-CACHE-003 AC1 requires an
Architect confirmation first; `confirmation` says how expensive that must be.

### `sandbox_apply` — task 3.1
Apply a diff to a disposable git worktree inside the security container. The live tree is
never touched.

| Argument | Required | Meaning |
|---|---|---|
| `--repo PATH` | no | Workspace to branch the worktree from. |
| `--in PATH \| -` | yes | The unified diff. |
| `--keep` | no | Leave the sandbox on disk for inspection. |

Payload: `{tool, ok, verdict, sandbox, backend, detail}` · `0` applied · `1` did not apply.

### `static_gate` — task 9.2
`pyright`+`ruff` / `tsc`+`eslint` / `cargo check`+`clippy` / `go build`+`vet` / `eslint`,
per language, inside the container, on the sandbox (REQ-STAT-001/002).

| Argument | Required | Meaning |
|---|---|---|
| `--sandbox PATH` | yes | The worktree to analyse. |
| `--repo PATH` | no | Workspace, for project config and toolchain resolution. |
| `--language L` | no | Override the language from project config. |

Payload: `{tool, ok, verdict, strength, runners[], detail}` ·
`0` `clean` · `1` `dirty` or `unavailable`.

**`unavailable` is not `clean`.** A runner whose tool is not installed, or which failed on
its own configuration, yields `unavailable` — checked *before* `dirty`, so a broken
`pyrightconfig.json` cannot be laundered into "your code is wrong" and spend the Builder's
retry budget.

### `test_run` — task 9.3
The task's acceptance spec, in the **same** container and sandbox (REQ-STAT-004).

| Argument | Required | Meaning |
|---|---|---|
| `--sandbox PATH` | yes | The worktree. |
| `--task-id ID` | yes | Selects `tests/task_{id}_spec.*`. |
| `--repo PATH` | no | Workspace, for `test_runner_cmd` and the spec file. |

Payload: `{tool, ok, outcome, output, detail}` ·
`0` `pass` or `skipped` · `1` `fail` or `no_tests`.

**`no_tests` is a failure.** `pytest` signals it with exit 5, but `cargo test` prints
`running 0 tests` and `go test` prints `[no test files]` and *both exit 0* — so an empty
run would otherwise pass on two of the four frameworks. A missing `test_runner_cmd` is a
legitimate `skipped` at exit 0 (AC3).

### `compute_stability` — tasks 10.1, 10.2
The Auditor: zero-cost heuristics, then the N-pass judgment, then `audit_result.json`.

| Argument | Required | Meaning |
|---|---|---|
| `--repo PATH` | no | Workspace (default cwd). |
| `--task-id ID` | yes | The task being audited. |
| `--diff PATH \| -` | yes | The unified diff. |
| `--tasks PATH` | no | `tasks.json`, for the acceptance criteria. |
| `--static PATH` | no | The clean static report. |
| `--tests PATH` | no | The test-runner output. |
| `--spec PATH` | no | The task spec the tests came from. |
| `--passes N` | no | REQ-AUD-002's N, default 3. |
| `--model M` | no | Saltnitor router section: `A_STD`, `A_FOCUS`, `B`. |
| `--online` | no | Report the online escalation recommendation. **Makes no network call.** |

Payload: `{tool, ok, verdict, next_action, audit_result, heuristics, measurement, escalation, online, criteria_available}` ·
`0` `pass` · `1` any other verdict · `3` the judgment could not be run.

**Nothing here escalates.** REQ-AUD-002 AC2's Flash re-judgment is the extension's move;
this reports `escalation ∈ {none, flash_rejudgment, offline_majority}` and the extension
spends the API call.

### `apply_live` — task 10.3
Land an Auditor-passed diff on the live working tree, **uncommitted**.

| Argument | Required | Meaning |
|---|---|---|
| `--repo PATH` | no | The live tree (default cwd). |
| `--in PATH \| -` | yes | The unified diff. |

Payload: `{tool, ok, status, detail, paths[], blocked_paths[], committed}` ·
`0` `applied` · `1` `refused` or `failed`.

`committed` is always `false`, and says so explicitly: design §10.1 puts the commit after
the regression gate, so a caller that assumed otherwise would skip the gate that catches
cross-task breakage. A `tests/**` write is `refused` (REQ-BLD-003) and is reported
*before* git is asked, so it never surfaces as "the patch did not apply".

### `compact_spec` — task 12.1
Strip completed and resolved content from `design.md`, leaving `## HARD CONSTRAINTS`
byte-identical (REQ-CMP-001 AC1).

| Argument | Required | Meaning |
|---|---|---|
| `--design PATH` | yes | `design.md`. |
| `--sprint N` | no | Fires every 5. **Omit to force.** |
| `--proposed PATH` | no | A model-produced rewrite, treated as untrusted. |
| `--dry-run` | no | Report without writing. |

Payload: `{tool, ok, verdict, written, dry_run, constraints_chars, original_length, compacted_length, bytes_saved, sections_removed[], constraints_preserved, detail}` ·
`0` `compacted`, `no_change` or `not_due` · `1` `refused`.

A refusal leaves the file untouched: it is the outcome REQ-CMP-001 exists to produce when
preservation cannot be guaranteed.

### `calibrate` — task 14b.1
The measured-then-fixed protocol (REQ-CAL-001, REQ-AUD-005).

| Argument | Required | Meaning |
|---|---|---|
| `--repo PATH` | no | Workspace (default cwd). |
| `--set PATH` | yes | The labelled calibration set (JSON; shape in the module docstring). |
| `--only HALF` | no | `auditor` or `semantic`. |
| `--passes N` | no | N for the Auditor measurement. |
| `--model M` | no | Saltnitor router section. |
| `--dry-run` | no | Measure and report without writing. |

Payload: `{tool, ok, verdict, written, artifact, dry_run, measured[], thresholds, problems[], resolved, warning, auditor?, semantic?}` ·
`0` at least one threshold measured · `1` `nothing_to_measure` or `not_measurable`.

Only thresholds this run actually measured appear under `thresholds` — that mapping is
what marks a threshold `calibrated: true`, so writing a key for anything else would forge
the flag.

### `read_scoped` — task 10.4
The Builder's scoped file read (REQ-BLD-002).

| Argument | Required | Meaning |
|---|---|---|
| `--repo PATH` | no | Workspace root (default cwd). |
| `--tasks PATH` | no | `tasks.json` (default `<repo>/.saltcode/tasks.json`). |
| `--task-id ID` | yes | The task whose scope applies. |
| `--path PATH` | yes | The file to read. |

Payload: `{tool, ok, verdict, task_id, path, files_affected[], bytes?, content?, detail?}` ·
`0` `in_scope` · `1` `out_of_scope` or `not_readable`.

**There is deliberately no `--files` argument** and the role is fixed to `builder`. The
scope comes from the contract; a backend re-check that accepts the caller's copy of the
allowed list is not an independent check (`.claude/rules/privacy-boundary.md`). A refusal
payload carries no `content` key at all.

### `contained_exec` — task 13.3 (added for G-029)
Run one allowlisted command — or land one file — **inside the security container**. This is
the channel REQ-SEC-007 AC1 needs: Pi's built-in `write`/`edit`/`bash` are overridden by
the extension, and each override routes here rather than touching the host.

The other thirteen each run one *specific* contained job. This one takes the caller's argv,
which is why the allowlist is the whole authorisation and there is deliberately no way to
extend it from the command line.

| Argument | Required | Meaning |
|---|---|---|
| `--sandbox PATH` | yes | The container's writable root (see below). |
| `--repo PATH` | no | Workspace whose `.saltcode/audit_log.jsonl` receives the record (default: `--sandbox`). |
| `--arg WORD` | exec mode | One argv element; repeat for each. |
| `--write-path PATH` | copy-in mode | Destination, relative to `--sandbox`. |
| `--content-file PATH` | copy-in mode | The bytes to land there. |
| `--timeout SECONDS` | no | Override the time ceiling only. |

Exactly one mode per invocation: `--arg`, or both `--write-path` and `--content-file`.
Neither or both is exit `2`.

Payload: `{tool, ok, mode, verdict, exit_code, stdout, stderr, backend, container_id,
refused, timed_out, limits_enforced, detail}` — plus `argv` (exec) or `write_path`/`bytes`
(copy-in) · `0` the command exited 0 · `1` it exited non-zero, was refused by the
allowlist, timed out, or the write path was rejected · `3` **no containment backend**.

**A refusal is exit 1, an absent container is exit 3**, and the difference matters.
"Not permitted" is a normal outcome the caller routes on; "there is nowhere safe to run
this" is REQ-SEC-005's stop condition and must never read as a verdict.

**Copy-in does not widen the allowlist.** Its argv is `cp <content-file> <target>`, built
by the module, and a one-entry allowlist authorises exactly that for that call. The
allowlist exists to constrain *caller-supplied* commands; here the caller supplies a
destination and some bytes, both checked — the path is confined to `--sandbox` after
resolution (so `..` and symlinks cannot escape) and `tests/**` is refused outright.

**What `--sandbox` means.** It is the writable root. In Phase 2 that is the disposable
worktree, so the live tree is untouched. In interactive mode the extension passes the
project directory, because a `write` the human asked for has to land in the project. Every
other container guarantee is unchanged either way — no network, no `$HOME`, no
credentials, isolated PID namespace, memory/CPU/time ceilings, auto-cleanup. What
interactive mode gives up against Phase 2 is the read-only *project*, not containment.

### `connectivity` — task 2.3
The online/offline probe that selects the Phase-1 tier (REQ-MOD-003, design §12).

| Argument | Required | Meaning |
|---|---|---|
| `--timeout SECONDS` | no | Probe timeout. |

Payload: `{tool, ok, online, endpoints, detail}` · always `0` — being offline is a
supported mode, not a failure.

---

## What is *not* promised

- **Field order and whitespace.** `emit` uses `sort_keys=True` and 2-space indent so
  output is byte-stable for a given payload, which keeps entrypoint tests deterministic —
  but parse the JSON, do not diff it.
- **`detail` strings.** Written for humans and free to change. Route on `verdict` and the
  exit code.
- **Additional keys.** Tools may add fields; a reader must ignore unknown ones.
- **Cross-tool key meanings.** `verdict` is per-tool. `static_gate`'s `clean` and
  `test_run`'s `pass` are both exit 0 and neither is a synonym for the other.
