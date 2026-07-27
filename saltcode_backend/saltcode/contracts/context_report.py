from pydantic import BaseModel, Field


class ContextReport(BaseModel):
    schema_version: str = Field(default="1")
    existing_patterns: list[str] = Field(default_factory=list)
    relevant_files: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)
