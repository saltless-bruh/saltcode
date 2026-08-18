"""Run the task's acceptance spec in the container, after CLEAN (task 9.3).

REQ-STAT-004: once the static gate is CLEAN, the task's `tests/task_{id}_spec.*` runs
on the **same sandbox, in the same container**, via the project's `test_runner_cmd`.
FAIL discards the sandbox and short-circuits to the Builder with the output (AC1);
PASS forwards results to the Auditor alongside the clean static report (AC2); no
configured command SKIPs the step (AC3).

Two things here are load-bearing and neither is obvious from the requirement text.

**Getting the spec into the sandbox (G-003).** The sandbox is a git worktree checked
out at `HEAD`, but Test Intent writes the spec to the *live* tree and leaves it
uncommitted (REQ-CON-004), so the file is simply absent from the worktree. The Builder
cannot carry it in either — REQ-BLD-003 bars it from `tests/**`. So the spec is copied
in explicitly before the run. *(Maintainer decision, 2026-07-29, closing G-003;
alternatives were committing the specs, which would commit every task's spec before any
task passes and muddy the checkpoint discipline, or bind-mounting the live `tests/`,
which exposes every task's spec to every run.)*

**"No tests collected" is a failure, not a pass.** This is the false green G-003 warns
about: `pytest` exits **5** when it collects nothing, and a naive `exit_code == 0`
check would read a missing spec as success and ship an unimplemented task. Every
framework has its own version of this — `cargo test` prints `running 0 tests` and exits
**0**, `go test` reports `[no test files]` and exits **0** — so a pass is only a pass
once the output proves a test actually ran.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from saltcode.harness.sandbox import CONTAINER_ENV, run_in_container
from saltcode.static_gate.toolchain import container_env_for, resolve_tool, ro_binds_for

TestOutcome = Literal["pass", "fail", "skipped", "no_tests", "unavailable", "refused", "timeout"]

SPEC_DIRECTORY = "tests"


@dataclass
class SpecRunReport:
    """The test runner's verdict and the output the Builder retry would carry.

    Named `Spec…`, not `Test…`: pytest treats any `Test*` class as a candidate test
    class and warns (or silently skips) when one has an `__init__`, which every
    dataclass does. `testpaths` already keeps pytest out of the package, but a name
    that cannot be mistaken for a test survives someone invoking pytest differently.
    """

    __test__ = False

    outcome: TestOutcome
    exit_code: int | None
    output: str
    detail: str
    command: str = ""
    spec_file: str | None = None

    @property
    def passed(self) -> bool:
        return self.outcome == "pass"

    @property
    def blocks_auditor(self) -> bool:
        """Whether this outcome must stop the pipeline before the Auditor.

        `skipped` does not (REQ-STAT-004 AC3 makes an unconfigured runner a
        legitimate skip). Everything that is not a pass and not a skip does —
        including `no_tests`, which is the false green this module exists to catch.
        """
        return self.outcome not in ("pass", "skipped")

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "detail": self.detail,
            "command": self.command,
            "spec_file": self.spec_file,
            "output": self.output[-4000:],
        }


NO_TESTS_MARKERS: tuple[str, ...] = (
    "no tests ran",
    "no tests collected",
    "collected 0 items",
    "no tests found",
    "running 0 tests",
    "no test files",
    "[no test files]",
    "testing: warning: no tests to run",
)
"""Phrases that mean "nothing was actually exercised", across pytest, jest, cargo
test and go test. Matched case-insensitively against combined stdout+stderr.

