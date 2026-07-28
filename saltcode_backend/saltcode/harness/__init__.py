from saltcode.harness.audit_log import append_entry, audit_log_path, read_entries
from saltcode.harness.budget import (
    BudgetError,
    BudgetExceededError,
    BudgetTracker,
    Phase1FireLimitExceededError,
    SpecDefectLimitExceededError,
)
from saltcode.harness.command_allowlist import (
    DEFAULT_ALLOWLIST,
    AllowlistDecision,
    CommandRefusedError,
    check_command,
)
from saltcode.harness.connectivity import check_connectivity
from saltcode.harness.ctx_compactor import compact_builder_context
from saltcode.harness.dag import DAG, DAGExecutor, DAGNode, LoopCapExceededError
from saltcode.harness.phase_gate import check_and_advance_phase, compute_spec_hash, is_spec_locked, unlock_spec
from saltcode.harness.router import get_agent_tier, should_escalate_auditor
from saltcode.harness.sandbox import (
    ContainedResult,
    ContainerLimits,
    DiffApplicationError,
    NoContainmentBackendError,
    SandboxError,
    apply_diff,
    detect_containment_backend,
    disposable_sandbox,
    require_containment_backend,
    run_in_container,
)
from saltcode.harness.scope_probe import run_scope_probe
from saltcode.harness.thinking_gate import get_thinking_mode
from saltcode.harness.write_allowlist import validate_agent_write_path, validate_builder_diff_paths

__all__ = [
    "check_connectivity",
    "DAG",
    "DAGNode",
    "DAGExecutor",
    "LoopCapExceededError",
    "BudgetTracker",
    "BudgetError",
    "BudgetExceededError",
    "SpecDefectLimitExceededError",
    "Phase1FireLimitExceededError",
    "get_thinking_mode",
    "validate_agent_write_path",
    "validate_builder_diff_paths",
    "compact_builder_context",
    "check_and_advance_phase",
    "is_spec_locked",
    "unlock_spec",
    "compute_spec_hash",
    "get_agent_tier",
    "should_escalate_auditor",
    "disposable_sandbox",
    "apply_diff",
    "run_in_container",
    "detect_containment_backend",
    "require_containment_backend",
    "ContainerLimits",
    "ContainedResult",
    "SandboxError",
    "DiffApplicationError",
    "NoContainmentBackendError",
    "check_command",
    "AllowlistDecision",
    "CommandRefusedError",
    "DEFAULT_ALLOWLIST",
    "append_entry",
    "audit_log_path",
    "read_entries",
    "run_scope_probe",
]
