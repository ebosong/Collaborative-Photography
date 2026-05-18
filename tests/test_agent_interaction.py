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
        },
        "safety_defaults": {
            "max_speed": 0.5,
            "min_distance": 0.8,
            "lost_target_action": "slow_stop_and_search",
            "fallback_template": "mid_follow_safe",
        },
    }


def _base_plan() -> ScriptPlan:
    return ScriptPlan.model_validate(
        {
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
    )


def test_partial_json_revision_merges_into_previous_plan() -> None:
    repairer = JsonRepairer(_config())
    previous = _base_plan()

    revised = repairer.repair_and_validate(
        raw_text=json.dumps({"shot_plan": {"duration_s": 12}}),
        previous_plan=previous,
    )

    assert revised.shot_plan.duration_s == 12
    assert revised.shot_plan.distance_m == 2.2
    assert revised.robot_task.name == "track_subject_with_framing"


def test_leaf_json_revision_merges_into_previous_plan() -> None:
    repairer = JsonRepairer(_config())
    previous = _base_plan()

    revised = repairer.repair_and_validate(
        raw_text=json.dumps({"duration_s": 12, "subject_region": "left"}),
        previous_plan=previous,
    )

    assert revised.shot_plan.duration_s == 12
    assert revised.shot_plan.subject_region == "left"
    assert revised.shot_plan.distance_m == 2.2


def test_cli_command_normalization() -> None:
    assert _normalize_command("/confirm") == "confirm"
    assert _normalize_command("\uff0fconfirm") == "confirm"
    assert _normalize_command(" confirm ") == "confirm"
    assert _normalize_command("\u786e\u8ba4") == "confirm"


def test_review_includes_concrete_filming_actions() -> None:
    review = PlanReviewer().render(_base_plan())

    assert "\u5177\u4f53\u62cd\u6444\u52a8\u4f5c" in review
    assert "\u51c6\u5907\u9636\u6bb5" in review
    assert "\u8ddf\u62cd\u52a8\u4f5c" in review
    assert "\u7ed3\u675f\u52a8\u4f5c" in review


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
    assert calls[0].shot_plan.subject_region == "left"
    assert calls[0].shot_plan.distance_m == 1.4


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
