from typing import Literal

from pydantic import BaseModel, Field


class Gap(BaseModel):
    id: str
    type: Literal["design_gap", "plan_gap", "constraint_violation"]
    detail: str
    target: Literal["architect", "planner"]

class EvaluatorReport(BaseModel):
    schema_version: str = Field(default="1")
    status: Literal["pass", "gaps"]
    gaps: list[Gap] = []
    routing_summary: str
