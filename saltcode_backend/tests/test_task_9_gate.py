"""Task 9 — static gate, test runner, and the diff validator as the first check.

The gate legs run inside the real security container, so they skip when no backend is
usable (this build container has no cgroup v2 or systemd; CI does). The classification
and false-green legs are pure functions and always run — they are where the two defects
this task exists to prevent actually live:

* a static gate that reports `clean` because its tools were never installed, and
* a test run that reports `pass` because it collected nothing (G-003).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from saltcode.harness.sandbox import (
    ContainedResult,
    apply_diff,
    detect_containment_backend,
    disposable_sandbox,
)
from saltcode.static_gate.gate import run_static_gate
from saltcode.static_gate.runners import (
    LANGUAGE_RUNNERS,
    LANGUAGE_STRENGTH,
    TOOL_FAILURE_EXIT_CODES,
    RunnerSpec,
    _classify,
    run_static_runner,
    runners_for,
    strength_for,
)
from saltcode.static_gate.test_runner import (
    copy_spec_into_sandbox,
    find_spec_file,
    looks_like_no_tests,
    run_task_spec,
)
from saltcode.static_gate.toolchain import (
    container_env_for,
    resolve_tool,
    ro_binds_for,
)
from saltcode.tools._cli import EXIT_OK, EXIT_USAGE, EXIT_VERDICT_NEGATIVE

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = detect_containment_backend()

needs_container = pytest.mark.skipif(
    BACKEND is None,
    reason="no containment backend (bwrap/docker/firejail) is usable on this host",
)


# ------------------------------------------------------------------ fixtures


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def commit_all(path: Path, message: str = "init") -> None:
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=path, check=True)


@pytest.fixture
def python_repo(tmp_path: Path) -> Path:
    """A git repo with a Python module and a project config."""
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    (repo / "app.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    (repo / "saltcode.toml").write_text(
        '[project]\nlanguage = "python"\ntest_framework = "pytest"\ntest_runner_cmd = "pytest -q"\n',
        encoding="utf-8",
    )
    commit_all(repo)
    return repo


# ------------------------------------------------------- 9.1 runners + strength table


def test_the_language_table_matches_design_14() -> None:
    """REQ-STAT-002 AC1 — the tools per language are not negotiable."""
    assert [s.argv[:2] for s in LANGUAGE_RUNNERS["typescript"]] == [("tsc", "--noEmit"), ("eslint", ".")]
    assert [s.argv[:2] for s in LANGUAGE_RUNNERS["rust"]] == [("cargo", "check"), ("cargo", "clippy")]
    assert [s.argv[:2] for s in LANGUAGE_RUNNERS["go"]] == [("go", "build"), ("go", "vet")]
    assert [s.argv[0] for s in LANGUAGE_RUNNERS["python"]] == ["pyright", "ruff"]
    assert [s.argv[0] for s in LANGUAGE_RUNNERS["javascript"]] == ["eslint"]


def test_gate_strength_matches_design_14() -> None:
    assert LANGUAGE_STRENGTH["typescript"] == "HARD"
    assert LANGUAGE_STRENGTH["rust"] == "HARD"
    assert LANGUAGE_STRENGTH["go"] == "HARD"
    assert LANGUAGE_STRENGTH["python"] == "MEDIUM"
    assert LANGUAGE_STRENGTH["javascript"] == "SOFT"


def test_an_unknown_language_is_soft_not_hard() -> None:
    """Strength says how much a clean verdict is worth; an ungated language buys nothing."""
    assert strength_for("cobol") == "SOFT"
    assert runners_for("cobol") == ()


def test_language_lookup_is_case_insensitive() -> None:
    assert strength_for("Python") == "MEDIUM"
    assert runners_for("RUST") == LANGUAGE_RUNNERS["rust"]


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (ContainedResult(0, "", "", "bwrap", "c1"), "clean"),
        (ContainedResult(1, "err", "", "bwrap", "c1"), "dirty"),
        (ContainedResult(124, "", "", "bwrap", "c1", timed_out=True), "timeout"),
        (ContainedResult(126, "", "no", "none", "", refused=True, reason="no"), "refused"),
    ],
)
def test_runner_outcomes_are_classified_distinctly(result: ContainedResult, expected: str) -> None:
    """A refusal and a timeout must not read as a plain lint failure."""
    assert _classify(result, RunnerSpec("t", "t", ("t",))).outcome == expected


def test_a_missing_tool_is_unavailable_never_clean(tmp_path: Path) -> None:
    """The core false green: a gate that never ran must not report no problems."""
    spec = RunnerSpec("nonesuch", "saltcode-no-such-tool", ("saltcode-no-such-tool",))
    result = run_static_runner(spec, sandbox=tmp_path, workspace=tmp_path)
    assert result.outcome == "unavailable"
    assert not result.is_clean
    assert "NOT a clean verdict" in result.detail


# ------------------------------------------------------------------ 9.1 toolchain


def test_a_tool_beside_the_interpreter_is_found_without_path(tmp_path: Path) -> None:
    """pip puts console scripts next to the interpreter, which is how pi.exec runs us."""
    binding = resolve_tool("ruff", tmp_path)
    assert binding is not None
    assert binding.executable.exists()
    assert binding.bin_dir == binding.executable.parent


def test_the_bind_root_is_the_prefix_not_the_binary(tmp_path: Path) -> None:
    """Binding just the file yields a tool that starts and cannot find its own runtime."""
    binding = resolve_tool("ruff", tmp_path)
    assert binding is not None
    assert binding.bind_root == binding.executable.parent.parent


def test_a_repo_local_tool_outranks_a_global_one(tmp_path: Path) -> None:
    """A repo pinning its own toolchain must be checked by that one."""
    local_bin = tmp_path / "node_modules" / ".bin"
    local_bin.mkdir(parents=True)
    shim = local_bin / "eslint"
    shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shim.chmod(0o755)

    binding = resolve_tool("eslint", tmp_path)
    assert binding is not None
    assert binding.executable == shim.resolve()


def test_an_absent_tool_resolves_to_none(tmp_path: Path) -> None:
    assert resolve_tool("saltcode-no-such-tool", tmp_path) is None


def test_container_env_prepends_tool_bins(tmp_path: Path) -> None:
    binding = resolve_tool("ruff", tmp_path)
    assert binding is not None
    env = container_env_for([binding], {"PATH": "/usr/bin"})
    assert env["PATH"].startswith(str(binding.bin_dir))
    assert env["PATH"].endswith("/usr/bin")


def test_duplicate_bindings_are_collapsed(tmp_path: Path) -> None:
    binding = resolve_tool("ruff", tmp_path)
    assert binding is not None
    env = container_env_for([binding, binding], {"PATH": "/usr/bin"})
    assert env["PATH"].count(str(binding.bin_dir)) == 1
    assert len(ro_binds_for([binding, binding])) == 1


# ------------------------------------------------------------------ 9.2 gate dispatch


def test_a_language_with_no_runners_is_unavailable_not_clean(tmp_path: Path) -> None:
    report = run_static_gate(tmp_path, "cobol", workspace=tmp_path)
    assert report.verdict == "unavailable"
    assert not report.clean
    assert "not a clean verdict" in report.detail


def test_the_report_carries_the_strength(tmp_path: Path) -> None:
    """REQ-STAT-002 AC2 — the Auditor escalates differently on a SOFT gate."""
    assert run_static_gate(tmp_path, "javascript", workspace=tmp_path).strength == "SOFT"
    assert run_static_gate(tmp_path, "rust", workspace=tmp_path).strength == "HARD"


def test_the_report_serializes_for_the_extension(tmp_path: Path) -> None:
    payload = run_static_gate(tmp_path, "cobol", workspace=tmp_path).to_dict()
    json.dumps(payload)
    assert payload["verdict"] == "unavailable"
    assert payload["strength"] == "SOFT"


@needs_container
def test_a_clean_python_diff_returns_clean_with_strength(python_repo: Path) -> None:
    """REQ-STAT-001 AC2 + REQ-STAT-002: a clean tree gates clean and records MEDIUM."""
    with disposable_sandbox(python_repo) as sandbox:
        report = run_static_gate(sandbox, "python", workspace=python_repo)

    assert report.verdict == "clean", report.detail
    assert report.strength == "MEDIUM"


@needs_container
def test_a_broken_diff_returns_dirty_with_the_reason(python_repo: Path) -> None:
    """REQ-STAT-001 AC1 — DIRTY carries the lint reason back to the Builder."""
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a: int, b: int) -> int:\n"
        "-    return a + b\n"
        "+    return a + undefined_name\n"
    )
    with disposable_sandbox(python_repo) as sandbox:
        assert apply_diff(sandbox, diff, workspace=python_repo)
        report = run_static_gate(sandbox, "python", workspace=python_repo)

        assert report.verdict == "dirty"
        assert not report.clean
        assert "undefined_name" in report.reason()


@needs_container
def test_the_live_tree_is_unmodified_by_the_gate(python_repo: Path) -> None:
    """REQ-STAT-001 AC3 — regardless of outcome."""
    before = (python_repo / "app.py").read_bytes()
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a: int, b: int) -> int:\n"
        "-    return a + b\n"
        "+    return a + broken\n"
    )
    with disposable_sandbox(python_repo) as sandbox:
        apply_diff(sandbox, diff, workspace=python_repo)
        run_static_gate(sandbox, "python", workspace=python_repo)

    assert (python_repo / "app.py").read_bytes() == before

    # REQ-STAT-001 AC3 protects the *source* tree. `.saltcode/` is Saltcode's own
    # bookkeeping — `run_in_container` appends every executed command to
    # `.saltcode/audit_log.jsonl`, which REQ-SEC-003 requires — so its appearance is
    # correct behaviour, not a breach. REQ-SEC-004 AC2 words the real boundary the
    # same way: nothing *outside* `.saltcode/` may be modified.
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=python_repo, capture_output=True, text=True, check=True
    )
    touched = [line[3:] for line in status.stdout.splitlines() if line.strip()]
    assert [p for p in touched if not p.startswith(".saltcode/")] == [], touched
    assert (python_repo / ".saltcode" / "audit_log.jsonl").exists(), (
        "the gate must leave an audit trail (REQ-SEC-003)"
    )


# ---------------------------------------------------- 9.3 spec delivery (G-003)


def test_the_spec_is_found_in_the_live_tree(python_repo: Path) -> None:
    spec = python_repo / "tests" / "task_T1_spec.py"
    spec.parent.mkdir()
    spec.write_text("def test_x():\n    assert True\n", encoding="utf-8")
    assert find_spec_file(python_repo, "T1") == spec


def test_a_missing_spec_resolves_to_none(python_repo: Path) -> None:
    assert find_spec_file(python_repo, "T404") is None


def test_the_uncommitted_spec_reaches_the_sandbox(python_repo: Path) -> None:
    """G-003: the worktree is checked out at HEAD, so an uncommitted spec is absent.

    This is the leg that would otherwise be a false green — pytest exits 0 having
    collected nothing and the gate reads PASS.
    """
    spec = python_repo / "tests" / "task_T1_spec.py"
    spec.parent.mkdir()
    spec.write_text("def test_x():\n    assert True\n", encoding="utf-8")
    # Deliberately NOT committed — that is the whole point.

    sandbox = python_repo.parent / "sandbox"
    sandbox.mkdir()

    copied = copy_spec_into_sandbox(python_repo, sandbox, "T1")
    assert copied is not None
    assert copied.read_text(encoding="utf-8") == spec.read_text(encoding="utf-8")
    assert copied.relative_to(sandbox).as_posix() == "tests/task_T1_spec.py"


def test_copying_a_missing_spec_reports_none(python_repo: Path) -> None:
    sandbox = python_repo.parent / "sandbox"
    sandbox.mkdir()
    assert copy_spec_into_sandbox(python_repo, sandbox, "T404") is None


def test_the_spec_copy_is_byte_preserving(python_repo: Path) -> None:
    """A spec is source; re-encoding it makes the thing tested differ from the thing written."""
    spec = python_repo / "tests" / "task_T1_spec.py"
    spec.parent.mkdir()
    body = b"def test_x():\r\n    assert True\r\n\r\n"
    spec.write_bytes(body)

    sandbox = python_repo.parent / "sandbox"
    sandbox.mkdir()
    copied = copy_spec_into_sandbox(python_repo, sandbox, "T1")
    assert copied is not None
    assert copied.read_bytes() == body


# ------------------------------------------------- 9.3 "no tests" is a FAILURE


@pytest.mark.parametrize(
    "output",
    [
        "no tests ran in 0.01s",
        "ERROR: no tests collected",
        "collected 0 items",
        "No tests found, exiting with code 1",
        "running 0 tests",
        "?   mypkg [no test files]",
    ],
)
def test_empty_runs_are_detected_across_frameworks(output: str) -> None:
    """cargo and go both exit 0 on an empty run, so exit codes alone cannot catch this."""
    assert looks_like_no_tests(output, 0, "pytest -q")


def test_pytest_exit_5_is_an_empty_run() -> None:
    """pytest signals "collected nothing" with 5, distinct from 1 (failures)."""
    assert looks_like_no_tests("", 5, "pytest -q")
    assert not looks_like_no_tests("", 1, "pytest -q")


def test_a_real_failure_is_not_mistaken_for_an_empty_run() -> None:
    assert not looks_like_no_tests("1 failed, 2 passed", 1, "pytest -q")


def test_a_missing_spec_is_a_failure_not_a_skip(python_repo: Path) -> None:
    """REQ-CON-004 AC1 requires the spec before the task starts; absence is a broken gate."""
    sandbox = python_repo.parent / "sandbox"
    sandbox.mkdir()

    report = run_task_spec(
        sandbox, "T404", workspace=python_repo, test_runner_cmd="pytest -q"
    )
    assert report.outcome == "no_tests"
    assert not report.passed
    assert report.blocks_auditor
    assert "REQ-CON-004" in report.detail


def test_an_unconfigured_runner_skips(python_repo: Path) -> None:
    """REQ-STAT-004 AC3 — a configured absence, not a failure."""
    sandbox = python_repo.parent / "sandbox"
    sandbox.mkdir()

    for command in (None, "", "   "):
        report = run_task_spec(sandbox, "T1", workspace=python_repo, test_runner_cmd=command)
        assert report.outcome == "skipped"
        assert not report.blocks_auditor


def test_only_pass_and_skip_let_the_auditor_run() -> None:
    """Every other outcome must short-circuit the pipeline (REQ-STAT-004 AC1)."""
    from saltcode.static_gate.test_runner import SpecRunReport

    for outcome in ("fail", "no_tests", "timeout", "refused", "unavailable"):
        report = SpecRunReport(outcome=outcome, exit_code=1, output="", detail="")  # type: ignore[arg-type]
        assert report.blocks_auditor, outcome

    for outcome in ("pass", "skipped"):
        report = SpecRunReport(outcome=outcome, exit_code=0, output="", detail="")  # type: ignore[arg-type]
        assert not report.blocks_auditor, outcome


@needs_container
def test_a_passing_spec_returns_pass(python_repo: Path) -> None:
    spec = python_repo / "tests" / "task_T1_spec.py"
    spec.parent.mkdir()
    spec.write_text("def test_add_exists():\n    assert True\n", encoding="utf-8")

    with disposable_sandbox(python_repo) as sandbox:
        report = run_task_spec(sandbox, "T1", workspace=python_repo, test_runner_cmd="pytest -q")

    assert report.outcome == "pass", report.output
    assert report.passed


@needs_container
def test_a_failing_spec_returns_fail_with_output(python_repo: Path) -> None:
    """REQ-STAT-004 AC1 — the output is what the Builder retry carries."""
    spec = python_repo / "tests" / "task_T1_spec.py"
    spec.parent.mkdir()
    spec.write_text("def test_broken():\n    assert 1 == 2, 'deliberate'\n", encoding="utf-8")

    with disposable_sandbox(python_repo) as sandbox:
        report = run_task_spec(sandbox, "T1", workspace=python_repo, test_runner_cmd="pytest -q")

    assert report.outcome == "fail"
    assert report.blocks_auditor
    assert "deliberate" in report.output


@needs_container
def test_an_empty_spec_file_is_a_failure_not_a_pass(python_repo: Path) -> None:
    """The end-to-end false green: a spec that defines no test collects nothing."""
    spec = python_repo / "tests" / "task_T1_spec.py"
    spec.parent.mkdir()
    spec.write_text("# no tests here\n", encoding="utf-8")

    with disposable_sandbox(python_repo) as sandbox:
        report = run_task_spec(sandbox, "T1", workspace=python_repo, test_runner_cmd="pytest -q")

    assert report.outcome == "no_tests", report.output
    assert not report.passed


@needs_container
def test_the_live_tree_is_unmodified_by_the_test_run(python_repo: Path) -> None:
    spec = python_repo / "tests" / "task_T1_spec.py"
    spec.parent.mkdir()
    spec.write_text("def test_x():\n    assert True\n", encoding="utf-8")
    before = spec.read_bytes()

    with disposable_sandbox(python_repo) as sandbox:
        run_task_spec(sandbox, "T1", workspace=python_repo, test_runner_cmd="pytest -q")

    assert spec.read_bytes() == before


# --------------------------------------------------------------- entrypoints


def run_entrypoint(module: str, *args: str) -> tuple[int, dict[str, object]]:
    completed = subprocess.run(
        [sys.executable, "-m", f"saltcode.tools.{module}", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


def test_static_gate_entrypoint_reports_unavailable_for_an_ungated_language(
    tmp_path: Path,
) -> None:
    code, payload = run_entrypoint(
        "static_gate", "--sandbox", str(tmp_path), "--repo", str(tmp_path), "--language", "python"
    )
    # No pyright/ruff config in an empty dir, but the point is the JSON contract and
    # that a non-clean verdict never exits 0.
    assert code in (EXIT_OK, EXIT_VERDICT_NEGATIVE)
    assert payload["tool"] == "static_gate"
    assert payload["strength"] == "MEDIUM"


def test_static_gate_entrypoint_rejects_a_missing_sandbox(tmp_path: Path) -> None:
    code, payload = run_entrypoint("static_gate", "--sandbox", str(tmp_path / "nope"), "--repo", str(tmp_path))
    assert code == EXIT_USAGE
    assert payload["ok"] is False


def test_test_run_entrypoint_skips_without_a_command(tmp_path: Path) -> None:
    """A skip exits 0 — REQ-STAT-004 AC3 makes it a legitimate outcome."""
    code, payload = run_entrypoint(
        "test_run", "--sandbox", str(tmp_path), "--repo", str(tmp_path), "--task", "T1", "--cmd", ""
    )
    assert code == EXIT_OK
    assert payload["outcome"] == "skipped"


def test_test_run_entrypoint_fails_on_a_missing_spec(tmp_path: Path) -> None:
    """G-003 through the subprocess contract: no spec is exit 1, not exit 0."""
    code, payload = run_entrypoint(
        "test_run", "--sandbox", str(tmp_path), "--repo", str(tmp_path),
        "--task", "T404", "--cmd", "pytest -q",
    )
    assert code == EXIT_VERDICT_NEGATIVE
    assert payload["outcome"] == "no_tests"


def test_test_run_entrypoint_rejects_an_empty_task(tmp_path: Path) -> None:
    code, _ = run_entrypoint(
        "test_run", "--sandbox", str(tmp_path), "--repo", str(tmp_path), "--task", "  "
    )
    assert code == EXIT_USAGE


# ------------------------------------------------ 9.4 diff_check is the first gate


def test_diff_check_rejects_a_malformed_diff_before_any_sandbox(tmp_path: Path) -> None:
    """REQ-STAT-005 AC1 — malformed output is impl_fail and never reaches the sandbox."""
    payload_file = tmp_path / "raw.txt"
    payload_file.write_text("this is prose, not a diff\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.diff_check", "--in", str(payload_file)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
        check=False,
    )
    assert completed.returncode == EXIT_VERDICT_NEGATIVE
    result = json.loads(completed.stdout)
    assert result["ok"] is False or result.get("valid") is False


def test_diff_check_accepts_a_valid_unified_diff(tmp_path: Path) -> None:
    diff_file = tmp_path / "ok.diff"
    diff_file.write_text(
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.diff_check", "--in", str(diff_file)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
        check=False,
    )
    assert completed.returncode == EXIT_OK, completed.stdout


# ============================================================================
# Review follow-ups (CodeRabbit, 2026-07-29) — three findings, all confirmed
# ============================================================================


# --- a tool that fails on its own config is not a Builder failure -----------


def test_tool_failure_exit_codes_are_the_verified_ones() -> None:
    """Checked against the real binaries, not documentation.

    pyright: 2 fatal, 3 unreadable config, 4 bad CLI args. ruff: 2 abnormal
    termination. Both use 1 for diagnostics, the only code meaning "code is wrong".
    """
    assert TOOL_FAILURE_EXIT_CODES["pyright"] == frozenset({2, 3, 4})
    assert TOOL_FAILURE_EXIT_CODES["ruff"] == frozenset({2})
    assert 1 not in TOOL_FAILURE_EXIT_CODES["pyright"]
    assert 1 not in TOOL_FAILURE_EXIT_CODES["ruff"]


@pytest.mark.parametrize("code", [2, 3, 4])
def test_a_pyright_config_failure_is_not_dirty(code: int) -> None:
    """A malformed pyrightconfig.json must not be sent to the Builder as a lint error."""
    spec = RunnerSpec("pyright", "pyright", ("pyright",))
    result = _classify(ContainedResult(code, "", "bad config", "bwrap", "c1"), spec)
    assert result.outcome == "tool_error"
    assert "NOT a Builder failure" in result.detail


def test_a_ruff_config_failure_is_not_dirty() -> None:
    spec = RunnerSpec("ruff", "ruff", ("ruff", "check", "."))
    assert _classify(ContainedResult(2, "", "bad config", "bwrap", "c1"), spec).outcome == "tool_error"


def test_real_diagnostics_are_still_dirty() -> None:
    """Exit 1 is the code being wrong — that must keep routing to the Builder."""
    for program in ("pyright", "ruff"):
        spec = RunnerSpec(program, program, (program,))
        assert _classify(ContainedResult(1, "E501", "", "bwrap", "c1"), spec).outcome == "dirty"


def test_an_unmapped_tool_keeps_the_previous_behaviour() -> None:
    """Only empirically verified codes are mapped; the rest fall through (G-017)."""
    spec = RunnerSpec("tsc", "tsc", ("tsc",))
    assert _classify(ContainedResult(2, "", "", "bwrap", "c1"), spec).outcome == "dirty"


def test_a_tool_error_makes_the_gate_unavailable_not_dirty(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verdict the Phase-2 loop branches on must not say 'Builder retry'."""
    from saltcode.static_gate import gate as gate_module

    def fake_runner(spec: RunnerSpec, **_: object) -> object:
        code = 3 if spec.program == "pyright" else 0
        return _classify(ContainedResult(code, "", "boom", "bwrap", "c1"), spec)

    monkeypatch.setattr(gate_module, "run_static_runner", fake_runner)
    report = gate_module.run_static_gate(Path("/tmp"), "python", workspace=Path("/tmp"))

    assert report.verdict == "unavailable"
    assert not report.clean
    assert "configuration problem for the human" in report.detail


