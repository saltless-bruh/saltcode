"""``saltcode.tools.scope_probe`` — the lookup-time scope fingerprint (task 3.2).

Bridged into Pi as `saltcode_scope_probe` and called by the `/sprint` handler
before the cache ladder: it supplies the `scope_fingerprint` half of the
Spec-Cache key when there is no `tasks.json` yet (REQ-CACHE-002, design §11.2).

A **tool call, never a Phase-1 fire** — a filesystem walk, no LSP session and no
model call — so running it before every sprint cannot break the
one-fire-per-sprint invariant (REQ-ORC-001).

Usage::

    python -m saltcode.tools.scope_probe --repo .
    python -m saltcode.tools.scope_probe --repo . --scope src/auth.py --scope src/db.py

``--scope`` is used **directly, with no probe** (REQ-CACHE-002 AC2): an explicit
scope is the caller telling us the answer, and measuring it again could only
disagree.

Exit code is ``0`` whenever the probe ran. An empty result is not a failure —
REQ-CACHE-002 AC3 expects an empty repo to yield an empty fingerprint so the key
degrades to goal-only — so there is no negative verdict here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from saltcode.harness.scope_probe import run_scope_probe
from saltcode.tools._cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    emit,
    fail,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

TOOL = "scope_probe"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.scope_probe",
        description="Produce the sorted module list used as the lookup-time scope fingerprint.",
    )
    parser.add_argument("--repo", default=".", help="Workspace to probe (default: cwd).")
    parser.add_argument(
        "--scope",
        action="append",
        default=None,
        help="Use this path instead of probing. Repeatable (REQ-CACHE-002 AC2).",
    )
    parser.add_argument(
        "--language",
        default=None,
        choices=["python", "typescript", "javascript", "rust", "go"],
        help="Override the language from project config, which selects the source extensions.",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE

    if args.scope:
        # Sorted, because the fingerprint is order-independent and the caller's
        # argument order must not change the cache key.
        scope = sorted(set(args.scope))
        emit(
            {
                "tool": TOOL,
                "ok": True,
                "verdict": "provided",
                "scope": scope,
                "count": len(scope),
                "detail": "--scope was supplied, so no probe was run (REQ-CACHE-002 AC2).",
            }
        )
        return EXIT_OK

    repo = Path(args.repo)
    if not repo.is_dir():
        return fail(TOOL, "InputError", f"--repo is not a directory: {args.repo}", code=EXIT_ERROR)

    modules = run_scope_probe(repo, language=args.language)

    result: dict[str, Any] = {
        "tool": TOOL,
        "ok": True,
        "verdict": "probed" if modules else "empty",
        "scope": modules,
        "count": len(modules),
        "detail": (
            f"{len(modules)} source modules in scope."
            if modules
            else "no source modules found; the cache key degrades to goal-only (REQ-CACHE-002 AC3)."
        ),
    }
    emit(result)
    return EXIT_OK


def main() -> None:
    try:
        sys.exit(run())
    except Exception as exc:  # noqa: BLE001 - the entrypoint must never leak a traceback
        sys.exit(fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR))


if __name__ == "__main__":
    main()
