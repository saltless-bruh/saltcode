import sys
from pathlib import Path

import pytest

from saltcode.contracts.evaluator_report import EvaluatorReport
from saltcode.contracts.tasks import Task, TasksFile
from saltcode.harness import (
    DAG,
    BudgetExceededError,
    BudgetTracker,
    DAGExecutor,
    DAGNode,
    LoopCapExceededError,
    Phase1FireLimitExceededError,
    SpecDefectLimitExceededError,
    apply_diff,
    check_and_advance_phase,
    compact_builder_context,
    compute_spec_hash,
    disposable_sandbox,
    get_agent_tier,
    get_thinking_mode,
    is_spec_locked,
    run_command_in_sandbox,
    run_scope_probe,
    should_escalate_auditor,
    unlock_spec,
    validate_agent_write_path,
    validate_builder_diff_paths,
)


# ==========================================
# 1. Test DAG and DAGExecutor
# ==========================================
def test_dag_topo_sort_and_loop_cap() -> None:
    # Construct DAG
    dag = DAG()
    
    called_nodes = []
    
    def make_func(name: str):
        def func() -> str:
            called_nodes.append(name)
            return name
        return func
        
    node_a = DAGNode("node_a", make_func("node_a"), loop_cap=2)
    node_b = DAGNode("node_b", make_func("node_b"))
    node_c = DAGNode("node_c", make_func("node_c"))
    
    dag.add_node(node_a)
    dag.add_node(node_b)
    dag.add_node(node_c)
    
    dag.add_edge("node_a", "node_b")
    dag.add_edge("node_b", "node_c")
    
    # Check topological order
    assert dag.get_topological_order() == ["node_a", "node_b", "node_c"]
    
    # Assert cycle detection
    with pytest.raises(ValueError, match="create a cycle"):
        dag.add_edge("node_c", "node_a")
        
    # Execute node_a twice
    executor = DAGExecutor(dag)
    assert executor.run_node("node_a") == "node_a"
    assert executor.run_node("node_a") == "node_a"
    
    # Exceed loop cap
    with pytest.raises(LoopCapExceededError):
        executor.run_node("node_a")


# ==========================================
# 2. Test Budget Tracker
# ==========================================
def test_budget_tracker(tmp_path: Path) -> None:
    tracker = BudgetTracker(tmp_path)
    
    # Initial state
    assert tracker.get_active_tier("T1", "low") == "A"
    assert tracker.get_active_tier("T1", "high") == "B"
    
    # Failure on Tier A
    tracker.increment_task_failure("T1", "A")
    assert tracker.get_active_tier("T1", "low") == "A"
    
    # Second failure on Tier A
    tracker.increment_task_failure("T1", "A")
    # Sub-cap of 2 failures on Tier A reached -> escalates to Tier B on 3rd attempt
    assert tracker.get_active_tier("T1", "low") == "B"
    
    # Third failure (on Tier B)
    tracker.increment_task_failure("T1", "B")
    
    # Budget of 3 exceeded -> raises BudgetExceededError on next call
    with pytest.raises(BudgetExceededError):
        tracker.get_active_tier("T1", "low")
        
    # Test spec_defect limit: max 1 spec defect allowed per task
    tracker.increment_spec_defect("T2")
    assert tracker.get_spec_defect_count("T2") == 1
    
    with pytest.raises(SpecDefectLimitExceededError):
        tracker.increment_spec_defect("T2")

    # Test phase 1 fire limit (max 1 fire per sprint)
    tracker.mark_phase_1_fired()
    with pytest.raises(Phase1FireLimitExceededError):
        tracker.mark_phase_1_fired()

    # Test agent loop caps
    tracker.increment_architect_loop()
    tracker.increment_architect_loop()
    with pytest.raises(LoopCapExceededError):
        tracker.increment_architect_loop()

    tracker.increment_planner_loop()
    tracker.increment_planner_loop()
    tracker.increment_planner_loop()
    with pytest.raises(LoopCapExceededError):
        tracker.increment_planner_loop()


