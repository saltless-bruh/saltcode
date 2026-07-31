"""Applying a passed diff to the **live** working tree (task 10.3).

This is the only code path in the backend that writes to the repository the human is
working in. Everything before it — the static gate, the test runner, the Auditor — runs
against a disposable sandbox precisely so that the live tree is never the experiment
(REQ-STAT-001 AC3). By the time this runs, the diff has cleared five gates.

Two rules give this module its shape.

**The tree is left uncommitted.** Design §10.1 makes the checkpoint a *later* event: the
diff lands, then the regression gate runs the project's full suite on the integrated
tree, and only a green suite earns the commit. Committing here would make a regression
failure a `git revert` instead of a `git reset --hard <last checkpoint>`, and would put a
task in the history that the loop has not yet decided is finished.

**`tests/**` is refused, again.** REQ-BLD-003 is enforced at Pi's `tool_call` handler and
in the sub-agent allowlists; this is the third layer named by the privacy/write-scope
rule. It is not redundant defensively — this entrypoint is reachable by `pi.exec` from
anywhere, and a diff that reaches it has already been through several transformations.
A Builder that could edit its own acceptance tests can pass any task.

Unlike the sandbox apply (task 3.1), this does **not** run inside the security container:
the live tree is the one path the container deliberately does not make writable, and
`git apply` here is applying an already-audited patch rather than executing model-written
code. The command still goes through the audit log (REQ-SEC-003).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from saltcode.harness.audit_log import append_entry

GIT_APPLY_TIMEOUT_SECONDS = 60
"""Bounded like every other subprocess in the backend (REQ-STAT-003)."""

FORBIDDEN_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|/)tests/", re.IGNORECASE),
)
"""Paths no Builder diff may touch (REQ-BLD-003).

One pattern covers all three shapes, since :func:`blocked_paths` matches with `search`:
a top-level `tests/`, `.saltcode/tests/`, and a nested suite (`src/pkg/tests/…`, where
Python and Go projects usually keep theirs).

**Case-insensitive, and that is not cosmetic.** This module writes to the human's live
tree, which on macOS (APFS) or Windows (NTFS) is case-insensitive: a diff naming
`Tests/task_T1_spec.py` clears a case-sensitive check and then `git apply` writes the
existing `tests/task_T1_spec.py`. REQ-BLD-003 would be defeated on exactly the machine
this code exists to protect.
"""

_DIFF_PATH_RE = re.compile(
    r"^(?:"
    r"diff --git \"?[ab]/(?P<git_a>[^\"\t]+?)\"? \"?[ab]/(?P<git_b>[^\"\t]+?)\"?"
    r"|--- \"?(?:[ab]/)?(?P<minus>[^\"\t]+?)\"?"
    r"|\+\+\+ \"?(?:[ab]/)?(?P<plus>[^\"\t]+?)\"?"
    r"|rename (?:from|to) \"?(?P<rename>[^\"\t]+?)\"?"
    r"|(?:copy|new file|deleted file) .*"
    r")\s*$"
)
"""Every way a unified diff names a file.

