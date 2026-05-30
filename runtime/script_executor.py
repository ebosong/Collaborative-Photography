from __future__ import annotations

import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any

from schemas.action_script_schema import (
    ActionScript,
    ActionType,
    ArmInitPoseAction,
    ArmMoveXYZAction,
    ArmMoveDeltaAction,
    ArmWristAction,
    BaseLateralAction,
    BaseLongitudinalAction,
    BaseRotateAction,
    FailStrategy,
    LiftDeltaAction,
    VisionFailStrategy,
    WaitAction,
)
from runtime.arm_adapter import ArmAdapter
from runtime.arm_command_translator import ArmCommandTranslator
from runtime.base_controller import BaseController
from runtime.frame_evaluator import FrameEvaluator
from runtime.lift_controller import LiftController
from runtime.p4_arm_controller import P4ArmController
from runtime.vision_detector import VisionDetector
from runtime.visual_servo_controller import VisualServoController


class ScriptExecutionError(RuntimeError):
    pass


class ScriptExecutor:
    """
    Execute action scripts step by step.

    Transport split:
    - base / lift actions -> ESP32-S3
    - arm actions -> PC translator -> ESP32-P4 -> UART passthrough -> arm MCU

    Vision closed loop:
    - after each action, if expected_frame is enabled:
      YOLO/mock detection -> bbox evaluation -> small-step correction using
      base_rotate / base_longitudinal / lift_delta.
    - first version uses MockVisionDetector. Later replace it with YOLO detector
      without changing the execution logic.
    """

    def __init__(
        self,
        repo_root: str | Path,
        arm_config: dict[str, Any],
        base_controller: BaseController | None = None,
        lift_controller: LiftController | None = None,
        arm_adapter: ArmAdapter | None = None,
        p4_arm_controller: P4ArmController | None = None,
        arm_translator: ArmCommandTranslator | None = None,
        vision_detector: VisionDetector | None = None,
        frame_evaluator: FrameEvaluator | None = None,
        visual_servo: VisualServoController | None = None,
        s3_connect_timeout_s: float | None = None,
        p4_connect_timeout_s: float | None = None,
    ) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self.repo_root = Path(repo_root)

        self.base = base_controller or BaseController()
        self.lift = lift_controller or LiftController()
        self.arm = arm_adapter or ArmAdapter(self.repo_root, arm_config)
        self.p4_arm = p4_arm_controller or P4ArmController()
        self.arm_translator = arm_translator or ArmCommandTranslator()

        self.vision = vision_detector or VisionDetector()
        self.frame_evaluator = frame_evaluator or FrameEvaluator()
        self.visual_servo = visual_servo or VisualServoController()

        self.s3_connect_timeout_s = float(
            s3_connect_timeout_s
            if s3_connect_timeout_s is not None
            else os.getenv("S3_CONNECT_TIMEOUT_S", "30")
        )
        self.p4_connect_timeout_s = float(
            p4_connect_timeout_s
            if p4_connect_timeout_s is not None
            else os.getenv("P4_CONNECT_TIMEOUT_S", "30")
        )

    # --------------------------
    # lifecycle
    # --------------------------
    def connect(self) -> None:
        self.base.connect()
        self.lift.connect()
        self.arm.connect()
        self.p4_arm.connect()
        self.logger.info("Script executor connected all controllers.")

    def close(self) -> None:
        try:
            self.base.close()
        finally:
            try:
                self.lift.close()
            finally:
                try:
                    self.arm.close()
                finally:
                    self.p4_arm.close()
        self.logger.info("Script executor closed all controllers.")

    def stop_all(self) -> None:
        self.logger.warning("STOP ALL triggered.")
        try:
            self.base.stop()
        finally:
            try:
                self.lift.stop()
            finally:
                try:
                    if self.p4_arm.has_client():
                        self.p4_arm.stop()
                    else:
                        self.arm.stop()
                except Exception:
                    self.arm.stop()

    # --------------------------
    # loading
    # --------------------------
    def load_script(self, path: str | Path) -> ActionScript:
        script_path = Path(path)
        with script_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        script = ActionScript.model_validate(data)
        self.logger.info("Loaded script '%s' with %d step(s).", script.name, len(script.sequence))
        return script

    # --------------------------
    # execution
    # --------------------------
    def execute_script(self, script: ActionScript) -> None:
        self._wait_for_required_clients(script)

        self.logger.info("Start executing script: %s", script.name)
        for index, action in enumerate(script.sequence, start=1):
            self.logger.info("Step %d/%d | %s | %s", index, len(script.sequence), action.id, action.type)
            try:
                self._execute_action(action)
                self._run_expected_frame_loop(action)
            except Exception as exc:
                self.logger.exception("Action failed: %s", action.id)
                if action.on_fail == FailStrategy.STOP_ALL:
                    self.stop_all()
                    raise ScriptExecutionError(f"Action {action.id} failed: {exc}") from exc
                if action.on_fail == FailStrategy.SKIP:
                    self.logger.warning("Skipping failed action: %s", action.id)
                    continue
                if action.on_fail == FailStrategy.CONTINUE:
                    self.logger.warning("Continuing after failed action: %s", action.id)
                    continue
        self.logger.info("Script finished successfully: %s", script.name)

    def _wait_for_required_clients(self, script: ActionScript) -> None:
        needs_s3 = any(
            action.type in {
                ActionType.BASE_LATERAL,
                ActionType.BASE_LONGITUDINAL,
                ActionType.BASE_ROTATE,
                ActionType.LIFT_DELTA,
            }
            for action in script.sequence
        )

        # Vision correction may need S3 even if the script action itself is arm/wait.
        if any(getattr(action, "expected_frame", None) and action.expected_frame.enabled for action in script.sequence):
            needs_s3 = True

        needs_p4 = any(
            action.type in {
                ActionType.ARM_INIT_POSE,
                ActionType.ARM_MOVE_XYZ,
                ActionType.ARM_MOVE_DELTA,
            }
            for action in script.sequence
        )

        if needs_s3:
            tcp_server = getattr(self.base, "tcp_server", None)
            if tcp_server is None:
                raise ScriptExecutionError("BaseController has no tcp_server. Cannot wait for ESP32-S3 client.")

            if tcp_server.has_client():
                self.logger.info("ESP32-S3 client already connected. Ready to execute S3 actions.")
            else:
                self.logger.info(
                    "This script may contain S3-controlled actions. Waiting up to %.1f s for ESP32-S3 TCP client...",
                    self.s3_connect_timeout_s,
                )
                ok = tcp_server.wait_for_client(timeout_s=self.s3_connect_timeout_s)
                if not ok:
                    raise ScriptExecutionError(
                        f"ESP32-S3 did not connect within {self.s3_connect_timeout_s:.1f}s."
                    )
                self.logger.info("ESP32-S3 TCP client connected.")

        if needs_p4:
            if self.p4_arm.has_client():
                self.logger.info("ESP32-P4 client already connected. Ready to execute arm actions.")
            else:
                self.logger.info(
                    "This script contains P4-controlled arm actions. Waiting up to %.1f s for ESP32-P4 TCP client...",
                    self.p4_connect_timeout_s,
                )
            ok = self.p4_arm.wait_for_client(timeout_s=self.p4_connect_timeout_s)
            if not ok:
                raise ScriptExecutionError(
                    f"ESP32-P4 did not connect and send ready within {self.p4_connect_timeout_s:.1f}s."
                )
            self.logger.info("ESP32-P4 TCP client ready.")

        if not needs_s3 and not needs_p4:
            self.logger.info("No TCP-controlled actions in this script. Skip client wait.")

    def _execute_action(self, action: Any) -> None:
        start_ts = time.monotonic()

        if action.type == ActionType.ARM_INIT_POSE:
            self._exec_arm_init_pose(action)
        elif action.type == ActionType.ARM_WRIST:
            self._exec_arm_wrist(action)
        elif action.type == ActionType.ARM_MOVE_XYZ:
            self._exec_arm_move_xyz(action)
        elif action.type == ActionType.ARM_MOVE_DELTA:
            self._exec_arm_move_delta(action)
        elif action.type == ActionType.LIFT_DELTA:
            self._exec_lift_delta(action)
        elif action.type == ActionType.BASE_LATERAL:
            self._exec_base_lateral(action)
        elif action.type == ActionType.BASE_LONGITUDINAL:
            self._exec_base_longitudinal(action)
        elif action.type == ActionType.BASE_ROTATE:
            self._exec_base_rotate(action)
        elif action.type == ActionType.WAIT:
            self._exec_wait(action)
        else:
            raise ValueError(f"Unsupported action type: {action.type}")

        elapsed = time.monotonic() - start_ts
        if elapsed > action.timeout_s:
            raise TimeoutError(
                f"Action {action.id} exceeded timeout: {elapsed:.2f}s > {action.timeout_s:.2f}s"
            )

    # --------------------------
    # vision closed-loop
    # --------------------------
    def _run_expected_frame_loop(self, action: Any) -> None:
        expected = getattr(action, "expected_frame", None)
        if expected is None or not expected.enabled:
            return

        self.logger.info(
            "VISION loop start | action=%s target_class=%s expected_bbox=%s",
            action.id,
            expected.target_class,
            expected.bbox,
        )

        if hasattr(self.vision, "reset_for_expected_frame"):
            self.vision.reset_for_expected_frame(expected)

        for iter_index in range(1, expected.servo.max_iters + 1):
            detection = self.vision.detect_target(expected)
            evaluation = self.frame_evaluator.evaluate(expected, detection)

            self.logger.info(
                "VISION iter %d/%d | found=%s ok=%s detected=%s errors=%s message=%s",
                iter_index,
                expected.servo.max_iters,
                evaluation.found,
                evaluation.ok,
                evaluation.detected_bbox,
                evaluation.errors,
                evaluation.message,
            )

            if evaluation.ok:
                self.logger.info("VISION target satisfied for action=%s", action.id)
                return

            correction_data = self.visual_servo.make_next_correction(
                expected_frame=expected,
                evaluation=evaluation,
                step_id_prefix=action.id,
                iter_index=iter_index,
            )

            if correction_data is None:
                self.logger.warning("VISION no correction action generated for action=%s", action.id)
                break

            correction_action = self._validate_correction_action(correction_data)
            self.logger.info(
                "VISION correction | %s | params=%s",
                correction_action.type,
                correction_action.params.model_dump(),
            )

            self._execute_action(correction_action)

            if hasattr(self.vision, "apply_correction_action"):
                self.vision.apply_correction_action(correction_action, expected)

            settle_s = float(expected.servo.settle_s)
            if settle_s > 0:
                time.sleep(settle_s)

        msg = f"VISION failed to satisfy expected frame after {expected.servo.max_iters} iterations for action={action.id}"
        if expected.on_vision_fail == VisionFailStrategy.STOP_ALL:
            raise ScriptExecutionError(msg)
        if expected.on_vision_fail == VisionFailStrategy.SKIP_CORRECTION:
            self.logger.warning("%s; skip correction and continue.", msg)
            return

        self.logger.warning("%s; continue by policy.", msg)

    def _validate_correction_action(self, correction_data: dict) -> Any:
        tmp_script = ActionScript.model_validate(
            {
                "name": "vision_correction",
                "version": "1.0",
                "sequence": [correction_data],
            }
        )
        return tmp_script.sequence[0]

    # --------------------------
    # arm actions
    # --------------------------
    def _exec_arm_init_pose(self, action: ArmInitPoseAction) -> None:
        self.logger.info("ARM init pose | via P4 raw forwarding")
        raw = self.arm_translator.build_init_pose_command()
        self.p4_arm.send_raw_command(raw)

        self.arm_translator.set_cached_pose_mm(
            x_mm=150.0,
            y_mm=0.0,
            z_mm=200.0,
            t_rad=3.14,
        )

        wait_s = float(action.params.wait_first_s)
        if wait_s > 0:
            self.logger.info("ARM init pose | local wait %.2f s for arm settle", wait_s)
            time.sleep(wait_s)

    def _exec_arm_wrist(self, action: ArmWristAction) -> None:
        pitch_deg = action.params.pitch_deg
        speed_deg_s = action.params.speed_deg_s

        self.logger.info(
            "ARM wrist move | pitch_deg=%.2f speed_deg_s=%.2f | local placeholder path",
            pitch_deg,
            speed_deg_s,
        )

        if hasattr(self.arm, "set_wrist_pitch"):
            self.arm.set_wrist_pitch(pitch_deg=pitch_deg, speed_deg_s=speed_deg_s)
        else:
            command = f"[ARM CMD] wrist_pitch pitch_deg={pitch_deg:.2f} speed_deg_s={speed_deg_s:.2f}"
            print(command)
            self.logger.info(command)
            time.sleep(abs(pitch_deg) / max(speed_deg_s, 1e-6))

    def _exec_arm_move_xyz(self, action: ArmMoveXYZAction) -> None:
        x_m, y_m, z_m = action.params.target_xyz_m
        speed = float(action.params.speed)
        target_t_rad = action.params.target_t_rad

        self.logger.info(
            "ARM xyz goal move | target=(%.3f, %.3f, %.3f) t=%s spd=%.3f | T=104 single command -> P4",
            x_m,
            y_m,
            z_m,
            "cached" if target_t_rad is None else f"{target_t_rad:.4f}",
            speed,
        )

        if action.params.start_xyz_m is not None:
            sx_m, sy_m, sz_m = action.params.start_xyz_m
            self.arm_translator.set_cached_pose_mm(
                x_mm=float(sx_m) * 1000.0,
                y_mm=float(sy_m) * 1000.0,
                z_mm=float(sz_m) * 1000.0,
            )
            self.logger.info(
                "ARM xyz goal move | cache reset from start_xyz_m=(%.3f, %.3f, %.3f)",
                sx_m,
                sy_m,
                sz_m,
            )

        raw = self.arm_translator.build_absolute_goal_m(
            target_xyz_m=[float(x_m), float(y_m), float(z_m)],
            speed=speed,
            t_rad=target_t_rad,
            update_cached_pose=True,
        )

        self.logger.info("ARM xyz goal move | raw=%s cached_pose=%s", raw, self.arm_translator.get_cached_pose())
        self.p4_arm.send_raw_command(raw)

    def _exec_arm_move_delta(self, action: ArmMoveDeltaAction) -> None:
        p = action.params

        self.logger.info(
            "ARM delta goal move | front_cm=%.2f left_cm=%.2f up_cm=%.2f wrist_delta_deg=%.2f target_t_rad=%s spd=%.3f | T=104 single command -> P4",
            p.front_cm,
            p.left_cm,
            p.up_cm,
            p.wrist_delta_deg,
            "None" if p.target_t_rad is None else f"{p.target_t_rad:.4f}",
            p.speed,
        )

        raw = self.arm_translator.build_delta_goal_cm(
            front_cm=p.front_cm,
            left_cm=p.left_cm,
            up_cm=p.up_cm,
            wrist_delta_deg=p.wrist_delta_deg,
            target_t_rad=p.target_t_rad,
            speed=p.speed,
            update_cached_pose=True,
        )

        self.logger.info("ARM delta goal move | raw=%s cached_pose=%s", raw, self.arm_translator.get_cached_pose())
        self.p4_arm.send_raw_command(raw)

    # --------------------------
    # lift actions
    # --------------------------
    def _exec_lift_delta(self, action: LiftDeltaAction) -> None:
        delta_cm = action.params.delta_cm
        delta_m = delta_cm / 100.0

        self.logger.info("LIFT delta move | delta_cm=%.2f", delta_cm)
        self.lift.move_by(delta_m)

    # --------------------------
    # base actions
    # --------------------------
    def _exec_base_lateral(self, action: BaseLateralAction) -> None:
        distance_m = action.params.distance_m
        speed_m_s = action.params.speed_m_s

        self.logger.info("BASE lateral | distance_m=%.3f speed_m_s=%.3f", distance_m, speed_m_s)

        if hasattr(self.base, "move_lateral"):
            self.base.move_lateral(distance_m=distance_m, speed_m_s=speed_m_s)
            return

        command = f"[BASE CMD] lateral distance_m={distance_m:.3f} speed_m_s={speed_m_s:.3f}"
        print(command)
        self.logger.info("%s (legacy fallback: no move_lateral API)", command)
        time.sleep(abs(distance_m) / max(speed_m_s, 1e-6))
        self.base.stop()

    def _exec_base_longitudinal(self, action: BaseLongitudinalAction) -> None:
        distance_m = action.params.distance_m
        speed_m_s = action.params.speed_m_s
        duration_s = abs(distance_m) / max(speed_m_s, 1e-6)

        self.logger.info(
            "BASE longitudinal | distance_m=%.3f speed_m_s=%.3f duration_s=%.3f",
            distance_m,
            speed_m_s,
            duration_s,
        )

        if hasattr(self.base, "move_longitudinal"):
            self.base.move_longitudinal(distance_m=distance_m, speed_m_s=speed_m_s)
            return

        linear_x = speed_m_s if distance_m >= 0 else -speed_m_s
        self.base.move(linear_x=linear_x, angular_z=0.0)
        time.sleep(duration_s)
        self.base.stop()

    def _exec_base_rotate(self, action: BaseRotateAction) -> None:
        radius_m = action.params.radius_m
        angular_speed_rad_s = action.params.angular_speed_rad_s
        angle_deg = action.params.angle_deg

        signed_w = angular_speed_rad_s if angle_deg >= 0 else -angular_speed_rad_s
        linear_x = abs(angular_speed_rad_s) * radius_m
        duration_s = math.radians(abs(angle_deg)) / max(abs(angular_speed_rad_s), 1e-6)

        self.logger.info(
            "BASE rotate | radius_m=%.3f angular_speed_rad_s=%.3f angle_deg=%.3f duration_s=%.3f",
            radius_m,
            signed_w,
            angle_deg,
            duration_s,
        )

        if hasattr(self.base, "rotate"):
            self.base.rotate(
                radius_m=radius_m,
                angular_speed_rad_s=abs(angular_speed_rad_s),
                angle_deg=angle_deg,
            )
            return

        self.base.move(linear_x=linear_x, angular_z=signed_w)
        time.sleep(duration_s)
        self.base.stop()

    # --------------------------
    # other
    # --------------------------
    def _exec_wait(self, action: WaitAction) -> None:
        duration_s = action.params.duration_s
        self.logger.info("WAIT | duration_s=%.3f", duration_s)
        time.sleep(duration_s)
