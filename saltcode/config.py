import os

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

    # Thresholds & Calibration
    auditor_stability_threshold: float = Field(default=0.5)
    semantic_cosine_threshold: float = Field(default=0.85)
    pcd_low_density_bar: float = Field(default=0.2)
    pcd_high_density_bar: float = Field(default=0.8)
    calibrated: bool = Field(default=False)

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
    online_mode=os.getenv("SALTCODE_OFFLINE", "0") != "1",
)

import logging

logger = logging.getLogger(__name__)

def check_calibration() -> None:
    """Logs a warning at session open if the thresholds are uncalibrated."""
    if not settings.calibrated:
        logger.warning(
            "Thresholds (stability, cosine, PCD bars) are UNCALIBRATED. "
            "Using conservative defaults (stability=0.5, cosine=0.85, PCD bars=[0.2, 0.8])."
        )

