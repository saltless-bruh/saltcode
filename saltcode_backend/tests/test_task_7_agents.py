"""Task 7.2 — the sub-agent definitions, and the invariant that keeps them honest.

These are repo-root assets, not backend modules, but they are asserted from the backend
lane because it is the only lane that runs assertions. `test_task_7b_entrypoints.py`
already reaches up to `docs/entrypoints.md` the same way, so the precedent is set.

**What actually needs guarding.** Design DD-16 requires each agent's skill to be preloaded
into its system prompt. `pi-subagents` does not provide that (G-023), so the skill is
inlined into the definition body, which `pi-subagents` uses as the child prompt verbatim.
Inlining means the text exists twice, and text that exists twice drifts. The guard is
`scripts/sync_agent_skills.py --check`: if a `SKILL.md` changes and the definition is not
regenerated, or someone hand-edits between the markers, this fails.

Without it the failure is silent and expensive — an agent whose prompt disagrees with its
skill still loads, still spawns, and still produces plausible output.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"
SKILLS_DIR = REPO_ROOT / "skills"
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_agent_skills.py"

AGENTS: tuple[str, ...] = ("scout", "architect", "planner", "test-intent", "evaluator", "builder")
"""The six spawned sub-agents (design §7). The Auditor is deliberately absent — its
N-pass judgment is the backend `compute_stability` tool, not a spawned agent (§5.6a)."""

_FRONTMATTER_RE = re.compile(r"\A---\n(?P<body>.*?)\n---\n", re.DOTALL)


def frontmatter(name: str) -> dict[str, str]:
    text = (AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    assert match is not None, f"{name}.md has no frontmatter, so no agent would load from it"
    fields: dict[str, str] = {}
    for line in match.group("body").splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def body(name: str) -> str:
    text = (AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    return text[match.end() :] if match else text


# ------------------------------------------------------------------ the drift guard


def test_every_agent_definition_is_in_sync_with_its_skill() -> None:
    """The one that matters. See the module docstring."""
    completed = subprocess.run(
        [sys.executable, str(SYNC_SCRIPT), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, (
        f"agent definitions are stale against their skills:\n{completed.stderr}"
    )


def test_the_sync_script_detects_a_hand_edit_between_the_markers(tmp_path: Path) -> None:
    """A guard that cannot fail is not a guard.

    Proves `--check` actually compares content rather than merely finding the markers —
    otherwise the test above would pass on a definition whose skill block had been
    emptied.
    """
    scratch = tmp_path / "repo"
    (scratch / "agents").mkdir(parents=True)
    (scratch / "skills" / "demo").mkdir(parents=True)
    (scratch / "scripts").mkdir()

    (scratch / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: d\n---\n\nThe real skill text.\n", encoding="utf-8"
    )
    (scratch / "agents" / "demo.md").write_text(
        "---\nname: demo\ndescription: d\nskill-source: skills/demo/SKILL.md\n---\n\n"
        "# Demo\n\n"
        "<!-- BEGIN SKILL: skills/demo/SKILL.md — generated, do not edit between markers -->\n"
        "Something a human typed instead.\n"
        "<!-- END SKILL -->\n",
        encoding="utf-8",
    )
    (scratch / "scripts" / "sync_agent_skills.py").write_bytes(SYNC_SCRIPT.read_bytes())

    stale = subprocess.run(
        [sys.executable, str(scratch / "scripts" / "sync_agent_skills.py"), "--check"],
        capture_output=True, text=True, cwd=scratch, timeout=120, check=False,
    )
    assert stale.returncode == 1, "a hand-edited skill block was reported as in sync"
    assert "demo.md" in stale.stderr

    fixed = subprocess.run(
        [sys.executable, str(scratch / "scripts" / "sync_agent_skills.py")],
        capture_output=True, text=True, cwd=scratch, timeout=120, check=False,
    )
    assert fixed.returncode == 0
    assert "The real skill text." in (scratch / "agents" / "demo.md").read_text(encoding="utf-8")


# ------------------------------------------------------- the roster and the schema


def test_the_six_spawned_agents_exist_and_the_auditor_does_not() -> None:
    on_disk = {p.stem for p in AGENTS_DIR.glob("*.md")}
    assert on_disk == set(AGENTS), f"unexpected: {sorted(on_disk ^ set(AGENTS))}"
    assert "auditor" not in on_disk, "the Auditor is a backend tool, not a spawned sub-agent (§5.6a)"


@pytest.mark.parametrize("name", AGENTS)
def test_required_frontmatter_keys_are_present(name: str) -> None:
    """`pi-subagents` skips a definition lacking `name` or `description` *silently*."""
    fields = frontmatter(name)
    assert fields.get("name", "").startswith("saltcode-")
    assert len(fields.get("description", "")) > 20


@pytest.mark.parametrize("name", AGENTS)
def test_each_definition_replaces_the_prompt_and_inherits_nothing(name: str) -> None:
    """REQ-EXT-012: an isolated context, not the parent's with extras.

    `systemPromptMode: replace` is also what makes the inlined skill the *whole* prompt
    rather than an appendix to Pi's base one.
    """
    fields = frontmatter(name)
    assert fields.get("systemPromptMode") == "replace"
    assert fields.get("inheritProjectContext") == "false"
    assert fields.get("inheritSkills") == "false"


@pytest.mark.parametrize("name", AGENTS)
def test_each_definition_declares_its_skill_source(name: str) -> None:
    """`skill-source` is comma-separated: an agent may preload more than one skill."""
    sources = [p.strip() for p in frontmatter(name).get("skill-source", "").split(",") if p.strip()]
    assert sources, f"{name} declares no skill-source"
    for source in sources:
        assert source.startswith("skills/saltcode-"), source
        assert (REPO_ROOT / source).is_file(), source


# --------------------------------------------------------------- the hard boundaries


def test_scout_holds_no_file_body_capability() -> None:
    """The privacy boundary, asserted as absence rather than as a promise.

    REQ-SCT-001 and `.claude/rules/privacy-boundary.md`: Scout is the one Phase-1 agent
    touching the repository, and it is API-routed, so a body-reading tool in its allowlist
    is a path from raw source to a network provider. Absent capability beats blocked
    capability (7.2b point 4).
    """
    tools = frontmatter("scout").get("tools", "")
    assert "read_scoped" not in tools
    assert not re.search(r"\bread\b", tools), f"scout must hold no read tool; got: {tools}"
    assert "bash" not in tools


def test_only_the_builder_may_read_file_bodies() -> None:
    """It runs on a local model, which is the entire reason it is allowed to."""
    assert "saltcode_read_scoped" in frontmatter("builder").get("tools", "")
    for name in set(AGENTS) - {"builder"}:
        assert "saltcode_read_scoped" not in frontmatter(name).get("tools", ""), name


def test_no_agent_holds_a_shell() -> None:
    """`bash` would route around every tool-level boundary at once."""
    for name in AGENTS:
        assert "bash" not in frontmatter(name).get("tools", ""), name


@pytest.mark.parametrize("name", AGENTS)
def test_each_definition_states_its_negative_scope_and_escalation(name: str) -> None:
    """7.2b points 2 and 6 — the two sections most likely to be dropped when adding an
    agent later, and the two that keep one agent from absorbing another's job."""
    text = body(name)
    assert "You do NOT do these things" in text, f"{name}: no negative-scope section"
    assert re.search(r"When you cannot (proceed|answer|decide)|## Escalation", text), (
        f"{name}: no escalation section"
    )


