"""Task 1.7 — the ``saltcode.tools.*`` CLI entrypoints, exercised via subprocess.

Task 1's Done-when requires that both entrypoints "return correct exit codes when
run via subprocess", so every test here actually spawns the interpreter rather
than calling ``run()`` in-process. That is the contract the extension depends on
(REQ-EXT-004: ``pi.exec("python", ["-m", "saltcode.tools.<name>", ...])``).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from saltcode.tools._cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    EXIT_VERDICT_NEGATIVE,
)

VALID_DIFF = """--- a/hello.txt
+++ b/hello.txt
@@ -1,1 +1,1 @@
-old
+new
"""

VALID_CONTEXT_REPORT = json.dumps(
    {
        "schema_version": "1",
        "existing_patterns": ["uses pydantic v2"],
        "relevant_files": ["saltcode/contracts/tasks.py"],
        "constraints": ["all contracts carry schema_version"],
        "anti_patterns": ["no raw SQL in handlers"],
    }
)


def run_tool(module: str, *args: str, stdin: str = "") -> tuple[int, dict[str, Any]]:
    """Run ``python -m saltcode.tools.<module>`` and parse its JSON stdout."""
    completed = subprocess.run(
        [sys.executable, "-m", f"saltcode.tools.{module}", *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    payload: dict[str, Any] = {}
    if completed.stdout.strip():
        payload = json.loads(completed.stdout)
    return completed.returncode, payload


# ---------------------------------------------------------------- validate_contract


def test_validate_contract_accepts_valid_payload() -> None:
    code, out = run_tool("validate_contract", "--contract", "context_report", stdin=VALID_CONTEXT_REPORT)
    assert code == EXIT_OK
    assert out["ok"] is True
    assert out["contract"] == "context_report"
    assert out["written"] is False


def test_validate_contract_rejects_malformed_and_writes_nothing(tmp_path: Path) -> None:
    """REQ-GLB-002 AC1: on repeated failure, no partial/garbage file is written."""
    out_path = tmp_path / "context_report.json"
    code, out = run_tool(
        "validate_contract",
        "--contract",
        "context_report",
        "--out",
        str(out_path),
        stdin='{"schema_version": "1", "existing_patterns": "not-a-list"}',
    )
    assert code == EXIT_VERDICT_NEGATIVE
    assert out["ok"] is False
    assert out["written"] is False
    assert out["error"] == "ContractValidationError"
    assert not out_path.exists(), "a rejected contract must leave no file behind"


def test_validate_contract_rejects_prose_by_default() -> None:
    """REQ-GLB-002 AC2: JSON-only agents reject prose-only responses."""
    code, out = run_tool(
        "validate_contract", "--contract", "context_report", stdin="I could not complete this task."
    )
    assert code == EXIT_VERDICT_NEGATIVE
    assert out["error"] == "NonJSONOutputError"


def test_validate_contract_persists_on_success(tmp_path: Path) -> None:
    out_path = tmp_path / "nested" / "context_report.json"
    code, out = run_tool(
        "validate_contract",
        "--contract",
        "context_report",
        "--out",
        str(out_path),
        stdin=VALID_CONTEXT_REPORT,
    )
    assert code == EXIT_OK
    assert out["written"] is True
    assert out["path"] == str(out_path)
    assert json.loads(out_path.read_text(encoding="utf-8"))["constraints"] == [
        "all contracts carry schema_version"
    ]


def test_validate_contract_rejects_cyclic_depends_on() -> None:
    """REQ-PLN-002 AC1 / REQ-CON-003 AC2 surfaced through the entrypoint."""
    cyclic = json.dumps(
        {
            "schema_version": "1",
            "tasks": [
                {
                    "id": "T1",
                    "description": "a",
                    "files_affected": [],
                    "acceptance_criteria": [],
                    "depends_on": ["T2"],
                    "complexity": "low",
                },
                {
                    "id": "T2",
                    "description": "b",
                    "files_affected": [],
                    "acceptance_criteria": [],
                    "depends_on": ["T1"],
                    "complexity": "low",
                },
            ],
        }
    )
    code, out = run_tool("validate_contract", "--contract", "tasks", stdin=cyclic)
    assert code == EXIT_VERDICT_NEGATIVE
    assert "cycle" in out["detail"].lower()


def test_validate_contract_reads_from_file(tmp_path: Path) -> None:
    payload = tmp_path / "payload.json"
    payload.write_text(VALID_CONTEXT_REPORT, encoding="utf-8")
    code, out = run_tool("validate_contract", "--contract", "context_report", "--in", str(payload))
    assert code == EXIT_OK
    assert out["ok"] is True


def test_validate_contract_unknown_contract_is_usage_error() -> None:
    code, _ = run_tool("validate_contract", "--contract", "not_a_contract", stdin="{}")
    assert code == EXIT_USAGE


def test_validate_contract_missing_input_file_is_error() -> None:
    code, out = run_tool("validate_contract", "--contract", "tasks", "--in", "/nonexistent/x.json")
    assert code == EXIT_ERROR
    assert out["error"] == "InputError"


# ---------------------------------------------------------------------- diff_check


def test_diff_check_accepts_valid_diff() -> None:
    code, out = run_tool("diff_check", "--no-git-check", stdin=VALID_DIFF)
    assert code == EXIT_OK
    assert out["verdict"] == "valid"
    assert out["repaired"] is False


def test_diff_check_rejects_prose_as_impl_fail() -> None:
    """REQ-STAT-005 AC1: non-diff output is impl_fail."""
    code, out = run_tool("diff_check", "--no-git-check", stdin="Here is my implementation plan.")
    assert code == EXIT_VERDICT_NEGATIVE
    assert out["verdict"] == "impl_fail"
    assert out["git_apply_check"] == "not_attempted"


def test_diff_check_repairs_fenced_diff() -> None:
    """REQ-STAT-005 AC2: one bounded repair extracts a diff from a markdown fence."""
    fenced = f"Sure, here you go:\n\n```diff\n{VALID_DIFF.rstrip()}\n```\n"
    code, out = run_tool("diff_check", "--no-git-check", stdin=fenced)
    assert code == EXIT_OK
    assert out["repaired"] is True


def test_diff_check_git_apply_passes_on_real_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "hello.txt").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "hello.txt"], cwd=repo, check=True)

    code, out = run_tool("diff_check", "--repo", str(repo), stdin=VALID_DIFF)
    assert code == EXIT_OK
    assert out["git_apply_check"] == "pass"


def test_diff_check_git_apply_fails_when_diff_does_not_apply(tmp_path: Path) -> None:
    """A structurally valid diff that does not apply is still impl_fail."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "hello.txt").write_text("something else entirely\n", encoding="utf-8")
    subprocess.run(["git", "add", "hello.txt"], cwd=repo, check=True)

    code, out = run_tool("diff_check", "--repo", str(repo), stdin=VALID_DIFF)
    assert code == EXIT_VERDICT_NEGATIVE
    assert out["verdict"] == "impl_fail"
    assert out["git_apply_check"] == "fail"


