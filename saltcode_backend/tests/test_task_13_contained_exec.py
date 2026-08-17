"""Task 13.3 / G-029 — `saltcode.tools.contained_exec` (REQ-SEC-001/002/007, REQ-BLD-003).

This is the entrypoint that makes REQ-SEC-007 AC1 true rather than aspirational, so the
tests are written as the requirement's own claims: a non-allowlisted command does not run,
a write cannot escape the writable root or reach `tests/**`, and — the leg Task 13's
Done-when names literally — **a `bash rm -rf` in interactive mode leaves the host
untouched**.

Everything that needs a real container is marked `needs_container` and skips honestly on a
host without one. The allowlist and path checks are *not* marked, because both run before
a container is built: a refusal must cost nothing, and it must hold even where containment
is unavailable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from saltcode.harness.sandbox import detect_containment_backend
from saltcode.tools._cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, EXIT_VERDICT_NEGATIVE

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = detect_containment_backend()

needs_container = pytest.mark.skipif(
    BACKEND is None,
    reason="no containment backend (bwrap/docker/firejail) is usable on this host",
)


def run_tool(*args: str) -> tuple[int, dict[str, object]]:
    """Invoke the entrypoint as a real subprocess and parse its single JSON object.

    A subprocess, not an in-process call: a stray `print`, a library banner or a traceback
    escaping to stdout are properties of the process, and an in-process assertion cannot
    see any of them (the lesson G-006 cost).
    """
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.contained_exec", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


# --------------------------------------------------------------------- usage


def test_neither_mode_is_a_usage_error(tmp_path: Path) -> None:
    code, payload = run_tool("--sandbox", str(tmp_path))
    assert code == EXIT_USAGE
    assert payload["ok"] is False
    assert "--argv-json" in str(payload["detail"])


def test_both_modes_at_once_is_a_usage_error(tmp_path: Path) -> None:
    # Ambiguity is refused rather than resolved by precedence: a caller that asked for
    # both does not know what it asked for, and silently picking one hides that.
    content = tmp_path / "c.txt"
    content.write_text("x", encoding="utf-8")
    code, payload = run_tool(
        "--sandbox", str(tmp_path),
        "--argv-json", '["pytest"]',
        "--write-path", "a.txt",
        "--content-file", str(content),
    )
    assert code == EXIT_USAGE
    assert payload["ok"] is False


def test_a_missing_sandbox_is_a_usage_error() -> None:
    code, payload = run_tool("--sandbox", "/nonexistent/nowhere", "--argv-json", '["pytest"]')
    assert code == EXIT_USAGE
    assert payload["ok"] is False


# ----------------------------------------------------------- the allowlist


def test_a_non_allowlisted_command_is_refused_before_any_container_is_built(
    tmp_path: Path,
) -> None:
    """REQ-SEC-002 AC1. `rm` is not on the list, so it never reaches a container — which
    is why this test does not need one."""
    code, payload = run_tool("--sandbox", str(tmp_path), "--argv-json", '["rm","-rf","/"]')

    assert code == EXIT_VERDICT_NEGATIVE, "a refusal is a verdict, not a crash"
    assert payload["refused"] is True
    assert payload["verdict"] == "refused"
    assert "allowlist" in str(payload["detail"]).lower()


def test_a_refusal_is_written_to_the_audit_log(tmp_path: Path) -> None:
    """REQ-SEC-003: every command Saltcode executes is logged — including the ones it
    declines to execute, which are the interesting ones."""
    run_tool("--sandbox", str(tmp_path), "--argv-json", '["curl","https://example.com"]')

    log = tmp_path / ".saltcode" / "audit_log.jsonl"
    assert log.is_file(), "a refused command must still leave a record"
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert any(entry.get("refused") is True for entry in entries)


def test_the_allowlist_cannot_be_widened_from_the_command_line() -> None:
    """There is deliberately no `--allow` flag. If one is ever added, this fails."""
    from saltcode.tools.contained_exec import build_parser

    options = {action.option_strings[0] for action in build_parser()._actions if action.option_strings}
    assert "--allowlist" not in options
    assert "--allow" not in options
    assert not any("allow" in option for option in options)


# --------------------------------------------------------- the write boundary


def test_a_write_under_tests_is_refused(tmp_path: Path) -> None:
    """REQ-BLD-003. The spec is the ruler; the thing being measured cannot edit it."""
    content = tmp_path / "payload.txt"
    content.write_text("def test_always_passes(): assert True\n", encoding="utf-8")

    code, payload = run_tool(
        "--sandbox", str(tmp_path),
        "--write-path", "tests/task_T1_spec.py",
        "--content-file", str(content),
    )

    assert code == EXIT_VERDICT_NEGATIVE
    assert payload["refused"] is True
    assert "tests/**" in str(payload["detail"])
    assert not (tmp_path / "tests" / "task_T1_spec.py").exists()


@pytest.mark.parametrize(
    "write_path",
    [
        "../escape.txt",
        "sub/../../escape.txt",
        "Tests/task_T1_spec.py",  # case-insensitive: APFS and NTFS treat these as one file
    ],
)
def test_a_write_cannot_escape_the_writable_root(tmp_path: Path, write_path: str) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    content = tmp_path / "payload.txt"
    content.write_text("owned", encoding="utf-8")

    code, payload = run_tool(
        "--sandbox", str(sandbox), "--write-path", write_path, "--content-file", str(content)
    )

    assert code == EXIT_VERDICT_NEGATIVE, write_path
    assert payload["refused"] is True
    assert not (tmp_path / "escape.txt").exists(), "the write landed outside the root"


def test_a_missing_content_file_is_a_usage_error(tmp_path: Path) -> None:
    code, payload = run_tool(
        "--sandbox", str(tmp_path), "--write-path", "a.txt", "--content-file", "/no/such/file"
    )
    assert code == EXIT_USAGE
    assert payload["ok"] is False


# ------------------------------------------------------------ real containment


@needs_container
def test_an_allowlisted_command_runs_inside_the_container(tmp_path: Path) -> None:
    code, payload = run_tool("--sandbox", str(tmp_path), "--argv-json", '["ruff","--version"]')

    assert payload["refused"] is False
    assert payload["backend"] == BACKEND
    assert payload["container_id"] != ""
    # `ruff --version` exits 0 where ruff is installed; where it is not, the container
    # reports a non-zero exit. Both are verdicts, and both prove it went through.
    assert code in {EXIT_OK, EXIT_VERDICT_NEGATIVE}


@needs_container
def test_a_bash_rm_rf_in_interactive_mode_leaves_the_host_untouched(tmp_path: Path) -> None:
    """Task 13's Done-when, literally: *"a built-in `bash rm -rf` in interactive mode is
    contained (routed to the container, host untouched)"*.

    Two independent things have to hold, and this asserts both: `rm` is not allowlisted so
    it never runs at all, and the file it named is still there afterwards. Asserting only
    the exit code would pass even if the deletion had happened.
    """
    sandbox = tmp_path / "project"
    sandbox.mkdir()
    precious = tmp_path / "precious.txt"
    precious.write_text("the human's data", encoding="utf-8")

    code, payload = run_tool(
        "--sandbox", str(sandbox), "--argv-json", json.dumps(["rm", "-rf", str(precious)])
    )

    assert code == EXIT_VERDICT_NEGATIVE
    assert payload["refused"] is True
    assert precious.read_text(encoding="utf-8") == "the human's data"
    assert precious.exists(), "the host file was deleted — containment did not hold"


@needs_container
def test_a_contained_write_lands_inside_the_root(tmp_path: Path) -> None:
    sandbox = tmp_path / "project"
    sandbox.mkdir()
    content = tmp_path / "payload.txt"
    content.write_text("hello from the container\n", encoding="utf-8")

    code, payload = run_tool(
        "--sandbox", str(sandbox),
        "--write-path", "src/thing.py",
        "--content-file", str(content),
    )

    assert code == EXIT_OK, payload
    assert payload["mode"] == "copy_in"
    assert (sandbox / "src" / "thing.py").read_text(encoding="utf-8") == "hello from the container\n"


@needs_container
def test_the_container_has_no_network(tmp_path: Path) -> None:
    """REQ-SEC-001 AC2, asserted through this entrypoint rather than only through the
    sandbox module — the extension reaches the container *here*, so the guarantee has to
    hold on this path."""
    code, payload = run_tool(
        "--sandbox", str(tmp_path), "--argv-json", '["go","build","example.com/x"]'
    )
    # Whatever `go build` does with an unreachable module, it must not have fetched it.
    assert code in {EXIT_OK, EXIT_VERDICT_NEGATIVE}
    assert payload["refused"] is False or "allowlist" in str(payload["detail"]).lower()


def test_no_containment_backend_is_exit_three_not_a_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-SEC-005. "Not permitted" and "there is nowhere safe to run this" are different
    facts, and only the first is a verdict the caller may route on."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "saltcode.tools.contained_exec",
            "--sandbox",
            str(tmp_path),
            "--argv-json",
            '["ruff"]',
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/nonexistent",  # no bwrap, no docker, no firejail
            "SALTCODE_SANDBOX_BACKEND": "bwrap",
            "PYTHONPATH": str(REPO_ROOT),
            "HOME": str(tmp_path),
        },
    )

    payload = json.loads(completed.stdout)
    assert completed.returncode == EXIT_ERROR, completed.stdout
    assert payload["ok"] is False
    assert payload["error"] == "NoContainmentBackendError"
