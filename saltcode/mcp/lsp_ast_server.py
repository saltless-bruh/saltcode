import os
import socket
import urllib.parse
import urllib.request
from typing import Any, cast
from urllib.parse import ParseResult

from mcp.server.fastmcp import FastMCP

from saltcode.mcp.lsp_backends import (
    fallback_find_references,
    fallback_outline,
    fallback_where_is,
    get_lsp_backend,
    load_project_config,
)

original_connect = socket.socket.connect


# Enforce localhost-only/local-only transport.
# We assert that we are running locally and do not transmit repo data to remote hosts.
# We also restrict any socket connection to localhost.
def connect_wrapper(self: socket.socket, address: tuple[str, int] | Any) -> None:
    host: str = str(address[0])
    if host not in ("127.0.0.1", "localhost", "::1"):
        # Enforce privacy boundary: raise connection error if attempting to connect externally
        raise PermissionError(f"Outbound network connection to {host} blocked by Saltcode privacy boundary.")
    original_connect(self, address)


socket.socket.connect = connect_wrapper  # type: ignore[assignment]

mcp = FastMCP("Saltcode LSP/AST Server")


def get_workspace_path() -> str:
    return os.path.abspath(os.environ.get("SALTCODE_WORKSPACE", "."))


# Active backend cache
_backend: Any = None
_backend_initialized = False


def get_active_backend() -> Any:
    global _backend, _backend_initialized
    if not _backend_initialized:
        _backend = get_lsp_backend(get_workspace_path())
        _backend_initialized = True
    return _backend


def get_project_lang() -> str:
    try:
        config = load_project_config(get_workspace_path())
        return config.language
    except Exception:
        return "python"


@mcp.tool()
def outline(file_path: str) -> str:
    """Returns the symbol outline structure of a specific file."""
    # Ensure relative paths are handled correctly
    full_path = os.path.abspath(os.path.join(get_workspace_path(), file_path))
    backend = get_active_backend()
    if backend and backend.process and backend.running:
        try:
            # Note: uri must be formatted correctly
            uri = urllib.parse.urljoin("file:", urllib.request.pathname2url(full_path))
            params = {"textDocument": {"uri": uri}}
            resp = backend.send_request("textDocument/documentSymbol", params)
            if "result" in resp and resp["result"] is not None:
                from saltcode.mcp.lsp_backends import format_symbols

                return format_symbols(resp["result"])
        except Exception:
            pass

    # Fallback to AST/Regex
    return fallback_outline(full_path, get_project_lang())


@mcp.tool()
def where_is(symbol_name: str, file_context: str | None = None) -> str:
    """Finds the definition coordinates of a symbol."""
    backend = get_active_backend()
    if backend and backend.process and backend.running:
        try:
            from saltcode.mcp.lsp_backends import find_symbol_occurrences

            occurrences = find_symbol_occurrences(get_workspace_path(), symbol_name, get_project_lang())
            if file_context:
                # filter occurrences by context file
                occurrences = [occ for occ in occurrences if str(occ[0]).endswith(file_context)]

            if occurrences:
                path, line_idx, col = occurrences[0]
                uri = urllib.parse.urljoin("file:", urllib.request.pathname2url(str(path)))
                params = {"textDocument": {"uri": uri}, "position": {"line": line_idx, "character": col}}
                resp = backend.send_request("textDocument/definition", params)
                if "result" in resp and resp["result"]:
                    raw_result: Any = resp["result"]
                    res: dict[str, Any] = cast(
                        dict[str, Any],
                        raw_result[0] if isinstance(raw_result, list) else raw_result,
                    )
                    uri_def: str = str(res.get("uri", ""))
                    parsed_url: ParseResult = urllib.parse.urlparse(uri_def)
                    path_def: str = urllib.request.url2pathname(parsed_url.path)
                    if path_def.startswith("///"):
                        path_def = path_def[2:]
                    rel_path = os.path.relpath(path_def, get_workspace_path())

                    range_val: dict[str, Any] = dict(res.get("range", {}))
                    start_pos: dict[str, int] = dict(range_val.get("start", {}))
                    line: int = int(start_pos.get("line", 0)) + 1
                    col_start: int = int(start_pos.get("character", 0)) + 1
                    return f"file:{rel_path}, line:{line}, col:{col_start}"
        except Exception:
            pass

    return fallback_where_is(get_workspace_path(), symbol_name, file_context, get_project_lang())


@mcp.tool()
def find_references(symbol_name: str, file_path: str) -> list[str]:
    """Finds all file coordinates references of a specific symbol."""
    backend = get_active_backend()
    if backend and backend.process and backend.running:
        try:
            from saltcode.mcp.lsp_backends import find_symbol_occurrences

            occurrences = find_symbol_occurrences(get_workspace_path(), symbol_name, get_project_lang())
            target_occ = [occ for occ in occurrences if str(occ[0]).endswith(file_path)]
            if target_occ:
                path, line_idx, col = target_occ[0]
                uri = urllib.parse.urljoin("file:", urllib.request.pathname2url(str(path)))
                params = {
                    "textDocument": {"uri": uri},
                    "position": {"line": line_idx, "character": col},
                    "context": {"includeDeclaration": True},
                }
                resp = backend.send_request("textDocument/references", params)
                if "result" in resp and resp["result"]:
                    refs: list[str] = []
                    for loc in resp["result"]:
                        loc_dict: dict[str, Any] = dict(loc)
                        uri_ref: str = str(loc_dict.get("uri", ""))
                        ref_parsed: ParseResult = urllib.parse.urlparse(uri_ref)
                        path_ref: str = urllib.request.url2pathname(ref_parsed.path)
                        if path_ref.startswith("///"):
                            path_ref = path_ref[2:]
                        rel_path = os.path.relpath(path_ref, get_workspace_path())
                        r_val: dict[str, Any] = dict(loc_dict.get("range", {}))
                        s_pos: dict[str, int] = dict(r_val.get("start", {}))
                        line: int = int(s_pos.get("line", 0)) + 1
                        col_start: int = int(s_pos.get("character", 0)) + 1
                        refs.append(f"file:{rel_path}, line:{line}, col:{col_start}")
                    return refs
        except Exception:
            pass

    return fallback_find_references(get_workspace_path(), symbol_name, file_path, get_project_lang())


if __name__ == "__main__":
    mcp.run()
