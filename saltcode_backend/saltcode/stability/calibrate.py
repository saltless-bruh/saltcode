"""The measured-then-fixed threshold protocol (task 14b, REQ-CAL-001, REQ-AUD-005).

Every threshold in Saltcode starts as a conservative guess that *announces itself as a
guess* (`saltcode/thresholds.py`). This module is how a guess becomes a measurement: it
takes a labelled calibration set, runs the real machinery over it, and derives each
threshold from the observed distribution rather than from anyone's intuition.

**The Auditor bar** (REQ-AUD-005). Each sample carries a diff and the verdict a human
says is correct. The N-pass measurement runs on it, producing a `stability_score` and a
majority verdict; the verdict is *trustworthy* when it matches the label. The threshold
is then the one maximising the F1 of "stable ⇒ trustworthy" over the set — the
requirement's own words. What makes this a measurement rather than a fit is that the
relationship being tested (does agreement across passes predict correctness?) is the
premise the whole design rests on, so a poor best-F1 is itself the finding.

**The semantic bar** (REQ-CACHE-003 AC4). Pairs of goals labelled match/non-match are
embedded and their cosines compared. The threshold sits **just above the highest
non-match cosine**, not at a best-F1 point, because the two errors are not symmetric: a
missed match costs one Phase-1 fire, while a false match reuses a plan written for a
different goal. BIFAI-NET's sim_floor protocol makes the same choice.

**Nothing here writes a threshold it did not measure.** A set with no semantic pairs
calibrates the Auditor bar alone and leaves the cosine bar reading `calibrated: false`,
because a partially calibrated project is the normal mid-protocol state and claiming
otherwise is the one outcome that would make the flag worthless.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from saltcode.memory.semantic_cache import get_semantic_query
from saltcode.providers.embeddings import EmbeddingClient
from saltcode.stability.measure import (
    DEFAULT_N_PASSES,
    AuditEvidence,
    JudgmentClient,
    measure_stability,
)
from saltcode.thresholds import (
    AUDITOR_STABILITY,
    CALIBRATION_FILE,
    PCD_HIGH_BAR,
    PCD_LOW_BAR,
    SEMANTIC_COSINE,
    calibration_dir,
)

CALIBRATION_SCHEMA_VERSION = "1"

COSINE_MARGIN = 1e-3
"""How far above the highest non-match cosine the semantic bar sits.

Small enough not to reject a genuine match that merely sits close to the boundary, large
enough that floating-point equality cannot put a known non-match on the reuse side.
"""

PCD_LOW_QUANTILE = 0.25
PCD_HIGH_QUANTILE = 0.75
"""Where the density bars are read off the observed PCD distribution.

**The specs do not give an estimator for these.** Design §11.9 names the PCD bars as
things calibration produces and stops there; REQ-CACHE-003 AC2/AC3 describe only what
happens on either side of them. Quartiles are the smallest reading consistent with the
surrounding design — "dense" and "sparse" relative to what this project's own cache
actually looks like — and the assumption is recorded in `specs/known_gaps.md`.
"""

MIN_SAMPLES = 2
"""Below this there is no distribution, only a data point.

