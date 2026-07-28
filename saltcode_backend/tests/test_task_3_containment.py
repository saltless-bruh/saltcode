"""Task 3 — sandbox, security container, command allowlist, audit log.

Organised around Task 3's Done-when legs:

1. the sandbox applies a diff and runs a command inside a container with no
   network and a read-only host filesystem;
2. a malicious ``os.system('rm -rf ~/')`` does NOT affect the host;
3. a non-allowlisted command is refused **and logged**;
4. if no containment backend is found, the backend refuses Phase 2;
5. the scope probe returns a sorted module list via subprocess — **not covered
   here**; sub-step 3.2 is open pending a spec question (see `specs/tasks.md`).

Leg 2 is exercised the way it actually happens: the destructive code arrives as
a *test file* and is executed by the real test runner inside the container, not
as a command Saltcode was asked to run. Anything less proves less.

Container tests skip when no backend is usable, so a host without bubblewrap
reports "not verified" rather than a false green. The suite records which
backend it ran under.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from saltcode.harness.audit_log import append_entry, audit_log_path, hash_output, read_entries
from saltcode.harness.command_allowlist import (
    DEFAULT_ALLOWLIST,
    check_command,
    normalize_allowlist,
)
from saltcode.harness.sandbox import (
    CONTAINER_HOME,
    ContainerLimits,
    NoContainmentBackendError,
    apply_diff,
    detect_containment_backend,
    disposable_sandbox,
    git_bind_args,
    require_containment_backend,
    run_in_container,
)
from saltcode.tools._cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, EXIT_VERDICT_NEGATIVE

if TYPE_CHECKING:
    from collections.abc import Iterator

BACKEND = detect_containment_backend()

needs_container = pytest.mark.skipif(
    BACKEND is None,
    reason="no containment backend (bwrap/docker/firejail) is usable on this host",
)

# The interpreter running the suite; bound read-only so allowlisted tools resolve.
TOOLCHAIN = Path(sys.prefix).resolve()
CONTAINER_PATH = f"{TOOLCHAIN}/bin:/usr/local/bin:/usr/bin:/bin"
CONTAINER_ENV = {
    "PATH": CONTAINER_PATH,
    "HOME": CONTAINER_HOME,
    "TMPDIR": "/tmp",
    "LANG": "C.UTF-8",
    "TERM": "dumb",
    "PYTHONDONTWRITEBYTECODE": "1",
}

SIMPLE_DIFF = "--- a/f.txt\n+++ b/f.txt\n@@ -1 +1 @@\n-alpha\n+omega\n"


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A one-commit git repo, so the sandbox takes the worktree path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "f.txt").write_text("alpha\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def commit_probe(repo: Path, name: str, source: str) -> None:
    """Write a probe test into the repo and commit it.

    The commit is not incidental: the sandbox is a worktree checked out at HEAD,
    so an uncommitted file simply is not there. (Worth remembering at Task 9 —
    Test Intent writes `tests/task_{id}_spec.*` to the *live* tree, and the test
    runner will need those specs to reach the sandbox somehow.)
    """
    probe_dir = repo / "tests"
    probe_dir.mkdir(exist_ok=True)
    (probe_dir / name).write_text(source, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", f"probe {name}"], cwd=repo, check=True)


@pytest.fixture
def host_canary(tmp_path: Path) -> Iterator[Path]:
    """A file on the host, outside the sandbox, that must survive everything."""
    canary = tmp_path / "outside" / "canary.txt"
    canary.parent.mkdir(parents=True)
    canary.write_text("must survive\n", encoding="utf-8")
    yield canary


def run_tool(*args: str, stdin: str = "") -> tuple[int, dict[str, Any]]:
    """Run ``python -m saltcode.tools.sandbox_apply`` and parse its JSON stdout."""
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.sandbox_apply", *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    payload: dict[str, Any] = {}
    if completed.stdout.strip():
        payload = json.loads(completed.stdout)
    return completed.returncode, payload


# ------------------------------------------------------------ leg 3: command allowlist


def test_default_allowlist_is_exactly_the_requirement() -> None:
    """REQ-SEC-002 fixes the list; drift here is a spec change, not a tweak."""
    assert {" ".join(entry) for entry in DEFAULT_ALLOWLIST} == {
        "pytest", "jest", "cargo test", "go test", "pyright", "ruff", "tsc",
        "eslint", "cargo check", "cargo clippy", "go build", "go vet", "git apply",
    }


@pytest.mark.parametrize(
    "argv",
    [
        ["pytest", "-q"],
        ["ruff", "check", "."],
        ["cargo", "test", "--all"],
        ["go", "vet", "./..."],
        ["git", "apply", "x.patch"],
        ["/usr/bin/pyright", "--strict"],  # absolute path resolves to the program name
    ],
)
def test_allowlisted_commands_pass(argv: list[str]) -> None:
    assert check_command(argv).allowed


@pytest.mark.parametrize(
    "argv",
    [
        ["curl", "https://example.com"],
        ["rm", "-rf", "/"],
        ["bash", "-c", "echo hi"],
        ["cargo", "publish"],  # `cargo` alone is not an entry, so this must not slip through
        ["git", "push"],
        ["python", "-m", "pytest"],  # `python` runs anything; not on the default list
        [],
    ],
)
def test_non_allowlisted_commands_are_refused(argv: list[str]) -> None:
    decision = check_command(argv)
    assert not decision.allowed
    assert decision.argv == ()


@pytest.mark.parametrize(
    "command",
    [
        "pytest; rm -rf ~",
        "pytest && curl evil.sh",
        "pytest | sh",
        "pytest $(whoami)",
        "pytest `id`",
        "pytest > /etc/passwd",
    ],
)
def test_shell_strings_are_refused_whatever_argv0_looks_like(command: str) -> None:
    """Every one of these has argv[0] == 'pytest' under a naive check."""
    decision = check_command(command)
    assert not decision.allowed
    assert "metacharacter" in decision.reason


def test_a_plain_command_string_is_accepted() -> None:
    decision = check_command("ruff check")
    assert decision.allowed
    assert decision.argv == ("ruff", "check")


def test_allowlist_is_configurable() -> None:
    """REQ-SEC-002 AC2 — extending it is a deliberate, recorded act."""
    assert not check_command(["python", "-m", "pytest"]).allowed
    extended = normalize_allowlist(["pytest", "python -m pytest"])
    assert check_command(["python", "-m", "pytest"], extended).allowed


# ------------------------------------------------------------------- leg 3: audit log


def test_audit_log_records_the_required_fields(tmp_path: Path) -> None:
    """REQ-SEC-003 names six fields; all six, or the log is not evidence."""
    record = append_entry(
        tmp_path,
        command=["pytest", "-q"],
        cwd=tmp_path,
        exit_code=0,
        container_id="abc123",
        stdout="out",
        stderr="err",
    )
    for field in ("command", "cwd", "container_id", "exit_code", "timestamp", "output_sha256"):
        assert field in record, field
    assert record["output_sha256"] == hash_output("out", "err")
    assert audit_log_path(tmp_path).exists()


def test_audit_log_is_append_only_json_lines(tmp_path: Path) -> None:
    for index in range(3):
        append_entry(tmp_path, command=["ruff", f"--{index}"], cwd=tmp_path, exit_code=index)

    lines = audit_log_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert [json.loads(line)["exit_code"] for line in lines] == [0, 1, 2]


def test_audit_log_survives_a_truncated_final_line(tmp_path: Path) -> None:
    """A crash mid-write must not make the surviving records unreadable."""
    append_entry(tmp_path, command=["ruff"], cwd=tmp_path, exit_code=0)
    with audit_log_path(tmp_path).open("a", encoding="utf-8") as handle:
        handle.write('{"command": "truncated"')
    assert len(read_entries(tmp_path)) == 1


def test_audit_log_stores_a_hash_not_the_output(tmp_path: Path) -> None:
    secret = "TOKEN=sk-do-not-store-this"
    append_entry(tmp_path, command=["pytest"], cwd=tmp_path, exit_code=0, stdout=secret)
    assert secret not in audit_log_path(tmp_path).read_text(encoding="utf-8")


@needs_container
def test_a_refused_command_is_logged_as_refused(tmp_path: Path) -> None:
    """REQ-SEC-002 AC1: refused *and logged* — the refusal is the record that matters."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    result = run_in_container(["curl", "https://example.com"], sandbox=sandbox, workspace=tmp_path)
    assert result.refused
    assert result.exit_code != 0

    entries = read_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["refused"] is True
    assert entries[0]["exit_code"] is None, "nothing ran, so there is no exit code to report"
    assert "curl" in entries[0]["command"]


