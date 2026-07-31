"""Task 14b — the measured-then-fixed threshold protocol (REQ-CAL-001, REQ-AUD-005).

The failure this task exists to prevent is a threshold that *claims* to be measured. So
the assertions are mostly about refusal: a set too small, too one-sided, or missing a
whole half must leave the corresponding flag reading `calibrated: false` rather than
producing a number with a `true` beside it.

Everything runs against in-process doubles — there is no Saltnitor and no embedding
endpoint on this host — and nothing here touches the network.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from saltcode.providers.embeddings import EmbeddingClient
from saltcode.stability.calibrate import (
    COSINE_MARGIN,
    MIN_SAMPLES,
    PCD_HIGH_QUANTILE,
    PCD_LOW_QUANTILE,
    AuditorSample,
    CalibrationError,
    CalibrationSet,
    SemanticPair,
    build_artifact,
    calibrate_auditor,
    calibrate_semantic,
    choose_max_f1_threshold,
    cosine_similarity,
    distribution,
    f1_at_threshold,
    quantile,
    write_artifact,
)
from saltcode.thresholds import (
    AUDITOR_STABILITY,
    DEFAULTS,
    PCD_HIGH_BAR,
    PCD_LOW_BAR,
    SEMANTIC_COSINE,
    load_thresholds,
)
from saltcode.tools._cli import EXIT_OK, EXIT_USAGE, EXIT_VERDICT_NEGATIVE

REPO_ROOT = Path(__file__).resolve().parents[1]

DIFF = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"


class VerdictClient:
    """Returns a scripted verdict sequence per sample id, so stability is controllable."""

    def __init__(self, per_sample: dict[str, list[str]]) -> None:
        self.per_sample = per_sample
        self.seen: dict[str, int] = {}

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
        header = messages[-1]["content"].splitlines()[0]
        sample_id = header.removeprefix("# TASK ").strip()
        index = self.seen.get(sample_id, 0)
        self.seen[sample_id] = index + 1
        sequence = self.per_sample[sample_id]
        return json.dumps({"verdict": sequence[index % len(sequence)]})


class AxisEmbedding(EmbeddingClient):
    """Maps text to an orthogonal-ish unit vector so cosines are exact and hand-checkable."""

    model_name = "axis-test"

    def __init__(self, axes: dict[str, list[float]]) -> None:
        self.axes = axes

    def _vector(self, text: str) -> list[float]:
        for key, vector in self.axes.items():
            if key in text:
                return vector
        return [0.0, 0.0, 1.0]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


def sample(sid: str, expected: str = "pass") -> AuditorSample:
    return AuditorSample(id=sid, diff=DIFF, expected_verdict=expected, spec_content="assert True")


# ------------------------------------------------------------------------ statistics


@pytest.mark.parametrize(
    ("threshold", "expected"),
    [
        # scores [0.9, 0.8, 0.2, 0.1], labels [T, T, F, F]
        (0.8, 1.0),  # both positives predicted, no false positives
        (0.0, 2 / 3),  # everything predicted positive: precision 0.5, recall 1.0
        (1.0, 0.0),  # nothing predicted positive
    ],
)
def test_f1_is_the_standard_definition(threshold: float, expected: float) -> None:
    scores = [0.9, 0.8, 0.2, 0.1]
    labels = [True, True, False, False]
    assert f1_at_threshold(scores, labels, threshold) == pytest.approx(expected)


def test_a_threshold_nothing_clears_scores_zero_not_one() -> None:
    """Otherwise max-F1 would happily select a bar that predicts nothing."""
    assert f1_at_threshold([0.1, 0.2], [True, True], 0.9) == 0.0


def test_max_f1_finds_the_separating_threshold() -> None:
    threshold, f1 = choose_max_f1_threshold([0.9, 0.8, 0.2, 0.1], [True, True, False, False])
    assert f1 == pytest.approx(1.0)
    assert threshold == pytest.approx(0.8)


def test_ties_break_toward_the_higher_threshold() -> None:
    """Two bars equal on this set will not behave equally off it; the higher escalates more."""
    threshold, f1 = choose_max_f1_threshold([0.9, 0.8], [True, True])
    assert f1 == pytest.approx(1.0)
    assert threshold == pytest.approx(0.8), "0.0 and 0.8 both score 1.0 here"


def test_a_set_with_no_trustworthy_sample_is_refused() -> None:
    """F1 is 0 everywhere, so the "maximum" would be an arbitrary pick called a measurement."""
    with pytest.raises(CalibrationError, match="nothing for the threshold to separate"):
        choose_max_f1_threshold([0.5, 0.9], [False, False])


def test_too_few_samples_is_refused() -> None:
    with pytest.raises(CalibrationError, match=f"at least {MIN_SAMPLES}"):
        choose_max_f1_threshold([0.5], [True])


def test_the_distribution_is_documented_as_data() -> None:
    """REQ-CAL-001 AC3 / REQ-AUD-005 AC3 want the distribution recorded, not rendered."""
    summary = distribution([0.0, 0.5, 1.0], bins=4)
    assert summary["count"] == 3
    assert summary["min"] == 0.0
    assert summary["max"] == 1.0
    assert sum(b["count"] for b in summary["bins"]) == 3, "every value lands in exactly one bin"


def test_a_degenerate_distribution_does_not_divide_by_zero() -> None:
    summary = distribution([0.7, 0.7, 0.7])
    assert summary["count"] == 3
    assert sum(b["count"] for b in summary["bins"]) == 3


def test_an_empty_distribution_is_reported_as_empty() -> None:
    assert distribution([])["count"] == 0


def test_quantiles_interpolate() -> None:
    assert quantile([0.0, 1.0], 0.5) == pytest.approx(0.5)
    assert quantile([0.0, 0.5, 1.0], 0.0) == pytest.approx(0.0)
    assert quantile([0.0, 0.5, 1.0], 1.0) == pytest.approx(1.0)


def test_cosine_of_orthogonal_vectors_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


# ------------------------------------------------------------- 14b.2 the Auditor bar


def test_the_auditor_bar_is_measured_from_the_labelled_set() -> None:
    """Stable-and-correct scores 1.0; oscillating-and-wrong scores 0.0."""
    samples = [sample("good", "pass"), sample("bad", "pass")]
    client = VerdictClient(
        {
            "good": ["pass", "pass", "pass"],  # stable, majority matches the label
            # Unstable *and* wrong. Note the majority must genuinely differ from the
            # label: a three-way split ties back to the first verdict seen, which would
            # make this sample trustworthy and the fixture meaningless.
            "bad": ["impl_fail", "pass", "impl_fail"],
        }
    )

    result = calibrate_auditor(samples, client)

    assert result.scores["good"] == pytest.approx(1.0)
    assert result.scores["bad"] == pytest.approx(0.0)
    assert result.threshold == pytest.approx(1.0)
    assert result.f1 == pytest.approx(1.0)
    assert result.n_trustworthy == 1


def test_the_calibrated_bar_differs_from_the_default() -> None:
    """A Done-when leg: calibration has to actually move the number."""
    samples = [sample("good", "pass"), sample("bad", "pass")]
    client = VerdictClient({"good": ["pass"] * 3, "bad": ["pass", "impl_fail", "pass"]})
    result = calibrate_auditor(samples, client)
    assert result.threshold != DEFAULTS[AUDITOR_STABILITY]


def test_a_different_model_yields_a_different_threshold() -> None:
    """A Done-when leg, and REQ-CAL-001 AC4's reason for re-running on a model change.

    The two clients stand in for two models judging the same set: one stable, one that
    changes its mind. The measured bar follows the model, which is the whole point of
    measuring it per project rather than shipping a constant.
    """
    samples = [sample("a", "pass"), sample("b", "pass"), sample("c", "pass")]

    steady = calibrate_auditor(
        samples,
        VerdictClient({"a": ["pass"] * 3, "b": ["pass"] * 3, "c": ["pass", "impl_fail", "pass"]}),
    )
    wobbly = calibrate_auditor(
        samples,
        VerdictClient(
            {
                "a": ["pass", "pass", "impl_fail"],
                "b": ["pass", "pass", "impl_fail"],
                "c": ["impl_fail", "pass", "impl_fail"],
            }
        ),
    )

    assert steady.threshold != wobbly.threshold


def test_the_auditor_calibration_records_both_distributions() -> None:
    """REQ-AUD-005 AC3: correct vs wrong, so the separation is inspectable."""
    samples = [sample("good", "pass"), sample("bad", "pass")]
    result = calibrate_auditor(
        samples, VerdictClient({"good": ["pass"] * 3, "bad": ["impl_fail", "pass", "impl_fail"]})
    )
    assert result.distribution_trustworthy["count"] == 1
    assert result.distribution_untrustworthy["count"] == 1
    assert result.distribution_all["count"] == 2


def test_a_one_sample_auditor_set_is_refused() -> None:
    with pytest.raises(CalibrationError, match="at least"):
        calibrate_auditor([sample("only")], VerdictClient({"only": ["pass"] * 3}))


# ------------------------------------------------------------ 14b.3 the semantic bars


def semantic_set() -> list[SemanticPair]:
    return [
        SemanticPair(id="m1", goal_a="add login alpha", goal_b="implement sign-in alpha", match=True),
        SemanticPair(id="m2", goal_a="add login alpha", goal_b="build auth alpha", match=True),
        SemanticPair(id="n1", goal_a="add login alpha", goal_b="rewrite billing beta", match=False),
    ]


def axis_client() -> AxisEmbedding:
    return AxisEmbedding({"alpha": [1.0, 0.0, 0.0], "beta": [0.0, 1.0, 0.0]})


def test_the_cosine_bar_sits_just_above_the_highest_non_match() -> None:
    """Not at max-F1: a false match reuses a plan written for a different goal."""
    result = calibrate_semantic(semantic_set(), axis_client())
    assert result.max_non_match_cosine == pytest.approx(0.0)
    assert result.cosine_threshold == pytest.approx(COSINE_MARGIN)
    assert result.min_match_cosine == pytest.approx(1.0)
    assert result.separable is True


def test_an_overlapping_set_is_reported_as_not_separable() -> None:
    """A fact about the embedding model on this project's goals — the operator's call."""
    pairs = [
        SemanticPair(id="m1", goal_a="alpha one", goal_b="alpha two", match=True),
        SemanticPair(id="n1", goal_a="alpha three", goal_b="alpha four", match=False),
        SemanticPair(id="n2", goal_a="alpha five", goal_b="beta six", match=False),
    ]
    result = calibrate_semantic(pairs, axis_client())
    assert result.separable is False


def test_the_bar_never_exceeds_one() -> None:
    pairs = [
        SemanticPair(id="m1", goal_a="alpha a", goal_b="alpha b", match=True),
        SemanticPair(id="n1", goal_a="alpha c", goal_b="alpha d", match=False),
    ]
    assert calibrate_semantic(pairs, axis_client()).cosine_threshold <= 1.0


def test_the_pcd_bars_come_from_the_observed_distribution() -> None:
    result = calibrate_semantic(semantic_set(), axis_client())
    observed = sorted(result.pcd_values.values())
    assert result.pcd_low_bar == pytest.approx(quantile(observed, PCD_LOW_QUANTILE))
    assert result.pcd_high_bar == pytest.approx(quantile(observed, PCD_HIGH_QUANTILE))


def test_pcd_holds_each_query_out_of_its_own_corpus() -> None:
    """Counting the query against itself adds a systematic 1/n to every value.

    Runtime PCD measures a goal against a cache it is not in — an exactly-cached goal
    would have hit tier 1 — so self-inclusion is a bias, and on a small calibration set
    it is most of the signal.
    """
    pairs = [
        SemanticPair(id="m1", goal_a="alpha a", goal_b="alpha b", match=True),
        SemanticPair(id="n1", goal_a="beta c", goal_b="beta d", match=False),
    ]
    result = calibrate_semantic(pairs, axis_client())
    # Two queries on orthogonal axes: neither is near the other, so both PCDs are 0.
    # With self-inclusion each would have been 0.5.
    assert set(result.pcd_values.values()) == {0.0}


def test_a_one_sided_semantic_set_is_refused() -> None:
    """With no non-matches there is nothing for the bar to sit above."""
    pairs = [
        SemanticPair(id="m1", goal_a="alpha a", goal_b="alpha b", match=True),
        SemanticPair(id="m2", goal_a="alpha c", goal_b="alpha d", match=True),
    ]
    with pytest.raises(CalibrationError, match="both matching and non-matching"):
        calibrate_semantic(pairs, axis_client())


def test_a_short_embedding_response_is_refused_not_silently_truncated() -> None:
    class ShortClient(EmbeddingClient):
        def embed_query(self, text: str) -> list[float]:
            return [1.0]

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[1.0]]

    with pytest.raises(CalibrationError, match="returned 1 vectors"):
        calibrate_semantic(semantic_set(), ShortClient())


# ------------------------------------------------------- 14b.4 the artifact and flags


def test_only_measured_thresholds_appear_in_the_artifact() -> None:
    """That mapping is exactly what flips `calibrated: true`, so a spurious key forges it."""
    auditor = calibrate_auditor(
        [sample("a"), sample("b")],
        VerdictClient({"a": ["pass"] * 3, "b": ["pass", "impl_fail", "pass"]}),
    )
    artifact = build_artifact(auditor, None, set_name="s.json")
    assert set(artifact["thresholds"]) == {AUDITOR_STABILITY}
    assert SEMANTIC_COSINE not in artifact["thresholds"]


def test_a_semantic_only_run_writes_only_the_semantic_bars() -> None:
    semantic = calibrate_semantic(semantic_set(), axis_client())
    artifact = build_artifact(None, semantic, set_name="s.json")
    assert set(artifact["thresholds"]) == {SEMANTIC_COSINE, PCD_LOW_BAR, PCD_HIGH_BAR}


def test_the_artifact_records_what_it_was_measured_against() -> None:
    """REQ-CAL-001 AC4's "re-run on model change" is only checkable if this is recorded."""
    artifact = build_artifact(None, calibrate_semantic(semantic_set(), axis_client()),
                              set_name="s.json", judgment_model="B", embedding_model="axis-test")
    assert artifact["judgment_model"] == "B"
    assert artifact["embedding_model"] == "axis-test"
    assert artifact["calibration_set"] == "s.json"
    assert artifact["measured_at"]


