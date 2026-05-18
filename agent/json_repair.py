"""JSON extraction and repair helpers for LLM planner output."""

from __future__ import annotations

import json
import logging
from typing import Any

from chain.validator import PlanValidator
from providers.llm_provider import LLMProvider
from schemas.script_schema import ScriptPlan


class JsonRepairer:
    """Validate planner output and try to repair invalid JSON before falling back."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.provider = LLMProvider(config)
        self.validator = PlanValidator(config)
        self.logger = logging.getLogger(self.__class__.__name__)

    def repair_and_validate(
        self,
        raw_text: str,
        previous_plan: ScriptPlan | None = None,
    ) -> ScriptPlan:
        """Return a validated plan, using repair attempts before safe fallback."""
        for candidate in self._candidate_texts(raw_text):
            plan = self._try_validate(candidate)
            if plan is not None:
                return plan

        repaired_text = self._llm_repair(raw_text, previous_plan=previous_plan)
        if repaired_text:
            for candidate in self._candidate_texts(repaired_text):
                plan = self._try_validate(candidate)
                if plan is not None:
                    return plan

        if previous_plan is not None:
            self.logger.warning("JSON repair failed; keeping the previous valid plan.")
            return previous_plan

        self.logger.warning("JSON repair failed without previous plan; using validator fallback.")
        return self.validator.validate_and_clip("{}")

    def _try_validate(self, candidate: str) -> ScriptPlan | None:
        try:
            return self.validator.validate_and_clip_strict(candidate)
        except Exception as exc:
            self.logger.warning("Candidate JSON failed validation: %s", exc)
            return None

    def _candidate_texts(self, raw_text: str) -> list[str]:
        candidates = [raw_text.strip()]
        extracted = self._extract_first_json_object(raw_text)
        if extracted and extracted not in candidates:
            candidates.append(extracted)
        return [candidate for candidate in candidates if candidate]

    def _llm_repair(self, raw_text: str, previous_plan: ScriptPlan | None = None) -> str | None:
        current_plan_block = ""
        if previous_plan is not None:
            current_plan_block = (
                "Current JSON plan:\n"
                f"{previous_plan.model_dump_json()}\n"
                "If the broken text cannot be repaired safely, return the current JSON plan unchanged.\n"
            )
        prompt = (
            "Fix the following text into one strict JSON object for the CamBot plan schema.\n"
            "Return JSON only. Do not add markdown or prose.\n"
            "Required top-level fields: shot_plan, robot_task, safety_rules, fallback.\n"
            f"{current_plan_block}"
            "Original text:\n"
            f"{raw_text}"
        )
        try:
            return self.provider.generate(prompt)
        except Exception as exc:
            self.logger.warning("LLM JSON repair request failed: %s", exc)
            return None

    @staticmethod
    def _extract_first_json_object(text: str) -> str | None:
        start = text.find("{")
        if start < 0:
            return None

        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
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
                    return text[start : index + 1]
        return None
