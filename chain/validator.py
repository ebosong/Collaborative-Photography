"""Plan parsing, validation, clipping, and fallback substitution.

This validator keeps the current script + commands format, but clips values into
ranges that match the current S3/P4 compatibility executor.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from schemas.script_schema import MotionCommand, ScriptPlan


class PlanValidator:
    """Validate LLM command scripts and clip values into safe runtime ranges."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

    def validate_and_clip(self, plan_text: str) -> ScriptPlan:
        """Parse raw text, validate against schema, and clip unsafe values."""
        payload = self._parse_json(plan_text)

        try:
            plan = ScriptPlan.model_validate(payload)
        except ValidationError as exc:
            self.logger.warning("Schema validation failed, applying safe fallback payload: %s", exc)
            plan = ScriptPlan.model_validate(self._safe_default_payload())

        return self._clip_plan(plan)

    def validate_and_clip_strict(self, plan_text: str) -> ScriptPlan:
        """Parse and validate without substituting a safe default payload."""
        payload = json.loads(plan_text)
        if not isinstance(payload, dict):
            raise ValueError("Planner output must be a JSON object.")
        plan = ScriptPlan.model_validate(payload)
        return self._clip_plan(plan)

    def _clip_plan(self, plan: ScriptPlan) -> ScriptPlan:
        """Clip a validated command script into configured safe runtime ranges."""
        commands = [self._clip_command(index, command) for index, command in enumerate(plan.commands, start=1)]
        if not commands:
            commands = [
                MotionCommand(
                    id="cmd_01",
                    phase="安全兜底",
                    target="base",
                    action="stop",
                    description="未生成有效动作时停止底盘。",
                )
            ]

        commands = self._ensure_stop_commands(commands)
        plan.commands = self._renumber_commands(commands)
        plan.script.total_duration_s = self._total_duration(plan.commands)
        if not plan.script.title:
            plan.script.title = "CamBot executable filming script"
        if not plan.script.summary:
            plan.script.summary = "逐条执行的拍摄运动控制脚本。"
        return plan

    def _clip_command(self, index: int, command: MotionCommand) -> MotionCommand:
        command.id = command.id or f"cmd_{index:02d}"
        command.phase = command.phase or "拍摄动作"
        command = self._normalize_target_action(command)
        command.description = command.description or self._default_description(command)
        command.duration_s = self._clip_duration(command.duration_s)

        if command.target == "base":
            if command.action == "move":
                command.linear_x = self._clip_optional(
                    command.linear_x,
                    self._limit("base", "max_linear_speed", 0.30),
                    default=0.0,
                )
                command.angular_z = self._clip_optional(
                    command.angular_z,
                    self._limit("base", "max_angular_speed", 0.50),
                    default=0.0,
                )
                command = self._force_base_move_to_single_axis(command)
            elif command.action == "stop":
                command.linear_x = None
                command.angular_z = None
                command.duration_s = 0.0

        if command.target == "lift":
            height_limits = self.config.get("limits", {}).get("height_m", {})
            if command.action == "move_to":
                command.height_m = self._clip_range(
                    command.height_m,
                    minimum=float(height_limits.get("min", 0.6)),
                    maximum=float(height_limits.get("max", 1.8)),
                    default=float(height_limits.get("default", 1.0)),
                )
            elif command.action == "move_by":
                command.delta_m = self._clip_optional(
                    command.delta_m,
                    self._limit("lift", "max_delta_per_step", 0.05),
                    default=0.0,
                )
            elif command.action == "stop":
                command.height_m = None
                command.delta_m = None
                command.duration_s = 0.0

        if command.target == "arm":
            if command.action == "preset":
                preset = (command.preset or "ready").strip().lower()
                if preset not in {"ready", "home", "init", "initial", "reset"}:
                    self.logger.warning("Unsupported arm preset '%s'; rewriting to ready.", preset)
                    preset = "ready"
                command.preset = preset
            if command.action == "stop":
                command.preset = None
                command.duration_s = 0.0

        if command.target == "wait":
            command.action = "wait"
            command.linear_x = None
            command.angular_z = None
            command.height_m = None
            command.delta_m = None
            command.preset = None

        return command

    @staticmethod
    def _force_base_move_to_single_axis(command: MotionCommand) -> MotionCommand:
        """Current S3 compatibility layer supports straight or in-place rotate first."""
        linear_x = float(command.linear_x or 0.0)
        angular_z = float(command.angular_z or 0.0)

        if abs(linear_x) > 1e-6 and abs(angular_z) > 1e-6:
            if abs(linear_x) >= abs(angular_z):
                command.angular_z = 0.0
                command.description = (
                    command.description + "（已按硬件兼容规则转换为直线运动。）"
                )
            else:
                command.linear_x = 0.0
                command.description = (
                    command.description + "（已按硬件兼容规则转换为原地旋转。）"
                )
        return command

    @staticmethod
    def _normalize_target_action(command: MotionCommand) -> MotionCommand:
        allowed_actions = {
            "base": {"connect", "move", "stop"},
            "lift": {"connect", "move_to", "move_by", "stop"},
            "arm": {"connect", "preset", "stop"},
            "wait": {"wait"},
        }
        if command.action in allowed_actions[command.target]:
            return command

        command.phase = command.phase or "安全兜底"
        command.description = (
            command.description
            or f"无效指令 {command.target}.{command.action} 已转换为等待占位。"
        )
        command.target = "wait"
        command.action = "wait"
        command.duration_s = max(0.0, command.duration_s)
        command.linear_x = None
        command.angular_z = None
        command.height_m = None
        command.delta_m = None
        command.preset = None
        return command

    def _ensure_stop_commands(self, commands: list[MotionCommand]) -> list[MotionCommand]:
        stopped_targets = {
            command.target
            for command in commands
            if command.target in {"base", "lift", "arm"} and command.action == "stop"
        }
        next_index = len(commands) + 1
        for target, description in [
            ("base", "拍摄结束后停止底盘。"),
            ("lift", "拍摄结束后停止升降。"),
            ("arm", "拍摄结束后停止机械臂。"),
        ]:
            if target in stopped_targets:
                continue
            commands.append(
                MotionCommand(
                    id=f"cmd_{next_index:02d}",
                    phase="结束动作",
                    target=target,  # type: ignore[arg-type]
                    action="stop",
                    description=description,
                )
            )
            next_index += 1
        return commands

    @staticmethod
    def _renumber_commands(commands: list[MotionCommand]) -> list[MotionCommand]:
        for index, command in enumerate(commands, start=1):
            command.id = f"cmd_{index:02d}"
        return commands

    @staticmethod
    def _total_duration(commands: list[MotionCommand]) -> float:
        return round(sum(max(0.0, float(command.duration_s)) for command in commands), 2)

    def _parse_json(self, plan_text: str) -> dict[str, Any]:
        try:
            payload = json.loads(plan_text)
        except json.JSONDecodeError as exc:
            self.logger.warning("Planner returned invalid JSON, using safe default: %s", exc)
            payload = self._safe_default_payload()

        if not isinstance(payload, dict):
            self.logger.warning("Planner returned non-object JSON, using safe default payload.")
            return self._safe_default_payload()
        return payload

    def _limit(self, section: str, key: str, default: float) -> float:
        return float(self.config.get("limits", {}).get(section, {}).get(key, default))

    @staticmethod
    def _clip_optional(value: float | None, limit: float, default: float) -> float:
        raw = default if value is None else float(value)
        return max(-limit, min(limit, raw))

    @staticmethod
    def _clip_range(
        value: float | None,
        minimum: float,
        maximum: float,
        default: float,
    ) -> float:
        raw = default if value is None else float(value)
        return max(minimum, min(maximum, raw))

    @staticmethod
    def _clip_duration(value: float) -> float:
        return round(max(0.0, min(15.0, float(value))), 2)

    @staticmethod
    def _default_description(command: MotionCommand) -> str:
        return f"{command.phase}: {command.target}.{command.action}"

    def _safe_default_payload(self) -> dict[str, Any]:
        default_height = float(self.config.get("limits", {}).get("height_m", {}).get("default", 1.0))
        return {
            "script": {
                "title": "Safe centered filming script",
                "summary": "连接控制器，进入准备位，执行短暂稳定跟拍，然后停止。",
                "total_duration_s": 5.0,
            },
            "commands": [
                {
                    "id": "cmd_01",
                    "phase": "准备阶段",
                    "target": "base",
                    "action": "connect",
                    "description": "连接底盘控制器。",
                },
                {
                    "id": "cmd_02",
                    "phase": "准备阶段",
                    "target": "lift",
                    "action": "connect",
                    "description": "连接升降控制器。",
                },
                {
                    "id": "cmd_03",
                    "phase": "准备阶段",
                    "target": "arm",
                    "action": "connect",
                    "description": "连接机械臂控制器。",
                },
                {
                    "id": "cmd_04",
                    "phase": "准备阶段",
                    "target": "arm",
                    "action": "preset",
                    "preset": "ready",
                    "description": "机械臂进入 ready 预置位。",
                },
                {
                    "id": "cmd_05",
                    "phase": "起拍动作",
                    "target": "lift",
                    "action": "move_to",
                    "height_m": default_height,
                    "description": "升降调整到稳定中景高度。",
                },
                {
                    "id": "cmd_06",
                    "phase": "跟拍动作",
                    "target": "base",
                    "action": "move",
                    "linear_x": 0.10,
                    "angular_z": 0.0,
                    "duration_s": 3.0,
                    "description": "底盘低速向前跟拍主体。",
                },
                {
                    "id": "cmd_07",
                    "phase": "结束动作",
                    "target": "base",
                    "action": "stop",
                    "description": "停止底盘。",
                },
                {
                    "id": "cmd_08",
                    "phase": "结束动作",
                    "target": "lift",
                    "action": "stop",
                    "description": "停止升降。",
                },
                {
                    "id": "cmd_09",
                    "phase": "结束动作",
                    "target": "arm",
                    "action": "stop",
                    "description": "停止机械臂。",
                },
            ],
        }
