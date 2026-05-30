"""LLM provider abstraction with an open-loop mock fallback for local MVP runs."""

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
        self.logger.warning("Falling back to open-loop mock LLM output after live request failure: %s", exc)
        return self._mock_response(prompt)

    def generate(self, prompt: str) -> str:
        """Generate raw planner text using the configured provider or mock mode."""
        try:
            model = self.build_chat_model()
        except Exception as exc:
            if not self.should_use_mock():
                raise
            self.logger.warning("Falling back to open-loop mock LLM output: %s", exc)
            return self._mock_response(prompt)

        try:
            response = model.invoke(prompt)
            return getattr(response, "content", str(response))
        except Exception as exc:
            return self.handle_generation_error(exc, prompt=prompt)

    @staticmethod
    def _mock_response(prompt: str = "") -> str:
        """Return a valid deterministic open-loop JSON command script for offline development."""
        payload = LLMProvider._extract_current_plan(prompt) or {
            "script": {
                "title": "Mock open-loop camera motion script",
                "summary": "按顺序执行机械臂、升降杆和底盘的开环动作组合，用于测试拍摄机位变化。",
                "total_duration_s": 2.5,
            },
            "commands": LLMProvider._default_commands(),
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
        """Apply simple local text rules while keeping the mock plan open-loop."""
        text = prompt.lower()
        commands = payload.setdefault("commands", LLMProvider._default_commands())
        script = payload.setdefault("script", {})

        base_moves = [
            command for command in commands
            if command.get("target") == "base" and command.get("action") == "move"
        ]
        lift_moves = [
            command for command in commands
            if command.get("target") == "lift" and command.get("action") == "move_by"
        ]

        base_command = base_moves[0] if base_moves else None
        lift_command = lift_moves[0] if lift_moves else None

        # Base direction
        if base_command is not None:
            base_command.setdefault("angular_z", 0.0)
            base_command.setdefault("duration_s", 2.0)

            if any(token in text for token in ["后退", "退后", "backward", "back"]):
                base_command["linear_x"] = -0.10
                base_command["angular_z"] = 0.0
                base_command["duration_s"] = 2.0
                base_command["description"] = "底盘开环后退一小段距离。"
            elif any(token in text for token in ["前进", "向前", "forward"]):
                base_command["linear_x"] = 0.10
                base_command["angular_z"] = 0.0
                base_command["duration_s"] = 2.0
                base_command["description"] = "底盘开环前进一小段距离。"

            if any(token in text for token in ["左转", "turn left", "rotate left"]):
                base_command["linear_x"] = 0.0
                base_command["angular_z"] = 0.18
                base_command["duration_s"] = 1.5
                base_command["description"] = "底盘开环原地左转一小段角度。"
            elif any(token in text for token in ["右转", "turn right", "rotate right"]):
                base_command["linear_x"] = 0.0
                base_command["angular_z"] = -0.18
                base_command["duration_s"] = 1.5
                base_command["description"] = "底盘开环原地右转一小段角度。"

            if any(token in text for token in ["快", "faster", "fast"]):
                if abs(float(base_command.get("linear_x", 0.0))) > 0:
                    base_command["linear_x"] = 0.15 if base_command["linear_x"] > 0 else -0.15
            elif any(token in text for token in ["慢", "slower", "slow", "稳一点"]):
                if abs(float(base_command.get("linear_x", 0.0))) > 0:
                    base_command["linear_x"] = 0.06 if base_command["linear_x"] > 0 else -0.06

        # Lift direction
        if lift_command is not None:
            if any(token in text for token in ["下降", "降低", "低一点", "down", "lower"]):
                lift_command["delta_m"] = -0.02
                lift_command["description"] = "升降杆开环下降 2 cm。"
            elif any(token in text for token in ["上升", "升高", "高一点", "up", "raise"]):
                lift_command["delta_m"] = 0.02
                lift_command["description"] = "升降杆开环上升 2 cm。"

        script["title"] = "Mock open-loop camera motion script"
        script["summary"] = "按顺序执行普通开环动作组合；当前不包含实时跟拍或视觉闭环。"
        script["total_duration_s"] = round(
            sum(float(command.get("duration_s", 0.0) or 0.0) for command in commands),
            2,
        )

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

    @staticmethod
    def _default_commands() -> list[dict[str, Any]]:
        return [
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
                "description": "升降杆开环下降 2 cm。",
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
        ]
