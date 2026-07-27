import json
import re
from typing import TypeVar

import json_repair
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

class ContractEnforcementError(Exception):
    """Base exception for contract enforcement failures."""

class OutputLengthExceededError(ContractEnforcementError):
    """Exception raised when the model output length exceeds the limit."""

class NonJSONOutputError(ContractEnforcementError):
    """Exception raised when the model output contains no JSON structure in JSON-only mode."""

class ContractValidationError(ContractEnforcementError):
    """Exception raised when JSON fails schema validation."""


def extract_json_string(output: str) -> str:
    """Extracts JSON body from markdown code blocks or returns the cleaned string."""
    cleaned = output.strip()
    
    # Try finding markdown code blocks: ```json ... ``` or ``` ... ```
    pattern = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
    match = pattern.search(cleaned)
    if match:
        return match.group(1).strip()
        
    return cleaned


def extract_json_substring(s: str) -> str:
    """Extracts the first outermost JSON object or array substring from a string."""
    start_idx = -1
    for i, c in enumerate(s):
        if c in ("{", "["):
            start_idx = i
            break
    if start_idx == -1:
        return s.strip()
        
    end_idx = -1
    for i in range(len(s) - 1, start_idx, -1):
        if s[i] in ("}", "]"):
            end_idx = i
            break
            
    if end_idx == -1:
        return s[start_idx:].strip()
        
    return s[start_idx : end_idx + 1].strip()


def enforce_output(
    output: str,
    model_class: type[T],
    max_length: int | None = None,
    json_only: bool = True
) -> T:
    """Validates the model output against a schema, applying one bounded repair on failure.

    Raises ContractEnforcementError subclasses if validation fails.
    """
    if max_length is not None and len(output) > max_length:
        raise OutputLengthExceededError(
            f"Model output length ({len(output)}) exceeds configured maximum length ({max_length})."
        )
        
    extracted = extract_json_string(output)
    
    if json_only:
        if "{" not in extracted and "[" not in extracted:
            raise NonJSONOutputError("Model output contains no JSON structure (no '{' or '[' found).")
        if not (extracted.startswith("{") or extracted.startswith("[")):
            extracted = extract_json_substring(extracted)
            if "{" not in extracted and "[" not in extracted:
                raise NonJSONOutputError("Model output contains no JSON structure after extraction.")

    # Try standard validation first
    try:
        return model_class.model_validate_json(extracted)
    except (ValidationError, json.JSONDecodeError, ValueError) as e:
        # Bounded repair: attempt one repair using json_repair
        try:
            repaired = json_repair.repair_json(extracted)
            return model_class.model_validate_json(repaired)
        except Exception:
            raise ContractValidationError(
                f"Failed to validate contract against {model_class.__name__} even after repair. "
                f"Validation error: {e}"
            ) from e