@needs_container
def test_a_refused_command_never_reaches_a_container(tmp_path: Path) -> None:
    """The allowlist is checked before a container is built, so refusal is free."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    result = run_in_container("rm -rf /", sandbox=sandbox, workspace=tmp_path)
    assert result.refused
    assert result.container_id == "", "no container should have been created"
    assert result.backend == "none"


# ---------------------------------------------------------- leg 4: refuse without a backend


def test_no_backend_refuses_phase_2(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-SEC-005 AC1 — refuse and name what to install; never run uncontained."""
    monkeypatch.setattr("saltcode.harness.sandbox.shutil.which", lambda _name: None)
    monkeypatch.setattr("saltcode.harness.sandbox._PROBE_CACHE", {})

    assert detect_containment_backend() is None
    with pytest.raises(NoContainmentBackendError) as exc_info:
        require_containment_backend()

    message = str(exc_info.value)
    assert "bubblewrap" in message and "Docker" in message and "firejail" in message
    assert "refuses to run" in message


def test_an_installed_but_broken_bwrap_is_not_a_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Presence is not capability: a restricted userns must not read as contained."""
    monkeypatch.setattr("saltcode.harness.sandbox.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("saltcode.harness.sandbox._PROBE_CACHE", {"bwrap": False})
    monkeypatch.setattr("saltcode.config.settings", __import__("saltcode.config", fromlist=["settings"]).settings)

    # firejail is "present" under the patched which(), so bwrap must be skipped for it.
    assert detect_containment_backend() == "firejail"


def test_an_explicit_backend_choice_is_never_silently_downgraded(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-SEC-005 AC2: asking for docker and getting bwrap would be a lie."""
    monkeypatch.setattr(
        "saltcode.harness.sandbox.shutil.which",
        lambda name: None if name == "docker" else "/usr/bin/x",
    )
    monkeypatch.setattr("saltcode.harness.sandbox._PROBE_CACHE", {"bwrap": True})

    assert detect_containment_backend(preferred="docker") is None
    with pytest.raises(NoContainmentBackendError, match="docker"):
        require_containment_backend(preferred="docker")


