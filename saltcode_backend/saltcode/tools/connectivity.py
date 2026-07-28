"""``saltcode.tools.connectivity`` — the online/offline probe entrypoint.

Bridged into Pi as `saltcode_connectivity` and called at session open (design
§11.1 step 2), where its verdict selects the routing path for the whole session:
online → DeepSeek per the design §6 tier table; offline → all Phase-1 agents on
Tier-B Saltnitor with the Auditor staying local (REQ-GATE-001, REQ-MOD-003).

Satisfies task 2.3.

Usage::

    python -m saltcode.tools.connectivity
    python -m saltcode.tools.connectivity --timeout 1.0 --target https://api.deepseek.com

**Exit code 1 means offline, not broken.** Offline is a supported operating mode
(design §12), so the caller routes on it rather than treating it as a failure;
`1` is used because the shared convention in :mod:`saltcode.tools._cli` reserves
it for a negative verdict with valid JSON on stdout. A genuine fault — bad
arguments, an unexpected exception — is `2` or `3` as usual.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, Any

from saltcode.harness.connectivity import (
    DEFAULT_TIMEOUT_SECONDS,
    probe_connectivity,
)
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

TOOL = "connectivity"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m saltcode.tools.connectivity",
        description="Probe network reachability to select the online or offline routing path.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Seconds allowed per target (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--target",
        action="append",
        dest="targets",
        default=None,
        help="Probe this URL instead of the defaults. Repeatable; tried in order.",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE

    if args.timeout <= 0:
        return fail(TOOL, "UsageError", "--timeout must be greater than zero.", code=EXIT_USAGE)

    targets: tuple[str, ...] | None = tuple(args.targets) if args.targets else None
    report = probe_connectivity(timeout=args.timeout, targets=targets)

    if report.forced_offline:
        detail = "SALTCODE_OFFLINE=1 — offline was requested, so no probe was made."
    elif report.online:
        reached = next(probe.url for probe in report.probes if probe.reachable)
        detail = f"Reached {reached}."
    else:
        detail = f"No target responded ({len(report.probes)} probed)."

    result: dict[str, Any] = {
        "tool": TOOL,
        "ok": report.online,
        "verdict": "online" if report.online else "offline",
        "online": report.online,
        "forced_offline": report.forced_offline,
        "detail": detail,
        "probes": [
            {"url": probe.url, "reachable": probe.reachable, "detail": probe.detail} for probe in report.probes
        ],
    }
    emit(result)
    return EXIT_OK if report.online else EXIT_VERDICT_NEGATIVE


def main() -> None:
    try:
        sys.exit(run())
    except Exception as exc:  # noqa: BLE001 - the entrypoint must never leak a traceback
        sys.exit(fail(TOOL, type(exc).__name__, str(exc), code=EXIT_ERROR))


if __name__ == "__main__":
    main()
