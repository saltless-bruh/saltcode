"""Task 12 — the Spec Compactor (REQ-CMP-001).

The whole task reduces to one property: after compaction the `## HARD CONSTRAINTS`
block is byte-identical (AC1). These tests attack that property directly, including
with a deliberately hostile "model-produced" rewrite, because the realistic failure is
not a crash — it is a paraphrased constraint that reads perfectly and is gone.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from saltcode.contracts.design_doc import extract_hard_constraints_from_markdown
from saltcode.contracts.spec_compactor import (
    COMPACTION_INTERVAL,
    SpecCompactionError,
    compact_spec,
    locate_constraints_block,
    should_compact,
    strip_obsolete_sections,
)
from saltcode.tools._cli import EXIT_OK, EXIT_USAGE, EXIT_VERDICT_NEGATIVE

REPO_ROOT = Path(__file__).resolve().parents[1]

DESIGN = """# Design

## Overview
The system does a thing.

## Completed Work
This sprint we shipped the login page.
Nobody needs this text any more.

### Resolved discussion about colours
We argued about blue. We chose blue.

## HARD CONSTRAINTS
- Never log PII
- All money is integer cents
- Do not add a network call to the parser

## Open Questions
What about rate limiting?
"""


def constraints_text(document: str) -> str:
    block = locate_constraints_block(document)
    assert block.present
    return block.text


# ------------------------------------------------------------------ cadence


@pytest.mark.parametrize("sprint", [5, 10, 15, 100])
def test_compaction_is_due_every_fifth_sprint(sprint: int) -> None:
    assert should_compact(sprint)


@pytest.mark.parametrize("sprint", [1, 2, 3, 4, 6, 9, 11])
def test_compaction_is_not_due_otherwise(sprint: int) -> None:
    assert not should_compact(sprint)


def test_sprint_zero_never_compacts() -> None:
    """A fresh project has nothing to compact; firing would spend a Flash call for nothing."""
    assert not should_compact(0)
    assert not should_compact(-5)


def test_the_interval_is_the_documented_five() -> None:
    assert COMPACTION_INTERVAL == 5


# ------------------------------------------------------ locating the block


def test_the_block_runs_to_the_next_heading() -> None:
    block = locate_constraints_block(DESIGN)
    assert block.present
    assert "Never log PII" in block.text
    assert "Open Questions" not in block.text


def test_a_document_without_the_block_is_reported_absent() -> None:
    assert not locate_constraints_block("# Design\n\nNo constraints here.\n").present


def test_a_block_at_end_of_file_runs_to_the_end() -> None:
    document = "# D\n\n## HARD CONSTRAINTS\n- Only this\n"
    block = locate_constraints_block(document)
    assert block.present
    assert "Only this" in block.text


# --------------------------------------------------- AC1: byte-identical block


def test_the_constraints_block_survives_compaction_byte_for_byte() -> None:
    """REQ-CMP-001 AC1 — the requirement, stated as an equality."""
    before = constraints_text(DESIGN)
    result = compact_spec(DESIGN)
    assert constraints_text(result.content) == before
    assert result.constraints_preserved


def test_the_constraint_strings_are_unchanged_as_a_set() -> None:
    """Belt and braces: the Evaluator's preservation check reads them as a set."""
    before = extract_hard_constraints_from_markdown(DESIGN)
    after = extract_hard_constraints_from_markdown(compact_spec(DESIGN).content)
    assert after == before


def test_obsolete_content_outside_the_block_is_stripped() -> None:
    """REQ-CMP-001 AC3."""
    result = compact_spec(DESIGN)
    assert "Nobody needs this text any more" not in result.content
    assert "We argued about blue" not in result.content
    assert result.bytes_saved > 0
    assert any("Completed" in s for s in result.sections_removed)


def test_live_content_outside_the_block_is_kept() -> None:
    result = compact_spec(DESIGN)
    assert "The system does a thing." in result.content
    assert "What about rate limiting?" in result.content


def test_a_document_with_no_constraints_block_is_refused() -> None:
    """Refusing beats writing a spec the Evaluator's preservation check must fail."""
    with pytest.raises(SpecCompactionError) as excinfo:
        compact_spec("# Design\n\n## Completed\nold stuff\n")
    assert "HARD CONSTRAINTS" in str(excinfo.value)


def test_compaction_is_idempotent() -> None:
    once = compact_spec(DESIGN).content
    twice = compact_spec(once).content
    assert twice == once


# ------------------------------------- a hostile model rewrite cannot win


