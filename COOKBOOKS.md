# Cookbooks — third-party skill provenance and trust review

Task 7.1. Every skill Saltcode ships that it did not write. For each: where it came
from, under what licence, when it was reviewed, and what the review concluded.

REQ-SEC-006 is the reason this file exists: an adopted skill runs with full system
permissions, so adoption is a **trust decision**, and a trust decision that is not
written down has not been made. A skill whose row here says `not adopted` is not
shipped under `skills/` — it stays in `.agents/skills/` as a local working copy.

**Reviewed 2026-08-02.** Every executable file in every skill below was read in full,
not sampled.

---

## How these arrived

All eight third-party skills entered the repository through a **single bulk install
from an Antigravity skills marketplace**, recorded in
`.agents/.antigravity-install-manifest.json`: schema 1, `updatedAt`
`2026-06-20T15:19:50Z`, **1,679 entries**, of which these eight are members. No
`saltcode-*` skill appears in that manifest, which independently confirms the eight
bespoke ones are ours.

That channel is worth stating plainly because it is the weakest part of the provenance
story. A curated vendoring from a named upstream carries the upstream's identity with
it; a 1,679-entry bulk catalogue install does not. Per-skill upstreams below are
therefore established by **content matching against the candidate upstream's own
description of its contents**, and are labelled with how confident that match is —
never asserted from the name alone.

## The register

| Skill | In design §17 | Declared source | Licence | Provenance confidence | Verdict |
|---|---|---|---|---|---|
| `mcp-builder` | ✅ | community | **Apache 2.0** (`LICENSE.txt` bundled in-tree) | **Certain** — licence travels with the copy | **adopt** |
| `test-driven-development` | ✅ | community | **MIT** (`obra/superpowers`) | **High** — see below | **adopt** |
| `systematic-debugging` | ✅ | community | **MIT** (`obra/superpowers`) | **High** — see below | **adopt** |
| `agent-tool-builder` | ✅ | `vibeship-spawner-skills` | **Apache 2.0** (declared in frontmatter) | Medium — declared, not bundled | **adopt** |
| `python-pro` | ✅ | community | **undetermined** | Medium — matches `rmyndharis/antigravity-skills` | **adopt, licence recorded as undetermined** — F-2 |
| `git-pushing` | ✅ | community | **undetermined** | Low | **adopt, script removed** — F-1 |
| `agent-evaluation` | ❌ | `vibeship-spawner-skills` | Apache 2.0 (declared) | Medium | not adopted — not in §17 |
| `ai-agents-architect` | ❌ | `vibeship-spawner-skills` | Apache 2.0 (declared) | Medium | not adopted — not in §17 |

**Why `obra/superpowers` is a High match and not a guess.** The upstream describes its
own skills as *"test-driven-development — RED-GREEN-REFACTOR cycle (**includes testing
anti-patterns reference**)"* and *"systematic-debugging — 4-phase root cause process
(includes root-cause-tracing, defense-in-depth, **condition-based-waiting** techniques)"*.
The vendored copies carry exactly `test-driven-development/testing-anti-patterns.md` and
`systematic-debugging/condition-based-waiting-example.ts`. Auxiliary filenames matching
the upstream's own inventory is evidence the name alone cannot supply. Repository licence
confirmed MIT at <https://github.com/obra/superpowers>.

## Trust review

**No malicious content was found in any skill.** No obfuscation, no network exfiltration,
no credential access, no destructive filesystem operation. Four executables exist and all
four were read in full:

| File | What it does | Assessment |
|---|---|---|
| `systematic-debugging/find-polluter.sh` | bisects a test suite to find which test creates a stray file; runs `npm test` per file | benign |
| `systematic-debugging/condition-based-waiting-example.ts` | illustrative sample, not executed | benign |
| `mcp-builder/scripts/connections.py` | opens an MCP connection over stdio/SSE/HTTP | benign |
| `mcp-builder/scripts/evaluation.py` | evaluation harness; calls the Anthropic API to score an MCP server | benign, **network-capable** — see F-3 |
| `git-pushing/scripts/smart_commit.sh` | `git add .` → `git commit` → `git push -u origin $BRANCH` | **conflicts with project rules** — F-1 |

Five of the eight skills carried `risk: unknown` in their frontmatter, meaning the review
had never been completed on any of them. That is what this pass closes.

### F-1 — `git-pushing` contradicts the checkpoint discipline · **do not adopt**

`scripts/smart_commit.sh` is nineteen lines and does exactly what it says:

```bash
git add .
git commit -m "$MESSAGE"
git push -u origin "$(git rev-parse --abbrev-ref HEAD)"
```

Nothing about it is malicious. It is nonetheless the one skill here that must not ship,
because it teaches an agent a workflow this project forbids in two separate places:

* `.claude/rules/project-architecture.md`: *"Pushing stays explicit: no automatic
  `git push` unless `auto_push = true`, **in any mode**."* The script pushes
  unconditionally and takes no such flag.
* The same rule's checkpoint discipline: *"after `apply_live` the tree stays
  **uncommitted** until the regression gate passes; only then does the commit +
  `saltcode:checkpoint` snapshot happen."* A `git add .` mid-task commits the
  uncommitted apply the regression gate has not yet cleared — and sweeps in
  `.saltcode/` state and Test Intent's uncommitted `tests/task_*_spec.*` besides.

Its own frontmatter already said `risk: critical`. This review agrees and gives the
reason.

**Resolution (maintainer, 2026-08-02): adopt it with `scripts/` removed, keep the prose.**
Design §17 names `git-pushing` among the six cookbooks, and the commit-message guidance is
the part worth having; the script was the whole hazard. `skills/git-pushing/` therefore
ships `SKILL.md` only. The body has been rewritten so it does not invoke a file that no
longer exists — a dangling `bash …/smart_commit.sh` would be worse than the script itself,
since it fails at the point of use with no explanation — and now states *why* the steps
are explicit: `auto_push` and the uncommitted-until-regression boundary are project rules
a one-shot script cannot express.

### F-2 — `python-pro` has no determinable licence · **adopt, recorded**

Content matches `rmyndharis/antigravity-skills`, but no licence travels with the copy and
none was confirmed upstream. Task 7.1 says to prefer a bespoke skill over an *unlicensed*
import, which is why this was raised rather than waved through. It is otherwise entirely
benign — prose only, no executables.

**Resolution (maintainer, 2026-08-02): ship it, with the licence recorded here as
undetermined.** It is a widely circulated community skill with no executable content, and
it is not redistributed beyond this repository. The honest record is the point: this row
says "undetermined", not "assumed MIT", so anyone later deciding whether Saltcode can be
published under a given licence sees exactly what is and is not known.

### F-3 — `mcp-builder/scripts/evaluation.py` makes outbound API calls

It constructs an `Anthropic()` client and accepts `-u <url>` plus arbitrary `-H` headers.
This is correct and expected for an MCP evaluation harness, and it is **operator-run, not
agent-run** — nothing in the Saltcode pipeline invokes it. Recorded because a script that
takes a URL and an `Authorization` header is exactly the shape that must never be handed
repository source: `.claude/rules/privacy-boundary.md` binds it like anything else, and
no agent has a path to it.

## Not third party

`skills/saltcode-*` are written for this project and have no upstream. `saltcode-planning`
is a build-planning skill for *this* repository's own development and is deliberately
**not** one of design §7's eight agent skills — it is not shipped as part of the package.
