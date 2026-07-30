"""The fuzzy tier of the cache ladder, with PCD-adaptive confirmation (task 5.3).

Tier 2 of design §11.2: `embed(goal + scope)`, cosine ≥ threshold → *candidate*. A
candidate is never reuse. REQ-CACHE-003 AC1 is explicit — reuse requires an Architect
confirmation, and this module cannot run one (agents belong to the extension, Task 8.1).
So the backend's job is to return the candidate **and the bar the confirmation must
clear**, and let the extension spend the tokens.

**PCD (Prior Cluster Density)**, adapted from BIFAI-NET v5.2: the share of the cache
lying within a cosine radius of the query. A dense neighbourhood means the goal is in
well-trodden territory and the match is probably genuine — confirmation can be cheap. A
sparse one means the goal is an outlier and the single cosine hit is the kind of
coincidence the scope fingerprint exists to catch — be skeptical.

**On the distance identity.** LanceDB's ``metric("cosine")`` returns
``_distance = 1 - cosine_similarity``, so ``_distance <= 1 - threshold`` is exactly
``similarity >= threshold``. The PCD radius is therefore the cosine threshold itself,
which is what design §11.9 asks for ("compute PCD at that threshold"). Written out here
because it looks like an off-by-one and is not.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from saltcode.contracts.tasks import TasksFile
from saltcode.harness.scope_probe import run_scope_probe
from saltcode.memory.lancedb_store import (
    SEMANTIC_CACHE_TABLE,
    check_vector_dimension,
    init_db,
)
from saltcode.providers.embeddings import EmbeddingClient, LocalEmbeddingClient
from saltcode.thresholds import Thresholds, load_thresholds

Confirmation = Literal["skip", "cheap", "full", "fall_through"]
"""What the extension must do before reusing a semantic candidate.

