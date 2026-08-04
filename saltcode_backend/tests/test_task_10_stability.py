"""Task 10.1/10.2 — measured Auditor confidence and the anti-gaming heuristics.

The defect this task exists to prevent is a *decorative* confidence number: N passes
that were never actually distinct, agreeing trivially, scoring 1.0 on every diff. So the
distinctness of the conditions is asserted directly (REQ-AUD-002 AC5), not inferred from
a score.

Every judgment runs against an in-process double. There is no Saltnitor on this host, and
a test that reached for one would either skip — proving nothing — or touch the network.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from saltcode.contracts.audit_result import AuditResult
from saltcode.providers.local import LocalClient
from saltcode.stability.audit import (
    audit_diff,
    build_audit_result,
    evidence_with_heuristics,
    resolve_reason,
)
from saltcode.stability.heuristics import (
    EMPTY_BODY,
    FIXTURE_RETURN,
    TEST_KEYED_BRANCH,
    added_lines,
    extract_fixture_literals,
    run_heuristics,
)
from saltcode.stability.measure import (
    DEFAULT_N_PASSES,
    EVIDENCE_SECTIONS,
    MAX_TEMPERATURE,
    UNPARSEABLE_VERDICT,
    AuditEvidence,
    StabilityMeasurementError,
    build_conditions,
    compute_gac,
    compute_stability_score,
    escalation_for,
    majority_verdict,
    measure_stability,
    parse_verdict,
    render_prompt,
)
from saltcode.tools._cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, EXIT_VERDICT_NEGATIVE

REPO_ROOT = Path(__file__).resolve().parents[1]


class ScriptedClient:
    """Returns a queued verdict per call and records the exact request it was given.

    Recording the requests is the point: AC5 is a claim about what reached the model,
    so the assertions read the captured payloads rather than trusting the caller.
    """

    def __init__(self, verdicts: list[str]) -> None:
        self.verdicts = list(verdicts)
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        thinking: bool,
        json_schema: dict[str, Any] | None = None,
        model: str | None = None,
        contains_raw_source: bool = False,
        temperature: float | None = None,
    ) -> str:
        self.calls.append(
            {
                "content": messages[-1]["content"],
                "temperature": temperature,
                "model": model,
                "thinking": thinking,
                "contains_raw_source": contains_raw_source,
            }
        )
        if len(self.calls) > len(self.verdicts):
            # Never fall back to "pass": a test that runs more passes than it scripted
            # would then measure three agreeing verdicts and succeed for the wrong reason.
            raise AssertionError(
                f"ScriptedClient ran out after {len(self.verdicts)} scripted verdicts"
            )
        verdict = self.verdicts[len(self.calls) - 1]
        return json.dumps({"verdict": verdict, "detail": f"pass {len(self.calls)}"})


class ExplodingClient:
    """Fails every call, standing in for an unreachable Saltnitor."""

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        raise RuntimeError("connection refused")


def evidence(diff: str = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n", spec: str = "") -> AuditEvidence:
    return AuditEvidence(
        task_id="T1",
        diff=diff,
        acceptance_criteria=["it works"],
        static_report="clean",
        test_results="1 passed",
        spec_content=spec,
    )


# --------------------------------------------------------------------- the formula


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        (["pass", "pass", "pass"], 1.0),
        (["pass", "pass", "impl_fail"], 0.5),
        (["pass", "impl_fail", "pass"], 0.0),
        (["impl_fail", "impl_fail"], 1.0),
        (["pass"], 1.0),
    ],
)
def test_stability_score_is_the_documented_formula(verdicts: list[str], expected: float) -> None:
    """`1.0 - (verdict_changes / (N-1))` — REQ-AUD-002, REQ-CON-006 AC2."""
    assert compute_stability_score(verdicts) == pytest.approx(expected)


def test_the_score_matches_what_the_contract_validates() -> None:
    """The formula lives in two places; they must not drift.

    `StabilityInfo` re-derives the score and rejects a mismatch, so constructing one
    from a measurement is itself the cross-check.
    """
    client = ScriptedClient(["pass", "impl_fail", "impl_fail"])
    measurement = measure_stability(evidence(), client)
    result = build_audit_result(measurement, run_heuristics(evidence().diff, ""))
    assert result.stability.stability_score == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        (["pass", "pass", "pass"], 1),
        (["impl_fail", "pass", "pass"], 2),
        (["pass", "impl_fail", "pass"], 3),
        (["pass"], 1),
        ([], 0),
    ],
)
def test_gac_is_the_pass_the_verdict_last_settled_on(verdicts: list[str], expected: int) -> None:
    """1-based, and a run that never settles "stabilises" only on its final pass.

    The specs do not pin the index base down (see the docstring and G-020); this locks
    in the reading the code documents so a later change is a deliberate one.
    """
    assert compute_gac(verdicts) == expected


# ------------------------------------------------- AC5: the passes must really differ


def test_every_pass_runs_under_a_distinct_condition() -> None:
    """REQ-AUD-002 AC5. Without this the whole measurement is decorative."""
    conditions = build_conditions(DEFAULT_N_PASSES)
    assert len(conditions) == DEFAULT_N_PASSES
    assert len({c.describe() for c in conditions}) == DEFAULT_N_PASSES


def test_the_passes_differ_in_temperature_and_in_prompt_bytes() -> None:
    """Both axes, because either alone can be silently neutralised.

    A server may ignore or pin `temperature` and say nothing; reordering changes the
    request itself, so it survives that.
    """
    client = ScriptedClient(["pass", "pass", "pass"])
    measure_stability(evidence(spec="assert x == 1"), client)

    temperatures = [c["temperature"] for c in client.calls]
    prompts = [c["content"] for c in client.calls]

    assert len(set(temperatures)) == DEFAULT_N_PASSES, temperatures
    assert len(set(prompts)) == DEFAULT_N_PASSES, "evidence order must differ pass to pass"


def test_reordering_permutes_blocks_without_changing_the_facts() -> None:
    """The set of evidence is identical every pass; only its order moves."""
    conditions = build_conditions(DEFAULT_N_PASSES)
    ev = evidence(spec="fixture")
    rendered = [render_prompt(ev, c) for c in conditions]

    for text in rendered:
        for heading in ("UNIFIED DIFF", "ACCEPTANCE CRITERIA", "STATIC ANALYSIS", "TEST RESULTS", "TASK SPEC"):
            assert heading in text
    assert len(set(rendered)) == len(rendered)


def test_the_diff_body_is_never_permuted() -> None:
    """Reordering hunks would change what the diff means — a different change per pass."""
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-a\n+b\n-c\n+d\n"
    for condition in build_conditions(DEFAULT_N_PASSES):
        assert diff in render_prompt(evidence(diff=diff), condition)


def test_the_temperature_ladder_is_clamped() -> None:
    """Past ~1.0 the output is noise, which would report instability that means nothing."""
    conditions = build_conditions(6)
    assert max(c.temperature for c in conditions) <= MAX_TEMPERATURE


def test_more_passes_than_distinct_conditions_is_refused() -> None:
    """The ladder saturates and the rotation cycles, so past some N two passes share a
    condition — and passes that share one agree trivially. Continuing would produce a
    `stability_score` from non-independent passes, which is exactly the decorative number
    this design exists to eliminate, so it is refused rather than reported as a flag.
    """
    largest = max(n for n in range(1, 60) if _conditions_are_distinct(n))
    assert build_conditions(largest), "the ceiling itself must still build"
    with pytest.raises(ValueError, match="distinct stability conditions"):
        build_conditions(largest + 1)


def _conditions_are_distinct(n: int) -> bool:
    try:
        conditions = build_conditions(n)
    except ValueError:
        return False
    return len({c.describe() for c in conditions}) == n


def test_the_measurement_reports_whether_the_conditions_were_distinct() -> None:
    """Auditable from the tool's own JSON rather than taken on trust."""
    measurement = measure_stability(evidence(), ScriptedClient(["pass", "pass", "pass"]))
    assert measurement.conditions_distinct is True


