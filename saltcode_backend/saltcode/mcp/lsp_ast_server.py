import ipaddress
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

# --------------------------------------------------------------- egress guard (4.3)
#
# REQ-MCP-002: this server holds the whole repository's symbol graph, so it is
# the single process with the most to leak. It may talk to a language server
# over stdio and to nothing else. The guard is installed at import — not behind
# a call the `__main__` block might skip — because a privacy boundary that
# depends on someone remembering to switch it on is not a boundary.
#
# It patches the socket layer rather than any HTTP client: every TCP connection
# in CPython goes through `socket.connect`/`connect_ex`, so there is no library
# that routes around it. `_SALTCODE_GUARDED` makes installation idempotent —
# without it, a module reload would capture the wrapper as "the original" and
# recurse forever on the next connect.

_GUARD_FLAG = "_saltcode_egress_guarded"

_REFUSAL = (
    "Outbound network connection to {host} blocked by Saltcode privacy boundary "
    "(REQ-MCP-002: the LSP/AST server never transmits repo content off-box)."
)


def _is_loopback(host: str) -> bool:
    """Only the local machine counts as local (single-machine deployment).

    Matches `providers/guard.classify_destination`: loopback literals and the
    reserved `localhost` name, nothing else. Unparseable → not loopback, so the
    unknown case is the refusing one.
    """
    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


def install_egress_guard() -> None:
    """Refuse any non-loopback socket connection from this process. Idempotent."""
    if getattr(socket.socket, _GUARD_FLAG, False):
        return

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def _refused_host(address: object) -> str | None:
        """The host to refuse, or None when this address may proceed.

        Only AF_INET/AF_INET6 addresses are (host, port) tuples. A unix socket
        address is a path string and carries no network egress, so it passes.
        """
        if not isinstance(address, tuple) or not address:
            return None
        host = str(cast("tuple[Any, ...]", address)[0])
        return None if _is_loopback(host) else host

    def connect_wrapper(self: socket.socket, address: Any) -> None:
        host = _refused_host(address)
        if host is not None:
            raise PermissionError(_REFUSAL.format(host=host))
        original_connect(self, address)

    def connect_ex_wrapper(self: socket.socket, address: Any) -> int:
        # Also guarded: `connect_ex` returns an errno instead of raising, so a
        # caller using it would otherwise have an unguarded path to the network.
        host = _refused_host(address)
        if host is not None:
            raise PermissionError(_REFUSAL.format(host=host))
        return original_connect_ex(self, address)

    socket.socket.connect = connect_wrapper  # type: ignore[assignment]
    socket.socket.connect_ex = connect_ex_wrapper  # type: ignore[assignment]
    socket.socket._saltcode_egress_guarded = True  # type: ignore[attr-defined]


install_egress_guard()

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
            # The document must be opened first: pyright answers documentSymbol
            # for an unknown document with an empty result, which is
            # indistinguishable from "this file has no symbols".
            backend.did_open(full_path, get_project_lang())

            uri = urllib.parse.urljoin("file:", urllib.request.pathname2url(full_path))
            params = {"textDocument": {"uri": uri}}
            resp = backend.send_request("textDocument/documentSymbol", params)
            if "result" in resp and resp["result"]:
                from saltcode.mcp.lsp_backends import format_symbols

                formatted = format_symbols(resp["result"])
                # An empty render falls through rather than being returned: a
                # live-but-unhelpful language server must never leave the caller
                # worse off than having none at all.
                if formatted.strip():
                    return formatted
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
