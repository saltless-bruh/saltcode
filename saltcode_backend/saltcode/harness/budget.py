import json
from pathlib import Path
from typing import Any, Literal


class BudgetError(Exception):
    """Base exception for budget tracker errors."""


class BudgetExceededError(BudgetError):
    """Exception raised when the retry budget is exceeded for a task."""


class SpecDefectLimitExceededError(BudgetError):
    """Exception raised when the spec_defect limit is exceeded for a task."""


class Phase1FireLimitExceededError(BudgetError):
    """Exception raised when a second Phase-1 fire is attempted in a sprint."""


class LoopCapExceededError(BudgetError):
    """Exception raised when an agent loop cap is exceeded in a sprint."""


class BudgetTracker:
    """Tracks and persists per-task and per-sprint loop/retry counters.

    Stores status in .saltcode/budget.json inside the target workspace.
    """

    def __init__(self, workspace_path: Path | str) -> None:
        self.workspace_path = Path(workspace_path)
        self.filepath = self.workspace_path / ".saltcode" / "budget.json"
        self.data: dict[str, Any] = {
            "tasks": {},
            "sprint": {
                "architect_loops": 0,
                "planner_loops": 0,
                "phase_1_fired": False
            }
        }
        self.load()

    def load(self) -> None:
        """Loads state from disk, or initializes defaults if not present."""
        if self.filepath.exists():
            try:
                content = self.filepath.read_text(encoding="utf-8")
                self.data = json.loads(content)
            except Exception:
                # Keep default data if loading fails
                pass

    def save(self) -> None:
        """Saves current state atomically to disk."""
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        # Simple atomic write via temporary file
        import contextlib
        import tempfile
        
        dir_name = self.filepath.parent
        with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
            temp_name = f.name
            
        try:
            Path(temp_name).replace(self.filepath)
        except Exception as e:
            with contextlib.suppress(Exception):
                Path(temp_name).unlink()
            raise e

    def _get_task_data(self, task_id: str) -> dict[str, int]:
        if "tasks" not in self.data:
            self.data["tasks"] = {}
        if task_id not in self.data["tasks"]:
            self.data["tasks"][task_id] = {
                "failures_tier_a": 0,
                "failures_tier_b": 0,
                "spec_defects": 0
            }
        from typing import cast
        return cast(dict[str, int], self.data["tasks"][task_id])

    def increment_task_failure(self, task_id: str, tier: Literal["A", "B"]) -> None:
        """Increments failure count for a specific task and tier."""
        task_data = self._get_task_data(task_id)
        if tier == "A":
            task_data["failures_tier_a"] += 1
        elif tier == "B":
            task_data["failures_tier_b"] += 1
        else:
            raise ValueError(f"Unknown tier: {tier}")
        self.save()

    def increment_spec_defect(self, task_id: str) -> None:
        """Increments spec_defect count for a task. Raises SpecDefectLimitExceededError if limit breached (>1)."""
        task_data = self._get_task_data(task_id)
        # Limit is <= 1 spec defect. If we are trying to trigger another spec defect (making it >= 2):
        if task_data["spec_defects"] >= 1:
            raise SpecDefectLimitExceededError(
                f"Spec defect limit exceeded for task '{task_id}' (more than 1 spec_defect)."
            )
        task_data["spec_defects"] += 1
        self.save()

    def get_spec_defect_count(self, task_id: str) -> int:
        """Returns the number of spec defects recorded for a task."""
        return self._get_task_data(task_id).get("spec_defects", 0)

    def increment_architect_loop(self) -> None:
        """Increments Architect loop count. Raises LoopCapExceededError on breach (>2)."""
        loops = self.data["sprint"].get("architect_loops", 0)
        if loops >= 2:
            raise LoopCapExceededError("Architect loop cap exceeded (max 2 per sprint).")
        self.data["sprint"]["architect_loops"] = loops + 1
        self.save()

    def increment_planner_loop(self) -> None:
        """Increments Planner loop count. Raises LoopCapExceededError on breach (>3)."""
        loops = self.data["sprint"].get("planner_loops", 0)
        if loops >= 3:
            raise LoopCapExceededError("Planner loop cap exceeded (max 3 per sprint).")
        self.data["sprint"]["planner_loops"] = loops + 1
        self.save()

    def mark_phase_1_fired(self) -> None:
        """Sets phase_1_fired to True. Raises Phase1FireLimitExceededError if already fired."""
        if self.data["sprint"].get("phase_1_fired", False):
            raise Phase1FireLimitExceededError("Only one Phase-1 fire is permitted per sprint.")
        self.data["sprint"]["phase_1_fired"] = True
        self.save()

    def get_active_tier(self, task_id: str, initial_complexity: str) -> Literal["A", "B"]:
        """Returns the active model tier for the task based on retry counters.

        Raises BudgetExceededError if total retry budget is exhausted (>= 3).
        """
        task_data = self._get_task_data(task_id)
        failures_a = task_data.get("failures_tier_a", 0)
        failures_b = task_data.get("failures_tier_b", 0)
        total_failures = failures_a + failures_b

        # Check total budget limit (max 3 failures allowed before attempt, meaning total_failures >= 3 blocks)
        if total_failures >= 3:
            raise BudgetExceededError(
                f"Retry budget exhausted for task '{task_id}' (failed {total_failures} times)."
            )

        if initial_complexity == "high":
            return "B"

        # Tier-A sub-cap is 2 retries (failures_a >= 2 triggers B)
        if failures_a >= 2:
            return "B"

        return "A"
