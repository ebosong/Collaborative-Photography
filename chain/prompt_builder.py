"""Prompt assembly for strict JSON-only TimelineScript filming plans."""

from __future__ import annotations

import json


class PromptBuilder:
    """Build planner prompts for the Timeline + Checkpoint + Lighting protocol."""

    def build(self, user_instruction: str, retrieved_context: dict[str, list[str]]) -> str:
        """Construct a JSON-only planning prompt with explicit output rules."""
        context_json = json.dumps(retrieved_context, ensure_ascii=False, indent=2)
        canonical_format = self._canonical_format()

        return (
            "You are the top-level CamBot filming Agent. You output one strict JSON TimelineScript.\n"
            "The lower runtime will parse the TimelineScript and translate it to S3/P4/YOLO/lighting systems.\n"
            "You must NOT output markdown, prose, comments, native S3/P4 commands, or raw arm commands such as {\"T\":100} or {\"T\":104}.\n"
            "\n"
            "Required top-level JSON fields:\n"
            "- name: english or pinyin identifier without spaces.\n"
            "- version: exactly \"2.0\".\n"
            "- mode: exactly \"timeline\".\n"
            "- summary: short Chinese summary for the user.\n"
            "- timeline: array of high-level actions.\n"
            "- lighting_plan: array of lighting intent entries; include a default entry even when the user did not request lighting.\n"
            "\n"
            "Supported timeline action types:\n"
            "- base_longitudinal: S3 base open-loop forward/backward. params: distance_m, speed_m_s.\n"
            "- base_rotate: S3 base open-loop in-place turn. params: angle_deg, angular_speed_rad_s.\n"
            "- lift_delta: S3 lift relative move. params: delta_cm.\n"
            "- arm_init_pose: P4 arm preparation pose. params: wait_first_s.\n"
            "- arm_move_delta: P4 arm relative move. params: front_cm, left_cm, up_cm, wrist_delta_deg, speed.\n"
            "- arm_move_xyz: optional P4 absolute target. params: target_xyz_m, target_t_rad, speed. Prefer arm_move_delta unless absolute position is clearly needed.\n"
            "- wait: local wait. params: duration_s.\n"
            "- checkpoint: local vision pause/check. Include expected_frame and servo; do not output correction actions.\n"
            "- follow_mode: local continuous vision follow for duration_s. Include target_frame and servo; do not output each correction action.\n"
            "\n"
            "Scheduling rules:\n"
            "- Every timeline item must have a globally unique id.\n"
            "- Use start_after for ordinary ordered actions and for checkpoint dependencies.\n"
            "- Use start_at_s only for earliest start time or parallel/staggered actions.\n"
            "- If both start_at_s and start_after appear, both conditions must be satisfied.\n"
            "- start_after references must point to existing timeline ids.\n"
            "\n"
            "Device/channel rules:\n"
            "- base_longitudinal/base_rotate: device=s3, channel=base.\n"
            "- lift_delta: device=s3, channel=lift.\n"
            "- arm_init_pose/arm_move_delta/arm_move_xyz: device=p4, channel=arm.\n"
            "- wait: device=local, channel=scheduler.\n"
            "- checkpoint/follow_mode: device=local, channel=vision.\n"
            "\n"
            "Vision rules:\n"
            "- checkpoint pauses future timeline work, checks YOLO framing, and lets the lower visual servo generate corrections.\n"
            "- checkpoint expected_frame uses bbox_format=\"cxcywh_norm\" and bbox=[cx, cy, w, h].\n"
            "- Use default target person frame unless the user clearly asks otherwise: bbox [0.5, 0.52, 0.35, 0.65].\n"
            "- checkpoint servo default: max_iters=8, allow_base=true, allow_lift=true, allow_arm=false.\n"
            "- follow_mode is the only action that may be described as 跟拍 or 跟随.\n"
            "- Ordinary base_longitudinal/base_rotate actions are finite open-loop movement; do not call them real-time tracking/following.\n"
            "\n"
            "Lighting rules:\n"
            "- lighting_plan only states lighting intent, not lighting-car paths, UWB control, orbit radius, or concrete motion trajectories.\n"
            "- color_temperature enum: warm, cool, neutral.\n"
            "- intensity enum: strong, medium, weak.\n"
            "- azimuth enum: front, side, back.\n"
            "- height enum: bottom, middle, top.\n"
            "- If unspecified, output: neutral, medium, front, middle, id=light_default, start_at_s=0.0.\n"
            "\n"
            "Safety and output constraints:\n"
            "- Do not output stop actions. Stopping is handled by the PC scheduler and ACK logic.\n"
            "- Do not output low-level command wrappers such as device/action/cmd_id payloads.\n"
            "- Keep movements conservative: distance_m -0.5..0.5, speed_m_s 0.03..0.20, angle_deg -45..45, angular_speed_rad_s 0.05..0.35, lift delta -10..10 cm.\n"
            "- Prefer small steps: lift delta usually -3..3 cm; arm deltas usually -5..5 cm; wrist_delta_deg -20..20; arm speed 0.10..0.35.\n"
            "- Include timeout_s, blocking, on_fail or on_vision_fail, and a Chinese description for each timeline item.\n"
            "\n"
            "Use this schema shape and field names:\n"
            f"{json.dumps(canonical_format, ensure_ascii=False, indent=2)}\n"
            "Retrieved local context:\n"
            f"{context_json}\n"
            "User instruction:\n"
            f"{user_instruction}\n"
            "Now return one strict JSON object only."
        )

    def build_revision(
        self,
        current_plan: dict,
        user_feedback: str,
        retrieved_context: dict[str, list[str]],
    ) -> str:
        """Construct a JSON-only prompt for revising an existing TimelineScript."""
        context_json = json.dumps(retrieved_context, ensure_ascii=False, indent=2)
        current_json = json.dumps(current_plan, ensure_ascii=False, indent=2)

        return (
            "You are revising a CamBot TimelineScript. Return the complete revised strict JSON object, not a patch.\n"
            "Keep version exactly \"2.0\" and mode exactly \"timeline\".\n"
            "Keep unrelated timeline actions and lighting entries stable while applying the latest feedback.\n"
            "Do not output stop actions, low-level device/action/cmd_id payloads, or raw arm commands such as {\"T\":100} or {\"T\":104}.\n"
            "Only follow_mode may be described as 跟拍 or 跟随; ordinary base actions remain finite open-loop movement.\n"
            "lighting_plan must always be present and must use only warm/cool/neutral, strong/medium/weak, front/side/back, bottom/middle/top.\n"
            "Every timeline id must be unique, and every start_after reference must exist.\n"
            "Return strict JSON only. Do not return markdown, prose, comments, or text outside the JSON object.\n"
            "Current JSON plan:\n"
            f"{current_json}\n"
            "Retrieved local context:\n"
            f"{context_json}\n"
            "Latest user feedback:\n"
            f"{user_feedback}\n"
            "Now return the revised strict JSON object only."
        )

    @staticmethod
    def _canonical_format() -> dict:
        return {
            "name": "back_lower_checkpoint_warm_side_light",
            "version": "2.0",
            "mode": "timeline",
            "summary": "机器人先开环后退，再降低机位，随后检查人物构图，并使用暖光侧面中光。",
            "timeline": [
                {
                    "id": "b1",
                    "type": "base_longitudinal",
                    "start_at_s": 0.0,
                    "device": "s3",
                    "channel": "base",
                    "params": {
                        "distance_m": -0.2,
                        "speed_m_s": 0.1,
                    },
                    "timeout_s": 8,
                    "blocking": True,
                    "on_fail": "stop_all",
                    "description": "小车后退 20 cm。",
                },
                {
                    "id": "l1",
                    "type": "lift_delta",
                    "start_after": ["b1"],
                    "device": "s3",
                    "channel": "lift",
                    "params": {
                        "delta_cm": -2.0,
                    },
                    "timeout_s": 8,
                    "blocking": True,
                    "on_fail": "stop_all",
                    "description": "升降杆下降 2 cm。",
                },
                {
                    "id": "cp1",
                    "type": "checkpoint",
                    "start_after": ["l1"],
                    "device": "local",
                    "channel": "vision",
                    "expected_frame": {
                        "enabled": True,
                        "target_class": "person",
                        "target_id": "main_actor",
                        "bbox_format": "cxcywh_norm",
                        "bbox": [0.5, 0.52, 0.35, 0.65],
                        "tolerance": {
                            "center_x": 0.05,
                            "center_y": 0.05,
                            "width": 0.08,
                            "height": 0.10,
                        },
                    },
                    "servo": {
                        "max_iters": 8,
                        "allow_base": True,
                        "allow_lift": True,
                        "allow_arm": False,
                    },
                    "timeout_s": 30,
                    "blocking": True,
                    "on_vision_fail": "continue",
                    "description": "检查人物是否位于预期画面中部。",
                },
            ],
            "lighting_plan": [
                {
                    "id": "light1",
                    "start_at_s": 0.0,
                    "color_temperature": "warm",
                    "intensity": "medium",
                    "azimuth": "side",
                    "height": "middle",
                    "description": "暖光、中等强度、侧面中光。",
                }
            ],
        }