def test_writing_the_artifact_marks_the_threshold_calibrated(tmp_path: Path) -> None:
    """The end-to-end leg: measured → written → read back as `calibrated: true`."""
    before = load_thresholds(tmp_path)
    assert before.auditor_stability_threshold.calibrated is False
    assert before.auditor_stability_threshold.value == DEFAULTS[AUDITOR_STABILITY]

    auditor = calibrate_auditor(
        [sample("a"), sample("b")],
        VerdictClient({"a": ["pass"] * 3, "b": ["pass", "impl_fail", "pass"]}),
    )
    write_artifact(tmp_path, build_artifact(auditor, None, set_name="s.json"))

    after = load_thresholds(tmp_path)
    assert after.auditor_stability_threshold.calibrated is True
    assert after.auditor_stability_threshold.source == "calibration"
    assert after.auditor_stability_threshold.value == pytest.approx(auditor.threshold)


def test_an_uncalibrated_half_still_warns(tmp_path: Path) -> None:
    """REQ-CAL-001 AC2, and the reason the flag is per threshold rather than global."""
    auditor = calibrate_auditor(
        [sample("a"), sample("b")],
        VerdictClient({"a": ["pass"] * 3, "b": ["pass", "impl_fail", "pass"]}),
    )
    write_artifact(tmp_path, build_artifact(auditor, None, set_name="s.json"))

    resolved = load_thresholds(tmp_path)
    assert resolved.fully_calibrated is False
    names = {t.name for t in resolved.uncalibrated()}
    assert names == {SEMANTIC_COSINE, PCD_LOW_BAR, PCD_HIGH_BAR}