# ==========================================
# 3. Test Thinking Gate Policy
# ==========================================
def test_thinking_gate_policy() -> None:
    assert get_thinking_mode("Architect") is True
    assert get_thinking_mode("Planner") is False
    assert get_thinking_mode("Scout") is False
    assert get_thinking_mode("Builder") is False
    
    # Evaluator
    assert get_thinking_mode("Evaluator", is_reinvoked=False) is False
    assert get_thinking_mode("Evaluator", is_reinvoked=True) is True
    
    # Auditor
    assert get_thinking_mode("Auditor", is_retry=False, is_gaming_suspected=False) is False
    assert get_thinking_mode("Auditor", is_retry=True) is True
    assert get_thinking_mode("Auditor", is_gaming_suspected=True) is True


# ==========================================
# 4. Test Write Allowlist
# ==========================================
def test_write_allowlist_agents_and_diff(tmp_path: Path) -> None:
    # Agent path writes
    assert validate_agent_write_path("Scout", tmp_path / ".saltcode" / "context_report.json", tmp_path) is True
    assert validate_agent_write_path("Scout", tmp_path / "context_report.json", tmp_path) is False
    
    assert validate_agent_write_path("Architect", tmp_path / ".saltcode" / "design.md", tmp_path) is True
    assert validate_agent_write_path("Planner", tmp_path / ".saltcode" / "tasks.json", tmp_path) is True
    assert validate_agent_write_path("Evaluator", tmp_path / ".saltcode" / "evaluator_report.json", tmp_path) is True
    assert validate_agent_write_path("Auditor", tmp_path / ".saltcode" / "audit_result.json", tmp_path) is True
    
    assert validate_agent_write_path(
        "Test-Intent", tmp_path / ".saltcode" / "tests" / "task_T1_spec.py", tmp_path
    ) is True
    assert validate_agent_write_path(
        "Test-Intent", tmp_path / ".saltcode" / "tests" / "some_other.py", tmp_path
    ) is False

    
    assert validate_agent_write_path("Builder", tmp_path / "src" / "main.py", tmp_path) is True
    assert validate_agent_write_path("Builder", tmp_path / "tests" / "test_main.py", tmp_path) is False
    assert validate_agent_write_path("Builder", tmp_path / ".saltcode" / "tests" / "task_T1_spec.py", tmp_path) is False

    # Diff validation rejecting tests edits
    valid_diff = """
--- a/saltcode/harness/dag.py
+++ b/saltcode/harness/dag.py
@@ -1,3 +1,4 @@
 import os
+import sys
"""
    assert validate_builder_diff_paths(valid_diff) is True

    invalid_diff_1 = """
--- a/tests/test_main.py
+++ b/tests/test_main.py
@@ -1,3 +1,4 @@
"""
    assert validate_builder_diff_paths(invalid_diff_1) is False

    invalid_diff_2 = """
--- a/src/main.py
+++ b/src/main.py
--- a/.saltcode/tests/task_T1_spec.py
+++ b/.saltcode/tests/task_T1_spec.py
"""
    assert validate_builder_diff_paths(invalid_diff_2) is False


# ==========================================
# 5. Test Context Compactor
# ==========================================
def test_context_compactor() -> None:
    task = Task(
        id="T1",
        description="implement feature",
        files_affected=["src/feature.py"],
        acceptance_criteria=[],
        depends_on=[],
        complexity="low"
    )
    
    files_content = {"src/feature.py": "def run(): pass"}
    ast_slices = {"symbols": ["run"]}
    spec_content = "def test_run(): pass"
    
    # Valid compaction
    ctx = compact_builder_context(task, files_content, ast_slices, spec_content)
    assert ctx["task"]["id"] == "T1"
    assert ctx["files_affected"] == files_content
    assert ctx["spec"] == spec_content
    
    # Extraneous files error
    bad_files_content = {"src/feature.py": "pass", "src/other.py": "pass"}
    with pytest.raises(ValueError, match="Extraneous file"):
        compact_builder_context(task, bad_files_content, ast_slices, spec_content)


