"""Reserved chassis interface that prints low-level commands instead of sending them."""

from __future__ import annotations

import logging


class BaseController:
    """Minimal mock chassis controller for safe MVP inspection."""

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self.connected = False

    def connect(self) -> None:
        self.connected = True
        self.logger.info("Base controller connected in mock mode.")

    def move(self, linear_x: float, angular_z: float) -> None:
        command = f"[BASE CMD] linear_x={linear_x:.3f} angular_z={angular_z:.3f}"
        print(command)
        self.logger.info(command)

    def stop(self) -> None:
        command = "[BASE CMD] STOP"
        print(command)
        self.logger.info(command)

    def close(self) -> None:
        self.connected = False
        self.logger.info("Base controller closed.")
