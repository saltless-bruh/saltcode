"""``saltcode.tools.sandbox_apply`` — the second gate of the Phase-2 loop (task 3.1).

Bridged into Pi as `saltcode_sandbox_apply` and called after `diff_check`
passes: create a disposable git worktree, apply the diff to it inside the
security container, and hand the sandbox path to the static gate (design §10).
The live tree is never touched — the diff reaches it only after the Auditor
returns `pass`, via `saltcode_apply_live` (task 10.3).

Satisfies REQ-STAT-001 (sandbox before the Auditor), REQ-SEC-001 (contained
execution), REQ-SEC-005 (refuse when nothing can contain).

Usage::

    python -m saltcode.tools.sandbox_apply --repo . --in builder.patch --keep
    cat builder.patch | python -m saltcode.tools.sandbox_apply --repo /path/to/tree

By default the sandbox is **discarded** before returning, which proves the apply
worked and leaves nothing behind. Pass ``--keep`` when the caller is the Phase-2
loop and the next gate needs the tree; the path comes back in ``sandbox``, and
the caller owns removing it.

Exit codes follow :mod:`saltcode.tools._cli`: ``0`` applied, ``1`` did not apply
(`impl_fail`), ``2`` usage, ``3`` internal error — including "no containment
backend", which is a stop condition for Phase 2 rather than a verdict on the diff.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from saltcode.harness.sandbox import (
    NoContainmentBackendError,
    apply_diff,
    disposable_sandbox,
    require_containment_backend,
)
from saltcode.tools._cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_VERDICT_NEGATIVE,
    STDIN_SENTINEL,
    emit,
    fail,
    parse_cli,
    read_payload,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

TOOL = "sandbox_apply"
IMPL_FAIL = "impl_fail"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.sandbox_apply",
        description="Apply a Builder diff to a disposable sandbox inside the security container.",
    )
    parser.add_argument("--repo", default=".", help="Target working tree to sandbox (default: cwd).")
    parser.add_argument(
        "--in",
        dest="source",
        default=STDIN_SENTINEL,
        help="Path to the unified diff, or '-' for stdin (default: stdin).",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Leave the sandbox on disk for the next gate; the caller must remove it.",
    )
    parser.add_argument(
        "--backend",
        default=None,
        choices=["bwrap", "docker", "firejail"],
        help="Force a containment backend instead of autodetecting (REQ-SEC-005 AC2).",
    )
    parser.add_argument(
        "--check-containment",
        action="store_true",
        help="Report which containment backend would be used, then exit without doing anything.",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args, code = parse_cli(parser, argv, TOOL)
    if args is None:
        # `--help` (code 0) or a usage error whose JSON envelope parse_cli
        # already emitted. Either way there is nothing further to run.
        return code

    if args.check_containment:
        try:
            backend = require_containment_backend(args.backend)
        except NoContainmentBackendError as exc:
            return fail(TOOL, "NoContainmentBackendError", str(exc), code=EXIT_ERROR)
        emit({"tool": TOOL, "ok": True, "verdict": "contained", "backend": backend, "detail": "containment available"})
        return EXIT_OK

    repo = Path(args.repo)
    if not repo.is_dir():
        return fail(TOOL, "InputError", f"--repo is not a directory: {args.repo}", code=EXIT_ERROR)

    try:
        diff_text = read_payload(args.source)
    except OSError as exc:
        return fail(TOOL, "InputError", f"Could not read the diff from {args.source!r}: {exc}")

    if not diff_text.strip():
        emit(
            {
                "tool": TOOL,
                "ok": False,
                "verdict": IMPL_FAIL,
                "detail": "empty diff — nothing to apply.",
            }
        )
        return EXIT_VERDICT_NEGATIVE

    # Checked up front so "nothing can contain this" is reported as a stop
    # condition, not as a diff that failed to apply (REQ-SEC-005).
    try:
        backend = require_containment_backend(args.backend)
    except NoContainmentBackendError as exc:
        return fail(TOOL, "NoContainmentBackendError", str(exc), code=EXIT_ERROR)

    kept_path: str | None = None
    applied = False
    with disposable_sandbox(repo) as sandbox:
        applied = apply_diff(sandbox, diff_text, workspace=repo, backend=args.backend)
        if applied and args.keep:
            # Move it out of the context manager's reach so the caller owns it.
            kept = Path(str(sandbox) + "-kept")
            shutil.move(str(sandbox), str(kept))
            kept_path = str(kept)
            sandbox.mkdir(parents=True, exist_ok=True)

    if not applied:
        emit(
            {
                "tool": TOOL,
                "ok": False,
                "verdict": IMPL_FAIL,
                "backend": backend,
                "detail": (
                    "the diff did not apply to a clean sandbox; discard and short-circuit to the Builder."
                ),
            }
        )
        return EXIT_VERDICT_NEGATIVE

    result: dict[str, Any] = {
        "tool": TOOL,
        "ok": True,
        "verdict": "applied",
        "backend": backend,
        "kept": kept_path is not None,
        "detail": "diff applied to a disposable sandbox inside the container; the live tree is untouched.",
    }
    if kept_path is not None:
        result["sandbox"] = kept_path
    emit(result)
    return EXIT_OK


def main() -> None:
    try:
        sys.exit(run())
    except Exception as exc:  # noqa: BLE001 - the entrypoint must never leak a traceback
        sys.exit(fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR))


if __name__ == "__main__":
    main()
