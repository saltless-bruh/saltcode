# Saltcode — Developer Handbook & Cookbook

This document is a technical developer guide and code cookbook for implementing the **Saltcode** framework. It contains concrete architecture recipes, JSON-RPC LSP handshakes, LanceDB schemas, sandbox git commands, and mathematical formulations for the stability-scoring mechanisms.

---

## Table of Contents
1. [LSP JSON-RPC Client Integration](#1-lsp-json-rpc-client-integration)
2. [FastMCP AST Server & Scoped Broker](#2-fastmcp-ast-server--scoped-broker)
3. [Pydantic Contracts & Output-Length Enforcer](#3-pydantic-contracts--output-length-enforcer)
4. [LanceDB Memory & Cache Ladder (PCD)](#4-lancedb-memory--cache-ladder-pcd)
5. [Local serving & Saltnitor Integration](#5-local-serving--saltnitor-integration)
6. [Sandbox Operations & Git Worktrees](#6-sandbox-operations--git-worktrees)
7. [Auditor Faithfulness & Multi-Pass Stability](#7-auditor-faithfulness--multi-pass-stability)

---

## 1. LSP JSON-RPC Client Integration

The LSP/AST MCP server drives backends (`pyright`, `tsserver`, `rust-analyzer`, `gopls`) over standard input/output (`stdio`) using JSON-RPC 2.0.

### 1.1 The Stdio Protocol & Framing
Every message sent to or received from a language server must have a header specifying the content length in bytes:
```http
Content-Length: <n>\r\n
\r\n
<JSON-RPC Message Content>
```

#### Message Reader Helper (Python)
Reading from the stdout stream must be done in a non-blocking or threaded manner to avoid buffer blocks.
```python
import io
import sys

def read_lsp_message(stream: io.TextIOBase) -> dict:
    """Reads a single framed JSON-RPC message from an LSP stdout stream."""
    content_length = 0
    while True:
        line = stream.readline()
        if not line:
            raise EOFError("Stdout stream closed by language server")
        line = line.strip()
        if line.startswith("Content-Length:"):
            content_length = int(line.split(":")[1].strip())
        elif line == "":
            # End of headers, start of content
            break
            
    if content_length == 0:
        raise ValueError("Invalid LSP frame: Content-Length is missing or 0")
        
    content = stream.read(content_length)
    return json.loads(content)
```

#### Message Writer Helper
```python
def write_lsp_message(stream: io.TextIOBase, message: dict):
    """Writes a framed JSON-RPC message to an LSP stdin stream."""
    content = json.dumps(message)
    content_bytes = content.encode("utf-8")
    header = f"Content-Length: {len(content_bytes)}\r\n\r\n"
    stream.write(header + content)
    stream.flush()
```

### 1.2 Handshake Sequence
Before making queries, the client must perform the initialization handshake:

```
Client                                     Server
  │                                          │
  ├─────── initialize (request) ────────────>│
  │<────── [Response] ───────────────────────┤
  ├─────── initialized (notification) ──────>│
  │                                          │
```

#### JSON Payloads

**1. `initialize` Request**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "processId": null,
    "rootPath": "/absolute/path/to/target/project",
    "rootUri": "file:///absolute/path/to/target/project",
    "capabilities": {
      "textDocument": {
        "documentSymbol": {
          "hierarchicalDocumentSymbolSupport": true
        },
        "definition": {
          "dynamicRegistration": true
        },
        "references": {
          "dynamicRegistration": true
        }
      }
    }
  }
}
```

**2. `initialized` Notification** (Note: has no `id` field)
```json
{
  "jsonrpc": "2.0",
  "method": "initialized",
  "params": {}
}
```

### 1.3 Target Commands
- **Pyright**: `pyright-langserver --stdio`
- **Tsserver**: `typescript-language-server --stdio`
- **Rust-analyzer**: `rust-analyzer`
- **Gopls**: `gopls`

---

## 2. FastMCP AST Server & Scoped Broker

Using `FastMCP` (from the MCP Python SDK), we expose structural tools to the agent harness. 

### 2.1 Tool Definitions
```python
from fastmcp import FastMCP
import os
import sys

mcp = FastMCP("Saltcode AST Server")

@mcp.tool()
def outline(file_path: str) -> str:
    """Returns the symbol outline structure of a specific file."""
    # Logic connects to the active LSP server and sends 'textDocument/documentSymbol'
    pass

@mcp.tool()
def where_is(symbol_name: str, file_context: str = None) -> str:
    """Finds the definition coordinates of a symbol."""
    # Sends 'textDocument/definition' request to LSP
    pass

@mcp.tool()
def find_references(symbol_name: str, file_path: str) -> list[str]:
    """Finds all file coordinates references of a specific symbol."""
    # Sends 'textDocument/references' request to LSP
    pass
```

### 2.2 Scoped File-Read Broker
To enforce the **privacy boundary** and **task isolation context limit**, a broker wrapper intercepts tool execution.

```python
class ToolBroker:
    def __init__(self, current_agent_role: str, files_affected: list[str] = None):
        self.role = current_agent_role
        self.files_affected = [os.path.abspath(f) for f in (files_affected or [])]

    def authorize_read(self, file_path: str) -> bool:
        """Determines if the active agent is authorized to read the file body."""
        abs_path = os.path.abspath(file_path)
        
        # Invariants:
        # 1. Scout is denied read access (restricted to symbols/AST only).
        # 2. Builder is allowed to read ONLY the files specified in files_affected.
        # 3. All other planning/auditing agents are denied raw file reads.
        if self.role == "Builder":
            return abs_path in self.files_affected
        return False
```

---

## 3. Pydantic Contracts & Output-Length Enforcer

All Phase 1 structures are strictly typed using Pydantic v2 to validate bounds and layout correctness.

### 3.1 Pydantic Schemas
```python
from pydantic import BaseModel, Field, field_validator
from typing import List, Literal, Optional

class ContextReport(BaseModel):
    schema_version: str = "1"
    existing_patterns: List[str]
    relevant_files: List[str]
    constraints: List[str]
    anti_patterns: List[str]

class Task(BaseModel):
    id: str
    description: str
    files_affected: List[str]
    acceptance_criteria: List[str]
    depends_on: List[str]
    complexity: Literal["low", "med", "high"]

class TasksFile(BaseModel):
    tasks: List[Task]
    
    @field_validator("tasks")
    @classmethod
    def validate_acyclic_dag(cls, tasks: List[Task]) -> List[Task]:
        # Implementation of Topological Sort (Kahn's or DFS)
        adj = {t.id: t.depends_on for t in tasks}
        visited = {} # id -> state (0 = visiting, 1 = visited)
        
        def visit(node):
            if node not in adj:
                raise ValueError(f"Dangling task dependency detected: {node}")
            if visited.get(node) == 0:
                raise ValueError("Cyclic dependency detected in task plan")
            if node not in visited:
                visited[node] = 0
                for dep in adj[node]:
                    visit(dep)
                visited[node] = 1
                
        for t_id in adj:
            visit(t_id)
        return tasks
```

### 3.2 Output-Length Enforcer & json_repair
When models return truncated/malformed JSON strings, we attempt a single bounded repair using the `json_repair` library.

```python
import json
from json_repair import repair_json

def clean_and_parse_json(model_output: str) -> dict:
    """Preprocesses raw LLM text, extracts JSON block, repairs if malformed, and parses."""
    # 1. Extract markdown block if present
    content = model_output.strip()
    if "```json" in content:
        content = content.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in content:
        content = content.split("```", 1)[1].split("```", 1)[0].strip()
        
    # 2. Parse directly
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
        
    # 3. Attempt repair
    try:
        repaired = repair_json(content)
        return json.loads(repaired)
    except Exception as e:
        raise ValueError(f"Enforcer was unable to repair malformed JSON structure: {str(e)}")
```

---

## 4. LanceDB Memory & Cache Ladder (PCD)

For offline-capable caching and RAG, we use a local LanceDB storage.

### 4.1 Schema Layout
We define tables using `LanceModel` to support semantic-vector indexes alongside fields.

```python
import lancedb
from lancedb.pydantic import LanceModel, Vector

# Dimension for bge-small-en-v1.5 / nomic-embed is 384 / 768
VECTOR_DIM = 384

class SpecCacheModel(LanceModel):
    goal_hash: str                  # sha256(normalized_goal + scope_fingerprint)
    normalized_goal: str
    scope_fingerprint: str          # sorted list of files/modules
    tasks_json_content: str

class SemanticCacheModel(LanceModel):
    vector: Vector(VECTOR_DIM)
    goal: str
    scope_fingerprint: str
    tasks_json_content: str
```

### 4.2 Prior Cluster Density (PCD)
The semantic cosine threshold needs to be adaptive to prevent false reuses in sparse goal areas.

$$\text{PCD} = \frac{\text{Count of items within cosine radius } r \text{ from query}}{\text{Total records in cache}}$$

#### PCD Calculation Implementation
```python
def calculate_pcd(table, query_vector: list[float], radius: float = 0.15) -> float:
    """Computes Prior Cluster Density of a goal vector within a specific cosine radius."""
    total_records = len(table)
    if total_records == 0:
        return 0.0
        
    # Perform vector search
    results = table.search(query_vector).distance_range(0, radius).to_list()
    matching_count = len(results)
    
    return matching_count / total_records
```

---

## 5. Local serving & Saltnitor Integration

Phase 2 runs sequential local inference using **Saltnitor's** router-switch control API at `http://127.0.0.1:8765/v1`.

### 5.1 VRAM Hot-Swap (`ensure` Endpoint)
Before prompting the local model, the harness commands the VRAM manager to allocate the corresponding profile:
```python
import httpx

def ensure_model_profile(profile_name: str):
    """Locks and hot-swaps active LLM in VRAM (Saltnitor DD-2 oracle)."""
    try:
        response = httpx.post(
            "http://127.0.0.1:8765/v1/ensure",
            json={"profile": profile_name},
            timeout=30.0
        )
        if response.status_code == 507:
            # Out of memory
            raise MemoryError("Saltnitor VRAM Allocation Refused (OOM)")
        response.raise_for_status()
    except httpx.RequestError as e:
        # Fallback to local direct llama-server running at port 8080
        pass
```

### 5.2 VRAM Triangle Parameter Profiles
To prevent VRAM bottlenecks on a single 12GB GPU target:
*   **A_STD**: Modest Context (64K), Thinking ON, MTP OFF.
*   **A_FOCUS**: Extended Context (256K), Thinking OFF, MTP OFF.

---

## 6. Sandbox Operations & Git Worktrees

The harness executes diff checks, static compilers, and test suites strictly in a sandbox, keeping the developer's working directory clean.

### 6.1 Sandbox Workflow (Python/Git Worktree Implementation)
```python
import subprocess
import tempfile
import shutil
import os

class GitSandbox:
    def __init__(self, main_repo_path: str):
        self.main_repo = main_repo_path
        self.sandbox_path = None
        self.branch_name = None

    def __enter__(self):
        # 1. Create a unique branch and temporary workspace path
        self.branch_name = f"saltcode-sandbox-{tempfile.mktemp()[-6:]}"
        self.sandbox_path = os.path.join(self.main_repo, "..", f"sandbox-{self.branch_name}")
        
        # 2. Add git worktree
        subprocess.run(
            ["git", "-C", self.main_repo, "worktree", "add", "-b", self.branch_name, self.sandbox_path, "HEAD"],
            check=True, capture_output=True
        )
        return self.sandbox_path

    def __exit__(self, exc_type, exc_val, exc_tb):
        # 3. Clean up worktree and delete the temporary branch
        if self.sandbox_path and os.path.exists(self.sandbox_path):
            subprocess.run(
                ["git", "-C", self.main_repo, "worktree", "remove", "--force", self.sandbox_path],
                capture_output=True
            )
            # Remove branch
            subprocess.run(
                ["git", "-C", self.main_repo, "branch", "-D", self.branch_name],
                capture_output=True
            )
```

### 6.2 Sandbox Compilation/Lifting Commands
Inside the sandbox workspace path:
- **TypeScript**: `npm run build` or `npx tsc --noEmit` and `npx eslint`
- **Rust**: `cargo check --message-format=json` or `cargo clippy`
- **Go**: `go build ./...` and `go vet ./...`
- **Python**: `pyright --strict` and `ruff check`

---

## 7. Auditor Faithfulness & Multi-Pass Stability

To counter gaming behaviors (such as returning hardcoded constants from mock fixtures), the Auditor executes a behavioral stability test.

### 7.1 Multi-Pass Verdict Formula
The Auditor is invoked $N$ times (default $N=3$) under slightly varied conditions (temperature jitter, reordered prompt inputs).
Let $V = [v_1, v_2, \dots, v_N]$ be the sequence of verdicts ($v_i \in \{\text{pass}, \text{impl\_fail}, \text{gaming\_suspected}, \text{spec\_defect}\}$).

Let $C$ be the number of transitions where $v_i \neq v_{i-1}$ for $i \in \{2, \dots, N\}$:

$$C = \sum_{i=2}^{N} \mathbb{I}(v_i \neq v_{i-1})$$

The **Stability Score** is defined as:

$$\text{stability\_score} = 1.0 - \frac{C}{N - 1}$$

If all $N$ verdicts match, $\text{stability\_score} = 1.0$.

### 7.2 Generation-at-Classification (GaC)
**GaC** represents the index $g \in \{0, \dots, N-1\}$ where the verdict sequence stabilizes permanently:

$$g = \min \left\{ k \in [0, N-1] \mid v_j = v_k \text{ for all } j \ge k \right\}$$

If the verdict sequence never stabilizes (e.g., oscillating between `pass` and `impl_fail` until the final element), $g = N-1$. A high GaC relative to $N$ indicates high inference noise.

### 7.3 Jitter Parameters Recipe
To create distinct context conditions, each stability pass is initialized with a different request configuration:
*   **Pass 1**: $T = 0.3$, Inputs presented: `[Diff, Acceptance Criteria, Test Output]`.
*   **Pass 2**: $T = 0.5$, Inputs presented: `[Acceptance Criteria, Test Output, Diff]`.
*   **Pass 3**: $T = 0.7$, Inputs presented: `[Test Output, Diff, Acceptance Criteria]`.
