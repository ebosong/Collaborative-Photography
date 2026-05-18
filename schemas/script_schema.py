"""Pydantic schemas for the CamBot executable command script protocol."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


CommandTarget = Literal["base", "lift", "arm", "wait"]
CommandAction = Literal["connect", "move", "move_to", "move_by", "preset", "stop", "wait"]


class MotionCommand(BaseModel):
    """One low-level command that can be dispatched to a wrapped controller."""

    id: str = Field(default="")
    phase: str = Field(default="拍摄动作")
    target: CommandTarget
    action: CommandAction
    description: str = Field(default="")
    duration_s: float = Field(default=0.0)
    linear_x: float | None = None
    angular_z: float | None = None
    height_m: float | None = None
    delta_m: float | None = None
    preset: str | None = None


class ScriptMetadata(BaseModel):
    """Human-readable summary fields for the executable script."""

    title: str = Field(default="CamBot executable filming script")
    summary: str = Field(default="逐条执行的拍摄运动控制脚本。")
    total_duration_s: float = Field(default=8.0)


class ScriptPlan(BaseModel):
    """Canonical JSON script consumed directly by the CamBot executor."""

    script: ScriptMetadata = Field(default_factory=ScriptMetadata)
    commands: list[MotionCommand] = Field(default_factory=list)
