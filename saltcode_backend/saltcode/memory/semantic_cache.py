import json
from pathlib import Path
from typing import Any

from saltcode.config import settings
from saltcode.contracts.tasks import TasksFile
from saltcode.harness.scope_probe import run_scope_probe
from saltcode.memory.lancedb_store import init_db
from saltcode.providers.embeddings import EmbeddingClient, LocalEmbeddingClient


def get_semantic_query(goal: str, scope: list[str]) -> str:
    """Constructs the combined query text for embedding generation."""
    normalized_goal = goal.strip().lower()
    scope_str = ",".join(sorted(scope))
    return f"{normalized_goal}:{scope_str}"

def store_semantic_spec(
    workspace_path: Path | str,
    goal: str,
    scope: list[str],
    tasks_file: TasksFile,
    embedding_client: EmbeddingClient | None = None
) -> None:
    """Stores a spec in the semantic cache with its vector representation."""
    client: EmbeddingClient = embedding_client if embedding_client is not None else LocalEmbeddingClient()
        
    query_text = get_semantic_query(goal, scope)
    vector = client.embed_query(query_text)
    
    db: Any = init_db(workspace_path, client)
    table: Any = db.open_table("semantic_cache")
    
    scope_fingerprint = ",".join(sorted(scope))
    escaped_goal = goal.replace("'", "''")
    escaped_scope = scope_fingerprint.replace("'", "''")
    
    # Remove existing exact match if duplicate to update it
    table.delete(f"goal = '{escaped_goal}' AND scope_fingerprint = '{escaped_scope}'")
    
    table.add([{
        "vector": vector,
        "goal": goal,
        "scope_fingerprint": scope_fingerprint,
        "tasks_json": tasks_file.model_dump_json()
    }])

def lookup_semantic(
    workspace_path: Path | str,
    goal: str,
    scope: list[str] | None = None,
    embedding_client: EmbeddingClient | None = None
) -> tuple[TasksFile | None, float]:
    """Fuzzy searches for a cached spec using cosine similarity.
    
    Returns a tuple of (TasksFile, pcd_score) or (None, pcd_score).
    """
    resolved_scope = run_scope_probe(goal, workspace_path) if scope is None else scope
        
    client: EmbeddingClient = embedding_client if embedding_client is not None else LocalEmbeddingClient()
        
    db: Any = init_db(workspace_path, client)
    table: Any = db.open_table("semantic_cache")
    
    total_specs: int = int(table.count_rows())
    if total_specs == 0:
        return None, 0.0
        
    query_text = get_semantic_query(goal, resolved_scope)
    vector = client.embed_query(query_text)
    
    threshold: float = float(settings.semantic_cosine_threshold)
    
    # Retrieve all candidates to perform PCD calculation
    search_builder: Any = table.search(vector).metric("cosine").limit(total_specs)
    results: list[dict[str, Any]] = search_builder.to_list()
    if not results:
        return None, 0.0
        
    # PCD is the fraction of all specs within the cosine distance limit (1.0 - threshold)
    radius = 1.0 - threshold
    nearby_count = sum(1 for r in results if float(r.get("_distance", 1.0)) <= radius)
    pcd = nearby_count / total_specs
    
    # Find best match
    best_candidate = results[0]
    best_distance = float(best_candidate.get("_distance", 1.0))
    best_similarity = 1.0 - best_distance
    
    if best_similarity >= threshold:
        try:
            tasks_data = json.loads(str(best_candidate["tasks_json"]))
            if isinstance(tasks_data, dict):
                tasks_file = TasksFile.model_validate(tasks_data)
                return tasks_file, pcd
            return None, pcd
        except Exception:
            return None, pcd
            
    return None, pcd