def test_a_single_pass_is_allowed_but_zero_is_not() -> None:
    assert len(build_conditions(1)) == 1
    with pytest.raises(ValueError, match="at least 1"):
        build_conditions(0)


def test_rotation_covers_every_evidence_section() -> None:
    orders = {c.evidence_order for c in build_conditions(len(EVIDENCE_SECTIONS))}
    assert len(orders) == len(EVIDENCE_SECTIONS)


# ------------------------------------------------------- oscillation and escalation


def test_three_stable_passes_score_one() -> None:
    """The first Done-when leg."""
    measurement = measure_stability(evidence(), ScriptedClient(["pass", "pass", "pass"]))
    assert measurement.stability_score == 1.0
    assert measurement.stable is True
    assert escalation_for(measurement, online=True) == "none"


def test_oscillating_verdicts_score_low_and_flag_for_escalation() -> None:
    """The second Done-when leg: the extension is told to re-judge, not the backend."""
    measurement = measure_stability(evidence(), ScriptedClient(["pass", "impl_fail", "pass"]))
    assert measurement.stability_score == 0.0
    assert measurement.stable is False
    assert escalation_for(measurement, online=True) == "flash_rejudgment"


def test_offline_instability_never_asks_for_a_flash_call() -> None:
    """REQ-AUD-002 AC1 — no Flash call offline, however unstable the score."""
    measurement = measure_stability(evidence(), ScriptedClient(["pass", "impl_fail", "pass"]))
    assert escalation_for(measurement, online=False) == "offline_majority"


