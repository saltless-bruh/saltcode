import re


def extract_diff_from_fences(output: str) -> str:
    """Extracts a unified diff from markdown code fences if present."""
    cleaned = output.strip()
    
    # Try finding markdown code blocks: ```diff, ```patch, or just ```
    pattern = re.compile(r"```(?:diff|patch)?\s*\n(.*?)\n\s*```", re.DOTALL | re.IGNORECASE)
    match = pattern.search(cleaned)
    if match:
        return match.group(1).strip()
    return cleaned

def validate_diff(output: str) -> tuple[bool, str | None]:
    """Validates if the output is a parseable unified diff.

    Returns (True, clean_diff_content) if valid, or (False, None) if invalid.
    """
    diff_text = extract_diff_from_fences(output)
    lines = diff_text.splitlines()
    
    has_header_minus = False
    has_header_plus = False
    has_hunk = False
    in_hunk = False
    
    hunk_pattern = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@")
    
    for line in lines:
        if line.startswith("--- "):
            has_header_minus = True
            in_hunk = False
        elif line.startswith("+++ "):
            has_header_plus = True
            in_hunk = False
        elif hunk_pattern.match(line):
            has_hunk = True
            in_hunk = True
        elif in_hunk:
            if not line:
                continue
            # Context line, addition, deletion, or diff metadata (e.g. \ No newline at end of file)
            if not line.startswith(("+", "-", " ", "\\")):
                return False, None
                
    if has_header_minus and has_header_plus and has_hunk:
        return True, diff_text
        
    return False, None
