"""``saltcode.tools.regression`` — the full-suite regression gate (task 17.1).

Bridged into Pi as `saltcode_regression`. Step 7 of the Phase-2 pipeline: `apply_live`
has put the audited diff on the live tree and left it uncommitted, and this decides
whether that integrated tree earns a checkpoint (design §10.1, REQ-CKP-002).

Usage::

    python -m saltcode.tools.regression --repo .
    python -m saltcode.tools.regression --repo . --cmd "pytest -q"
    python -m saltcode.tools.regression --repo . --cmd ""      # force a SKIP

`--cmd` overrides `[checkpoint] regression_cmd` in `saltcode.toml`. An empty string
forces the SKIP explicitly, which is how a caller says "there is no full suite here"
without editing the project's config.

**Unconfigured is a SKIP at exit 0, not a failure.** REQ-CKP-002 AC3 is explicit, and the
skip is carried into the payload as ``regression: "unverified"`` so the checkpoint records
the weaker assurance rather than inheriting a green. (design §10.1's parenthetical gloss —
"default = the full `test_runner_cmd` with no task filter" — is not implemented as an
automatic fallback: deriving it would mean stripping a task filter out of an arbitrary
command string by guesswork, and the binding AC says SKIP. Recorded as doc drift.)

Exit codes: ``0`` pass or skipped; ``1`` fail or unavailable — both negative verdicts the
loop routes on, not crashes; ``2`` usage; ``3`` internal.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from saltcode.checkpoint.regression import RegressionResult, run_regression
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

TOOL = "regression"

CONFIG_CANDIDATES = ("saltcode.toml", ".saltcode/config.toml")
"""The same discovery order `mcp/lsp_backends.load_project_config` uses."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.regression",
        description="Run the project's full test suite on the integrated live tree, contained.",
    )
    parser.add_argument("--repo", default=".", help="The live working tree (default: cwd).")
    parser.add_argument(
        "--cmd",
        default=None,
        help="Override [checkpoint] regression_cmd. An empty string forces a SKIP.",
    )
    parser.add_argument("--backend", default=None, help="Force a containment backend.")
    return parser


def resolve_command(repo: Path, override: str | None) -> str | None:
    """The regression command: the flag if given (even empty), else `[checkpoint]`.

    A *missing* config is ``None`` (→ SKIP). A malformed one raises, because a TOML syntax
    error silently read as "no full suite configured" would report a broken project as a
    legitimate skip at exit 0 — the same false green `test_run.resolve_command` guards.
    """
    if override is not None:
        return override

    for name in CONFIG_CANDIDATES:
        path = repo / name
        if path.is_file():
            with path.open("rb") as handle:
                data: dict[str, Any] = dict(tomllib.load(handle))
            section: object = data.get("checkpoint")
            if isinstance(section, dict):
                value: object = cast("dict[str, Any]", section).get("regression_cmd")
                if isinstance(value, str):
                    return value
            return None
    return None


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args, code = parse_cli(parser, argv, TOOL)
    if args is None:
        return code

    repo = Path(args.repo)
    if not repo.is_dir():
        return fail(TOOL, "InputError", f"--repo is not a directory: {args.repo}", code=EXIT_USAGE)

    try:
        command = resolve_command(repo, args.cmd)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return fail(
            TOOL,
            type(exc).__name__,
            f"could not read the project config for [checkpoint] regression_cmd: {exc}",
            code=EXIT_ERROR,
        )

    result: RegressionResult = run_regression(repo, regression_cmd=command, backend=args.backend)

    payload: dict[str, Any] = {
        "tool": TOOL,
        "ok": result.outcome in {"pass", "skipped"},
        **asdict(result),
        "failing_files": list(result.failing_files),
        # What `checkpoint --regression` should be given. `skipped` becomes `unverified`
        # here rather than at the checkpoint, so the two tools cannot disagree about what
        # a skip means (REQ-CKP-002 AC3).
        "checkpoint_regression": {
            "pass": "pass",
            "fail": "fail",
            "skipped": "unverified",
            "unavailable": "unverified",
        }[result.outcome],
        "attributable": result.attributable,
    }
    emit(payload)
    return EXIT_OK if payload["ok"] else EXIT_VERDICT_NEGATIVE


def main() -> None:
    try:
        sys.exit(run())
    except Exception as exc:  # noqa: BLE001 - the entrypoint must never leak a traceback
        sys.exit(fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR))


if __name__ == "__main__":
    main()
