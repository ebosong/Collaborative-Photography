from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from runtime.arm_command_translator import ArmCommandTranslator
from runtime.base_controller import BaseController
from runtime.frame_evaluator import FrameEvaluator
from runtime.lift_controller import LiftController
from runtime.p4_arm_controller import P4ArmController
from runtime.vision_detector import VisionDetector
from runtime.visual_servo_controller import VisualServoController
from schemas.timeline_script_schema import (
    ArmInitPoseAction,
    ArmMoveDeltaAction,
    ArmMoveXYZAction,
    BaseLongitudinalAction,
    BaseRotateAction,
    CheckpointAction,
    FailStrategy,
    FollowModeAction,
    LiftDeltaAction,
    TimelineAction,
    TimelineScript,
    VisionFailStrategy,
    WaitAction,
)


class ScriptExecutionError(RuntimeError):
    pass


class ScriptExecutor:
    """
    Lower-layer executor draft for TimelineScript.

    The top-level Agent only emits abstract actions. This executor is the place
    where S3/P4 commands, ACK waiting, checkpoint YOLO checks, follow_mode loops,
    and future lighting-car conversion should live.
    """

    def __init__(
        self,
        repo_root: str | Path,
        arm_config: dict[str, Any] | None = None,
        base_controller: BaseController | None = None,
        lift_controller: LiftController | None = None,
        p4_arm_controller: P4ArmController | None = None,
        arm_translator: ArmCommandTranslator | None = None,
        vision_detector: VisionDetector | None = None,
        frame_evaluator: FrameEvaluator | None = None,
        visual_servo: VisualServoController | None = None,
        s3_connect_timeout_s: float = 30.0,
        p4_connect_timeout_s: float = 30.0,
    ) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self.repo_root = Path(repo_root)
        self.arm_config = arm_config or {}

        self.base = base_controller or BaseController()
        self.lift = lift_controller or LiftController()
        self.p4_arm = p4_arm_controller or P4ArmController()
        self.arm_translator = arm_translator or ArmCommandTranslator()
        self.vision = vision_detector or VisionDetector()
        self.frame_evaluator = frame_evaluator or FrameEvaluator()
        self.visual_servo = visual_servo or VisualServoController()
        self.s3_connect_timeout_s = float(s3_connect_timeout_s)
        self.p4_connect_timeout_s = float(p4_connect_timeout_s)

    def connect(self) -> None:
        self.base.connect()
        self.lift.connect()
        self.p4_arm.connect()
        self.logger.info("Timeline executor connected shared S3/P4 controllers.")

    def close(self) -> None:
        try:
            self.base.close()
        finally:
            try:
                self.lift.close()
            finally:
                self.p4_arm.close()
        self.logger.info("Timeline executor closed controllers.")

    def stop_all(self) -> None:
        self.logger.warning("STOP ALL triggered.")
        try:
            self.base.stop()
        finally:
            try:
                self.lift.stop()
            finally:
                if self.p4_arm.has_client():
                    self.p4_arm.stop()

    def load_script(self, path: str | Path) -> TimelineScript:
        script_path = Path(path)
        with script_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        script = TimelineScript.model_validate(data)
        self.logger.info("Loaded TimelineScript '%s' with %d action(s).", script.name, len(script.timeline))
        return script

    def execute_script(self, script: TimelineScript) -> None:
        """Execute actions when their start_after and start_at_s constraints are met."""
        self._wait_for_required_clients(script)
        completed: set[str] = set()
        started_at = time.monotonic()

        self.logger.info("Start executing TimelineScript: %s", script.name)
        while len(completed) < len(script.timeline):
            runnable = [
                action
                for action in script.timeline
                if action.id not in completed and self._can_start(action, completed, started_at)
            ]
            if not runnable:
                time.sleep(0.02)
                continue

            action = runnable[0]
            self.logger.info("Timeline action | %s | %s", action.id, action.type)
            try:
                self._execute_action(action)
            except Exception as exc:
                self.logger.exception("Timeline action failed: %s", action.id)
                if self._enum_value(getattr(action, "on_fail", None)) == FailStrategy.CONTINUE.value:
                    completed.add(action.id)
                    continue
                self.stop_all()
                raise ScriptExecutionError(f"Action {action.id} failed: {exc}") from exc
            completed.add(action.id)

        self.logger.info("TimelineScript finished successfully: %s", script.name)

    @staticmethod
    def _can_start(action: TimelineAction, completed: set[str], started_at: float) -> bool:
        if any(dep not in completed for dep in action.start_after):
            return False
        if action.start_at_s is not None and time.monotonic() - started_at < action.start_at_s:
            return False
        return True

    def _wait_for_required_clients(self, script: TimelineScript) -> None:
        needs_s3 = any(action.device == "s3" for action in script.timeline)
        needs_p4 = any(action.device == "p4" for action in script.timeline)

        if needs_s3:
            self.base.connect()
            self.lift.connect()
            tcp_server = getattr(self.base, "tcp_server", None)
            if tcp_server is not None and not tcp_server.has_client():
                self.logger.info("Waiting up to %.1fs for ESP32-S3 client.", self.s3_connect_timeout_s)
                tcp_server.wait_for_client(timeout_s=self.s3_connect_timeout_s)

        if needs_p4:
            self.p4_arm.connect()
            if not self.p4_arm.has_client():
                self.logger.info("Waiting up to %.1fs for ESP32-P4 client.", self.p4_connect_timeout_s)
                self.p4_arm.wait_for_client(timeout_s=self.p4_connect_timeout_s)

    def _execute_action(self, action: TimelineAction) -> None:
        start_ts = time.monotonic()

        if isinstance(action, BaseLongitudinalAction):
            self.base.move_longitudinal(
                distance_m=action.params.distance_m,
                speed_m_s=action.params.speed_m_s,
            )
        elif isinstance(action, BaseRotateAction):
            self.base.rotate(
                radius_m=0.0,
                angular_speed_rad_s=action.params.angular_speed_rad_s,
                angle_deg=action.params.angle_deg,
            )
        elif isinstance(action, LiftDeltaAction):
            self.lift.move_by(action.params.delta_cm / 100.0)
        elif isinstance(action, ArmInitPoseAction):
            raw = self.arm_translator.build_init_pose_command()
            self.p4_arm.send_raw_command(raw)
            if action.params.wait_first_s > 0:
                time.sleep(action.params.wait_first_s)
        elif isinstance(action, ArmMoveDeltaAction):
            raw = self.arm_translator.build_delta_goal_cm(
                front_cm=action.params.front_cm,
                left_cm=action.params.left_cm,
                up_cm=action.params.up_cm,
                wrist_delta_deg=action.params.wrist_delta_deg,
                target_t_rad=action.params.target_t_rad,
                speed=action.params.speed,
                update_cached_pose=True,
            )
            self.p4_arm.send_raw_command(raw)
        elif isinstance(action, ArmMoveXYZAction):
            raw = self.arm_translator.build_absolute_goal_m(
                target_xyz_m=action.params.target_xyz_m,
                speed=action.params.speed,
                t_rad=action.params.target_t_rad,
                update_cached_pose=True,
            )
            self.p4_arm.send_raw_command(raw)
        elif isinstance(action, WaitAction):
            time.sleep(action.params.duration_s)
        elif isinstance(action, CheckpointAction):
            self._run_checkpoint(action)
        elif isinstance(action, FollowModeAction):
            self._run_follow_mode(action)
        else:
            raise ValueError(f"Unsupported timeline action type: {action.type}")

        elapsed = time.monotonic() - start_ts
        if elapsed > action.timeout_s:
            raise TimeoutError(
                f"Action {action.id} exceeded timeout: {elapsed:.2f}s > {action.timeout_s:.2f}s"
            )

    def _run_checkpoint(self, action: CheckpointAction) -> None:
        if not action.expected_frame.enabled:
            self.logger.info("Checkpoint %s has expected_frame disabled.", action.id)
            return
        ok = self._run_visual_servo_loop(
            step_id_prefix=action.id,
            expected_frame=self._frame_with_servo(action.expected_frame, action.servo),
            max_iters=action.servo.max_iters,
            deadline_s=time.monotonic() + action.timeout_s,
        )
        if not ok and self._enum_value(action.on_vision_fail) == VisionFailStrategy.STOP_ALL.value:
            raise ScriptExecutionError(f"Checkpoint {action.id} failed vision validation.")

    def _run_follow_mode(self, action: FollowModeAction) -> None:
        expected_frame = self._frame_with_servo(action.target_frame, action.servo)
        deadline_s = time.monotonic() + action.duration_s
        self._run_visual_servo_loop(
            step_id_prefix=action.id,
            expected_frame=expected_frame,
            max_iters=action.servo.max_iters,
            deadline_s=deadline_s,
        )

    def _run_visual_servo_loop(
        self,
        step_id_prefix: str,
        expected_frame: Any,
        max_iters: int,
        deadline_s: float,
    ) -> bool:
        if hasattr(self.vision, "reset_for_expected_frame"):
            self.vision.reset_for_expected_frame(expected_frame)

        iter_index = 0
        while iter_index < max_iters and time.monotonic() <= deadline_s:
            iter_index += 1
            detection = self.vision.detect_target(expected_frame)
            evaluation = self.frame_evaluator.evaluate(expected_frame, detection)
            self.logger.info(
                "VISION %s iter %d/%d | found=%s ok=%s errors=%s",
                step_id_prefix,
                iter_index,
                max_iters,
                evaluation.found,
                evaluation.ok,
                evaluation.errors,
            )
            if evaluation.ok:
                return True

            correction_data = self.visual_servo.make_next_correction(
                expected_frame=expected_frame,
                evaluation=evaluation,
                step_id_prefix=step_id_prefix,
                iter_index=iter_index,
            )
            if correction_data is None:
                return False
            correction_action = TimelineScript.model_validate(
                {
                    "name": "vision_correction",
                    "version": "2.0",
                    "mode": "timeline",
                    "summary": "视觉伺服修正动作。",
                    "timeline": [self._normalize_correction_action(correction_data)],
                    "lighting_plan": [
                        {
                            "id": "light_default",
                            "start_at_s": 0.0,
                            "color_temperature": "neutral",
                            "intensity": "medium",
                            "azimuth": "front",
                            "height": "middle",
                            "description": "默认中性光、中等强度、正面中光。",
                        }
                    ],
                }
            ).timeline[0]
            self._execute_action(correction_action)
            if hasattr(self.vision, "apply_correction_action"):
                self.vision.apply_correction_action(correction_action, expected_frame)

        return False

    @staticmethod
    def _normalize_correction_action(data: dict[str, Any]) -> dict[str, Any]:
        action = dict(data)
        action.pop("note", None)
        action.setdefault("description", "视觉伺服小幅修正。")
        action.setdefault("blocking", True)
        action.setdefault("timeout_s", 5.0)
        action.setdefault("on_fail", "continue")
        if action.get("type") in {"base_longitudinal", "base_rotate"}:
            action.setdefault("device", "s3")
            action.setdefault("channel", "base")
            params = action.setdefault("params", {})
            params.pop("radius_m", None)
        elif action.get("type") == "lift_delta":
            action.setdefault("device", "s3")
            action.setdefault("channel", "lift")
        return action

    @staticmethod
    def _frame_with_servo(frame: Any, servo: Any) -> Any:
        """Adapt TimelineScript vision config to the visual-servo controller interface."""
        use_actions = []
        if servo.allow_base:
            use_actions.extend(["base_longitudinal", "base_rotate"])
        if servo.allow_lift:
            use_actions.append("lift_delta")
        if servo.allow_arm:
            use_actions.append("arm_move_delta")

        tolerance = SimpleNamespace(
            center_x=frame.tolerance.center_x,
            center_y=frame.tolerance.center_y,
            width=frame.tolerance.width,
            height=frame.tolerance.height,
            area=max(float(frame.tolerance.width) * float(frame.tolerance.height), 0.01),
        )
        servo_adapter = SimpleNamespace(
            max_iters=int(servo.max_iters),
            use_actions=use_actions,
            max_step=SimpleNamespace(longitudinal_m=0.05, rotate_deg=3.0, lift_cm=1.0),
            gain=SimpleNamespace(
                longitudinal_m_per_area=0.30,
                rotate_deg_per_norm_x=-12.0,
                lift_cm_per_norm_y=-6.0,
            ),
            direction_sign=SimpleNamespace(longitudinal=1.0, rotate=1.0, lift=1.0),
        )
        return SimpleNamespace(
            enabled=getattr(frame, "enabled", True),
            target_class=frame.target_class,
            target_id=frame.target_id,
            bbox_format=frame.bbox_format,
            bbox=frame.bbox,
            tolerance=tolerance,
            servo=servo_adapter,
        )

    @staticmethod
    def _enum_value(value: Any) -> str:
        return getattr(value, "value", str(value))
