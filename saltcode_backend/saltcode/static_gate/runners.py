"""Per-language static-analysis runners, each contained (task 9.1, REQ-STAT-002).

One runner = one tool invocation inside the security container on the sandbox. The
language → tool mapping is design §14's table, reproduced exactly:

============ ============================== ==========
Language     Tools                          Strength
============ ============================== ==========
TypeScript   `tsc`, `eslint`                HARD
Rust         `cargo check`, `cargo clippy`  HARD
Go           `go build`, `go vet`           HARD
Python       `pyright --strict`, `ruff`     MEDIUM
JavaScript   `eslint`                       SOFT
============ ============================== ==========

Strength is recorded, not just used: REQ-STAT-002 AC2 has the Auditor prefer online
Flash escalation on a SOFT gate, so downstream needs to know how much the gate's
silence is actually worth. A JS `clean` and a Rust `clean` are not the same claim.

**Zero GPU, bounded time** (REQ-STAT-003): these are subprocesses, no model is loaded,
and the container carries the memory/CPU/time ceilings from `ContainerLimits`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from saltcode.harness.sandbox import CONTAINER_ENV, ContainedResult, run_in_container
from saltcode.static_gate.toolchain import (
    ToolchainBinding,
    container_env_for,
    resolve_tool,
    ro_binds_for,
)

Language = Literal["python", "typescript", "javascript", "rust", "go"]
GateStrength = Literal["HARD", "MEDIUM", "SOFT"]
RunnerOutcome = Literal["clean", "dirty", "unavailable", "refused", "timeout"]


@dataclass(frozen=True)
class RunnerSpec:
    """One tool invocation: the program to resolve and the argv to run."""

    name: str
    program: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class RunnerResult:
    """What one runner concluded, with the evidence."""

    name: str
    outcome: RunnerOutcome
    exit_code: int | None
    output: str
    detail: str

    @property
    def is_clean(self) -> bool:
        return self.outcome == "clean"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "detail": self.detail,
            # Bounded: a full `tsc` dump on a broken tree is megabytes, and this
            # is fed back to the Builder as a retry reason, not archived.
            "output": self.output[-4000:],
        }


LANGUAGE_RUNNERS: dict[str, tuple[RunnerSpec, ...]] = {
    "python": (
        RunnerSpec("pyright", "pyright", ("pyright", "--outputjson")),
        RunnerSpec("ruff", "ruff", ("ruff", "check", ".")),
    ),
    "typescript": (
        RunnerSpec("tsc", "tsc", ("tsc", "--noEmit")),
        RunnerSpec("eslint", "eslint", ("eslint", ".")),
    ),
    "javascript": (RunnerSpec("eslint", "eslint", ("eslint", ".")),),
    "rust": (
        RunnerSpec("cargo check", "cargo", ("cargo", "check")),
        RunnerSpec("cargo clippy", "cargo", ("cargo", "clippy")),
    ),
    "go": (
        RunnerSpec("go build", "go", ("go", "build", "./...")),
        RunnerSpec("go vet", "go", ("go", "vet", "./...")),
    ),
}
"""Design §14, verbatim. `pyright` runs under `--outputjson` rather than
`--strict`: strictness is the project's own `pyproject.toml`
(`typeCheckingMode = "strict"`), and passing `--strict` on the command line would
*override* a project that deliberately configured something else. REQ-STAT-002 AC1
asks for `pyright --strict` as the Python gate, which this repo's own config
already selects; a target project that has not opted in gets its configured mode,
and the gate reports which."""

LANGUAGE_STRENGTH: dict[str, GateStrength] = {
    "typescript": "HARD",
    "rust": "HARD",
    "go": "HARD",
    "python": "MEDIUM",
    "javascript": "SOFT",
}
"""Design §14's strength column. Not derived from the runner list — a language with
two tools is not automatically HARD; `python` runs two and is MEDIUM."""


def runners_for(language: str) -> tuple[RunnerSpec, ...]:
    return LANGUAGE_RUNNERS.get(language.lower(), ())


def strength_for(language: str) -> GateStrength:
    """The gate's strength for a language; unknown languages are SOFT.

    SOFT rather than HARD because strength describes *how much a clean verdict is
    worth*, and a language we have no runner for buys nothing at all. Claiming HARD
    there would tell the Auditor to relax on the one input it should trust least.
    """
    return LANGUAGE_STRENGTH.get(language.lower(), "SOFT")


def _classify(result: ContainedResult, spec: RunnerSpec) -> RunnerResult:
    output = f"{result.stdout}\n{result.stderr}".strip()

    if result.refused:
        return RunnerResult(
            name=spec.name,
            outcome="refused",
            exit_code=None,
            output=output,
            detail=f"{spec.name} was refused by the command allowlist: {result.reason}",
        )
    if result.timed_out:
        return RunnerResult(
            name=spec.name,
            outcome="timeout",
            exit_code=result.exit_code,
            output=output,
            detail=f"{spec.name} exceeded the container time limit and was killed",
        )
    if result.exit_code == 0:
        return RunnerResult(
            name=spec.name,
            outcome="clean",
            exit_code=0,
            output=output,
            detail=f"{spec.name} reported no problems",
        )
    return RunnerResult(
        name=spec.name,
        outcome="dirty",
        exit_code=result.exit_code,
        output=output,
        detail=f"{spec.name} exited {result.exit_code}",
    )


def run_static_runner(
    spec: RunnerSpec,
    *,
    sandbox: Path | str,
    workspace: Path | str | None = None,
    backend: str | None = None,
) -> RunnerResult:
    """Run one static-analysis tool inside the container on the sandbox.

    A tool that is not installed yields ``unavailable`` — never ``clean``. That
    distinction is the whole point: a gate that reports "no problems found" because
    it never ran is a false green, and REQ-STAT-001 makes this the last barrier
    before the Auditor.
    """
    sandbox_path = Path(sandbox).resolve()
    resolve_root = Path(workspace).resolve() if workspace is not None else sandbox_path

    binding: ToolchainBinding | None = resolve_tool(spec.program, resolve_root)
    if binding is None:
        return RunnerResult(
            name=spec.name,
            outcome="unavailable",
            exit_code=None,
            output="",
            detail=(
                f"{spec.program!r} is not installed on this host, so {spec.name} could "
                "not run. This is NOT a clean verdict."
            ),
        )

    bindings = [binding]
    result = run_in_container(
        list(spec.argv),
        sandbox=sandbox_path,
        workspace=workspace,
        env=container_env_for(bindings, CONTAINER_ENV),
        extra_ro_binds=ro_binds_for(bindings),
        backend=backend,
    )
    return _classify(result, spec)
