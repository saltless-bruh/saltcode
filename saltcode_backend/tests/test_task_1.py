from pathlib import Path

import pytest
from pydantic import ValidationError

from saltcode.contracts import (
    AuditResult,
    ContextReport,
    ContractValidationError,
    EvaluatorReport,
    Gap,
    NonJSONOutputError,
    OutputLengthExceededError,
    StabilityInfo,
    Task,
    TasksFile,
    UnsupportedSchemaVersionError,
    enforce_output,
    extract_hard_constraints_from_markdown,
    load_contract,
    save_contract,
)
from saltcode.diffs.diff_validator import validate_diff


def test_context_report_validation() -> None:
    # Valid ContextReport
    report = ContextReport(
        schema_version="1",
        existing_patterns=["singleton", "factory"],
        relevant_files=["src/main.py"],
        constraints=["Do not use libraries"],
        anti_patterns=["global variables"]
    )
    assert report.schema_version == "1"
    assert report.constraints == ["Do not use libraries"]

    # Invalid - missing relevant_files type check
    with pytest.raises(ValidationError):
        ContextReport(relevant_files=123)  # type: ignore

def test_tasks_file_acyclicity_and_dangling() -> None:
    # Valid DAG
    tasks = [
        Task(id="T1", description="desc1", complexity="low", depends_on=[]),
        Task(id="T2", description="desc2", complexity="med", depends_on=["T1"]),
        Task(id="T3", description="desc3", complexity="high", depends_on=["T1", "T2"]),
    ]
    tasks_file = TasksFile(schema_version="1", tasks=tasks)
    assert len(tasks_file.tasks) == 3

    # Cyclic dependency T1 -> T2 -> T1
    cyclic_tasks = [
        Task(id="T1", description="desc1", complexity="low", depends_on=["T2"]),
        Task(id="T2", description="desc2", complexity="med", depends_on=["T1"]),
    ]
    with pytest.raises(ValidationError) as exc_info:
        TasksFile(schema_version="1", tasks=cyclic_tasks)
    assert "Dependency cycle detected" in str(exc_info.value)

    # Dangling reference: T2 depends on non-existent T3
    dangling_tasks = [
        Task(id="T1", description="desc1", complexity="low", depends_on=[]),
        Task(id="T2", description="desc2", complexity="med", depends_on=["T3"]),
    ]
    with pytest.raises(ValidationError) as exc_info:
        TasksFile(schema_version="1", tasks=dangling_tasks)
    assert "Dangling reference" in str(exc_info.value)

def test_evaluator_report_validation() -> None:
    # Valid report
    report = EvaluatorReport(
        status="gaps",
        gaps=[
            Gap(id="T3", type="plan_gap", detail="missing task", target="planner")
        ],
        routing_summary="summary"
    )
    assert report.status == "gaps"
    assert len(report.gaps) == 1

def test_stability_score_validation() -> None:
    # Valid StabilityInfo
    stab = StabilityInfo(
        n_passes=3,
        verdicts=["pass", "pass", "pass"],
        stability_score=1.0,
        gac=0
    )
    assert stab.stability_score == 1.0

    # 1.0 - (1 / 2) = 0.5
    stab_mixed = StabilityInfo(
        n_passes=3,
        verdicts=["pass", "pass", "fail"],
        stability_score=0.5,
        gac=1
    )
    assert stab_mixed.stability_score == 0.5

    # Incorrect stability score
    with pytest.raises(ValidationError):
        StabilityInfo(
            n_passes=3,
            verdicts=["pass", "pass", "fail"],
            stability_score=1.0,
            gac=1
        )

    # Verdicts length mismatch with n_passes
    with pytest.raises(ValidationError):
        StabilityInfo(
            n_passes=2,
            verdicts=["pass", "pass", "pass"],
            stability_score=1.0,
            gac=0
        )

def test_audit_result_validation() -> None:
    stab = StabilityInfo(n_passes=1, verdicts=["pass"], stability_score=1.0, gac=0)
    
    # Valid AuditResult
    res = AuditResult(
        task_id="T1",
        status="pass",
        reason="pass",
        next_action="next_task",
        detail="good job",
        stability=stab
    )
    assert res.status == "pass"

    # Invalid: status=pass but reason=impl_fail
    with pytest.raises(ValidationError):
        AuditResult(
            task_id="T1",
            status="pass",
            reason="impl_fail",
            next_action="builder_retry",
            detail="failed",
            stability=stab
        )

    # Invalid: reason=spec_defect but next_action is not test_intent_respec
    with pytest.raises(ValidationError):
        AuditResult(
            task_id="T1",
            status="fail",
            reason="spec_defect",
            next_action="builder_retry",
            detail="bad spec",
            stability=stab
        )

