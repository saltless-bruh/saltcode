"""Per-threshold configuration with calibration provenance (task 5.6, REQ-CAL-001).

Every configurable threshold in Saltcode — the Auditor's stability bar, the semantic
cache's cosine bar, and the two PCD density bars — is meant to be **measured, then
fixed** (design §11.9), never guessed. Until a measurement exists, the threshold runs on
a conservative default and must *say so*: REQ-CAL-001 AC1 requires a `calibrated: bool`
per threshold, AC2 a warning at session open, and AC3 the calibration artifacts under
`.saltcode/calibration/`.

A single project-wide `calibrated` flag cannot express this. Calibration is per
threshold and arrives per threshold: Task 14b calibrates the Auditor bar from labelled
diffs and the semantic bars from labelled goal pairs, and either can land without the
other. A project with a measured cosine bar and a still-defaulted stability bar is the
normal mid-calibration state, and one flag would have to lie about one of them.

**Resolution order** (first hit wins, per threshold):

1. ``.saltcode/calibration/thresholds.json`` — written by ``saltcode_calibrate``
   (Task 14b). Read-only here.
2. ``saltcode.toml`` ``[thresholds]`` (or ``.saltcode/config.toml``) — the human's
   project config.
3. The conservative defaults below, marked ``calibrated=False``.

A threshold from source 2 counts as calibrated only if the human explicitly wrote
``<name>_calibrated = true``. Writing a number is not a claim to have measured it, and
silently promoting a hand-set value to "calibrated" would defeat the whole protocol.
"""

from __future__ import annotations

import json
import logging
import tomllib
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel

logger = logging.getLogger(__name__)

ThresholdSource = Literal["default", "project_config", "calibration"]

AUDITOR_STABILITY = "auditor_stability_threshold"
SEMANTIC_COSINE = "semantic_cosine_threshold"
PCD_LOW_BAR = "pcd_low_density_bar"
PCD_HIGH_BAR = "pcd_high_density_bar"

THRESHOLD_NAMES = (AUDITOR_STABILITY, SEMANTIC_COSINE, PCD_LOW_BAR, PCD_HIGH_BAR)

DEFAULTS: dict[str, float] = {
    # REQ-AUD-005 AC1: first sprint, no data.
    AUDITOR_STABILITY: 0.5,
    # REQ-CACHE-003 AC5: first sprint, no data.
    SEMANTIC_COSINE: 0.85,
    # The PCD bars are inert while uncalibrated — REQ-CACHE-003 AC5 forces full
    # Architect confirmation regardless of where they sit (see confirmation_for_pcd
    # in memory/semantic_cache.py). They carry usable values so that a calibration
    # run only has to overwrite the numbers, not invent the shape.
    PCD_LOW_BAR: 0.2,
    PCD_HIGH_BAR: 0.8,
}

CALIBRATION_FILE = "thresholds.json"


class Threshold(BaseModel):
    """One threshold, its value, and whether that value was measured."""

    name: str
    value: float
    calibrated: bool = False
    source: ThresholdSource = "default"


class Thresholds(BaseModel):
    """The resolved threshold set for one workspace."""

    auditor_stability_threshold: Threshold
    semantic_cosine_threshold: Threshold
    pcd_low_density_bar: Threshold
    pcd_high_density_bar: Threshold
    # REQ-CACHE-003 AC3 admits two readings for a low-PCD candidate — "require full
    # confirmation **or** fall through to Phase 1". `full` is the default because it
    # strictly dominates: REQ-CACHE-001 AC2 already falls through when the Architect
    # says no, so full confirmation can only end in confirmed reuse or that same
    # fall-through, at the cost of one cheap call. Configurable for the operator who
    # wants maximum skepticism. (Maintainer decision, 2026-07-29.)
    pcd_low_action: Literal["full", "fall_through"] = "full"
    # REQ-CACHE-003 AC2 says confirmation MAY be skipped at very high density "if
    # config allows" — so it is off unless the operator turns it on.
    pcd_allow_skip: bool = False

    def all_thresholds(self) -> list[Threshold]:
        return [getattr(self, name) for name in THRESHOLD_NAMES]

    def uncalibrated(self) -> list[Threshold]:
        """The thresholds still running on unmeasured values."""
        return [t for t in self.all_thresholds() if not t.calibrated]

    @property
    def fully_calibrated(self) -> bool:
        return not self.uncalibrated()

    def summary(self) -> dict[str, Any]:
        """A JSON-safe report for the extension to surface (REQ-CAL-001 AC2)."""
        return {
            "fully_calibrated": self.fully_calibrated,
            "uncalibrated": [t.name for t in self.uncalibrated()],
            "values": {
                t.name: {"value": t.value, "calibrated": t.calibrated, "source": t.source}
                for t in self.all_thresholds()
            },
            "pcd_low_action": self.pcd_low_action,
            "pcd_allow_skip": self.pcd_allow_skip,
        }