def test_entrypoint_reports_the_containment_backend() -> None:
    code, out = run_tool("--check-containment")
    if BACKEND is None:
        assert code == EXIT_ERROR
        assert out["error"] == "NoContainmentBackendError"
    else:
        assert code == EXIT_OK
        assert out["backend"] == BACKEND


# ----------------------------------------------- leg 1: sandbox + contained execution


def test_the_sandbox_is_a_worktree_and_the_live_tree_is_untouched(git_repo: Path) -> None:
    with disposable_sandbox(git_repo) as sandbox:
        assert (sandbox / ".git").is_file(), "a git repo should yield a worktree, not a copy"
        (sandbox / "f.txt").write_text("scribbled\n", encoding="utf-8")

    assert (git_repo / "f.txt").read_text(encoding="utf-8") == "alpha\n"


def test_the_sandbox_is_removed_on_exit(git_repo: Path) -> None:
    with disposable_sandbox(git_repo) as sandbox:
        captured = sandbox
    assert not captured.exists()

    listed = subprocess.run(
        ["git", "worktree", "list"], cwd=git_repo, capture_output=True, text=True, check=True
    )
    assert str(captured) not in listed.stdout, "the worktree registration must be pruned too"


def test_git_bind_args_reach_the_worktree_admin_directory(git_repo: Path) -> None:
    with disposable_sandbox(git_repo) as sandbox:
        binds = git_bind_args(sandbox)
        assert "--bind" in binds, "the admin dir must be writable or git cannot resolve the worktree"
        assert "--ro-bind" in binds, "the parent .git must be readable for objects and refs"
        assert any("worktrees" in part for part in binds)


