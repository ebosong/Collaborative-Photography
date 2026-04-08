"""Rule-based framing controller for chassis and lift suggestions."""

from __future__ import annotations

from typing import Any


class FramingController:
    """Compute simple motion suggestions from target framing error."""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def compute_control(
        self,
        shot_plan: Any,
        target_state: dict[str, float | bool],
        current_height: float,
    ) -> dict[str, float]:
        """Return rule-based control suggestions for base and lift."""
        if not target_state.get("detected", False):
            return {"linear_x": 0.0, "angular_z": 0.0, "lift_delta": 0.0}

        center_error = float(target_state["center_x"]) - 0.5
        scale_error = float(shot_plan.subject_scale_target) - float(target_state["scale"])
        height_error = float(shot_plan.height_m) - float(current_height)

        angular_z = -center_error * 1.5
        linear_x = scale_error * 1.2
        lift_delta = height_error * 0.4

        return {
            "linear_x": linear_x,
            "angular_z": angular_z,
            "lift_delta": lift_delta,
        }
