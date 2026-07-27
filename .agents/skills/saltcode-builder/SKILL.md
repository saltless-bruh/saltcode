---
name: saltcode-builder
description: Guides the Builder agent to implement a single task using scoped file reads, producing a valid unified diff without editing tests.
---

# Saltcode Builder Skill

Use this when implementing a single task in Phase 2 of the Saltcode workflow.

## Context Reconstruction (mandatory on every task)

1. Read the current task object from `.saltcode/tasks.json`.
2. Read the task's spec file: `.saltcode/tests/task_{id}_spec.*`.
3. Use LSP tools (`outline`, `where_is`, `find_references`) for symbol context.
4. Read the full body of files listed in `task.files_affected` ONLY.
5. **Do NOT read**: `design.md`, sibling tasks, `context_report.json`, files not in `files_affected`.
6. **Do NOT carry over** state, notes, or context from a prior task or retry.

## Output Format

Your output MUST be a valid **unified diff**:

```diff
--- a/saltcode/contracts/context_report.py
+++ b/saltcode/contracts/context_report.py
@@ -1,3 +1,5 @@
+from pydantic import BaseModel
+
 class ContextReport(BaseModel):
     schema_version: str
```

If your output is not parseable by `git apply --check`, it will be rejected as `impl_fail` before any further gates run.

## Forbidden Actions

- **No edits to `tests/**`\*\*: the write-path allowlist will reject any hunk touching test files. You make tests pass by writing correct implementation code, not by weakening the tests.
- **No file reads outside `task.files_affected`**: the broker will deny the read.
- **No hardcoding test fixtures**: if your code returns literal values that match test expectations without actual logic, the Auditor will flag `gaming_suspected`.

## What "Making the Tests Pass" Means

The test spec describes desired behavior. Your implementation must satisfy that behavior through genuine logic — computing the right answer, not memorizing it. If you find yourself writing `if input == "test_value": return "expected_output"`, stop. That's gaming.

## Tier Routing

- `task.complexity == low|med` → you run on Tier A (Qwen3.5-9B, fast).
- `task.complexity == high` → you run on Tier B (Qwen3.6-35B, stronger).
- If you fail twice on Tier A, the 3rd attempt auto-escalates to Tier B.
