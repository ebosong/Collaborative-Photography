from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any


@dataclass
class DetectionResult:
    found: bool
    target_class: str
    confidence: float
    bbox: dict[str, float]
    source: str = "mock"


class MockVisionDetector:
    """
    First-version mock detector for closed-loop testing.

    It does not run YOLO. It simulates a detected bbox and updates that bbox
    when correction actions are executed, so the whole closed-loop pipeline can
    be tested before connecting a real detector and video stream.
    """

    def __init__(
        self,
        initial_offset: tuple[float, float, float] = (-0.12, 0.08, -0.06),
        correction_alpha: float = 0.45,
    ) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self.initial_offset = initial_offset
        self.correction_alpha = float(correction_alpha)
        self.current_bbox: dict[str, float] | None = None
        self.expected_bbox: dict[str, float] | None = None

    def reset_for_expected_frame(self, expected_frame: Any) -> None:
        cx, cy, w, h = [float(v) for v in expected_frame.bbox]
        off_x, off_y, off_area = self.initial_offset

        expected_area = max(w * h, 1e-6)
        area = max(0.01, min(0.95, expected_area + off_area))
        scale = (area / expected_area) ** 0.5

        self.expected_bbox = {
            "cx": cx,
            "cy": cy,
            "w": w,
            "h": h,
            "area": expected_area,
        }
        self.current_bbox = {
            "cx": self._clamp01(cx + off_x),
            "cy": self._clamp01(cy + off_y),
            "w": self._clamp01(w * scale),
            "h": self._clamp01(h * scale),
        }
        self.current_bbox["area"] = self.current_bbox["w"] * self.current_bbox["h"]

        self.logger.info(
            "Mock vision reset: current_bbox=%s expected=%s",
            self.current_bbox,
            self.expected_bbox,
        )

    def detect_target(self, expected_frame: Any) -> DetectionResult:
        if self.current_bbox is None or self.expected_bbox is None:
            self.reset_for_expected_frame(expected_frame)

        return DetectionResult(
            found=True,
            target_class=expected_frame.target_class,
            confidence=0.90,
            bbox=dict(self.current_bbox or {}),
            source="mock",
        )

    def apply_correction_action(self, action: Any, expected_frame: Any) -> None:
        if self.current_bbox is None or self.expected_bbox is None:
            self.reset_for_expected_frame(expected_frame)

        assert self.current_bbox is not None
        assert self.expected_bbox is not None

        raw_type = getattr(action, "type", "")
        action_type = getattr(raw_type, "value", str(raw_type))

        if action_type == "base_longitudinal":
            self._move_area_toward_expected()
        elif action_type == "base_rotate":
            self._move_key_toward_expected("cx")
        elif action_type == "lift_delta":
            self._move_key_toward_expected("cy")
        else:
            self.logger.info("Mock vision ignores non-correction action type=%s", action_type)
            return

        self.logger.info("Mock vision after correction %s: %s", action_type, self.current_bbox)

    def _move_key_toward_expected(self, key: str) -> None:
        assert self.current_bbox is not None
        assert self.expected_bbox is not None
        cur = self.current_bbox[key]
        exp = self.expected_bbox[key]
        self.current_bbox[key] = self._clamp01(cur + (exp - cur) * self.correction_alpha)

    def _move_area_toward_expected(self) -> None:
        assert self.current_bbox is not None
        assert self.expected_bbox is not None

        cur_area = max(self.current_bbox["area"], 1e-6)
        exp_area = max(self.expected_bbox["area"], 1e-6)
        new_area = cur_area + (exp_area - cur_area) * self.correction_alpha
        scale = (new_area / cur_area) ** 0.5

        self.current_bbox["w"] = self._clamp01(self.current_bbox["w"] * scale)
        self.current_bbox["h"] = self._clamp01(self.current_bbox["h"] * scale)
        self.current_bbox["area"] = self.current_bbox["w"] * self.current_bbox["h"]

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, float(value)))


