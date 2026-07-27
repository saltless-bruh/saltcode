import json
from pathlib import Path
from typing import Any

from saltcode.memory.lancedb_store import init_db
from saltcode.providers.embeddings import EmbeddingClient, LocalEmbeddingClient

# Session frozen state
_frozen_notes: list[str] | None = None
_session_goal: str | None = None

def clear_session_notes() -> None:
    """Resets the frozen session state. Typically used during tests or session boundary."""
    global _frozen_notes, _session_goal
    _frozen_notes = None
    _session_goal = None

def get_frozen_notes() -> list[str] | None:
    """Retrieves the frozen notes if a session is currently initialized."""
    return _frozen_notes

def add_note(
    workspace_path: Path | str,
    content: str,
    metadata: dict[str, Any] | None = None,
    embedding_client: EmbeddingClient | None = None
) -> None:
    """Adds a note into the LanceDB notes table."""
    client: EmbeddingClient = embedding_client if embedding_client is not None else LocalEmbeddingClient()
        
    vector = client.embed_query(content)
    db: Any = init_db(workspace_path, client)
    table: Any = db.open_table("notes")
    
    meta_str = json.dumps(metadata or {})
    table.add([{
        "vector": vector,
        "content": content,
        "metadata": meta_str
    }])

def initialize_session_notes(
    workspace_path: Path | str,
    goal: str,
    embedding_client: EmbeddingClient | None = None
) -> list[str]:
    """Retrieves at most 5 atomic notes at session open and freezes them.
    
    Subsequent calls return the exact same frozen list.
    """
    global _frozen_notes, _session_goal
    
    if _frozen_notes is not None:
        return _frozen_notes
        
    client: EmbeddingClient = embedding_client if embedding_client is not None else LocalEmbeddingClient()
        
    db: Any = init_db(workspace_path, client)
    table: Any = db.open_table("notes")
    
    # If empty notes table
    if table.count_rows() == 0:
        _frozen_notes = []
        _session_goal = goal
        return _frozen_notes
        
    # Perform semantic query on the session goal
    query_vector = client.embed_query(goal)
    
    # Retrieve top 5 closest notes
    results: list[dict[str, Any]] = table.search(query_vector).limit(5).to_list()
    
    # Extract only the note contents
    notes = [str(r["content"]) for r in results]
    
    _frozen_notes = notes
    _session_goal = goal
    return _frozen_notes
