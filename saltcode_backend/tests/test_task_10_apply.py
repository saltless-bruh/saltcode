"""Task 10.3/10.4 — the live-tree apply and the Builder's scoped read.

Two invariants carry this task, and both are about what must *not* happen:

* `apply_live` never writes under `tests/**` (REQ-BLD-003). A Builder that can edit its
  own acceptance tests passes every task.
* `read_scoped` never returns a body outside `task.files_affected` (REQ-BLD-002), and
  derives that scope from the contract rather than from its caller.

These run without a container: the live-tree apply deliberately does not use one (the
container makes the live tree read-only by design), so nothing here is skipped on a host
without bubblewrap.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from saltcode.diffs.apply import (
    LiveApplyError,
    WriteScopeViolationError,
    apply_live,
    blocked_paths,
    check_write_scope,
    diff_paths,
)
from saltcode.tools._cli import EXIT_OK, EXIT_USAGE, EXIT_VERDICT_NEGATIVE

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def live_repo(tmp_path: Path) -> Path:
    """A real git repo with one committed source file and one committed spec."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "auth.py").write_text("def login():\n    return False\n", encoding="utf-8")
    (repo / "tests" / "task_T1_spec.py").write_text("def test_login():\n    assert login()\n", encoding="utf-8")

    for args in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "base"],
    ):
        subprocess.run(args, cwd=repo, check=True, capture_output=True)
    return repo


GOOD_DIFF = """--- a/src/auth.py
+++ b/src/auth.py
@@ -1,2 +1,2 @@
 def login():
-    return False
+    return True
"""

TEST_DIFF = """--- a/tests/task_T1_spec.py
+++ b/tests/task_T1_spec.py
@@ -1,2 +1,2 @@
 def test_login():
-    assert login()
+    assert True
"""


# ------------------------------------------------------------------- path extraction


def test_paths_are_read_from_every_diff_header_form() -> None:
    """`--- a/x` is not the only way a diff names a file."""
    assert diff_paths("--- a/src/x.py\n+++ b/src/x.py\n") == ["src/x.py"]
    assert diff_paths("--- src/x.py\n+++ src/x.py\n") == ["src/x.py"]
    assert "src/x.py" in diff_paths("diff --git a/src/x.py b/src/x.py\n")


def test_dev_null_is_not_a_path() -> None:
    """It is how a diff spells "did not exist", not somewhere anything is written."""
    assert diff_paths("--- /dev/null\n+++ b/src/new.py\n") == ["src/new.py"]


def test_a_rename_into_tests_is_caught() -> None:
    """A move carries no `+++ b/tests/...` line, so a header-only check misses it.

    This is the reachable bypass: `git format-patch` spells a rename with `rename to`,
    and a Builder diff that moved a source file onto a spec would have been applied.
    """
    diff = (
        "diff --git a/src/auth.py b/tests/task_T1_spec.py\n"
        "similarity index 100%\n"
        "rename from src/auth.py\n"
        "rename to tests/task_T1_spec.py\n"
    )
    assert "tests/task_T1_spec.py" in blocked_paths(diff)


def test_a_nested_suite_is_still_tests() -> None:
    """`src/pkg/tests/` is where Python and Go projects usually keep them."""
    assert blocked_paths("--- a/src/pkg/tests/x_test.go\n+++ b/src/pkg/tests/x_test.go\n")


def test_the_saltcode_spec_directory_is_blocked_too() -> None:
    assert blocked_paths("--- a/.saltcode/tests/task_T1_spec.py\n+++ b/.saltcode/tests/task_T1_spec.py\n")


def test_source_paths_are_not_blocked() -> None:
    assert blocked_paths(GOOD_DIFF) == []
    assert blocked_paths("--- a/src/latest/x.py\n+++ b/src/latest/x.py\n") == []


def test_check_write_scope_names_every_offender() -> None:
    """The Builder retry has to say which file, not just "denied"."""
    with pytest.raises(WriteScopeViolationError, match="tests/task_T1_spec.py"):
        check_write_scope(TEST_DIFF)


# --------------------------------------------------------------------- applying live


