import contextlib
import json
import tempfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class ContractIOError(Exception):
    """Base exception for contract reader/writer failures."""

class UnsupportedSchemaVersionError(ContractIOError):
    """Exception raised when the contract has an unsupported major schema version."""

class ContractLoadError(ContractIOError):
    """Exception raised when loading/validating a contract fails."""


def load_contract(filepath: Path | str, model_class: type[T]) -> T:
    """Loads a JSON contract from disk, verifying its major schema version and schema validation.

    Raises UnsupportedSchemaVersionError or ContractLoadError on failure.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Contract file not found: {path}")
        
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as e:
        raise ContractLoadError(f"Failed to read or parse JSON from {path}: {e}") from e

    # Extract default schema_version from model_class field if it exists
    expected_version = "1"
    if "schema_version" in model_class.model_fields:
        field_info = model_class.model_fields["schema_version"]
        if field_info.default is not None:
            expected_version = str(field_info.default)

    loaded_version = str(data.get("schema_version", "1"))
    
    # Compare major versions
    major_loaded = loaded_version.split(".")[0]
    major_expected = expected_version.split(".")[0]
    
    if major_loaded != major_expected:
        raise UnsupportedSchemaVersionError(
            f"Unsupported major schema version '{major_loaded}' (expected '{major_expected}') in contract {path}."
        )

    try:
        return model_class.model_validate(data)
    except ValidationError as e:
        raise ContractLoadError(f"Contract validation failed for {model_class.__name__} in {path}: {e}") from e


def save_contract(filepath: Path | str, model: BaseModel) -> None:
    """Saves a Pydantic contract model atomically to disk by writing to a temp file and renaming it.

    Ensures parent directories are created.
    """
    path = Path(filepath)
    try:
        # Validate model (redundant but safe)
        model.model_validate(model.model_dump())
        json_data = model.model_dump_json(indent=2)
    except Exception as e:
        raise ContractIOError(f"Failed to validate contract model before saving: {e}") from e

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        dir_name = path.parent
        
        # Write to temporary file in the same directory for atomic rename
        with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as f:
            f.write(json_data)
            temp_name = f.name
            
        try:
            # Atomic rename (replacement)
            Path(temp_name).replace(path)
        except Exception as e:
            with contextlib.suppress(Exception):
                Path(temp_name).unlink()
            raise e
            
    except Exception as e:
        raise ContractIOError(f"Failed to atomically write contract to {path}: {e}") from e