@needs_container
def test_a_diff_applies_inside_the_container(git_repo: Path) -> None:
    with disposable_sandbox(git_repo) as sandbox:
        assert apply_diff(sandbox, SIMPLE_DIFF, workspace=git_repo)
        assert (sandbox / "f.txt").read_text(encoding="utf-8") == "omega\n"

    assert (git_repo / "f.txt").read_text(encoding="utf-8") == "alpha\n", "REQ-STAT-001 AC3"


@needs_container
def test_a_diff_that_does_not_apply_is_reported_not_forced(git_repo: Path) -> None:
    stale = "--- a/f.txt\n+++ b/f.txt\n@@ -1 +1 @@\n-something-else\n+omega\n"
    with disposable_sandbox(git_repo) as sandbox:
        assert not apply_diff(sandbox, stale, workspace=git_repo)


@needs_container
def test_no_network_inside_the_container(git_repo: Path) -> None:
    """REQ-SEC-001 AC2. Run through the test runner, since raw python is not allowlisted."""
    commit_probe(
        git_repo,
        "test_net.py",
        "import socket\n"
        "def test_no_network():\n"
        "    try:\n"
        "        socket.create_connection(('1.1.1.1', 443), timeout=5)\n"
        "    except OSError as exc:\n"
        "        print('REFUSED:', exc)\n"
        "        return\n"
        "    raise AssertionError('NETWORK REACHABLE')\n",
    )

    with disposable_sandbox(git_repo) as sandbox:
        result = run_in_container(
            ["pytest", "-q", "-s", "tests/test_net.py"],
            sandbox=sandbox,
            workspace=git_repo,
            extra_ro_binds=[TOOLCHAIN],
            env=CONTAINER_ENV,
        )
    assert result.exit_code == 0, f"network was reachable inside the container:\n{result.stdout}"
    assert "REFUSED" in result.stdout


@needs_container
def test_the_host_filesystem_is_read_only_outside_the_sandbox(git_repo: Path, host_canary: Path) -> None:
    """REQ-SEC-001: read-only host fs outside the worktree."""
    commit_probe(
        git_repo,
        "test_ro.py",
        "import pathlib\n"
        "def test_readonly():\n"
        f"    target = pathlib.Path({str(host_canary)!r})\n"
        "    try:\n"
        "        target.write_text('overwritten')\n"
        "    except OSError as exc:\n"
        "        print('BLOCKED:', exc)\n"
        "        return\n"
        "    raise AssertionError('WROTE TO THE HOST')\n",
    )

    with disposable_sandbox(git_repo) as sandbox:
        result = run_in_container(
            ["pytest", "-q", "-s", "tests/test_ro.py"],
            sandbox=sandbox,
            workspace=git_repo,
            extra_ro_binds=[TOOLCHAIN],
            env=CONTAINER_ENV,
        )
    assert result.exit_code == 0, result.stdout
    assert host_canary.read_text(encoding="utf-8") == "must survive\n"