def test_a_model_that_paraphrases_a_constraint_is_overruled() -> None:
    """REQ-CMP-001 AC2's Flash rewrite is untrusted.

    This is the realistic failure: not a crash, but a plausible paraphrase. "All money
    is integer cents" becoming "Use cents for money" reads fine and is a different
    constraint — and the Evaluator would then fail preservation forever.
    """
    hostile = DESIGN.replace("- All money is integer cents", "- Use cents for money")
    result = compact_spec(DESIGN, proposed_body=hostile)

    assert constraints_text(result.content) == constraints_text(DESIGN)
    assert "- All money is integer cents" in result.content
    assert "- Use cents for money" not in result.content


def test_a_model_that_drops_a_constraint_is_overruled() -> None:
    hostile = DESIGN.replace("- Do not add a network call to the parser\n", "")
    result = compact_spec(DESIGN, proposed_body=hostile)
    assert extract_hard_constraints_from_markdown(result.content) == (
        extract_hard_constraints_from_markdown(DESIGN)
    )


def test_a_model_that_deletes_the_whole_block_is_overruled() -> None:
    """The block is re-appended rather than lost, and the result still validates."""
    hostile = "# Design\n\n## Overview\nTrimmed.\n"
    result = compact_spec(DESIGN, proposed_body=hostile)
    assert constraints_text(result.content) == constraints_text(DESIGN)


def test_a_model_that_reorders_constraints_is_overruled() -> None:
    """Byte-identity, not set-equality: AC1 says the block, not just its contents."""
    reordered = DESIGN.replace(
        "- Never log PII\n- All money is integer cents\n",
        "- All money is integer cents\n- Never log PII\n",
    )
    result = compact_spec(DESIGN, proposed_body=reordered)
    assert constraints_text(result.content) == constraints_text(DESIGN)


def test_a_model_rewrite_of_other_sections_is_accepted() -> None:
    """The guard is on the constraints only; the model is free everywhere else."""
    proposed = DESIGN.replace("The system does a thing.", "Summary: it does a thing.")
    result = compact_spec(DESIGN, proposed_body=proposed)
    assert "Summary: it does a thing." in result.content
    assert constraints_text(result.content) == constraints_text(DESIGN)


# ------------------------------------------------------------ section stripping


def test_stripping_takes_subsections_with_the_parent() -> None:
    text = "## Completed\ndone\n### Detail\nalso done\n## Live\nkeep\n"
    kept, removed = strip_obsolete_sections(text)
    assert "done" not in kept
    assert "also done" not in kept
    assert "keep" in kept
    assert removed == ["Completed"]


def test_stripping_stops_at_a_sibling_heading() -> None:
    text = "## Resolved\ngone\n## Active\nkept\n"
    kept, _ = strip_obsolete_sections(text)
    assert "gone" not in kept
    assert "kept" in kept


def test_nothing_is_stripped_from_a_document_of_live_sections() -> None:
    text = "## Overview\na\n## Design\nb\n"
    kept, removed = strip_obsolete_sections(text)
    assert kept == text
    assert removed == []


@pytest.mark.parametrize(
    "heading",
    [
        "## Unresolved Issues",
        "## Undone Items",
        "## Uncompleted Work",
        "## Abandoned Approaches",
        "## Redone Migration",
        "## Incomplete Rollout",
    ],
)
def test_a_negated_marker_is_not_an_obsolete_section(heading: str) -> None:
    """Substring matching strips the sections that most need to survive.

    "Unresolved" contains "resolved", "Undone" contains "done", "Uncompleted" contains
    "completed" — every one of these read as obsolete and was deleted along with its
    subsections. In a living design document, unresolved issues are the last thing a
    compactor should throw away.
    """
    text = f"{heading}\nthis must survive\n\n## Overview\nlive\n"
    kept, removed = strip_obsolete_sections(text)
    assert kept == text
    assert removed == []


@pytest.mark.parametrize(
    "heading",
    [
        "## Completed Work",
        "## Resolved Discussions",
        "## Obsolete Notes",
        "## Done",
        "## Superseded Design",
        "## Historical Context",
        "## Changelog",
    ],
)
def test_a_genuine_marker_is_still_stripped(heading: str) -> None:
    """The negation fix must not have blunted the markers it was protecting."""
    text = f"{heading}\nold\n\n## Overview\nlive\n"
    kept, removed = strip_obsolete_sections(text)
    assert "old" not in kept
    assert "live" in kept
    assert removed == [heading.lstrip("#").strip()]


def test_unresolved_issues_survive_a_full_compaction() -> None:
    """The end-to-end version of the same property, through `compact_spec`."""
    document = DESIGN.replace("## Open Questions", "## Unresolved Issues")
    result = compact_spec(document)
    assert "What about rate limiting?" in result.content
    assert "Unresolved Issues" in result.content


# --------------------------------------------------------------- entrypoint