def test_an_unreachable_judgment_is_an_error_not_a_low_score() -> None:
    """An outage is not a wavering Auditor, and must not be reported as one."""
    with pytest.raises(StabilityMeasurementError, match="could not be run"):
        measure_stability(evidence(), ExplodingClient())


def test_the_judgment_declares_its_payload_as_source() -> None:
    """The diff and spec are file bodies; the on-box guard re-checks the endpoint."""
    client = ScriptedClient(["pass", "pass", "pass"])
    measure_stability(evidence(), client)
    assert all(c["contains_raw_source"] for c in client.calls)


# -------------------------------------------------------------------- verdict parsing


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"verdict": "pass", "detail": "ok"}', "pass"),
        ('here you go:\n{"verdict": "gaming_suspected"}\nthanks', "gaming_suspected"),
        ("I judge this impl_fail.", "impl_fail"),
        ("spec_defect", "spec_defect"),
        ("I have no idea what this code does.", UNPARSEABLE_VERDICT),
        ("", UNPARSEABLE_VERDICT),
    ],
)
def test_verdicts_are_read_not_guessed(raw: str, expected: str) -> None:
    assert parse_verdict(raw)[0] == expected


def test_an_unparseable_pass_lowers_stability_rather_than_agreeing() -> None:
    """Coercing it into a neighbour's verdict would manufacture the very agreement
    the measurement exists to detect the absence of."""
    measurement = measure_stability(evidence(), ScriptedClient(["pass", "pass", "pass"]))
    assert measurement.stability_score == 1.0

    class Garbage:
        def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
            return "no verdict here at all"

    unreadable = measure_stability(evidence(), Garbage())
    assert unreadable.verdicts == [UNPARSEABLE_VERDICT] * DEFAULT_N_PASSES
    assert unreadable.majority_verdict == UNPARSEABLE_VERDICT


def test_a_tie_is_not_resolved_by_severity() -> None:
    """A tie is exactly the disagreement AC2's Flash re-judgment exists to settle."""
    assert majority_verdict(["pass", "impl_fail"]) == "pass"
    assert majority_verdict(["impl_fail", "pass"]) == "impl_fail"


# ----------------------------------------------------------- 10.2 gaming heuristics


def test_a_fixture_literal_return_is_flagged() -> None:
    """REQ-AUD-001: the diff satisfies the test by returning what the test asserts."""
    diff = '--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n+    return "alice@example.com"\n'
    spec = 'def test_email():\n    assert lookup(1) == "alice@example.com"\n'
    report = run_heuristics(diff, spec)
    assert FIXTURE_RETURN in report.names
    assert "alice@example.com" in report.flags[0].detail


def test_an_empty_body_under_test_is_flagged() -> None:
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n+def charge(amount):\n+    pass\n"
    assert EMPTY_BODY in run_heuristics(diff, "").names


@pytest.mark.parametrize(
    "stub",
    ["    pass", "    ...", "    raise NotImplementedError", "    return None"],
)
def test_stub_bodies_are_recognised_across_shapes(stub: str) -> None:
    diff = f"--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n+def charge(amount):\n+{stub}\n"
    assert EMPTY_BODY in run_heuristics(diff, "").names


def test_a_branch_keyed_on_a_test_input_is_flagged() -> None:
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n+    if user_id == 4242:\n+        return True\n"
    spec = "def test_admin():\n    assert is_admin(4242)\n"
    assert TEST_KEYED_BRANCH in run_heuristics(diff, spec).names


def test_real_work_raises_no_flag() -> None:
    """A detector that always fires carries no information and burns the retry budget."""
    diff = (
        "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n"
        "+def total(items):\n"
        "+    return sum(item.price for item in items)\n"
    )
    spec = "def test_total():\n    assert total([Item(1500)]) == 1500\n"
    assert run_heuristics(diff, spec).fired is False