``skip``          reuse without asking the Architect (REQ-CACHE-003 AC2, opt-in only)
``cheap``         a short, low-cost Architect confirmation
``full``          a full Architect confirmation
``fall_through``  do not confirm at all; fire Phase 1 (REQ-CACHE-003 AC3, opt-in)
"""


def get_semantic_query(goal: str, scope: list[str]) -> str:
    """The text that gets embedded: the normalized goal joined to its sorted scope."""
    normalized_goal = goal.strip().lower()
    scope_str = ",".join(sorted(scope))
    return f"{normalized_goal}:{scope_str}"


def semantic_row_key(goal: str, scope: list[str]) -> str:
    """The row's identity: a sha256 of the same text that gets embedded.

    Deletes filter on this rather than on the goal and scope columns. A LanceDB
    delete takes a SQL predicate string, and goal text is free-form: the previous
    version interpolated it with hand-rolled `\'` doubling, which is a quoting
    scheme rather than a guarantee. A hex digest has no character that can end a
    string literal, which is exactly the reasoning `spec_cache` already applied to
    its own key — the two tiers now agree.
    """
    return hashlib.sha256(get_semantic_query(goal, scope).encode("utf-8")).hexdigest()


def confirmation_for_pcd(pcd: float, thresholds: Thresholds) -> Confirmation:
    """Map measured density to the Architect-confirmation bar (REQ-CACHE-003).

    The uncalibrated case comes first and overrides everything: AC5 requires maximum
    skepticism until the bars have been measured, and bars that have not been measured
    cannot be used to *lower* the bar. Skipping confirmation on the strength of an
    unmeasured density bar is precisely the false-reuse failure the whole protocol
    exists to prevent.
    """
    semantic_calibrated = (
        thresholds.semantic_cosine_threshold.calibrated
        and thresholds.pcd_low_density_bar.calibrated
        and thresholds.pcd_high_density_bar.calibrated
    )
    if not semantic_calibrated:
        return "full"

    if pcd >= thresholds.pcd_high_density_bar.value:
        # AC2: "MAY be cheap/skipped (if config allows)" — so skipping is opt-in.
        return "skip" if thresholds.pcd_allow_skip else "cheap"

    if pcd <= thresholds.pcd_low_density_bar.value:
        # AC3 admits both readings; `full` is the configured default (see Thresholds).
        return thresholds.pcd_low_action

    return "full"


def store_semantic_spec(
    workspace_path: Path | str,
    goal: str,
    scope: list[str],
    tasks_file: TasksFile,
    embedding_client: EmbeddingClient | None = None,
) -> None:
    """Store a spec in the semantic cache alongside its vector."""
    client: EmbeddingClient = embedding_client if embedding_client is not None else LocalEmbeddingClient()

    query_text = get_semantic_query(goal, scope)
    vector = client.embed_query(query_text)

    db: Any = init_db(workspace_path, client)
    check_vector_dimension(workspace_path, vector)
    table: Any = db.open_table(SEMANTIC_CACHE_TABLE)

    scope_fingerprint = ",".join(sorted(scope))
    key = semantic_row_key(goal, scope)

    # Replace rather than accumulate: re-planning the same goal over the same scope
    # must not stack duplicate rows, which would also skew the PCD denominator. The
    # predicate carries only a hex digest, never the goal text.
    table.delete(f"key = '{key}'")

    table.add(
        [
            {
                "key": key,
                "vector": vector,
                "goal": goal,
                "scope_fingerprint": scope_fingerprint,
                "tasks_json": tasks_file.model_dump_json(),
            }
        ]
    )


class SemanticResult:
    """The outcome of a semantic lookup, with everything needed to explain it."""

    def __init__(
        self,
        *,
        tasks: TasksFile | None,
        similarity: float,
        pcd: float,
        confirmation: Confirmation,
        cache_size: int,
        threshold: float,
        detail: str,
    ) -> None:
        self.tasks = tasks
        self.similarity = similarity
        self.pcd = pcd
        self.confirmation = confirmation
        self.cache_size = cache_size
        self.threshold = threshold
        self.detail = detail

    @property
    def hit(self) -> bool:
        """A candidate was found. **Not** an authorization to reuse it."""
        return self.tasks is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "hit": self.hit,
            "similarity": round(self.similarity, 6),
            "pcd": round(self.pcd, 6),
            "confirmation": self.confirmation,
            "cache_size": self.cache_size,
            "threshold": self.threshold,
            "detail": self.detail,
        }


def lookup_semantic_result(
    workspace_path: Path | str,
    goal: str,
    scope: list[str] | None = None,
    embedding_client: EmbeddingClient | None = None,
    thresholds: Thresholds | None = None,
) -> SemanticResult:
    """Search the semantic cache and compute the confirmation bar for the result.

    The scope probe takes the workspace only; the goal is combined with the scope in
    :func:`get_semantic_query` rather than filtering it (REQ-CACHE-002, G-C03).
    """
    resolved_scope: list[str] = run_scope_probe(workspace_path) if scope is None else scope
    bars = thresholds if thresholds is not None else load_thresholds(workspace_path)
    threshold = bars.semantic_cosine_threshold.value

    client: EmbeddingClient = embedding_client if embedding_client is not None else LocalEmbeddingClient()

    db: Any = init_db(workspace_path, client)
    table: Any = db.open_table(SEMANTIC_CACHE_TABLE)

    total_specs: int = int(table.count_rows())
    if total_specs == 0:
        return SemanticResult(
            tasks=None,
            similarity=0.0,
            pcd=0.0,
            confirmation="fall_through",
            cache_size=0,
            threshold=threshold,
            detail="the semantic cache is empty",
        )

    query_text = get_semantic_query(goal, resolved_scope)
    vector = client.embed_query(query_text)
    check_vector_dimension(workspace_path, vector)

    # The whole cache is scanned because PCD's denominator is the whole cache; a
    # top-k search cannot answer "what fraction lies within the radius". Fine at the
    # cache sizes this is built for, and a known cost if a cache ever grows large.
    results: list[dict[str, Any]] = table.search(vector).metric("cosine").limit(total_specs).to_list()
    if not results:
        return SemanticResult(
            tasks=None,
            similarity=0.0,
            pcd=0.0,
            confirmation="fall_through",
            cache_size=total_specs,
            threshold=threshold,
            detail="no rows returned by the vector search",
        )

    radius = 1.0 - threshold
    nearby_count = sum(1 for r in results if float(r.get("_distance", 1.0)) <= radius)
    pcd = nearby_count / total_specs

    best_candidate = results[0]
    best_similarity = 1.0 - float(best_candidate.get("_distance", 1.0))

    if best_similarity < threshold:
        return SemanticResult(
            tasks=None,
            similarity=best_similarity,
            pcd=pcd,
            confirmation="fall_through",
            cache_size=total_specs,
            threshold=threshold,
            detail=f"best similarity {best_similarity:.4f} is below the threshold {threshold}",
        )

    confirmation = confirmation_for_pcd(pcd, bars)

    try:
        tasks_data: Any = json.loads(str(best_candidate["tasks_json"]))
        tasks = TasksFile.model_validate(tasks_data) if isinstance(tasks_data, dict) else None
    except Exception as exc:  # noqa: BLE001 - a poisoned row is a miss, not a crash
        return SemanticResult(
            tasks=None,
            similarity=best_similarity,
            pcd=pcd,
            confirmation="fall_through",
            cache_size=total_specs,
            threshold=threshold,
            detail=f"cached candidate failed to validate ({type(exc).__name__}: {exc})",
        )

    if tasks is None:
        return SemanticResult(
            tasks=None,
            similarity=best_similarity,
            pcd=pcd,
            confirmation="fall_through",
            cache_size=total_specs,
            threshold=threshold,
            detail="cached candidate was not a JSON object",
        )

    return SemanticResult(
        tasks=tasks,
        similarity=best_similarity,
        pcd=pcd,
        confirmation=confirmation,
        cache_size=total_specs,
        threshold=threshold,
        detail=(
            f"candidate at similarity {best_similarity:.4f} (PCD {pcd:.4f}); "
            f"Architect confirmation required: {confirmation}"
        ),
    )


def lookup_semantic(
    workspace_path: Path | str,
    goal: str,
    scope: list[str] | None = None,
    embedding_client: EmbeddingClient | None = None,
) -> tuple[TasksFile | None, float]:
    """Backwards-compatible view of :func:`lookup_semantic_result`: ``(tasks, pcd)``."""
    result = lookup_semantic_result(workspace_path, goal, scope, embedding_client)
    return result.tasks, result.pcd