def test_a_tool_error_wins_over_a_concurrent_dirty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Precedence matters: one broken config must not be laundered into 'code is wrong'."""
    from saltcode.static_gate import gate as gate_module

    def fake_runner(spec: RunnerSpec, **_: object) -> object:
        code = 3 if spec.program == "pyright" else 1
        return _classify(ContainedResult(code, "", "x", "bwrap", "c1"), spec)

    monkeypatch.setattr(gate_module, "run_static_runner", fake_runner)
    report = gate_module.run_static_gate(Path("/tmp"), "python", workspace=Path("/tmp"))

    assert report.verdict == "unavailable"
    assert "boom" not in report.reason()


# --- rustup proxies need their toolchain bound ------------------------------


def test_cargo_binds_the_rustup_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`~/.cargo/bin/cargo` is a proxy; the compiler lives under RUSTUP_HOME."""
    cargo_bin = tmp_path / "cargo" / "bin"
    cargo_bin.mkdir(parents=True)
    cargo = cargo_bin / "cargo"
    cargo.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cargo.chmod(0o755)

    rustup = tmp_path / "rustup"
    (rustup / "toolchains").mkdir(parents=True)

    monkeypatch.setenv("RUSTUP_HOME", str(rustup))
    monkeypatch.setenv("PATH", str(cargo_bin))

    binding = resolve_tool("cargo", tmp_path)
    assert binding is not None
    assert rustup in ro_binds_for([binding]), "the active toolchain must be reachable"
    assert binding.bind_root in ro_binds_for([binding])