def test_design_doc_constraints_extraction() -> None:
    markdown = """
# System Design

## Description
This is a design document.

## HARD CONSTRAINTS
- Verbatim constraint 1
* Verbatim constraint 2
- Verbatim constraint 3

## Other Section
Some other design details.
"""
    constraints = extract_hard_constraints_from_markdown(markdown)
    assert constraints == {"Verbatim constraint 1", "Verbatim constraint 2", "Verbatim constraint 3"}

    # Missing constraints section
    bad_markdown = """
# System Design
No constraints section here.
"""
    with pytest.raises(ValueError) as exc_info:
        extract_hard_constraints_from_markdown(bad_markdown)
    assert "HARD CONSTRAINTS" in str(exc_info.value)

def test_enforce_output_length_and_prose() -> None:
    # Test valid JSON within length
    output_str = (
        '{"schema_version": "1", "existing_patterns": ["pat1"], '
        '"relevant_files": [], "constraints": [], "anti_patterns": []}'
    )
    res = enforce_output(output_str, ContextReport, max_length=500)
    assert res.schema_version == "1"

    # Test length exceeded
    with pytest.raises(OutputLengthExceededError):
        enforce_output(output_str, ContextReport, max_length=50)

    # Test prose-only rejection
    prose_str = "This is not JSON at all, it's just some conversational prose explaining the results."
    with pytest.raises(NonJSONOutputError):
        enforce_output(prose_str, ContextReport, json_only=True)

    # Test markdown fenced JSON extraction
    fenced_str = """
Here is the requested JSON report:
```json
{
  "schema_version": "1",
  "existing_patterns": ["pat1"],
  "relevant_files": [],
  "constraints": [],
  "anti_patterns": []
}
```
    """
    res_fenced = enforce_output(fenced_str, ContextReport)
    assert res_fenced.existing_patterns == ["pat1"]

    # Test bounded repair (e.g. truncated/bad JSON)
    bad_json = (
        '{"schema_version": "1", "existing_patterns": ["pat1"], '
        '"relevant_files": [], "constraints": [], "anti_patterns": []'
    )
    res_repaired = enforce_output(bad_json, ContextReport)
    assert res_repaired.existing_patterns == ["pat1"]

    # Test repair fails on type-mismatched garbage that violates schema
    garbage_json = '{"schema_version": "1", "existing_patterns": 123'
    with pytest.raises(ContractValidationError):
        enforce_output(garbage_json, ContextReport)

def test_io_safe_write_and_version_rejection(tmp_path: Path) -> None:
    report = ContextReport(
        schema_version="1",
        existing_patterns=["pattern1"],
        relevant_files=["f1.py"],
        constraints=["c1"],
        anti_patterns=["a1"]
    )
    
    # Save contract
    target_file = tmp_path / "context_report.json"
    save_contract(target_file, report)
    assert target_file.exists()

    # Load contract
    loaded = load_contract(target_file, ContextReport)
    assert loaded.schema_version == "1"
    assert loaded.existing_patterns == ["pattern1"]

    # Unsupported Schema Version rejection
    invalid_version_file = tmp_path / "invalid_version.json"
    invalid_version_file.write_text(
        '{"schema_version": "2", "existing_patterns": [], '
        '"relevant_files": [], "constraints": [], "anti_patterns": []}',
        encoding="utf-8",
    )

    with pytest.raises(UnsupportedSchemaVersionError):
        load_contract(invalid_version_file, ContextReport)

    # Malformed file (no version) is loaded using default version "1" unless it fails validation
    no_version_file = tmp_path / "no_version.json"
    no_version_file.write_text(
        '{"existing_patterns": ["p1"], "relevant_files": [], '
        '"constraints": [], "anti_patterns": []}',
        encoding="utf-8",
    )
    loaded_no_ver = load_contract(no_version_file, ContextReport)
    assert loaded_no_ver.schema_version == "1"  # Default field value

def test_diff_validator() -> None:
    # Valid unified diff
    valid_diff = """
--- a/saltcode/contracts/tasks.py
+++ b/saltcode/contracts/tasks.py
@@ -1,3 +1,4 @@
 import os
+import sys
-import time
"""
    ok, cleaned = validate_diff(valid_diff)
    assert ok
    assert cleaned is not None
    assert "--- a/saltcode/contracts/tasks.py" in cleaned

    # Markdown fenced diff
    fenced_diff = f"""
Sure! Here is the unified diff to apply:
```diff
{valid_diff}
```
And that's it!
"""
    ok_fence, cleaned_fence = validate_diff(fenced_diff)
    assert ok_fence
    assert cleaned_fence is not None
    assert cleaned_fence.startswith("--- a/saltcode/contracts/tasks.py")

    # Invalid diff (prose/garbage)
    invalid_diff = "This is not a diff at all. I am conversational text."
    ok_bad, cleaned_bad = validate_diff(invalid_diff)
    assert not ok_bad
    assert cleaned_bad is None

    # Invalid diff inside a hunk (conversational text inside hunk)
    bad_hunk_diff = """
--- a/file.py
+++ b/file.py
@@ -1,3 +1,4 @@
 context line
+added line
Some random conversation here that shouldn't be in a diff hunk.
"""
    ok_hunk, cleaned_hunk = validate_diff(bad_hunk_diff)
    assert not ok_hunk
    assert cleaned_hunk is None
