from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class FrameError:
    center_x: float
    center_y: float
    width: float
    height: float
    area: float

    def as_dict(self) -> dict[str, float]:
        return {
            "center_x": self.center_x,
            "center_y": self.center_y,
            "width": self.width,
            "height": self.height,
            "area": self.area,
        }


class CheckpointCorrectionPlanner:
    """
    Convert checkpoint visual error into S31 command payloads.

    Latest lower-layer protocol:
        {
          "type": "cmd",
          "cmd_id": "cp1_fix_01_base_depth",
          "board_id": "s31",
          "device": "base",
          "action": "move_longitudinal" / "rotate",
          "params": {...}
        }

    Correction policy, first version:
        - width error controls forward/backward distance.
          detected width > expected width: subject too large -> move backward.
          detected width < expected width: subject too small -> move forward.
        - center_x error controls base rotation.
          detected cx > expected cx: subject appears right -> rotate right.
          detected cx < expected cx: subject appears left -> rotate left.
        - center_y error controls lift.
          detected cy > expected cy: subject appears lower -> lift up.
          detected cy < expected cy: subject appears higher -> lift down.

    The planner intentionally emits small conservative corrections.
    """

    def __init__(
        self,
        depth_gain_m_per_width: float = 0.45,
        rotate_gain_deg_per_cx: float = 25.0,
        lift_gain_cm_per_cy: float = 18.0,
        max_depth_step_m: float = 0.06,
        max_rotate_step_deg: float = 5.0,
        max_lift_step_cm: float = 2.0,
        base_speed_m_s: float = 0.05,
        angular_speed_rad_s: float = 0.20,
    ) -> None:
        self.depth_gain_m_per_width = float(depth_gain_m_per_width)
        self.rotate_gain_deg_per_cx = float(rotate_gain_deg_per_cx)
        self.lift_gain_cm_per_cy = float(lift_gain_cm_per_cy)
        self.max_depth_step_m = abs(float(max_depth_step_m))
        self.max_rotate_step_deg = abs(float(max_rotate_step_deg))
        self.max_lift_step_cm = abs(float(max_lift_step_cm))
        self.base_speed_m_s = abs(float(base_speed_m_s))
        self.angular_speed_rad_s = abs(float(angular_speed_rad_s))

    @staticmethod
    def compute_error(detected_bbox: dict[str, float], expected_frame: dict[str, Any]) -> FrameError:
        expected_bbox = expected_frame.get("bbox", [0.5, 0.52, 0.35, 0.65])
        exp_cx, exp_cy, exp_w, exp_h = [float(v) for v in expected_bbox]

        det_cx = float(detected_bbox.get("cx", 0.0))
        det_cy = float(detected_bbox.get("cy", 0.0))
        det_w = float(detected_bbox.get("w", 0.0))
        det_h = float(detected_bbox.get("h", 0.0))

        return FrameError(
            center_x=det_cx - exp_cx,
            center_y=det_cy - exp_cy,
            width=det_w - exp_w,
            height=det_h - exp_h,
            area=(det_w * det_h) - (exp_w * exp_h),
        )

    @staticmethod
    def is_satisfied(error: FrameError, expected_frame: dict[str, Any]) -> bool:
        tolerance = expected_frame.get("tolerance", {}) or {}
        return (
            abs(error.center_x) <= float(tolerance.get("center_x", 0.05))
            and abs(error.center_y) <= float(tolerance.get("center_y", 0.05))
            and abs(error.width) <= float(tolerance.get("width", 0.08))
            and abs(error.height) <= float(tolerance.get("height", 0.10))
        )

    def make_correction_commands(
        self,
        checkpoint_id: str,
        iter_index: int,
        error: FrameError,
        expected_frame: dict[str, Any],
        servo: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        servo = servo or {}
        tolerance = expected_frame.get("tolerance", {}) or {}

        allow_base = bool(servo.get("allow_base", True))
        allow_lift = bool(servo.get("allow_lift", True))

        tol_x = float(tolerance.get("center_x", 0.05))
        tol_y = float(tolerance.get("center_y", 0.05))
        tol_w = float(tolerance.get("width", 0.08))

        commands: list[dict[str, Any]] = []

        # 1) Fix scale/depth first because it often changes framing a lot.
        if allow_base and abs(error.width) > tol_w:
            # Positive width error means target too large -> move backward (negative distance).
            distance_m = -error.width * self.depth_gain_m_per_width
            distance_m = clamp(distance_m, -self.max_depth_step_m, self.max_depth_step_m)
            if abs(distance_m) >= 0.005:
                commands.append(
                    {
                        "type": "cmd",
                        "cmd_id": f"{checkpoint_id}_fix_{iter_index:02d}_base_depth",
                        "board_id": "s31",
                        "device": "base",
                        "action": "move_longitudinal",
                        "params": {
                            "distance_m": round(distance_m, 4),
                            "speed_m_s": round(self.base_speed_m_s, 4),
                        },
                    }
                )
                return commands

        # 2) Fix horizontal framing by base rotation.
        if allow_base and abs(error.center_x) > tol_x:
            # Positive center_x means target appears right -> rotate right, angle negative.
            angle_deg = -error.center_x * self.rotate_gain_deg_per_cx
            angle_deg = clamp(angle_deg, -self.max_rotate_step_deg, self.max_rotate_step_deg)
            if abs(angle_deg) >= 0.5:
                commands.append(
                    {
                        "type": "cmd",
                        "cmd_id": f"{checkpoint_id}_fix_{iter_index:02d}_base_rotate",
                        "board_id": "s31",
                        "device": "base",
                        "action": "rotate",
                        "params": {
                            "angle_deg": round(angle_deg, 3),
                            "angular_speed_rad_s": round(self.angular_speed_rad_s, 4),
                            "radius_m": 0.0,
                        },
                    }
                )
                return commands

        # 3) Fix vertical framing by lift.
        if allow_lift and abs(error.center_y) > tol_y:
            # Positive center_y means target appears lower -> lift up, delta positive.
            delta_cm = error.center_y * self.lift_gain_cm_per_cy
            delta_cm = clamp(delta_cm, -self.max_lift_step_cm, self.max_lift_step_cm)
            if abs(delta_cm) >= 0.2:
                commands.append(
                    {
                        "type": "cmd",
                        "cmd_id": f"{checkpoint_id}_fix_{iter_index:02d}_lift_y",
                        "board_id": "s31",
                        "device": "lift",
                        "action": "move_delta",
                        "params": {
                            "delta_cm": round(delta_cm, 3),
                        },
                    }
                )
                return commands

        return commands