# ==========================================
# 6. Test Phase Gate advancing
# ==========================================
def test_phase_gate(tmp_path: Path) -> None:
    eval_rep_fail = EvaluatorReport(status="gaps", gaps=[], routing_summary="some gaps")
    eval_rep_pass = EvaluatorReport(status="pass", gaps=[], routing_summary="looks good")
    
    tasks_file = TasksFile(schema_version="1", tasks=[])
    goal = "Implement auth module"
    scope = ["src/auth.py"]
    
    # Fail doesn't advance
    assert check_and_advance_phase(eval_rep_fail, tasks_file, goal, scope, tmp_path) is False
    assert not is_spec_locked(tmp_path)
    
    # Pass advances and locks
    assert check_and_advance_phase(eval_rep_pass, tasks_file, goal, scope, tmp_path) is True
    assert is_spec_locked(tmp_path)
    
    # Spec hash match
    expected_hash = compute_spec_hash(goal, scope)
    assert (tmp_path / ".saltcode" / ".spec_hash").read_text() == expected_hash
    
    # Unlock spec works
    unlock_spec(tmp_path)
    assert not is_spec_locked(tmp_path)


# ==========================================
# 7. Test Agent Tier Router
# ==========================================
def test_agent_tier_router() -> None:
    # Online project tiers
    assert get_agent_tier("Scout", is_online=True, is_fresh_project=True) == "deepseek-chat"
    assert get_agent_tier("Planner", is_online=True, is_fresh_project=False) == "deepseek-chat"
    assert get_agent_tier("Architect", is_online=True, is_fresh_project=True) == "deepseek-reasoner"
    assert get_agent_tier("Architect", is_online=True, is_fresh_project=False) == "deepseek-chat"
    assert get_agent_tier("Evaluator", is_online=True, is_fresh_project=True) == "deepseek-reasoner"
    assert get_agent_tier("Evaluator", is_online=True, is_fresh_project=False) == "deepseek-chat"
    
    # Offline planning uses Tier B
    assert get_agent_tier("Architect", is_online=False, is_fresh_project=True) == "B"
    assert get_agent_tier("Evaluator", is_online=False, is_fresh_project=False) == "B"
    assert get_agent_tier("Scout", is_online=False, is_fresh_project=False) == "B"
    
    # Auditor escalation
    assert should_escalate_auditor(0.4, 0.5, is_online=True) is True
    assert should_escalate_auditor(0.6, 0.5, is_online=True) is False
    assert should_escalate_auditor(0.4, 0.5, is_online=False) is False


# ==========================================
# 8. Test Sandbox & Diff Application
# ==========================================
def test_sandbox_copy_and_cmd(tmp_path: Path) -> None:
    # Create mock workspace
    src_file = tmp_path / "main.py"
    src_file.write_text("print('hello')", encoding="utf-8")
    
    # Ensure ignore patterns work by adding ignoreable dirs
    (tmp_path / ".git").mkdir()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / ".git" / "config").write_text("git config", encoding="utf-8")
    
    with disposable_sandbox(tmp_path) as sandbox:
        # Check files copied and ignored
        assert (sandbox / "main.py").exists()
        assert not (sandbox / ".git").exists()
        
        # Test command execution
        res = run_command_in_sandbox(sandbox, [sys.executable, "main.py"])
        assert res.returncode == 0
        assert "hello" in res.stdout
        
        # Test applying diff
        diff = """
--- a/main.py
+++ b/main.py
@@ -1,1 +1,2 @@
 print('hello')
+print('world')
"""
        assert apply_diff(sandbox, diff)
        assert (sandbox / "main.py").read_text(encoding="utf-8") == "print('hello')\nprint('world')\n"
        
    # Check live workspace remained unmodified
    assert src_file.read_text(encoding="utf-8") == "print('hello')"


# ==========================================
# 9. Test Scope Probe
# ==========================================
def test_scope_probe(tmp_path: Path) -> None:
    # Create workspace files
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("pass")
    (tmp_path / "src" / "db.py").write_text("pass")
    (tmp_path / "main.py").write_text("pass")
    
    # Match specific file name
    res1 = run_scope_probe("Implement sign in inside auth.py", tmp_path)
    assert res1 == ["src/auth.py"]
    
    # Match folders/multiple files
    res2 = run_scope_probe("Update code inside src", tmp_path)
    assert res2 == ["src/auth.py", "src/db.py"]
    
    # Match relative path
    res3 = run_scope_probe("Check src/db.py query", tmp_path)
    assert res3 == ["src/db.py"]
    
    # No matches fallback
    res4 = run_scope_probe("Implement hello world feature", tmp_path)
    assert res4 == []
