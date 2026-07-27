import re
from pathlib import Path


def extract_hard_constraints_from_markdown(content: str) -> set[str]:
    """Extracts verbatim constraints from the '## HARD CONSTRAINTS' markdown H2 block."""
    lines = content.splitlines()
    constraints: set[str] = set()
    in_constraints_section = False
    
    header_pattern = re.compile(r"^##\s+HARD\s+CONSTRAINTS\s*$", re.IGNORECASE)
    
    for line in lines:
        stripped = line.strip()
        if header_pattern.match(stripped):
            in_constraints_section = True
            continue
        
        if in_constraints_section:
            # Stop if we hit any other header (e.g. ##, ###, #)
            if stripped.startswith("#"):
                break
            
            # Match bullet points starting with - or *
            if stripped.startswith(("-", "*")):
                item = stripped[1:].strip()
                if item:
                    constraints.add(item)
                    
    if not in_constraints_section:
        raise ValueError("Required '## HARD CONSTRAINTS' section not found in design document.")
        
    return constraints

def extract_hard_constraints(filepath: Path | str) -> set[str]:
    """Reads a design document file and extracts its verbatim constraints."""
    try:
        content = Path(filepath).read_text(encoding="utf-8")
    except Exception as e:
        raise ValueError(f"Could not read design doc file {filepath}: {e}") from e
    return extract_hard_constraints_from_markdown(content)
