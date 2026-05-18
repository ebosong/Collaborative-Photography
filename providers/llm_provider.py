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

    def should_use_mock(self) -> bool:
        """Return whether mock fallback is allowed for local/offline runs."""
        return (
            self.config["app"].get("mock_mode", True)
            and self.config["llm"].get("use_mock_when_unconfigured", True)
        )

    def build_chat_model(self) -> Any:
        """Build an OpenAI-compatible chat model for Qwen or similar providers."""
        from langchain_openai import ChatOpenAI

        llm_config = self.config["llm"]
        api_key = llm_config.get("api_key", "")
        base_url = llm_config.get("base_url", "")
        model = llm_config["model"]
        trust_env = bool(llm_config.get("trust_env", False))

        if not api_key or not base_url:
            raise RuntimeError("Missing Qwen/OpenAI-compatible API configuration in config/default.yaml.")

        client_kwargs = {
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "temperature": float(llm_config.get("temperature", 0.1)),
            "timeout": float(llm_config.get("timeout_s", 30)),
        }

        try:
            import httpx

            client_kwargs["http_client"] = httpx.Client(trust_env=trust_env)
            client_kwargs["http_async_client"] = httpx.AsyncClient(trust_env=trust_env)
        except Exception as exc:
            self.logger.info("httpx customization unavailable, using default client settings: %s", exc)

        return ChatOpenAI(**client_kwargs)

    def handle_generation_error(self, exc: Exception, prompt: str = "") -> str:
        """Return a mock plan when configured, otherwise re-raise the original error."""
        if not self.should_use_mock():
            raise exc
        self.logger.warning("Falling back to mock LLM output after live request failure: %s", exc)
        return self._mock_response(prompt)

    def generate(self, prompt: str) -> str:
        """Generate raw planner text using the configured provider or mock mode."""
        try:
            model = self.build_chat_model()
        except Exception as exc:
            if not self.should_use_mock():
                raise
            self.logger.warning("Falling back to mock LLM output: %s", exc)
            return self._mock_response(prompt)

        try:
            response = model.invoke(prompt)
            return getattr(response, "content", str(response))
        except Exception as exc:
            return self.handle_generation_error(exc, prompt=prompt)

    @staticmethod
    def _mock_response(prompt: str = "") -> str:
        """Return a valid deterministic JSON plan for offline development."""
        payload = LLMProvider._extract_current_plan(prompt) or {
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
        LLMProvider._apply_mock_feedback(payload, LLMProvider._extract_user_text(prompt))
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _extract_current_plan(prompt: str) -> dict[str, Any] | None:
        marker = "Current JSON plan:"
        start = prompt.find(marker)
        if start < 0:
            return None

        json_start = prompt.find("{", start + len(marker))
        if json_start < 0:
            return None

        depth = 0
        in_string = False
        escaped = False
        for index in range(json_start, len(prompt)):
            char = prompt[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(prompt[json_start : index + 1])
                    except json.JSONDecodeError:
                        return None
                    return data if isinstance(data, dict) else None
        return None

    @staticmethod
    def _apply_mock_feedback(payload: dict[str, Any], prompt: str) -> None:
        text = prompt.lower()
        shot_plan = payload.setdefault("shot_plan", {})
        safety_rules = payload.setdefault("safety_rules", {})

        if any(token in text for token in ["left", "左"]):
            shot_plan["subject_region"] = "left"
        elif any(token in text for token in ["right", "右"]):
            shot_plan["subject_region"] = "right"
        elif any(token in text for token in ["center", "中央", "中间", "居中"]):
            shot_plan["subject_region"] = "center"

        if any(token in text for token in ["closer", "close-up", "更近", "靠近", "近一点"]):
            shot_plan["distance_m"] = 1.4
            shot_plan["subject_scale_target"] = 0.55
        elif any(token in text for token in ["farther", "wider", "更远", "远一点", "广一点"]):
            shot_plan["distance_m"] = 3.0
            shot_plan["subject_scale_target"] = 0.3

        if any(token in text for token in ["higher", "raise", "抬高", "高一点"]):
            shot_plan["height_m"] = 1.5
        elif any(token in text for token in ["lower", "低一点", "降低"]):
            shot_plan["height_m"] = 0.9

        if any(token in text for token in ["longer", "extend", "延长", "久一点"]):
            shot_plan["duration_s"] = 12
        elif any(token in text for token in ["shorter", "quicker", "缩短", "短一点", "快一点"]):
            shot_plan["duration_s"] = 5

        if any(token in text for token in ["side", "侧", "侧前"]):
            shot_plan["template"] = "side_front_follow"
        elif any(token in text for token in ["safe", "稳定", "保守", "安全"]):
            shot_plan["template"] = "mid_follow_safe"

        if any(token in text for token in ["slow", "慢", "稳一点", "更稳"]):
            safety_rules["max_speed"] = 0.3
        elif any(token in text for token in ["fast", "快", "更快"]):
            safety_rules["max_speed"] = 0.5

    @staticmethod
    def _extract_user_text(prompt: str) -> str:
        for marker in ["Latest user feedback:", "User instruction:"]:
            start = prompt.find(marker)
            if start >= 0:
                content_start = start + len(marker)
                end = prompt.find("Now return", content_start)
                if end < 0:
                    end = len(prompt)
                return prompt[content_start:end]
        return prompt
