"""Periodic spec compaction with deterministic constraint retention (task 12.1).

REQ-CMP-001: every 5 sprints, strip completed and resolved content from `design.md`
while **never stripping any line from the `## HARD CONSTRAINTS` block**. Constraints
leave only by explicit human edit.

**Why the block is spliced rather than merely avoided.** The obvious implementation is
"be careful not to touch the constraints", which is a property of the stripping logic
and therefore only as good as that logic — and under REQ-CMP-001 AC2 the compaction may
be proposed by a *model* (Flash, thinking off), which can paraphrase, re-order or drop a
bullet while looking entirely plausible. So this module does not trust the rewrite at
all: it records the constraints block's exact byte span up front, compacts everything
else, splices the original bytes back in, and then **re-reads its own output and refuses
to return if the block is not byte-identical**. AC1 becomes a post-condition that holds
by construction, not an outcome to be hoped for.

The cost of getting this wrong is not a lost line of text. Design §11.7: a stripped
constraint fails the Evaluator's preservation check (REQ-EVL-001 AC1), which routes to
the Architect, which re-emits the constraint, which the next compaction strips again —
a compactor→Evaluator→Architect oscillation that burns a Pro-tier API call every lap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

COMPACTION_INTERVAL = 5
"""REQ-CMP-001: once per 5 sprints."""

HARD_CONSTRAINTS_HEADING = re.compile(r"^##\s+HARD\s+CONSTRAINTS\s*$", re.IGNORECASE)
ANY_HEADING = re.compile(r"^#{1,6}\s+\S")

OBSOLETE_HEADING_MARKERS: tuple[str, ...] = (
    "completed",
    "resolved",
    "obsolete",
    "done",
    "superseded",
    "historical",
    "changelog",
)
"""Section headings whose content design §11.7 names as compactable — "task
descriptions, resolved discussions, obsolete notes". Matched on the heading text so
compaction is structural and reviewable, never a judgement about prose."""


class SpecCompactionError(RuntimeError):
    """The compaction would have altered the HARD CONSTRAINTS block."""


@dataclass(frozen=True)
class ConstraintsBlock:
    """The exact byte span of the `## HARD CONSTRAINTS` section."""

    start: int
    end: int
    text: str

    @property
    def present(self) -> bool:
        return self.start >= 0


@dataclass
class CompactionResult:
    """A compaction that has already been proven constraint-preserving."""

    content: str
    original_length: int
    compacted_length: int
    sections_removed: list[str] = field(default_factory=list[str])
    constraints_preserved: bool = True
    detail: str = ""

    @property
    def bytes_saved(self) -> int:
        return self.original_length - self.compacted_length

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_length": self.original_length,
            "compacted_length": self.compacted_length,
            "bytes_saved": self.bytes_saved,
            "sections_removed": self.sections_removed,
            "constraints_preserved": self.constraints_preserved,
            "detail": self.detail,
        }


def should_compact(sprint_count: int, interval: int = COMPACTION_INTERVAL) -> bool:
    """Whether this sprint is a compaction sprint (REQ-CMP-001: once per 5).

    Sprint 0 does not compact — there is nothing yet to compact, and firing on the
    first sprint of a fresh project would run the Flash call for no benefit.
    """
    if sprint_count <= 0 or interval <= 0:
        return False
    return sprint_count % interval == 0


def locate_constraints_block(content: str) -> ConstraintsBlock:
    """Find the `## HARD CONSTRAINTS` block's exact character span.

    The block runs from its heading to the next heading of any level, or to end of
    file. Returns a block with ``start == -1`` when the document has no such section —
    which callers must treat as a refusal to compact, not as "nothing to preserve".
    """
    lines = content.splitlines(keepends=True)
    offset = 0
    start = -1
    end = len(content)

    for line in lines:
        if start < 0:
            if HARD_CONSTRAINTS_HEADING.match(line.strip()):
                start = offset
        elif ANY_HEADING.match(line.lstrip()):
            end = offset
            break
        offset += len(line)

    if start < 0:
        return ConstraintsBlock(start=-1, end=-1, text="")
    return ConstraintsBlock(start=start, end=end, text=content[start:end])


OBSOLETE_MARKER_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(m) for m in OBSOLETE_HEADING_MARKERS) + r")\b"
)
"""Whole-word matching, because several markers are substrings of their own negations.