Deliberately wider than `--- a/` / `+++ b/`. `git diff --no-prefix` emits `--- path`;
`git format-patch` emits `diff --git a/x b/y` plus `rename from`/`rename to` lines for a
move. A path check that only understood the `a/`-prefixed form would let a rename *into*
`tests/` through — the diff body would carry no `+++ b/tests/...` line at all.
"""


class LiveApplyError(RuntimeError):
    """The diff could not be applied to the live tree."""


class WriteScopeViolationError(LiveApplyError):
    """The diff touches a path no agent may write (REQ-BLD-003)."""


@dataclass
class LiveApplyResult:
    """The outcome of applying a diff to the live tree."""

    status: Literal["applied", "refused", "failed"]
    detail: str
    paths: list[str] = field(default_factory=list[str])
    blocked_paths: list[str] = field(default_factory=list[str])
    committed: bool = False
    """Always ``False``. Present because the checkpoint discipline is the thing a reader
    of this JSON most needs to be sure of: the tree is dirty and the regression gate has
    not run yet (design §10.1)."""

    @property
    def ok(self) -> bool:
        return self.status == "applied"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "paths": self.paths,
            "blocked_paths": self.blocked_paths,
            "committed": self.committed,
        }


def _as_text(raw: str | bytes | None) -> str:
    """`TimeoutExpired.stdout` is `str` under `text=True` but typed as `bytes | None`."""
    if raw is None:
        return ""
    return raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")


def diff_paths(diff_text: str) -> list[str]:
    """Every repository path a unified diff names, de-duplicated, in first-seen order.

    ``/dev/null`` is dropped — it is how a diff spells "this file did not exist", not a
    path anything writes to.
    """
    found: list[str] = []
    for line in diff_text.splitlines():
        match = _DIFF_PATH_RE.match(line)
        if match is None:
            continue
        for value in match.groupdict().values():
            if not value or value == "/dev/null":
                continue
            normalised = value.replace("\\", "/").strip()
            if normalised and normalised not in found:
                found.append(normalised)
    return found


def blocked_paths(diff_text: str) -> list[str]:
    """The paths in ``diff_text`` that REQ-BLD-003 forbids."""
    return [
        path
        for path in diff_paths(diff_text)
        if any(pattern.search(path) for pattern in FORBIDDEN_PATH_PATTERNS)
    ]


def check_write_scope(diff_text: str) -> None:
    """Refuse a diff that writes under `tests/**`.

    Raises:
        WriteScopeViolationError: naming every offending path, so the Builder retry can
            say which file it may not touch rather than "denied".
    """
    blocked = blocked_paths(diff_text)
    if blocked:
        raise WriteScopeViolationError(
            "the diff writes to paths no agent may modify (REQ-BLD-003): "
            + ", ".join(blocked)
            + ". Task specs are written by Test Intent and are immutable to the Builder; "
            "a defective spec is fixed by a re-spec (REQ-TST-002), never by editing it."
        )


def apply_live(
    repo: Path | str,
    diff_text: str,
    *,
    timeout: int = GIT_APPLY_TIMEOUT_SECONDS,
) -> LiveApplyResult:
    """Apply an Auditor-passed diff to the live working tree, leaving it uncommitted.

    The order is: write-scope check, then ``git apply --check``, then the real apply.
    The scope check comes first so a forbidden path is refused without git ever being
    asked — a `tests/**` write must not be reported as "the patch did not apply", which
    reads as a Builder formatting problem and routes to the wrong retry.

    Args:
        repo: The live working tree.
        diff_text: The unified diff, already validated by `saltcode_diff_check`.
        timeout: Seconds before `git apply` is killed.

    Returns:
        A :class:`LiveApplyResult`. ``status`` is ``"refused"`` for a write-scope
        violation, ``"failed"`` when git declined the patch, ``"applied"`` on success.
    """
    repo_path = Path(repo).resolve()

    try:
        check_write_scope(diff_text)
    except WriteScopeViolationError as exc:
        return LiveApplyResult(
            status="refused",
            detail=str(exc),
            paths=diff_paths(diff_text),
            blocked_paths=blocked_paths(diff_text),
        )

    if shutil.which("git") is None:
        raise LiveApplyError("git is not on PATH, so the diff cannot be applied to the live tree")
    if not (repo_path / ".git").exists():
        raise LiveApplyError(f"not a git repository: {repo_path}")

    paths = diff_paths(diff_text)

    def run(args: list[str]) -> subprocess.CompletedProcess[str]:
        # REQ-SEC-003: every executed command is recorded, including the ones that run
        # outside the container. A write to the live tree is the last thing that should
        # be missing from the audit log — and a command that *timed out* still ran, and
        # may have written part of the patch before it was killed, so it is the entry a
        # reader would most want. `exit_code=None` is the log's own "nothing exited"
        # value, with the reason naming the timeout.
        try:
            completed = subprocess.run(
                args,
                cwd=repo_path,
                input=diff_text,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            append_entry(
                repo_path,
                command=args,
                cwd=str(repo_path),
                container_id=None,
                exit_code=None,
                stdout=_as_text(exc.stdout),
                stderr=_as_text(exc.stderr),
                reason=f"timed out after {timeout}s",
            )
            raise

        append_entry(
            repo_path,
            command=args,
            cwd=str(repo_path),
            container_id=None,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        return completed

    try:
        checked = run(["git", "apply", "--check", "-"])
    except subprocess.TimeoutExpired:
        return LiveApplyResult(
            status="failed", detail=f"git apply --check timed out after {timeout}s", paths=paths
        )

    if checked.returncode != 0:
        reason = (checked.stderr or checked.stdout).strip() or "git apply --check failed"
        return LiveApplyResult(
            status="failed",
            detail=f"the diff does not apply to the live tree: {reason}",
            paths=paths,
        )

    try:
        applied = run(["git", "apply", "-"])
    except subprocess.TimeoutExpired:
        return LiveApplyResult(
            status="failed", detail=f"git apply timed out after {timeout}s", paths=paths
        )

    if applied.returncode != 0:
        reason = (applied.stderr or applied.stdout).strip() or "git apply failed"
        return LiveApplyResult(status="failed", detail=reason, paths=paths)

    return LiveApplyResult(
        status="applied",
        detail=(
            f"applied {len(paths)} path(s) to the live tree, left UNCOMMITTED. "
            "The regression gate runs next; only a green full suite earns the checkpoint "
            "commit (design §10.1)."
        ),
        paths=paths,
    )
