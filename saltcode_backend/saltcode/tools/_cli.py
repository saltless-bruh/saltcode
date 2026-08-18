"""Shared conventions for the ``saltcode.tools.*`` CLI entrypoints (REQ-EXT-004).

Every entrypoint is invoked by the extension as
``pi.exec("python", ["-m", "saltcode.tools.<name>", ...])``, so all of them share
one contract:

* **stdout is exactly one JSON object** — the tool result the extension surfaces.
* **stderr carries human-readable diagnostics only** — never part of the contract.
* **the exit code classifies the outcome**, so the extension can short-circuit
  without parsing:

  ===== ==================================================================
  Code  Meaning
  ===== ==================================================================
  0     The tool ran and the verdict is positive (valid / clean / pass).
  1     The tool ran and the verdict is negative (invalid / dirty / fail).
        This is a *result*, not a crash — stdout still holds valid JSON.
  2     Usage error: bad or missing arguments. JSON on stdout.
  3     Internal error: I/O failure, unreadable input, unexpected exception.
  ===== ==================================================================

The specs do not fix these numbers; they are the smallest scheme consistent with
the design's "short-circuit on failure" gate sequence (design §10) and are
documented here as the contract Task 7b.2 consolidates.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

EXIT_OK = 0
"""Ran successfully; verdict is positive."""

EXIT_VERDICT_NEGATIVE = 1
"""Ran successfully; verdict is negative. stdout still holds valid JSON."""

EXIT_USAGE = 2
"""Arguments were missing or invalid."""

EXIT_ERROR = 3
"""Internal failure: I/O error or an unexpected exception."""

STDIN_SENTINEL = "-"
"""Passed to ``--in`` to read the payload from stdin."""


def emit(payload: dict[str, Any]) -> None:
    """Write the single JSON result object to stdout.

    Uses ``sort_keys`` so byte output is stable for a given payload, which keeps
    entrypoint tests deterministic.
    """
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    sys.stdout.flush()


def fail(tool: str, error: str, detail: str, code: int = EXIT_ERROR) -> int:
    """Emit a negative result envelope and return the exit code to propagate."""
    emit({"tool": tool, "ok": False, "error": error, "detail": detail})
    return code


def read_payload(source: str) -> str:
    """Read an entrypoint's input payload from a file path or stdin.

    ``source`` is either a filesystem path or :data:`STDIN_SENTINEL` (``"-"``).

    Raises:
        OSError: If the path cannot be read.
    """
    if source == STDIN_SENTINEL:
        return sys.stdin.read()
    from pathlib import Path

    return Path(source).read_text(encoding="utf-8")


def parse_cli(
    parser: argparse.ArgumentParser,
    argv: Sequence[str] | None,
    tool: str,
) -> tuple[argparse.Namespace | None, int]:
    """Parse arguments, emitting the JSON envelope on a usage error.

    Returns ``(args, EXIT_OK)`` on success, or ``(None, code)`` when the caller should
    return ``code`` immediately.

    **Why this exists.** `argparse` writes its usage message to *stderr* and raises
    `SystemExit(2)`; catching that and returning :data:`EXIT_USAGE` gets the exit code
    right and leaves **stdout empty**. Every entrypoint did exactly that, so a mistyped
    flag produced exit 2 with nothing to parse — and the extension's `JSON.parse` throws
    on an empty string, turning a typo into an unhandled error rather than a tool result.
    The exit-code half of the contract was tested per tool; the payload half was not,
    which is what G-006 warned about and what `tests/test_task_7b_entrypoints.py` caught.

    ``--help`` is the one documented exception: `argparse` raises ``SystemExit(0)`` after
    printing help text to stdout, and appending a JSON object to that would produce
    output that is neither help nor parseable. It returns :data:`EXIT_OK` with no payload.

    The usage message is preserved on stderr *and* carried in the payload's ``detail``,
    so a human running the CLI still sees it and a machine can read it.
    """
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            args = parser.parse_args(argv)
    except SystemExit as exc:
        message = buffer.getvalue()
        sys.stderr.write(message)
        code = exit_code_for(exc)
        if code == EXIT_OK:
            return None, EXIT_OK
        return None, fail(tool, "UsageError", message.strip() or "invalid arguments", code=code)
    else:
        sys.stderr.write(buffer.getvalue())
        return args, EXIT_OK


def exit_code_for(exc: SystemExit) -> int:
    """Map an ``argparse``-raised :class:`SystemExit` to our exit-code scheme.

    ``parse_args`` raises ``SystemExit(0)`` for ``-h``/``--help`` and ``SystemExit(2)``
    for a genuine parse error. Catching it unconditionally and returning
    :data:`EXIT_USAGE` reports a *successful* ``--help`` as a usage error, which any
    caller shelling out to discover a tool's interface would read as a failure.

    ``SystemExit.code`` may also be ``None`` (a bare ``sys.exit()``) or a string; both
    mean "not one of our codes", so they collapse to :data:`EXIT_USAGE`.
    """
    return exc.code if isinstance(exc.code, int) else EXIT_USAGE