def test_a_re_run_keeps_the_previous_measurement(tmp_path: Path) -> None:
    """REQ-CAL-001 AC4 makes re-runs routine; a protocol that erases its own history
    cannot show that a threshold moved."""
    first = build_artifact(None, calibrate_semantic(semantic_set(), axis_client()), set_name="s.json")
    first["measured_at"] = "2026-01-01T00:00:00+00:00"
    write_artifact(tmp_path, first)

    second = build_artifact(None, calibrate_semantic(semantic_set(), axis_client()), set_name="s.json")
    second["measured_at"] = "2026-06-01T00:00:00+00:00"
    write_artifact(tmp_path, second)

    stamped = sorted((tmp_path / ".saltcode" / "calibration").glob("thresholds-*.json"))
    assert len(stamped) == 2, [p.name for p in stamped]


def test_the_calibration_set_model_accepts_the_documented_shape() -> None:
    parsed = CalibrationSet.model_validate(
        {
            "auditor": [{"id": "a", "diff": DIFF, "expected_verdict": "pass"}],
            "semantic": [{"id": "p", "goal_a": "x", "goal_b": "y", "match": True}],
        }
    )
    assert len(parsed.auditor) == 1
    assert len(parsed.semantic) == 1


# ------------------------------------------------------------------------ entrypoint