A plain `marker in heading` test strips exactly the sections that must survive:
`## Unresolved Issues` contains "resolved", `## Undone Items` contains "done", and
`## Uncompleted Work` contains "completed" — all three read as obsolete and were
removed along with their subsections. In a living design document "Unresolved
Issues" is the last thing a compactor should delete."""


def _is_obsolete_heading(line: str) -> bool:
    stripped = line.strip().lstrip("#").strip().lower()
    if not stripped:
        return False
    return OBSOLETE_MARKER_PATTERN.search(stripped) is not None


def _heading_level(line: str) -> int:
    match = re.match(r"^(#{1,6})\s", line.lstrip())
    return len(match.group(1)) if match else 0


def strip_obsolete_sections(content: str) -> tuple[str, list[str]]:
    """Remove sections whose heading marks them completed/resolved/obsolete.

    A section ends at the next heading of the same or shallower level, so removing a
    `## Completed` section takes its `###` subsections with it and stops at the next
    `##`. Operates on text the caller has already guaranteed excludes the constraints
    block.
    """
    lines = content.splitlines(keepends=True)
    kept: list[str] = []
    removed: list[str] = []
    skip_until_level = 0

    for line in lines:
        level = _heading_level(line)

        if skip_until_level:
            # A heading at the same or shallower level ends the skipped section.
            if level and level <= skip_until_level:
                skip_until_level = 0
            else:
                continue

        if level and _is_obsolete_heading(line):
            removed.append(line.strip().lstrip("#").strip())
            skip_until_level = level
            continue

        kept.append(line)

    return "".join(kept), removed


def compact_spec(content: str, proposed_body: str | None = None) -> CompactionResult:
    """Compact a design document, preserving `## HARD CONSTRAINTS` byte for byte.

    Args:
        content: The current `design.md`.
        proposed_body: An optional already-compacted document — the Flash-produced
            rewrite of REQ-CMP-001 AC2. It is treated as *untrusted*: whatever it did
            to the constraints block is discarded and the original bytes are spliced
            back in. Passing ``None`` uses the deterministic structural strip below.

    Returns:
        A :class:`CompactionResult` whose content is proven constraint-preserving.

    Raises:
        SpecCompactionError: The document has no `## HARD CONSTRAINTS` block, or the
            splice failed its own byte-identity check. Both refuse to produce output
            rather than emit a document that may have lost a constraint.
    """
    original = locate_constraints_block(content)
    if not original.present:
        raise SpecCompactionError(
            "the document has no '## HARD CONSTRAINTS' block, so compaction is refused: "
            "there is nothing to guarantee preservation of, and writing anyway would "
            "silently produce a spec the Evaluator's preservation check must fail "
            "(REQ-CMP-001, REQ-EVL-001)"
        )

    if proposed_body is not None:
        # The model's version of the constraints block is discarded wholesale — a
        # paraphrased constraint is a lost constraint, and it would look correct.
        proposed = locate_constraints_block(proposed_body)
        if proposed.present:
            body = proposed_body[: proposed.start] + original.text + proposed_body[proposed.end :]
        else:
            body = proposed_body.rstrip() + "\n\n" + original.text
        removed: list[str] = []
    else:
        before = content[: original.start]
        after = content[original.end :]
        stripped_before, removed_before = strip_obsolete_sections(before)
        stripped_after, removed_after = strip_obsolete_sections(after)
        body = stripped_before + original.text + stripped_after
        removed = removed_before + removed_after

    # The post-condition, checked against our own output rather than assumed.
    final = locate_constraints_block(body)
    if not final.present or final.text != original.text:
        raise SpecCompactionError(
            "compaction would have altered the '## HARD CONSTRAINTS' block, so the "
            "result is discarded. REQ-CMP-001 AC1 requires it byte-identical; "
            "constraints are removed only by an explicit human edit."
        )

    return CompactionResult(
        content=body,
        original_length=len(content),
        compacted_length=len(body),
        sections_removed=removed,
        constraints_preserved=True,
        detail=(
            f"removed {len(removed)} obsolete section(s); "
            f"HARD CONSTRAINTS preserved byte-identically ({len(original.text)} chars)"
        ),
    )
