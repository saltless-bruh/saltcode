import contextlib
import json
import tempfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticUndefined

T = TypeVar("T", bound=BaseModel)


class ContractIOError(Exception):
    """Base exception for contract reader/writer failures."""

class UnsupportedSchemaVersionError(ContractIOError):
    """Exception raised when the contract has an unsupported major schema version."""

class ContractLoadError(ContractIOError):
    """Exception raised when loading/validating a contract fails."""


def expected_major_version(model_class: type[BaseModel]) -> str:
    """The major `schema_version` this build accepts for `model_class`.

    The single source of truth for REQ-GLB-005's "readers reject unknown major
    versions" rule, so the on-disk contract reader and the cache readers cannot
    drift apart.

    Reading the model's `Field` default is deliberately guarded: `model_fields[...]
    .default` is `PydanticUndefined` — **not** `None` — when a field has no default,
    and `PydanticUndefined` is *truthy*, so a naive `default or "1"` yields the
    string `"PydanticUndefined"` and every stored record is rejected as unsupported.
    """
    field = model_class.model_fields.get("schema_version")
    if field is None:
        return "1"

    default = field.default
    if default is None or default is PydanticUndefined or not isinstance(default, str):
        return "1"
    return default.split(".")[0]


def major_of(version: object) -> str:
    """The major component of a `schema_version` value read from disk."""
    return str(version if version is not None else "1").split(".")[0]


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

    major_expected = expected_major_version(model_class)
    major_loaded = major_of(data.get("schema_version", "1"))

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
