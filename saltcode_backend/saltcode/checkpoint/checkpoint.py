"""The checkpoint: a git commit plus a durable record (task 17.2 · REQ-CKP-001/007).

A task reaches a checkpoint only when the whole robustness bar holds — five gates, the
diff applied to the live tree, and the regression gate green *or* honestly SKIPPED
(design §10.1). This module is the last step of that bar and enforces the one part it can
see: it **refuses to commit a failed regression**. The rest of the bar is the extension's
to assemble, and the record says what it was told so a later reader can check.

**Two things this deliberately does not do.**

It does not push. REQ-CKP-007 makes that unconditional, in every run mode — a checkpoint
is a local boundary, and a `full` auto run that pushed would put unreviewed code on a
shared remote with nobody having looked. There is no `--push` flag here for the same
reason there is no `--allow` on `contained_exec`: the safe default has to be the only one.

It does not commit `.saltcode/`. The staging pathspec excludes it, so a checkpoint
records the *project's* work and never Saltcode's own bookkeeping. Two reasons, and the
second is the load-bearing one: the sprint artifacts and the audit log are not the task's
output and would add a churning directory to every commit; and a checkpoint ledger under
version control is rewound by the very `git reset --hard` that reads it, which silently
destroyed the ledger the first time this was run end to end. Saltcode's state is versioned
by the ledger and the session entries, not by the repository under test. A human who wants
the artifacts in history commits them deliberately.

It does not run inside the container. `git commit` executes no Builder-written code — it
records bytes an audited diff already placed — which is the same reasoning `apply_live`
records for `git apply`. REQ-SEC-001 contains *code execution*; containing a commit would
buy nothing and would need `git commit` on the REQ-SEC-002 allowlist, widening it for no
gain.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GIT_TIMEOUT_SECONDS = 60
"""Bounded like every other subprocess in the backend (REQ-STAT-003)."""

CHECKPOINTS_FILE = ".saltcode/checkpoints.jsonl"
"""The backend's durable copy.

The extension also writes `pi.appendEntry("saltcode:checkpoint", …)`, and both are wanted:
the session entry is what `/resume` replays, and this file is what survives a session store
being cleared, moved, or read by something that is not Pi.
"""


class CheckpointError(RuntimeError):
    """The checkpoint could not be made. The loop must not advance."""


class CheckpointRefusedError(CheckpointError):
    """The checkpoint was refused on the evidence, not prevented by a failure.

    A failed regression and an empty apply are *verdicts* — the system worked and the
    answer was no — so the entrypoint reports them at exit ``1`` rather than exit ``3``.
    Collapsing the two would make "your task did not meet the bar" indistinguishable
    from "git is broken", and the loop routes those very differently.
    """


@dataclass(frozen=True)
class CheckpointRecord:
    """Exactly the shape REQ-CKP-001 AC1 names, plus the description for humans."""

    task_id: str
    commit_sha: str
    gate_results: dict[str, Any] = field(default_factory=dict[str, Any])
    stability: float | None = None
    regression: str = "unverified"
    timestamp: str = ""
    sprint_id: str = ""
    description: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def git_command(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - fixed program, arguments built here
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - needs a wedged git
        raise CheckpointError(f"git {' '.join(args)} timed out after {GIT_TIMEOUT_SECONDS}s") from exc


def require_repo(repo: Path) -> None:
    if shutil.which("git") is None:
        raise CheckpointError("git is not on PATH, so no checkpoint can be made")
    if not (repo / ".git").exists():
        raise CheckpointError(f"not a git repository: {repo}")


def commit_message(task_id: str, description: str) -> str:
    """`saltcode: <task_id> — <description>`, subject bounded to a readable width.

    Prefixed so a human scanning `git log` can tell at a glance which commits an
    autonomous run produced, which matters most in `hybrid` and `full` modes where a
    stretch of history may have landed without anyone watching it happen.
    """
    subject = description.strip().splitlines()[0] if description.strip() else "task complete"
    if len(subject) > 60:
        subject = f"{subject[:57]}..."
    return f"saltcode: {task_id} — {subject}"


STAGE_PATHSPEC = [".", ":(exclude).saltcode"]
"""What a checkpoint stages: everything except Saltcode's own directory (see the docstring)."""


