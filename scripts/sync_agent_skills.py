#!/usr/bin/env python3
"""Splice each agent's Saltcode skill into its sub-agent definition body.

**Why this exists.** Design DD-16 requires every agent's skill to be *preloaded* into its
sub-agent system prompt, because Pi only auto-injects a skill for an agent holding `read`
and Saltcode's agents are deliberately locked down — Scout has no file-body capability at
all. The sub-agent extension turns out not to provide preloading either: its `skills:`
frontmatter emits a manifest plus "use the read tool to load a skill's file", which is the
same dependency (see G-023 and `docs/subagent_contract.md` §4).

What *does* satisfy DD-16 is the definition body: `pi-subagents` uses it as the child's
system prompt verbatim under `systemPromptMode: replace`. So the skill is inlined here —
no tool call, no `read`, present by construction.

The cost of inlining is duplication, and duplication drifts. This script is the fix: the
`SKILL.md` stays the single hand-authored source, and each `agents/*.md` declares its
source in frontmatter (`skill-source:`) and carries a marker block this script fills.

    python scripts/sync_agent_skills.py           # rewrite the marker blocks
    python scripts/sync_agent_skills.py --check   # exit 1 if any file is stale

`--check` is what the test suite runs, so a hand-edit inside a marker block or a change to
a `SKILL.md` that was not synced fails CI rather than silently shipping an agent whose
prompt disagrees with its skill.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / "agents"

BEGIN = "<!-- BEGIN SKILL: {source} — generated, do not edit between markers -->"
END = "<!-- END SKILL -->"

_FRONTMATTER_RE = re.compile(r"\A---\n(?P<body>.*?)\n---\n", re.DOTALL)
_SOURCE_RE = re.compile(r"^skill-source:\s*(?P<paths>.+?)\s*$", re.MULTILINE)
_BLOCK_RE = re.compile(
    r"<!-- BEGIN SKILL:.*?-->\n(?P<content>.*?)(?P<end>\n?)<!-- END SKILL -->",
    re.DOTALL,
)


class SyncError(RuntimeError):
    """A definition or skill that cannot be synced, reported rather than skipped."""


def skill_body(skill_path: Path) -> str:
    """The skill's prose, with its YAML frontmatter removed.

    The frontmatter is Pi's skill-catalogue metadata (`name`, `description`); splicing it
    into a system prompt would put a stray `---` document separator mid-prompt and repeat
    a description the definition already states in its own frontmatter.
    """
    text = skill_path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    return (text[match.end() :] if match else text).strip()


def sync_one(agent_path: Path, *, check: bool) -> bool:
    """Return True when `agent_path` was already in sync."""
    text = agent_path.read_text(encoding="utf-8")

    frontmatter = _FRONTMATTER_RE.match(text)
    if frontmatter is None:
        raise SyncError(f"{agent_path.name}: no frontmatter, so no agent would load from it")

    source = _SOURCE_RE.search(frontmatter.group("body"))
    if source is None:
        raise SyncError(f"{agent_path.name}: no `skill-source:` key, so its skill cannot be inlined")

    # Comma-separated, matching how `pi-subagents` parses its own list fields. An agent can
    # need more than one skill: REQ-EXT-016 AC2 preloads `saltcode-lsp-usage` for Scout and
    # Builder *alongside* each one's own agent skill.
    sources = [p.strip() for p in source.group("paths").split(",") if p.strip()]
    if not sources:
        raise SyncError(f"{agent_path.name}: `skill-source:` is empty")

    bodies: list[str] = []
    for rel in sources:
        skill_path = REPO_ROOT / rel
        if not skill_path.is_file():
            raise SyncError(f"{agent_path.name}: skill-source {rel} does not exist")
        bodies.append(skill_body(skill_path))

    block = _BLOCK_RE.search(text)
    if block is None:
        raise SyncError(f"{agent_path.name}: no BEGIN/END SKILL marker block to splice into")

    wanted = "\n\n".join(bodies)
    if block.group("content").strip() == wanted:
        return True

    if check:
        return False

    replacement = BEGIN.format(source=", ".join(sources)) + "\n" + wanted + "\n" + END
    agent_path.write_text(text[: block.start()] + replacement + text[block.end() :], encoding="utf-8")
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Report staleness without writing.")
    args = parser.parse_args(argv)

    definitions = sorted(AGENTS_DIR.glob("*.md"))
    if not definitions:
        print(f"no agent definitions under {AGENTS_DIR}", file=sys.stderr)
        return 1

    stale: list[str] = []
    for agent_path in definitions:
        try:
            if not sync_one(agent_path, check=args.check):
                stale.append(agent_path.name)
        except SyncError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    if not stale:
        print(f"{len(definitions)} agent definitions in sync")
        return 0

    if args.check:
        print(
            "stale (skill content differs from its source): " + ", ".join(stale) + "\n"
            "run `python scripts/sync_agent_skills.py` to regenerate",
            file=sys.stderr,
        )
        return 1

    print(f"synced: {', '.join(stale)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
