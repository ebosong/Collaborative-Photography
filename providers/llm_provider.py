"""LLM provider abstraction with a safe mock fallback for local MVP runs."""

from __future__ import annotations

import json
import logging
from typing import Any


class LLMProvider:
    """Encapsulates provider setup so planning code stays simple."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

    def build_chat_model(self) -> Any:
        """Build an OpenAI-compatible chat model for Qwen or similar providers."""
        from langchain_openai import ChatOpenAI

        llm_config = self.config["llm"]
        api_key = llm_config.get("api_key", "")
        base_url = llm_config.get("base_url", "")
        model = llm_config["model"]

        if not api_key or not base_url:
            raise RuntimeError("Missing Qwen/OpenAI-compatible API configuration in config/default.yaml.")

        return ChatOpenAI(
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=float(llm_config.get("temperature", 0.1)),
            timeout=float(llm_config.get("timeout_s", 30)),
        )

    def generate(self, prompt: str) -> str:
        """Generate raw planner text using the configured provider or mock mode."""
        use_mock = (
            self.config["app"].get("mock_mode", True)
            and self.config["llm"].get("use_mock_when_unconfigured", True)
        )

        try:
            model = self.build_chat_model()
        except Exception as exc:
            if not use_mock:
                raise
            self.logger.warning("Falling back to mock LLM output: %s", exc)
            return self._mock_response()

        response = model.invoke(prompt)
        return getattr(response, "content", str(response))

    @staticmethod
    def _mock_response() -> str:
        """Return a valid deterministic JSON plan for offline development."""
        payload = {
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
        return json.dumps(payload, ensure_ascii=False)
