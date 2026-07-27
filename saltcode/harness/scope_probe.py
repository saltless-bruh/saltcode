import re
from pathlib import Path


def run_scope_probe(goal: str, workspace_path: Path | str) -> list[str]:
    """Lightweight scope probe that extracts a sorted list of file paths mentioned in the goal.

    Walks the workspace directory, identifies files whose name or relative path is mentioned
    in the goal (case-insensitive), and returns them as a sorted list.
    """
    workspace = Path(workspace_path).resolve()
    if not workspace.exists():
        return []

    # Clean and tokenize goal
    # Convert goal to lowercase and find all word/path-like tokens (allowing slash/dot)
    tokens = set(re.findall(r"[a-zA-Z0-9_\-\./]+", goal.lower()))

    matched_paths: set[str] = set()

    # Walk directory to find files
    for path in workspace.rglob("*"):
        if path.is_file():
            # Skip VCS, cache, and build directories
            parts = path.relative_to(workspace).parts
            ignored_dirs = (".git", ".saltcode", "__pycache__", "node_modules", "build", "dist", ".pytest_cache")
            if any(p in ignored_dirs for p in parts):
                continue

                
            rel_path = path.relative_to(workspace).as_posix()
            rel_path_lower = rel_path.lower()
            filename_lower = path.name.lower()

            # Check if filename is mentioned directly (e.g. "auth.py" or "dag.py")
            if filename_lower in tokens:
                matched_paths.add(rel_path)
                continue
                
            # Check if relative path is mentioned (e.g. "saltcode/harness/dag.py")
            if rel_path_lower in tokens:
                matched_paths.add(rel_path)
                continue

            # Check if any parent folder is mentioned (e.g. if goal mentions "harness", match files in harness/)
            for part in parts[:-1]:
                if part.lower() in tokens:
                    matched_paths.add(rel_path)
                    break

    return sorted(matched_paths)
