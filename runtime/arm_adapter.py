"""Adapter around the existing RoArm motion API with a mock-safe fallback."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any


class ArmAdapter:
    """Wrap the existing mechanical arm code without refactoring it."""

    def __init__(self, repo_root: str | Path, arm_config: dict[str, Any]):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.repo_root = Path(repo_root)
        self.arm_config = arm_config
        self.client: Any | None = None
        self.connected = False

    def connect(self) -> None:
        if not self.arm_config.get("enabled", False):
            self.logger.info("Arm adapter running in disabled/mock mode.")
            return

        api_path = self.repo_root / "RoArm-M2-S_python" / "roarm_motion_api.py"
        spec = importlib.util.spec_from_file_location("roarm_motion_api", api_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load arm API from {api_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        arm_class = getattr(module, "RoArm3D")

        self.client = arm_class(
            port=self.arm_config["port"],
            baudrate=int(self.arm_config.get("baudrate", 115200)),
            verbose=True,
        )
        self.client.connect()
        self.connected = True
        self.logger.info("Arm adapter connected to existing RoArm implementation.")

    def execute_preset(self, name: str) -> None:
        command = f"[ARM CMD] preset={name}"
        print(command)
        self.logger.info(command)

        if not self.client:
            return
        if name == "init_pose":
            self.client.move_init_pose()

    def stop(self) -> None:
        command = "[ARM CMD] STOP"
        print(command)
        self.logger.info(command)

    def close(self) -> None:
        if self.client and self.connected:
            self.client.disconnect()
        self.connected = False
        self.logger.info("Arm adapter closed.")
