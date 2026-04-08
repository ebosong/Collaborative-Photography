"""Pydantic schemas for the minimal CamBot filming plan protocol."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ShotPlan(BaseModel):
    """High-level filming parameters chosen by the planner."""

    template: str = Field(default="mid_follow_safe")
    duration_s: int = Field(default=8)
    distance_m: float = Field(default=2.2)
    height_m: float = Field(default=1.2)
    subject_region: str = Field(default="center")
    subject_scale_target: float = Field(default=0.4)


class RobotTask(BaseModel):
    """Named robot behavior for the executor."""

    name: str = Field(default="track_subject_with_framing")


class SafetyRules(BaseModel):
    """Planner-provided safety envelope that remains high level."""

    max_speed: float = Field(default=0.5)
    min_distance: float = Field(default=0.8)
    lost_target_action: str = Field(default="slow_stop_and_search")


class FallbackPlan(BaseModel):
    """Fallback behavior reference when a plan needs substitution."""

    template: str = Field(default="mid_follow_safe")


class ScriptPlan(BaseModel):
    """Canonical structured plan consumed by the CamBot executor."""

    shot_plan: ShotPlan
    robot_task: RobotTask
    safety_rules: SafetyRules
    fallback: FallbackPlan
