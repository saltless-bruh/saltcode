"""``saltcode.tools.calibrate`` — the measured-then-fixed protocol (task 14b).

Bridged into Pi as `saltcode_calibrate`. Takes a labelled calibration set, runs the real
machinery over it, derives each threshold from the observed distribution, and writes the
artifacts REQ-CAL-001 AC3 requires under `.saltcode/calibration/`.

The calibration set is one JSON file::

    {
      "schema_version": "1",
      "auditor": [
        {"id": "good-1", "diff": "--- a/x.py\\n+++ b/x.py\\n@@ ...",
         "expected_verdict": "pass", "spec_content": "...",
         "acceptance_criteria": ["..."], "static_report": "clean",
         "test_results": "1 passed"}
      ],
      "semantic": [
        {"id": "p1", "goal_a": "add login", "goal_b": "implement sign-in",
         "scope_a": ["src/auth.py"], "scope_b": ["src/auth.py"], "match": true}
      ]
    }

Either section may be empty; each calibrates its own thresholds and neither is inferred
from the other, so a run with only `auditor` entries leaves the semantic bars reading
`calibrated: false`. That partial state is the normal mid-protocol one, and claiming
otherwise would make the flag worthless.

Usage::

    python -m saltcode.tools.calibrate --repo . --set calibration.json
    python -m saltcode.tools.calibrate --repo . --set calibration.json --dry-run
    python -m saltcode.tools.calibrate --repo . --set calibration.json --only auditor

Exit codes: ``0`` at least one threshold was measured and written; ``1`` the set could
not support any measurement — a *verdict*, since an under-specified set is a normal
first attempt; ``2`` usage; ``3`` internal, including an unreachable judgment or
embedding endpoint.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from saltcode.providers.embeddings import EmbeddingClient, LocalEmbeddingClient
from saltcode.stability.calibrate import (
    AuditorCalibration,
    CalibrationError,
    CalibrationSet,
    SemanticCalibration,
    build_artifact,
    calibrate_auditor,
    calibrate_semantic,
    write_artifact,
)
from saltcode.stability.measure import (
    DEFAULT_N_PASSES,
    JudgmentClient,
    StabilityMeasurementError,
)
from saltcode.thresholds import load_thresholds, warn_if_uncalibrated
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

TOOL = "calibrate"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.calibrate",
        description="Measure Saltcode's thresholds from a labelled calibration set.",
    )
    parser.add_argument("--repo", default=".", help="Workspace root (default: cwd).")
    parser.add_argument("--set", dest="calibration_set", required=True, help="Calibration set JSON.")
    parser.add_argument(
        "--only",
        choices=["auditor", "semantic"],
        default=None,
        help="Calibrate one half only. Default: whichever sections the set carries.",
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=DEFAULT_N_PASSES,
        help=f"N for the Auditor measurement (default: {DEFAULT_N_PASSES}).",
    )
    parser.add_argument("--model", default=None, help="Saltnitor router section: A_STD, A_FOCUS, B.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Measure and report without writing to .saltcode/calibration/.",
    )
    return parser


def run(
    argv: Sequence[str] | None = None,
    client: JudgmentClient | None = None,
    embedding_client: EmbeddingClient | None = None,
) -> int:
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

    set_path = Path(args.calibration_set)
    if not set_path.is_file():
        return fail(TOOL, "InputError", f"--set is not a file: {args.calibration_set}", code=EXIT_USAGE)

    try:
        raw: Any = json.loads(set_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # ValueError covers both a JSON syntax error and UnicodeDecodeError, so a
        # binary file handed to --set is a usage error rather than a traceback.
        return fail(TOOL, "InputError", f"could not read {set_path}: {exc}", code=EXIT_USAGE)

    try:
        calibration_set = CalibrationSet.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - surfaced as a typed envelope
        return fail(TOOL, "ValidationError", f"{set_path} is not a valid calibration set: {exc}", code=EXIT_USAGE)

    want_auditor = args.only in (None, "auditor") and bool(calibration_set.auditor)
    want_semantic = args.only in (None, "semantic") and bool(calibration_set.semantic)

    if not want_auditor and not want_semantic:
        emit(
            {
                "tool": TOOL,
                "ok": False,
                "verdict": "nothing_to_measure",
                "detail": (
                    f"{set_path} carries no samples for the requested calibration "
                    f"({args.only or 'either half'}). Nothing was written, and the existing "
                    "thresholds are unchanged."
                ),
                "written": False,
            }
        )
        return EXIT_VERDICT_NEGATIVE

    auditor: AuditorCalibration | None = None
    semantic: SemanticCalibration | None = None
    problems: list[str] = []

    if want_auditor:
        judgment: JudgmentClient = client if client is not None else _default_judgment_client()
        try:
            auditor = calibrate_auditor(
                calibration_set.auditor, judgment, n_passes=args.passes, model=args.model
            )
        except CalibrationError as exc:
            # A set too small or too one-sided is a reportable verdict, not a crash:
            # the operator's next move is to add samples.
            problems.append(f"auditor: {exc}")
        except StabilityMeasurementError as exc:
            return fail(TOOL, "StabilityMeasurementError", str(exc), code=EXIT_ERROR)

    if want_semantic:
        embeddings: EmbeddingClient = (
            embedding_client if embedding_client is not None else LocalEmbeddingClient()
        )
        try:
            semantic = calibrate_semantic(calibration_set.semantic, embeddings)
        except CalibrationError as exc:
            problems.append(f"semantic: {exc}")
        except Exception as exc:  # noqa: BLE001 - an unreachable endpoint is internal
            return fail(TOOL, type(exc).__name__, f"semantic calibration failed: {exc}", code=EXIT_ERROR)

    if auditor is None and semantic is None:
        emit(
            {
                "tool": TOOL,
                "ok": False,
                "verdict": "not_measurable",
                "detail": "; ".join(problems),
                "problems": problems,
                "written": False,
            }
        )
        return EXIT_VERDICT_NEGATIVE

    artifact = build_artifact(
        auditor,
        semantic,
        set_name=set_path.name,
        judgment_model=args.model,
        embedding_model=_embedding_model_name(embedding_client) if want_semantic else None,
    )

    written_to: str | None = None
    if not args.dry_run:
        try:
            written_to = str(write_artifact(repo, artifact))
        except OSError as exc:
            return fail(TOOL, "IOError", f"could not write the calibration artifact: {exc}", code=EXIT_ERROR)

    # Read the thresholds back through the same loader the rest of the system uses, so
    # the reported state is what a later session will actually see rather than what this
    # run believes it wrote.
    resolved = load_thresholds(repo)

    payload: dict[str, Any] = {
        "tool": TOOL,
        "ok": True,
        "verdict": "calibrated",
        "written": written_to is not None,
        "artifact": written_to,
        "dry_run": bool(args.dry_run),
        "measured": sorted(artifact["thresholds"].keys()),
        "thresholds": artifact["thresholds"],
        "problems": problems,
        "resolved": resolved.summary(),
        "warning": warn_if_uncalibrated(resolved),
    }
    if auditor is not None:
        payload["auditor"] = auditor.model_dump()
    if semantic is not None:
        payload["semantic"] = semantic.model_dump()

    emit(payload)
    return EXIT_OK


def _default_judgment_client() -> JudgmentClient:
    """The on-box Saltnitor client. Imported late so `--help` needs no config."""
    from saltcode.providers.local import LocalClient

    return LocalClient()


def _embedding_model_name(client: EmbeddingClient | None) -> str | None:
    """The embedding identity to record, so REQ-CAL-001 AC4's "re-run on change" is checkable."""
    if client is not None:
        name = getattr(client, "model_name", None)
        return str(name) if isinstance(name, str) else None
    from saltcode.config import settings

    return str(settings.embedding_model)


def main() -> None:
    try:
        sys.exit(run())
    except Exception as exc:  # noqa: BLE001 - the entrypoint must never leak a traceback
        sys.exit(fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR))


if __name__ == "__main__":
    main()
