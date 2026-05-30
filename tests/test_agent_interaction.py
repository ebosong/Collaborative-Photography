"""Regression tests for the interactive TimelineScript planning agent."""

from __future__ import annotations

import json
from pathlib import Path

from agent.json_repair import JsonRepairer
from agent.reviewer import PlanReviewer
from agent.service import PlanAgentService
from app import _normalize_command
from providers.llm_provider import LLMProvider
from schemas.timeline_script_schema import TimelineScript


def _config() -> dict:
    return {
        "app": {"mock_mode": True, "log_dir": "logs"},
        "llm": {"use_mock_when_unconfigured": True},
        "planner": {"top_k": 2},
        "limits": {
            "height_m": {"min": 0.6, "max": 1.8, "default": 1.2},
            "base": {"max_linear_speed": 0.5, "max_angular_speed": 0.6},
            "lift": {"max_delta_per_step": 0.05},
        },
        "arm": {"enabled": False},
    }


def _base_plan() -> TimelineScript:
    return TimelineScript.model_validate(
        {
            "name": "test_timeline",
            "version": "2.0",
            "mode": "timeline",
            "summary": "执行小幅开环后退并检查构图。",
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
                    "description": "小车开环后退 20 cm。",
                },
                {
                    "id": "cp1",
                    "type": "checkpoint",
                    "start_after": ["b1"],
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
                    "id": "light_default",
                    "start_at_s": 0.0,
                    "color_temperature": "neutral",
                    "intensity": "medium",
                    "azimuth": "front",
                    "height": "middle",
                    "description": "默认中性光、中等强度、正面中光。",
                }
            ],
        }
    )


def test_partial_json_revision_merges_into_previous_plan() -> None:
    repairer = JsonRepairer(_config())
    previous = _base_plan()

    revised = repairer.repair_and_validate(
        raw_text=json.dumps({"summary": "改成更近的构图检查。"}),
        previous_plan=previous,
    )

    assert revised.summary == "改成更近的构图检查。"
    assert revised.timeline[0].id == "b1"
    assert revised.timeline[1].type == "checkpoint"
    assert revised.lighting_plan[0].id == "light_default"


def test_timeline_values_are_clipped_and_default_lighting_is_added() -> None:
    repairer = JsonRepairer(_config())

    revised = repairer.repair_and_validate(
        raw_text=json.dumps(
            {
                "name": "unsafe_timeline",
                "version": "2.0",
                "mode": "timeline",
                "summary": "过大动作。",
                "timeline": [
                    {
                        "id": "b1",
                        "type": "base_longitudinal",
                        "device": "s3",
                        "channel": "base",
                        "params": {
                            "distance_m": 9.0,
                            "speed_m_s": 9.0,
                        },
                        "timeout_s": 500,
                        "blocking": True,
                        "on_fail": "stop_all",
                        "description": "小车实时跟拍前进很远。",
                    }
                ],
            }
        ),
    )

    action = revised.timeline[0]
    assert action.params.distance_m == 0.5
    assert action.params.speed_m_s == 0.2
    assert "跟拍" not in action.description
    assert revised.lighting_plan[0].color_temperature == "neutral"
    assert revised.lighting_plan[0].azimuth == "front"


def test_start_after_references_are_validated() -> None:
    repairer = JsonRepairer(_config())

    revised = repairer.repair_and_validate(
        raw_text=json.dumps(
            {
                "name": "bad_dependency",
                "version": "2.0",
                "mode": "timeline",
                "summary": "错误依赖。",
                "timeline": [
                    {
                        "id": "cp1",
                        "type": "checkpoint",
                        "start_after": ["missing"],
                        "device": "local",
                        "channel": "vision",
                        "timeout_s": 30,
                        "blocking": True,
                        "on_vision_fail": "continue",
                        "description": "检查人物构图。",
                    }
                ],
                "lighting_plan": [],
            }
        )
    )

    ids = {action.id for action in revised.timeline}
    assert "missing" not in ids
    for action in revised.timeline:
        assert set(action.start_after).issubset(ids)


def test_cli_command_normalization() -> None:
    assert _normalize_command("/confirm") == "confirm"
    assert _normalize_command("\uff0fconfirm") == "confirm"
    assert _normalize_command(" confirm ") == "confirm"
    assert _normalize_command("\u786e\u8ba4") == "confirm"


def test_review_includes_timeline_and_lighting() -> None:
    review = PlanReviewer().render(_base_plan())

    assert "时间轴动作规划" in review
    assert "打光计划" in review
    assert "画面检查点" in review
    assert "light_default" in review


def test_confirm_plan_saves_timeline_without_hardware_dispatch(tmp_path) -> None:
    calls: list[TimelineScript] = []

    class FakeExecutor:
        def __init__(self, **_: object) -> None:
            pass

        def execute(self, plan: TimelineScript) -> None:
            calls.append(plan)

    repo_root = Path(__file__).resolve().parents[1]
    service = PlanAgentService(
        _config(),
        repo_root=repo_root,
        log_root=tmp_path,
        executor_factory=FakeExecutor,
    )
    response = service.create_session("先跟拍5秒，再后退并检查构图，打暖光侧面中光")

    response = service.confirm_plan(response.session_id)

    assert response.status == "executed"
    assert response.confirmed is True
    assert len(calls) == 1
    assert any(action.type == "follow_mode" for action in calls[0].timeline)
    assert any(action.type == "checkpoint" for action in calls[0].timeline)
    assert calls[0].lighting_plan[0].color_temperature == "warm"
    assert calls[0].lighting_plan[0].azimuth == "side"


def test_confirm_plan_only_does_not_execute(tmp_path) -> None:
    calls: list[TimelineScript] = []

    class FakeExecutor:
        def __init__(self, **_: object) -> None:
            pass

        def execute(self, plan: TimelineScript) -> None:
            calls.append(plan)

    repo_root = Path(__file__).resolve().parents[1]
    service = PlanAgentService(
        _config(),
        repo_root=repo_root,
        log_root=tmp_path,
        executor_factory=FakeExecutor,
    )
    response = service.create_session("centered open-loop shot")

    response = service.confirm_plan_only(response.session_id)

    assert response.status == "confirmed"
    assert response.confirmed is True
    assert calls == []


def test_deepseek_provider_profile_uses_hardcoded_config() -> None:
    config = _config()
    config["llm"] = {
        "provider": "deepseek_openai_compatible",
        "api_key": "sk-qwen-key-should-not-be-used",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.6-plus",
        "temperature": 0.1,
        "timeout_s": 30,
        "trust_env": False,
        "use_mock_when_unconfigured": True,
        "providers": {
            "deepseek_openai_compatible": {
                "api_key": "sk-hardcoded-deepseek-key",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
            }
        },
    }

    resolved = LLMProvider(config)._resolved_llm_config()

    assert resolved["provider"] == "deepseek_openai_compatible"
    assert resolved["api_key"] == "sk-hardcoded-deepseek-key"
    assert resolved["base_url"] == "https://api.deepseek.com"
    assert resolved["model"] == "deepseek-v4-flash"
