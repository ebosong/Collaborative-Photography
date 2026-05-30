from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Iterable


@dataclass
class ArmPose:
    """
    Cached end-effector pose.

    Units:
    - x_mm, y_mm, z_mm: millimeters
    - t_rad: radians
    """
    x_mm: float = 150.0
    y_mm: float = 0.0
    z_mm: float = 200.0
    t_rad: float = 3.14


class ArmCommandTranslator:
    """
    Translate PC-side high-level arm motions into RoArm raw JSON commands.

    New default arm motion command:
    - Use official RoArm CMD_XYZT_GOAL_CTRL:
      {"T":104,"x":...,"y":...,"z":...,"t":...,"spd":...}

    This means:
    - no PC-side cubic interpolation by default
    - no forward_raw_batch required for normal arm movement
    - P4 only forwards one raw command over UART

    The PC keeps a cached target pose. Delta commands are applied on top of the
    previous target pose.
    """

    def __init__(
        self,
        init_pose: ArmPose | None = None,
        sample_period_s: float = 0.05,
        min_steps: int = 10,
        verbose: bool = False,
    ) -> None:
        self.pose = init_pose or ArmPose()
        # Kept for backward compatibility with old code paths.
        self.sample_period_s = float(sample_period_s)
        self.min_steps = int(min_steps)
        self.verbose = bool(verbose)

    # =========================
    # raw command builders
    # =========================
    @staticmethod
    def _json_line(data: dict) -> str:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    def build_init_pose_command(self, update_cached_pose: bool = True) -> str:
        if update_cached_pose:
            self.set_cached_pose_mm(x_mm=150.0, y_mm=0.0, z_mm=200.0, t_rad=3.14)
        return self._json_line({"T": 100})

    def build_query_feedback_command(self) -> str:
        return self._json_line({"T": 105})

    def build_goal_ctrl_command(
        self,
        x_mm: float,
        y_mm: float,
        z_mm: float,
        t_rad: float | None = None,
        speed: float = 0.25,
        update_cached_pose: bool = True,
    ) -> str:
        """
        Build official RoArm T=104 goal command.

        Example output:
            {"T":104,"x":160.0,"y":10.0,"z":210.0,"t":3.14,"spd":0.25}
        """
        if speed <= 0:
            raise ValueError("speed must be > 0")

        t_use = self.pose.t_rad if t_rad is None else float(t_rad)

        target = ArmPose(
            x_mm=float(x_mm),
            y_mm=float(y_mm),
            z_mm=float(z_mm),
            t_rad=t_use,
        )

        cmd = {
            "T": 104,
            "x": round(target.x_mm, 2),
            "y": round(target.y_mm, 2),
            "z": round(target.z_mm, 2),
            "t": round(target.t_rad, 4),
            "spd": round(float(speed), 4),
        }

        if update_cached_pose:
            self.pose = target

        return self._json_line(cmd)

    def build_absolute_goal_m(
        self,
        target_xyz_m: list[float] | tuple[float, float, float],
        speed: float = 0.25,
        t_rad: float | None = None,
        update_cached_pose: bool = True,
    ) -> str:
        if len(target_xyz_m) != 3:
            raise ValueError("target_xyz_m must have exactly 3 values")
        x_m, y_m, z_m = target_xyz_m
        return self.build_goal_ctrl_command(
            x_mm=float(x_m) * 1000.0,
            y_mm=float(y_m) * 1000.0,
            z_mm=float(z_m) * 1000.0,
            t_rad=t_rad,
            speed=speed,
            update_cached_pose=update_cached_pose,
        )

    def build_delta_goal_cm(
        self,
        front_cm: float = 0.0,
        left_cm: float = 0.0,
        up_cm: float = 0.0,
        wrist_delta_deg: float = 0.0,
        target_t_rad: float | None = None,
        speed: float = 0.25,
        update_cached_pose: bool = True,
    ) -> str:
        """
        Build one T=104 command from a relative movement.

        Axis convention:
        - front_cm -> x
        - left_cm  -> y
        - up_cm    -> z
        """
        target_x = self.pose.x_mm + float(front_cm) * 10.0
        target_y = self.pose.y_mm + float(left_cm) * 10.0
        target_z = self.pose.z_mm + float(up_cm) * 10.0

        if target_t_rad is not None:
            target_t = float(target_t_rad)
        else:
            target_t = self.pose.t_rad + math.radians(float(wrist_delta_deg))

        return self.build_goal_ctrl_command(
            x_mm=target_x,
            y_mm=target_y,
            z_mm=target_z,
            t_rad=target_t,
            speed=speed,
            update_cached_pose=update_cached_pose,
        )

    # =========================
    # backward-compatible helpers
    # =========================
    def build_pose_command(
        self,
        x_mm: float,
        y_mm: float,
        z_mm: float,
        t_rad: float | None = None,
    ) -> str:
        """
        Kept for legacy code. It still builds one T=1041 direct pose command.
        New script path should use build_goal_ctrl_command(T=104).
        """
        if t_rad is None:
            t_rad = self.pose.t_rad

        return self._json_line(
            {
                "T": 1041,
                "x": round(float(x_mm), 2),
                "y": round(float(y_mm), 2),
                "z": round(float(z_mm), 2),
                "t": round(float(t_rad), 4),
            }
        )

    def plan_absolute_move_mm(
        self,
        target_x_mm: float,
        target_y_mm: float,
        target_z_mm: float,
        speed_cm_s: float = 5.0,
        t_rad: float | None = None,
        update_cached_pose: bool = True,
    ) -> list[str]:
        """
        Backward-compatible wrapper.

        Old behavior returned many T=1041 interpolation points.
        New behavior returns exactly one official T=104 goal command.
        """
        # Map legacy speed_cm_s to a conservative official spd only when this
        # old method is called directly. Main script_executor uses explicit speed.
        speed = 0.25
        return [
            self.build_goal_ctrl_command(
                x_mm=target_x_mm,
                y_mm=target_y_mm,
                z_mm=target_z_mm,
                t_rad=t_rad,
                speed=speed,
                update_cached_pose=update_cached_pose,
            )
        ]

    def plan_delta_move_cm(
        self,
        dx_cm: float = 0.0,
        dy_cm: float = 0.0,
        dz_cm: float = 0.0,
        speed_cm_s: float = 5.0,
        t_rad: float | None = None,
        update_cached_pose: bool = True,
    ) -> list[str]:
        target_x = self.pose.x_mm + float(dx_cm) * 10.0
        target_y = self.pose.y_mm + float(dy_cm) * 10.0
        target_z = self.pose.z_mm + float(dz_cm) * 10.0
        return self.plan_absolute_move_mm(
            target_x_mm=target_x,
            target_y_mm=target_y,
            target_z_mm=target_z,
            speed_cm_s=speed_cm_s,
            t_rad=t_rad,
            update_cached_pose=update_cached_pose,
        )

    def plan_left_up_front_move_cm(
        self,
        left_cm: float = 0.0,
        up_cm: float = 0.0,
        front_cm: float = 0.0,
        speed_cm_s: float = 5.0,
        t_rad: float | None = None,
        update_cached_pose: bool = True,
    ) -> list[str]:
        return self.plan_delta_move_cm(
            dx_cm=float(front_cm),
            dy_cm=float(left_cm),
            dz_cm=float(up_cm),
            speed_cm_s=speed_cm_s,
            t_rad=t_rad,
            update_cached_pose=update_cached_pose,
        )

    # =========================
    # cache / feedback helpers
    # =========================
    def set_cached_pose_mm(
        self,
        x_mm: float | None = None,
        y_mm: float | None = None,
        z_mm: float | None = None,
        t_rad: float | None = None,
    ) -> None:
        self.pose = ArmPose(
            x_mm=self.pose.x_mm if x_mm is None else float(x_mm),
            y_mm=self.pose.y_mm if y_mm is None else float(y_mm),
            z_mm=self.pose.z_mm if z_mm is None else float(z_mm),
            t_rad=self.pose.t_rad if t_rad is None else float(t_rad),
        )

    def sync_cached_pose_from_feedback(self, feedback: dict) -> None:
        """
        Update the cached pose using a parsed arm feedback payload, typically T=1051.
        """
        self.pose = ArmPose(
            x_mm=float(feedback.get("x", self.pose.x_mm)),
            y_mm=float(feedback.get("y", self.pose.y_mm)),
            z_mm=float(feedback.get("z", self.pose.z_mm)),
            t_rad=float(feedback.get("t", self.pose.t_rad)),
        )

    def get_cached_pose(self) -> dict:
        return {
            "x_mm": round(self.pose.x_mm, 2),
            "y_mm": round(self.pose.y_mm, 2),
            "z_mm": round(self.pose.z_mm, 2),
            "t_rad": round(self.pose.t_rad, 4),
        }

    # =========================
    # payload helpers for P4 forwarding
    # =========================
    @staticmethod
    def build_p4_forward_raw_payload(raw_command: str) -> dict:
        return {
            "device": "arm",
            "action": "forward_raw",
            "params": {
                "raw_command": raw_command,
            },
        }

    @staticmethod
    def build_p4_forward_batch_payload(commands: Iterable[str]) -> dict:
        return {
            "device": "arm",
            "action": "forward_raw_batch",
            "params": {
                "commands": list(commands),
            },
        }
