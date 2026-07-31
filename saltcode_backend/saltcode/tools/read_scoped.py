"""``saltcode.tools.read_scoped`` — the Builder's scoped file read (task 10.4).

Bridged into Pi as `saltcode_read_scoped`. REQ-BLD-002 gives the Builder the **full
bodies of only the files in `task.files_affected`**, and nothing else: not design.md, not
a sibling task's files, not the rest of the repository.

**The scope is read from the contract, never from the caller.** This entrypoint takes a
`tasks.json` path and a task id, looks up that task, and hands its `files_affected` to
:class:`~saltcode.mcp.broker.ToolBroker`. It deliberately offers no "here is my allowed
list" argument, because such an argument is the whole check: an extension bug, a prompt
injection into an agent that can call tools, or a hand-run command could then widen the
scope simply by saying so. The privacy rule names this layer as the backend's
independent re-check (`.claude/rules/privacy-boundary.md`), and a re-check that trusts
its caller's copy of the answer is not one.

The role is fixed to `builder` for the same reason — it is the only role
`SCOPED_READ_ROLES` admits, and accepting a `--role` would let a caller claim it.

Usage::

    python -m saltcode.tools.read_scoped --repo . --task-id T3 --path src/auth.py
    python -m saltcode.tools.read_scoped --repo . --tasks .saltcode/tasks.json \\
        --task-id T3 --path src/auth.py

Exit codes: ``0`` the body is on stdout inside the JSON envelope; ``1`` the read was
refused as out of scope — a *verdict*, since REQ-BLD-002 AC1 expects blocked reads in
normal operation; ``2`` usage; ``3`` internal.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from saltcode.contracts.io import load_contract
from saltcode.contracts.tasks import TasksFile
from saltcode.mcp.broker import ScopeViolationError, ToolBroker
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

TOOL = "read_scoped"

DEFAULT_TASKS_PATH = ".saltcode/tasks.json"

BUILDER_ROLE = "builder"
"""Fixed, not an argument. See the module docstring."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.read_scoped",
        description="Read a file body, if it is inside the current task's files_affected.",
    )
    parser.add_argument("--repo", default=".", help="Workspace root (default: cwd).")
    parser.add_argument(
        "--tasks",
        default=None,
        help=f"Path to tasks.json (default: <repo>/{DEFAULT_TASKS_PATH}).",
    )
    parser.add_argument("--task-id", required=True, help="The task whose scope applies.")
    parser.add_argument("--path", required=True, help="The file to read, relative to the repo.")
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

    tasks_path = Path(args.tasks) if args.tasks else repo / DEFAULT_TASKS_PATH
    if not tasks_path.is_file():
        return fail(
            TOOL,
            "InputError",
            f"tasks.json not found at {tasks_path}. The read scope comes from the contract, "
            "so without it there is no scope to enforce and the read is refused.",
            code=EXIT_USAGE,
        )

    try:
        tasks_file = load_contract(tasks_path, TasksFile)
    except Exception as exc:  # noqa: BLE001 - surfaced as a typed envelope, never a traceback
        return fail(TOOL, type(exc).__name__, f"could not load {tasks_path}: {exc}", code=EXIT_ERROR)

    task = next((t for t in tasks_file.tasks if t.id == args.task_id), None)
    if task is None:
        return fail(
            TOOL,
            "InputError",
            f"no task with id {args.task_id!r} in {tasks_path}",
            code=EXIT_USAGE,
        )

    try:
        broker = ToolBroker(BUILDER_ROLE, repo, task.files_affected)
    except ScopeViolationError as exc:
        # A `files_affected` entry pointing out of the workspace is a defective task,
        # not a bad read — reported as such so the Planner's output is what gets fixed.
        return fail(TOOL, "ScopeViolationError", str(exc), code=EXIT_ERROR)

    try:
        content = broker.read_file(args.path)
    except ScopeViolationError as exc:
        emit(
            {
                "tool": TOOL,
                "ok": False,
                "verdict": "out_of_scope",
                "task_id": task.id,
                "path": args.path,
                "files_affected": task.files_affected,
                "detail": str(exc),
            }
        )
        return EXIT_VERDICT_NEGATIVE
    except (FileNotFoundError, IsADirectoryError) as exc:
        # In scope but not readable. Distinct from a refusal: the Builder is allowed
        # this path, so the answer is "create it", not "you may not look".
        emit(
            {
                "tool": TOOL,
                "ok": False,
                "verdict": "not_readable",
                "task_id": task.id,
                "path": args.path,
                "files_affected": task.files_affected,
                "detail": str(exc),
            }
        )
        return EXIT_VERDICT_NEGATIVE
    except OSError as exc:
        return fail(TOOL, "IOError", f"could not read {args.path}: {exc}", code=EXIT_ERROR)

    payload: dict[str, Any] = {
        "tool": TOOL,
        "ok": True,
        "verdict": "in_scope",
        "task_id": task.id,
        "path": args.path,
        "files_affected": task.files_affected,
        "bytes": len(content.encode("utf-8")),
        "content": content,
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