@pytest.mark.parametrize("name", AGENTS)
def test_each_definition_cites_the_requirements_it_satisfies(name: str) -> None:
    """7.2b point 9. A definition with no REQ trace cannot be reviewed against anything."""
    assert re.search(r"REQ-[A-Z]{3,4}-\d{3}", body(name)), f"{name}: no REQ ids cited"


# ------------------------------------------------------------------------- routing


ROUTING: dict[str, tuple[str, str | None]] = {
    # design §6: base model, and the static thinking level where one applies.
    "scout": ("deepseek/v4-flash", "off"),
    "architect": ("deepseek/v4-pro", "high"),
    "planner": ("deepseek/v4-flash", "off"),
    "test-intent": ("deepseek/v4-flash", "off"),
    "evaluator": ("deepseek/v4-flash", "off"),
    # The Builder's thinking is set per task (the VRAM triangle), so no static level.
    "builder": ("saltnitor/A_STD", None),
}


@pytest.mark.parametrize("name", AGENTS)
def test_model_and_thinking_match_design_section_6(name: str) -> None:
    model, thinking = ROUTING[name]
    fields = frontmatter(name)
    assert fields.get("model") == model
    if thinking is None:
        assert "thinking" not in fields, f"{name} must not pin a static thinking level"
    else:
        assert fields.get("thinking") == thinking


