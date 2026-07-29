"""``saltcode.tools.cache_lookup`` — the cache ladder (task 5.7, REQ-CACHE-001).

Bridged into Pi as `saltcode_cache_lookup` and called by the `/sprint` handler before
Phase 1 (design §5.3, §11.2). Evaluates the ladder **strictly top-down and stops at the
first hit**:

1. **Spec Cache (exact)** — `sha256(normalized_goal + scope_fingerprint)`. A hit reuses
   `tasks.json` with zero API calls and the ladder stops (AC1).
2. **Semantic Cache (fuzzy)** — cosine ≥ threshold produces a *candidate* plus a
   PCD-adaptive confirmation bar. **This is not authorization to reuse:** REQ-CACHE-003
   AC1 requires an Architect confirmation, which only the extension can run. The verdict
   says which bar that confirmation must clear.
3. **Miss** — Phase 1 fires (AC3).

Usage::

    python -m saltcode.tools.cache_lookup --repo . --goal "add rate limiting"
    python -m saltcode.tools.cache_lookup --repo . --goal "..." --scope src/auth.py
    python -m saltcode.tools.cache_lookup --repo . --goal "..." --exact-only

Exit codes follow ``saltcode.tools._cli``: ``0`` when the ladder produced something to
use (exact hit or semantic candidate), ``1`` for a miss — a negative *verdict*, not a
crash, and the signal to fire Phase 1 — ``2`` for usage, ``3`` for an internal error.

Every response carries `scope_fingerprint`, `scope_source`, `key` and the threshold
calibration status, so a miss can be explained. That matters more here than elsewhere:
the store-time and lookup-time fingerprints are different objects by design (Trade C5,
see `memory/spec_cache.py`), so an exact hit generally needs the caller to pass the same
`--scope` it passed last time, and a cache that silently never hits looks identical to
one that is not wired up.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from saltcode.memory.spec_cache import lookup_spec_result
from saltcode.thresholds import load_thresholds, warn_if_uncalibrated
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

TOOL = "cache_lookup"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.cache_lookup",
        description="Run the exact -> semantic cache ladder for a goal; stop at the first hit.",
    )
    parser.add_argument("--repo", default=".", help="Workspace to look up in (default: cwd).")
    parser.add_argument("--goal", required=True, help="The sprint goal to look up.")
    parser.add_argument(
        "--scope",
        action="append",
        default=None,
        help="Scope path, used directly with no probe (REQ-CACHE-002 AC2). Repeatable.",
    )
    parser.add_argument(
        "--exact-only",
        action="store_true",
        help="Skip the semantic tier (e.g. when no embedding endpoint is reachable).",
    )
    return parser


def _semantic_tier(
    repo: Path, goal: str, scope: list[str] | None, thresholds: Any
) -> tuple[dict[str, Any], Any]:
    """Run the fuzzy tier, degrading to an explicit 'unavailable' rather than erroring.

    The semantic tier needs a local embedding endpoint; the exact tier does not. If the
    endpoint is down, the right outcome is a ladder that still ran its exact tier and
    says why the fuzzy one did not — not a failed tool call that leaves `/sprint` unable
    to decide whether to fire Phase 1.
    """
    from saltcode.memory.semantic_cache import lookup_semantic_result

    try:
        result = lookup_semantic_result(repo, goal, scope, thresholds=thresholds)
    except Exception as exc:  # noqa: BLE001 - a degraded tier is a result, not a crash
        return (
            {
                "status": "unavailable",
                "detail": f"semantic tier unavailable ({type(exc).__name__}: {exc})",
            },
            None,
        )
    return ({"status": "ran", **result.to_dict()}, result)


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE

    if not args.goal.strip():
        return fail(TOOL, "InputError", "--goal must not be empty", code=EXIT_USAGE)

    repo = Path(args.repo)
    if not repo.is_dir():
        return fail(TOOL, "InputError", f"--repo is not a directory: {args.repo}", code=EXIT_ERROR)

    thresholds = load_thresholds(repo)
    # REQ-CAL-001 AC2: the warning goes to stderr (stdout is the JSON contract) and the
    # status also rides in the payload so the extension can surface it at session open.
    warn_if_uncalibrated(thresholds)

    scope: list[str] | None = sorted(set(args.scope)) if args.scope else None

    exact = lookup_spec_result(repo, args.goal, scope)

    payload: dict[str, Any] = {
        "tool": TOOL,
        "ok": True,
        "goal": args.goal,
        "scope_fingerprint": exact.scope_fingerprint,
        "scope_source": exact.scope_source,
        "key": exact.key,
        "thresholds": thresholds.summary(),
        "exact": exact.to_dict(),
    }

    if exact.hit and exact.tasks is not None:
        # AC1: stop the ladder. The semantic tier is not consulted at all — running it
        # would spend an embedding call to answer a question already answered.
        payload["verdict"] = "exact_hit"
        payload["reuse_authorized"] = True
        payload["confirmation"] = "skip"
        payload["tasks"] = exact.tasks.model_dump(mode="json")
        payload["detail"] = "exact spec-cache hit; reuse tasks.json with zero API calls"
        emit(payload)
        return EXIT_OK

    if args.exact_only:
        payload["verdict"] = "miss"
        payload["reuse_authorized"] = False
        payload["semantic"] = {"status": "skipped", "detail": "--exact-only was passed"}
        payload["detail"] = "exact miss; semantic tier skipped, fire Phase 1"
        emit(payload)
        return EXIT_VERDICT_NEGATIVE

    semantic_payload, semantic = _semantic_tier(repo, args.goal, scope, thresholds)
    payload["semantic"] = semantic_payload

    if semantic is not None and semantic.hit and semantic.confirmation != "fall_through":
        payload["verdict"] = "semantic_candidate"
        # REQ-CACHE-003 AC1: a candidate is never reuse until the Architect confirms.
        payload["reuse_authorized"] = False
        payload["confirmation"] = semantic.confirmation
        payload["tasks"] = semantic.tasks.model_dump(mode="json") if semantic.tasks else None
        payload["detail"] = (
            f"semantic candidate (similarity {semantic.similarity:.4f}, PCD {semantic.pcd:.4f}); "
            f"Architect confirmation required: {semantic.confirmation}"
        )
        emit(payload)
        return EXIT_OK

    payload["verdict"] = "miss"
    payload["reuse_authorized"] = False
    payload["confirmation"] = "fall_through"
    payload["detail"] = "no exact or semantic hit; fire Phase 1"
    emit(payload)
    return EXIT_VERDICT_NEGATIVE


def main() -> None:
    try:
        sys.exit(run())
    except Exception as exc:  # noqa: BLE001 - the entrypoint must never leak a traceback
        sys.exit(fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR))


if __name__ == "__main__":
    main()
