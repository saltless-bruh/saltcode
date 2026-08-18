"""``saltcode.tools.rollback`` — return the tree to a named checkpoint (task 17.3).

Bridged into Pi as `saltcode_rollback` and the backend half of `/rollback last` /
`/rollback <task_id>` (REQ-CKP-009). Resets the working tree to a checkpoint's commit,
rewinds the checkpoint ledger so resume (REQ-CKP-008) agrees with the tree again, and
records the whole action in `.saltcode/audit_log.jsonl` (AC1).

Usage::

    python -m saltcode.tools.rollback --repo .                    # 'last'
    python -m saltcode.tools.rollback --repo . --to T3 --sprint S1
    python -m saltcode.tools.rollback --repo . --to T3 --force

**Every payload carries the loss warning** (AC2), refusal and success alike, because a
caller reading only the success case is exactly the caller who needs it.

`--force` proceeds despite tracked, uncommitted changes outside `.saltcode/`. That is the
loop's normal path after a failed regression — the extension knows the dirty tree is the
uncommitted `apply_live` it is meant to discard (REQ-CKP-002 AC2) and the backend cannot
tell that from a human's editing, so the caller who knows says so.

Exit codes: ``0`` reset done; ``1`` refused — no such checkpoint, a dirty tree without
``--force``, or a target that is not an ancestor of HEAD; ``2`` usage; ``3`` internal.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from saltcode.checkpoint.checkpoint import CheckpointError
from saltcode.checkpoint.rollback import rollback
from saltcode.tools._cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    EXIT_VERDICT_NEGATIVE,
    emit,
    fail,
    parse_cli,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

TOOL = "rollback"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.rollback",
        description="Reset the live tree to a checkpoint, rewind the ledger, and log it.",
    )
    parser.add_argument("--repo", default=".", help="The live working tree (default: cwd).")
    parser.add_argument(
        "--to",
        default="last",
        help="'last' (default) or a task id. A task id resolves to its newest checkpoint.",
    )
    parser.add_argument("--sprint", default=None, help="Restrict the search to one sprint id.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Proceed despite uncommitted tracked changes outside .saltcode/.",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args, code = parse_cli(parser, argv, TOOL)
    if args is None:
        return code

    repo = Path(args.repo)
    if not repo.is_dir():
        return fail(TOOL, "InputError", f"--repo is not a directory: {args.repo}", code=EXIT_USAGE)

    try:
        result = rollback(repo, args.to, sprint_id=args.sprint, force=args.force)
    except CheckpointError as exc:
        return fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR)

    emit({"tool": TOOL, "selector": args.to, **result.to_dict()})
    return EXIT_OK if result.ok else EXIT_VERDICT_NEGATIVE


def main() -> None:
    try:
        sys.exit(run())
    except Exception as exc:  # noqa: BLE001 - the entrypoint must never leak a traceback
        sys.exit(fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR))


if __name__ == "__main__":
    main()