def test_only_added_lines_are_examined() -> None:
    """A `-` line is what the Builder removed; flagging it blames them for old code."""
    diff = '--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-    return "alice@example.com"\n+    return lookup(user)\n'
    assert run_heuristics(diff, 'assert x == "alice@example.com"').fired is False


def test_the_file_header_is_not_mistaken_for_added_code() -> None:
    """`+++ b/path` starts with `+`, and a path can carry a spec literal."""
    assert added_lines("--- a/tests/alice.py\n+++ b/tests/alice.py\n@@ -1 +1 @@\n+x = 1\n") == ["x = 1"]


def test_short_and_small_literals_are_not_fixtures() -> None:
    """`0`, `1`, `""` appear everywhere; matching them flags every diff."""
    literals = extract_fixture_literals('assert f(1) == 0 and g() == "a" and h() == "alice@example.com"')
    assert "alice@example.com" in literals
    assert "a" not in literals
    assert "1" not in literals


# ------------------------------------------------ 10.2 composition: REQ-AUD-001 AC1


def test_a_flagged_diff_the_judgment_clears_is_a_pass() -> None:
    """"unless judgment clears it" — the branch that lets heuristics be imprecise."""
    diff = '--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n+    return "alice@example.com"\n'
    spec = 'assert lookup(1) == "alice@example.com"'
    result, _, report = audit_diff(evidence(diff=diff, spec=spec), ScriptedClient(["pass"] * 3))
    assert report.fired is True
    assert result.reason == "pass"
    assert result.status == "pass"
    assert result.next_action == "next_task"


def test_a_flagged_diff_the_judgment_does_not_clear_is_gaming_suspected() -> None:
    diff = '--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n+    return "alice@example.com"\n'
    spec = 'assert lookup(1) == "alice@example.com"'
    result, _, _ = audit_diff(evidence(diff=diff, spec=spec), ScriptedClient(["impl_fail"] * 3))
    assert result.reason == "gaming_suspected"
    assert result.next_action == "builder_retry"


def test_a_hardcoded_to_fixtures_diff_ends_as_gaming_suspected() -> None:
    """The Done-when leg, end to end and in its realistic shape.

    A diff that returns the spec's own fixture, with a judgment that agrees: heuristics
    fire, the judgment does not clear them, and the verdict routes to a Builder retry
    carrying the flagged patterns (REQ-AUD-003).
    """
    diff = (
        "--- a/src/users.py\n+++ b/src/users.py\n@@ -1,2 +1,2 @@\n"
        " def lookup(user_id):\n"
        '+    return "alice@example.com"\n'
    )
    spec = 'def test_lookup():\n    assert lookup(4242) == "alice@example.com"\n'

    result, measurement, report = audit_diff(
        evidence(diff=diff, spec=spec), ScriptedClient(["gaming_suspected"] * 3)
    )

    assert FIXTURE_RETURN in report.names
    assert result.reason == "gaming_suspected"
    assert result.status == "fail"
    assert result.next_action == "builder_retry"
    assert measurement.stability_score == 1.0, "three agreeing passes are stable"


def test_an_unflagged_diff_keeps_the_judgment_verdict() -> None:
    result, _, report = audit_diff(evidence(), ScriptedClient(["impl_fail"] * 3))
    assert report.fired is False
    assert result.reason == "impl_fail"


def test_the_detail_comes_from_a_pass_that_returned_the_majority_verdict() -> None:
    """Taking `passes[0]` unconditionally pairs a `pass` reason with the impl_fail
    pass's sentence — an `audit_result` whose own detail contradicts its verdict."""
    result, measurement, _ = audit_diff(
        evidence(), ScriptedClient(["impl_fail", "pass", "pass"])
    )
    assert result.reason == "pass"
    assert measurement.passes[0].verdict == "impl_fail"
    assert result.detail == "pass 2", "the detail must come from a pass that said 'pass'"


def test_spec_defect_routes_to_a_respec_not_a_retry() -> None:
    """REQ-AUD-003 AC1 / REQ-FAIL-001 AC3: it does not consume a Builder retry."""
    result, _, _ = audit_diff(evidence(), ScriptedClient(["spec_defect"] * 3))
    assert result.reason == "spec_defect"
    assert result.next_action == "test_intent_respec"


def test_an_unreadable_judgment_flags_a_human_rather_than_the_builder() -> None:
    """Nothing here says the diff is wrong, so a Builder retry would be an accusation."""

    class Garbage:
        def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
            return "???"

    result, _, _ = audit_diff(evidence(), Garbage())
    assert result.next_action == "flag_human"
    assert result.status == "fail"
    assert "no readable verdict" in result.detail