def test_a_passed_diff_lands_on_the_live_tree(live_repo: Path) -> None:
    """The Done-when leg: a passed diff reaches the working tree."""
    result = apply_live(live_repo, GOOD_DIFF)
    assert result.ok, result.detail
    assert "return True" in (live_repo / "src" / "auth.py").read_text(encoding="utf-8")


def test_the_apply_is_left_uncommitted(live_repo: Path) -> None:
    """Design §10.1: the commit is the checkpoint, and the regression gate comes first.

    Committing here would turn a regression failure into a `git revert` instead of a
    `git reset --hard <last checkpoint>`, and would record a task the loop has not yet
    decided is finished.
    """
    result = apply_live(live_repo, GOOD_DIFF)
    assert result.committed is False

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=live_repo, capture_output=True, text=True, check=True
    )
    assert "src/auth.py" in status.stdout, "the change must be present and unstaged/uncommitted"

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=live_repo, capture_output=True, text=True, check=True
    )
    assert len(log.stdout.strip().splitlines()) == 1, "no commit may have been made"


def test_a_tests_hunk_is_refused_and_changes_nothing(live_repo: Path) -> None:
    """REQ-BLD-003, and the file must be byte-identical afterwards."""
    spec = live_repo / "tests" / "task_T1_spec.py"
    before = spec.read_bytes()

    result = apply_live(live_repo, TEST_DIFF)

    assert result.status == "refused"
    assert result.blocked_paths == ["tests/task_T1_spec.py"]
    assert spec.read_bytes() == before


def test_a_refusal_is_reported_before_git_is_asked(live_repo: Path) -> None:
    """A `tests/**` write must not surface as "the patch did not apply".

    That reads as a Builder formatting problem and routes to the wrong retry. The diff
    used here applies cleanly, so a "failed" status could only come from the wrong check
    running first.
    """
    result = apply_live(live_repo, TEST_DIFF)
    assert result.status == "refused"
    assert "REQ-BLD-003" in result.detail


def test_a_mixed_diff_is_refused_whole(live_repo: Path) -> None:
    """One forbidden hunk poisons the diff; there is no partial apply."""
    source = live_repo / "src" / "auth.py"
    before = source.read_bytes()

    result = apply_live(live_repo, GOOD_DIFF + TEST_DIFF)

    assert result.status == "refused"
    assert source.read_bytes() == before, "the allowed half must not land either"


def test_a_diff_that_does_not_apply_is_a_failure_not_a_refusal(live_repo: Path) -> None:
    stale = GOOD_DIFF.replace("return False", "return MAYBE")
    result = apply_live(live_repo, stale)
    assert result.status == "failed"
    assert result.blocked_paths == []


def test_applying_outside_a_git_repo_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(LiveApplyError, match="not a git repository"):
        apply_live(tmp_path, GOOD_DIFF)


def test_the_apply_is_recorded_in_the_audit_log(live_repo: Path) -> None:
    """REQ-SEC-003. A write to the live tree is the last thing that should be missing."""
    from saltcode.harness.audit_log import read_entries

    apply_live(live_repo, GOOD_DIFF)
    assert (live_repo / ".saltcode" / "audit_log.jsonl").is_file()

    commands = [str(e["command"]) for e in read_entries(live_repo)]
    assert any("git apply --check" in c for c in commands), commands
    assert any(c.endswith("git apply -") for c in commands), commands


# ------------------------------------------------------------- apply_live entrypoint


def run_apply(*args: str, stdin: str | None = None) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.apply_live", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        input=stdin,
        timeout=300,
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


def test_entrypoint_applies_and_exits_zero(live_repo: Path) -> None:
    code, payload = run_apply("--repo", str(live_repo), "--in", "-", stdin=GOOD_DIFF)
    assert code == EXIT_OK, payload
    assert payload["status"] == "applied"
    assert payload["committed"] is False


def test_entrypoint_refusal_is_a_verdict_not_a_crash(live_repo: Path) -> None:
    code, payload = run_apply("--repo", str(live_repo), "--in", "-", stdin=TEST_DIFF)
    assert code == EXIT_VERDICT_NEGATIVE
    assert payload["status"] == "refused"
    assert payload["blocked_paths"] == ["tests/task_T1_spec.py"]


def test_entrypoint_rejects_an_empty_diff(live_repo: Path) -> None:
    code, payload = run_apply("--repo", str(live_repo), "--in", "-", stdin="   \n")
    assert code == EXIT_USAGE
    assert payload["ok"] is False


