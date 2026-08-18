"""The regression gate: the project's FULL suite, on the live tree, in the container.

Task 17.1 · REQ-CKP-002, REQ-SEC-001.

This is the gate that separates "the task passed its own spec" from "the task did not
break anything else". It runs *after* ``apply_live`` has put the diff on the live tree and
*before* the checkpoint commit, which is why design §10.1 leaves the tree uncommitted in
between: a failing regression has to be discardable with `git reset --hard`, and a commit
would have made that a revert instead.

**Why the live tree is the container's writable root.** Every other gate runs on a
disposable worktree. This one cannot: the whole question it answers is whether the
*integrated* tree is healthy, and a sandbox copy is not the integrated tree. So the
project directory is the writable root, and the container's other guarantees — no network,
no ``$HOME``, no credentials, isolated PID namespace, memory/CPU/time ceilings — carry the
isolation. Same tradeoff `contained_exec` documents for interactive mode, made for the
same reason and stated rather than assumed.

**The toolchain is resolved and bound in, exactly as the task-spec runner does it.**
`CONTAINER_ENV` sets `PATH` to `/usr/local/bin:/usr/bin:/bin` and `/home` is deliberately
never bound, so a `pytest` living in a virtualenv is simply not there — the first
end-to-end run of this gate came back `bwrap: execvp pytest: No such file or directory`,
i.e. a green suite reported as a red tree. `static_gate/toolchain.py` already solved this
for the static gate and the task-spec runner; the regression gate uses the same three
calls rather than a second answer.

**No `regression_cmd` is a SKIP, not a pass** (AC3). The distinction is load-bearing: a
skipped gate marks the checkpoint ``regression: unverified``, and design §10.1 says the
human review carries the difference. Reporting it as a pass would silently claim
robustness the run never established.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from saltcode.harness.command_allowlist import check_command
from saltcode.harness.sandbox import CONTAINER_ENV, ContainerLimits, run_in_container
from saltcode.static_gate.toolchain import container_env_for, resolve_tool, ro_binds_for

RegressionOutcome = Literal["pass", "fail", "skipped", "unavailable"]


@dataclass(frozen=True)
class RegressionResult:
    """The gate's answer, plus what the extension needs to route a failure."""

    outcome: RegressionOutcome
    detail: str
    output: str = ""
    command: str = ""
    backend: str = ""
    exit_code: int | None = None
    limits_enforced: bool = True
    failing_files: tuple[str, ...] = field(default_factory=tuple)
    """Test files the output names as failing.

    **A heuristic, and the caller must treat an empty tuple as *unknown*, never as
    "nothing outside scope".** REQ-CKP-003 AC1 sends a failure whose files fall outside
    `task.files_affected` to a human; if this could not identify them, the safe reading is
    the same one — flag — because the alternative is auto-retrying a Builder against a
    breakage it cannot see. `attributable` says which case you are in.
    """

    @property
    def ok(self) -> bool:
        """`skipped` is not `pass`. Only a suite that ran and stayed green is a pass."""
        return self.outcome == "pass"

    @property
    def attributable(self) -> bool:
        """Whether the failure can be attributed to specific files at all."""
        return bool(self.failing_files)


# ---------------------------------------------------------------- failing-file extraction

_FAILING_PATTERNS: tuple[re.Pattern[str], ...] = (
    # pytest: "FAILED tests/test_x.py::test_y - AssertionError" and the short-summary form.
    re.compile(r"^(?:FAILED|ERROR)\s+(?P<path>[^\s:]+\.py)(?:::|\s|$)", re.MULTILINE),
    # pytest tracebacks: "tests/test_x.py:12: AssertionError"
    re.compile(r"^(?P<path>[^\s:]+\.py):\d+:\s", re.MULTILINE),
    # jest / vitest: "FAIL src/thing.test.ts"
    re.compile(r"^\s*FAIL\s+(?P<path>\S+\.[jt]sx?)\s*$", re.MULTILINE),
    # go test: "--- FAIL: TestX" gives no file, but the failure line does: "x_test.go:12:"
    re.compile(r"^\s*(?P<path>[\w./-]+_test\.go):\d+:", re.MULTILINE),
    # cargo test names modules, not files; the panic line carries one.
    re.compile(r"^thread '[^']*' panicked at (?P<path>[\w./-]+\.rs):\d+", re.MULTILINE),
)


