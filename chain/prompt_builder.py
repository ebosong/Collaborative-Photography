"""Prompt assembly for strict JSON-only filming plan generation."""

from __future__ import annotations

import json


class PromptBuilder:
    """Build the final planner prompt from instruction plus retrieved context."""

    def build(self, user_instruction: str, retrieved_context: dict[str, list[str]]) -> str:
        """Construct a JSON-only planning prompt with explicit output rules."""
        context_json = json.dumps(retrieved_context, ensure_ascii=False, indent=2)
        canonical_format = {
            "shot_plan": {
                "template": "mid_follow",
                "duration_s": 8,
                "distance_m": 2.2,
                "height_m": 1.2,
                "subject_region": "center",
                "subject_scale_target": 0.4,
            },
            "robot_task": {"name": "track_subject_with_framing"},
            "safety_rules": {
                "max_speed": 0.5,
                "min_distance": 0.8,
                "lost_target_action": "slow_stop_and_search",
            },
            "fallback": {"template": "mid_follow_safe"},
        }

        return (
            "You are planning a filming script for a single-camera robot called CamBot.\n"
            "Use the retrieved local knowledge to choose a minimal safe filming plan.\n"
            "The LLM is responsible only for high-level semantic planning.\n"
            "Never output hardware-level motor commands, actuator commands, trajectories, or code.\n"
            "Return strict JSON only.\n"
            "Do not return markdown.\n"
            "Do not return explanatory prose.\n"
            "Do not return any text before or after the JSON object.\n"
            "Use this exact schema shape and field names:\n"
            f"{json.dumps(canonical_format, ensure_ascii=False, indent=2)}\n"
            "Retrieved local context:\n"
            f"{context_json}\n"
            "User instruction:\n"
            f"{user_instruction}\n"
            "Now return one strict JSON object only."
        )
