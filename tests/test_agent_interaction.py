"""Regression tests for the interactive planning agent."""

from __future__ import annotations

import json
from pathlib import Path

from agent.json_repair import JsonRepairer
from agent.reviewer import PlanReviewer
from agent.service import PlanAgentService
from app import _normalize_command
from schemas.script_schema import ScriptPlan


def _config() -> dict:
    return {
        "app": {"mock_mode": True},
        "llm": {"use_mock_when_unconfigured": True},
        "planner": {"top_k": 2},
        "limits": {
            "duration_s": {"min": 3, "max": 30, "default": 8},
            "distance_m": {"min": 0.8, "max": 4.0, "default": 2.2},
            "height_m": {"min": 0.6, "max": 1.8, "default": 1.2},
            "subject_scale_target": {"min": 0.2, "max": 0.7, "default": 0.4},
            "allowed_templates": ["mid_follow", "side_front_follow", "mid_follow_safe"],
            "allowed_regions": ["left", "center", "right"],
            "base": {"max_linear_speed": 0.5, "max_angular_speed": 0.6},
            "lift": {"max_delta_per_step": 0.05},
        },
        "safety_defaults": {
            "max_speed": 0.5,
            "min_distance": 0.8,
            "lost_target_action": "slow_stop_and_search",
            "fallback_template": "mid_follow_safe",
        },
        "arm": {"enabled": False},
    }


def _base_plan() -> ScriptPlan:
    return ScriptPlan.model_validate(
        {
            "script": {
                "title": "Test command script",
                "summary": "稳定居中跟拍。",
                "total_duration_s": 6.0,
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
                    "phase": "跟拍动作",
                    "target": "base",
                    "action": "move",
                    "linear_x": 0.18,
                    "angular_z": 0.0,
                    "duration_s": 6.0,
                    "description": "底盘低速向前移动，保持主体居中稳定跟拍。",
                },
                {
                    "id": "cmd_03",
                    "phase": "结束动作",
                    "target": "base",
                    "action": "stop",
                    "description": "停止底盘运动。",
                },
                {
                    "id": "cmd_04",
                    "phase": "结束动作",
                    "target": "lift",
                    "action": "stop",
                    "description": "停止升降运动。",
                },
                {
                    "id": "cmd_05",
                    "phase": "结束动作",
                    "target": "arm",
                    "action": "stop",
                    "description": "停止机械臂动作。",
                },
            ],
        }
    )


def test_partial_json_revision_merges_into_previous_plan() -> None:
    repairer = JsonRepairer(_config())
    previous = _base_plan()

    revised = repairer.repair_and_validate(
        raw_text=json.dumps({"script": {"summary": "改成更近的跟拍。"}}),
        previous_plan=previous,
    )

    assert revised.script.summary == "改成更近的跟拍。"
    assert revised.commands[1].linear_x == 0.18
    assert revised.commands[-1].target == "arm"


def test_command_script_values_are_clipped_and_stop_commands_are_added() -> None:
    repairer = JsonRepairer(_config())

    revised = repairer.repair_and_validate(
        raw_text=json.dumps(
            {
                "script": {"title": "Fast shot", "summary": "过快动作。"},
                "commands": [
                    {
                        "phase": "跟拍动作",
                        "target": "base",
                        "action": "move",
                        "linear_x": 9.0,
                        "angular_z": -9.0,
                        "duration_s": 60.0,
                    }
                ],
            }
        ),
    )

    assert revised.commands[0].linear_x == 0.5
    assert revised.commands[0].angular_z == -0.6
    assert revised.commands[0].duration_s == 30.0
    assert [command.target for command in revised.commands[-3:]] == ["base", "lift", "arm"]


def test_cli_command_normalization() -> None:
    assert _normalize_command("/confirm") == "confirm"
    assert _normalize_command("\uff0fconfirm") == "confirm"
    assert _normalize_command(" confirm ") == "confirm"
    assert _normalize_command("\u786e\u8ba4") == "confirm"


def test_review_includes_command_script_actions() -> None:
    review = PlanReviewer().render(_base_plan())

    assert "具体拍摄动作规划" in review
    assert "下位控制指令" in review
    assert "base.move" in review
    assert "cmd_02" in review


def test_confirm_plan_executes_current_plan(tmp_path) -> None:
    calls: list[ScriptPlan] = []

    class FakeExecutor:
        def __init__(self, **_: object) -> None:
            pass

        def execute(self, plan: ScriptPlan) -> None:
            calls.append(plan)

    repo_root = Path(__file__).resolve().parents[1]
    service = PlanAgentService(
        _config(),
        repo_root=repo_root,
        log_root=tmp_path,
        executor_factory=FakeExecutor,
    )
    response = service.create_session("centered follow shot")
    response = service.send_message(response.session_id, "move subject left and closer")

    response = service.confirm_plan(response.session_id)

    assert response.status == "executed"
    assert response.confirmed is True
    assert len(calls) == 1
    move_command = next(command for command in calls[0].commands if command.action == "move")
    assert move_command.angular_z == 0.18
    assert move_command.linear_x == 0.26


def test_confirm_plan_only_does_not_execute(tmp_path) -> None:
    calls: list[ScriptPlan] = []

    class FakeExecutor:
        def __init__(self, **_: object) -> None:
            pass

        def execute(self, plan: ScriptPlan) -> None:
            calls.append(plan)

    repo_root = Path(__file__).resolve().parents[1]
    service = PlanAgentService(
        _config(),
        repo_root=repo_root,
        log_root=tmp_path,
        executor_factory=FakeExecutor,
    )
    response = service.create_session("centered follow shot")

    response = service.confirm_plan_only(response.session_id)

    assert response.status == "confirmed"
    assert response.confirmed is True
    assert calls == []
