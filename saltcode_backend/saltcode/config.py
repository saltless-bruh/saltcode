import logging
import os
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    # DeepSeek configuration
    deepseek_api_key: str | None = Field(default=None)
    deepseek_base_url: str = Field(default="https://api.deepseek.com")
    deepseek_chat_model: str = Field(default="deepseek-chat")
    deepseek_reasoning_model: str = Field(default="deepseek-reasoner")

    # Local serving configuration
    saltnitor_url: str = Field(default="http://127.0.0.1:8765")
    llamacpp_fallback_url: str = Field(default="http://127.0.0.1:8080")
    local_default_model: str = Field(default="A_STD")
    # DD-8: retrieval must work offline, so the embedding model is a local one
    # (bge-small / nomic-embed) served behind the OpenAI-compatible endpoint.
    embedding_model: str = Field(default="bge-small")

    # Thresholds & Calibration
    auditor_stability_threshold: float = Field(default=0.5)
    semantic_cosine_threshold: float = Field(default=0.85)
    pcd_low_density_bar: float = Field(default=0.2)
    pcd_high_density_bar: float = Field(default=0.8)
    calibrated: bool = Field(default=False)

    # Security containment (REQ-SEC-001 defaults, REQ-SEC-005 AC2 override)
    sandbox_backend: str | None = Field(default=None)  # None → autodetect bwrap → docker → firejail
    sandbox_image: str | None = Field(default=None)  # Docker only; no default is invented
    sandbox_memory_mb: int = Field(default=2048)
    sandbox_cpus: float = Field(default=2.0)
    sandbox_timeout_seconds: float = Field(default=60.0)

    # Flags
    online_mode: bool = Field(default=True)
    builder_mtp_enabled: bool = Field(default=False)

# Load settings from environment variables
settings = Settings(
    deepseek_api_key=os.getenv("DEEPSEEK_API_KEY"),
    deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    deepseek_chat_model=os.getenv("DEEPSEEK_CHAT_MODEL", "deepseek-chat"),
    deepseek_reasoning_model=os.getenv("DEEPSEEK_REASONING_MODEL", "deepseek-reasoner"),
    saltnitor_url=os.getenv("SALTNITOR_URL", "http://127.0.0.1:8765"),
    llamacpp_fallback_url=os.getenv("LLAMACPP_FALLBACK_URL", "http://127.0.0.1:8080"),
    local_default_model=os.getenv("LOCAL_DEFAULT_MODEL", "A_STD"),
    embedding_model=os.getenv("SALTCODE_EMBEDDING_MODEL", "bge-small"),
    sandbox_backend=os.getenv("SALTCODE_SANDBOX_BACKEND") or None,
    sandbox_image=os.getenv("SALTCODE_SANDBOX_IMAGE") or None,
    sandbox_memory_mb=int(os.getenv("SALTCODE_SANDBOX_MEMORY_MB", "2048")),
    sandbox_cpus=float(os.getenv("SALTCODE_SANDBOX_CPUS", "2.0")),
    sandbox_timeout_seconds=float(os.getenv("SALTCODE_SANDBOX_TIMEOUT", "60.0")),
    online_mode=os.getenv("SALTCODE_OFFLINE", "0") != "1",
)

logger = logging.getLogger(__name__)

def check_calibration(workspace_path: Path | str | None = None) -> None:
    """Log a warning at session open when any threshold is still uncalibrated.

    REQ-CAL-001 AC1/AC2. With a workspace, the per-threshold resolution in
    `saltcode.thresholds` decides — calibration arrives one threshold at a time
    (Task 14b calibrates the Auditor bar from labelled diffs and the semantic bars
    from labelled goal pairs, independently), so a single project-wide flag would
    have to misreport one of them. Without a workspace this falls back to the
    process-wide `settings.calibrated`, which is what the pre-Task-5 callers used.
    """
    if workspace_path is not None:
        from saltcode.thresholds import load_thresholds, warn_if_uncalibrated

        warn_if_uncalibrated(load_thresholds(workspace_path))
        return

    if not settings.calibrated:
        logger.warning(
            "Thresholds (stability, cosine, PCD bars) are UNCALIBRATED. "
            "Using conservative defaults (stability=0.5, cosine=0.85, PCD bars=[0.2, 0.8])."
        )