def test_entrypoint_help_exits_zero() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.apply_live", "--help"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
        check=False,
    )
    assert completed.returncode == EXIT_OK


# ------------------------------------------------------- 10.4 the scoped read


def write_tasks(repo: Path, files_affected: list[str]) -> Path:
    path = repo / ".saltcode" / "tasks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "tasks": [
                    {
                        "id": "T1",
                        "description": "make login work",
                        "files_affected": files_affected,
                        "acceptance_criteria": ["login returns True"],
                        "depends_on": [],
                        "complexity": "low",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def run_read(*args: str) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.read_scoped", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


def test_a_file_in_scope_is_returned(live_repo: Path) -> None:
    """The Done-when leg: `read_scoped` honours `task.files_affected`."""
    write_tasks(live_repo, ["src/auth.py"])
    code, payload = run_read("--repo", str(live_repo), "--task-id", "T1", "--path", "src/auth.py")
    assert code == EXIT_OK, payload
    assert payload["verdict"] == "in_scope"
    assert "def login" in payload["content"]


def test_a_file_outside_scope_is_refused(live_repo: Path) -> None:
    """REQ-BLD-002: not design.md, not a sibling task's files, not the rest of the repo."""
    (live_repo / "src" / "billing.py").write_text("SECRET = 1\n", encoding="utf-8")
    write_tasks(live_repo, ["src/auth.py"])

    code, payload = run_read("--repo", str(live_repo), "--task-id", "T1", "--path", "src/billing.py")

    assert code == EXIT_VERDICT_NEGATIVE
    assert payload["verdict"] == "out_of_scope"
    assert "content" not in payload, "a refusal must not leak the body it refused"


def test_escaping_the_workspace_is_refused(live_repo: Path) -> None:
    write_tasks(live_repo, ["src/auth.py"])
    code, payload = run_read("--repo", str(live_repo), "--task-id", "T1", "--path", "../../etc/passwd")
    assert code == EXIT_VERDICT_NEGATIVE
    assert payload["verdict"] == "out_of_scope"


def test_the_scope_cannot_be_widened_by_the_caller(live_repo: Path) -> None:
    """The re-check reads the contract; there is no "here is my allowed list" argument.

    A backend layer that trusted the caller's copy of the scope would not be an
    independent check at all (`.claude/rules/privacy-boundary.md`).
    """
    write_tasks(live_repo, ["src/auth.py"])
    (live_repo / "src" / "billing.py").write_text("SECRET = 1\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "saltcode.tools.read_scoped",
            "--repo",
            str(live_repo),
            "--task-id",
            "T1",
            "--path",
            "src/billing.py",
            "--files",
            "src/billing.py",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
        check=False,
    )
    assert completed.returncode == EXIT_USAGE, "there must be no --files escape hatch"
    assert "SECRET" not in completed.stdout


def test_a_missing_tasks_file_refuses_rather_than_reading_everything(live_repo: Path) -> None:
    """No contract means no scope to enforce, which must fail closed."""
    code, payload = run_read("--repo", str(live_repo), "--task-id", "T1", "--path", "src/auth.py")
    assert code == EXIT_USAGE
    assert payload["ok"] is False
    assert "tasks.json" in payload["detail"]


def test_an_unknown_task_id_is_a_usage_error(live_repo: Path) -> None:
    write_tasks(live_repo, ["src/auth.py"])
    code, payload = run_read("--repo", str(live_repo), "--task-id", "T9", "--path", "src/auth.py")
    assert code == EXIT_USAGE
    assert "T9" in payload["detail"]


def test_an_in_scope_file_that_does_not_exist_is_distinguishable(live_repo: Path) -> None:
    """"You may look but it is not there" is a different answer from "you may not"."""
    write_tasks(live_repo, ["src/new_module.py"])
    code, payload = run_read("--repo", str(live_repo), "--task-id", "T1", "--path", "src/new_module.py")
    assert code == EXIT_VERDICT_NEGATIVE
    assert payload["verdict"] == "not_readable"


def test_read_scoped_help_exits_zero() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.read_scoped", "--help"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
        check=False,
    )
    assert completed.returncode == EXIT_OK
