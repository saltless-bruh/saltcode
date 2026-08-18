# The sub-agent extension contract (Task 7.0)

What `pi-subagents` **actually** accepts, read from its source rather than inferred
from its README. Task 7.2 authors `agents/*.md` against this document; task 7.0 exists
because a guessed frontmatter schema produces definitions that load without error and
silently drop the fields that matter.

**Pinned version:** `pi-subagents@0.40.0` (npm, MIT, Nico Bailon,
<https://github.com/nicobailon/pi-subagents>). Read on 2026-08-02 from the published
tarball. **Nothing is installed** — adoption is a trust decision under REQ-SEC-006 and
belongs to whoever performs the review, exactly as G-011 left the MCP client extension.

> **Scope note.** This records a *capability contract*, per REQ-EXT-015: Saltcode depends
> on "isolated-context spawn with per-agent model, thinking level and tool allowlist",
> not on `pi-subagents` internals. Where a field below is idiosyncratic to this
> implementation rather than inherent to the capability, it is marked **[impl]** — a
> substitute extension is not required to provide it.

---

## 1. Peer-dependency scope — this one is on the right side of the rename

```json
"peerDependencies": {
  "@earendil-works/pi-agent-core": "*",
  "@earendil-works/pi-ai": ">=0.80.0",
  "@earendil-works/pi-coding-agent": "*",
  "@earendil-works/pi-tui": "*"
}
```

All four are **optional** (`peerDependenciesMeta`), and its devDependencies pin `0.81.0`.
This project targets `^0.82.1`.

This matters because **G-011 found the opposite for `pi-mcp-extension`**, which declares
peers on the *pre-rename* `@mariozechner/pi-*` scope and may not load on Pi 0.82.1 at
all (REQ-EXT-001 AC2). The sub-agent half of the DD-15 dependency pair does not carry
that risk. The MCP half still does; that remains G-011's problem and Task 20.1's.

## 2. Where agent definitions are discovered

`loadAgentsFromDir` scans a directory **recursively** for `*.md`, excluding
`*.chain.md` and anything under a `.agents/skills/` path. A file is skipped silently
unless it has **both** `name:` and `description:` in frontmatter.

| Scope | Directory | Notes |
|---|---|---|
| builtin | `<package>/agents/` | the nine shipped agents |
| **package** | whatever the package's own `package.json` declares | see below |
| project | `<projectRoot>/.agents/` | legacy, still read |
| project | `<projectRoot>/.pi/agents/` | preferred (`.pi` is `DEFAULT_CONFIG_DIR_NAME`) |
| user | `~/.agents/`, `~/.pi/agent/agents/` | |
| user | `$PI_SUBAGENT_EXTRA_AGENT_DIRS` | PATH-style, read-only, loaded as `user` |

**A repo-root `agents/` directory is read by none of these paths by default.** Design
§17's tree puts Saltcode's definitions exactly there, so the location has to be declared.
Package-scope discovery reads the package's `package.json` (`extractSubagentPathsFromPackageRoot`)
and accepts **either** key, resolving entries against the package root:

```jsonc
{ "pi-subagents": { "agents": ["./agents"], "chains": ["./chains"] } }
// or, nested under the existing "pi" key:
{ "pi": { "subagents": { "agents": ["./agents"] } } }
```

Saltcode already ships as a Pi package with a `pi` key, so the nested form keeps design
§17's tree correct with one addition and no relocation. **Caveat:** package roots are
collected from `node_modules`, so this path is live for an *installed* Saltcode. A
working copy being developed in place is not in `node_modules`, so dev-time and
Task 7.3 spawn verification need either an install, a link, or `.pi/agents/`.

## 3. Frontmatter — the fields Saltcode uses

Confirmed at `src/agents/agents.ts:1428-1560`. Parsing is a **hand-rolled** frontmatter
reader (`src/agents/frontmatter.ts`), not a YAML library: it takes `key: value`, folded
block scalars (`>`/`>-`), and nested blocks stored as raw strings. Keys must match
`^[\w-]+:`. Unrecognised keys are preserved as `extraFields`, **not** rejected — a typo
is silent.

| Field | Type | Meaning |
|---|---|---|
| `name` | string | **required**; the spawn identifier |
| `description` | string | **required**; how the parent selects the agent |
| `tools` | list | **strict child allowlist.** Comma-separated or `- item` block |
| `model` | string | default model id |
| `thinking` | string \| `false` | appended as a `:level` **model-id suffix** at runtime |
| `systemPromptMode` | `replace` \| `append` | `replace` is the default for every name but `delegate` |
| `inheritProjectContext` | `true` \| `false` | keep or strip inherited project instruction blocks |
| `inheritSkills` | `true` \| `false` | keep or strip Pi's discovered skills catalog; defaults **false** |
| `skills` (or `skill`) | list | selects skills for the child — **see §4, this is not a preload** |
| `skillPath` | list | extra skill *discovery* directories, relative to the definition file |
| `output` | string | default output file for a single-agent run |
| `defaultContext` | `fresh` \| `fork` | launch context |
| `fallbackModels` | list | ordered backups for provider/model failure only |
| the Markdown **body** | text | becomes the child's `systemPrompt` **verbatim** |

**[impl]** `package`, `aliases`, `extensions`, `subagentOnlyExtensions`, `defaultReads`,
`defaultProgress`, `async`, `timeoutMs`, `turnBudget`, `acceptance`, `acceptanceRole`,
`completionGuard`, `interactive`, `maxSubagentDepth`, `toolBudget`, `memory`.

### Thinking levels

Pi's own vocabulary (`@earendil-works/pi-ai`, `types.d.ts:22-23`) is
`ModelThinkingLevel = "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max"`,
so design §6's `off` / `medium` / `high` are all valid Pi levels and map straight across.

Two mechanisms must not be confused:

* `thinking: off` — Pi's real "no reasoning" level, appended as the `:off` suffix.
* `thinking: false` — **[impl]** the extension's opt-out: append *no* suffix and inherit.

Design §6 asks for `off` on Scout, Planner, Test Intent, Evaluator and the Compactor.
That is `thinking: off`, not `thinking: false`. Whether a given provider accepts the
`:off` suffix is a runtime fact and is a **7.3 verification item**, not an assumption.

## 4. Skill delivery — the contract does *not* preload skill content

This is the one place where the design's stated premise and the extension's behaviour
disagree, and it changes how every definition is authored.

`skills:` resolves each named skill and passes it to `buildSkillInjection`
(`src/agents/skills.ts:671-690`), which appends to the system prompt:

```text
The following configured skills are available to this subagent.
Use the read tool to load a skill's file when the task matches its description.
...
<available_skills>
  <skill>
    <name>saltcode-scout</name>
    <description>…</description>
    <location>/abs/path/to/SKILL.md</location>
  </skill>
</available_skills>
```

**A manifest and a file path — not the skill's text.** The child must hold `read` and
spend a tool call to obtain the content.

Design DD-16 asserts the opposite: *"each agent's skill is **preloaded** into its
sub-agent system prompt (a feature of the sub-agent extension), not left to Pi's
description-match auto-loading — because Pi only injects a skill into an agent's prompt
if that agent holds the `read` tool, and Saltcode agents are locked down."* Task 7.2
repeats it: *"its Saltcode skill **preloaded directly** into the prompt (do NOT rely on
Pi's read-tool auto-discovery — locked-down agents lack `read`)."*

The named feature does not exist in 0.40.0. `skills:` reproduces precisely the
read-tool dependency DD-16 was written to avoid. Note that content injection *does*
exist for **[impl]** `memory:` (a `MEMORY.md` excerpt goes into the child prompt
directly), so the absence for skills is a real gap in the extension, not a misreading
of it.

Granting `read` to close the gap is **not available**: `.claude/rules/privacy-boundary.md`
denies Scout any file-body capability, and REQ-MCP-001 makes that a hard invariant.

**The mechanism that does satisfy DD-16's intent is the body.** `systemPrompt: body`
(`agents.ts:1541`) with `systemPromptMode: replace` puts the definition's Markdown into
the child's system prompt verbatim, with no tool call and no `read`. Preloading is
therefore achievable — by placing the skill's content in the agent definition body
rather than by naming it in `skills:`. The cost is that the text then lives in two
places, which needs a sync mechanism or it drifts. **Resolution is a maintainer
decision; see the Task 7 entry in `specs/known_gaps.md` (G-023).**

## 5. Spawn API

Three surfaces, in increasing order of coupling:

1. **The `subagent` tool** (`src/extension/index.ts:404`, registered via
   `pi.registerTool`). Single-agent, chain, and parallel launches. This is what an LLM
   calls; it is *not* what Saltcode's `/sprint` handler should call, since design §5.6a
   requires the command handler — not a model — to own ordering.
2. **`pi-subagents/delegation`** — a versioned event contract
   (`SUBAGENT_DELEGATION_REQUEST_EVENT` / `..._RESPONSE_EVENT`, v1) for an extension to
   request one configured **foreground** agent per request. This is the surface Task
   13.8's serial Phase-1 spawn should target.
3. **`pi-subagents/preflight`** — `resolveSubagentLaunchContract({agent, task, context,
   cwd, sessionRoot, availableModels})`, side-effect-free, returning the resolved
   contract (`effectiveAllowlist`, effective model and thinking, skill resolution,
   digests) or a typed failure: `missing_agent`, `ambiguous_agent`, **`missing_skill`**,
   **`denied_required_tool`**, `invalid_artifact_dir`, `invalid_cwd`, `unsupported_mode`.

Preflight is directly useful to **task 7.3b**: it resolves a launch contract without
running anything, so "Scout cannot obtain a file body" can be asserted against
`contract.tools.effectiveAllowlist` rather than by observing a live spawn. It is
evidence about the *allowlist*, not about the agent's behaviour, so 7.3b still needs a
real spawn for the behavioural half.

## 6. What this document does not promise

Field *semantics* beyond what the source shows; behaviour of **[impl]** fields Saltcode
does not use; that a substitute extension meeting REQ-EXT-015's capability contract
offers the same field names; and anything about versions other than 0.40.0. The parser
accepts unknown keys silently, so a field absent from this table is not thereby safe —
it is unread.
