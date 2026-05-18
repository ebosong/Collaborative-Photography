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
        """Return a valid deterministic JSON command script for offline development."""
        payload = LLMProvider._extract_current_plan(prompt) or {
            "script": {
                "title": "Mock centered follow script",
                "summary": "连接控制器，调整到中景高度，低速稳定跟拍后停止。",
                "total_duration_s": 6.0,
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
        text = prompt.lower()
        commands = payload.setdefault("commands", LLMProvider._default_commands())
        script = payload.setdefault("script", {})

        move_commands = [
            command
            for command in commands
            if command.get("target") == "base" and command.get("action") == "move"
        ]
        lift_commands = [
            command
            for command in commands
            if command.get("target") == "lift" and command.get("action") == "move_to"
        ]
        follow_command = move_commands[0] if move_commands else None

        if any(token in text for token in ["left", "左", "左侧", "左边"]):
            if follow_command is not None:
                follow_command["angular_z"] = 0.18
                follow_command["description"] = "底盘向前跟拍并轻微左转，让主体偏向画面左侧。"
            script["summary"] = "执行偏左构图的稳定跟拍动作。"
        elif any(token in text for token in ["right", "右", "右侧", "右边"]):
            if follow_command is not None:
                follow_command["angular_z"] = -0.18
                follow_command["description"] = "底盘向前跟拍并轻微右转，让主体偏向画面右侧。"
            script["summary"] = "执行偏右构图的稳定跟拍动作。"
        elif any(token in text for token in ["center", "居中", "中间", "中央"]):
            if follow_command is not None:
                follow_command["angular_z"] = 0.0
                follow_command["description"] = "底盘低速向前移动，保持主体居中稳定跟拍。"
            script["summary"] = "执行居中构图的稳定跟拍动作。"

        if any(token in text for token in ["closer", "close-up", "更近", "靠近", "近一点"]):
            if follow_command is not None:
                follow_command["linear_x"] = 0.26
                follow_command["description"] = (
                    f"{follow_command.get('description', '底盘跟拍')} 同时略微靠近主体。"
                )
        elif any(token in text for token in ["farther", "wider", "更远", "远一点", "广一点"]):
            if follow_command is not None:
                follow_command["linear_x"] = 0.10
                follow_command["description"] = (
                    f"{follow_command.get('description', '底盘跟拍')} 同时保持更宽距离。"
                )

        if any(token in text for token in ["higher", "raise", "抬高", "高一点"]):
            for command in lift_commands:
                command["height_m"] = 1.5
                command["description"] = "升降抬高到更高机位。"
        elif any(token in text for token in ["lower", "低一点", "降低"]):
            for command in lift_commands:
                command["height_m"] = 0.9
                command["description"] = "升降降低到更低机位。"

        if any(token in text for token in ["longer", "extend", "延长", "久一点"]):
            if follow_command is not None:
                follow_command["duration_s"] = 12.0
        elif any(token in text for token in ["shorter", "quicker", "缩短", "短一点", "快一点"]):
            if follow_command is not None:
                follow_command["duration_s"] = 4.0

        if any(token in text for token in ["slow", "慢", "稳一点", "更稳"]):
            if follow_command is not None:
                follow_command["linear_x"] = min(float(follow_command.get("linear_x", 0.18)), 0.12)
        elif any(token in text for token in ["fast", "快", "更快"]):
            if follow_command is not None:
                follow_command["linear_x"] = max(float(follow_command.get("linear_x", 0.18)), 0.28)

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
                "description": "底盘低速向前移动，保持主体居中稳定跟拍。",
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
        ]
