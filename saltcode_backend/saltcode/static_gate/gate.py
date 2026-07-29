"""The static gate: dispatch by language, report `clean | dirty` (task 9.2).

REQ-STAT-001 makes this a **hard gate before the Auditor**, run on a disposable
sandbox inside the security container, never on the live tree. DIRTY short-circuits
back to the Builder with the lint reason and the Auditor is not invoked at all
(AC1); CLEAN forwards the report to the test runner (AC2); either way the live tree
is untouched (AC3).

**A gate that could not run is not clean.** Three outcomes exist, not two: `clean`,
`dirty`, and `unavailable` — the last when no runner is configured for the language
or the tools are not installed. Collapsing `unavailable` into `clean` would let a repo
with no toolchain sail past the one deterministic check in the pipeline; collapsing it
into `dirty` would make an unconfigured project unable to build anything. It is
surfaced as its own verdict so the caller decides deliberately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from saltcode.static_gate.runners import (
    GateStrength,
    RunnerResult,
    run_static_runner,
    runners_for,
    strength_for,
)

GateVerdict = Literal["clean", "dirty", "unavailable"]


@dataclass
class StaticGateReport:
    """The gate's verdict plus every runner's evidence.

    Handed to the test runner and then to the Auditor (REQ-STAT-004 AC2), so it
    carries the strength: a `clean` from `eslint` alone (SOFT) and a `clean` from
    `cargo check` + `clippy` (HARD) are different claims, and REQ-STAT-002 AC2 has
    the Auditor escalate differently on each.
    """

    verdict: GateVerdict
    language: str
    strength: GateStrength
    runners: list[RunnerResult] = field(default_factory=list[RunnerResult])
    detail: str = ""

    @property
    def clean(self) -> bool:
        return self.verdict == "clean"

    def reason(self) -> str:
        """The failure text fed back to the Builder on a DIRTY verdict."""
        failing = [r for r in self.runners if r.outcome in ("dirty", "timeout", "refused")]
        if not failing:
            return self.detail
        blocks = [f"--- {r.name} ({r.outcome}) ---\n{r.output}".strip() for r in failing]
        return "\n\n".join(blocks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "language": self.language,
            "strength": self.strength,
            "detail": self.detail,
            "runners": [r.to_dict() for r in self.runners],
        }


def run_static_gate(
    sandbox: Path | str,
    language: str,
    *,
    workspace: Path | str | None = None,
    backend: str | None = None,
) -> StaticGateReport:
    """Run every runner for `language` on the sandbox and combine their verdicts.

    Runners are **not** short-circuited against each other: `ruff` still runs after
    `pyright` fails, because the Builder retry is far more useful carrying every
    problem at once than carrying the first one repeatedly. The short-circuit
    REQ-STAT-001 AC1 requires is of the *pipeline* — the Auditor is not invoked —
    and that is the caller's move, driven by this verdict.
    """
    sandbox_path = Path(sandbox).resolve()
    strength = strength_for(language)
    specs = runners_for(language)

    if not specs:
        return StaticGateReport(
            verdict="unavailable",
            language=language,
            strength=strength,
            detail=(
                f"no static-analysis runners are defined for language {language!r}; "
                "this is not a clean verdict (design §14)"
            ),
        )

    results = [
        run_static_runner(spec, sandbox=sandbox_path, workspace=workspace, backend=backend)
        for spec in specs
    ]

    if any(r.outcome in ("dirty", "timeout", "refused") for r in results):
        failed = [r.name for r in results if r.outcome in ("dirty", "timeout", "refused")]
        return StaticGateReport(
            verdict="dirty",
            language=language,
            strength=strength,
            runners=results,
            detail=f"static analysis failed: {', '.join(failed)}",
        )

    if all(r.outcome == "unavailable" for r in results):
        missing = ", ".join(r.name for r in results)
        return StaticGateReport(
            verdict="unavailable",
            language=language,
            strength=strength,
            runners=results,
            detail=(
                f"none of the {language} static-analysis tools are installed ({missing}); "
                "this is not a clean verdict"
            ),
        )

    ran = [r.name for r in results if r.outcome == "clean"]
    skipped = [r.name for r in results if r.outcome == "unavailable"]
    detail = f"clean: {', '.join(ran)}"
    if skipped:
        # A partial toolchain still gates, but the report must say what did not run
        # so a `clean` is never read as more assurance than it is.
        detail += f" (not installed, did not run: {', '.join(skipped)})"

    return StaticGateReport(
        verdict="clean",
        language=language,
        strength=strength,
        runners=results,
        detail=detail,
    )