class YOLO26VisionDetector:
    """
    YOLO detector wrapper for the existing closed-loop interface.

    Environment variables:
    - YOLO_MODEL_PATH: default models/yolo26n.pt
    - YOLO_SOURCE: image path, video path, camera index, or stream URL
    - YOLO_CONF: default 0.25
    - YOLO_IMGSZ: default 640

    Target selection:
    - uses expected_frame.target_class, e.g. "person"
    - if the target class can be mapped to a YOLO class id, classes=[id] is passed
      into model.predict(...) so unrelated classes are filtered early
    - if multiple objects match, choose the highest-confidence one
    """

    def __init__(
        self,
        model_path: str | None = None,
        source: str | int | None = None,
        conf: float | None = None,
        imgsz: int | None = None,
    ) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)

        self.model_path = model_path or os.getenv("YOLO_MODEL_PATH", "models/yolo26n.pt")
        self.source = source if source is not None else os.getenv("YOLO_SOURCE", "test_images/person.jpg")
        self.conf = float(conf if conf is not None else os.getenv("YOLO_CONF", "0.25"))
        self.imgsz = int(imgsz if imgsz is not None else os.getenv("YOLO_IMGSZ", "640"))

        if isinstance(self.source, str) and self.source.isdigit():
            self.source = int(self.source)

        try:
            from ultralytics import YOLO
        except Exception as exc:
            raise RuntimeError(
                "Failed to import ultralytics. Install with: "
                "python -m pip install -U ultralytics opencv-python"
            ) from exc

        self.model = YOLO(self.model_path)

        # Build class-name -> id mapping from the loaded model.
        names = getattr(self.model, "names", {})
        self.class_name_to_id = {}
        if isinstance(names, dict):
            for cls_id, cls_name in names.items():
                self.class_name_to_id[str(cls_name)] = int(cls_id)
        elif isinstance(names, (list, tuple)):
            for cls_id, cls_name in enumerate(names):
                self.class_name_to_id[str(cls_name)] = int(cls_id)

        self.logger.info(
            "YOLO26VisionDetector loaded model=%s source=%s conf=%.2f imgsz=%d class_name_to_id_keys=%s",
            self.model_path,
            self.source,
            self.conf,
            self.imgsz,
            sorted(self.class_name_to_id.keys())[:10],
        )

    def reset_for_expected_frame(self, expected_frame: Any) -> None:
        self.logger.info(
            "YOLO vision ready for target_class=%s expected_bbox=%s",
            expected_frame.target_class,
            expected_frame.bbox,
        )

    def _resolve_classes_filter(self, target_class: str) -> list[int] | None:
        cls_id = self.class_name_to_id.get(str(target_class))
        if cls_id is None:
            self.logger.warning(
                "YOLO class id not found for target_class=%s; will run without classes filter.",
                target_class,
            )
            return None
        return [cls_id]

    def detect_target(self, expected_frame: Any) -> DetectionResult:
        target_class = str(expected_frame.target_class)
        classes_filter = self._resolve_classes_filter(target_class)

        results = self.model.predict(
            source=self.source,
            imgsz=self.imgsz,
            conf=self.conf,
            classes=classes_filter,
            verbose=False,
        )

        best: dict[str, Any] | None = None

        for result in results:
            img_h, img_w = result.orig_shape
            names = result.names

            for box in result.boxes:
                cls_id = int(box.cls[0])
                class_name = names[cls_id]
                confidence = float(box.conf[0])

                # Keep the explicit software-side filter as well.
                if class_name != target_class:
                    continue

                x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
                w = max(0.0, (x2 - x1) / img_w)
                h = max(0.0, (y2 - y1) / img_h)
                bbox = {
                    "cx": ((x1 + x2) / 2.0) / img_w,
                    "cy": ((y1 + y2) / 2.0) / img_h,
                    "w": w,
                    "h": h,
                    "area": w * h,
                }

                if best is None or confidence > best["confidence"]:
                    best = {
                        "confidence": confidence,
                        "bbox": bbox,
                    }

        if best is None:
            self.logger.warning(
                "YOLO target not found: class=%s source=%s classes_filter=%s",
                target_class,
                self.source,
                classes_filter,
            )
            return DetectionResult(
                found=False,
                target_class=target_class,
                confidence=0.0,
                bbox={},
                source=str(self.source),
            )

        self.logger.info(
            "YOLO detection | class=%s conf=%.3f bbox=%s classes_filter=%s",
            target_class,
            best["confidence"],
            best["bbox"],
            classes_filter,
        )
        return DetectionResult(
            found=True,
            target_class=target_class,
            confidence=float(best["confidence"]),
            bbox=dict(best["bbox"]),
            source=str(self.source),
        )


class VisionDetector:
    """
    Factory wrapper used by ScriptExecutor.

    Default mode is mock, so old tests still work.

    Set:
        $env:VISION_MODE="yolo"
        $env:YOLO_MODEL_PATH="models/yolo26n.pt"
        $env:YOLO_SOURCE="test_images/person.jpg"
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        mode = os.getenv("VISION_MODE", "mock").strip().lower()
        if mode in {"yolo", "yolo26", "yolo_image", "yolo_stream"}:
            return YOLO26VisionDetector(*args, **kwargs)
        if mode == "mock":
            return MockVisionDetector(*args, **kwargs)
        raise ValueError(f"Unsupported VISION_MODE={mode!r}. Use 'mock' or 'yolo'.")
