import json
from pathlib import Path
from typing import Any

from saltcode.contracts.tasks import TasksFile
from saltcode.harness.phase_gate import compute_spec_hash
from saltcode.harness.scope_probe import run_scope_probe
from saltcode.memory.lancedb_store import init_db


def store_spec(workspace_path: Path | str, goal: str, tasks_file: TasksFile) -> None:
    """Stores a TasksFile spec in the exact spec cache."""
    # Compute scope fingerprint as sorted unique files_affected across all tasks
    files: set[str] = set()
    for task in tasks_file.tasks:
        for f in task.files_affected:
            files.add(f)
    scope_fingerprint = sorted(files)
    
    key = compute_spec_hash(goal, scope_fingerprint)
    
    db: Any = init_db(workspace_path)
    table: Any = db.open_table("spec_cache")
    
    # Overwrite if exists
    table.delete(f"key = '{key}'")
    
    tasks_json = tasks_file.model_dump_json()
    table.add([{"key": key, "tasks_json": tasks_json}])

def lookup_spec(
    workspace_path: Path | str, 
    goal: str, 
    scope: list[str] | None = None
) -> TasksFile | None:
    """Looks up a spec in the exact cache. Falls back to the scope probe if scope is None.

    REQ-CACHE-002 AC2: an explicit `scope` is used directly, with no probe. The
    probe takes the workspace only — the goal is hashed into the key separately
    (see `harness/scope_probe.py`), so feeding it in as well would make two
    phrasings of one goal key differently for an identical tree.
    """
    resolved_scope: list[str] = run_scope_probe(workspace_path) if scope is None else scope


    key = compute_spec_hash(goal, resolved_scope)
    
    db: Any = init_db(workspace_path)
    table: Any = db.open_table("spec_cache")
    
    res: list[dict[str, Any]] = table.search().where(f"key = '{key}'").limit(1).to_list()
    if not res:
        return None
        
    try:
        tasks_data = json.loads(str(res[0]["tasks_json"]))
        if isinstance(tasks_data, dict):
            return TasksFile.model_validate(tasks_data)
        return None
    except Exception:
        return None
