import ast
import json
import logging
import os
import re
import subprocess
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Literal

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        import pip._vendor.tomli as tomllib  # type: ignore

from pydantic import BaseModel

logger = logging.getLogger(__name__)

class ProjectConfig(BaseModel):
    language: Literal["python", "typescript", "rust", "go", "javascript"]
    test_framework: Literal["pytest", "jest", "cargo-test", "go-test"]
    test_runner_cmd: str | None = None
    static_gate_cmd: str | None = None

def load_project_config(workspace_path: Path | str) -> ProjectConfig:
    workspace_path = Path(workspace_path)
    config_file = workspace_path / "saltcode.toml"
    if not config_file.exists():
        config_file = workspace_path / ".saltcode" / "config.toml"
    
    if not config_file.exists():
        raise FileNotFoundError(f"Project config file not found in {workspace_path}")
        
    with open(config_file, "rb") as f:
        data: dict[str, Any] = dict(tomllib.load(f))  # type: ignore[reportUnknownMemberType]
    
    project_data: dict[str, Any] = dict(data.get("project", data))
    return ProjectConfig(
        language=str(project_data.get("language", "python")),  # type: ignore[arg-type]
        test_framework=str(project_data.get("test_framework", "pytest")),  # type: ignore[arg-type]
        test_runner_cmd=project_data.get("test_runner_cmd"),
        static_gate_cmd=project_data.get("static_gate_cmd"),
    )

class LSPBackend:
    cmd: list[str]
    root_path: str
    process: subprocess.Popen[bytes] | None
    reader_thread: threading.Thread | None
    request_id: int
    pending_requests: dict[int, Future[dict[str, Any]]]
    lock: threading.Lock
    running: bool

    def __init__(self, cmd: list[str], root_path: str) -> None:
        self.cmd = cmd
        self.root_path = os.path.abspath(root_path)
        self.process = None
        self.reader_thread = None
        self.request_id = 0
        self.pending_requests = {}
        self.lock = threading.Lock()
        self.running = False
        
    def start(self) -> None:
        try:
            self.process = subprocess.Popen(
                self.cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0
            )
        except FileNotFoundError:
            logger.warning(f"LSP executable {self.cmd[0]} not found. Running in fallback mode.")
            self.process = None
            return
            
        self.running = True
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()
        
        try:
            self._handshake()
        except Exception as e:
            logger.error(f"LSP handshake failed: {e}. Falling back.")
            self.stop()

    def _reader_loop(self) -> None:
        if not self.process or not self.process.stdout:
            return
        stdout = self.process.stdout
        try:
            while self.running:
                headers: dict[str, str] = {}
                while True:
                    line = stdout.readline()
                    if not line:
                        return
                    line_str = line.decode("utf-8").strip()
                    if not line_str:
                        break
                    if ":" in line_str:
                        key, val = line_str.split(":", 1)
                        headers[key.strip().lower()] = val.strip()
                
                content_length = int(headers.get("content-length", 0))
                if content_length == 0:
                    continue
                
                body_bytes = stdout.read(content_length)
                if len(body_bytes) < content_length:
                    return
                
                message = json.loads(body_bytes.decode("utf-8"))
                self._handle_message(message)
        except Exception:
            pass

    def _handle_message(self, message: dict[str, Any]) -> None:
        if "id" in message:
            msg_id = message["id"]
            if isinstance(msg_id, int):
                with self.lock:
                    future = self.pending_requests.pop(msg_id, None)
                if future:
                    future.set_result(message)

    def send_request(self, method: str, params: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
        if not self.process or not self.running:
            raise RuntimeError("LSP server not running")
            
        with self.lock:
            self.request_id += 1
            req_id = self.request_id
            future: Future[dict[str, Any]] = Future()
            self.pending_requests[req_id] = future
            
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params
        }
        
        self._write_message(payload)
        return future.result(timeout=timeout)

    def send_notification(self, method: str, params: dict[str, Any]) -> None:
        if not self.process or not self.running:
            return
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        self._write_message(payload)

    def _write_message(self, payload: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            return
        body = json.dumps(payload)
        body_bytes = body.encode("utf-8")
        header = f"Content-Length: {len(body_bytes)}\r\n\r\n".encode()
        try:
            self.process.stdin.write(header + body_bytes)
            self.process.stdin.flush()
        except Exception:
            self.stop()

    def _handshake(self) -> None:
        init_params = {
            "processId": os.getpid(),
            "rootPath": self.root_path,
            "rootUri": f"file://{self.root_path}",
            "capabilities": {
                "textDocument": {
                    "documentSymbol": {
                        "hierarchicalDocumentSymbolSupport": True
                    },
                    "definition": {
                        "dynamicRegistration": True
                    },
                    "references": {
                        "dynamicRegistration": True
                    }
                }
            }
        }
        self.send_request("initialize", init_params)
        self.send_notification("initialized", {})

    def stop(self) -> None:
        self.running = False
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=1.0)
            except Exception:
                import contextlib
                with contextlib.suppress(Exception):
                    self.process.kill()
            self.process = None


