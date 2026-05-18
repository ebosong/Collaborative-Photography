"""Prompt assembly for strict JSON-only executable filming scripts."""

from __future__ import annotations

import json


class PromptBuilder:
    """Build the final planner prompt from instruction plus retrieved context."""

    def build(self, user_instruction: str, retrieved_context: dict[str, list[str]]) -> str:
        """Construct a JSON-only planning prompt with explicit output rules."""
        context_json = json.dumps(retrieved_context, ensure_ascii=False, indent=2)
        canonical_format = self._canonical_format()

        return (
            "You are writing an executable filming command script for a single-camera robot called CamBot.\n"
            "The JSON must be a complete ordered script. Each item in commands is one lower-level control command "
            "that will be sent to the wrapped runtime controller in order.\n"
            "Use only these command targets: base, lift, arm, wait.\n"
            "Use only these command actions: connect, move, move_to, move_by, preset, stop, wait.\n"
            "For base.move include linear_x, angular_z, and duration_s.\n"
            "For lift.move_to include height_m. For lift.move_by include delta_m. For arm.preset include preset.\n"
            "Use phase and description so a non-technical user can understand each filming action.\n"
            "Always include preparation commands and explicit stop commands for base, lift, and arm at the end.\n"
            "Keep speeds conservative and durations short unless the user clearly asks otherwise.\n"
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
            "You are revising an executable filming command script for CamBot.\n"
            "Use only the current JSON plan, the latest user feedback, and retrieved local knowledge.\n"
            "The output must remain a complete ordered command script, not a partial patch.\n"
            "Each item in commands is one lower-level control command that will be sent to the wrapped runtime "
            "controller in order.\n"
            "Apply the user's requested change conservatively and keep unrelated commands stable when possible.\n"
            "Use only these command targets: base, lift, arm, wait.\n"
            "Use only these command actions: connect, move, move_to, move_by, preset, stop, wait.\n"
            "Always keep explicit stop commands for base, lift, and arm at the end.\n"
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
                "title": "Smooth centered follow shot",
                "summary": "逐条下发底盘、升降和机械臂控制指令，完成稳定中景跟拍。",
                "total_duration_s": 8.0,
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
                    "height_m": 1.2,
                    "description": "升降调整到中景跟拍高度。",
                },
                {
                    "id": "cmd_06",
                    "phase": "跟拍动作",
                    "target": "base",
                    "action": "move",
                    "linear_x": 0.18,
                    "angular_z": 0.0,
                    "duration_s": 6.0,
                    "description": "底盘低速向前移动，保持主体稳定跟拍。",
                },
                {
                    "id": "cmd_07",
                    "phase": "结束动作",
                    "target": "base",
                    "action": "stop",
                    "description": "停止底盘运动。",
                },
                {
                    "id": "cmd_08",
                    "phase": "结束动作",
                    "target": "lift",
                    "action": "stop",
                    "description": "停止升降运动。",
                },
                {
                    "id": "cmd_09",
                    "phase": "结束动作",
                    "target": "arm",
                    "action": "stop",
                    "description": "停止机械臂动作。",
                },
            ],
        }
