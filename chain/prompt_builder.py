"""Prompt assembly for strict JSON-only executable open-loop filming scripts."""

from __future__ import annotations

import json


class PromptBuilder:
    """Build the final planner prompt from instruction plus retrieved context."""

    def build(self, user_instruction: str, retrieved_context: dict[str, list[str]]) -> str:
        """Construct a JSON-only planning prompt with explicit output rules."""
        context_json = json.dumps(retrieved_context, ensure_ascii=False, indent=2)
        canonical_format = self._canonical_format()

        return (
            "You are writing an executable OPEN-LOOP filming command script for a single-camera robot called CamBot.\n"
            "The current system does NOT have online visual tracking enabled inside this planner yet.\n"
            "Therefore, ordinary base movement must be treated as finite open-loop motion, not real-time following or tracking.\n"
            "Do NOT describe ordinary base movement as 跟拍, tracking, following, or keeping the subject centered unless the user explicitly asks for a future follow mode.\n"
            "\n"
            "Current supported mode:\n"
            "- open_loop_script: a finite ordered list of device actions executed one by one.\n"
            "\n"
            "Future modes, NOT enabled in this schema yet:\n"
            "- follow_mode: YOLO continuously measures subject error and outputs correction actions.\n"
            "- checkpoint: execution pauses, YOLO checks framing, then correction actions are generated.\n"
            "Do NOT output follow_mode or checkpoint commands in the current JSON.\n"
            "If the user asks for follow/tracking/checkpoint behavior, keep the current JSON open-loop and mention the intended idea only in description text.\n"
            "\n"
            "The JSON must be a complete ordered script. Each item in commands is one lower-level OPEN-LOOP control command.\n"
            "Use only these command targets: base, lift, arm, wait.\n"
            "Use only these command actions: connect, move, move_to, move_by, preset, stop, wait.\n"
            "\n"
            "Hardware mapping rules:\n"
            "- base.move is open-loop chassis motion. Use linear_x for straight forward/backward motion.\n"
            "- Positive linear_x means forward; negative linear_x means backward.\n"
            "- base.move with angular_z is open-loop in-place rotation.\n"
            "- Do not set linear_x and angular_z non-zero at the same time.\n"
            "- For base.move include linear_x, angular_z, and duration_s.\n"
            "- Prefer lift.move_by over lift.move_to, because the current lift controller is mainly relative-motion based.\n"
            "- For lift.move_by include delta_m. Positive delta_m means up; negative delta_m means down.\n"
            "- For lift.move_to include height_m only when the user clearly asks for an absolute height.\n"
            "- arm currently supports preset=ready for initialization. Do not generate complex arm trajectories unless explicitly requested.\n"
            "- For arm.preset include preset. Use preset=ready by default.\n"
            "- wait.wait can be used between actions.\n"
            "\n"
            "Safety rules:\n"
            "- Keep each open-loop movement small and conservative.\n"
            "- For base.move, prefer duration_s <= 3.0 and |linear_x| <= 0.15 unless the user clearly asks for a larger move.\n"
            "- For base rotation, prefer duration_s <= 2.0 and |angular_z| <= 0.25.\n"
            "- For lift.move_by, prefer |delta_m| <= 0.03.\n"
            "- Always include preparation commands and explicit stop commands for base, lift, and arm at the end.\n"
            "\n"
            "Use phase and description so a non-technical user can understand each filming action.\n"
            "Return strict JSON only. Do not return markdown, prose, comments, or text outside the JSON object.\n"
            "Use this exact schema shape and field names:\n"
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
        """Construct a JSON-only prompt for revising an existing command script."""
        context_json = json.dumps(retrieved_context, ensure_ascii=False, indent=2)
        current_json = json.dumps(current_plan, ensure_ascii=False, indent=2)

        return (
            "You are revising an executable OPEN-LOOP filming command script for CamBot.\n"
            "The current planner is only allowed to output finite open-loop device actions.\n"
            "Do NOT turn ordinary chassis movement into real-time following/tracking unless the user explicitly asks for future follow mode.\n"
            "The output must remain a complete ordered command script, not a partial patch.\n"
            "\n"
            "Use only these command targets: base, lift, arm, wait.\n"
            "Use only these command actions: connect, move, move_to, move_by, preset, stop, wait.\n"
            "\n"
            "Hardware rules:\n"
            "- base.move is open-loop. Use linear_x for straight motion, angular_z for in-place rotation.\n"
            "- Do not set linear_x and angular_z non-zero at the same time.\n"
            "- Prefer lift.move_by over lift.move_to.\n"
            "- arm currently supports preset=ready for initialization.\n"
            "- Keep movements conservative: base duration_s usually <= 3.0, |linear_x| usually <= 0.15, |lift.delta_m| usually <= 0.03.\n"
            "- Keep unrelated commands stable when applying user feedback.\n"
            "- Always keep explicit stop commands for base, lift, and arm at the end.\n"
            "\n"
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
            "script": {
                "title": "Open-loop camera motion script",
                "summary": "按顺序执行机械臂、升降杆和底盘的开环动作组合，用于测试拍摄机位变化。",
                "total_duration_s": 5.5,
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
                    "description": "连接升降杆控制器。",
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
                    "description": "机械臂回到准备位。",
                },
                {
                    "id": "cmd_05",
                    "phase": "机位调整",
                    "target": "lift",
                    "action": "move_by",
                    "delta_m": -0.02,
                    "description": "升降杆下降 2 cm，形成较低机位。",
                },
                {
                    "id": "cmd_06",
                    "phase": "机位调整",
                    "target": "base",
                    "action": "move",
                    "linear_x": -0.10,
                    "angular_z": 0.0,
                    "duration_s": 2.0,
                    "description": "底盘开环后退一小段距离。",
                },
                {
                    "id": "cmd_07",
                    "phase": "停顿",
                    "target": "wait",
                    "action": "wait",
                    "duration_s": 0.5,
                    "description": "等待设备稳定。",
                },
                {
                    "id": "cmd_08",
                    "phase": "结束动作",
                    "target": "base",
                    "action": "stop",
                    "description": "停止底盘运动。",
                },
                {
                    "id": "cmd_09",
                    "phase": "结束动作",
                    "target": "lift",
                    "action": "stop",
                    "description": "停止升降杆运动。",
                },
                {
                    "id": "cmd_10",
                    "phase": "结束动作",
                    "target": "arm",
                    "action": "stop",
                    "description": "停止机械臂动作。",
                },
            ],
        }
