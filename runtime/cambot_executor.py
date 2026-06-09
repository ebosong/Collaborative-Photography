"""CamBot TimelineScript confirmation executor.

Role in the full workflow:
    1. Top-level Agent emits or revises a TimelineScript.
    2. User reviews it in the CamBot interactive shell.
    3. User types /confirm.
    4. This executor saves the confirmed script.
    5. This executor starts the real TimelineScheduler:
        - opens the dual camera display window if enabled
        - waits for required boards such as s31 and p4
        - sends S31/P4 protocol commands
        - waits for done ACKs
        - runs checkpoint vision correction

`run_timeline_script.py` remains a standalone debug entry.
For the normal demo, app.py + /confirm should use this executor.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from runtime.timeline_command_translator import (
    TimelineCommandTranslator,
    compact_json,
    expected_ack_for_payload,
)
from runtime.timeline_scheduler import TimelineScheduler
from schemas.timeline_script_schema import TimelineScript


class CamBotExecutor:
    """Save confirmed TimelineScript and execute it with TimelineScheduler."""

    def __init__(self, config: dict[str, Any], repo_root: str):
        self.config = config
        self.repo_root = Path(repo_root)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.translator = TimelineCommandTranslator()

    def execute(self, plan: TimelineScript) -> None:
        """
        Save the confirmed plan, write a protocol preview file, then execute.

        The interactive behavior is preserved by app.py:
            - before /confirm, the user can review and revise the plan
            - only after /confirm does this method run
        """
        output_dir = self._resolve_log_dir()
        output_dir.mkdir(parents=True, exist_ok=True)

        payload = plan.model_dump()

        output_path = output_dir / "confirmed_timeline_script.json"
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        preview_path = output_dir / "confirmed_timeline_s31_p4_preview.jsonl"
        self._write_protocol_preview(plan, preview_path)

        self.logger.info(
            "Confirmed TimelineScript saved to %s with %d timeline action(s) and %d lighting entrie(s).",
            output_path,
            len(plan.timeline),
            len(plan.lighting_plan),
        )

        print("=" * 80)
        print(f"[TIMELINE SCRIPT CONFIRMED] {plan.name}")
        print(f"[SAVED] {output_path}")
        print(f"[PREVIEW SAVED] {preview_path}")
        print("[EXECUTOR] Starting TimelineScheduler after confirmation...")

        scheduler_kwargs = self._build_scheduler_kwargs(payload)
        self._print_scheduler_config(scheduler_kwargs)

        scheduler = TimelineScheduler(plan=payload, **scheduler_kwargs)
        result_code = scheduler.run()

        if result_code == 0:
            print("[EXECUTOR DONE] TimelineScheduler finished successfully.")
        else:
            print(f"[EXECUTOR ERROR] TimelineScheduler exited with code {result_code}.")

    def _resolve_log_dir(self) -> Path:
        app_cfg = self.config.get("app", {}) or {}
        log_dir = str(app_cfg.get("log_dir", "logs"))
        log_path = Path(log_dir)
        if not log_path.is_absolute():
            log_path = self.repo_root / log_path
        return log_path

    def _write_protocol_preview(self, plan: TimelineScript, preview_path: Path) -> None:
        preview_items: list[dict[str, Any]] = []

        print("=" * 80)
        print(f"[TIMELINE SCRIPT] {plan.name} | actions={len(plan.timeline)} | lighting={len(plan.lighting_plan)}")
        print("[PREVIEW] TimelineScript -> S31/P4 command protocol")

        for index, action in enumerate(plan.timeline, start=1):
            print("=" * 80)
            print(f"[{index:02d}] {action.id} | {action.type}")
            print(f"desc: {action.description}")
            print(f"start_after: {action.start_after} start_at_s: {action.start_at_s}")

            for item in self.translator.translate_action(action):
                item_dict = item.as_dict()
                preview_items.append(item_dict)

                if item.kind == "send" and item.payload:
                    print(f"PORT {item.port} / BOARD {item.board_id} SEND:")
                    print(compact_json(item.payload))
                    print("EXPECTED DONE ACK:")
                    print(compact_json(expected_ack_for_payload(item.payload)))
                elif item.kind == "local":
                    print("LOCAL:", item.note)
                elif item.kind == "warning":
                    print("WARNING:", item.note)
                else:
                    print(f"{item.kind.upper()}:", item_dict)

        if plan.lighting_plan:
            print("=" * 80)
            print("[LIGHTING INTENT]")
            for light in plan.lighting_plan:
                item = self.translator.translate_lighting(light)
                preview_items.append(item.as_dict())
                print(item.note)

        with preview_path.open("w", encoding="utf-8") as f:
            for item in preview_items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def _build_scheduler_kwargs(self, payload: dict[str, Any]) -> dict[str, Any]:
        app_cfg = self.config.get("app", {}) or {}
        runtime_cfg = self.config.get("runtime", {}) or {}
        scheduler_cfg = runtime_cfg.get("timeline_scheduler", {}) or {}
        vision_cfg = self.config.get("vision", {}) or {}
        hardware_cfg = self.config.get("hardware", {}) or {}

        # Common config aliases, so this works with slightly different yaml structures.
        host = str(
            scheduler_cfg.get(
                "host",
                runtime_cfg.get("host", hardware_cfg.get("host", "0.0.0.0")),
            )
        )

        ack_timeout_s = float(
            scheduler_cfg.get(
                "ack_timeout_s",
                runtime_cfg.get("ack_timeout_s", hardware_cfg.get("ack_timeout_s", 20.0)),
            )
        )

        connect_timeout_s = float(
            scheduler_cfg.get(
                "connect_timeout_s",
                runtime_cfg.get("connect_timeout_s", hardware_cfg.get("connect_timeout_s", 0.0)),
            )
        )

        camera_index = int(
            scheduler_cfg.get(
                "camera_index",
                vision_cfg.get("camera_index", app_cfg.get("camera_index", 1)),
            )
        )

        yolo_model_path = self._resolve_optional_path(
            scheduler_cfg.get(
                "yolo_model_path",
                vision_cfg.get("yolo_model_path", vision_cfg.get("yolo_model", "models/yolo26n.pt")),
            )
        )

        template_paths = self._resolve_template_paths(
            scheduler_cfg.get(
                "template_paths",
                vision_cfg.get("template_paths", os.getenv("VISION_TEMPLATE_PATHS", "")),
            )
        )

        imgsz = int(scheduler_cfg.get("imgsz", vision_cfg.get("imgsz", 416)))
        conf = float(scheduler_cfg.get("conf", vision_cfg.get("conf", 0.25)))

        # For the demo, dual window is enabled by default after /confirm.
        show_window = self._as_bool(
            scheduler_cfg.get("show_window", runtime_cfg.get("show_window", app_cfg.get("show_window", True)))
        )

        # For safety: real execution by default. Set mock_acks=true in config for dry-run.
        mock_acks = self._as_bool(
            scheduler_cfg.get("mock_acks", runtime_cfg.get("mock_acks", app_cfg.get("mock_acks", False)))
        )

        expected_board_ids = self._parse_board_ids(
            scheduler_cfg.get(
                "expected_board_ids",
                runtime_cfg.get("expected_board_ids", hardware_cfg.get("expected_board_ids", ["s31", "p4"])),
            )
        )

        cam_width = int(scheduler_cfg.get("cam_width", vision_cfg.get("cam_width", 1280)))
        cam_height = int(scheduler_cfg.get("cam_height", vision_cfg.get("cam_height", 720)))
        display_width = int(scheduler_cfg.get("display_width", vision_cfg.get("display_width", 1600)))
        display_height = int(scheduler_cfg.get("display_height", vision_cfg.get("display_height", 720)))

        kwargs = {
            "host": host,
            "connect_timeout_s": connect_timeout_s,
            "default_ack_timeout_s": ack_timeout_s,
            "camera_index": camera_index,
            "yolo_model_path": yolo_model_path,
            "template_paths": template_paths,
            "imgsz": imgsz,
            "conf": conf,
            "show_window": show_window,
            "mock_acks": mock_acks,
            "cam_width": cam_width,
            "cam_height": cam_height,
            "display_width": display_width,
            "display_height": display_height,
            "expected_board_ids": expected_board_ids,
        }

        # Compatibility fallback for older TimelineScheduler versions.
        try:
            import inspect

            valid = set(inspect.signature(TimelineScheduler.__init__).parameters.keys())
            valid.discard("self")
            kwargs = {k: v for k, v in kwargs.items() if k in valid}
        except Exception:
            pass

        return kwargs

    def _print_scheduler_config(self, scheduler_kwargs: dict[str, Any]) -> None:
        print("=" * 80)
        print("[SCHEDULER CONFIG]")
        safe_cfg = dict(scheduler_kwargs)
        print(json.dumps(safe_cfg, ensure_ascii=False, indent=2))

        if scheduler_kwargs.get("mock_acks"):
            print("[MODE] mock_acks=True: no TCP command will be sent.")
        else:
            boards = scheduler_kwargs.get("expected_board_ids", ["s31", "p4"])
            print(f"[MODE] real hardware: waiting for boards {boards} before timeline execution.")

    def _resolve_optional_path(self, path_value: Any) -> str:
        if path_value is None:
            return ""
        path = Path(str(path_value))
        if path.is_absolute():
            return str(path)
        return str(self.repo_root / path)

    def _resolve_template_paths(self, value: Any) -> list[str]:
        raw_items: list[str] = []

        if value is None:
            return []

        if isinstance(value, str):
            # Support either semicolon or comma separated config/env string.
            split_items = value.replace(",", ";").split(";")
            raw_items.extend([item.strip() for item in split_items if item.strip()])
        elif isinstance(value, (list, tuple)):
            raw_items.extend([str(item).strip() for item in value if str(item).strip()])
        else:
            raw_items.append(str(value).strip())

        resolved: list[str] = []
        for item in raw_items:
            path = Path(item)
            if not path.is_absolute():
                path = self.repo_root / path
            resolved.append(str(path))
        return resolved

    @staticmethod
    def _parse_board_ids(value: Any) -> list[str]:
        if value is None:
            return ["s31", "p4"]

        if isinstance(value, str):
            items = value.replace(";", ",").split(",")
            boards = [item.strip() for item in items if item.strip()]
            return boards or ["s31", "p4"]

        if isinstance(value, (list, tuple, set)):
            boards = [str(item).strip() for item in value if str(item).strip()]
            return boards or ["s31", "p4"]

        return [str(value).strip()]

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        return text in {"1", "true", "yes", "y", "on"}
