"""``saltcode.tools.compute_stability`` — the Auditor's measured verdict (tasks 10.1, 10.2).

Bridged into Pi as `saltcode_stability`. The Auditor is **not** a spawned sub-agent
(design §5.6a): its judgment runs here, N=3 times against the local Saltnitor client, to
avoid N process spawns per task. This entrypoint runs the zero-cost heuristics, then the
N-pass judgment, then emits `audit_result.json` plus the evidence behind it.

Usage::

    python -m saltcode.tools.compute_stability --repo . --task-id T3 --diff diff.patch
    python -m saltcode.tools.compute_stability --repo . --task-id T3 --diff - \\
        --static static.txt --tests tests.txt --spec tests/task_T3_spec.py --online

Exit codes: ``0`` the Auditor returned `pass`; ``1`` any other verdict — a *verdict*, not
a crash, and the one the loop routes on; ``2`` usage; ``3`` internal, including a
judgment that could not be run at all (an unreachable Saltnitor is not a low score).

**Nothing here escalates.** REQ-AUD-002 AC2's Flash re-judgment is the extension's call
(design §5.6a) — the payload carries `escalation` and the extension spends the API call.
`--online` only tells this tool which recommendation to emit; it never makes a network
request, and it cannot: the judgment client is the on-box Saltnitor and REQ-AUD-002 AC1
forbids anything else.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from saltcode.contracts.io import load_contract
from saltcode.contracts.tasks import TasksFile
from saltcode.diffs.diff_validator import extract_diff_from_fences
from saltcode.providers.local import LocalClient
from saltcode.stability.audit import audit_diff, audit_payload
from saltcode.stability.measure import (
    DEFAULT_N_PASSES,
    AuditEvidence,
    JudgmentClient,
    StabilityMeasurementError,
)
from saltcode.thresholds import load_thresholds
from saltcode.tools._cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    EXIT_VERDICT_NEGATIVE,
    emit,
    exit_code_for,
    fail,
    read_payload,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

TOOL = "compute_stability"

DEFAULT_TASKS_PATH = ".saltcode/tasks.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.compute_stability",
        description="Run the Auditor's heuristics and N-pass stability judgment on a diff.",
    )
    parser.add_argument("--repo", default=".", help="Workspace root (default: cwd).")
    parser.add_argument("--task-id", required=True, help="The task being audited.")
    parser.add_argument("--diff", required=True, help="Path to the unified diff, or '-' for stdin.")
    parser.add_argument("--tasks", default=None, help=f"tasks.json (default: <repo>/{DEFAULT_TASKS_PATH}).")
    parser.add_argument("--static", default=None, help="The clean static-gate report.")
    parser.add_argument("--tests", default=None, help="The test-runner output.")
    parser.add_argument("--spec", default=None, help="The task spec file the tests came from.")
    parser.add_argument(
        "--passes",
        type=int,
        default=DEFAULT_N_PASSES,
        help=f"N, the number of judgment passes (default: {DEFAULT_N_PASSES}).",
    )
    parser.add_argument("--model", default=None, help="Saltnitor router section: A_STD, A_FOCUS, B.")
    parser.add_argument(
        "--online",
        action="store_true",
        help="Report the online escalation recommendation. Makes no network call itself.",
    )
    return parser


def _read_optional(path: str | None) -> str:
    """Read an optional evidence file, or return an empty string when unset."""
    if not path:
        return ""
    return read_payload(path)


def run(argv: Sequence[str] | None = None, client: JudgmentClient | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exit_code_for(exc)

    if args.passes < 1:
        return fail(TOOL, "InputError", f"--passes must be at least 1, got {args.passes}", code=EXIT_USAGE)

    repo = Path(args.repo)
    if not repo.is_dir():
        return fail(TOOL, "InputError", f"--repo is not a directory: {args.repo}", code=EXIT_USAGE)

    try:
        raw_diff = read_payload(args.diff)
        static_report = _read_optional(args.static)
        test_results = _read_optional(args.tests)
        spec_content = _read_optional(args.spec)
    except OSError as exc:
        return fail(TOOL, "IOError", str(exc), code=EXIT_USAGE)

    diff_text = extract_diff_from_fences(raw_diff)
    if not diff_text.strip():
        return fail(TOOL, "InputError", "the diff is empty", code=EXIT_USAGE)

    # The acceptance criteria come from the contract when it is on disk. Absent, the
    # judgment still runs — it has the spec and the diff — and the payload says so
    # rather than pretending the criteria were considered.
    criteria: list[str] = []
    tasks_path = Path(args.tasks) if args.tasks else repo / DEFAULT_TASKS_PATH
    if tasks_path.is_file():
        try:
            tasks_file = load_contract(tasks_path, TasksFile)
        except Exception as exc:  # noqa: BLE001
            return fail(TOOL, type(exc).__name__, f"could not load {tasks_path}: {exc}", code=EXIT_ERROR)
        task = next((t for t in tasks_file.tasks if t.id == args.task_id), None)
        if task is not None:
            criteria = task.acceptance_criteria

    bars = load_thresholds(repo)
    stability_bar = bars.auditor_stability_threshold

    evidence = AuditEvidence(
        task_id=args.task_id,
        diff=diff_text,
        acceptance_criteria=criteria,
        static_report=static_report,
        test_results=test_results,
        spec_content=spec_content,
    )

    judgment: JudgmentClient = client if client is not None else LocalClient()

    try:
        result, measurement, report = audit_diff(
            evidence,
            judgment,
            n_passes=args.passes,
            model=args.model,
            threshold=stability_bar.value,
            threshold_calibrated=stability_bar.calibrated,
        )
    except StabilityMeasurementError as exc:
        # An unreachable judgment endpoint is an internal failure, never a verdict.
        # Emitting a low stability score here would tell the extension the Auditor is
        # unsure when in fact it never ran (REQ-AUD-002).
        return fail(TOOL, "StabilityMeasurementError", str(exc), code=EXIT_ERROR)

    payload = audit_payload(result, measurement, report, online=args.online)
    payload.update(
        {
            "tool": TOOL,
            "ok": result.status == "pass",
            "verdict": result.reason,
            "next_action": result.next_action,
            "criteria_available": bool(criteria),
        }
    )
    emit(payload)
    return EXIT_OK if result.status == "pass" else EXIT_VERDICT_NEGATIVE


def main() -> None:
    try:
        sys.exit(run())
    except Exception as exc:  # noqa: BLE001 - the entrypoint must never leak a traceback
        sys.exit(fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR))


if __name__ == "__main__":
    main()