@needs_container
def test_the_sandbox_itself_stays_writable(git_repo: Path) -> None:
    """Read-only everywhere would also block the gate from doing its job."""
    commit_probe(
        git_repo,
        "test_rw.py",
        "import pathlib\n"
        "def test_write():\n"
        "    pathlib.Path('artifact.txt').write_text('ok')\n"
        "    assert pathlib.Path('artifact.txt').read_text() == 'ok'\n",
    )
    with disposable_sandbox(git_repo) as sandbox:
        result = run_in_container(
            ["pytest", "-q", "tests/test_rw.py"],
            sandbox=sandbox,
            workspace=git_repo,
            extra_ro_binds=[TOOLCHAIN],
            env=CONTAINER_ENV,
        )
        assert result.exit_code == 0, result.stdout
        assert (sandbox / "artifact.txt").exists()


# --------------------------------------------------------- leg 2: destructive code


@needs_container
def test_rm_rf_home_does_not_affect_the_host(git_repo: Path, host_canary: Path) -> None:
    """REQ-SEC-001 AC1, run the way it would actually happen.

    The destructive code is a *test file* executed by the test runner — the
    Builder cannot ask Saltcode to run `rm`, but it can write a test that does.
    A canary is planted in the real `$HOME` because that is the path the attack
    names; it must be there afterwards.
    """
    home = Path.home()
    home_canary = home / f".saltcode-test-canary-{uuid.uuid4().hex[:8]}"
    home_canary.write_text("must survive\n", encoding="utf-8")

    commit_probe(
        git_repo,
        "test_evil.py",
        "import os, pathlib\n"
        "def test_destroy():\n"
        "    os.system('rm -rf ~/')\n"
        f"    os.system('rm -rf {host_canary.parent}')\n"
        f"    pathlib.Path({str(home_canary)!r}).unlink(missing_ok=True)\n"
        f"    root = pathlib.Path({str(home)!r})\n"
        "    visible = sorted(p.name for p in root.iterdir()) if root.is_dir() else []\n"
        "    print('HOST HOME CONTENTS:', visible)\n"
        "    print('CONTAINER HOME:', os.environ.get('HOME'))\n",
    )

    try:
        with disposable_sandbox(git_repo) as sandbox:
            result = run_in_container(
                ["pytest", "-q", "-s", "tests/test_evil.py"],
                sandbox=sandbox,
                workspace=git_repo,
                extra_ro_binds=[TOOLCHAIN],
                env=CONTAINER_ENV,
            )

        assert home_canary.exists(), "rm -rf ~/ reached the host home"
        assert home_canary.read_text(encoding="utf-8") == "must survive\n"
        assert host_canary.exists(), "a host path outside the sandbox was deleted"
        assert len(list(home.iterdir())) > 1, "the host home was emptied"

        # The home *path* can exist in the container when a bound toolchain lives
        # under it (bwrap creates the intermediate directories to mount it), so
        # the assertion is about contents, which is what actually leaks: nothing
        # of the host home is readable, canary included.
        contents = result.stdout.partition("HOST HOME CONTENTS: ")[2].splitlines()[0]
        assert home_canary.name not in contents, f"host home contents were readable: {contents}"
        assert contents in ("[]", f"['{TOOLCHAIN.relative_to(home).parts[0]}']"), contents
    finally:
        home_canary.unlink(missing_ok=True)


@needs_container
def test_the_container_home_is_not_the_host_home(git_repo: Path) -> None:
    assert CONTAINER_HOME.startswith("/tmp/"), "HOME must live on the container's own tmpfs"


# ------------------------------------------------------------------- resource limits


