"""TimelineScript parsing, validation, normalization, and safety clipping."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from schemas.timeline_script_schema import (
    ArmMoveDeltaAction,
    ArmMoveXYZAction,
    BaseLongitudinalAction,
    BaseRotateAction,
    CheckpointAction,
    FollowModeAction,
    LiftDeltaAction,
    LightingPlanEntry,
    TimelineActionType,
    TimelineScript,
    WaitAction,
    default_lighting_plan,
)


class PlanValidator:
    """Validate LLM TimelineScript output and clip values into conservative ranges."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

    def validate_and_clip(self, plan_text: str) -> TimelineScript:
        """Parse raw text, validate against schema, and clip unsafe values."""
        payload = self._parse_json(plan_text)
        payload = self._normalize_payload(payload)

        try:
            plan = TimelineScript.model_validate(payload)
        except ValidationError as exc:
            self.logger.warning("TimelineScript validation failed, using safe fallback payload: %s", exc)
            plan = TimelineScript.model_validate(self._safe_default_payload())
        except ValueError as exc:
            self.logger.warning("TimelineScript validation failed, using safe fallback payload: %s", exc)
            plan = TimelineScript.model_validate(self._safe_default_payload())

        return self._clip_plan(plan)

    def validate_and_clip_strict(self, plan_text: str) -> TimelineScript:
        """Parse and validate without substituting a safe default payload."""
        payload = json.loads(plan_text)
        if not isinstance(payload, dict):
            raise ValueError("Planner output must be a JSON object.")
        plan = TimelineScript.model_validate(self._normalize_payload(payload))
        return self._clip_plan(plan)

    def _clip_plan(self, plan: TimelineScript) -> TimelineScript:
        """Clip a validated timeline script into protocol and safety ranges."""
        plan.name = self._normalize_name(plan.name)
        plan.version = "2.0"
        plan.mode = "timeline"
        if not plan.summary.strip():
            plan.summary = "按时间轴执行拍摄动作，并提供画面检查和打光方案。"

        if not plan.timeline:
            plan.timeline = TimelineScript.model_validate(self._safe_default_payload()).timeline

        plan.timeline = [self._clip_action(action) for action in plan.timeline]
        plan.lighting_plan = plan.lighting_plan or [
            LightingPlanEntry.model_validate(item) for item in default_lighting_plan()
        ]
        plan.lighting_plan = [self._clip_lighting_entry(entry) for entry in plan.lighting_plan]

        return TimelineScript.model_validate(plan.model_dump())

    def _clip_action(self, action: Any) -> Any:
        action.description = self._sanitize_action_description(action.type, action.description)
        action.timeout_s = self._clip_range(float(action.timeout_s), 0.1, 120.0)

        if isinstance(action, BaseLongitudinalAction):
            action.params.distance_m = self._clip_range(action.params.distance_m, -0.5, 0.5)
            action.params.speed_m_s = self._clip_range(abs(action.params.speed_m_s), 0.03, 0.20)
            action.device = "s3"
            action.channel = "base"
            action.blocking = bool(action.blocking)
            action.timeout_s = self._clip_range(action.timeout_s, 1.0, 30.0)

        elif isinstance(action, BaseRotateAction):
            action.params.angle_deg = self._clip_range(action.params.angle_deg, -45.0, 45.0)
            action.params.angular_speed_rad_s = self._clip_range(
                abs(action.params.angular_speed_rad_s), 0.05, 0.35
            )
            action.device = "s3"
            action.channel = "base"
            action.blocking = bool(action.blocking)
            action.timeout_s = self._clip_range(action.timeout_s, 1.0, 30.0)

        elif isinstance(action, LiftDeltaAction):
            action.params.delta_cm = self._clip_range(action.params.delta_cm, -10.0, 10.0)
            action.device = "s3"
            action.channel = "lift"
            action.blocking = bool(action.blocking)
            action.timeout_s = self._clip_range(action.timeout_s, 1.0, 30.0)

        elif isinstance(action, ArmMoveDeltaAction):
            action.params.front_cm = self._clip_range(action.params.front_cm, -5.0, 5.0)
            action.params.left_cm = self._clip_range(action.params.left_cm, -5.0, 5.0)
            action.params.up_cm = self._clip_range(action.params.up_cm, -5.0, 5.0)
            action.params.wrist_delta_deg = self._clip_range(action.params.wrist_delta_deg, -20.0, 20.0)
            action.params.speed = self._clip_range(action.params.speed, 0.10, 0.35)
            action.device = "p4"
            action.channel = "arm"
            action.timeout_s = self._clip_range(action.timeout_s, 1.0, 30.0)

        elif isinstance(action, ArmMoveXYZAction):
            action.params.target_xyz_m = tuple(
                self._clip_range(float(value), -1.0, 1.0) for value in action.params.target_xyz_m
            )
            action.params.speed = self._clip_range(action.params.speed, 0.10, 0.35)
            action.device = "p4"
            action.channel = "arm"
            action.timeout_s = self._clip_range(action.timeout_s, 1.0, 30.0)

        elif action.type == TimelineActionType.ARM_INIT_POSE:
            action.params.wait_first_s = self._clip_range(action.params.wait_first_s, 0.0, 10.0)
            action.device = "p4"
            action.channel = "arm"
            action.timeout_s = self._clip_range(action.timeout_s, 1.0, 30.0)

        elif isinstance(action, WaitAction):
            action.params.duration_s = self._clip_range(action.params.duration_s, 0.0, 60.0)
            action.device = "local"
            action.channel = "scheduler"
            action.timeout_s = max(0.1, min(action.timeout_s, action.params.duration_s + 5.0))

        elif isinstance(action, CheckpointAction):
            action.device = "local"
            action.channel = "vision"
            action.timeout_s = self._clip_range(action.timeout_s, 1.0, 120.0)
            action.expected_frame.bbox = self._clip_bbox(action.expected_frame.bbox)
            action.expected_frame.target_class = action.expected_frame.target_class or "person"
            action.expected_frame.target_id = action.expected_frame.target_id or "main_actor"
            action.servo.max_iters = int(self._clip_range(float(action.servo.max_iters), 1.0, 30.0))

        elif isinstance(action, FollowModeAction):
            action.device = "local"
            action.channel = "vision"
            action.duration_s = self._clip_range(action.duration_s, 0.5, 60.0)
            action.timeout_s = self._clip_range(action.timeout_s, action.duration_s, action.duration_s + 30.0)
            action.target_frame.bbox = self._clip_bbox(action.target_frame.bbox)
            action.target_frame.target_class = action.target_frame.target_class or "person"
            action.target_frame.target_id = action.target_frame.target_id or "main_actor"
            action.servo.max_iters = int(self._clip_range(float(action.servo.max_iters), 1.0, 300.0))

        return action

    @staticmethod
    def _clip_lighting_entry(entry: LightingPlanEntry) -> LightingPlanEntry:
        if entry.start_at_s is not None:
            entry.start_at_s = max(0.0, float(entry.start_at_s))
        if not entry.description.strip():
            entry.description = "默认中性光、中等强度、正面中光。"
        return entry

    @classmethod
    def _normalize_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        """Fill missing TimelineScript defaults before schema validation."""
        normalized = dict(payload)

        normalized.setdefault("name", cls._normalize_name(str(normalized.get("title") or "timeline_script")))
        normalized.setdefault("version", "2.0")
        normalized.setdefault("mode", "timeline")

        normalized.setdefault("summary", "按时间轴执行拍摄动作，并提供画面检查和打光方案。")

        normalized.setdefault("timeline", [])
        normalized.setdefault("lighting_plan", default_lighting_plan())
        return normalized

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

    @staticmethod
    def _clip_range(value: float, minimum: float, maximum: float) -> float:
        return round(max(minimum, min(maximum, float(value))), 4)

    @staticmethod
    def _clip_bbox(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        cx, cy, width, height = (float(value) for value in bbox)
        return (
            round(max(0.0, min(1.0, cx)), 4),
            round(max(0.0, min(1.0, cy)), 4),
            round(max(0.01, min(1.0, width)), 4),
            round(max(0.01, min(1.0, height)), 4),
        )

    @staticmethod
    def _sanitize_action_description(action_type: str, description: str) -> str:
        description = description.strip() or f"{action_type} 动作。"
        if str(action_type) != "follow_mode":
            description = re.sub(r"实时?跟拍|跟随|tracking|following", "开环调整", description, flags=re.IGNORECASE)
        return description

    @staticmethod
    def _normalize_name(value: str) -> str:
        value = value.strip().lower()
        value = re.sub(r"[^a-z0-9_\-]+", "_", value)
        value = re.sub(r"_+", "_", value).strip("_")
        return value or "timeline_script"

    @staticmethod
    def _safe_default_payload() -> dict[str, Any]:
        return {
            "name": "safe_default_timeline",
            "version": "2.0",
            "mode": "timeline",
            "summary": "执行保守的小幅开环机位调整，并提供默认中性打光方案。",
            "timeline": [
                {
                    "id": "a1",
                    "type": "arm_init_pose",
                    "start_at_s": 0.0,
                    "device": "p4",
                    "channel": "arm",
                    "params": {"wait_first_s": 2.0},
                    "timeout_s": 10,
                    "blocking": True,
                    "on_fail": "stop_all",
                    "description": "机械臂回到准备位。",
                },
                {
                    "id": "w1",
                    "type": "wait",
                    "start_after": ["a1"],
                    "device": "local",
                    "channel": "scheduler",
                    "params": {"duration_s": 0.5},
                    "timeout_s": 2,
                    "blocking": True,
                    "on_fail": "continue",
                    "description": "等待设备稳定。",
                },
            ],
            "lighting_plan": default_lighting_plan(),
        }