def test_the_json_emitting_agents_have_thinking_off() -> None:
    """REQ-EXT-003 / design §6: reasoning traces corrupt a JSON contract."""
    for name in ("scout", "planner", "test-intent", "evaluator"):
        assert frontmatter(name).get("thinking") == "off", name


def test_every_skill_under_skills_has_valid_frontmatter() -> None:
    """A skill Pi cannot parse is a skill that silently does not load."""
    for skill in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        match = _FRONTMATTER_RE.match(skill.read_text(encoding="utf-8"))
        assert match is not None, f"{skill.parent.name}: no frontmatter"
        assert re.search(r"^name:\s*\S+", match.group("body"), re.M), skill.parent.name
        assert re.search(r"^description:\s*\S+", match.group("body"), re.M), skill.parent.name


# ------------------------------------------------------- task 7.4: the three new skills


NEW_SKILLS: tuple[str, ...] = ("saltcode-lsp-usage", "saltcode-delegation", "saltcode-checkpoint-ops")
"""REQ-EXT-016's three extension-targeting skills, named in the requirement itself."""


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_the_three_new_skills_exist_and_are_loadable(name: str) -> None:
    """REQ-EXT-016 AC4: valid per Pi's Agent Skills standard."""
    skill = SKILLS_DIR / name / "SKILL.md"
    assert skill.is_file(), f"{name} is missing"
    match = _FRONTMATTER_RE.match(skill.read_text(encoding="utf-8"))
    assert match is not None
    assert re.search(rf"^name:\s*{re.escape(name)}$", match.group("body"), re.M)
    assert re.search(r"^description:\s*\S", match.group("body"), re.M)


def test_lsp_usage_is_preloaded_for_scout_and_builder_only() -> None:
    """REQ-EXT-016 AC2 names those two specifically.

    Asserted on the *rendered* definition rather than on frontmatter, because the
    requirement is that the skill is present in the agent's prompt — which is the thing
    that would silently not happen.
    """
    marker = "# Using the LSP/AST tools"
    for name in ("scout", "builder"):
        assert marker in body(name), f"{name} lacks the preloaded lsp-usage skill"
    for name in set(AGENTS) - {"scout", "builder"}:
        assert marker not in body(name), f"{name} carries lsp-usage but AC2 does not name it"


def test_an_agent_can_preload_more_than_one_skill() -> None:
    """Scout must carry both its own contract and lsp-usage, not one at the cost of the other."""
    text = body("scout")
    assert "# Saltcode Scout Skill" in text
    assert "# Using the LSP/AST tools" in text


