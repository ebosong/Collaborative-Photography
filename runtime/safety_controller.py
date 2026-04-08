"""Safety clipping for rule-based control outputs and target-loss handling."""

from __future__ import annotations

import logging
from typing import Any


class SafetyController:
    """Apply runtime safety limits before commands are printed."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

    def apply(
        self,
        control: dict[str, float],
        plan: Any,
        target_state: dict[str, float | bool],
    ) -> dict[str, float]:
        """Clip control outputs and stop motion if the target is lost."""
        if not target_state.get("detected", False):
            self.logger.warning("Target lost. Applying lost-target safety behavior.")
            return {"linear_x": 0.0, "angular_z": 0.0, "lift_delta": 0.0}

        max_linear = min(
            float(plan.safety_rules.max_speed),
            float(self.config["limits"]["base"]["max_linear_speed"]),
        )
        max_angular = float(self.config["limits"]["base"]["max_angular_speed"])
        max_lift = float(self.config["limits"]["lift"]["max_delta_per_step"])

        return {
            "linear_x": self._clip(control["linear_x"], max_linear),
            "angular_z": self._clip(control["angular_z"], max_angular),
            "lift_delta": self._clip(control["lift_delta"], max_lift),
        }

    @staticmethod
    def _clip(value: float, limit: float) -> float:
        return max(-limit, min(limit, float(value)))
