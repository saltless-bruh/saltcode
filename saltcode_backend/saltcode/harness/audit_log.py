"""Append-only record of every command Saltcode executes (task 3.4, REQ-SEC-003).

`.saltcode/audit_log.jsonl` answers one question after the fact: *what did this
thing actually run on my machine?* One JSON object per line — greppable with
`jq`, readable without a tool, and append-only for the life of a sprint.

Each record carries the full command line, the working directory, the container
id, the exit code, an ISO-8601 UTC timestamp, and the SHA-256 of stdout+stderr
(REQ-SEC-003). The **hash, not the output**: a test run can emit megabytes, and
the log's job is to let you prove that a given run produced a given output, not
to become the output store.

**Refusals are logged too.** REQ-SEC-002 AC1 requires a non-allowlisted command
be "refused and logged" — and a refusal is exactly the entry an operator most
wants to find. Those records carry ``refused: true`` with a reason and a null
exit code, because nothing ran.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

AUDIT_LOG_RELATIVE_PATH = Path(".saltcode") / "audit_log.jsonl"

SCHEMA_VERSION = "1"
"""Bumped only on a breaking change to the record shape (REQ-GLB-005 in spirit)."""


def audit_log_path(workspace: Path | str) -> Path:
    """Resolve the audit log for a workspace: ``<workspace>/.saltcode/audit_log.jsonl``."""
    return Path(workspace).resolve() / AUDIT_LOG_RELATIVE_PATH


def hash_output(stdout: str, stderr: str) -> str:
    """SHA-256 over stdout concatenated with stderr, as REQ-SEC-003 specifies."""
    digest = hashlib.sha256()
    digest.update(stdout.encode("utf-8", errors="replace"))
    digest.update(stderr.encode("utf-8", errors="replace"))
    return digest.hexdigest()


def _format_command(command: Sequence[str] | str) -> str:
    """Render a command as one line for the log, without losing argument boundaries."""
    if isinstance(command, str):
        return command
    return " ".join(shlex.quote(part) for part in command)


def append_entry(
    workspace: Path | str,
    *,
    command: Sequence[str] | str,
    cwd: Path | str,
    exit_code: int | None,
    container_id: str | None = None,
    stdout: str = "",
    stderr: str = "",
    refused: bool = False,
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one record and return it.

    Opened with ``"a"`` per call rather than held open: a sprint is long, a crash
    is possible, and a log that is only flushed at exit is the log you do not
    have when you need it. Writes are a single ``write()`` of one line, which is
    atomic for the sizes involved on the platforms Saltcode targets.

    Args:
        workspace: The target repo; the log lands in its ``.saltcode/``.
        command: The command line, as argv or a string.
        cwd: Where it ran (or would have run).
        exit_code: The process exit code, or ``None`` when nothing ran.
        container_id: The containment backend's id for the run, when there was one.
        stdout: Captured stdout — hashed, never stored.
        stderr: Captured stderr — hashed, never stored.
        refused: True when the command was blocked before execution.
        reason: Why it was refused, or any other note worth keeping.
        extra: Additional fields to merge into the record.

    Returns:
        The record as written, so callers can surface it without re-reading.
    """
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": datetime.now(UTC).isoformat(),
        "command": _format_command(command),
        "cwd": str(cwd),
        "container_id": container_id,
        "exit_code": exit_code,
        "output_sha256": hash_output(stdout, stderr),
        "refused": refused,
    }
    if reason is not None:
        record["reason"] = reason
    if extra:
        record.update(extra)

    path = audit_log_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())

    return record


def read_entries(workspace: Path | str) -> list[dict[str, Any]]:
    """Read the log back, skipping any line that is not parseable JSON.

    A truncated final line (a crash mid-write) must not make the whole log
    unreadable — the surviving records are still evidence.
    """
    path = audit_log_path(workspace)
    if not path.exists():
        return []

    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            entries.append(cast("dict[str, Any]", parsed))
    return entries
