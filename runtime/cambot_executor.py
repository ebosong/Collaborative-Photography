"""CamBot TimelineScript confirmation adapter.

The top-level Agent now emits abstract TimelineScript JSON only. The concrete
S3/P4/YOLO/lighting scheduler is intentionally a lower-layer responsibility, so
this adapter persists/logs the confirmed timeline instead of dispatching hardware
commands directly.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from schemas.timeline_script_schema import TimelineScript


class CamBotExecutor:
    """Persist and print confirmed TimelineScript plans for lower-layer pickup."""

    def __init__(self, config: dict[str, Any], repo_root: str):
        self.config = config
        self.repo_root = Path(repo_root)
        self.logger = logging.getLogger(self.__class__.__name__)

    def execute(self, plan: TimelineScript) -> None:
        """Save the abstract timeline plan without sending hardware commands."""
        output_dir = self.repo_root / str(self.config.get("app", {}).get("log_dir", "logs"))
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "confirmed_timeline_script.json"
        payload = plan.model_dump()
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        self.logger.info(
            "Confirmed TimelineScript saved to %s with %d timeline action(s) and %d lighting entrie(s).",
            output_path,
            len(plan.timeline),
            len(plan.lighting_plan),
        )
        print(f"[TIMELINE SCRIPT] {plan.name} | actions={len(plan.timeline)} | lighting={len(plan.lighting_plan)}")
        print(f"[SAVED] {output_path}")
        print("[INFO] Hardware execution is delegated to the lower TimelineScript scheduler.")