Necessary because exit codes disagree: pytest signals it with **5**, while cargo and
go both exit **0**. An exit-code-only check would pass two of the four frameworks
with an empty run."""

PYTEST_NO_TESTS_EXIT = 5
"""`pytest` exits 5 for "no tests collected" — distinct from 1 (failures)."""


def find_spec_file(workspace: Path | str, task_id: str) -> Path | None:
    """Locate `tests/task_{id}_spec.*` in the live tree (REQ-CON-004).

    The extension is language-agnostic here, so the suffix is whatever Test Intent
    emitted (`.py`, `.ts`, `.rs`, `.go`). Returns ``None`` when no spec exists,
    which the caller must treat as a failure rather than a skip.
    """
    tests_dir = Path(workspace).resolve() / SPEC_DIRECTORY
    if not tests_dir.is_dir():
        return None
    matches = sorted(tests_dir.glob(f"task_{task_id}_spec.*"))
    return matches[0] if matches else None


def copy_spec_into_sandbox(workspace: Path | str, sandbox: Path | str, task_id: str) -> Path | None:
    """Copy the task's spec from the live tree into the sandbox (G-003).

    Returns the sandbox-relative path that was written, or ``None`` when the live
    tree has no spec for this task.
    """
    source = find_spec_file(workspace, task_id)
    if source is None:
        return None

    destination = Path(sandbox).resolve() / SPEC_DIRECTORY / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Byte-preserving: a spec is source code, and re-encoding it through text mode
    # would be one more way for the thing under test to differ from the thing written.
    destination.write_bytes(source.read_bytes())
    return destination


def looks_like_no_tests(output: str, exit_code: int, command: str) -> bool:
    """Whether a run exercised nothing, despite what its exit code claims."""
    lowered = output.lower()
    if any(marker in lowered for marker in NO_TESTS_MARKERS):
        return True
    return "pytest" in command and exit_code == PYTEST_NO_TESTS_EXIT


def run_task_spec(
    sandbox: Path | str,
    task_id: str,
    *,
    workspace: Path | str,
    test_runner_cmd: str | None,
    backend: str | None = None,
) -> SpecRunReport:
    """Run one task's acceptance spec inside the container on the sandbox.

    Args:
        sandbox: The disposable worktree the diff was applied to.
        task_id: Selects `tests/task_{id}_spec.*`.
        workspace: The live tree — source of the spec and of the audit log.
        test_runner_cmd: From project config; ``None``/empty SKIPs (AC3).
        backend: Force a containment backend.
    """
    sandbox_path = Path(sandbox).resolve()
    workspace_path = Path(workspace).resolve()

    if not test_runner_cmd or not test_runner_cmd.strip():
        return SpecRunReport(
            outcome="skipped",
            exit_code=None,
            output="",
            detail="no test_runner_cmd is configured, so the test step is SKIPPED (REQ-STAT-004 AC3)",
        )

    spec_in_sandbox = copy_spec_into_sandbox(workspace_path, sandbox_path, task_id)
    if spec_in_sandbox is None:
        # Not a skip. REQ-CON-004 AC1 requires the spec to exist before Phase 2
        # starts the task, so its absence is a broken precondition — and running
        # the suite anyway would collect nothing and read as a pass.
        return SpecRunReport(
            outcome="no_tests",
            exit_code=None,
            output="",
            detail=(
                f"no tests/task_{task_id}_spec.* exists in the live tree, so there is "
                "nothing to run. REQ-CON-004 AC1 requires the spec before the task "
                "starts; this is a failure, not a skip."
            ),
        )

    relative_spec = spec_in_sandbox.relative_to(sandbox_path).as_posix()
    argv = [*shlex.split(test_runner_cmd), relative_spec]
    command = " ".join(argv)

    binding = resolve_tool(argv[0], workspace_path)
    bindings = [binding] if binding is not None else []
    if binding is None:
        return SpecRunReport(
            outcome="unavailable",
            exit_code=None,
            output="",
            detail=f"the configured test runner {argv[0]!r} is not installed on this host",
            command=command,
            spec_file=relative_spec,
        )

    result = run_in_container(
        argv,
        sandbox=sandbox_path,
        workspace=workspace_path,
        env=container_env_for(bindings, CONTAINER_ENV),
        extra_ro_binds=ro_binds_for(bindings),
        backend=backend,
    )
    output = f"{result.stdout}\n{result.stderr}".strip()

    if result.refused:
        return SpecRunReport(
            outcome="refused",
            exit_code=None,
            output=output,
            detail=f"the test command was refused by the allowlist: {result.reason}",
            command=command,
            spec_file=relative_spec,
        )
    if result.timed_out:
        return SpecRunReport(
            outcome="timeout",
            exit_code=result.exit_code,
            output=output,
            detail="the test run exceeded the container time limit and was killed",
            command=command,
            spec_file=relative_spec,
        )

    if looks_like_no_tests(output, result.exit_code, command):
        return SpecRunReport(
            outcome="no_tests",
            exit_code=result.exit_code,
            output=output,
            detail=(
                "the runner collected no tests. Treated as a FAILURE, not a pass: an "
                "empty run proves nothing and would otherwise ship an unimplemented "
                "task (G-003)."
            ),
            command=command,
            spec_file=relative_spec,
        )

    if result.exit_code == 0:
        return SpecRunReport(
            outcome="pass",
            exit_code=0,
            output=output,
            detail="the task spec passed",
            command=command,
            spec_file=relative_spec,
        )

    return SpecRunReport(
        outcome="fail",
        exit_code=result.exit_code,
        output=output,
        detail=f"the task spec failed (exit {result.exit_code})",
        command=command,
        spec_file=relative_spec,
    )
