from typing import Any

from saltcode.contracts.tasks import Task


def compact_builder_context(
    task: Task | dict[str, Any],
    files_affected_content: dict[str, str],
    ast_slices: dict[str, Any],
    spec_content: str
) -> dict[str, Any]:
    """Assembles a compacted Builder context.

    Ensures only the single task, its spec, its affected files, and its AST slices are included.
    Raises ValueError if there is extraneous content (e.g. files not in files_affected).
    """
    # Extract affected files list
    if isinstance(task, Task):
        task_data = task.model_dump()
        files_affected = task.files_affected
    else:
        task_data = task
        files_affected = task.get("files_affected", [])

    allowed_files = set(files_affected)
    
    # Enforce constraint: files_affected_content must only contain files in task.files_affected
    for filepath in files_affected_content:
        if filepath not in allowed_files:
            raise ValueError(
                f"Extraneous file '{filepath}' not allowed in Builder context. "
                f"Allowed files: {allowed_files}"
            )

    return {
        "task": task_data,
        "files_affected": files_affected_content,
        "ast_slices": ast_slices,
        "spec": spec_content
    }
