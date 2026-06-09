"""TimelineScript -> S31/P4 command protocol translator.

This module is the middle adaptation layer between:

    Top-level Agent TimelineScript
        ↓
    Lower hardware protocol for S31 / P4

It does not open TCP sockets and does not execute hardware commands. It only
converts one high-level timeline action into one or more lower-layer command
payloads or local scheduler markers.

Current board mapping:
    s31: camera car ESP32-S3, controls base + lift, TCP port 2345
    p4 : ESP32-P4, forwards raw RoArm command, TCP port 2346

Lighting, checkpoint, and follow_mode are kept as local markers for now.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from runtime.arm_command_translator import ArmCommandTranslator
from runtime.arm_safety import ArmSafetyChecker


def compact_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def ensure_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def to_plain_dict(value: Any) -> dict[str, Any]:
    """Convert a pydantic model or plain dict into a plain dict."""
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    raise TypeError(f"Expected dict or pydantic model, got {type(value)!r}")


@dataclass
class TranslationItem:
    """One translated scheduler item."""

    kind: str
    cmd_id: str = ""
    board_id: str | None = None
    port: int | None = None
    payload: dict[str, Any] | None = None
    note: str | None = None
    source_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": self.kind,
            "cmd_id": self.cmd_id,
            "source_type": self.source_type,
        }
        if self.board_id is not None:
            data["board_id"] = self.board_id
        if self.port is not None:
            data["port"] = self.port
        if self.payload is not None:
            data["payload"] = self.payload
        if self.note is not None:
            data["note"] = self.note
        return data


class TimelineCommandTranslator:
    """Translate TimelineScript actions into the agreed S31/P4 command protocol."""

    def __init__(self) -> None:
        self.arm = ArmCommandTranslator()
        self.arm_safety = ArmSafetyChecker.from_config_file("config.yaml")

    def translate_action(self, action: Any) -> list[TranslationItem]:
        action_dict = to_plain_dict(action)
        action_type = str(action_dict.get("type", ""))
        cmd_id = str(action_dict.get("id", ""))
        params = action_dict.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        if action_type == "base_longitudinal":
            return [
                self._send_item(
                    cmd_id=cmd_id,
                    board_id="s31",
                    port=2345,
                    device="base",
                    action="move_longitudinal",
                    params={
                        "distance_m": round(float(params.get("distance_m", 0.0)), 4),
                        "speed_m_s": round(float(params.get("speed_m_s", 0.0)), 4),
                    },
                    source_type=action_type,
                )
            ]

        if action_type == "base_rotate":
            return [
                self._send_item(
                    cmd_id=cmd_id,
                    board_id="s31",
                    port=2345,
                    device="base",
                    action="rotate",
                    params={
                        "angle_deg": round(float(params.get("angle_deg", 0.0)), 3),
                        "angular_speed_rad_s": round(float(params.get("angular_speed_rad_s", 0.0)), 4),
                        "radius_m": 0.0,
                    },
                    source_type=action_type,
                )
            ]

        if action_type == "lift_delta":
            return [
                self._send_item(
                    cmd_id=cmd_id,
                    board_id="s31",
                    port=2345,
                    device="lift",
                    action="move_delta",
                    params={
                        "delta_cm": round(float(params.get("delta_cm", 0.0)), 3),
                    },
                    source_type=action_type,
                )
            ]

        if action_type == "arm_init_pose":
            raw = ensure_newline(self.arm.build_init_pose_command(update_cached_pose=True))
            return self._send_arm_raw_item(
                cmd_id=cmd_id,
                raw_command=raw,
                source_type=action_type,
            )

        if action_type == "arm_preset":
            preset = str(params.get("preset", "mid"))
            raw = self.arm_safety.get_preset_raw_command(preset)
            if raw is None:
                return [
                    TranslationItem(
                        kind="warning",
                        cmd_id=cmd_id,
                        board_id="p4",
                        port=2346,
                        note=f"[ARM SAFETY] unknown arm preset: {preset!r}; no command generated",
                        source_type=action_type,
                    )
                ]
            return self._send_arm_raw_item(
                cmd_id=cmd_id,
                raw_command=raw,
                source_type=action_type,
            )

        if action_type == "arm_move_delta":
            raw = ensure_newline(
                self.arm.build_delta_goal_cm(
                    front_cm=float(params.get("front_cm", 0.0)),
                    left_cm=float(params.get("left_cm", 0.0)),
                    up_cm=float(params.get("up_cm", 0.0)),
                    wrist_delta_deg=float(params.get("wrist_delta_deg", 0.0)),
                    target_t_rad=params.get("target_t_rad"),
                    speed=float(params.get("speed", 0.25)),
                    update_cached_pose=False,
                )
            )
            return self._send_arm_raw_item(
                cmd_id=cmd_id,
                raw_command=raw,
                source_type=action_type,
            )

        if action_type == "arm_move_xyz":
            target_xyz_m = params.get("target_xyz_m", (0.15, 0.0, 0.20))
            raw = ensure_newline(
                self.arm.build_absolute_goal_m(
                    target_xyz_m=target_xyz_m,
                    speed=float(params.get("speed", 0.25)),
                    t_rad=params.get("target_t_rad"),
                    update_cached_pose=False,
                )
            )
            return self._send_arm_raw_item(
                cmd_id=cmd_id,
                raw_command=raw,
                source_type=action_type,
            )

        if action_type == "wait":
            duration_s = float(params.get("duration_s", 0.0))
            return [
                TranslationItem(
                    kind="local",
                    cmd_id=cmd_id,
                    note=f"wait locally for {duration_s:.3f}s",
                    source_type=action_type,
                )
            ]

        if action_type == "checkpoint":
            expected_frame = action_dict.get("expected_frame", {})
            servo = action_dict.get("servo", {})
            return [
                TranslationItem(
                    kind="local",
                    cmd_id=cmd_id,
                    note=(
                        "checkpoint: pause timeline, run YOLO expected_frame evaluation, "
                        f"expected_frame={compact_json(expected_frame) if isinstance(expected_frame, dict) else expected_frame}, "
                        f"servo={compact_json(servo) if isinstance(servo, dict) else servo}"
                    ),
                    source_type=action_type,
                )
            ]

        if action_type == "follow_mode":
            target_frame = action_dict.get("target_frame", {})
            servo = action_dict.get("servo", {})
            duration_s = float(action_dict.get("duration_s", 0.0))
            return [
                TranslationItem(
                    kind="local",
                    cmd_id=cmd_id,
                    note=(
                        f"follow_mode: run closed-loop visual servo for {duration_s:.3f}s, "
                        f"target_frame={compact_json(target_frame) if isinstance(target_frame, dict) else target_frame}, "
                        f"servo={compact_json(servo) if isinstance(servo, dict) else servo}"
                    ),
                    source_type=action_type,
                )
            ]

        return [
            TranslationItem(
                kind="warning",
                cmd_id=cmd_id,
                note=f"unsupported timeline action type: {action_type!r}",
                source_type=action_type,
            )
        ]

    def translate_lighting(self, light: Any) -> TranslationItem:
        light_dict = to_plain_dict(light)
        return TranslationItem(
            kind="lighting",
            cmd_id=str(light_dict.get("id", "")),
            note=compact_json(light_dict),
            source_type="lighting_plan",
        )

    def _send_arm_raw_item(
        self,
        *,
        cmd_id: str,
        raw_command: str,
        source_type: str,
    ) -> list[TranslationItem]:
        safety = self.arm_safety.sanitize_raw_command(raw_command, source_type=source_type)

        if not safety.ok:
            return [
                TranslationItem(
                    kind="warning",
                    cmd_id=cmd_id,
                    board_id="p4",
                    port=2346,
                    note=(
                        f"[ARM SAFETY REJECTED] cmd_id={cmd_id}, "
                        f"source_type={source_type}, reason={safety.reason}, "
                        f"raw_command={raw_command.strip()!r}"
                    ),
                    source_type=source_type,
                )
            ]

        safe_raw = ensure_newline(safety.raw_command or raw_command)
        self._sync_arm_cache_from_safe_raw(safe_raw)

        if safety.adjusted:
            print(f"[ARM SAFETY ADJUSTED] cmd_id={cmd_id} {safety.reason}")
        elif safety.reason.startswith("[WARN_ONLY]"):
            print(f"[ARM SAFETY WARNING] cmd_id={cmd_id} {safety.reason}")
        elif "disabled by config" in safety.reason:
            print(f"[ARM SAFETY OFF] cmd_id={cmd_id} safety check disabled by config")

        return [
            self._send_item(
                cmd_id=cmd_id,
                board_id="p4",
                port=2346,
                device="arm",
                action="forward_raw",
                params={"raw_command": safe_raw},
                source_type=source_type,
            )
        ]

    def _sync_arm_cache_from_safe_raw(self, raw_command: str) -> None:
        try:
            cmd = json.loads(raw_command.strip())
        except Exception:
            return

        if cmd.get("T") == 100:
            self.arm.set_cached_pose_mm(x_mm=150.0, y_mm=0.0, z_mm=200.0, t_rad=3.14)
            return

        if cmd.get("T") == 104:
            self.arm.set_cached_pose_mm(
                x_mm=float(cmd.get("x", self.arm.pose.x_mm)),
                y_mm=float(cmd.get("y", self.arm.pose.y_mm)),
                z_mm=float(cmd.get("z", self.arm.pose.z_mm)),
                t_rad=float(cmd.get("t", self.arm.pose.t_rad)),
            )

    @staticmethod
    def _send_item(
        cmd_id: str,
        board_id: str,
        port: int,
        device: str,
        action: str,
        params: dict[str, Any],
        source_type: str,
    ) -> TranslationItem:
        payload = {
            "type": "cmd",
            "cmd_id": cmd_id,
            "board_id": board_id,
            "device": device,
            "action": action,
            "params": params,
        }
        return TranslationItem(
            kind="send",
            cmd_id=cmd_id,
            board_id=board_id,
            port=port,
            payload=payload,
            source_type=source_type,
        )


def expected_ack_for_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the ACK shape expected from S31/P4 after a command finishes."""
    return {
        "type": "ack",
        "ok": True,
        "cmd_id": payload.get("cmd_id", ""),
        "board_id": payload.get("board_id", ""),
        "device": payload.get("device", ""),
        "action": payload.get("action", ""),
        "status": "done",
        "duration_ms": 0,
        "msg": "done",
    }
