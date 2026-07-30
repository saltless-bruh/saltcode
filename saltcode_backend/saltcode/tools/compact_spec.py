"""``saltcode.tools.compact_spec`` — the periodic Spec Compactor (task 12.1).

Bridged into Pi as `saltcode_compact_spec` and fired once per 5 sprints (design §11.7).
Strips completed and resolved content from `design.md` while leaving the
`## HARD CONSTRAINTS` block byte-identical (REQ-CMP-001 AC1).

Usage::

    python -m saltcode.tools.compact_spec --design .saltcode/design.md --sprint 5
    python -m saltcode.tools.compact_spec --design … --sprint 5 --proposed compacted.md
    python -m saltcode.tools.compact_spec --design … --sprint 5 --dry-run

``--proposed`` accepts a model-produced rewrite (REQ-CMP-001 AC2 puts that on Flash with
thinking off; the extension chooses the model, this entrypoint only rewrites the file).
That rewrite is treated as **untrusted**: whatever it did to the constraints block is
discarded and the original bytes are spliced back in, then verified. A paraphrased
constraint is a lost constraint and would read as correct.

Exit codes: ``0`` compacted (or a legitimate no-op), ``1`` compaction refused because it
would have altered the constraints block, ``2`` usage, ``3`` internal. The refusal is a
*verdict*, not a crash — the file is left untouched and the caller can flag a human.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from saltcode.contracts.spec_compactor import (
    COMPACTION_INTERVAL,
    SpecCompactionError,
    compact_spec,
    locate_constraints_block,
    should_compact,
)
from saltcode.tools._cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    EXIT_VERDICT_NEGATIVE,
    emit,
    exit_code_for,
    fail,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

TOOL = "compact_spec"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.compact_spec",
        description="Compact design.md, preserving the HARD CONSTRAINTS block byte for byte.",
    )
    parser.add_argument("--design", required=True, help="Path to design.md.")
    parser.add_argument(
        "--sprint",
        type=int,
        default=None,
        help=f"Sprint number; compaction fires every {COMPACTION_INTERVAL}. Omit to force.",
    )
    parser.add_argument(
        "--proposed",
        default=None,
        help="A model-produced compacted document. Its constraints block is discarded.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing (REQ-SEC-004).",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # `--help` exits 0; only a real parse error is a usage error.
        return exit_code_for(exc)

    design_path = Path(args.design)
    if not design_path.is_file():
        return fail(TOOL, "InputError", f"--design is not a file: {args.design}", code=EXIT_USAGE)

    try:
        content = design_path.read_text(encoding="utf-8")
    except OSError as exc:
        return fail(TOOL, "IOError", f"could not read {args.design}: {exc}", code=EXIT_ERROR)

    if args.sprint is not None and not should_compact(args.sprint):
        emit(
            {
                "tool": TOOL,
                "ok": True,
                "verdict": "not_due",
                "sprint": args.sprint,
                "interval": COMPACTION_INTERVAL,
                "detail": (
                    f"sprint {args.sprint} is not a multiple of {COMPACTION_INTERVAL}; "
                    "no compaction is due (REQ-CMP-001)"
                ),
            }
        )
        return EXIT_OK

    proposed_body: str | None = None
    if args.proposed is not None:
        proposed_path = Path(args.proposed)
        if not proposed_path.is_file():
            return fail(TOOL, "InputError", f"--proposed is not a file: {args.proposed}", code=EXIT_USAGE)
        try:
            proposed_body = proposed_path.read_text(encoding="utf-8")
        except OSError as exc:
            return fail(TOOL, "IOError", f"could not read {args.proposed}: {exc}", code=EXIT_ERROR)

    before = locate_constraints_block(content)

    try:
        result = compact_spec(content, proposed_body)
    except SpecCompactionError as exc:
        # A refusal leaves the file untouched. This is the outcome REQ-CMP-001 exists
        # to produce when preservation cannot be guaranteed.
        emit(
            {
                "tool": TOOL,
                "ok": False,
                "verdict": "refused",
                "constraints_preserved": False,
                "detail": str(exc),
                "written": False,
            }
        )
        return EXIT_VERDICT_NEGATIVE

    changed = result.content != content
    written = False
    if not args.dry_run and changed:
        # Atomic: write a sibling temp file and rename over the target. A truncating
        # in-place write that dies partway (crash, disk full) leaves design.md
        # corrupted — potentially with a half-written `## HARD CONSTRAINTS` block,
        # which is the single outcome this module exists to make impossible. Same
        # pattern as `contracts/io.py::save_contract`.
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", dir=design_path.parent, delete=False, encoding="utf-8"
            ) as handle:
                temp_name = handle.name
                handle.write(result.content)
            Path(temp_name).replace(design_path)
            written = True
        except OSError as exc:
            # `temp_name` stays None when the failure was opening the temp file itself
            # (a read-only directory), so the cleanup must not assume it was assigned.
            if temp_name is not None:
                with contextlib.suppress(OSError):
                    Path(temp_name).unlink()
            return fail(TOOL, "IOError", f"could not write {args.design}: {exc}", code=EXIT_ERROR)

    payload: dict[str, Any] = {
        "tool": TOOL,
        "ok": True,
        # Derived from the same condition as `written`. Deriving it from
        # `bytes_saved` (a length delta) let a same-length rewrite report
        # "no_change" while `written` was True — a self-contradictory payload for
        # whatever automation consumes this JSON.
        "verdict": "compacted" if changed else "no_change",
        "written": written,
        "dry_run": bool(args.dry_run),
        "constraints_chars": len(before.text),
        **result.to_dict(),
    }
    emit(payload)
    return EXIT_OK


def main() -> None:
    try:
        sys.exit(run())
    except Exception as exc:  # noqa: BLE001 - the entrypoint must never leak a traceback
        sys.exit(fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR))


if __name__ == "__main__":
    main()
