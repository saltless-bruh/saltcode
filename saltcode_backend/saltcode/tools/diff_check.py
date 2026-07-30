"""``saltcode.tools.diff_check`` — unified-diff validation entrypoint.

Bridged into Pi as ``saltcode_diff_check`` (design §5.3) and run as the **first**
gate of the Phase-2 loop, before any sandbox is created (design §10).

Satisfies REQ-STAT-005: one bounded repair (extracting the diff from a markdown
fence), then a structural parse, then ``git apply --check``. Anything that fails
is reported as ``impl_fail`` — it counts against the shared per-task budget and
the Auditor is never invoked on it.

Usage::

    python -m saltcode.tools.diff_check --in builder_output.txt --repo .
    cat diff.patch | python -m saltcode.tools.diff_check --repo /path/to/tree

Exit codes follow :mod:`saltcode.tools._cli`: ``0`` valid diff, ``1``
``impl_fail``, ``2`` usage error, ``3`` I/O or internal error.

``git apply --check`` is reported as ``skipped`` — never silently as a pass —
when git is unavailable or the target is not a repository, so a check that did
not run stays visible to the caller.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, Any

from saltcode.diffs.diff_validator import (
    diff_needed_repair,
    git_apply_check,
    validate_diff,
)
from saltcode.tools._cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_VERDICT_NEGATIVE,
    STDIN_SENTINEL,
    emit,
    exit_code_for,
    fail,
    read_payload,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

TOOL = "diff_check"
IMPL_FAIL = "impl_fail"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.diff_check",
        description="Validate that Builder output is an appliable unified diff.",
    )
    parser.add_argument(
        "--in",
        dest="source",
        default=STDIN_SENTINEL,
        help="Path to the Builder output, or '-' for stdin (default: stdin).",
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Working tree to run `git apply --check` against (default: cwd).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write the extracted, validated diff here. Nothing is written on failure.",
    )
    parser.add_argument(
        "--no-git-check",
        action="store_true",
        help="Structural validation only; do not run `git apply --check`.",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # `--help` exits 0; only a real parse error is a usage error.
        return exit_code_for(exc)

    try:
        payload = read_payload(args.source)
    except OSError as exc:
        return fail(TOOL, "InputError", f"Could not read payload from {args.source!r}: {exc}")

    # REQ-STAT-005 AC2: exactly one bounded repair — unwrap a markdown fence,
    # drop leading prose, terminate the final line.
    repaired = diff_needed_repair(payload)

    is_valid, diff_text = validate_diff(payload)
    if not is_valid or diff_text is None:
        emit(
            {
                "tool": TOOL,
                "ok": False,
                "verdict": IMPL_FAIL,
                "repaired": repaired,
                "git_apply_check": "not_attempted",
                "detail": (
                    "Output is not a parseable unified diff "
                    "(expected '--- a/', '+++ b/' and at least one '@@' hunk)."
                ),
            }
        )
        return EXIT_VERDICT_NEGATIVE

    git_status = "skipped"
    git_detail = "disabled via --no-git-check"
    if not args.no_git_check:
        check = git_apply_check(diff_text, args.repo)
        git_status, git_detail = check.status, check.detail

        if check.status == "fail":
            emit(
                {
                    "tool": TOOL,
                    "ok": False,
                    "verdict": IMPL_FAIL,
                    "repaired": repaired,
                    "git_apply_check": "fail",
                    "detail": f"git apply --check rejected the diff: {check.detail}",
                }
            )
            return EXIT_VERDICT_NEGATIVE

    written_to: str | None = None
    if args.out is not None:
        try:
            from pathlib import Path

            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(diff_text, encoding="utf-8")
        except OSError as exc:
            return fail(TOOL, "WriteError", f"Valid diff, but could not write {args.out!r}: {exc}")
        written_to = args.out

    result: dict[str, Any] = {
        "tool": TOOL,
        "ok": True,
        "verdict": "valid",
        "repaired": repaired,
        "git_apply_check": git_status,
        "detail": git_detail,
        "written": written_to is not None,
    }
    if written_to is not None:
        result["path"] = written_to
    emit(result)
    return EXIT_OK


def main() -> None:
    try:
        sys.exit(run())
    except Exception as exc:  # noqa: BLE001 - the entrypoint must never leak a traceback
        sys.exit(fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR))


if __name__ == "__main__":
    main()