A threshold "measured" from one sample is a guess wearing a `calibrated: true` flag,
which is worse than an honest default: the whole point of the flag is that a reader can
trust it.
"""


class CalibrationError(ValueError):
    """The calibration set cannot support the measurement asked of it."""


class AuditorSample(BaseModel):
    """One labelled diff for the Auditor calibration (REQ-AUD-005: known-good and known-gaming)."""

    id: str
    diff: str
    expected_verdict: str
    """The verdict a human says is correct — the ground truth the measurement is scored against."""
    spec_content: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list[str])
    static_report: str = ""
    test_results: str = ""


class SemanticPair(BaseModel):
    """Two goals a human has labelled as the same task or not."""

    id: str
    goal_a: str
    goal_b: str
    scope_a: list[str] = Field(default_factory=list[str])
    scope_b: list[str] = Field(default_factory=list[str])
    match: bool


class CalibrationSet(BaseModel):
    """The labelled benchmark `saltcode_calibrate` consumes.

    Either section may be empty; each calibrates its own thresholds, and neither is
    invented from the other.
    """

    schema_version: str = Field(default=CALIBRATION_SCHEMA_VERSION)
    auditor: list[AuditorSample] = Field(default_factory=list[AuditorSample])
    semantic: list[SemanticPair] = Field(default_factory=list[SemanticPair])

    @model_validator(mode="after")
    def _ids_are_unique(self) -> CalibrationSet:
        """Reject a repeated id, in either section, before anything is measured.

        Both calibration routines key their per-sample evidence by `id` while counting
        and binning per *record*. A duplicate id therefore silently overwrites the
        earlier entry in `scores`/`verdicts` (or `match_cosines`/`pcd_values`) while
        `n_samples` and the distribution still count both — so the artifact reports more
        samples than it has scores, and the thresholds are chosen from a corpus quietly
        smaller than the one the operator supplied. Refusing the set is the only honest
        outcome: silently dropping a labelled sample corrupts the measurement that the
        whole measured-then-fixed protocol exists to make trustworthy.
        """
        for label, ids in (
            ("auditor", [s.id for s in self.auditor]),
            ("semantic", [p.id for p in self.semantic]),
        ):
            duplicates = sorted(i for i, n in Counter(ids).items() if n > 1)
            if duplicates:
                raise ValueError(
                    f"the {label} section repeats these ids, so their measurements would "
                    f"overwrite one another: {', '.join(duplicates)}"
                )
        return self


def distribution(values: Sequence[float], bins: int = 10) -> dict[str, Any]:
    """A JSON-safe summary of a score distribution (REQ-CAL-001 AC3, REQ-AUD-005 AC3).

    The requirements call for the *distribution* to be documented. Task 14b.2 words it as
    "plot"; a histogram plus the five-number-ish summary is that data, and rendering it
    to an image would add a plotting dependency to a backend whose gates are meant to be
    bounded, GPU-free subprocesses (REQ-STAT-003). The deviation is recorded in
    `specs/tasks.md`.
    """
    if not values:
        # Every key the populated branch emits, so a reader of the persisted artifact
        # handles one shape rather than two. A missing key and a null one are different
        # failures to whatever consumes this, and only one of them is honest here.
        return {"count": 0, "bins": [], "min": None, "max": None, "mean": None, "median": None}

    ordered = sorted(values)
    lo, hi = ordered[0], ordered[-1]
    width = (hi - lo) / bins if hi > lo else 0.0

    counts = [0] * bins
    for value in ordered:
        # A value exactly at the top edge belongs to the last bin, not to a phantom one.
        index = bins - 1 if width == 0.0 or value >= hi else int((value - lo) / width)
        counts[min(max(index, 0), bins - 1)] += 1

    return {
        "count": len(ordered),
        "min": lo,
        "max": hi,
        "mean": sum(ordered) / len(ordered),
        "median": ordered[len(ordered) // 2],
        "bins": [
            {"lower": lo + i * width, "upper": lo + (i + 1) * width, "count": counts[i]}
            for i in range(bins)
        ],
    }


def quantile(values: Sequence[float], q: float) -> float:
    """The ``q``-quantile by linear interpolation between order statistics."""
    if not values:
        raise CalibrationError("cannot take a quantile of an empty sample")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def f1_at_threshold(scores: Sequence[float], labels: Sequence[bool], threshold: float) -> float:
    """F1 of the classifier "score >= threshold ⇒ trustworthy" (REQ-AUD-005).

    Returns 0.0 when the classifier predicts no positives, rather than dividing by zero:
    a bar so high nothing clears it has no precision to speak of, and reporting it as
    perfect would let :func:`choose_max_f1_threshold` select it.
    """
    tp = sum(1 for s, y in zip(scores, labels, strict=True) if s >= threshold and y)
    fp = sum(1 for s, y in zip(scores, labels, strict=True) if s >= threshold and not y)
    fn = sum(1 for s, y in zip(scores, labels, strict=True) if s < threshold and y)

    if tp == 0:
        return 0.0
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    return 2 * precision * recall / (precision + recall)


def choose_max_f1_threshold(
    scores: Sequence[float], labels: Sequence[bool]
) -> tuple[float, float]:
    """The threshold maximising F1, and that F1 (REQ-AUD-005).

    Candidates are the observed scores themselves: F1 as a function of the threshold only
    changes as the bar crosses a data point, so the observed values are the complete set
    of interesting positions.

    Ties break toward the **higher** threshold. Two bars with equal F1 on this set will
    not behave equally off it, and the higher one escalates more — an extra Flash
    re-judgment, versus trusting a local verdict that should have been checked.

    Raises:
        CalibrationError: fewer than :data:`MIN_SAMPLES` samples, or no trustworthy
            sample at all. Without a positive case F1 is 0 everywhere and the "maximum"
            would be an arbitrary pick dressed up as a measurement.
    """
    if len(scores) < MIN_SAMPLES:
        raise CalibrationError(
            f"need at least {MIN_SAMPLES} calibration samples to measure a threshold, got {len(scores)}"
        )
    if not any(labels):
        raise CalibrationError(
            "no sample in the calibration set produced a trustworthy verdict, so there is "
            "nothing for the threshold to separate. Check the expected verdicts, or that "
            "the judgment model is reachable and answering."
        )

    best_threshold, best_f1 = 0.0, -1.0
    for candidate in sorted(set(scores)):
        f1 = f1_at_threshold(scores, labels, candidate)
        if f1 > best_f1 or (f1 == best_f1 and candidate > best_threshold):
            best_threshold, best_f1 = candidate, f1
    return best_threshold, best_f1


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine of the angle between two vectors, 0.0 when either has no length."""
    if len(a) != len(b):
        raise CalibrationError(f"vector widths differ: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class AuditorCalibration(BaseModel):
    """The measured Auditor stability bar and the evidence behind it."""

    threshold: float
    f1: float
    n_samples: int
    n_trustworthy: int
    scores: dict[str, float]
    verdicts: dict[str, str]
    distribution_all: dict[str, Any]
    distribution_trustworthy: dict[str, Any]
    distribution_untrustworthy: dict[str, Any]


class SemanticCalibration(BaseModel):
    """The measured cosine bar, the PCD bars, and the evidence behind them."""

    cosine_threshold: float
    pcd_low_bar: float
    pcd_high_bar: float
    n_pairs: int
    n_matches: int
    max_non_match_cosine: float
    min_match_cosine: float
    separable: bool
    """Whether every match sits above every non-match.

    False means the labelled sets overlap, so no cosine bar separates them and the
    chosen one will miss real matches. Reported rather than smoothed over: it is a fact
    about the embedding model on this project's goals, and the operator's call.
    """
    match_cosines: dict[str, float]
    non_match_cosines: dict[str, float]
    pcd_values: dict[str, float]
    distribution_match: dict[str, Any]
    distribution_non_match: dict[str, Any]
    distribution_pcd: dict[str, Any]


def calibrate_auditor(
    samples: Sequence[AuditorSample],
    client: JudgmentClient,
    *,
    n_passes: int = DEFAULT_N_PASSES,
    model: str | None = None,
) -> AuditorCalibration:
    """Run the N-pass measurement over every labelled diff and pick the max-F1 bar.

    Raises:
        CalibrationError: The set is too small, or nothing in it was judged correctly.
    """
    if len(samples) < MIN_SAMPLES:
        raise CalibrationError(
            f"need at least {MIN_SAMPLES} auditor samples, got {len(samples)}"
        )

    scores: dict[str, float] = {}
    verdicts: dict[str, str] = {}
    labels: list[bool] = []
    ordered_scores: list[float] = []

    for sample in samples:
        measurement = measure_stability(
            AuditEvidence(
                task_id=sample.id,
                diff=sample.diff,
                acceptance_criteria=sample.acceptance_criteria,
                static_report=sample.static_report,
                test_results=sample.test_results,
                spec_content=sample.spec_content,
            ),
            client,
            n_passes=n_passes,
            model=model,
        )
        scores[sample.id] = measurement.stability_score
        verdicts[sample.id] = measurement.majority_verdict
        ordered_scores.append(measurement.stability_score)
        labels.append(measurement.majority_verdict == sample.expected_verdict)

    threshold, f1 = choose_max_f1_threshold(ordered_scores, labels)

    trustworthy = [s for s, y in zip(ordered_scores, labels, strict=True) if y]
    untrustworthy = [s for s, y in zip(ordered_scores, labels, strict=True) if not y]

    return AuditorCalibration(
        threshold=threshold,
        f1=f1,
        n_samples=len(samples),
        n_trustworthy=len(trustworthy),
        scores=scores,
        verdicts=verdicts,
        distribution_all=distribution(ordered_scores),
        distribution_trustworthy=distribution(trustworthy),
        distribution_untrustworthy=distribution(untrustworthy),
    )


def calibrate_semantic(
    pairs: Sequence[SemanticPair],
    embedding_client: EmbeddingClient,
) -> SemanticCalibration:
    """Measure the cosine bar from labelled pairs, then the PCD bars from the corpus.

    Raises:
        CalibrationError: The set is too small, or carries only one class of label —
            with no non-matches there is nothing for the bar to sit above, and with no
            matches there is nothing it must admit.
    """
    if len(pairs) < MIN_SAMPLES:
        raise CalibrationError(f"need at least {MIN_SAMPLES} semantic pairs, got {len(pairs)}")

    matches = [p for p in pairs if p.match]
    non_matches = [p for p in pairs if not p.match]
    if not matches or not non_matches:
        raise CalibrationError(
            "the semantic calibration set needs both matching and non-matching pairs: the "
            "bar is placed just above the highest non-match, and validated against the "
            "lowest match."
        )

    # One batch, so a remote endpoint is hit once rather than 2N times.
    queries: list[str] = []
    for pair in pairs:
        queries.append(get_semantic_query(pair.goal_a, pair.scope_a))
        queries.append(get_semantic_query(pair.goal_b, pair.scope_b))
    vectors = embedding_client.embed_documents(queries)
    if len(vectors) != len(queries):
        raise CalibrationError(
            f"the embedding endpoint returned {len(vectors)} vectors for {len(queries)} queries"
        )

    match_cosines: dict[str, float] = {}
    non_match_cosines: dict[str, float] = {}
    for i, pair in enumerate(pairs):
        similarity = cosine_similarity(vectors[2 * i], vectors[2 * i + 1])
        (match_cosines if pair.match else non_match_cosines)[pair.id] = similarity

    max_non_match = max(non_match_cosines.values())
    min_match = min(match_cosines.values())

    # Just above the highest known non-match. A missed match costs one Phase-1 fire; a
    # false match reuses a plan written for a different goal (REQ-CACHE-003).
    cosine_threshold = min(max_non_match + COSINE_MARGIN, 1.0)

    # PCD of each pair's first goal against the calibration corpus, at the bar just
    # chosen — the same quantity `lookup_semantic_result` computes at runtime, so the
    # bars are read off the distribution they will actually be compared against.
    #
    # Each query is held out of its own corpus. Runtime PCD measures a goal against a
    # cache it is not yet in — an exactly-cached goal would have hit tier 1 and never
    # reached here — so counting the query against itself would add a systematic 1/n to
    # every value, which on a small calibration set is most of the signal.
    corpus = [vectors[2 * i] for i in range(len(pairs))]
    pcd_values: dict[str, float] = {}
    for i, pair in enumerate(pairs):
        others = [v for j, v in enumerate(corpus) if j != i]
        near = sum(1 for other in others if cosine_similarity(vectors[2 * i], other) >= cosine_threshold)
        pcd_values[pair.id] = near / len(others)

    observed = list(pcd_values.values())
    return SemanticCalibration(
        cosine_threshold=cosine_threshold,
        pcd_low_bar=quantile(observed, PCD_LOW_QUANTILE),
        pcd_high_bar=quantile(observed, PCD_HIGH_QUANTILE),
        n_pairs=len(pairs),
        n_matches=len(matches),
        max_non_match_cosine=max_non_match,
        min_match_cosine=min_match,
        separable=min_match > max_non_match,
        match_cosines=match_cosines,
        non_match_cosines=non_match_cosines,
        pcd_values=pcd_values,
        distribution_match=distribution(list(match_cosines.values())),
        distribution_non_match=distribution(list(non_match_cosines.values())),
        distribution_pcd=distribution(observed),
    )


def build_artifact(
    auditor: AuditorCalibration | None,
    semantic: SemanticCalibration | None,
    *,
    set_name: str,
    judgment_model: str | None = None,
    embedding_model: str | None = None,
) -> dict[str, Any]:
    """Assemble the artifact `thresholds.py` reads and REQ-CAL-001 AC3 requires on disk.

    Only measured thresholds appear under ``thresholds``. That mapping is exactly what
    marks a threshold `calibrated: true` (`thresholds._resolve_one`), so writing a key
    for something this run did not measure would forge the flag.

    The model identities are recorded because REQ-CAL-001 AC4 requires re-calibration
    when the model or embedding changes — which is only checkable if the artifact says
    what it was measured against.
    """
    measured: dict[str, float] = {}
    if auditor is not None:
        measured[AUDITOR_STABILITY] = auditor.threshold
    if semantic is not None:
        measured[SEMANTIC_COSINE] = semantic.cosine_threshold
        measured[PCD_LOW_BAR] = semantic.pcd_low_bar
        measured[PCD_HIGH_BAR] = semantic.pcd_high_bar

    artifact: dict[str, Any] = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "measured_at": datetime.now(UTC).isoformat(),
        "calibration_set": set_name,
        "judgment_model": judgment_model,
        "embedding_model": embedding_model,
        "thresholds": measured,
    }
    if auditor is not None:
        artifact["auditor"] = auditor.model_dump()
    if semantic is not None:
        artifact["semantic"] = semantic.model_dump()
    return artifact