def test_the_flags_reach_the_judgment_prompt() -> None:
    """AC1 makes a flag a suspicion, so the model has to be able to see and clear it."""
    diff = '--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n+    return "alice@example.com"\n'
    spec = 'assert lookup(1) == "alice@example.com"'
    ev = evidence(diff=diff, spec=spec)
    report = run_heuristics(diff, spec)
    enriched = evidence_with_heuristics(ev, report)
    assert "ANTI-GAMING FLAGS" in enriched.static_report
    assert enriched.diff == ev.diff, "only the static block changes"


def test_an_unflagged_diff_gets_untouched_evidence() -> None:
    ev = evidence()
    assert evidence_with_heuristics(ev, run_heuristics(ev.diff, "")) is ev


def test_instability_is_recorded_in_the_detail() -> None:
    """The extension needs to see why a `fail` might not be final (AC2)."""
    result, _, _ = audit_diff(
        evidence(), ScriptedClient(["impl_fail", "pass", "impl_fail"]), threshold=0.9
    )
    assert "stability" in result.detail
    assert "uncalibrated" in result.detail


def test_a_calibrated_threshold_says_so() -> None:
    result, _, _ = audit_diff(
        evidence(),
        ScriptedClient(["impl_fail", "pass", "impl_fail"]),
        threshold=0.9,
        threshold_calibrated=True,
    )
    assert "uncalibrated" not in result.detail


def test_resolve_reason_is_a_pure_function_of_the_two_halves() -> None:
    measurement = measure_stability(evidence(), ScriptedClient(["pass"] * 3))
    reason, _ = resolve_reason(measurement, run_heuristics(evidence().diff, ""))
    assert reason == "pass"


def test_the_result_validates_as_the_typed_contract() -> None:
    """REQ-CON-006: what this emits must survive the contract's own validator."""
    result, _, _ = audit_diff(evidence(), ScriptedClient(["pass"] * 3))
    assert AuditResult.model_validate(result.model_dump()).status == "pass"


# ------------------------------------------------------- G-005: the temperature knob


def test_the_local_client_accepts_a_per_pass_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    """G-005: `chat` pinned temperature, so "distinct conditions" was unsatisfiable."""
    client = LocalClient()
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None: ...

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": '{"verdict": "pass"}'}}]}

    class FakeClient:
        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *exc: object) -> None: ...

        def post(self, url: str, json: dict[str, Any], timeout: float) -> FakeResponse:
            captured.update(json)
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    client.chat([{"role": "user", "content": "x"}], thinking=False, model="A_STD", temperature=0.4)

    assert captured["temperature"] == pytest.approx(0.4)


@pytest.mark.parametrize("bad", [-0.1, 2.1])
def test_an_out_of_range_temperature_is_refused_not_forwarded(bad: float) -> None:
    """A silently clamped value leaves two "distinct" passes identical."""
    with pytest.raises(ValueError, match=r"\[0.0, 2.0\]"):
        LocalClient().chat([{"role": "user", "content": "x"}], thinking=False, temperature=bad)


# ------------------------------------------------------------------------ entrypoint


def run_tool(*args: str, stdin: str | None = None) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.compute_stability", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        input=stdin,
        timeout=300,
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


def test_the_entrypoint_reports_usage_errors_without_touching_a_model(tmp_path: Path) -> None:
    code, payload = run_tool("--repo", str(tmp_path), "--task-id", "T1", "--diff", "-", stdin="")
    assert code == EXIT_USAGE
    assert payload["ok"] is False


def test_the_entrypoint_rejects_a_bad_pass_count(tmp_path: Path) -> None:
    code, payload = run_tool("--repo", str(tmp_path), "--task-id", "T1", "--diff", "-", "--passes", "0")
    assert code == EXIT_USAGE
    assert "passes" in payload["detail"]


def test_the_entrypoint_help_exits_zero() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.compute_stability", "--help"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
        check=False,
    )
    assert completed.returncode == EXIT_OK