def write_set(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_entrypoint_calibrates_and_writes(tmp_path: Path) -> None:
    from saltcode.tools.calibrate import run as run_entry

    path = write_set(
        tmp_path,
        {
            "auditor": [
                {"id": "a", "diff": DIFF, "expected_verdict": "pass"},
                {"id": "b", "diff": DIFF, "expected_verdict": "pass"},
            ]
        },
    )
    code = run_entry(
        ["--repo", str(tmp_path), "--set", str(path)],
        client=VerdictClient({"a": ["pass"] * 3, "b": ["pass", "impl_fail", "pass"]}),
    )
    assert code == EXIT_OK
    assert (tmp_path / ".saltcode" / "calibration" / "thresholds.json").is_file()
    assert load_thresholds(tmp_path).auditor_stability_threshold.calibrated is True


def test_entrypoint_dry_run_writes_nothing(tmp_path: Path) -> None:
    from saltcode.tools.calibrate import run as run_entry

    path = write_set(
        tmp_path,
        {
            "auditor": [
                {"id": "a", "diff": DIFF, "expected_verdict": "pass"},
                {"id": "b", "diff": DIFF, "expected_verdict": "pass"},
            ]
        },
    )
    code = run_entry(
        ["--repo", str(tmp_path), "--set", str(path), "--dry-run"],
        client=VerdictClient({"a": ["pass"] * 3, "b": ["pass", "impl_fail", "pass"]}),
    )
    assert code == EXIT_OK
    assert not (tmp_path / ".saltcode" / "calibration").exists()
    assert load_thresholds(tmp_path).auditor_stability_threshold.calibrated is False


def test_entrypoint_reports_an_unmeasurable_set_as_a_verdict(tmp_path: Path) -> None:
    from saltcode.tools.calibrate import run as run_entry

    path = write_set(tmp_path, {"auditor": [{"id": "a", "diff": DIFF, "expected_verdict": "pass"}]})
    code = run_entry(
        ["--repo", str(tmp_path), "--set", str(path)],
        client=VerdictClient({"a": ["pass"] * 3}),
    )
    assert code == EXIT_VERDICT_NEGATIVE
    assert load_thresholds(tmp_path).auditor_stability_threshold.calibrated is False


def test_entrypoint_reports_an_empty_set_without_writing(tmp_path: Path) -> None:
    from saltcode.tools.calibrate import run as run_entry

    path = write_set(tmp_path, {})
    assert run_entry(["--repo", str(tmp_path), "--set", str(path)]) == EXIT_VERDICT_NEGATIVE
    assert not (tmp_path / ".saltcode" / "calibration").exists()


def run_tool(*args: str) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.calibrate", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


def test_entrypoint_rejects_a_missing_set(tmp_path: Path) -> None:
    code, payload = run_tool("--repo", str(tmp_path), "--set", str(tmp_path / "nope.json"))
    assert code == EXIT_USAGE
    assert payload["ok"] is False


def test_entrypoint_rejects_a_malformed_set(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    code, payload = run_tool("--repo", str(tmp_path), "--set", str(path))
    assert code == EXIT_USAGE
    assert payload["ok"] is False


def test_entrypoint_rejects_a_set_that_fails_validation(tmp_path: Path) -> None:
    path = write_set(tmp_path, {"auditor": [{"id": "a"}]})
    code, payload = run_tool("--repo", str(tmp_path), "--set", str(path))
    assert code == EXIT_USAGE
    assert "not a valid calibration set" in payload["detail"]


def test_entrypoint_help_exits_zero() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "saltcode.tools.calibrate", "--help"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
        check=False,
    )
    assert completed.returncode == EXIT_OK
    assert "--set" in completed.stdout
