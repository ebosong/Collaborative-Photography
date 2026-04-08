"""Minimal target tracker interface with a deterministic mock sequence."""

from __future__ import annotations

import itertools
import logging
from typing import Any


class Tracker:
    """Mock-friendly tracker returning normalized target state dictionaries."""

    def __init__(self, tracker_config: dict[str, Any]):
        self.logger = logging.getLogger(self.__class__.__name__)
        sequence = tracker_config.get("mock_sequence") or [
            {"detected": True, "center_x": 0.5, "center_y": 0.5, "scale": 0.4}
        ]
        self._states = itertools.cycle(sequence)

    def get_target_state(self, frame: Any = None) -> dict[str, float | bool]:
        """Return the next tracked target state in normalized image coordinates."""
        del frame
        state = dict(next(self._states))
        self.logger.info("Tracker state: %s", state)
        return state
