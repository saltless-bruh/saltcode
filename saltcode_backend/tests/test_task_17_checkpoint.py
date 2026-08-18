"""Task 17 — the regression gate, the checkpoint commit + record, and rollback.

Done-when: *"the full suite runs green / fails correctly on the live tree via subprocess;
a checkpoint commit + record is produced; rollback resets to a named sha and refuses on a
dirty non-Saltcode tree; correct exit codes throughout."*

The container legs skip when no containment backend is usable, the same shape Task 9 uses.
Everything else — the ledger, the refusals, the exit codes — is host-independent and
always runs, which matters because those are where the two defects this task actually hit
live: a rollback that deleted the whole checkpoint history, and a regression gate that
reported a green suite as a red tree because `pytest` was not on the container's PATH.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from saltcode.checkpoint.checkpoint import (
    CHECKPOINTS_FILE,
    CheckpointError,
    CheckpointRefusedError,
    commit_message,
    latest_record,
    read_records,
    write_checkpoint,
)
from saltcode.checkpoint.regression import (
    RegressionResult,
    extract_failing_files,
    run_regression,
)
from saltcode.checkpoint.rollback import (
    LOSS_WARNING,
    ROLLED_BACK_FILE,
    inspect_tree,
    resolve_target,
    rollback,
)
from saltcode.harness.audit_log import AUDIT_LOG_RELATIVE_PATH
from saltcode.harness.sandbox import detect_containment_backend
from saltcode.tools._cli import EXIT_OK, EXIT_USAGE, EXIT_VERDICT_NEGATIVE

BACKEND = detect_containment_backend()

needs_container = pytest.mark.skipif(
    BACKEND is None,
    reason="no containment backend (bwrap/docker/firejail) is usable on this host",
)


# ------------------------------------------------------------------------- fixtures


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with one commit, standing in for the live tree."""
    path = tmp_path / "live"
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "t")
    (path / "app.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "base")
    return path


def run_module(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", f"saltcode.tools.{name}", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[1],
    )


def payload(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(completed.stdout)


def write_suite(repo: Path, *, passing: bool) -> None:
    tests = repo / "tests"
    tests.mkdir(exist_ok=True)
    body = "def test_it():\n    assert 1 == 1\n" if passing else "def test_it():\n    assert 1 == 2\n"
    (tests / "test_suite.py").write_text(body, encoding="utf-8")


# ================================================================== 17.1 regression


def test_no_regression_cmd_is_a_skip_not_a_pass(repo: Path) -> None:
    """REQ-CKP-002 AC3. `ok` is true so the loop proceeds, but `pass` it is not."""
    result = run_regression(repo, regression_cmd=None)
    assert result.outcome == "skipped"
    assert result.ok is False
    assert "unverified" in result.detail


def test_an_empty_regression_cmd_is_also_a_skip(repo: Path) -> None:
    assert run_regression(repo, regression_cmd="   ").outcome == "skipped"


def test_a_non_allowlisted_regression_cmd_is_unavailable_not_fail(repo: Path) -> None:
    """A misconfiguration must never route as a red suite — that sends a Builder to fix
    code that was never run (REQ-SEC-002 AC1, REQ-CKP-003)."""
    result = run_regression(repo, regression_cmd="curl https://example.com")
    assert result.outcome == "unavailable"
    assert result.ok is False


@needs_container
def test_a_green_suite_passes_on_the_live_tree(repo: Path) -> None:
    """The Done-when's first leg. Also the toolchain-binding regression: before the fix
    this came back `fail` with `bwrap: execvp pytest: No such file or directory`."""
    write_suite(repo, passing=True)
    result = run_regression(repo, regression_cmd="pytest -q")
    assert result.outcome == "pass", result.output
    assert result.ok is True
    assert result.exit_code == 0


@needs_container
def test_a_red_suite_fails_and_names_the_failing_file(repo: Path) -> None:
    write_suite(repo, passing=False)
    result = run_regression(repo, regression_cmd="pytest -q")
    assert result.outcome == "fail"
    assert result.attributable is True
    assert any("test_suite.py" in path for path in result.failing_files)


def test_extract_failing_files_reads_the_common_runners() -> None:
    pytest_out = "FAILED tests/test_auth.py::test_login - AssertionError\n"
    jest_out = " FAIL src/token.test.ts\n"
    go_out = "    handler_test.go:42: want 1, got 2\n"
    rust_out = "thread 'tests::it' panicked at src/lib.rs:88:\n"
    assert extract_failing_files(pytest_out) == ("tests/test_auth.py",)
    assert extract_failing_files(jest_out) == ("src/token.test.ts",)
    assert extract_failing_files(go_out) == ("handler_test.go",)
    assert extract_failing_files(rust_out) == ("src/lib.rs",)


def test_an_unattributable_failure_says_so_rather_than_implying_nothing_broke() -> None:
    """The dangerous default. An empty tuple must read as *unknown*, so REQ-CKP-003 AC1
    flags rather than auto-retrying a Builder against a breakage it cannot see."""
    result = RegressionResult(outcome="fail", detail="", failing_files=())
    assert result.attributable is False
    assert extract_failing_files("something went wrong somewhere") == ()


# ================================================================== 17.2 checkpoint


def test_commit_message_is_prefixed_and_bounded() -> None:
    assert commit_message("T1", "add the refresh path") == "saltcode: T1 — add the refresh path"
    assert commit_message("T1", "") == "saltcode: T1 — task complete"
    long = commit_message("T1", "x" * 200)
    assert len(long.split("—", 1)[1].strip()) == 60


def test_a_checkpoint_commits_and_records(repo: Path) -> None:
    """The Done-when's second leg, in-process."""
    (repo / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    record = write_checkpoint(
        repo,
        task_id="T1",
        description="add feature",
        gate_results={"static": "clean", "tests": "pass"},
        stability=1.0,
        regression="pass",
        sprint_id="S1",
    )
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert record.commit_sha == head
    assert record.regression == "pass"
    assert record.gate_results == {"static": "clean", "tests": "pass"}

    on_disk = read_records(repo)
    assert [r.task_id for r in on_disk] == ["T1"]
    assert latest_record(repo, sprint_id="S1") is not None
    assert latest_record(repo, sprint_id="other") is None


def test_a_checkpoint_never_commits_saltcode_s_own_directory(repo: Path) -> None:
    """Found end to end: with `git add -A`, the ledger entered the project's history and
    the next `git reset --hard` deleted it. The commit records the project's work."""
    (repo / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    write_checkpoint(repo, task_id="T1", regression="pass")
    tracked = git(repo, "ls-files").stdout.split()
    assert not any(name.startswith(".saltcode/") for name in tracked)
    assert (repo / CHECKPOINTS_FILE).is_file()


def test_a_failed_regression_refuses_the_checkpoint(repo: Path) -> None:
    """REQ-CKP-001 AC2. A commit here would turn a discardable reset into a revert."""
    (repo / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    before = git(repo, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(CheckpointRefusedError, match="regression gate failed"):
        write_checkpoint(repo, task_id="T1", regression="fail")
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == before


def test_an_empty_apply_refuses_the_checkpoint(repo: Path) -> None:
    """A checkpoint records work. Nothing staged means the applied diff changed nothing."""
    with pytest.raises(CheckpointRefusedError, match="nothing is staged"):
        write_checkpoint(repo, task_id="T1", regression="pass")


def test_a_non_repository_is_an_error_not_a_refusal(tmp_path: Path) -> None:
    with pytest.raises(CheckpointError, match="not a git repository"):
        write_checkpoint(tmp_path, task_id="T1", regression="pass")


# =================================================================== 17.3 rollback


def make_checkpoint(repo: Path, task_id: str, filename: str) -> str:
    (repo / filename).write_text(f"# {task_id}\n", encoding="utf-8")
    return write_checkpoint(
        repo, task_id=task_id, description=f"add {filename}", regression="pass", sprint_id="S1"
    ).commit_sha


def test_rollback_resets_to_a_named_sha(repo: Path) -> None:
    """The Done-when's third leg."""
    first = make_checkpoint(repo, "T1", "one.py")
    make_checkpoint(repo, "T2", "two.py")

    result = rollback(repo, "T1")
    assert result.ok is True
    assert result.head_after == first
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == first
    assert (repo / "one.py").is_file()
    assert not (repo / "two.py").exists()
    assert result.dropped_commits  # AC2 warns about the commits too, not only the tree


def test_rollback_refuses_a_dirty_non_saltcode_tree(repo: Path) -> None:
    """The Done-when's fourth leg. Refusal is a verdict, not an exception."""
    make_checkpoint(repo, "T1", "one.py")
    make_checkpoint(repo, "T2", "two.py")
    (repo / "app.py").write_text("HUMAN EDIT\n", encoding="utf-8")

    result = rollback(repo, "T1")
    assert result.ok is False
    assert result.tree.blocking == ("app.py",)
    assert (repo / "two.py").is_file(), "a refusal must change nothing"
    assert result.warning == LOSS_WARNING, "AC2's warning belongs on refusals too"


def test_force_proceeds_through_a_dirty_tree(repo: Path) -> None:
    """The loop's normal path after a failed regression discards its own uncommitted apply."""
    first = make_checkpoint(repo, "T1", "one.py")
    make_checkpoint(repo, "T2", "two.py")
    (repo / "app.py").write_text("UNCOMMITTED APPLY\n", encoding="utf-8")

    result = rollback(repo, "T1", force=True)
    assert result.ok is True
    assert result.forced is True
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == first
    assert "HUMAN" not in (repo / "app.py").read_text(encoding="utf-8")


def test_saltcode_paths_and_untracked_files_never_block(repo: Path) -> None:
    """`reset --hard` leaves untracked files alone, so refusing on one would refuse on a
    stray build artifact the reset could not have harmed."""
    make_checkpoint(repo, "T1", "one.py")
    (repo / "scratch.log").write_text("noise\n", encoding="utf-8")

    state = inspect_tree(repo)
    assert state.blocking == ()
    assert "scratch.log" in state.untracked
    assert state.clean_enough is True


def test_rollback_rewinds_the_ledger_and_archives_what_it_supersedes(repo: Path) -> None:
    """The defect this test exists for: the first end-to-end run deleted the whole
    checkpoint history, so resume had nothing to match `git HEAD` against."""
    make_checkpoint(repo, "T1", "one.py")
    make_checkpoint(repo, "T2", "two.py")
    make_checkpoint(repo, "T3", "three.py")

    result = rollback(repo, "T1")
    assert result.superseded_tasks == ("T2", "T3")

    assert [r.task_id for r in read_records(repo)] == ["T1"]
    assert latest_record(repo) is not None
    assert latest_record(repo).task_id == "T1"  # type: ignore[union-attr]

    archived = (repo / ROLLED_BACK_FILE).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["task_id"] for line in archived] == ["T2", "T3"]
    assert all("superseded_by_rollback_to" in json.loads(line) for line in archived)


def test_rollback_logs_to_the_audit_log(repo: Path) -> None:
    """REQ-CKP-009 AC1."""
    make_checkpoint(repo, "T1", "one.py")
    make_checkpoint(repo, "T2", "two.py")
    rollback(repo, "T1")

    entries = [
        json.loads(line)
        for line in (repo / AUDIT_LOG_RELATIVE_PATH).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rollbacks = [entry for entry in entries if entry.get("action") == "rollback"]
    assert len(rollbacks) == 1
    assert rollbacks[0]["task_id"] == "T1"
    assert rollbacks[0]["command"].startswith("git reset --hard ")
    assert rollbacks[0]["superseded_tasks"] == ["T2"]


def test_an_unknown_selector_is_a_refusal_that_names_what_exists(repo: Path) -> None:
    make_checkpoint(repo, "T1", "one.py")
    result = rollback(repo, "T9")
    assert result.ok is False
    assert "T1" in result.detail


def test_last_resolves_to_the_newest_and_a_task_id_to_its_newest(repo: Path) -> None:
    make_checkpoint(repo, "T1", "one.py")
    make_checkpoint(repo, "T2", "two.py")
    make_checkpoint(repo, "T1", "three.py")  # T1 retried after a rollback

    assert resolve_target(repo, "last") is not None
    assert resolve_target(repo, "last").task_id == "T1"  # type: ignore[union-attr]
    target = resolve_target(repo, "T1")
    assert target is not None
    assert target.description == "add three.py"


def test_a_sha_the_repository_has_never_seen_is_refused_and_not_forceable(repo: Path) -> None:
    """The ledger and the repository disagreeing is not a risk the caller can accept."""
    make_checkpoint(repo, "T1", "one.py")
    ledger = repo / CHECKPOINTS_FILE
    record = json.loads(ledger.read_text(encoding="utf-8").strip())
    record["commit_sha"] = "0" * 40
    ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

    result = rollback(repo, "T1", force=True)
    assert result.ok is False
    assert "does not exist in this repository" in result.detail


# ============================================================= entrypoint contract


def test_regression_entrypoint_skips_at_exit_zero(repo: Path) -> None:
    completed = run_module("regression", "--repo", str(repo))
    assert completed.returncode == EXIT_OK
    body = payload(completed)
    assert body["outcome"] == "skipped"
    assert body["checkpoint_regression"] == "unverified"


@needs_container
def test_regression_entrypoint_green_then_red_via_subprocess(repo: Path) -> None:
    """The Done-when says *via subprocess*, because a green suite reported as red was a
    property of the child process's environment, not of `run_regression`."""
    write_suite(repo, passing=True)
    green = run_module("regression", "--repo", str(repo), "--cmd", "pytest -q")
    assert green.returncode == EXIT_OK, green.stdout
    assert payload(green)["outcome"] == "pass"

    write_suite(repo, passing=False)
    red = run_module("regression", "--repo", str(repo), "--cmd", "pytest -q")
    assert red.returncode == EXIT_VERDICT_NEGATIVE
    assert payload(red)["outcome"] == "fail"


def test_regression_entrypoint_reads_the_checkpoint_section(repo: Path) -> None:
    """design §10.1 puts `regression_cmd` under `[checkpoint]`, not `[project]`."""
    (repo / "saltcode.toml").write_text(
        '[project]\nlanguage = "python"\n[checkpoint]\nregression_cmd = "curl http://x"\n',
        encoding="utf-8",
    )
    completed = run_module("regression", "--repo", str(repo))
    assert completed.returncode == EXIT_VERDICT_NEGATIVE
    assert payload(completed)["outcome"] == "unavailable"


def test_checkpoint_entrypoint_commits_and_reports(repo: Path) -> None:
    (repo / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    gates = repo / "gates.json"
    gates.write_text('{"static": "clean"}', encoding="utf-8")

    completed = run_module(
        "checkpoint",
        "--repo", str(repo),
        "--task", "T1",
        "--sprint", "S1",
        "--regression", "pass",
        "--description", "add feature",
        "--stability", "1.0",
        "--in", str(gates),
    )
    assert completed.returncode == EXIT_OK, completed.stdout + completed.stderr
    body = payload(completed)
    assert body["pushed"] is False
    checkpoint = body["checkpoint"]
    assert isinstance(checkpoint, dict)
    assert checkpoint["gate_results"] == {"static": "clean"}
    assert checkpoint["commit_sha"] == git(repo, "rev-parse", "HEAD").stdout.strip()


def test_checkpoint_entrypoint_refuses_a_failed_regression_at_exit_one(repo: Path) -> None:
    (repo / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    completed = run_module(
        "checkpoint", "--repo", str(repo), "--task", "T1", "--regression", "fail"
    )
    assert completed.returncode == EXIT_VERDICT_NEGATIVE
    assert payload(completed)["error"] == "CheckpointRefusedError"


def test_checkpoint_entrypoint_rejects_a_bad_regression_state(repo: Path) -> None:
    completed = run_module(
        "checkpoint", "--repo", str(repo), "--task", "T1", "--regression", "green"
    )
    assert completed.returncode == EXIT_USAGE
    assert payload(completed)["ok"] is False


def test_rollback_entrypoint_resets_and_refuses_with_the_right_codes(repo: Path) -> None:
    make_checkpoint(repo, "T1", "one.py")
    make_checkpoint(repo, "T2", "two.py")
    (repo / "app.py").write_text("HUMAN EDIT\n", encoding="utf-8")

    refused = run_module("rollback", "--repo", str(repo), "--to", "T1")
    assert refused.returncode == EXIT_VERDICT_NEGATIVE
    assert payload(refused)["blocking_paths"] == ["app.py"]
    assert payload(refused)["warning"]

    git(repo, "checkout", "--", "app.py")
    done = run_module("rollback", "--repo", str(repo), "--to", "T1")
    assert done.returncode == EXIT_OK, done.stdout
    assert payload(done)["superseded_tasks"] == ["T2"]
    assert not (repo / "two.py").exists()


def test_rollback_entrypoint_defaults_to_last(repo: Path) -> None:
    make_checkpoint(repo, "T1", "one.py")
    second = make_checkpoint(repo, "T2", "two.py")
    completed = run_module("rollback", "--repo", str(repo))
    assert completed.returncode == EXIT_OK
    assert payload(completed)["head_after"] == second
