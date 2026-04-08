"""Plan parsing, validation, clipping, and fallback substitution."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from schemas.script_schema import ScriptPlan


class PlanValidator:
    """Validate LLM JSON output and clip values into safe runtime ranges."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        self.allowed_templates = set(config["limits"]["allowed_templates"])
        self.allowed_regions = set(config["limits"]["allowed_regions"])

    def validate_and_clip(self, plan_text: str) -> ScriptPlan:
        """Parse raw text, validate against schema, and clip unsafe values."""
        payload = self._parse_json(plan_text)

        try:
            plan = ScriptPlan.model_validate(payload)
        except ValidationError as exc:
            self.logger.warning("Schema validation failed, applying safe fallback payload: %s", exc)
            plan = ScriptPlan.model_validate(self._safe_default_payload())

        plan.shot_plan.template = self._validate_template(plan.shot_plan.template)
        plan.shot_plan.duration_s = int(
            self._clip_value("duration_s", plan.shot_plan.duration_s)
        )
        plan.shot_plan.distance_m = float(
            self._clip_value("distance_m", plan.shot_plan.distance_m)
        )
        plan.shot_plan.height_m = float(
            self._clip_value("height_m", plan.shot_plan.height_m)
        )
        plan.shot_plan.subject_scale_target = float(
            self._clip_value("subject_scale_target", plan.shot_plan.subject_scale_target)
        )
        if plan.shot_plan.subject_region not in self.allowed_regions:
            plan.shot_plan.subject_region = "center"

        plan.safety_rules.max_speed = min(
            float(plan.safety_rules.max_speed),
            float(self.config["safety_defaults"]["max_speed"]),
        )
        plan.safety_rules.min_distance = max(
            float(plan.safety_rules.min_distance),
            float(self.config["safety_defaults"]["min_distance"]),
        )
        if not plan.safety_rules.lost_target_action:
            plan.safety_rules.lost_target_action = self.config["safety_defaults"]["lost_target_action"]

        plan.fallback.template = self._validate_template(plan.fallback.template)
        return plan

    def _parse_json(self, plan_text: str) -> dict[str, Any]:
        try:
            payload = json.loads(plan_text)
        except json.JSONDecodeError as exc:
            self.logger.warning("Planner returned invalid JSON, using safe default: %s", exc)
            payload = self._safe_default_payload()

        if not isinstance(payload, dict):
            self.logger.warning("Planner returned non-object JSON, using safe default payload.")
            return self._safe_default_payload()
        return payload

    def _validate_template(self, template_name: str) -> str:
        if template_name in self.allowed_templates:
            return template_name
        return self.config["safety_defaults"]["fallback_template"]

    def _clip_value(self, key: str, value: float | int) -> float:
        limits = self.config["limits"][key]
        clipped = max(float(limits["min"]), min(float(limits["max"]), float(value)))
        if clipped != float(value):
            self.logger.info("Clipped %s from %s to %s", key, value, clipped)
        return clipped if key != "duration_s" else round(clipped)

    def _safe_default_payload(self) -> dict[str, Any]:
        defaults = self.config
        return {
            "shot_plan": {
                "template": defaults["safety_defaults"]["fallback_template"],
                "duration_s": defaults["limits"]["duration_s"]["default"],
                "distance_m": defaults["limits"]["distance_m"]["default"],
                "height_m": defaults["limits"]["height_m"]["default"],
                "subject_region": "center",
                "subject_scale_target": defaults["limits"]["subject_scale_target"]["default"],
            },
            "robot_task": {"name": "track_subject_with_framing"},
            "safety_rules": {
                "max_speed": defaults["safety_defaults"]["max_speed"],
                "min_distance": defaults["safety_defaults"]["min_distance"],
                "lost_target_action": defaults["safety_defaults"]["lost_target_action"],
            },
            "fallback": {"template": defaults["safety_defaults"]["fallback_template"]},
        }
