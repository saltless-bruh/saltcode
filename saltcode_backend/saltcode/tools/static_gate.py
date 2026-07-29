"""``saltcode.tools.static_gate`` — the per-language static gate (task 9.2).

Bridged into Pi as `saltcode_static_gate` and called by the Phase-2 loop after
`saltcode_sandbox_apply` (design §10). DIRTY short-circuits back to the Builder with
the lint reason and the Auditor is never invoked (REQ-STAT-001 AC1).

Usage::

    python -m saltcode.tools.static_gate --sandbox /path/to/worktree --repo .
    python -m saltcode.tools.static_gate --sandbox … --repo . --language rust

The language comes from project config unless `--language` overrides it.

Exit codes follow ``saltcode.tools._cli``: ``0`` clean, ``1`` dirty **or**
unavailable — both are negative verdicts that must not reach the Auditor — ``2``
usage, ``3`` internal. `unavailable` is distinguished in the payload's `verdict`
field rather than by exit code, because the caller's routing differs (a dirty gate
is a Builder retry; an unavailable one is a configuration problem) while the
short-circuit is the same.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from saltcode.static_gate.gate import run_static_gate
from saltcode.tools._cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    EXIT_VERDICT_NEGATIVE,
    emit,
    fail,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

TOOL = "static_gate"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.static_gate",
        description="Run the per-language static gate on a sandbox inside the security container.",
    )
    parser.add_argument("--sandbox", required=True, help="The disposable sandbox to analyse.")
    parser.add_argument("--repo", default=".", help="The live workspace (project config + audit log).")
    parser.add_argument(
        "--language",
        default=None,
        choices=["python", "typescript", "javascript", "rust", "go"],
        help="Override the language from project config.",
    )
    parser.add_argument("--backend", default=None, help="Force a containment backend.")
    return parser


def resolve_language(repo: Path, override: str | None) -> str:
    """The language to gate on: the flag, else project config, else python.

    Only a *missing* config falls back. A malformed `saltcode.toml`, an unreadable
    one, or one whose `language` fails validation propagates — the entrypoint turns
    it into an internal error (exit 3). Swallowing those would gate a TypeScript repo
    with `pyright` and report the result as if it meant something.
    """
    if override is not None:
        return override

    from saltcode.mcp.lsp_backends import load_project_config

    try:
        return load_project_config(repo).language
    except FileNotFoundError:
        # No config is not fatal — the gate reports `unavailable` for a language it
        # has no runners for, which is a verdict the caller can act on.
        return "python"


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE

    sandbox = Path(args.sandbox)
    if not sandbox.is_dir():
        return fail(TOOL, "InputError", f"--sandbox is not a directory: {args.sandbox}", code=EXIT_USAGE)

    repo = Path(args.repo)
    if not repo.is_dir():
        return fail(TOOL, "InputError", f"--repo is not a directory: {args.repo}", code=EXIT_ERROR)

    language = resolve_language(repo, args.language)
    report = run_static_gate(sandbox, language, workspace=repo, backend=args.backend)

    payload: dict[str, Any] = {"tool": TOOL, "ok": True, **report.to_dict()}
    if not report.clean:
        payload["reason"] = report.reason()

    emit(payload)
    return EXIT_OK if report.clean else EXIT_VERDICT_NEGATIVE


def main() -> None:
    try:
        sys.exit(run())
    except Exception as exc:  # noqa: BLE001 - the entrypoint must never leak a traceback
        sys.exit(fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR))


if __name__ == "__main__":
    main()
