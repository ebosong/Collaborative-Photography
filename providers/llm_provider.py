"""LLM provider abstraction with a TimelineScript mock fallback for local MVP runs."""

from __future__ import annotations

import json
import logging
import os
from typing import Any


class LLMProvider:
    """Encapsulates provider setup so planning code stays simple."""

    PROVIDER_DEFAULTS = {
        "qwen_openai_compatible": {
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen3.6-plus",
            "api_key_env": "QWEN_API_KEY",
        },
        "deepseek_openai_compatible": {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "api_key_env": "DEEPSEEK_API_KEY",
        },
        "deepseek": {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "api_key_env": "DEEPSEEK_API_KEY",
        },
    }

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
        """Build an OpenAI-compatible chat model for Qwen, DeepSeek, or similar providers."""
        from langchain_openai import ChatOpenAI

        llm_config = self._resolved_llm_config()
        provider = llm_config["provider"]
        api_key = llm_config.get("api_key", "")
        base_url = llm_config.get("base_url", "")
        model = llm_config["model"]
        trust_env = bool(llm_config.get("trust_env", False))

        if not api_key or not base_url:
            raise RuntimeError(
                "Missing OpenAI-compatible API configuration for "
                f"{provider!r}. Set api_key/base_url in config/default.yaml."
            )

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

    def _resolved_llm_config(self) -> dict[str, Any]:
        """Resolve the selected provider from hardcoded config plus optional env override."""
        root = dict(self.config.get("llm", {}))
        provider = str(root.get("provider") or "qwen_openai_compatible").strip().lower()
        defaults = dict(self.PROVIDER_DEFAULTS.get(provider, {}))

        profiles = root.get("providers", {})
        profile = {}
        if isinstance(profiles, dict) and isinstance(profiles.get(provider), dict):
            profile = dict(profiles[provider])

        common_keys = {
            "temperature",
            "timeout_s",
            "trust_env",
            "use_mock_when_unconfigured",
        }
        common = {key: root[key] for key in common_keys if key in root}
        flat = {
            key: value
            for key, value in root.items()
            if key not in {"providers"} and value not in {None, ""}
        }

        if profile:
            merged = {**defaults, **common, **profile}
        else:
            merged = {**defaults, **flat}
        merged["provider"] = provider

        api_key_env = str(merged.get("api_key_env") or "").strip()
        if api_key_env and os.getenv(api_key_env):
            merged["api_key"] = os.getenv(api_key_env)

        if provider.startswith("deepseek"):
            merged.setdefault("base_url", "https://api.deepseek.com")
            merged.setdefault("model", "deepseek-v4-flash")

        return merged

    def handle_generation_error(self, exc: Exception, prompt: str = "") -> str:
        """Return a mock plan when configured, otherwise re-raise the original error."""
        if not self.should_use_mock():
            raise exc
        self.logger.warning("Falling back to TimelineScript mock LLM output after live request failure: %s", exc)
        return self._mock_response(prompt)

    def generate(self, prompt: str) -> str:
        """Generate raw planner text using the configured provider or mock mode."""
        try:
            model = self.build_chat_model()
        except Exception as exc:
            if not self.should_use_mock():
                raise
            self.logger.warning("Falling back to TimelineScript mock LLM output: %s", exc)
            return self._mock_response(prompt)

        try:
            response = model.invoke(prompt)
            return getattr(response, "content", str(response))
        except Exception as exc:
            return self.handle_generation_error(exc, prompt=prompt)

    @staticmethod
    def _mock_response(prompt: str = "") -> str:
        """Return a valid deterministic TimelineScript for offline development."""
        payload = LLMProvider._extract_current_plan(prompt) or LLMProvider._default_timeline_script()
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
        """Apply simple local text rules while keeping the mock plan valid."""
        text = prompt.lower()
        payload.setdefault("version", "2.0")
        payload.setdefault("mode", "timeline")
        payload.setdefault("timeline", [])
        payload.setdefault("lighting_plan", LLMProvider._default_lighting_plan())

        timeline = payload["timeline"]
        base_action = LLMProvider._find_action(timeline, "base_longitudinal")
        rotate_action = LLMProvider._find_action(timeline, "base_rotate")
        lift_action = LLMProvider._find_action(timeline, "lift_delta")

        if any(token in text for token in ["跟拍", "跟随", "follow", "tracking"]):
            LLMProvider._ensure_follow_mode(payload, text)

        if any(token in text for token in ["检查", "checkpoint", "构图", "画面", "framing"]):
            LLMProvider._ensure_checkpoint(payload)

        if base_action is not None:
            if any(token in text for token in ["后退", "退后", "backward", "back"]):
                base_action["params"]["distance_m"] = -0.2
                base_action["params"]["speed_m_s"] = 0.1
                base_action["description"] = "小车开环后退 20 cm。"
            elif any(token in text for token in ["前进", "向前", "forward"]):
                base_action["params"]["distance_m"] = 0.2
                base_action["params"]["speed_m_s"] = 0.1
                base_action["description"] = "小车开环前进 20 cm。"

        if any(token in text for token in ["左转", "turn left", "rotate left"]):
            if rotate_action is None:
                rotate_action = LLMProvider._insert_after_last(payload, LLMProvider._base_rotate_action("r1"))
            rotate_action["params"]["angle_deg"] = 15.0
            rotate_action["description"] = "小车开环原地左转 15 度。"
        elif any(token in text for token in ["右转", "turn right", "rotate right"]):
            if rotate_action is None:
                rotate_action = LLMProvider._insert_after_last(payload, LLMProvider._base_rotate_action("r1"))
            rotate_action["params"]["angle_deg"] = -15.0
            rotate_action["description"] = "小车开环原地右转 15 度。"

        if lift_action is not None:
            if any(token in text for token in ["下降", "降低", "低一点", "down", "lower"]):
                lift_action["params"]["delta_cm"] = -2.0
                lift_action["description"] = "升降杆下降 2 cm。"
            elif any(token in text for token in ["上升", "升高", "高一点", "up", "raise"]):
                lift_action["params"]["delta_cm"] = 2.0
                lift_action["description"] = "升降杆上升 2 cm。"

        LLMProvider._apply_lighting_feedback(payload, text)
        payload["name"] = LLMProvider._infer_name(payload)
        payload["summary"] = LLMProvider._summary_from_payload(payload)

    @staticmethod
    def _find_action(timeline: list[dict[str, Any]], action_type: str) -> dict[str, Any] | None:
        return next((action for action in timeline if action.get("type") == action_type), None)

    @staticmethod
    def _insert_after_last(payload: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        timeline = payload.setdefault("timeline", [])
        if timeline and "start_after" not in action and "start_at_s" not in action:
            action["start_after"] = [timeline[-1]["id"]]
        timeline.append(action)
        return action

    @staticmethod
    def _ensure_checkpoint(payload: dict[str, Any]) -> None:
        timeline = payload.setdefault("timeline", [])
        if LLMProvider._find_action(timeline, "checkpoint") is not None:
            return
        start_after = [timeline[-1]["id"]] if timeline else []
        timeline.append(LLMProvider._checkpoint_action("cp1", start_after=start_after))

    @staticmethod
    def _ensure_follow_mode(payload: dict[str, Any], text: str) -> None:
        timeline = payload.setdefault("timeline", [])
        if LLMProvider._find_action(timeline, "follow_mode") is not None:
            return
        duration_s = 5.0
        if "4" in text or "四" in text:
            duration_s = 4.0
        elif "8" in text or "八" in text:
            duration_s = 8.0
        start_after = [timeline[-1]["id"]] if timeline else []
        timeline.append(LLMProvider._follow_action("follow1", start_after=start_after, duration_s=duration_s))

    @staticmethod
    def _apply_lighting_feedback(payload: dict[str, Any], text: str) -> None:
        light = payload.setdefault("lighting_plan", LLMProvider._default_lighting_plan())[0]

        if any(token in text for token in ["暖", "warm"]):
            light["color_temperature"] = "warm"
        elif any(token in text for token in ["冷", "cool"]):
            light["color_temperature"] = "cool"
        elif any(token in text for token in ["中性", "neutral"]):
            light["color_temperature"] = "neutral"

        if any(token in text for token in ["强光", "strong", "更亮"]):
            light["intensity"] = "strong"
        elif any(token in text for token in ["弱光", "weak", "暗一点"]):
            light["intensity"] = "weak"
        elif any(token in text for token in ["中等", "medium"]):
            light["intensity"] = "medium"

        if any(token in text for token in ["侧", "side"]):
            light["azimuth"] = "side"
        elif any(token in text for token in ["背", "back light", "backlight"]):
            light["azimuth"] = "back"
        elif any(token in text for token in ["正面", "front"]):
            light["azimuth"] = "front"

        if any(token in text for token in ["顶光", "top"]):
            light["height"] = "top"
        elif any(token in text for token in ["底光", "bottom"]):
            light["height"] = "bottom"
        elif any(token in text for token in ["中光", "middle"]):
            light["height"] = "middle"

        color_map = {"warm": "暖光", "cool": "冷光", "neutral": "中性光"}
        intensity_map = {"strong": "强", "medium": "中等强度", "weak": "弱"}
        azimuth_map = {"front": "正面", "side": "侧面", "back": "背面"}
        height_map = {"bottom": "底光", "middle": "中光", "top": "顶光"}
        light["description"] = (
            f"{color_map[light['color_temperature']]}、{intensity_map[light['intensity']]}、"
            f"{azimuth_map[light['azimuth']]}{height_map[light['height']]}。"
        )

    @staticmethod
    def _infer_name(payload: dict[str, Any]) -> str:
        parts = []
        action_types = [action.get("type", "") for action in payload.get("timeline", [])]
        if "follow_mode" in action_types:
            parts.append("follow")
        if "base_longitudinal" in action_types:
            base = LLMProvider._find_action(payload.get("timeline", []), "base_longitudinal")
            if base and float(base.get("params", {}).get("distance_m", 0.0)) < 0:
                parts.append("back")
            else:
                parts.append("forward")
        if "lift_delta" in action_types:
            lift = LLMProvider._find_action(payload.get("timeline", []), "lift_delta")
            if lift and float(lift.get("params", {}).get("delta_cm", 0.0)) < 0:
                parts.append("lower")
            else:
                parts.append("lift")
        if "checkpoint" in action_types:
            parts.append("checkpoint")
        parts.append("lighting")
        return "_".join(parts)

    @staticmethod
    def _summary_from_payload(payload: dict[str, Any]) -> str:
        action_types = [action.get("type", "") for action in payload.get("timeline", [])]
        fragments = []
        if "follow_mode" in action_types:
            fragments.append("进入视觉跟随模式")
        if "base_longitudinal" in action_types:
            fragments.append("执行小幅开环底盘移动")
        if "base_rotate" in action_types:
            fragments.append("进行小角度开环旋转")
        if "lift_delta" in action_types:
            fragments.append("调整升降杆机位")
        if "checkpoint" in action_types:
            fragments.append("随后检查人物画面构图")
        fragments.append("并提供打光方案")
        return "，".join(fragments) + "。"

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
    def _default_timeline_script() -> dict[str, Any]:
        return {
            "name": "mock_timeline_with_lighting",
            "version": "2.0",
            "mode": "timeline",
            "summary": "执行小幅开环机位调整，并提供默认中性打光方案。",
            "timeline": [
                {
                    "id": "a1",
                    "type": "arm_init_pose",
                    "start_at_s": 0.0,
                    "device": "p4",
                    "channel": "arm",
                    "params": {"wait_first_s": 2.0},
                    "timeout_s": 10,
                    "blocking": True,
                    "on_fail": "stop_all",
                    "description": "机械臂回到准备位。",
                },
                {
                    "id": "l1",
                    "type": "lift_delta",
                    "start_after": ["a1"],
                    "device": "s3",
                    "channel": "lift",
                    "params": {"delta_cm": -2.0},
                    "timeout_s": 8,
                    "blocking": True,
                    "on_fail": "stop_all",
                    "description": "升降杆下降 2 cm。",
                },
                {
                    "id": "b1",
                    "type": "base_longitudinal",
                    "start_after": ["l1"],
                    "device": "s3",
                    "channel": "base",
                    "params": {
                        "distance_m": -0.2,
                        "speed_m_s": 0.1,
                    },
                    "timeout_s": 8,
                    "blocking": True,
                    "on_fail": "stop_all",
                    "description": "小车开环后退 20 cm。",
                },
                {
                    "id": "w1",
                    "type": "wait",
                    "start_after": ["b1"],
                    "device": "local",
                    "channel": "scheduler",
                    "params": {"duration_s": 0.5},
                    "timeout_s": 2,
                    "blocking": True,
                    "on_fail": "continue",
                    "description": "等待设备稳定。",
                },
            ],
            "lighting_plan": LLMProvider._default_lighting_plan(),
        }

    @staticmethod
    def _base_rotate_action(action_id: str) -> dict[str, Any]:
        return {
            "id": action_id,
            "type": "base_rotate",
            "device": "s3",
            "channel": "base",
            "params": {
                "angle_deg": 15.0,
                "angular_speed_rad_s": 0.2,
            },
            "timeout_s": 8,
            "blocking": True,
            "on_fail": "stop_all",
            "description": "小车开环原地左转 15 度。",
        }

    @staticmethod
    def _checkpoint_action(action_id: str, start_after: list[str]) -> dict[str, Any]:
        return {
            "id": action_id,
            "type": "checkpoint",
            "start_after": start_after,
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
        }

    @staticmethod
    def _follow_action(action_id: str, start_after: list[str], duration_s: float) -> dict[str, Any]:
        return {
            "id": action_id,
            "type": "follow_mode",
            "start_after": start_after,
            "device": "local",
            "channel": "vision",
            "duration_s": duration_s,
            "target_frame": {
                "target_class": "person",
                "target_id": "main_actor",
                "bbox_format": "cxcywh_norm",
                "bbox": [0.5, 0.52, 0.35, 0.65],
                "tolerance": {
                    "center_x": 0.06,
                    "center_y": 0.06,
                    "width": 0.10,
                    "height": 0.12,
                },
            },
            "servo": {
                "max_iters": int(duration_s * 10),
                "allow_base": True,
                "allow_lift": True,
                "allow_arm": False,
            },
            "timeout_s": max(10.0, duration_s + 5.0),
            "blocking": True,
            "on_fail": "continue",
            "description": f"进入 {duration_s:.0f} 秒视觉跟随模式，保持人物在画面中部。",
        }

    @staticmethod
    def _default_lighting_plan() -> list[dict[str, Any]]:
        return [
            {
                "id": "light_default",
                "start_at_s": 0.0,
                "color_temperature": "neutral",
                "intensity": "medium",
                "azimuth": "front",
                "height": "middle",
                "description": "默认中性光、中等强度、正面中光。",
            }
        ]
