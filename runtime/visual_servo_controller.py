from __future__ import annotations

import logging
from typing import Any


class VisualServoController:
    """
    Rule-based small-step visual servo.

    First-version policy:
    1. area error       -> base_longitudinal
    2. center_x error   -> base_rotate
    3. center_y error   -> lift_delta

    Only one correction action is generated per iteration. This makes the
    correction look like continuous small-step servoing instead of a large,
    jerky one-shot correction.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)

    @staticmethod
    def _clamp(value: float, limit: float) -> float:
        return max(-limit, min(limit, value))

    def make_next_correction(self, expected_frame: Any, evaluation: Any, step_id_prefix: str, iter_index: int) -> dict | None:
        if evaluation.ok or not evaluation.found:
            return None

        errors = evaluation.errors
        servo = expected_frame.servo
        use_actions = set(servo.use_actions)

        tol = expected_frame.tolerance
        max_step = servo.max_step
        gain = servo.gain
        sign = servo.direction_sign

        # 1. Distance/size correction first.
        if (
            "base_longitudinal" in use_actions
            and abs(errors.get("area", 0.0)) > tol.area
        ):
            distance_m = sign.longitudinal * gain.longitudinal_m_per_area * errors["area"]
            distance_m = self._clamp(distance_m, max_step.longitudinal_m)

            if abs(distance_m) > 1e-4:
                return {
                    "id": f"{step_id_prefix}_vision_longitudinal_{iter_index}",
                    "type": "base_longitudinal",
                    "params": {
                        "distance_m": round(distance_m, 4),
                        "speed_m_s": 0.06,
                    },
                    "timeout_s": 5.0,
                    "blocking": True,
                    "on_fail": "continue",
                    "note": "visual servo correction: bbox area",
                }

        # 2. Horizontal center correction.
        if (
            "base_rotate" in use_actions
            and abs(errors.get("center_x", 0.0)) > tol.center_x
        ):
            angle_deg = sign.rotate * gain.rotate_deg_per_norm_x * errors["center_x"]
            angle_deg = self._clamp(angle_deg, max_step.rotate_deg)

            if abs(angle_deg) > 0.05:
                return {
                    "id": f"{step_id_prefix}_vision_rotate_{iter_index}",
                    "type": "base_rotate",
                    "params": {
                        "radius_m": 0.0,
                        "angular_speed_rad_s": 0.12,
                        "angle_deg": round(angle_deg, 3),
                    },
                    "timeout_s": 5.0,
                    "blocking": True,
                    "on_fail": "continue",
                    "note": "visual servo correction: center_x",
                }

        # 3. Vertical center correction.
        if (
            "lift_delta" in use_actions
            and abs(errors.get("center_y", 0.0)) > tol.center_y
        ):
            delta_cm = sign.lift * gain.lift_cm_per_norm_y * errors["center_y"]
            delta_cm = self._clamp(delta_cm, max_step.lift_cm)

            if abs(delta_cm) > 0.05:
                return {
                    "id": f"{step_id_prefix}_vision_lift_{iter_index}",
                    "type": "lift_delta",
                    "params": {
                        "delta_cm": round(delta_cm, 3),
                    },
                    "timeout_s": 5.0,
                    "blocking": True,
                    "on_fail": "continue",
                    "note": "visual servo correction: center_y",
                }

        return None
