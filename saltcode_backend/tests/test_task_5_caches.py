"""Task 5.1–5.3, 5.6 — the cache ladder's two tiers, PCD, and threshold calibration.

Everything here runs against a deterministic in-process embedding double: this host has
no Saltnitor and no embedding endpoint, and a test that reached for one would either
skip (proving nothing) or touch the network (which the suite must never do).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from saltcode.contracts.tasks import Task, TasksFile
from saltcode.harness.phase_gate import compute_spec_hash
from saltcode.memory.lancedb_store import (
    NOTES_TABLE,
    SEMANTIC_CACHE_TABLE,
    SKILLS_TABLE,
    SPEC_CACHE_COMPATIBLE_VERSIONS,
    SPEC_CACHE_TABLE,
    STORE_SCHEMA_VERSION,
    EmbeddingDimensionMismatchError,
    EmbeddingDimensionUnavailableError,
    StoreSchemaVersionMismatchError,
    check_vector_dimension,
    init_db,
    probe_vector_dimension,
    read_store_meta,
    write_store_meta,
)
from saltcode.memory.semantic_cache import (
    confirmation_for_pcd,
    get_semantic_query,
    lookup_semantic_result,
    store_semantic_spec,
)
from saltcode.memory.spec_cache import (
    lookup_spec_result,
    resolve_lookup_scope,
    scope_fingerprint_for_tasks,
    store_spec,
)
from saltcode.providers.embeddings import EmbeddingClient
from saltcode.thresholds import (
    DEFAULTS,
    PCD_HIGH_BAR,
    PCD_LOW_BAR,
    SEMANTIC_COSINE,
    THRESHOLD_NAMES,
    load_thresholds,
    warn_if_uncalibrated,
)


class AxisEmbedding(EmbeddingClient):
    """Maps text to one of three orthogonal unit vectors, so cosines are exact.

    Orthogonal axes make similarity either 1.0 or 0.0, which lets the PCD assertions
    below be hand-computed rather than approximated.
    """

    def embed_query(self, text: str) -> list[float]:
        lowered = text.lower()
        if "auth" in lowered or "login" in lowered or "signup" in lowered:
            return [1.0, 0.0, 0.0]
        if "database" in lowered or "sql" in lowered:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]


class WideEmbedding(EmbeddingClient):
    """A different width, to stand in for a changed embedding model."""

    def embed_query(self, text: str) -> list[float]:
        return [0.0] * 8

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]


class BrokenEmbedding(EmbeddingClient):
    """An unreachable endpoint."""

    def embed_query(self, text: str) -> list[float]:
        raise ConnectionRefusedError("no embedding endpoint on this box")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise ConnectionRefusedError("no embedding endpoint on this box")


@pytest.fixture
def embedding() -> AxisEmbedding:
    return AxisEmbedding()


def one_task_file(task_id: str = "T1", files: list[str] | None = None) -> TasksFile:
    return TasksFile(
        schema_version="1.0",
        tasks=[
            Task(
                id=task_id,
                description="a task",
                files_affected=files if files is not None else ["src/auth.py"],
                acceptance_criteria=[],
                depends_on=[],
                complexity="low",
            )
        ],
    )


# --------------------------------------------------------------------------------------
# 5.1 — the store records its embedding dimension instead of guessing
# --------------------------------------------------------------------------------------


def test_init_db_creates_all_four_tables(tmp_path: Path, embedding: AxisEmbedding) -> None:
    db = init_db(tmp_path, embedding)
    for table in (SPEC_CACHE_TABLE, SEMANTIC_CACHE_TABLE, NOTES_TABLE, SKILLS_TABLE):
        assert table in db


def test_creating_the_store_records_the_embedding_dimension(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    init_db(tmp_path, embedding)
    meta = read_store_meta(tmp_path)
    assert meta is not None
    assert meta["dimension"] == 3


class CountingEmbedding(AxisEmbedding):
    def __init__(self) -> None:
        self.calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        return super().embed_query(text)


def test_init_db_does_not_embed_once_the_tables_exist(tmp_path: Path) -> None:
    """A probe on every init_db is a network round-trip per cache lookup."""
    client = CountingEmbedding()
    init_db(tmp_path, client)
    after_creation = client.calls
    assert after_creation >= 1, "creating the vector tables must establish the dimension"

    for _ in range(5):
        init_db(tmp_path, client)
    assert client.calls == after_creation


def test_an_unreachable_endpoint_refuses_to_create_tables(tmp_path: Path) -> None:
    """Guessing 384 builds a store the real model can never write to."""
    with pytest.raises(EmbeddingDimensionUnavailableError):
        probe_vector_dimension(BrokenEmbedding())

    with pytest.raises(EmbeddingDimensionUnavailableError):
        init_db(tmp_path, BrokenEmbedding())


def test_the_exact_tier_still_works_without_an_embedding_endpoint(tmp_path: Path) -> None:
    """Design §11.2 tier 1 is "zero API" — and an outage is when a cached plan is
    worth the most, so the exact tier must not depend on the embedding endpoint.

    Exercised end to end through store/lookup with no embedding client anywhere.
    """
    store_spec(tmp_path, "implement auth", one_task_file())
    result = lookup_spec_result(tmp_path, "implement auth", ["src/auth.py"])
    assert result.hit

    # The vector tables were never created, so nothing tried to embed.
    db: Any = init_db(tmp_path, vectors=False)
    assert SPEC_CACHE_TABLE in db
    assert SEMANTIC_CACHE_TABLE not in db


def test_the_semantic_tier_does_require_an_endpoint(tmp_path: Path) -> None:
    """The contrast to the test above: vectors cannot be faked, so this one refuses."""
    with pytest.raises(EmbeddingDimensionUnavailableError):
        init_db(tmp_path, BrokenEmbedding())


def test_a_changed_embedding_model_is_reported_not_absorbed(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    init_db(tmp_path, embedding)
    with pytest.raises(EmbeddingDimensionMismatchError) as excinfo:
        check_vector_dimension(tmp_path, WideEmbedding().embed_query("anything"))
    assert "3" in str(excinfo.value) and "8" in str(excinfo.value)


def test_a_store_with_no_sidecar_recovers_its_width_from_the_schema(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    """Stores created before the sidecar existed must keep working."""
    init_db(tmp_path, embedding)
    (tmp_path / ".saltcode" / "cache" / "lancedb" / "_meta.json").unlink()

    # No sidecar and no reachable endpoint: the table schema is enough.
    init_db(tmp_path, BrokenEmbedding())


def test_a_corrupt_sidecar_does_not_take_the_store_down(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    init_db(tmp_path, embedding)
    (tmp_path / ".saltcode" / "cache" / "lancedb" / "_meta.json").write_text("{not json", encoding="utf-8")
    assert read_store_meta(tmp_path) is None
    check_vector_dimension(tmp_path, [0.0] * 99)  # unknown width -> nothing to contradict


# --------------------------------------------------------------------------------------
# 5.2 — the exact tier: key, fingerprints, and an explainable miss
# --------------------------------------------------------------------------------------


def test_same_goal_different_scope_produces_different_keys(tmp_path: Path) -> None:
    """REQ-CACHE-002 AC1 — the reason scope is in the key at all."""
    goal = "rate-limit the endpoint"
    login = lookup_spec_result(tmp_path, goal, ["src/login.py"])
    signup = lookup_spec_result(tmp_path, goal, ["src/signup.py"])
    assert login.key != signup.key


def test_the_key_is_the_documented_sha256(tmp_path: Path) -> None:
    result = lookup_spec_result(tmp_path, "  Add Rate Limiting  ", ["b.py", "a.py"])
    assert result.key == compute_spec_hash("add rate limiting", ["a.py", "b.py"])


def test_argument_order_does_not_change_the_key(tmp_path: Path) -> None:
    forwards = lookup_spec_result(tmp_path, "goal", ["a.py", "b.py"]).key
    backwards = lookup_spec_result(tmp_path, "goal", ["b.py", "a.py"]).key
    assert forwards == backwards


def test_a_stored_spec_is_found_under_its_files_affected(tmp_path: Path) -> None:
    tasks = one_task_file(files=["src/auth.py", "src/db.py"])
    key = store_spec(tmp_path, "implement auth", tasks)

    hit = lookup_spec_result(tmp_path, "implement auth", ["src/db.py", "src/auth.py"])
    assert hit.hit
    assert hit.key == key
    assert hit.tasks is not None
    assert hit.tasks.tasks[0].id == "T1"
    assert hit.scope_source == "provided"


def test_the_store_time_fingerprint_is_files_affected(tmp_path: Path) -> None:
    tasks = TasksFile(
        schema_version="1.0",
        tasks=[
            Task(
                id="T1", description="a", files_affected=["b.py", "a.py"],
                acceptance_criteria=[], depends_on=[], complexity="low",
            ),
            Task(
                id="T2", description="b", files_affected=["a.py", "c.py"],
                acceptance_criteria=[], depends_on=[], complexity="low",
            ),
        ],
    )
    assert scope_fingerprint_for_tasks(tasks) == ["a.py", "b.py", "c.py"]


def test_an_explicit_scope_is_used_directly_with_no_probe(tmp_path: Path) -> None:
    """REQ-CACHE-002 AC2 — a probe could only disagree with the caller."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "unrelated.py").write_text("x = 1", encoding="utf-8")

    fingerprint, source = resolve_lookup_scope(tmp_path, ["only/this.py"])
    assert fingerprint == ["only/this.py"]
    assert source == "provided"


