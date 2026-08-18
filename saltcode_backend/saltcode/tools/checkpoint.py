"""``saltcode.tools.checkpoint`` — commit the task boundary and persist the record (17.2).

Bridged into Pi as `saltcode_checkpoint`. Step 8, the last of the Phase-2 pipeline: the
five gates passed, `apply_live` landed the diff, and `saltcode_regression` returned green
or an honest SKIP. This makes the git commit and writes the record that makes the run
resumable and roll-back-able (REQ-CKP-001).

Usage::

    python -m saltcode.tools.checkpoint --repo . --task T3 --regression pass \\
        --description "add the token refresh path" --sprint S1 --stability 1.0
    python -m saltcode.tools.checkpoint --repo . --task T3 --in gate_results.json

`--in` takes the gate results as a JSON object (or ``-`` for stdin) and records them
verbatim, so a later reader can see what the checkpoint was granted on rather than
trusting that it was granted correctly.

**It never pushes** (REQ-CKP-007) and there is no flag to make it. **It refuses a failed
regression**, at exit ``1``: that is the bar working, not an error.

Exit codes: ``0`` checkpoint made; ``1`` refused on the evidence (failed regression, or
nothing staged — the applied diff changed nothing); ``2`` usage; ``3`` internal.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from saltcode.checkpoint.checkpoint import (
    CHECKPOINTS_FILE,
    CheckpointError,
    CheckpointRefusedError,
    write_checkpoint,
)
from saltcode.tools._cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    EXIT_VERDICT_NEGATIVE,
    emit,
    fail,
    parse_cli,
    read_payload,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

TOOL = "checkpoint"

REGRESSION_STATES = ("pass", "unverified", "fail")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.checkpoint",
        description="Commit a completed task and append its checkpoint record. Never pushes.",
    )
    parser.add_argument("--repo", default=".", help="The live working tree (default: cwd).")
    parser.add_argument("--task", required=True, help="The task id this checkpoint closes.")
    parser.add_argument("--description", default="", help="Human summary; the commit subject.")
    parser.add_argument("--sprint", default="", help="The sprint id this task belongs to.")
    parser.add_argument(
        "--regression",
        default="unverified",
        choices=REGRESSION_STATES,
        help="The regression gate's outcome. 'fail' is refused (REQ-CKP-001).",
    )
    parser.add_argument(
        "--stability",
        default=None,
        type=float,
        help="The Auditor's measured stability score for this task.",
    )
    parser.add_argument(
        "--in",
        dest="input",
        default=None,
        help="Path to a JSON object of gate results, or '-' for stdin.",
    )
    return parser


def load_gate_results(source: str | None) -> dict[str, Any]:
    """Read `--in` as a JSON object.

    Raises:
        ValueError: The payload is not a JSON object. Recorded gates are evidence, and a
            bare string or list would be stored as something no reader can interpret.
        OSError: The path could not be read.
    """
    if source is None:
        return {}
    raw = read_payload(source)
    if not raw.strip():
        return {}
    parsed: object = json.loads(raw)
    if not isinstance(parsed, dict):
        msg = f"--in must be a JSON object, got {type(parsed).__name__}"
        raise ValueError(msg)
    return {str(key): value for key, value in cast("dict[Any, Any]", parsed).items()}


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args, code = parse_cli(parser, argv, TOOL)
    if args is None:
        return code

    repo = Path(args.repo)
    if not repo.is_dir():
        return fail(TOOL, "InputError", f"--repo is not a directory: {args.repo}", code=EXIT_USAGE)

    try:
        gate_results = load_gate_results(args.input)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return fail(TOOL, type(exc).__name__, f"could not read --in: {exc}", code=EXIT_USAGE)

    try:
        record = write_checkpoint(
            repo,
            task_id=args.task,
            description=args.description,
            gate_results=gate_results,
            stability=args.stability,
            regression=args.regression,
            sprint_id=args.sprint,
        )
    except CheckpointRefusedError as exc:
        # The bar working. Exit 1 so the loop reads it as "not checkpointed" and holds,
        # rather than as a tool crash.
        emit({"tool": TOOL, "ok": False, "error": "CheckpointRefusedError", "detail": str(exc)})
        return EXIT_VERDICT_NEGATIVE
    except CheckpointError as exc:
        return fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR)

    emit(
        {
            "tool": TOOL,
            "ok": True,
            "checkpoint": record.to_json(),
            "records_path": CHECKPOINTS_FILE,
            "pushed": False,
            "detail": (
                f"checkpoint {record.task_id} committed as {record.commit_sha[:12]} "
                f"(regression: {record.regression}). Nothing was pushed — REQ-CKP-007 "
                "keeps that explicit in every run mode."
            ),
        }
    )
    return EXIT_OK


def main() -> None:
    try:
        sys.exit(run())
    except Exception as exc:  # noqa: BLE001 - the entrypoint must never leak a traceback
        sys.exit(fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR))


if __name__ == "__main__":
    main()
