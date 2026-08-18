from typing import Literal

from pydantic import BaseModel, Field, model_validator


class StabilityInfo(BaseModel):
    n_passes: int = Field(..., ge=1)
    verdicts: list[str] = Field(default_factory=list)
    stability_score: float = Field(..., ge=0.0, le=1.0)
    # 1-based, per REQ-CON-006 AC4 (added 2026-08-02, G-020). The old `ge=0` admitted both
    # a 0-based and a 1-based reading, and nothing cross-checked the field — unlike
    # `stability_score`, whose formula is re-derived below.
    gac: int = Field(..., ge=1)

    @model_validator(mode="after")
    def validate_stability(self) -> "StabilityInfo":
        if len(self.verdicts) != self.n_passes:
            raise ValueError(
                f"verdicts length ({len(self.verdicts)}) must equal n_passes ({self.n_passes})"
            )

        if self.gac > self.n_passes:
            # REQ-CON-006 AC4 bounds it to [1, n_passes]: a run cannot settle on a pass it
            # never made, and a never-settling run reports its final pass.
            raise ValueError(f"gac ({self.gac}) cannot exceed n_passes ({self.n_passes})")
        
        if self.n_passes <= 1:
            expected_score = 1.0
        else:
            verdict_changes = sum(
                1 for i in range(len(self.verdicts) - 1)
                if self.verdicts[i] != self.verdicts[i + 1]
            )
            expected_score = 1.0 - (verdict_changes / (self.n_passes - 1))
        
        if abs(self.stability_score - expected_score) > 1e-9:
            raise ValueError(
                f"stability_score ({self.stability_score}) does not match "
                f"calculated value ({expected_score})"
            )
            
        return self

class AuditResult(BaseModel):
    schema_version: str = Field(default="1")
    task_id: str
    status: Literal["pass", "fail"]
    reason: Literal["pass", "impl_fail", "gaming_suspected", "spec_defect"]
    next_action: Literal["next_task", "builder_retry", "test_intent_respec", "flag_human"]
    detail: str
    stability: StabilityInfo

    @model_validator(mode="after")
    def validate_audit_verdict(self) -> "AuditResult":
        if (self.status == "pass") != (self.reason == "pass"):
            raise ValueError("status can be 'pass' if and only if reason is also 'pass'")
        
        if self.reason == "spec_defect" and self.next_action != "test_intent_respec":
            raise ValueError("next_action must be 'test_intent_respec' when reason is 'spec_defect'")
            
        return self