def test_lsp_usage_states_the_scout_builder_asymmetry() -> None:
    """The two agents share the skill but not the rule, so the skill must say which is which.

    A shared skill that stated only the Builder's permission would read, to Scout, as
    licence to read bodies — the exact failure REQ-SCT-001 exists to prevent.
    """
    text = (SKILLS_DIR / "saltcode-lsp-usage" / "SKILL.md").read_text(encoding="utf-8")
    assert "Scout: you have no body-reading tool at all" in text
    assert "task.files_affected" in text
    assert "Never emit raw source" in text


def test_delegation_names_the_auditor_as_not_a_subagent() -> None:
    """The single most likely misreading of the roster (design §5.6a)."""
    text = (SKILLS_DIR / "saltcode-delegation" / "SKILL.md").read_text(encoding="utf-8")
    assert "The Auditor is not on this list" in text
    assert "saltcode_stability" in text


def test_checkpoint_ops_forbids_auto_fixing_an_out_of_scope_regression() -> None:
    """REQ-FAIL-004 / Trade B: the one place the correct action is the non-obvious one."""
    text = (SKILLS_DIR / "saltcode-checkpoint-ops" / "SKILL.md").read_text(encoding="utf-8")
    assert "FLAG HUMAN. Never auto-fix" in text
    assert "/rollback" in text and "/checkpoints" in text
    assert "auto_push" in text, "pushing must be stated as non-automatic"


# ------------------------------------------------------- task 7.1c: the shipped roster


DESIGN_17_SKILLS: frozenset[str] = frozenset({
    # the 8 agent skills
    "saltcode-scout", "saltcode-architect", "saltcode-planner", "saltcode-test-intent",
    "saltcode-evaluator", "saltcode-builder", "saltcode-auditor", "saltcode-compactor",
    # the 3 new extension-targeting skills (REQ-EXT-016)
    "saltcode-lsp-usage", "saltcode-delegation", "saltcode-checkpoint-ops",
    # the 6 community cookbooks
    "python-pro", "mcp-builder", "test-driven-development",
    "systematic-debugging", "agent-tool-builder", "git-pushing",
})
"""Design §17's shipped set, verbatim. Task 7's Done-when is "all 14 base skills + 3 new"."""


def test_skills_ships_exactly_design_section_17s_set() -> None:
    on_disk = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}
    assert on_disk == DESIGN_17_SKILLS, (
        f"only on disk: {sorted(on_disk - DESIGN_17_SKILLS)}; "
        f"only in design §17: {sorted(DESIGN_17_SKILLS - on_disk)}"
    )


def test_git_pushing_ships_without_its_script() -> None:
    """G-025: adopted for its prose; `smart_commit.sh` staged, committed and pushed
    unconditionally, against `auto_push` and the uncommitted-until-regression rule."""
    assert not (SKILLS_DIR / "git-pushing" / "scripts").exists()
    body_text = (SKILLS_DIR / "git-pushing" / "SKILL.md").read_text(encoding="utf-8")

    # What must not survive is an *invocation* — a dangling `bash …/smart_commit.sh` fails
    # at the point of use with no explanation, which is worse than shipping the script.
    # Naming the removed file in the adoption note is the opposite: it is what makes the
    # deviation from design §17 legible instead of mysterious.
    assert not re.search(r"^\s*(bash|sh)\s+\S*smart_commit\.sh", body_text, re.M), (
        "the skill still invokes the removed script"
    )
    assert "auto_push" in body_text


def test_the_builder_reads_its_spec_from_the_documented_location() -> None:
    """G-027. REQ-CON-004, design §7/§9 and `test_runner.py` all say `tests/`, not
    `.saltcode/tests/`. This is inlined into the Builder's live prompt, so a wrong path
    sends the agent to a directory that does not exist."""
    for path in (SKILLS_DIR / "saltcode-builder" / "SKILL.md", AGENTS_DIR / "builder.md"):
        text = path.read_text(encoding="utf-8")
        assert ".saltcode/tests/" not in text, path
        assert "tests/task_{id}_spec.*" in text, path