def test_an_empty_repo_degrades_the_key_to_goal_only(tmp_path: Path) -> None:
    """REQ-CACHE-002 AC3."""
    fingerprint, source = resolve_lookup_scope(tmp_path, None)
    assert fingerprint == []
    assert source == "empty"

    result = lookup_spec_result(tmp_path, "some goal", None)
    assert result.scope_source == "empty"
    assert result.key == compute_spec_hash("some goal", [])


def test_a_probed_scope_is_labelled_as_probed(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("x = 1", encoding="utf-8")

    result = lookup_spec_result(tmp_path, "goal", None)
    assert result.scope_source == "probed"
    assert result.scope_fingerprint == ["src/auth.py"]


def test_a_miss_explains_itself(tmp_path: Path) -> None:
    """G-004: an unexplained miss is indistinguishable from a cache that is not wired up."""
    result = lookup_spec_result(tmp_path, "never stored", ["src/a.py"])
    assert not result.hit
    assert result.key
    assert result.scope_source == "provided"
    assert "no entry" in result.detail


def test_trade_c5_the_two_fingerprints_differ_by_design(tmp_path: Path) -> None:
    """The store-time and lookup-time fingerprints are different objects (G-004).

    Storing a plan that touches one file, then looking up in a repo of three, must
    miss — and the caller must be able to see why and fix it by passing --scope.
    """
    for name in ("auth.py", "db.py", "web.py"):
        (tmp_path / name).write_text("x = 1", encoding="utf-8")

    store_spec(tmp_path, "implement auth", one_task_file(files=["auth.py"]))

    probed = lookup_spec_result(tmp_path, "implement auth", None)
    assert not probed.hit
    assert probed.scope_fingerprint == ["auth.py", "db.py", "web.py"]

    with_scope = lookup_spec_result(tmp_path, "implement auth", ["auth.py"])
    assert with_scope.hit


def test_a_cached_entry_from_a_future_schema_is_refused(tmp_path: Path) -> None:
    """REQ-GLB-005 — readers reject unknown major versions rather than guess."""
    store_spec(tmp_path, "goal", one_task_file())

    db: Any = init_db(tmp_path, vectors=False)
    table: Any = db.open_table(SPEC_CACHE_TABLE)
    key = compute_spec_hash("goal", ["src/auth.py"])
    table.delete(f"key = '{key}'")
    table.add([{"key": key, "tasks_json": json.dumps({"schema_version": "9.0", "tasks": []})}])

    result = lookup_spec_result(tmp_path, "goal", ["src/auth.py"])
    assert not result.hit
    assert "unsupported major schema_version '9'" in result.detail
    assert "REQ-GLB-005" in result.detail


def test_a_poisoned_cache_row_is_a_reasoned_miss(tmp_path: Path) -> None:
    db: Any = init_db(tmp_path, vectors=False)
    table: Any = db.open_table(SPEC_CACHE_TABLE)
    key = compute_spec_hash("goal", [])
    table.add([{"key": key, "tasks_json": "{not json"}])

    result = lookup_spec_result(tmp_path, "goal", [])
    assert not result.hit
    assert "not readable JSON" in result.detail


def test_storing_the_same_goal_twice_replaces_rather_than_duplicates(tmp_path: Path) -> None:
    store_spec(tmp_path, "goal", one_task_file("T1"))
    store_spec(tmp_path, "goal", one_task_file("T2"))

    result = lookup_spec_result(tmp_path, "goal", ["src/auth.py"])
    assert result.tasks is not None
    assert result.tasks.tasks[0].id == "T2"


# --------------------------------------------------------------------------------------
# 5.3 — the semantic tier and PCD
# --------------------------------------------------------------------------------------


def test_the_semantic_query_joins_normalized_goal_and_sorted_scope() -> None:
    assert get_semantic_query("  Add Auth  ", ["b.py", "a.py"]) == "add auth:a.py,b.py"


def test_pcd_is_the_share_of_the_cache_inside_the_radius(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    """Two of three cached specs sit on the auth axis, so PCD is exactly 2/3."""
    tasks = one_task_file()
    store_semantic_spec(tmp_path, "implement login auth", ["src/auth.py"], tasks, embedding)
    store_semantic_spec(tmp_path, "implement signup auth", ["src/auth.py"], tasks, embedding)
    store_semantic_spec(tmp_path, "optimize sql database queries", ["src/db.py"], tasks, embedding)

    result = lookup_semantic_result(tmp_path, "add login auth", ["src/auth.py"], embedding)
    assert result.cache_size == 3
    assert result.pcd == pytest.approx(2 / 3)
    assert result.similarity == pytest.approx(1.0)
    assert result.hit


def test_an_outlier_goal_misses_the_semantic_tier(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    tasks = one_task_file()
    store_semantic_spec(tmp_path, "implement login auth", ["src/auth.py"], tasks, embedding)

    result = lookup_semantic_result(tmp_path, "reverse a string", ["src/utils.py"], embedding)
    assert not result.hit
    assert result.confirmation == "fall_through"
    assert "below the threshold" in result.detail


def test_an_empty_semantic_cache_reports_itself(tmp_path: Path, embedding: AxisEmbedding) -> None:
    result = lookup_semantic_result(tmp_path, "anything", ["a.py"], embedding)
    assert not result.hit
    assert result.cache_size == 0
    assert result.detail == "the semantic cache is empty"


def test_restoring_the_same_goal_and_scope_does_not_skew_pcd(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    tasks = one_task_file()
    for _ in range(3):
        store_semantic_spec(tmp_path, "implement login auth", ["src/auth.py"], tasks, embedding)

    result = lookup_semantic_result(tmp_path, "implement login auth", ["src/auth.py"], embedding)
    assert result.cache_size == 1


# --------------------------------------------------------------------------------------
# 5.3 / 5.6 — the PCD-adaptive confirmation bar
# --------------------------------------------------------------------------------------


def calibrate(tmp_path: Path, **overrides: Any) -> None:
    """Write a calibration artifact marking the semantic thresholds as measured."""
    values: dict[str, Any] = {name: DEFAULTS[name] for name in THRESHOLD_NAMES}
    values.update(overrides)
    directory = tmp_path / ".saltcode" / "calibration"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "thresholds.json").write_text(json.dumps({"thresholds": values}), encoding="utf-8")


@pytest.mark.parametrize("pcd", [0.0, 0.1, 0.2, 0.5, 0.8, 0.95, 1.0])
def test_uncalibrated_bars_always_require_full_confirmation(tmp_path: Path, pcd: float) -> None:
    """REQ-CACHE-003 AC5 — max skepticism until the bars have been measured.

    An unmeasured bar cannot be used to *lower* the bar; doing so is exactly the
    false-reuse failure the measured-then-fixed protocol exists to prevent.
    """
    thresholds = load_thresholds(tmp_path)
    assert not thresholds.fully_calibrated
    assert confirmation_for_pcd(pcd, thresholds) == "full"


def test_high_pcd_earns_a_cheap_confirmation_once_calibrated(tmp_path: Path) -> None:
    """REQ-CACHE-003 AC2."""
    calibrate(tmp_path)
    thresholds = load_thresholds(tmp_path)
    assert thresholds.fully_calibrated
    assert confirmation_for_pcd(0.9, thresholds) == "cheap"


def test_skipping_confirmation_is_opt_in(tmp_path: Path) -> None:
    """AC2 says confirmation MAY be skipped 'if config allows' — so not by default."""
    calibrate(tmp_path)
    assert confirmation_for_pcd(1.0, load_thresholds(tmp_path)) == "cheap"

    (tmp_path / "saltcode.toml").write_text("[thresholds]\npcd_allow_skip = true\n", encoding="utf-8")
    assert confirmation_for_pcd(1.0, load_thresholds(tmp_path)) == "skip"


def test_low_pcd_requires_full_confirmation_by_default(tmp_path: Path) -> None:
    """REQ-CACHE-003 AC3, resolved to `full` (maintainer decision, 2026-07-29)."""
    calibrate(tmp_path)
    assert confirmation_for_pcd(0.1, load_thresholds(tmp_path)) == "full"


def test_low_pcd_can_be_configured_to_fall_through(tmp_path: Path) -> None:
    calibrate(tmp_path)
    (tmp_path / "saltcode.toml").write_text(
        "[thresholds]\npcd_low_action = \"fall_through\"\n", encoding="utf-8"
    )
    assert confirmation_for_pcd(0.1, load_thresholds(tmp_path)) == "fall_through"


def test_mid_density_requires_full_confirmation(tmp_path: Path) -> None:
    calibrate(tmp_path)
    assert confirmation_for_pcd(0.5, load_thresholds(tmp_path)) == "full"


def test_one_uncalibrated_semantic_bar_is_enough_to_force_full(tmp_path: Path) -> None:
    """The bars are only trustworthy together; a half-calibrated set is not calibrated."""
    calibrate(tmp_path)
    artifact = tmp_path / ".saltcode" / "calibration" / "thresholds.json"
    values = {name: DEFAULTS[name] for name in THRESHOLD_NAMES if name != PCD_HIGH_BAR}
    artifact.write_text(json.dumps({"thresholds": values}), encoding="utf-8")

    thresholds = load_thresholds(tmp_path)
    assert thresholds.pcd_high_density_bar.calibrated is False
    assert confirmation_for_pcd(0.99, thresholds) == "full"


# --------------------------------------------------------------------------------------
# 5.6 — per-threshold calibration flags
# --------------------------------------------------------------------------------------


def test_defaults_are_conservative_and_marked_uncalibrated(tmp_path: Path) -> None:
    """REQ-AUD-005 AC1 and REQ-CACHE-003 AC5."""
    thresholds = load_thresholds(tmp_path)
    assert thresholds.auditor_stability_threshold.value == 0.5
    assert thresholds.semantic_cosine_threshold.value == 0.85
    for threshold in thresholds.all_thresholds():
        assert threshold.calibrated is False
        assert threshold.source == "default"


def test_each_threshold_carries_its_own_flag(tmp_path: Path) -> None:
    """REQ-CAL-001 AC1 — calibration arrives per threshold, not project-wide."""
    calibrate(tmp_path)
    artifact = tmp_path / ".saltcode" / "calibration" / "thresholds.json"
    artifact.write_text(json.dumps({"thresholds": {SEMANTIC_COSINE: 0.91}}), encoding="utf-8")

    thresholds = load_thresholds(tmp_path)
    assert thresholds.semantic_cosine_threshold.calibrated is True
    assert thresholds.semantic_cosine_threshold.value == 0.91
    assert thresholds.auditor_stability_threshold.calibrated is False
    assert thresholds.fully_calibrated is False


def test_a_hand_set_value_is_not_a_measurement(tmp_path: Path) -> None:
    """Writing a number in config is not a claim to have calibrated it."""
    (tmp_path / "saltcode.toml").write_text(
        "[thresholds]\nsemantic_cosine_threshold = 0.7\n", encoding="utf-8"
    )
    thresholds = load_thresholds(tmp_path)
    assert thresholds.semantic_cosine_threshold.value == 0.7
    assert thresholds.semantic_cosine_threshold.calibrated is False
    assert thresholds.semantic_cosine_threshold.source == "project_config"


def test_an_explicit_flag_promotes_a_configured_value(tmp_path: Path) -> None:
    (tmp_path / "saltcode.toml").write_text(
        "[thresholds]\nsemantic_cosine_threshold = 0.7\nsemantic_cosine_threshold_calibrated = true\n",
        encoding="utf-8",
    )
    assert load_thresholds(tmp_path).semantic_cosine_threshold.calibrated is True


def test_calibration_artifacts_outrank_project_config(tmp_path: Path) -> None:
    (tmp_path / "saltcode.toml").write_text(
        "[thresholds]\nsemantic_cosine_threshold = 0.7\n", encoding="utf-8"
    )
    calibrate(tmp_path, semantic_cosine_threshold=0.93)

    thresholds = load_thresholds(tmp_path)
    assert thresholds.semantic_cosine_threshold.value == 0.93
    assert thresholds.semantic_cosine_threshold.source == "calibration"


def test_the_uncalibrated_warning_names_which_thresholds(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """REQ-CAL-001 AC2 — 'some thresholds are uncalibrated' is not actionable."""
    import logging

    with caplog.at_level(logging.WARNING):
        message = warn_if_uncalibrated(load_thresholds(tmp_path))

    assert message is not None
    for name in THRESHOLD_NAMES:
        assert name in message
    assert any("UNCALIBRATED" in record.message for record in caplog.records)


def test_a_fully_calibrated_set_does_not_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    calibrate(tmp_path)
    with caplog.at_level(logging.WARNING):
        assert warn_if_uncalibrated(load_thresholds(tmp_path)) is None
    assert not caplog.records


def test_an_unreadable_project_config_falls_back_to_defaults(tmp_path: Path) -> None:
    (tmp_path / "saltcode.toml").write_text("[thresholds\nbroken", encoding="utf-8")
    thresholds = load_thresholds(tmp_path)
    assert thresholds.semantic_cosine_threshold.value == DEFAULTS[SEMANTIC_COSINE]
    assert thresholds.semantic_cosine_threshold.source == "default"


def test_a_non_numeric_threshold_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "saltcode.toml").write_text(
        "[thresholds]\npcd_low_density_bar = \"soon\"\n", encoding="utf-8"
    )
    thresholds = load_thresholds(tmp_path)
    assert thresholds.pcd_low_density_bar.value == DEFAULTS[PCD_LOW_BAR]
    assert thresholds.pcd_low_density_bar.source == "default"


def test_the_summary_is_json_safe(tmp_path: Path) -> None:
    summary = load_thresholds(tmp_path).summary()
    json.dumps(summary)
    assert summary["fully_calibrated"] is False
    assert set(summary["uncalibrated"]) == set(THRESHOLD_NAMES)


def test_write_store_meta_round_trips(tmp_path: Path) -> None:
    write_store_meta(tmp_path, 384, "bge-small")
    meta = read_store_meta(tmp_path)
    assert meta is not None
    assert meta["dimension"] == 384
    assert meta["embedding_model"] == "bge-small"


# ============================================================================
# Review follow-ups (CodeRabbit, 2026-07-29)
# ============================================================================


def test_semantic_deletes_never_interpolate_goal_text(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    """A goal full of quotes must not corrupt the delete predicate.

    `spec_cache` had always keyed on a hex digest for exactly this reason; the
    semantic tier was interpolating raw goal text with hand-rolled quote doubling.
    """
    from saltcode.memory.semantic_cache import semantic_row_key

    nasty = "auth' OR '1'='1 -- \\ \" ;drop"
    tasks = one_task_file()

    store_semantic_spec(tmp_path, nasty, ["src/auth.py"], tasks, embedding)
    store_semantic_spec(tmp_path, nasty, ["src/auth.py"], tasks, embedding)

    result = lookup_semantic_result(tmp_path, nasty, ["src/auth.py"], embedding)
    assert result.cache_size == 1, "re-storing must replace, not stack duplicates"

    key = semantic_row_key(nasty, ["src/auth.py"])
    assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)


def test_the_row_key_is_stable_and_scope_order_independent() -> None:
    from saltcode.memory.semantic_cache import semantic_row_key

    assert semantic_row_key("Goal", ["b.py", "a.py"]) == semantic_row_key("  goal  ", ["a.py", "b.py"])
    assert semantic_row_key("goal a", ["x.py"]) != semantic_row_key("goal b", ["x.py"])


def test_a_store_from_an_older_layout_is_reported_not_silently_used(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    """A schema bump must be visible, and must not delete the operator's data."""
    from saltcode.memory.lancedb_store import StoreSchemaVersionMismatchError

    init_db(tmp_path, embedding)
    meta_path = tmp_path / ".saltcode" / "cache" / "lancedb" / "_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["schema_version"] = "1"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(StoreSchemaVersionMismatchError) as excinfo:
        init_db(tmp_path, embedding)
    assert "delete" in str(excinfo.value).lower()
    assert meta_path.exists(), "the store must not be removed on our own initiative"


def test_creating_a_table_twice_is_not_an_error(tmp_path: Path, embedding: AxisEmbedding) -> None:
    """`name not in db` then `create_table` is check-then-act; entrypoints are processes."""
    import lancedb

    from saltcode.memory.lancedb_store import _spec_cache_schema, get_db_path

    init_db(tmp_path, embedding)
    db: Any = lancedb.connect(str(get_db_path(tmp_path)))
    db.create_table(SPEC_CACHE_TABLE, schema=_spec_cache_schema(), exist_ok=True)


def test_the_expected_major_survives_a_field_without_a_default() -> None:
    """`model_fields[...].default` is PydanticUndefined — truthy — when absent.

    Reading it naively yields the literal string "PydanticUndefined", which would
    reject every cached row as unsupported.
    """
    from pydantic import BaseModel

    from saltcode.contracts.io import expected_major_version, major_of

    class NoDefault(BaseModel):
        schema_version: str

    assert expected_major_version(NoDefault) == "1"
    assert major_of("2.7") == "2"
    assert major_of(None) == "1"


# ============================================================================
# Review follow-ups (CodeRabbit round 3, 2026-07-30)
# ============================================================================


def stamp_store_version(workspace: Path, version: str) -> None:
    """Rewrite the sidecar's `schema_version`, simulating a store built by another build."""
    meta_path = workspace / ".saltcode" / "cache" / "lancedb" / "_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["schema_version"] = version
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


def test_an_older_layout_still_opens_for_the_zero_api_exact_tier(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    """The store version is one number, but the tiers do not fail together.

    The 2 bump added a `key` column to `semantic_cache` and touched nothing else, so
    refusing to open a v1 store for an exact lookup takes down the tier design §11.2
    calls "zero API" — the tier whose entire value is being available when the
    embedding side is not.
    """
    init_db(tmp_path, embedding)  # a full store, so the sidecar exists to be aged
    store_spec(tmp_path, "add auth", one_task_file())
    stamp_store_version(tmp_path, "1")

    result = lookup_spec_result(tmp_path, "add auth", scope=["src/auth.py"])
    assert result.hit, result.detail

    # The vector tiers are still refused: their layout is the one that changed.
    with pytest.raises(StoreSchemaVersionMismatchError):
        init_db(tmp_path, embedding)


def test_an_unknown_future_layout_is_refused_by_both_tiers(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    """The relaxation is a named compatibility set, not "skip the check when offline"."""
    init_db(tmp_path, embedding)
    store_spec(tmp_path, "add auth", one_task_file())
    stamp_store_version(tmp_path, "99")

    with pytest.raises(StoreSchemaVersionMismatchError):
        init_db(tmp_path, vectors=False)


def test_the_current_version_is_in_the_compatible_set() -> None:
    """A bump that forgets to update the set would silently refuse its own store."""
    assert STORE_SCHEMA_VERSION in SPEC_CACHE_COMPATIBLE_VERSIONS


def test_restoring_a_spec_never_leaves_the_row_missing(
    tmp_path: Path, embedding: AxisEmbedding
) -> None:
    """Replacement is one commit, not delete-then-add.

    The entrypoints are separate processes (`pi.exec`), so a concurrent `cache_lookup`
    landing between a delete and its add reads a miss and fires a full Phase 1 for a
    goal that is in fact cached — and a crash in that window loses the row for good.
    """
    tasks = one_task_file()
    store_semantic_spec(tmp_path, "add auth", ["src/auth.py"], tasks, embedding)

    db: Any = init_db(tmp_path, embedding)
    table: Any = db.open_table(SEMANTIC_CACHE_TABLE)
    versions_before = int(table.count_rows())

    replacement = one_task_file("T2")
    store_semantic_spec(tmp_path, "add auth", ["src/auth.py"], replacement, embedding)

    result = lookup_semantic_result(tmp_path, "add auth", ["src/auth.py"], embedding)
    assert result.cache_size == versions_before == 1
    assert result.tasks is not None
    assert [t.id for t in result.tasks.tasks] == ["T2"], "the upsert must update in place"