@needs_container
@pytest.mark.skipif(BACKEND != "bwrap", reason="limit wrapper is exercised via the bwrap path")
def test_memory_limit_kills_a_runaway_allocation(git_repo: Path) -> None:
    """REQ-SEC-001 AC3.

    `MemoryMax` alone does not bind — cgroup v2 swaps instead of killing — so
    this fails unless `MemorySwapMax=0` is set alongside it.
    """
    commit_probe(
        git_repo,
        "test_hog.py",
        "def test_hog():\n    blob = bytearray(400 * 1024 * 1024)\n    assert len(blob)\n",
    )

    with disposable_sandbox(git_repo) as sandbox:
        result = run_in_container(
            ["pytest", "-q", "tests/test_hog.py"],
            sandbox=sandbox,
            workspace=git_repo,
            limits=ContainerLimits(memory_mb=64, cpus=1.0, timeout_seconds=60.0),
            extra_ro_binds=[TOOLCHAIN],
            env=CONTAINER_ENV,
        )

    if not result.limits_enforced:
        pytest.skip("systemd-run is unavailable, so cgroup limits could not be applied")
    assert result.exit_code != 0, "a 400MB allocation under a 64MB cap must not succeed"


@needs_container
@pytest.mark.skipif(BACKEND != "bwrap", reason="timeout kill is exercised via the bwrap path")
def test_timeout_kills_the_container(git_repo: Path) -> None:
    commit_probe(
        git_repo, "test_slow.py", "import time\ndef test_slow():\n    time.sleep(120)\n"
    )

    with disposable_sandbox(git_repo) as sandbox:
        result = run_in_container(
            ["pytest", "-q", "tests/test_slow.py"],
            sandbox=sandbox,
            workspace=git_repo,
            limits=ContainerLimits(timeout_seconds=5.0),
            extra_ro_binds=[TOOLCHAIN],
            env=CONTAINER_ENV,
        )

    assert result.timed_out
    assert result.exit_code == 124

    survivors = subprocess.run(
        ["pgrep", "-fa", "test_slow.py"], capture_output=True, text=True, check=False
    )
    assert "test_slow.py" not in survivors.stdout, "the container outlived its kill"


# ---------------------------------------------------------------- the entrypoint


@needs_container
def test_entrypoint_applies_a_diff_via_subprocess(git_repo: Path) -> None:
    code, out = run_tool("--repo", str(git_repo), stdin=SIMPLE_DIFF)
    assert code == EXIT_OK, out
    assert out["verdict"] == "applied"
    assert out["backend"] == BACKEND
    assert (git_repo / "f.txt").read_text(encoding="utf-8") == "alpha\n", "the live tree must be untouched"


@needs_container
def test_entrypoint_keeps_the_sandbox_when_asked(git_repo: Path) -> None:
    code, out = run_tool("--repo", str(git_repo), "--keep", stdin=SIMPLE_DIFF)
    assert code == EXIT_OK
    kept = Path(out["sandbox"])
    try:
        assert kept.is_dir()
        assert (kept / "f.txt").read_text(encoding="utf-8") == "omega\n"
    finally:
        subprocess.run(["rm", "-rf", str(kept)], check=False)


@needs_container
def test_entrypoint_reports_impl_fail_for_a_stale_diff(git_repo: Path) -> None:
    stale = "--- a/f.txt\n+++ b/f.txt\n@@ -1 +1 @@\n-nope\n+omega\n"
    code, out = run_tool("--repo", str(git_repo), stdin=stale)
    assert code == EXIT_VERDICT_NEGATIVE
    assert out["verdict"] == "impl_fail"


def test_entrypoint_rejects_an_empty_diff(git_repo: Path) -> None:
    code, out = run_tool("--repo", str(git_repo), stdin="   \n")
    assert code == EXIT_VERDICT_NEGATIVE
    assert out["verdict"] == "impl_fail"


def test_entrypoint_rejects_a_missing_repo() -> None:
    code, out = run_tool("--repo", "/nonexistent/repo", stdin=SIMPLE_DIFF)
    assert code == EXIT_ERROR
    assert out["error"] == "InputError"


def test_entrypoint_rejects_an_unknown_backend() -> None:
    code, _ = run_tool("--backend", "nope")
    assert code == EXIT_USAGE
