"""CamBot executor for mock-safe command script dispatch."""

from __future__ import annotations

import logging
import time
from typing import Any

from runtime.arm_adapter import ArmAdapter
from runtime.base_controller import BaseController
from runtime.lift_controller import LiftController
from schemas.script_schema import MotionCommand, ScriptPlan


class CamBotExecutor:
    """Dispatch validated LLM-generated commands through wrapped controllers."""

    def __init__(self, config: dict[str, Any], repo_root: str):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        self.base_controller = BaseController()
        self.lift_controller = LiftController(
            initial_height=float(config["limits"]["height_m"]["default"])
        )
        self.arm_adapter = ArmAdapter(repo_root=repo_root, arm_config=config["arm"])

    def execute(self, plan: ScriptPlan) -> None:
        """Run each validated command in order."""
        self.logger.info(
            "Starting CamBot command script with %d commands.",
            len(plan.commands),
        )
        print(f"[SCRIPT] {plan.script.title} | commands={len(plan.commands)}")

        try:
            for command in plan.commands:
                self._dispatch(command)
            self.logger.info("CamBot command script finished cleanly.")
        except Exception as exc:
            self.logger.exception("Executor stopped due to unexpected error: %s", exc)
            self._stop_all()
            raise
        finally:
            self.arm_adapter.close()
            self.base_controller.close()
            self.lift_controller.close()

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
            self.base_controller.connect()
        elif command.action == "move":
            self.base_controller.move(
                linear_x=float(command.linear_x or 0.0),
                angular_z=float(command.angular_z or 0.0),
            )
            self._sleep(command.duration_s)
        elif command.action == "stop":
            self.base_controller.stop()

    def _dispatch_lift(self, command: MotionCommand) -> None:
        if command.action == "connect":
            self.lift_controller.connect()
        elif command.action == "move_to":
            self.lift_controller.move_to(float(command.height_m or 0.0))
            self._sleep(command.duration_s)
        elif command.action == "move_by":
            self.lift_controller.move_by(float(command.delta_m or 0.0))
            self._sleep(command.duration_s)
        elif command.action == "stop":
            self.lift_controller.stop()

    def _dispatch_arm(self, command: MotionCommand) -> None:
        if command.action == "connect":
            self.arm_adapter.connect()
        elif command.action == "preset":
            self.arm_adapter.execute_preset(command.preset or "ready")
            self._sleep(command.duration_s)
        elif command.action == "stop":
            self.arm_adapter.stop()

    def _stop_all(self) -> None:
        self.base_controller.stop()
        self.lift_controller.stop()
        self.arm_adapter.stop()

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
