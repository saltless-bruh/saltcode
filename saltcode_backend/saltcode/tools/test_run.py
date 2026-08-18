"""``saltcode.tools.test_run`` — the task-spec test runner (task 9.3).

Bridged into Pi as `saltcode_test_run` and called after the static gate reports CLEAN
(design §10). FAIL discards the sandbox and short-circuits to the Builder with the test
output; PASS forwards the results to the Auditor (REQ-STAT-004).

Usage::

    python -m saltcode.tools.test_run --sandbox /path/to/worktree --repo . --task T1
    python -m saltcode.tools.test_run --sandbox … --repo . --task T1 --cmd "pytest -q"

`--cmd` overrides `test_runner_cmd` from project config; when neither is set the step
is SKIPPED (REQ-STAT-004 AC3) and the exit code is ``0``, because a skip is not a
failure — it is a configured absence the checkpoint records as weaker assurance.

Exit codes: ``0`` pass **or** skip, ``1`` any negative verdict (fail, no_tests,
timeout, refused, unavailable), ``2`` usage, ``3`` internal. Note that `no_tests`
lands on ``1``: an empty run is the false green this gate exists to catch (G-003).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from saltcode.static_gate.test_runner import run_task_spec
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

TOOL = "test_run"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.test_run",
        description="Run a task's acceptance spec on the sandbox inside the security container.",
    )
    parser.add_argument("--sandbox", required=True, help="The sandbox the diff was applied to.")
    parser.add_argument("--repo", default=".", help="The live workspace (spec source + audit log).")
    parser.add_argument("--task", required=True, help="Task id selecting tests/task_{id}_spec.*")
    parser.add_argument(
        "--cmd",
        default=None,
        help="Override test_runner_cmd from project config. Empty string forces a SKIP.",
    )
    parser.add_argument("--backend", default=None, help="Force a containment backend.")
    return parser


def resolve_command(repo: Path, override: str | None) -> str | None:
    """The test command: the flag if given (even empty), else project config.

    Only a *missing* config yields ``None``. A malformed or invalid one propagates to
    an internal error (exit 3) rather than becoming ``None`` — which would present as
    a legitimate `skipped` outcome at **exit 0**, i.e. a broken config silently
    reported as "the tests were fine to skip". That is precisely the false green
    REQ-STAT-004 AC3's skip must never be confused with.
    """
    if override is not None:
        return override

    from saltcode.mcp.lsp_backends import load_project_config

    try:
        return load_project_config(repo).test_runner_cmd
    except FileNotFoundError:
        return None


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args, code = parse_cli(parser, argv, TOOL)
    if args is None:
        # `--help` (code 0) or a usage error whose JSON envelope parse_cli
        # already emitted. Either way there is nothing further to run.
        return code

    sandbox = Path(args.sandbox)
    if not sandbox.is_dir():
        return fail(TOOL, "InputError", f"--sandbox is not a directory: {args.sandbox}", code=EXIT_USAGE)

    repo = Path(args.repo)
    if not repo.is_dir():
        return fail(TOOL, "InputError", f"--repo is not a directory: {args.repo}", code=EXIT_ERROR)

    if not args.task.strip():
        return fail(TOOL, "InputError", "--task must not be empty", code=EXIT_USAGE)

    report = run_task_spec(
        sandbox,
        args.task,
        workspace=repo,
        test_runner_cmd=resolve_command(repo, args.cmd),
        backend=args.backend,
    )

    payload: dict[str, Any] = {"tool": TOOL, "ok": True, "task_id": args.task, **report.to_dict()}
    emit(payload)

    # A skip is not a failure: REQ-STAT-004 AC3 makes an unconfigured runner a
    # legitimate outcome, and the checkpoint records the weaker assurance instead.
    return EXIT_OK if report.outcome in ("pass", "skipped") else EXIT_VERDICT_NEGATIVE


def main() -> None:
    try:
        sys.exit(run())
    except Exception as exc:  # noqa: BLE001 - the entrypoint must never leak a traceback
        sys.exit(fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR))


if __name__ == "__main__":
    main()