def calibration_dir(workspace_path: Path | str) -> Path:
    """Where `saltcode_calibrate` stores its artifacts (REQ-CAL-001 AC3)."""
    return Path(workspace_path) / ".saltcode" / "calibration"


def _read_toml_thresholds(workspace_path: Path | str) -> dict[str, Any]:
    """The ``[thresholds]`` table from project config, or empty when there is none.

    Mirrors the config discovery in `mcp/lsp_backends.py::load_project_config`:
    `saltcode.toml` at the workspace root, else `.saltcode/config.toml`.
    """
    workspace = Path(workspace_path)
    for candidate in (workspace / "saltcode.toml", workspace / ".saltcode" / "config.toml"):
        if not candidate.exists():
            continue
        try:
            with candidate.open("rb") as handle:
                data: dict[str, Any] = dict(tomllib.load(handle))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            logger.warning("Ignoring unreadable project config %s: %s", candidate, exc)
            return {}
        section = data.get("thresholds")
        return cast("dict[str, Any]", section) if isinstance(section, dict) else {}
    return {}


def _read_calibration(workspace_path: Path | str) -> dict[str, Any]:
    """The calibration artifact written by Task 14b, or empty when absent."""
    path = calibration_dir(workspace_path) / CALIBRATION_FILE
    if not path.exists():
        return {}
    try:
        loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable calibration artifact %s: %s", path, exc)
        return {}
    if not isinstance(loaded, dict):
        return {}
    typed = cast("dict[str, Any]", loaded)
    # Accept both {"thresholds": {...}} and a flat mapping, so Task 14b can add
    # provenance fields (set, date, distribution) beside the values without
    # breaking this reader.
    section = typed.get("thresholds")
    return cast("dict[str, Any]", section) if isinstance(section, dict) else typed


def _as_float(raw: Any) -> float | None:
    """A threshold value must be a real number; anything else is ignored, loudly."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def _resolve_one(name: str, calibration: dict[str, Any], project: dict[str, Any]) -> Threshold:
    calibrated_value = _as_float(calibration.get(name))
    if calibrated_value is not None:
        return Threshold(name=name, value=calibrated_value, calibrated=True, source="calibration")

    configured = _as_float(project.get(name))
    if configured is not None:
        # Only an explicit flag promotes a hand-set number to "measured".
        claimed = project.get(f"{name}_calibrated")
        return Threshold(
            name=name,
            value=configured,
            calibrated=claimed is True,
            source="project_config",
        )

    return Threshold(name=name, value=DEFAULTS[name], calibrated=False, source="default")


def load_thresholds(workspace_path: Path | str) -> Thresholds:
    """Resolve every threshold for a workspace, recording where each value came from."""
    calibration = _read_calibration(workspace_path)
    project = _read_toml_thresholds(workspace_path)

    resolved = {name: _resolve_one(name, calibration, project) for name in THRESHOLD_NAMES}

    low_action_raw = project.get("pcd_low_action")
    low_action: Literal["full", "fall_through"] = (
        "fall_through" if low_action_raw == "fall_through" else "full"
    )

    return Thresholds(
        auditor_stability_threshold=resolved[AUDITOR_STABILITY],
        semantic_cosine_threshold=resolved[SEMANTIC_COSINE],
        pcd_low_density_bar=resolved[PCD_LOW_BAR],
        pcd_high_density_bar=resolved[PCD_HIGH_BAR],
        pcd_low_action=low_action,
        pcd_allow_skip=project.get("pcd_allow_skip") is True,
    )


def warn_if_uncalibrated(thresholds: Thresholds) -> str | None:
    """Log and return the session-open warning, or ``None`` when all are measured.

    REQ-CAL-001 AC2. The message names *which* thresholds are provisional and what
    they currently sit at — a warning that only says "some thresholds are
    uncalibrated" tells the operator nothing they can act on.
    """
    pending = thresholds.uncalibrated()
    if not pending:
        return None

    detail = ", ".join(f"{t.name}={t.value}" for t in pending)
    message = (
        f"UNCALIBRATED thresholds in use ({len(pending)} of {len(THRESHOLD_NAMES)}): {detail}. "
        "These are conservative defaults, not measurements — run `saltcode_calibrate` "
        "(REQ-CAL-001) to replace them. While the semantic bars are uncalibrated, every "
        "semantic cache candidate requires full Architect confirmation (REQ-CACHE-003 AC5)."
    )
    logger.warning(message)
    return message
