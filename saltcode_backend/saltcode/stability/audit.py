"""Heuristics + measured stability → `audit_result.json` (task 10.2, REQ-AUD-003).

The order REQ-AUD-001 fixes is "zero-cost heuristics **then** a judgment pass". This
module runs both and turns the two into the single typed verdict the Phase-2 loop routes
on, so the composition rule lives in one place rather than being re-derived by whichever
caller happens to hold both halves.

**Why the heuristics feed the judgment rather than pre-empt it.** REQ-AUD-001 AC1 makes
a flag a *suspicion* — "the verdict SHALL be `gaming_suspected` unless judgment clears
it". So the flags go into the evidence the model judges, and a `pass` verdict overrides
them. A heuristic that could convict on its own would make the Builder retry on every
diff that returns a string the spec happens to mention.
"""

from __future__ import annotations

from typing import Any

from saltcode.contracts.audit_result import AuditResult, StabilityInfo
from saltcode.stability.heuristics import HeuristicReport, run_heuristics
from saltcode.stability.measure import (
    DEFAULT_N_PASSES,
    UNPARSEABLE_VERDICT,
    AuditEvidence,
    JudgmentClient,
    StabilityMeasurement,
    measure_stability,
)

NEXT_ACTION_FOR_REASON: dict[str, str] = {
    "pass": "next_task",
    "impl_fail": "builder_retry",
    "gaming_suspected": "builder_retry",
    "spec_defect": "test_intent_respec",
}
"""REQ-AUD-003's routing table, as a table rather than as a chain of ifs."""


def evidence_with_heuristics(evidence: AuditEvidence, report: HeuristicReport) -> AuditEvidence:
    """Append the heuristic findings to the static report the judgment sees.

    Carried in the static block rather than as a new evidence section so the
    reordering in :func:`~saltcode.stability.measure.build_conditions` keeps working
    unchanged: the flags travel with a block that is already rotated, and no pass sees
    a different *set* of evidence from another — only a different order (REQ-AUD-002 AC5
    varies the presentation, never the facts).
    """
    if not report.fired:
        return evidence

    lines = [evidence.static_report.rstrip(), "", "### ANTI-GAMING FLAGS (heuristic, unconfirmed)"]
    lines += [f"- [{f.name}] {f.detail} — `{f.line.strip()[:200]}`" for f in report.flags]
    lines.append(
        "These are suspicions raised by pattern matching, not findings. Judge the diff: "
        "if the code genuinely implements the task, answer `pass` and the flags are cleared."
    )
    return AuditEvidence(
        task_id=evidence.task_id,
        diff=evidence.diff,
        acceptance_criteria=evidence.acceptance_criteria,
        static_report="\n".join(lines).strip(),
        test_results=evidence.test_results,
        spec_content=evidence.spec_content,
    )


def resolve_reason(measurement: StabilityMeasurement, report: HeuristicReport) -> tuple[str, str]:
    """Combine the judgment and the flags into ``(reason, detail)`` (REQ-AUD-001 AC1).

    The three cases:

    * **No flag** — the judgment stands as-is.
    * **Flags, judgment says `pass`** — cleared. This is the "unless judgment clears it"
      branch, and it is the reason heuristics may be imprecise without costing retries.
    * **Flags, judgment says anything else** — `gaming_suspected`, so the Builder retry
      carries the "no hardcoding" reason and the flagged patterns (REQ-AUD-003).

    A judgment this module cannot read is never laundered into a verdict: an
    ``unparseable`` majority becomes a human flag (see :func:`build_audit_result`), not
    a Builder retry for a failure the Builder may not have committed.
    """
    verdict = measurement.majority_verdict

    if not report.fired:
        return verdict, measurement.passes[0].detail if measurement.passes else ""

    if verdict == "pass":
        return "pass", f"judgment cleared {report.summary()}"

    if verdict == UNPARSEABLE_VERDICT:
        return verdict, f"judgment unreadable; {report.summary()}"

    # REQ-AUD-001 AC1, read literally: a fired flag that judgment did not clear is
    # `gaming_suspected`, even when the judgment named a different failure. For
    # `impl_fail` this loses nothing — both route to a Builder retry, and the gaming
    # reason carries strictly more detail. For `spec_defect` it does cost something,
    # and that collision is recorded in specs/known_gaps.md rather than resolved here.
    if verdict != "gaming_suspected":
        return "gaming_suspected", (
            f"{report.summary()}; the judgment returned {verdict!r} and did not clear them "
            "(REQ-AUD-001 AC1)"
        )
    return "gaming_suspected", report.summary()