def test_diff_check_reports_skip_outside_a_repo(tmp_path: Path) -> None:
    """A check that could not run is reported as skipped, never as a pass."""
    code, out = run_tool("diff_check", "--repo", str(tmp_path), stdin=VALID_DIFF)
    assert code == EXIT_OK
    assert out["git_apply_check"] == "skipped"
    assert "not a git repository" in out["detail"]


def test_diff_check_preserves_trailing_blank_context_line(tmp_path: Path) -> None:
    """A context line for a blank source line is a single space and must survive.

    Stripping the payload deletes such a line when it lands at the end, leaving
    the hunk one line short of its declared count; `git apply` then rejects the
    patch as *corrupt* rather than applying it. Most files end in a blank line,
    so this is the common case, not a corner case.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    # Note the file's trailing blank line.
    (repo / "notes.md").write_text("alpha\n\n", encoding="utf-8")
    subprocess.run(["git", "add", "notes.md"], cwd=repo, check=True)

    # The final line is a context line holding exactly one space.
    diff = "--- a/notes.md\n+++ b/notes.md\n@@ -1,2 +1,2 @@\n-alpha\n+omega\n \n"
    assert diff.endswith(" \n")

    code, out = run_tool("diff_check", "--repo", str(repo), stdin=diff)
    assert code == EXIT_OK, out
    assert out["git_apply_check"] == "pass"
    assert out["repaired"] is False, "a well-formed diff must not be reported as repaired"


def test_diff_check_does_not_report_repair_for_clean_input() -> None:
    code, out = run_tool("diff_check", "--no-git-check", stdin=VALID_DIFF)
    assert code == EXIT_OK
    assert out["repaired"] is False


def test_diff_check_strips_leading_prose() -> None:
    code, out = run_tool(
        "diff_check", "--no-git-check", stdin=f"Sure! Here is the patch:\n\n{VALID_DIFF}"
    )
    assert code == EXIT_OK
    assert out["repaired"] is True


def test_diff_check_writes_extracted_diff(tmp_path: Path) -> None:
    out_path = tmp_path / "clean.patch"
    fenced = f"```diff\n{VALID_DIFF.rstrip()}\n```\n"
    code, out = run_tool(
        "diff_check", "--no-git-check", "--out", str(out_path), stdin=fenced
    )
    assert code == EXIT_OK
    assert out["written"] is True
    assert out_path.read_text(encoding="utf-8").startswith("--- a/hello.txt")


def test_diff_check_does_not_write_on_rejection(tmp_path: Path) -> None:
    out_path = tmp_path / "clean.patch"
    code, _ = run_tool(
        "diff_check", "--no-git-check", "--out", str(out_path), stdin="not a diff"
    )
    assert code == EXIT_VERDICT_NEGATIVE
    assert not out_path.exists()