def has_staged_changes(repo: Path) -> bool:
    """Whether anything is staged. `git commit` with nothing staged is not a checkpoint."""
    return git_command(repo, ["diff", "--cached", "--quiet"]).returncode != 0


def write_checkpoint(
    workspace: Path | str,
    *,
    task_id: str,
    description: str = "",
    gate_results: dict[str, Any] | None = None,
    stability: float | None = None,
    regression: str = "unverified",
    sprint_id: str = "",
) -> CheckpointRecord:
    """Commit the applied task and persist its record.

    Args:
        workspace: The live tree.
        task_id: The task this checkpoint closes.
        description: Human summary; becomes the commit subject.
        gate_results: What the five gates reported, recorded verbatim.
        stability: The Auditor's measured stability for this task.
        regression: ``pass``, ``unverified`` (SKIPPED), or ``fail``.
        sprint_id: The sprint this task belongs to.

    Returns:
        The persisted :class:`CheckpointRecord`.

    Raises:
        CheckpointError: A failed regression, an unstageable tree, or a git failure —
            every case in which the loop must stop rather than advance (REQ-CKP-001 AC2).
    """
    repo = Path(workspace).resolve()
    require_repo(repo)

    if regression == "fail":
        raise CheckpointRefusedError(
            f"refusing to checkpoint {task_id}: the regression gate failed. REQ-CKP-001 "
            "makes a green (or honestly SKIPPED) regression part of the bar, and a commit "
            "here would turn a discardable `git reset` into a revert."
        )

    staged = git_command(repo, ["add", "-A", "--", *STAGE_PATHSPEC])
    if staged.returncode != 0:
        raise CheckpointError(f"git add failed: {staged.stderr.strip()}")

    if not has_staged_changes(repo):
        # apply_live ran and the tree is identical to HEAD. Not a checkpoint — something
        # upstream reported success without changing anything, and committing an empty
        # tree would record a task as finished on no evidence.
        raise CheckpointRefusedError(
            f"refusing to checkpoint {task_id}: nothing is staged, so the applied diff "
            "changed nothing on the live tree outside .saltcode/. A checkpoint records "
            "work; there is none."
        )

    message = commit_message(task_id, description)
    committed = git_command(repo, ["commit", "-m", message])
    if committed.returncode != 0:
        raise CheckpointError(f"git commit failed: {committed.stderr.strip() or committed.stdout.strip()}")

    head = git_command(repo, ["rev-parse", "HEAD"])
    if head.returncode != 0:
        raise CheckpointError(f"could not read HEAD after committing: {head.stderr.strip()}")

    record = CheckpointRecord(
        task_id=task_id,
        commit_sha=head.stdout.strip(),
        gate_results=gate_results or {},
        stability=stability,
        regression=regression,
        timestamp=datetime.now(UTC).isoformat(),
        sprint_id=sprint_id,
        description=description,
    )
    append_record(repo, record)
    return record


def append_record(workspace: Path | str, record: CheckpointRecord) -> Path:
    """Append one record to `.saltcode/checkpoints.jsonl` and return the path."""
    path = Path(workspace) / CHECKPOINTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.to_json(), sort_keys=True) + "\n")
    return path


def read_records(workspace: Path | str) -> list[CheckpointRecord]:
    """Every checkpoint on disk, oldest first. A malformed line is skipped, not fatal."""
    path = Path(workspace) / CHECKPOINTS_FILE
    if not path.is_file():
        return []
    records: list[CheckpointRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data.get("task_id"), str) and isinstance(data.get("commit_sha"), str):
            records.append(
                CheckpointRecord(
                    task_id=data["task_id"],
                    commit_sha=data["commit_sha"],
                    gate_results=data.get("gate_results") or {},
                    stability=data.get("stability"),
                    regression=str(data.get("regression", "unverified")),
                    timestamp=str(data.get("timestamp", "")),
                    sprint_id=str(data.get("sprint_id", "")),
                    description=str(data.get("description", "")),
                )
            )
    return records


def latest_record(workspace: Path | str, *, sprint_id: str | None = None) -> CheckpointRecord | None:
    """The most recent checkpoint, optionally scoped to one sprint (REQ-CKP-008)."""
    records = read_records(workspace)
    if sprint_id is not None:
        records = [record for record in records if record.sprint_id == sprint_id]
    return records[-1] if records else None