def write_artifact(workspace_path: Path | str, artifact: dict[str, Any]) -> Path:
    """Write the artifact under `.saltcode/calibration/` (REQ-CAL-001 AC3).

    Two files: `thresholds.json`, which `load_thresholds` reads, and a timestamped copy
    so a re-calibration after a model change does not erase what the previous one
    measured. REQ-CAL-001 AC4 makes re-runs routine, and a protocol that overwrites its
    own history cannot show that a threshold moved.
    """
    directory = calibration_dir(workspace_path)
    directory.mkdir(parents=True, exist_ok=True)

    body = json.dumps(artifact, indent=2, sort_keys=True)
    stamp = str(artifact.get("measured_at", "")).replace(":", "").replace("-", "")[:15]

    (directory / f"thresholds-{stamp}.json").write_text(body, encoding="utf-8")
    current = directory / CALIBRATION_FILE

    # Atomic, for the same reason `contracts/spec_compactor` writes atomically: a
    # truncating in-place write leaves a window in which `thresholds.json` is empty or
    # half-written, and `load_thresholds` runs on *every* tool invocation. Its failure
    # mode is silent — a malformed file falls back to the conservative defaults, so an
    # interrupted write would quietly un-calibrate every threshold rather than raise.
    staged: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=directory, prefix=".thresholds-", suffix=".tmp", delete=False, encoding="utf-8"
        ) as handle:
            staged = handle.name
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, current)
    except OSError:
        # `staged` stays None when opening the temp file itself failed (a read-only
        # directory), so cleanup must not assume it was assigned.
        if staged is not None:
            with contextlib.suppress(OSError):
                Path(staged).unlink()
        raise
    return current