def extract_failing_files(output: str) -> tuple[str, ...]:
    """Best-effort test files named as failing, in first-seen order.

    Deliberately conservative about what counts: only paths that appear in a *failure*
    construct, never every path mentioned. Over-reporting would push a routable failure to
    FLAG HUMAN (annoying but safe); under-reporting would let a real cross-task breakage be
    auto-retried against the wrong task (unsafe), which is why every pattern here anchors
    on a failure marker rather than on a bare path.
    """
    seen: dict[str, None] = {}
    for pattern in _FAILING_PATTERNS:
        for match in pattern.finditer(output):
            path = match.group("path").lstrip("./")
            if path:
                seen.setdefault(path, None)
    return tuple(seen)


# ------------------------------------------------------------------------------ the gate


def run_regression(
    workspace: Path | str,
    *,
    regression_cmd: str | None,
    limits: ContainerLimits | None = None,
    backend: str | None = None,
) -> RegressionResult:
    """Run the full suite on the live tree, contained.

    Args:
        workspace: The live tree. Also the container's writable root — see the module
            docstring for why this one gate cannot use a disposable worktree.
        regression_cmd: From `saltcode.toml [checkpoint]`. Empty/``None`` SKIPs (AC3).
        limits: Resource ceilings; defaults from project config.
        backend: Force a containment backend.

    Returns:
        A :class:`RegressionResult`. Never raises for a failing suite — a red suite is the
        outcome this gate exists to detect, not an error.
    """
    workspace_path = Path(workspace).resolve()

    if regression_cmd is None or not regression_cmd.strip():
        return RegressionResult(
            outcome="skipped",
            detail=(
                "no regression_cmd is configured under [checkpoint], so the regression gate "
                "is SKIPPED and the checkpoint is marked regression: unverified "
                "(REQ-CKP-002 AC3). Robustness is correspondingly weaker; design §10.1 "
                "puts that difference on the human review."
            ),
            command="",
        )

    decision = check_command(regression_cmd)
    if not decision.allowed:
        # Not a suite failure. A refused command means the *configuration* is wrong, and
        # routing it as `fail` would send a Builder to fix code that was never run.
        return RegressionResult(
            outcome="unavailable",
            detail=(
                f"regression_cmd is not runnable: {decision.reason} "
                "Fix [checkpoint] regression_cmd, or add the command to "
                "[security] allowed_commands deliberately (REQ-SEC-002 AC2)."
            ),
            command=regression_cmd,
        )

    argv = list(decision.argv or ())
    binding = resolve_tool(argv[0], workspace_path)
    if binding is None:
        # Same shape as `check_command` refusing: nothing ran, so this is not a red suite.
        # Reporting it as `fail` would send a Builder to fix code that was never tested.
        return RegressionResult(
            outcome="unavailable",
            detail=(
                f"the configured regression runner {argv[0]!r} is not installed on this "
                "host, so the full suite never ran. This is not a failing suite."
            ),
            command=regression_cmd,
        )

    contained = run_in_container(
        argv,
        sandbox=workspace_path,
        workspace=workspace_path,
        limits=limits,
        env=container_env_for([binding], CONTAINER_ENV),
        extra_ro_binds=ro_binds_for([binding]),
        backend=backend,
    )
    output = f"{contained.stdout}\n{contained.stderr}".strip()

    if contained.refused:
        return RegressionResult(
            outcome="unavailable",
            detail=f"the container refused the regression command: {contained.reason}",
            command=regression_cmd,
            backend=contained.backend,
            output=output,
        )

    if contained.timed_out:
        # A suite that never finished has not told us the tree is broken — only that it is
        # slow or hung. Treated as a failure because it cannot clear the robustness bar,
        # but the detail says which, so nobody reads it as a real test failure.
        return RegressionResult(
            outcome="fail",
            detail=(
                "the regression suite exceeded the container time limit and was killed. "
                "That is not a red suite — raise [security] sandbox_timeout or narrow "
                "regression_cmd before concluding the tree is broken."
            ),
            output=output,
            command=regression_cmd,
            backend=contained.backend,
            exit_code=contained.exit_code,
            limits_enforced=contained.limits_enforced,
        )

    if contained.exit_code == 0:
        return RegressionResult(
            outcome="pass",
            detail="the full suite is green on the integrated live tree",
            output=output,
            command=regression_cmd,
            backend=contained.backend,
            exit_code=0,
            limits_enforced=contained.limits_enforced,
        )

    failing = extract_failing_files(output)
    return RegressionResult(
        outcome="fail",
        detail=(
            f"the full suite failed on the integrated live tree (exit {contained.exit_code})"
            + (
                f"; failures attributed to {', '.join(failing)}"
                if failing
                else "; no failing file could be attributed from the output, so this must be "
                "treated as unroutable and flagged rather than retried (REQ-CKP-003 AC1)"
            )
        ),
        output=output,
        command=regression_cmd,
        backend=contained.backend,
        exit_code=contained.exit_code,
        limits_enforced=contained.limits_enforced,
        failing_files=failing,
    )
