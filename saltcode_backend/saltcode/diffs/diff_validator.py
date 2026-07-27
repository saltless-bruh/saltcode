"""Unified-diff validation for Builder output (REQ-STAT-005, task 1.6).

Two layers, composed by ``saltcode.tools.diff_check``:

1. :func:`validate_diff` — one bounded repair (extract the diff from a markdown
   fence) followed by a structural parse. Cheap, and needs no repository.
2. :func:`git_apply_check` — the authoritative check REQ-STAT-005 names:
   ``git apply --check``, which additionally proves the diff *applies* to a
   given tree.

Output failing either layer is treated as ``impl_fail`` and counts against the
shared per-task budget; the Auditor is never invoked on it (design §10).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

GIT_APPLY_TIMEOUT_SECONDS = 30
"""Bounded so a pathological diff cannot hang the gate (REQ-STAT-003)."""


_FENCE_RE = re.compile(
    r"^```(?:diff|patch)?[^\n]*\n(?P<body>.*?)\n?^```",
    re.DOTALL | re.MULTILINE | re.IGNORECASE,
)

_DIFF_START_RE = re.compile(r"^(?:diff --git |--- |Index: )")


def extract_diff_from_fences(output: str) -> str:
    """Extract a unified diff from markdown code fences if present, and normalise it.

    The diff body is preserved **byte for byte**; only surrounding prose and
    fence delimiters are removed, and a single trailing newline is guaranteed.

    Two things make this fiddlier than a ``strip()``:

    * A context line for a blank source line is a single space (``" "``).
      Stripping the payload deletes such a line when it lands at the end, which
      leaves the final hunk one line short of its declared count — ``git apply``
      then reports ``corrupt patch at line N``. Since most files end in a blank
      line, this silently corrupted a large share of real diffs.
    * A patch is line-oriented, so the final line must be newline-terminated or
      git rejects it for the same reason.
    """
    match = _FENCE_RE.search(output)
    body = match.group("body") if match else output

    # Drop anything before the first diff marker — surrounding prose, and blank
    # lines that a fenced block often opens with. Everything from that marker
    # onward is kept verbatim, including a trailing space-only context line.
    lines = body.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if _DIFF_START_RE.match(line)), None)
    body = "".join(lines[start:]) if start is not None else body.strip()

    body = body.rstrip("\n")
    return f"{body}\n" if body else ""


def diff_needed_repair(output: str) -> bool:
    """True when the payload was not already a bare, well-formed diff.

    That is: a markdown fence had to be unwrapped, leading prose had to be
    dropped, or a missing trailing newline had to be added. Reported so the
    caller can see that REQ-STAT-005 AC2's single bounded repair was spent.
    """
    return extract_diff_from_fences(output) != output


def validate_diff(output: str) -> tuple[bool, str | None]:
    """Validates if the output is a parseable unified diff.

    Returns (True, clean_diff_content) if valid, or (False, None) if invalid.
    """
    diff_text = extract_diff_from_fences(output)
    lines = diff_text.splitlines()

    has_header_minus = False
    has_header_plus = False
    has_hunk = False
    in_hunk = False

    hunk_pattern = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@")

    for line in lines:
        if line.startswith("--- "):
            has_header_minus = True
            in_hunk = False
        elif line.startswith("+++ "):
            has_header_plus = True
            in_hunk = False
        elif hunk_pattern.match(line):
            has_hunk = True
            in_hunk = True
        elif in_hunk:
            if not line:
                continue
            # Context line, addition, deletion, or diff metadata (e.g. \ No newline at end of file)
            if not line.startswith(("+", "-", " ", "\\")):
                return False, None

    if has_header_minus and has_header_plus and has_hunk:
        return True, diff_text

    return False, None


@dataclass(frozen=True)
class GitApplyCheck:
    """Outcome of ``git apply --check``.

    ``status`` is ``"skipped"`` when the check could not be attempted at all —
    git is absent, or the target is not a git repository. A skip is reported
    explicitly rather than quietly counted as a pass, so a check that did not
    run is always visible in the tool's JSON result.
    """

    status: Literal["pass", "fail", "skipped"]
    detail: str


def git_apply_check(
    diff_text: str,
    repo: Path | str,
    *,
    timeout: int = GIT_APPLY_TIMEOUT_SECONDS,
) -> GitApplyCheck:
    """Run ``git apply --check`` on ``diff_text`` against the tree at ``repo``.

    This never modifies the tree: ``--check`` only reports whether the patch
    *would* apply. Applying for real belongs to the sandbox (task 3.1) and the
    live tree (task 10.3).

    Args:
        diff_text: The unified diff, already extracted from any code fence.
        repo: Working tree to check the diff against.
        timeout: Seconds before the subprocess is killed.

    Returns:
        A :class:`GitApplyCheck` describing pass, fail, or why it was skipped.
    """
    repo_path = Path(repo)

    if shutil.which("git") is None:
        return GitApplyCheck("skipped", "git is not on PATH")
    if not repo_path.is_dir():
        return GitApplyCheck("skipped", f"not a directory: {repo_path}")
    if not (repo_path / ".git").exists():
        return GitApplyCheck("skipped", f"not a git repository: {repo_path}")

    try:
        completed = subprocess.run(
            ["git", "apply", "--check", "-"],
            cwd=repo_path,
            input=diff_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return GitApplyCheck("fail", f"git apply --check timed out after {timeout}s")
    except OSError as exc:  # pragma: no cover - defensive
        return GitApplyCheck("skipped", f"could not run git: {exc}")

    if completed.returncode == 0:
        return GitApplyCheck("pass", "diff applies cleanly")

    reason = (completed.stderr or completed.stdout).strip() or "git apply --check failed"
    return GitApplyCheck("fail", reason)