# Helper to format symbols (kinds mapped to string)
def format_symbols(symbols: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    kind_map = {
        1: "File", 2: "Module", 3: "Namespace", 4: "Package", 5: "Class",
        6: "Method", 7: "Property", 8: "Field", 9: "Constructor", 10: "Enum",
        11: "Interface", 12: "Function", 13: "Variable", 14: "Constant",
        15: "String", 16: "Number", 17: "Boolean", 18: "Array", 19: "Object",
        20: "Key", 21: "Null", 22: "EnumMember", 23: "Struct", 24: "Event",
        25: "Operator", 26: "TypeParameter"
    }
    
    def recurse(sym: dict[str, Any], depth: int = 0) -> None:
        name = sym.get("name", "")
        kind_id = sym.get("kind", 0)
        kind = kind_map.get(kind_id, f"Symbol({kind_id})")
        range_val: dict[str, Any] = sym.get("range", {})
        start_line = range_val.get("start", {}).get("line", 0) + 1
        end_line = range_val.get("end", {}).get("line", 0) + 1
        
        indent = "  " * depth
        lines.append(f"{indent}- {kind}: {name} (lines {start_line}-{end_line})")
        children: list[dict[str, Any]] = sym.get("children", [])
        for child in children:
            recurse(child, depth + 1)
            
    for sym in symbols:
        if "location" in sym:
            name = sym.get("name", "")
            kind_id = sym.get("kind", 0)
            kind = kind_map.get(kind_id, f"Symbol({kind_id})")
            loc: dict[str, Any] = sym.get("location", {})
            start_line = loc.get("range", {}).get("start", {}).get("line", 0) + 1
            lines.append(f"- {kind}: {name} (line {start_line})")
        else:
            recurse(sym)
            
    return "\n".join(lines)


# AST & Regex Fallbacks
def fallback_outline(file_path: str, language: str) -> str:
    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        return f"Error reading file {file_path}: {e}"

    if language.lower() == "python":
        try:
            tree = ast.parse(content)
            symbols: list[dict[str, Any]] = []
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.ClassDef):
                    node_lineno = int(node.lineno)
                    node_end_lineno = int(getattr(node, "end_lineno", None) or node_lineno)
                    
                    children: list[dict[str, Any]] = []
                    for child in ast.iter_child_nodes(node):
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            child_lineno = int(child.lineno)
                            child_end_lineno = int(getattr(child, "end_lineno", None) or child_lineno)
                            children.append({
                                "name": child.name,
                                "kind": 6, # Method
                                "range": {
                                    "start": {"line": child_lineno - 1, "character": 0},
                                    "end": {"line": child_end_lineno - 1, "character": 0}
                                }
                            })
                            
                    symbols.append({
                        "name": node.name,
                        "kind": 5, # Class
                        "range": {
                            "start": {"line": node_lineno - 1, "character": 0},
                            "end": {"line": node_end_lineno - 1, "character": 0}
                        },
                        "children": children
                    })
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    node_lineno = int(node.lineno)
                    node_end_lineno = int(getattr(node, "end_lineno", None) or node_lineno)
                    symbols.append({
                        "name": node.name,
                        "kind": 12, # Function
                        "range": {
                            "start": {"line": node_lineno - 1, "character": 0},
                            "end": {"line": node_end_lineno - 1, "character": 0}
                        }
                    })
            return format_symbols(symbols)
        except Exception:
            pass
            
    # Generic regex fallback (TypeScript/JavaScript/Go/Rust)
    lines = content.splitlines()
    symbols: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        class_match = re.search(r'\bclass\s+(\w+)', line)
        if class_match:
            symbols.append({
                "name": class_match.group(1),
                "kind": 5, # Class
                "range": {"start": {"line": idx, "character": 0}, "end": {"line": idx, "character": 0}}
            })
            continue
        fn_match = re.search(r'\b(?:def|function|fn|func)\s+(\w+)', line)
        if fn_match:
            symbols.append({
                "name": fn_match.group(1),
                "kind": 12, # Function
                "range": {"start": {"line": idx, "character": 0}, "end": {"line": idx, "character": 0}}
            })
            continue
        method_match = re.search(r'^\s*(\w+)\s*\([^)]*\)\s*\{', line)
        if method_match and method_match.group(1) not in ("if", "for", "while", "switch", "catch"):
            symbols.append({
                "name": method_match.group(1),
                "kind": 6, # Method
                "range": {"start": {"line": idx, "character": 0}, "end": {"line": idx, "character": 0}}
            })
            
    return format_symbols(symbols)


