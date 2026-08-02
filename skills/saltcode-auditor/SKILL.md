---
name: saltcode-auditor
description: Guides the Auditor agent to detect gaming behavior and compute multi-pass stability metrics for faithfulness verification of Builder diffs.
---

# Saltcode Auditor Skill

Use this when auditing a completed task's unified diff in Phase 2.

## Inputs
- The unified diff (from Builder).
- The clean static analysis report.
- Test runner results (pass + stdout/stderr).
- The test spec content (`tests/task_{id}_spec.*`).
- The task's `acceptance_criteria[]`.

## Step 1: Heuristic Detection (zero cost)
Scan the diff for these red flags:
- **Fixture-literal returns**: functions return hardcoded values matching test expectations.
- **Empty/throw-only bodies**: functions under test have no logic, just `pass` / `throw` / `return None`.
- **Test-input-keyed branches**: `if input == <known_test_value>: return <expected>`.
- **Suspiciously short implementations**: a complex task solved in 3 lines.

If any heuristic fires → preliminary verdict = `gaming_suspected` (the stability passes can override this if the implementation is actually correct).

## Step 2: Multi-Pass Stability (adapted from BIFAI-NET v5.2)
Run the faithfulness judgment **N=3 times** with varied conditions:
- Pass 1: temperature 0.3, evidence in original order.
- Pass 2: temperature 0.5, acceptance_criteria reordered.
- Pass 3: temperature 0.7, diff hunks reordered.

Each pass independently produces a verdict: `pass`, `impl_fail`, `gaming_suspected`, or `spec_defect`.

Compute:
```
stability_score = 1.0 - (verdict_changes / (N - 1))
gac = first pass index where all subsequent passes agree
```
- **stability_score = 1.0** (all 3 agree): high confidence, use the unanimous verdict.
- **stability_score < threshold** (oscillating): low confidence.
  - Online → re-run judgment once on Flash. Flash verdict is final.
  - Offline → accept the majority local verdict.

The threshold is set by the **measured-then-fixed calibration protocol** (see `specs/design.md` §8.9). If uncalibrated, use conservative default 0.5.

## Step 3: Emit Verdict
Write `.saltcode/audit_result.json`:
```json
{
  "schema_version": "1",
  "task_id": "T1",
  "status": "pass",
  "reason": "pass",
  "next_action": "next_task",
  "detail": "Implementation satisfies all acceptance criteria with genuine logic",
  "stability": {
    "n_passes": 3,
    "verdicts": ["pass", "pass", "pass"],
    "stability_score": 1.0,
    "gac": 0
  }
}
```

## Forbidden Actions
- **Cannot write code.** You only judge.
- **Cannot modify tests.** You only read them.
