from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa

from saltcode.providers.embeddings import EmbeddingClient, LocalEmbeddingClient


def get_db_path(workspace_path: Path | str) -> Path:
    """Returns the Path to the LanceDB cache directory."""
    return Path(workspace_path) / ".saltcode" / "cache" / "lancedb"

def get_vector_dimension(embedding_client: EmbeddingClient | None = None) -> int:
    """Detects the vector dimension from the embedding client, falling back to 384."""
    client: EmbeddingClient
    if embedding_client is None:
        try:
            client = LocalEmbeddingClient()
        except Exception:
            return 384
    else:
        client = embedding_client
    try:
        vec = client.embed_query("test")
        return len(vec)
    except Exception:
        return 384

def init_db(workspace_path: Path | str, embedding_client: EmbeddingClient | None = None) -> Any:
    """Initializes the database and ensures all required tables exist."""
    db_path = get_db_path(workspace_path)
    db_path.mkdir(parents=True, exist_ok=True)
    db: Any = lancedb.connect(str(db_path))
    
    # 1. Spec Cache Table (Key-Value)
    if "spec_cache" not in db:
        spec_schema = pa.schema([
            pa.field("key", pa.string()),
            pa.field("tasks_json", pa.string())
        ])
        db.create_table("spec_cache", schema=spec_schema)
        
    # Get embedding dimension
    dim = get_vector_dimension(embedding_client)
    
    # 2. Semantic Cache Table
    if "semantic_cache" not in db:
        semantic_schema = pa.schema([
            pa.field("vector", pa.list_(pa.float32(), list_size=dim)),
            pa.field("goal", pa.string()),
            pa.field("scope_fingerprint", pa.string()),
            pa.field("tasks_json", pa.string())
        ])
        db.create_table("semantic_cache", schema=semantic_schema)
        
    # 3. Notes Table
    if "notes" not in db:
        notes_schema = pa.schema([
            pa.field("vector", pa.list_(pa.float32(), list_size=dim)),
            pa.field("content", pa.string()),
            pa.field("metadata", pa.string())
        ])
        db.create_table("notes", schema=notes_schema)
        
    # 4. Skills Table
    if "skills" not in db:
        skills_schema = pa.schema([
            pa.field("vector", pa.list_(pa.float32(), list_size=dim)),
            pa.field("name", pa.string()),
            pa.field("content", pa.string()),
            pa.field("metadata", pa.string())
        ])
        db.create_table("skills", schema=skills_schema)
        
    return db
