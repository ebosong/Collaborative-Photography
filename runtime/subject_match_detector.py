from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
from ultralytics import YOLO


@dataclass
class DetectionResult:
    found: bool
    target_class: str
    confidence: float
    bbox: dict[str, float]
    source: str = "hist_match"
    message: str = ""


class SubjectMatchVisionDetector:
    """
    Lightweight main-actor detector without DINO.

    Pipeline:
        1. YOLO runs every frame and returns target_class candidate boxes.
        2. If template images are provided, build an HSV color histogram template.
        3. Score each YOLO candidate by:
             - HSV histogram similarity to template
             - spatial continuity with the last selected box
             - YOLO confidence
        4. If no template is provided, use YOLO + spatial continuity.

    This is much faster than DINO and is suitable for real-time checkpoint/follow
    experiments on CPU.

    Output bbox format:
        {"cx": ..., "cy": ..., "w": ..., "h": ..., "area": ..., "x1": ..., "y1": ..., "x2": ..., "y2": ...}
    all normalized to [0, 1].
    """

    def __init__(
        self,
        yolo_model_path: str | os.PathLike | None = None,
        target_class: str = "person",
        template_paths: list[str] | None = None,
        camera_index: int | None = None,
        conf: float = 0.25,
        iou: float = 0.60,
        imgsz: int = 416,
        fallback_to_yolo: bool = True,
        hist_threshold: float = 0.18,
        template_update_alpha: float = 0.04,
        device: str | None = None,
    ) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self.yolo_model_path = str(yolo_model_path or os.getenv("YOLO_MODEL_PATH", "models/yolo26n.pt"))
        self.target_class = str(os.getenv("TARGET_CLASS_NAME", target_class))
        self.template_paths = template_paths or self._read_template_paths_from_env()
        self.camera_index = camera_index
        self.conf = float(conf)
        self.iou = float(iou)
        self.imgsz = int(imgsz)
        self.fallback_to_yolo = bool(fallback_to_yolo)
        self.hist_threshold = float(hist_threshold)
        self.template_update_alpha = float(template_update_alpha)
        self.device = device

        self.yolo: YOLO | None = None
        self.target_class_id: int | None = None
        self.template_hist: np.ndarray | None = None
        self.cap: cv2.VideoCapture | None = None
        self._ready = False

        self.last_bbox: dict[str, float] | None = None
        self.last_hist: np.ndarray | None = None

    @staticmethod
    def _read_template_paths_from_env() -> list[str]:
        raw = os.getenv("VISION_TEMPLATE_PATHS") or os.getenv("TARGET_IMAGE_PATHS") or ""
        return [p.strip() for p in raw.split(";") if p.strip()]

    def setup(self) -> None:
        if self._ready:
            return

        if not Path(self.yolo_model_path).exists():
            raise FileNotFoundError(
                f"YOLO model not found: {self.yolo_model_path}. "
                "Set YOLO_MODEL_PATH or pass yolo_model_path explicitly."
            )

        self.logger.info("Loading YOLO model: %s", self.yolo_model_path)
        self.yolo = YOLO(self.yolo_model_path)
        self.target_class_id = self._get_yolo_class_id(self.target_class)

        self.template_hist = self._build_template_hist(self.template_paths)
        if self.template_hist is None:
            self.logger.warning("No valid template histogram; using YOLO + spatial continuity only.")

        if self.camera_index is not None:
            self.cap = cv2.VideoCapture(int(self.camera_index))
            if not self.cap.isOpened():
                raise RuntimeError(f"Cannot open camera index {self.camera_index}")

        self._ready = True

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def reset_for_expected_frame(self, expected_frame: Any) -> None:
        _ = expected_frame
        self.last_bbox = None
        self.last_hist = None

    def apply_correction_action(self, action: Any, expected_frame: Any) -> None:
        _ = action
        _ = expected_frame

    def detect_target(self, expected_frame: Any) -> DetectionResult:
        self.setup()
        if self.cap is None:
            raise RuntimeError("detect_target() requires camera_index. Use detect_frame(frame, expected_frame) otherwise.")

        ok, frame = self.cap.read()
        if not ok or frame is None:
            return DetectionResult(
                found=False,
                target_class=getattr(expected_frame, "target_class", self.target_class),
                confidence=0.0,
                bbox={},
                source="camera",
                message="failed to read camera frame",
            )
        return self.detect_frame(frame, expected_frame=expected_frame)

    def detect_frame(self, frame: np.ndarray, expected_frame: Any | None = None) -> DetectionResult:
        self.setup()
        assert self.yolo is not None

        img_h, img_w = frame.shape[:2]
        results = self.yolo(
            frame,
            conf=self.conf,
            iou=self.iou,
            imgsz=self.imgsz,
            classes=[self.target_class_id] if self.target_class_id is not None else None,
            verbose=False,
        )
        candidates = self._collect_candidates(results, img_w=img_w, img_h=img_h)

        if not candidates:
            return DetectionResult(
                found=False,
                target_class=self.target_class,
                confidence=0.0,
                bbox={},
                source="yolo",
                message="no YOLO candidate",
            )

        scored: list[dict[str, Any]] = []
        for cand in candidates:
            crop = self._crop(frame, cand)
            if crop is None:
                continue

            cand_hist = self._calc_hsv_hist(crop)

            hist_score = 0.0
            if self.template_hist is not None:
                hist_score = float(cv2.compareHist(self.template_hist, cand_hist, cv2.HISTCMP_CORREL))
                # cv2 correlation range can be [-1, 1]. Normalize loosely to [0, 1].
                hist_score = max(0.0, min(1.0, (hist_score + 1.0) / 2.0))
            elif self.last_hist is not None:
                hist_score = float(cv2.compareHist(self.last_hist, cand_hist, cv2.HISTCMP_CORREL))
                hist_score = max(0.0, min(1.0, (hist_score + 1.0) / 2.0))

            continuity_score = self._continuity_score(cand, img_w, img_h)
            conf_score = float(cand["conf"])

            if self.template_hist is not None or self.last_hist is not None:
                final_score = 0.55 * hist_score + 0.35 * continuity_score + 0.10 * conf_score
            else:
                final_score = 0.70 * conf_score + 0.30 * continuity_score

            cand = dict(cand)
            cand["hist_score"] = hist_score
            cand["continuity_score"] = continuity_score
            cand["final_score"] = final_score
            cand["hist"] = cand_hist
            scored.append(cand)

        if not scored:
            return DetectionResult(
                found=False,
                target_class=self.target_class,
                confidence=0.0,
                bbox={},
                source="hist_match",
                message="no valid crop",
            )

        best = max(scored, key=lambda c: c["final_score"])

        source = "hist_match" if (self.template_hist is not None or self.last_hist is not None) else "yolo_track"
        message = (
            f"hist={best['hist_score']:.3f}, continuity={best['continuity_score']:.3f}, "
            f"yolo={best['conf']:.3f}, final={best['final_score']:.3f}"
        )

        if self.template_hist is not None and best["hist_score"] < self.hist_threshold and not self.fallback_to_yolo:
            return DetectionResult(
                found=False,
                target_class=self.target_class,
                confidence=0.0,
                bbox={},
                source="hist_match",
                message=f"hist below threshold: {best['hist_score']:.3f} < {self.hist_threshold:.3f}",
            )

        result = self._candidate_to_result(best, img_w, img_h, source=source, score=float(best["final_score"]), message=message)
        self.last_bbox = result.bbox
        self.last_hist = best["hist"]

        # Slowly update template to adapt to illumination / pose changes.
        if self.template_hist is not None and self.template_update_alpha > 0:
            alpha = float(self.template_update_alpha)
            self.template_hist = cv2.normalize(
                (1.0 - alpha) * self.template_hist + alpha * best["hist"],
                None,
                alpha=0,
                beta=1,
                norm_type=cv2.NORM_MINMAX,
            )

        return result

    def _get_yolo_class_id(self, class_name: str) -> int:
        assert self.yolo is not None
        names = self.yolo.names
        for cls_id, name in names.items():
            if str(name) == class_name:
                return int(cls_id)
        raise ValueError(f"Class {class_name!r} not found in YOLO names: {names}")

    def _build_template_hist(self, template_paths: Iterable[str]) -> np.ndarray | None:
        hists: list[np.ndarray] = []
        for path_str in template_paths:
            path = Path(path_str)
            if not path.exists():
                self.logger.warning("Template image not found: %s", path)
                continue

            img = cv2.imread(str(path))
            if img is None:
                self.logger.warning("Failed to read template image: %s", path)
                continue

            crop = self._extract_template_crop(img)
            hist = self._calc_hsv_hist(crop)
            hists.append(hist)
            self.logger.info("Loaded template histogram: %s", path)

        if not hists:
            return None

        mean_hist = np.mean(np.stack(hists, axis=0), axis=0).astype(np.float32)
        return cv2.normalize(mean_hist, None, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)

    def _extract_template_crop(self, img: np.ndarray) -> np.ndarray:
        assert self.yolo is not None
        h, w = img.shape[:2]
        results = self.yolo(
            img,
            conf=max(0.10, min(self.conf, 0.35)),
            iou=self.iou,
            imgsz=self.imgsz,
            classes=[self.target_class_id] if self.target_class_id is not None else None,
            verbose=False,
        )
        candidates = self._collect_candidates(results, img_w=w, img_h=h)
        if not candidates:
            return img
        best = max(candidates, key=lambda c: c["conf"] * max(c["w"] * c["h"], 1.0))
        crop = self._crop(img, best)
        return crop if crop is not None else img

    @staticmethod
    def _calc_hsv_hist(bgr_img: np.ndarray) -> np.ndarray:
        # Focus on center region to reduce background influence.
        h, w = bgr_img.shape[:2]
        margin_x = int(w * 0.12)
        margin_y = int(h * 0.08)
        roi = bgr_img[margin_y:max(margin_y + 1, h - margin_y), margin_x:max(margin_x + 1, w - margin_x)]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

        # H/S histogram is fast and robust enough for clothing/color differences.
        hist = cv2.calcHist([hsv], [0, 1], None, [24, 24], [0, 180, 0, 256])
        hist = cv2.normalize(hist, None, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
        return hist.astype(np.float32)

    def _continuity_score(self, cand: dict[str, float], img_w: int, img_h: int) -> float:
        if self.last_bbox is None:
            return 0.5

        last_cx = float(self.last_bbox["cx"]) * img_w
        last_cy = float(self.last_bbox["cy"]) * img_h
        dx = (cand["cx"] - last_cx) / max(img_w, 1)
        dy = (cand["cy"] - last_cy) / max(img_h, 1)
        dist = float((dx * dx + dy * dy) ** 0.5)

        # dist 0 -> 1.0, dist >= 0.5 -> close to 0.
        return max(0.0, min(1.0, 1.0 - 2.0 * dist))

    @staticmethod
    def _collect_candidates(results: Any, img_w: int, img_h: int) -> list[dict[str, float]]:
        boxes = results[0].boxes
        names = results[0].names
        candidates: list[dict[str, float]] = []

        for box in boxes:
            cls = int(box.cls[0])
            conf = float(box.conf[0])
            class_name = str(names[cls])
            x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
            x1 = max(0.0, min(float(img_w - 1), x1))
            y1 = max(0.0, min(float(img_h - 1), y1))
            x2 = max(0.0, min(float(img_w - 1), x2))
            y2 = max(0.0, min(float(img_h - 1), y2))
            if x2 <= x1 or y2 <= y1:
                continue
            w = x2 - x1
            h = y2 - y1
            candidates.append(
                {
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "w": w,
                    "h": h,
                    "cx": (x1 + x2) / 2.0,
                    "cy": (y1 + y2) / 2.0,
                    "conf": conf,
                    "class_name": class_name,
                }
            )
        return candidates

    @staticmethod
    def _crop(frame: np.ndarray, cand: dict[str, float], expand_ratio: float = 0.04) -> np.ndarray | None:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = cand["x1"], cand["y1"], cand["x2"], cand["y2"]
        bw = x2 - x1
        bh = y2 - y1
        x1 -= bw * expand_ratio
        x2 += bw * expand_ratio
        y1 -= bh * expand_ratio
        y2 += bh * expand_ratio

        x1i = max(0, int(round(x1)))
        y1i = max(0, int(round(y1)))
        x2i = min(w, int(round(x2)))
        y2i = min(h, int(round(y2)))
        if x2i <= x1i or y2i <= y1i:
            return None
        return frame[y1i:y2i, x1i:x2i].copy()

    def _candidate_to_result(
        self,
        cand: dict[str, float],
        img_w: int,
        img_h: int,
        source: str,
        score: float,
        message: str = "",
    ) -> DetectionResult:
        bbox = {
            "cx": float(cand["cx"]) / float(img_w),
            "cy": float(cand["cy"]) / float(img_h),
            "w": float(cand["w"]) / float(img_w),
            "h": float(cand["h"]) / float(img_h),
        }
        bbox["area"] = bbox["w"] * bbox["h"]
        bbox["x1"] = float(cand["x1"]) / float(img_w)
        bbox["y1"] = float(cand["y1"]) / float(img_h)
        bbox["x2"] = float(cand["x2"]) / float(img_w)
        bbox["y2"] = float(cand["y2"]) / float(img_h)

        return DetectionResult(
            found=True,
            target_class=self.target_class,
            confidence=float(score),
            bbox=bbox,
            source=source,
            message=message,
        )