def run_tool(*args: str) -> tuple[int, dict[str, object]]:
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.compact_spec", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


def write_design(tmp_path: Path, content: str = DESIGN) -> Path:
    path = tmp_path / "design.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_entrypoint_compacts_and_preserves_the_block(tmp_path: Path) -> None:
    design = write_design(tmp_path)
    before = constraints_text(design.read_text(encoding="utf-8"))

    code, payload = run_tool("--design", str(design), "--sprint", "5")

    assert code == EXIT_OK
    assert payload["verdict"] == "compacted"
    assert payload["written"] is True
    assert payload["constraints_preserved"] is True
    assert constraints_text(design.read_text(encoding="utf-8")) == before


def test_entrypoint_does_nothing_when_not_due(tmp_path: Path) -> None:
    design = write_design(tmp_path)
    original = design.read_bytes()

    code, payload = run_tool("--design", str(design), "--sprint", "3")

    assert code == EXIT_OK
    assert payload["verdict"] == "not_due"
    assert design.read_bytes() == original


def test_entrypoint_dry_run_writes_nothing(tmp_path: Path) -> None:
    """REQ-SEC-004 AC2 — nothing outside .saltcode/ is modified."""
    design = write_design(tmp_path)
    original = design.read_bytes()

    code, payload = run_tool("--design", str(design), "--sprint", "5", "--dry-run")

    assert code == EXIT_OK
    assert payload["written"] is False
    assert payload["bytes_saved"] > 0
    assert design.read_bytes() == original


def test_entrypoint_refuses_a_document_without_constraints(tmp_path: Path) -> None:
    design = write_design(tmp_path, "# Design\n\n## Completed\nold\n")
    original = design.read_bytes()

    code, payload = run_tool("--design", str(design), "--sprint", "5")

    assert code == EXIT_VERDICT_NEGATIVE
    assert payload["verdict"] == "refused"
    assert payload["written"] is False
    assert design.read_bytes() == original, "a refusal must leave the file untouched"


def test_entrypoint_overrules_a_hostile_proposed_body(tmp_path: Path) -> None:
    design = write_design(tmp_path)
    proposed = tmp_path / "proposed.md"
    proposed.write_text(DESIGN.replace("- Never log PII", "- Try not to log PII"), encoding="utf-8")

    code, _ = run_tool("--design", str(design), "--sprint", "5", "--proposed", str(proposed))

    assert code == EXIT_OK
    final = design.read_text(encoding="utf-8")
    assert "- Never log PII" in final
    assert "- Try not to log PII" not in final


def test_entrypoint_rejects_a_missing_design(tmp_path: Path) -> None:
    code, payload = run_tool("--design", str(tmp_path / "nope.md"), "--sprint", "5")
    assert code == EXIT_USAGE
    assert payload["ok"] is False


def test_omitting_the_sprint_forces_compaction(tmp_path: Path) -> None:
    """`--sprint` is optional and documented as "omit to force" — prove it forces.

    Without this the cadence check is the only path anyone exercises, and the manual
    `/compact` route the extension needs (design §11.7) would be untested.
    """
    design = write_design(tmp_path)
    before = constraints_text(design.read_text(encoding="utf-8"))

    code, payload = run_tool("--design", str(design))

    assert code == EXIT_OK
    assert payload["verdict"] == "compacted"
    assert payload["written"] is True
    assert "Completed Work" in payload["sections_removed"]
    assert constraints_text(design.read_text(encoding="utf-8")) == before


def test_a_no_op_compaction_reports_no_change_and_writes_nothing(tmp_path: Path) -> None:
    """`verdict` and `written` must agree.

    Deriving the verdict from `bytes_saved` let a rewrite that changed content without
    changing length report "no_change" next to `written: true` — a self-contradictory
    payload for whatever automation reads this JSON.
    """
    design = write_design(tmp_path, "# Design\n\n## Overview\nlive\n\n## HARD CONSTRAINTS\n- Never log PII\n")
    original = design.read_bytes()

    code, payload = run_tool("--design", str(design))

    assert code == EXIT_OK
    assert payload["verdict"] == "no_change"
    assert payload["written"] is False
    assert design.read_bytes() == original


def test_the_write_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    """The atomic write renames a sibling temp file over the target."""
    design = write_design(tmp_path)

    code, _ = run_tool("--design", str(design), "--sprint", "5")

    assert code == EXIT_OK
    assert [p.name for p in tmp_path.iterdir()] == ["design.md"]


def test_help_exits_zero(tmp_path: Path) -> None:
    """`--help` is not a usage error; argparse exits 0 and the caller must pass it on."""
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.compact_spec", "--help"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
        check=False,
    )
    assert completed.returncode == EXIT_OK
    assert "--design" in completed.stdout