def test_the_entrypoint_emits_the_contract_in_process(tmp_path: Path) -> None:
    """Driven in-process because the subprocess would need a Saltnitor to judge.

    `run(argv, client=...)` exists for exactly this: the entrypoint's own assembly —
    argument handling, threshold loading, payload shape, exit code — is exercised, and
    only the network client is substituted.
    """
    from saltcode.tools.compute_stability import run as run_entry

    diff = tmp_path / "d.patch"
    diff.write_text("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n", encoding="utf-8")

    code = run_entry(
        ["--repo", str(tmp_path), "--task-id", "T1", "--diff", str(diff)],
        client=ScriptedClient(["pass"] * 3),
    )
    assert code == EXIT_OK

    code = run_entry(
        ["--repo", str(tmp_path), "--task-id", "T1", "--diff", str(diff)],
        client=ScriptedClient(["impl_fail"] * 3),
    )
    assert code == EXIT_VERDICT_NEGATIVE

    # An unreachable Saltnitor is exit 3, never a low-stability verdict at exit 1 —
    # this is the branch that keeps an outage from reading as "the Auditor is unsure".
    code = run_entry(
        ["--repo", str(tmp_path), "--task-id", "T1", "--diff", str(diff)],
        client=ExplodingClient(),
    )
    assert code == EXIT_ERROR


def test_a_mis_encoded_evidence_file_returns_a_usage_code(tmp_path: Path) -> None:
    """`UnicodeDecodeError` is a ValueError, so it would otherwise escape `run` entirely.

    `main` absorbs it, but `run(argv, client=...)` is a documented in-process entrypoint
    whose contract is to return an exit code.
    """
    from saltcode.tools.compute_stability import run as run_entry

    diff = tmp_path / "d.patch"
    diff.write_text("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n", encoding="utf-8")
    binary = tmp_path / "static.bin"
    binary.write_bytes(b"\xff\xfe\x00\x80 not utf-8")

    code = run_entry(
        ["--repo", str(tmp_path), "--task-id", "T1", "--diff", str(diff), "--static", str(binary)],
        client=ScriptedClient(["pass"] * 3),
    )
    assert code == EXIT_USAGE


# ------------------------------- G-020 / G-021: the 2026-08-02 requirements amendments


def test_a_spec_defect_judgment_wins_over_a_heuristic_flag() -> None:
    """REQ-AUD-001 AC1's carve-out, added 2026-08-02 (G-021).

    Before it, any non-`pass` verdict left `gaming_suspected` standing. For `spec_defect`
    that spent a Builder retry REQ-AUD-003 AC1 says must not be spent, and sent the Builder
    to fix code whose *tests* the Auditor believes are wrong.
    """
    # A diff that trips a heuristic (a fixture literal returned outright) *and* a judgment
    # that says the spec is the problem.
    gaming_diff = (
        "--- a/m.py\n+++ b/m.py\n@@ -1 +1,2 @@\n"
        '+def total():\n+    return "expected_value"\n'
    )
    result, _, report = audit_diff(
        evidence(diff=gaming_diff, spec='assert total() == "expected_value"'),
        ScriptedClient(["spec_defect"] * 3),
    )

    assert report.fired, "the fixture-literal heuristic should have fired on this diff"
    assert result.reason == "spec_defect"
    assert result.next_action == "test_intent_respec"
    # The flags are still surfaced — the operator should see what fired — they just do not
    # change the routing.
    assert "spec_defect" in result.detail


def test_a_heuristic_flag_still_beats_impl_fail() -> None:
    """The carve-out is narrow. `impl_fail` and `gaming_suspected` both route to a Builder
    retry, so the gaming reason is kept for carrying strictly more detail."""
    gaming_diff = (
        "--- a/m.py\n+++ b/m.py\n@@ -1 +1,2 @@\n"
        '+def total():\n+    return "expected_value"\n'
    )
    result, _, report = audit_diff(
        evidence(diff=gaming_diff, spec='assert total() == "expected_value"'),
        ScriptedClient(["impl_fail"] * 3),
    )
    assert report.fired
    assert result.reason == "gaming_suspected"


def test_gac_is_one_based_and_bounded_by_n_passes() -> None:
    """REQ-CON-006 AC4 (G-020). The contract now rejects both readings it used to admit."""
    from pydantic import ValidationError

    from saltcode.contracts.audit_result import StabilityInfo

    ok = StabilityInfo(n_passes=3, verdicts=["pass"] * 3, stability_score=1.0, gac=1)
    assert ok.gac == 1

    with pytest.raises(ValidationError):
        StabilityInfo(n_passes=3, verdicts=["pass"] * 3, stability_score=1.0, gac=0)

    with pytest.raises(ValidationError):
        StabilityInfo(n_passes=3, verdicts=["pass"] * 3, stability_score=1.0, gac=4)
