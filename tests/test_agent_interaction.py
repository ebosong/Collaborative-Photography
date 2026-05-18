"""Regression tests for the interactive planning agent."""

from __future__ import annotations

import json

from agent.json_repair import JsonRepairer
from app import _normalize_command
from schemas.script_schema import ScriptPlan


def _config() -> dict:
    return {
        "app": {"mock_mode": True},
        "llm": {"use_mock_when_unconfigured": True},
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
