"""``saltcode.tools.apply_live`` — land an Auditor-passed diff on the live tree (task 10.3).

Bridged into Pi as `saltcode_apply_live`. This is step 6 of the Phase-2 pipeline: the
diff has cleared the diff-format check, the sandbox apply, the static gate, the test
runner and the Auditor, and now lands on the repository the human is working in.

**It leaves the tree uncommitted, on purpose.** Design §10.1 puts the commit after the
regression gate, so `saltcode_regression` runs on the integrated tree next and only a
green full suite earns the checkpoint. The JSON says `committed: false` explicitly rather
than leaving it to be inferred, because a caller that assumed otherwise would skip the
gate that catches cross-task breakage.

Usage::

    python -m saltcode.tools.apply_live --repo . --in diff.patch
    cat diff.patch | python -m saltcode.tools.apply_live --repo . --in -

Exit codes: ``0`` applied; ``1`` refused or failed — both *verdicts* the loop routes on
(a `tests/**` write is REQ-BLD-003 working, not a crash); ``2`` usage; ``3`` internal.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from saltcode.diffs.apply import LiveApplyError, apply_live
from saltcode.diffs.diff_validator import extract_diff_from_fences
from saltcode.tools._cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    EXIT_VERDICT_NEGATIVE,
    emit,
    exit_code_for,
    fail,
    read_payload,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

TOOL = "apply_live"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.apply_live",
        description="Apply a passed unified diff to the live working tree, leaving it uncommitted.",
    )
    parser.add_argument("--repo", default=".", help="The live working tree (default: cwd).")
    parser.add_argument(
        "--in",
        dest="input",
        required=True,
        help="Path to the unified diff, or '-' for stdin.",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exit_code_for(exc)

    repo = Path(args.repo)
    if not repo.is_dir():
        return fail(TOOL, "InputError", f"--repo is not a directory: {args.repo}", code=EXIT_USAGE)

    try:
        raw = read_payload(args.input)
    except OSError as exc:
        return fail(TOOL, "IOError", f"could not read {args.input}: {exc}", code=EXIT_USAGE)

    # The same normalisation `diff_check` applies, so a diff that passed the gate is
    # byte-identical here. Not a second repair: REQ-STAT-005 AC2 allows exactly one, and
    # it was already spent upstream — this only keeps the two paths from disagreeing
    # about what the validated diff *was*.
    diff_text = extract_diff_from_fences(raw)
    if not diff_text.strip():
        return fail(TOOL, "InputError", "the diff is empty", code=EXIT_USAGE)

    try:
        result = apply_live(repo, diff_text)
    except LiveApplyError as exc:
        return fail(TOOL, "LiveApplyError", str(exc), code=EXIT_ERROR)

    payload: dict[str, Any] = {"tool": TOOL, "ok": result.ok, **result.to_dict()}
    emit(payload)
    return EXIT_OK if result.ok else EXIT_VERDICT_NEGATIVE


def main() -> None:
    try:
        sys.exit(run())
    except Exception as exc:  # noqa: BLE001 - the entrypoint must never leak a traceback
        sys.exit(fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR))


if __name__ == "__main__":
    main()
