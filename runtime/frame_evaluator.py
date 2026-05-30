from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class FrameEvaluation:
    ok: bool
    found: bool
    errors: dict[str, float]
    detected_bbox: dict[str, float]
    expected_bbox: dict[str, float]
    message: str = ""


class FrameEvaluator:
    """
    Compare expected_frame with detected bbox.

    First version focuses on:
    - center_x
    - center_y
    - area

    width/height errors are also reported for logging.
    """

    def evaluate(self, expected_frame: Any, detection: Any) -> FrameEvaluation:
        cx, cy, w, h = [float(v) for v in expected_frame.bbox]
        expected = {
            "cx": cx,
            "cy": cy,
            "w": w,
            "h": h,
            "area": w * h,
        }

        if not detection.found:
            return FrameEvaluation(
                ok=False,
                found=False,
                errors={},
                detected_bbox={},
                expected_bbox=expected,
                message="target not found",
            )

        detected = dict(detection.bbox)
        detected.setdefault("area", float(detected.get("w", 0.0)) * float(detected.get("h", 0.0)))

        errors = {
            "center_x": float(detected["cx"]) - expected["cx"],
            "center_y": float(detected["cy"]) - expected["cy"],
            "width": float(detected["w"]) - expected["w"],
            "height": float(detected["h"]) - expected["h"],
            "area": float(detected["area"]) - expected["area"],
        }

        tol = expected_frame.tolerance
        ok = (
            abs(errors["center_x"]) <= tol.center_x
            and abs(errors["center_y"]) <= tol.center_y
            and abs(errors["area"]) <= tol.area
        )

        return FrameEvaluation(
            ok=ok,
            found=True,
            errors=errors,
            detected_bbox=detected,
            expected_bbox=expected,
            message="ok" if ok else "bbox error exceeds tolerance",
        )
