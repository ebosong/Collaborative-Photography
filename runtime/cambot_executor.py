"""CamBot executor: old command-format plan -> current S3/P4 hardware protocol.

This adapter keeps the top-level agent's command format:

    ScriptPlan(script=..., commands=[MotionCommand(...)])

but dispatches to the current hardware controllers:

    S3 / 2345: base + lift
    P4 / 2346: arm forward_raw

Important policy:
    command target/action == *.stop is treated as a local scheduling marker only.
    It is NOT sent to S3/P4, because the PC-side scheduler/executor controls timing
    and the lower controllers should finish each finite command by themselves.
"""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any

from runtime.base_controller import BaseController
from runtime.lift_controller import LiftController
from runtime.p4_arm_controller import P4ArmController
from schemas.script_schema import MotionCommand, ScriptPlan


class CamBotExecutor:
    """Dispatch validated LLM-generated command scripts through S3/P4 controllers."""

    def __init__(self, config: dict[str, Any], repo_root: str):
        self.config = config
        self.repo_root = repo_root
        self.logger = logging.getLogger(self.__class__.__name__)

        self.base_controller = BaseController()
        self.lift_controller = LiftController(
            initial_height=float(config["limits"]["height_m"]["default"])
        )
        self.p4_arm_controller = P4ArmController()

        self._connected_base = False
        self._connected_lift = False
        self._connected_arm = False

    def execute(self, plan: ScriptPlan) -> None:
        """Run each validated command in order."""
        self.logger.info(
            "Starting CamBot command script with %d commands.",
            len(plan.commands),
        )
        print(f"[SCRIPT] {plan.script.title} | commands={len(plan.commands)}")

        try:
            # Start TCP servers early. Actual ESP32 clients may connect after this.
            self._ensure_base_lift_connected()
            self._ensure_arm_connected()

            for command in plan.commands:
                self._dispatch(command)

            self.logger.info("CamBot command script finished cleanly.")
        except Exception as exc:
            self.logger.exception("Executor stopped due to unexpected error: %s", exc)
            # Do not send plan-level stop commands here automatically.
            # If a real emergency stop is required later, add a separate emergency_stop path.
            raise
        finally:
            self.close()

    def _dispatch(self, command: MotionCommand) -> None:
        """Dispatch one command to the matching wrapped lower-level interface."""
        printable = self._format_command(command)
        print(printable)
        self.logger.info(printable)

        if command.target == "base":
            self._dispatch_base(command)
        elif command.target == "lift":
            self._dispatch_lift(command)
        elif command.target == "arm":
            self._dispatch_arm(command)
        elif command.target == "wait":
            self._sleep(command.duration_s)

    def _dispatch_base(self, command: MotionCommand) -> None:
        if command.action == "connect":
            self._ensure_base_lift_connected()
            return

        if command.action == "stop":
            self._local_stop_marker(command)
            return

        if command.action != "move":
            return

        self._ensure_base_lift_connected()

        linear_x = float(command.linear_x or 0.0)
        angular_z = float(command.angular_z or 0.0)
        duration_s = float(command.duration_s or 0.0)

        if abs(linear_x) < 1e-9 and abs(angular_z) < 1e-9:
            self.logger.info("Skip zero base move.")
            return

        # The validator should already avoid mixed linear+angular motion.
        # If both exist, keep the stronger one to avoid unsupported arc motion.
        if abs(linear_x) >= abs(angular_z):
            distance_m = linear_x * duration_s
            speed_m_s = abs(linear_x)
            if speed_m_s < 1e-6 or abs(distance_m) < 1e-6:
                self.logger.info("Skip tiny base longitudinal move.")
                return
            self.logger.info(
                "BASE command adapter | move_longitudinal distance_m=%.3f speed_m_s=%.3f",
                distance_m,
                speed_m_s,
            )
            self.base_controller.move_longitudinal(
                distance_m=distance_m,
                speed_m_s=speed_m_s,
            )
            return

        angle_deg = math.degrees(angular_z * duration_s)
        angular_speed_rad_s = abs(angular_z)
        if angular_speed_rad_s < 1e-6 or abs(angle_deg) < 1e-6:
            self.logger.info("Skip tiny base rotate.")
            return
        self.logger.info(
            "BASE command adapter | rotate angle_deg=%.3f angular_speed_rad_s=%.3f",
            angle_deg,
            angular_speed_rad_s,
        )
        self.base_controller.rotate(
            radius_m=0.0,
            angular_speed_rad_s=angular_speed_rad_s,
            angle_deg=angle_deg,
        )

    def _dispatch_lift(self, command: MotionCommand) -> None:
        if command.action == "connect":
            self._ensure_base_lift_connected()
            return

        if command.action == "stop":
            self._local_stop_marker(command)
            return

        self._ensure_base_lift_connected()

        if command.action == "move_by":
            delta_m = float(command.delta_m or 0.0)
            if abs(delta_m) < 1e-6:
                self.logger.info("Skip tiny lift move_by.")
                return
            self.logger.info("LIFT command adapter | move_by delta_m=%.3f", delta_m)
            self.lift_controller.move_by(delta_m)
            return

        if command.action == "move_to":
            target_height_m = float(command.height_m or self.lift_controller.current_height_m)
            current_height_m = float(self.lift_controller.current_height_m)
            delta_m = target_height_m - current_height_m
            if abs(delta_m) < 1e-6:
                self.logger.info("Skip lift move_to because target equals cached height.")
                return
            self.logger.info(
                "LIFT command adapter | move_to height_m=%.3f -> move_by delta_m=%.3f",
                target_height_m,
                delta_m,
            )
            self.lift_controller.move_by(delta_m)

    def _dispatch_arm(self, command: MotionCommand) -> None:
        if command.action == "connect":
            self._ensure_arm_connected()
            return

        if command.action == "stop":
            self._local_stop_marker(command)
            return

        if command.action != "preset":
            return

        self._ensure_arm_connected()

        preset = (command.preset or "ready").lower()
        if preset not in {"ready", "home", "init", "initial", "reset"}:
            self.logger.warning("Unsupported arm preset=%r; fallback to ready.", preset)

        raw = json.dumps({"T": 100}, ensure_ascii=False, separators=(",", ":"))
        self.logger.info("ARM command adapter | preset=%s -> raw=%s", preset, raw)
        self.p4_arm_controller.send_raw_command(raw)

    def _ensure_base_lift_connected(self) -> None:
        if self._connected_base and self._connected_lift:
            return
        self.base_controller.connect()
        self.lift_controller.connect()
        self._connected_base = True
        self._connected_lift = True

    def _ensure_arm_connected(self) -> None:
        if self._connected_arm:
            return
        self.p4_arm_controller.connect()
        self._connected_arm = True

    def _local_stop_marker(self, command: MotionCommand) -> None:
        """Treat plan stop commands as local timing/scheduling markers.

        We do not send stop to lower devices in normal scripts. Finite commands
        should complete by themselves and report ACK later when ACK mode is enabled.
        """
        self.logger.info(
            "LOCAL STOP MARKER only | %s.%s id=%s; no TCP payload sent.",
            command.target,
            command.action,
            command.id,
        )
        print(f"[LOCAL ONLY] {command.id} {command.target}.stop -> no TCP payload sent")

    def close(self) -> None:
        # Controllers may hold shared TCP servers. Their close methods are expected
        # to be lightweight and should not necessarily kill long-running servers.
        try:
            self.p4_arm_controller.close()
        except Exception:
            self.logger.debug("Ignoring P4 close error.", exc_info=True)
        try:
            self.base_controller.close()
        except Exception:
            self.logger.debug("Ignoring base close error.", exc_info=True)
        try:
            self.lift_controller.close()
        except Exception:
            self.logger.debug("Ignoring lift close error.", exc_info=True)

    @staticmethod
    def _sleep(duration_s: float) -> None:
        if duration_s > 0:
            time.sleep(float(duration_s))

    @staticmethod
    def _format_command(command: MotionCommand) -> str:
        if command.target == "base" and command.action == "move":
            return (
                f"[SCRIPT CMD] {command.id} {command.phase} | base.move "
                f"linear_x={command.linear_x or 0.0:.3f} "
                f"angular_z={command.angular_z or 0.0:.3f} "
                f"duration_s={command.duration_s:.2f} | {command.description}"
            )
        if command.target == "lift" and command.action == "move_to":
            return (
                f"[SCRIPT CMD] {command.id} {command.phase} | lift.move_to "
                f"height_m={command.height_m or 0.0:.3f} | {command.description}"
            )
        if command.target == "lift" and command.action == "move_by":
            return (
                f"[SCRIPT CMD] {command.id} {command.phase} | lift.move_by "
                f"delta_m={command.delta_m or 0.0:.3f} | {command.description}"
            )
        if command.target == "arm" and command.action == "preset":
            return (
                f"[SCRIPT CMD] {command.id} {command.phase} | arm.preset "
                f"name={command.preset or 'ready'} | {command.description}"
            )
        if command.target == "wait" or command.action == "wait":
            return (
                f"[SCRIPT CMD] {command.id} {command.phase} | wait "
                f"duration_s={command.duration_s:.2f} | {command.description}"
            )
        return (
            f"[SCRIPT CMD] {command.id} {command.phase} | "
            f"{command.target}.{command.action} | {command.description}"
        )