def find_symbol_occurrences(workspace_path: Path | str, symbol_name: str, language: str) -> list[tuple[Path, int, int]]:
    workspace_path = Path(workspace_path)
    exts = {
        "python": [".py"],
        "typescript": [".ts", ".tsx"],
        "javascript": [".js", ".jsx"],
        "rust": [".rs"],
        "go": [".go"]
    }.get(language.lower(), [".py", ".ts", ".rs", ".go", ".js"])
    
    occurrences: list[tuple[Path, int, int]] = []
    pattern = re.compile(r'\b' + re.escape(symbol_name) + r'\b')
    skip_dirs = ('node_modules', '__pycache__', 'workspace', 'dist', 'target', '.venv')
    for root, _, files in os.walk(workspace_path):
        if any(
            part.startswith('.') or part in skip_dirs
            for part in Path(root).parts
        ):
            continue
        for file in files:
            file_path = Path(root) / file
            if file_path.suffix in exts:
                try:
                    with open(file_path, encoding="utf-8", errors="ignore") as f:
                        for idx, line in enumerate(f):
                            match = pattern.search(line)
                            if match:
                                occurrences.append((file_path, idx, match.start()))
                except Exception:
                    pass
    return occurrences


def fallback_where_is(workspace_path: Path | str, symbol_name: str, file_context: str | None, language: str) -> str:
    pattern_def = re.compile(r'\b(?:def|class|function|fn|func)\s+' + re.escape(symbol_name) + r'\b')
    pattern_var = re.compile(r'\b' + re.escape(symbol_name) + r'\b\s*=')
    
    def check_file(file_path: Path) -> tuple[int, int] | None:
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                for idx, line in enumerate(f):
                    if pattern_def.search(line) or pattern_var.search(line):
                        match = re.search(r'\b' + re.escape(symbol_name) + r'\b', line)
                        col = match.start() if match else 0
                        return idx, col
        except Exception:
            pass
        return None

    if file_context:
        abs_context = Path(workspace_path) / file_context
        coords = check_file(abs_context)
        if coords:
            return f"file:{file_context}, line:{coords[0] + 1}, col:{coords[1] + 1}"
            
    occurrences = find_symbol_occurrences(workspace_path, symbol_name, language)
    for path, line_idx, col in occurrences:
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                line = list(f)[line_idx]
                if pattern_def.search(line) or pattern_var.search(line):
                    rel_path = path.relative_to(Path(workspace_path)).as_posix()
                    return f"file:{rel_path}, line:{line_idx + 1}, col:{col + 1}"
        except Exception:
            pass
            
    if occurrences:
        path, line_idx, col = occurrences[0]
        rel_path = path.relative_to(Path(workspace_path)).as_posix()
        return f"file:{rel_path}, line:{line_idx + 1}, col:{col + 1}"
        
    return f"Symbol '{symbol_name}' not found."


def fallback_find_references(workspace_path: Path | str, symbol_name: str, _file_path: str, language: str) -> list[str]:
    occurrences = find_symbol_occurrences(workspace_path, symbol_name, language)
    refs: list[str] = []
    for path, line_idx, col in occurrences:
        rel_path = path.relative_to(Path(workspace_path)).as_posix()
        refs.append(f"file:{rel_path}, line:{line_idx + 1}, col:{col + 1}")
    return refs


def get_lsp_command(language: str) -> list[str]:
    lang = language.lower()
    if lang == "python":
        return ["pyright-langserver", "--stdio"]
    if lang in ("typescript", "javascript"):
        return ["typescript-language-server", "--stdio"]
    if lang == "rust":
        return ["rust-analyzer"]
    if lang == "go":
        return ["gopls"]
    raise ValueError(f"Unsupported language for LSP: {language}")


def get_lsp_backend(workspace_path: Path | str) -> LSPBackend | None:
    try:
        config = load_project_config(workspace_path)
    except Exception:
        logger.warning(f"Could not load project config in {workspace_path}, using default python config.")
        config = ProjectConfig(language="python", test_framework="pytest")
        
    try:
        cmd = get_lsp_command(config.language)
        backend = LSPBackend(cmd, str(workspace_path))
        backend.start()
        if backend.process and backend.running:
            return backend
    except Exception as e:
        logger.warning(f"Failed to start LSP backend: {e}. Fallback will be used.")
    return None
