import re
from pathlib import Path


def validate_agent_write_path(agent_name: str, filepath: Path | str, workspace_path: Path | str) -> bool:
    """Enforces write-path permissions per agent role relative to the workspace.

    Scout: .saltcode/context_report.json
    Architect: .saltcode/design.md
    Planner: .saltcode/tasks.json
    Test Intent: .saltcode/tests/task_*_spec.*
    Evaluator: .saltcode/evaluator_report.json
    Auditor: .saltcode/audit_result.json
    Spec Compactor: .saltcode/design.md
    Builder: any file outside .saltcode/ and not in tests/**
    """
    try:
        abs_workspace = Path(workspace_path).resolve()
        abs_filepath = Path(filepath).resolve()
        
        # Ensure filepath is within workspace
        abs_filepath.relative_to(abs_workspace)
        rel_posix = abs_filepath.relative_to(abs_workspace).as_posix()
    except (ValueError, RuntimeError):
        # File is outside the workspace path
        return False

    name = agent_name.lower().replace(" ", "_").replace("-", "_")

    if name == "scout":
        return rel_posix == ".saltcode/context_report.json"
    if name == "architect":
        return rel_posix == ".saltcode/design.md"
    if name == "planner":
        return rel_posix == ".saltcode/tasks.json"
    if name in ("test_intent", "test-intent"):
        # Match .saltcode/tests/task_{id}_spec.{ext}
        parts = rel_posix.split("/")
        if len(parts) == 3 and parts[0] == ".saltcode" and parts[1] == "tests":
            return parts[2].startswith("task_") and "_spec." in parts[2]
        return False
    if name == "evaluator":
        return rel_posix == ".saltcode/evaluator_report.json"
    if name == "auditor":
        return rel_posix == ".saltcode/audit_result.json"
    if name in ("spec_compactor", "compactor"):
        return rel_posix == ".saltcode/design.md"
    if name == "builder":
        return (
            not rel_posix.startswith(".saltcode/")
            and not rel_posix.startswith("tests/")
            and "/tests/" not in rel_posix
        )

    return False


def validate_builder_diff_paths(diff_content: str) -> bool:
    """Parses builder unified diff and returns False if any hunk modifies tests/** or .saltcode/tests/**."""
    # Find all --- a/path and +++ b/path lines
    # Diff header patterns:
    # --- a/filename
    # +++ b/filename
    # --- /dev/null
    # +++ /dev/null
    pattern = re.compile(r"^(?:--- a/|\+\+\+ b/)(.*)$")
    
    for line in diff_content.splitlines():
        match = pattern.match(line)
        if match:
            path = match.group(1).strip()
            if path == "/dev/null":
                continue
            
            # Normalize path delimiters
            path_normalized = path.replace("\\", "/")
            
            # Reject if the path targets tests/** or .saltcode/tests/**
            if (
                path_normalized.startswith("tests/") or 
                path_normalized.startswith(".saltcode/tests/") or
                "/tests/" in path_normalized
            ):
                return False

    return True