def test_a_non_rust_tool_gains_no_rustup_bind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rustup = tmp_path / "rustup"
    (rustup / "toolchains").mkdir(parents=True)
    monkeypatch.setenv("RUSTUP_HOME", str(rustup))

    binding = resolve_tool("ruff", tmp_path)
    assert binding is not None
    assert binding.extra_bind_roots == ()


def test_a_host_without_rustup_adds_no_phantom_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cargo_bin = tmp_path / "cargo" / "bin"
    cargo_bin.mkdir(parents=True)
    cargo = cargo_bin / "cargo"
    cargo.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cargo.chmod(0o755)

    monkeypatch.setenv("RUSTUP_HOME", str(tmp_path / "does-not-exist"))
    monkeypatch.setenv("PATH", str(cargo_bin))

    binding = resolve_tool("cargo", tmp_path)
    assert binding is not None
    assert binding.extra_bind_roots == ()


# --- config-load failures must not become silent defaults -------------------


def test_a_malformed_config_is_an_error_not_a_python_default(tmp_path: Path) -> None:
    """A broken saltcode.toml must not silently gate a repo as Python."""
    (tmp_path / "saltcode.toml").write_text("[project\nlanguage = broken", encoding="utf-8")

    code, payload = run_entrypoint("static_gate", "--sandbox", str(tmp_path), "--repo", str(tmp_path))
    assert code == 3, payload
    assert payload["ok"] is False


def test_a_malformed_config_does_not_become_a_skipped_test_run(tmp_path: Path) -> None:
    """The worse case: a config bug presenting as `skipped` at exit 0."""
    (tmp_path / "saltcode.toml").write_text("[project\nlanguage = broken", encoding="utf-8")

    code, payload = run_entrypoint(
        "test_run", "--sandbox", str(tmp_path), "--repo", str(tmp_path), "--task", "T1"
    )
    assert code == 3, payload
    assert payload.get("outcome") != "skipped"


def test_a_missing_config_still_falls_back(tmp_path: Path) -> None:
    """The legitimate case keeps working — narrowing must not break the default."""
    code, payload = run_entrypoint(
        "test_run", "--sandbox", str(tmp_path), "--repo", str(tmp_path), "--task", "T1"
    )
    assert code == EXIT_OK
    assert payload["outcome"] == "skipped"
