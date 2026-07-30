"""``saltcode.tools.validate_contract`` — the Output-Length Enforcer entrypoint.

Bridged into Pi as ``saltcode_validate_contract`` (design §5.3). Validates an
agent's raw output against a typed contract, allowing exactly one bounded
repair, and optionally persists the result.

Satisfies REQ-GLB-002 (repair-or-reject, never persist non-conforming output),
REQ-GLB-005 (schema versioning) and REQ-CON-001..006 via the pydantic models.

Usage::

    python -m saltcode.tools.validate_contract --contract tasks --in out.json
    python -m saltcode.tools.validate_contract --contract context_report \\
        --in - --out .saltcode/context_report.json --max-length 20000

Exit codes follow :mod:`saltcode.tools._cli`: ``0`` valid, ``1`` invalid,
``2`` usage error, ``3`` I/O or internal error.

**Nothing is written unless validation succeeds.** On any failure the ``--out``
path is left untouched, so a malformed model response can never leave a partial
or garbage contract on disk (REQ-GLB-002 AC1).
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from saltcode.contracts import (
    AuditResult,
    ContextReport,
    ContractEnforcementError,
    EvaluatorReport,
    TasksFile,
    enforce_output,
    save_contract,
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

TOOL = "validate_contract"

CONTRACTS: dict[str, type[BaseModel]] = {
    "context_report": ContextReport,
    "tasks": TasksFile,
    "evaluator_report": EvaluatorReport,
    "audit_result": AuditResult,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.validate_contract",
        description="Validate agent output against a typed Saltcode contract.",
    )
    parser.add_argument(
        "--contract",
        required=True,
        choices=sorted(CONTRACTS),
        help="Which typed contract the payload must satisfy.",
    )
    parser.add_argument(
        "--in",
        dest="source",
        default=STDIN_SENTINEL,
        help="Path to the payload, or '-' for stdin (default: stdin).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write the validated contract here (atomically). Nothing is written on failure.",
    )
    parser.add_argument(
        "--max-length",
        type=int,
        default=None,
        help="Reject payloads longer than this many characters before parsing.",
    )
    parser.add_argument(
        "--allow-prose",
        action="store_true",
        help="Disable the JSON-only guard (REQ-GLB-002 AC2). Off by default.",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # `--help` exits 0; only a real parse error is a usage error.
        return exit_code_for(exc)

    contract_name: str = args.contract
    model_class = CONTRACTS[contract_name]

    try:
        payload = read_payload(args.source)
    except OSError as exc:
        return fail(TOOL, "InputError", f"Could not read payload from {args.source!r}: {exc}")

    try:
        model = enforce_output(
            payload,
            model_class,
            max_length=args.max_length,
            json_only=not args.allow_prose,
        )
    except ContractEnforcementError as exc:
        emit(
            {
                "tool": TOOL,
                "ok": False,
                "contract": contract_name,
                "error": type(exc).__name__,
                "detail": str(exc),
                "written": False,
            }
        )
        return EXIT_VERDICT_NEGATIVE

    written_to: str | None = None
    if args.out is not None:
        try:
            save_contract(args.out, model)
        except Exception as exc:  # noqa: BLE001 - surfaced as a typed JSON error
            return fail(TOOL, "WriteError", f"Validated, but could not write {args.out!r}: {exc}")
        written_to = args.out

    result: dict[str, Any] = {
        "tool": TOOL,
        "ok": True,
        "contract": contract_name,
        "detail": f"Payload is a valid {model_class.__name__}.",
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