def build_audit_result(
    measurement: StabilityMeasurement,
    report: HeuristicReport,
) -> AuditResult:
    """Assemble the typed `audit_result.json` (REQ-CON-006).

    `status` is derived from `reason` rather than carried separately — REQ-CON-006 AC1
    requires `reason == "pass"` if and only if `status == "pass"`, and deriving it is the
    only way that cannot drift.
    """
    reason, detail = resolve_reason(measurement, report)

    if reason == UNPARSEABLE_VERDICT:
        # No pass produced a readable verdict. There is no honest way to name which of
        # the four outcomes this was, and guessing would either accuse the Builder or
        # clear a diff nothing judged. The contract's `reason` set is closed, so the
        # verdict is recorded as a failure and routed to a human.
        return AuditResult(
            task_id=measurement.task_id,
            status="fail",
            reason="impl_fail",
            next_action="flag_human",
            detail=(
                f"the Auditor produced no readable verdict in {measurement.n_passes} passes "
                f"({detail}). Routed to a human rather than to a Builder retry: nothing here "
                "says the diff is wrong."
            ),
            stability=StabilityInfo(**measurement.stability_info()),
        )

    next_action = NEXT_ACTION_FOR_REASON[reason]

    if reason != "pass" and not measurement.stable:
        # REQ-AUD-002 AC2/AC3: an unstable verdict is not final. The escalation itself
        # is the extension's (design §5.6a), so the routing stays as the local verdict
        # says and the instability is stated in `detail` for the caller to act on.
        detail = (
            f"{detail} [stability {measurement.stability_score:.2f} < threshold "
            f"{measurement.threshold:.2f}"
            f"{'' if measurement.threshold_calibrated else ', uncalibrated'}]"
        )

    return AuditResult(
        task_id=measurement.task_id,
        status="pass" if reason == "pass" else "fail",
        reason=reason,  # pyright: ignore[reportArgumentType]  # constrained by NEXT_ACTION_FOR_REASON
        next_action=next_action,  # pyright: ignore[reportArgumentType]
        detail=detail or reason,
        stability=StabilityInfo(**measurement.stability_info()),
    )


def audit_diff(
    evidence: AuditEvidence,
    client: JudgmentClient,
    *,
    n_passes: int = DEFAULT_N_PASSES,
    model: str | None = None,
    threshold: float = 0.5,
    threshold_calibrated: bool = False,
) -> tuple[AuditResult, StabilityMeasurement, HeuristicReport]:
    """The full Auditor pipeline: heuristics, then the N-pass judgment, then the verdict.

    Returns the typed result along with the two measurements behind it, so a caller can
    report *why* without re-running anything.

    Takes no ``online`` flag: nothing in the pipeline behaves differently online, because
    nothing in it may make a network call (REQ-AUD-002 AC1). Connectivity only selects
    the *recommendation*, which is :func:`~saltcode.stability.measure.escalation_for`'s
    job and is applied in :func:`audit_payload`.
    """
    report = run_heuristics(evidence.diff, evidence.spec_content)
    measurement = measure_stability(
        evidence_with_heuristics(evidence, report),
        client,
        n_passes=n_passes,
        model=model,
        threshold=threshold,
        threshold_calibrated=threshold_calibrated,
    )
    result = build_audit_result(measurement, report)
    return result, measurement, report


def audit_payload(
    result: AuditResult,
    measurement: StabilityMeasurement,
    report: HeuristicReport,
    *,
    online: bool,
) -> dict[str, Any]:
    """The entrypoint's JSON: the contract plus the evidence for it."""
    from saltcode.stability.measure import escalation_for

    return {
        "audit_result": result.model_dump(),
        "heuristics": report.to_dict(),
        "measurement": measurement.to_dict(),
        "escalation": escalation_for(measurement, online=online),
        "online": online,
    }
