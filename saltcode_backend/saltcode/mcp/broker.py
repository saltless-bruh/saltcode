import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from saltcode.mcp.lsp_ast_server import find_references, outline, where_is


class ToolBroker:
    def __init__(self, agent_role: str, workspace_path: Path | str, files_affected: list[str] | None = None):
        self.agent_role = agent_role
        self.workspace_path = os.path.abspath(workspace_path)
        self.files_affected = [os.path.abspath(os.path.join(self.workspace_path, f)) for f in (files_affected or [])]

    def get_tools(self) -> dict[str, Callable[..., Any]]:
        """Returns the dictionary of tool_name -> callable appropriate for this agent role."""
        # Scout gets AST tools only (no read_file).
        # Builder gets AST tools AND read_file.
        # Other agents get no read_file.
        tools = {
            "outline": outline,
            "where_is": where_is,
            "find_references": find_references,
        }

        role_clean = self.agent_role.lower().replace(" ", "_").replace("-", "_")
        if role_clean == "builder":
            tools["read_file"] = self.read_file

        return tools

    def read_file(self, path: str) -> str:
        """Reads a file from the workspace path, checking Builder permissions and preventing directory traversal."""
        role_clean = self.agent_role.lower().replace(" ", "_").replace("-", "_")
        if role_clean != "builder":
            raise PermissionError(f"Role '{self.agent_role}' is not authorized to read file bodies.")

        abs_path = os.path.abspath(os.path.join(self.workspace_path, path))

        # Prevent directory traversal: must be inside workspace_path
        if not abs_path.startswith(self.workspace_path):
            raise PermissionError("Access denied: path is outside the workspace directory.")

        # Scope check: must be in files_affected
        if abs_path not in self.files_affected:
            raise PermissionError(f"Access denied: path '{path}' is not in the allowed task.files_affected list.")

        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"File not found: {path}")

        with open(abs_path, encoding="utf-8", errors="ignore") as f:
            return f.read()
