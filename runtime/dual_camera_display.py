from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


@dataclass
class DualViewStatus:
    mode: str = "normal"
    message: str = ""
    bbox: dict[str, float] | None = None
    expected_frame: dict[str, Any] | None = None
    error: dict[str, float] | None = None
    satisfied: bool = False
    score: float = 0.0


class DualCameraDisplay:
    """
    One physical camera, two side-by-side panels.

    Important Windows/OpenCV rule:
        Camera capture can run in a background thread,
        but cv2.imshow / cv2.waitKey should run in the main thread.

    Left panel:
        clean raw preview, used as final camera output demonstration.

    Right panel:
        normal stage -> clean raw preview
        checkpoint stage -> processed correction view
    """

    def __init__(
        self,
        camera_index: int = 1,
        cam_width: int = 1280,
        cam_height: int = 720,
        cam_fps: int = 30,
        display_width: int = 1600,
        display_height: int = 720,
        window_name: str = "Preview | Correction",
        enabled: bool = True,
    ) -> None:
        self.camera_index = int(camera_index)
        self.cam_width = int(cam_width)
        self.cam_height = int(cam_height)
        self.cam_fps = int(cam_fps)
        self.display_width = int(display_width)
        self.display_height = int(display_height)
        self.window_name = window_name
        self.enabled = bool(enabled)

        self.cap: cv2.VideoCapture | None = None
        self.lock = threading.Lock()
        self.latest_frame: np.ndarray | None = None
        self.status = DualViewStatus()
        self.running = False
        self.capture_thread: threading.Thread | None = None

        self.fps = 0.0
        self._frame_count = 0
        self._fps_start = time.monotonic()

    def start(self) -> None:
        if self.running:
            return

        self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            self.cap = cv2.VideoCapture(self.camera_index)

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cam_width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cam_height)
        self.cap.set(cv2.CAP_PROP_FPS, self.cam_fps)

        if not self.cap.isOpened():
            raise RuntimeError(f"Cannot open camera index {self.camera_index}")

        self.running = True

        if self.enabled:
            cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(self.window_name, self.display_width, self.display_height)

        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()

        # Let the camera warm up and render a few frames from main thread.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            self.tick()
            if self.get_frame() is not None:
                return
            time.sleep(0.03)

    def stop(self) -> None:
        self.running = False

        if self.capture_thread is not None:
            self.capture_thread.join(timeout=1.0)
            self.capture_thread = None

        if self.cap is not None:
            self.cap.release()
            self.cap = None

        if self.enabled:
            try:
                cv2.destroyWindow(self.window_name)
                cv2.waitKey(1)
            except Exception:
                pass

    def get_frame(self) -> np.ndarray | None:
        with self.lock:
            if self.latest_frame is None:
                return None
            return self.latest_frame.copy()

    def set_normal(self, message: str = "") -> None:
        with self.lock:
            self.status = DualViewStatus(mode="normal", message=message)

    def set_checkpoint(
        self,
        bbox: dict[str, float] | None,
        expected_frame: dict[str, Any],
        error: dict[str, float] | None,
        satisfied: bool,
        score: float = 0.0,
        message: str = "",
    ) -> None:
        with self.lock:
            self.status = DualViewStatus(
                mode="checkpoint",
                message=message,
                bbox=bbox,
                expected_frame=expected_frame,
                error=error,
                satisfied=satisfied,
                score=float(score),
            )

    def tick(self) -> bool:
        """
        Render one UI frame. Call this from the main thread.
        Returns False if user pressed q/ESC.
        """
        if not self.enabled:
            return True

        with self.lock:
            frame = None if self.latest_frame is None else self.latest_frame.copy()
            status = self.status

        if frame is None:
            blank = np.zeros((self.display_height, self.display_width, 3), dtype=np.uint8)
            cv2.putText(blank, "Waiting for camera...", (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            cv2.imshow(self.window_name, blank)
        else:
            cv2.imshow(self.window_name, self._make_combined_view(frame, status))

        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q")):
            self.running = False
            return False
        return True

    def pump(self, duration_s: float, fps: float = 30.0) -> bool:
        end = time.monotonic() + max(0.0, float(duration_s))
        interval = 1.0 / max(1.0, float(fps))
        while time.monotonic() < end:
            if not self.tick():
                return False
            time.sleep(interval)
        return True

    def _capture_loop(self) -> None:
        while self.running:
            assert self.cap is not None
            ok, frame = self.cap.read()
            if not ok or frame is None:
                time.sleep(0.02)
                continue

            now = time.monotonic()
            self._frame_count += 1
            if now - self._fps_start >= 1.0:
                self.fps = self._frame_count / max(now - self._fps_start, 1e-6)
                self._frame_count = 0
                self._fps_start = now

            with self.lock:
                self.latest_frame = frame.copy()

            time.sleep(0.001)

    def _make_combined_view(self, frame: np.ndarray, status: DualViewStatus) -> np.ndarray:
        left = self._put_panel_title(frame.copy(), "Preview")

        if status.mode == "checkpoint":
            right = self._draw_checkpoint_view(frame.copy(), status)
        else:
            right = frame.copy()
            if status.message:
                cv2.putText(right, status.message, (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, cv2.LINE_AA)
        right = self._put_panel_title(right, "Correction")

        separator_w = 12
        panel_w = max(1, (self.display_width - separator_w) // 2)
        panel_h = max(1, self.display_height)

        left_panel = self._resize_for_panel(left, panel_w, panel_h)
        right_panel = self._resize_for_panel(right, panel_w, panel_h)
        separator = np.zeros((panel_h, separator_w, 3), dtype=np.uint8)

        combined = np.hstack([left_panel, separator, right_panel])
        return np.ascontiguousarray(combined)

    def _draw_checkpoint_view(self, frame: np.ndarray, status: DualViewStatus) -> np.ndarray:
        h, w = frame.shape[:2]
        expected_frame = status.expected_frame or {}
        bbox = status.bbox
        error = status.error or {}

        exp = expected_frame.get("bbox", [0.5, 0.52, 0.35, 0.65])
        exp_cx, exp_cy, exp_w, exp_h = [float(v) for v in exp]
        ex1 = int((exp_cx - exp_w / 2) * w)
        ey1 = int((exp_cy - exp_h / 2) * h)
        ex2 = int((exp_cx + exp_w / 2) * w)
        ey2 = int((exp_cy + exp_h / 2) * h)

        cv2.rectangle(frame, (ex1, ey1), (ex2, ey2), (255, 0, 0), 2)
        cv2.circle(frame, (int(exp_cx * w), int(exp_cy * h)), 5, (255, 0, 0), -1)
        cv2.putText(frame, "Target frame", (max(10, ex1), max(25, ey1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 0, 0), 2, cv2.LINE_AA)

        if bbox:
            x1 = int(float(bbox["x1"]) * w)
            y1 = int(float(bbox["y1"]) * h)
            x2 = int(float(bbox["x2"]) * w)
            y2 = int(float(bbox["y2"]) * h)
            cx = int(float(bbox["cx"]) * w)
            cy = int(float(bbox["cy"]) * h)
            color = (0, 255, 0) if status.satisfied else (0, 165, 255)

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.circle(frame, (cx, cy), 5, (0, 0, 255), -1)
            cv2.line(frame, (cx, cy), (int(exp_cx * w), int(exp_cy * h)), (0, 255, 255), 2)

        status_text = "Checkpoint OK" if status.satisfied else "Correction running"
        cv2.putText(
            frame,
            f"{status_text} | score={status.score:.3f} | fps={self.fps:.1f}",
            (20, 68),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.70,
            (0, 255, 0) if status.satisfied else (0, 165, 255),
            2,
            cv2.LINE_AA,
        )

        err_text = (
            f"err_x={float(error.get('center_x', 0.0)):+.3f}  "
            f"err_y={float(error.get('center_y', 0.0)):+.3f}  "
            f"err_w={float(error.get('width', 0.0)):+.3f}  "
            f"err_h={float(error.get('height', 0.0)):+.3f}"
        )
        cv2.putText(frame, err_text, (20, h - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.66, (255, 255, 255), 2, cv2.LINE_AA)

        if status.message:
            cv2.putText(frame, status.message, (20, 98), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2, cv2.LINE_AA)

        return frame

    @staticmethod
    def _put_panel_title(img: np.ndarray, title: str) -> np.ndarray:
        out = img.copy()
        cv2.rectangle(out, (0, 0), (out.shape[1], 42), (20, 20, 20), -1)
        cv2.putText(out, title, (15, 29), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
        return out

    @staticmethod
    def _resize_for_panel(img: np.ndarray, panel_width: int, panel_height: int) -> np.ndarray:
        h, w = img.shape[:2]
        scale = min(panel_width / max(w, 1), panel_height / max(h, 1))
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        canvas = np.zeros((panel_height, panel_width, 3), dtype=np.uint8)
        x0 = (panel_width - new_w) // 2
        y0 = (panel_height - new_h) // 2
        canvas[y0:y0 + new_h, x0:x0 + new_w] = resized
        return canvas
