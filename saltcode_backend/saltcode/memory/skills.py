import json
from pathlib import Path
from typing import Any, cast

from saltcode.memory.lancedb_store import init_db
from saltcode.providers.embeddings import EmbeddingClient, LocalEmbeddingClient


def add_skill(
    workspace_path: Path | str,
    name: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    embedding_client: EmbeddingClient | None = None
) -> None:
    """Adds or updates a skill in the LanceDB skills table."""
    client: EmbeddingClient = embedding_client if embedding_client is not None else LocalEmbeddingClient()
        
    desc = (metadata or {}).get("description", "")
    embed_text = f"{name} {desc}".strip()
    if not embed_text:
        embed_text = name
        
    vector = client.embed_query(embed_text)
    db: Any = init_db(workspace_path, client)
    table: Any = db.open_table("skills")
    
    escaped_name = name.replace("'", "''")
    table.delete(f"name = '{escaped_name}'")
    
    meta_str = json.dumps(metadata or {})
    table.add([{
        "vector": vector,
        "name": name,
        "content": content,
        "metadata": meta_str
    }])

def search_skills(
    workspace_path: Path | str,
    query: str,
    limit: int = 3,
    embedding_client: EmbeddingClient | None = None
) -> list[dict[str, Any]]:
    """Retrieves relevant skills via semantic search on the query."""
    client: EmbeddingClient = embedding_client if embedding_client is not None else LocalEmbeddingClient()
        
    db: Any = init_db(workspace_path, client)
    table: Any = db.open_table("skills")
    
    if table.count_rows() == 0:
        return []
        
    query_vector = client.embed_query(query)
    results: list[dict[str, Any]] = table.search(query_vector).limit(limit).to_list()
    
    skills: list[dict[str, Any]] = []
    for r in results:
        meta: dict[str, Any] = {}
        if r.get("metadata"):
            try:
                parsed = json.loads(str(r["metadata"]))
                if isinstance(parsed, dict):
                    meta = cast(dict[str, Any], parsed)
            except Exception:
                pass
        skills.append({
            "name": str(r["name"]),
            "content": str(r["content"]),
            "metadata": meta,
            "score": 1.0 - float(r.get("_distance", 0.0)) # Cosine similarity
        })
        
    return skills
