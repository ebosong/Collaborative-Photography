"""Reserved lift interface with internal mock state and printed actions."""

from __future__ import annotations

import logging


class LiftController:
    """Mock lift controller that logs and prints movements without hardware."""

    def __init__(self, initial_height: float = 1.0) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self.connected = False
        self.height_m = float(initial_height)

    def connect(self) -> None:
        self.connected = True
        self.logger.info("Lift controller connected in mock mode at height %.2f m.", self.height_m)

    def move_to(self, height: float) -> None:
        self.height_m = float(height)
        command = f"[LIFT CMD] move_to height_m={self.height_m:.3f}"
        print(command)
        self.logger.info(command)

    def move_by(self, delta: float) -> None:
        self.height_m += float(delta)
        command = f"[LIFT CMD] move_by delta={delta:.3f} new_height_m={self.height_m:.3f}"
        print(command)
        self.logger.info(command)

    def stop(self) -> None:
        command = "[LIFT CMD] STOP"
        print(command)
        self.logger.info(command)

    def get_height(self) -> float:
        return self.height_m

    def close(self) -> None:
        self.connected = False
        self.logger.info("Lift controller closed.")
