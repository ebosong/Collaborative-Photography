"""Lift controller with shared ESP32-S3 TCP transport."""

from __future__ import annotations

import logging

from runtime.base_controller import get_shared_tcp_server
from runtime.tcp_server import TCPServerError


class LiftController:
    """
    Lift controller.

    Current behavior:
    - reuses the same PC-side TCP server as BaseController
    - ESP32-S3 connects to PC:2345
    - lift commands are sent as newline-delimited JSON
    - no ACK is required by default, aligned with current BaseController compat mode

    Protocol sent to ESP32-S3:
    - {"device":"lift","action":"move_delta","params":{"delta_cm":10.0}}
    - {"device":"lift","action":"move_to","params":{"height_m":1.2,"delta_cm":20.0}}
    - {"device":"lift","action":"stop","params":{}}
    """

    def __init__(
        self,
        initial_height: float = 1.0,
        require_ack: bool = False,
        ack_timeout_s: float = 3.0,
    ) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self.connected = False
        self.height_m = float(initial_height)
        self.require_ack = bool(require_ack)
        self.ack_timeout_s = float(ack_timeout_s)
        self.tcp_server = get_shared_tcp_server()

    def connect(self) -> None:
        self.tcp_server.start()
        self.connected = True
        self.logger.info(
            "Lift controller connected. TCP server ready on %s:%s. current_height=%.2f m client_connected=%s require_ack=%s",
            self.tcp_server.host,
            self.tcp_server.port,
            self.height_m,
            self.tcp_server.has_client(),
            self.require_ack,
        )

    def move_to(self, height: float) -> None:
        height = float(height)
        old_height = self.height_m
        delta_cm = (height - old_height) * 100.0
        self.height_m = height

        command = (
            f"[LIFT CMD] move_to height_m={height:.3f} "
            f"delta_cm={delta_cm:.2f}"
        )

        payload = {
            "device": "lift",
            "action": "move_to",
            "params": {
                "height_m": float(height),
                "delta_cm": float(delta_cm),
            },
        }

        self._send_or_mock(payload, command)

    def move_by(self, delta: float) -> None:
        """
        Move lift by a relative delta in meters.

        ScriptExecutor passes lift_delta as cm, then converts to meters before
        calling this method. This method converts it back to delta_cm for the
        ESP32-S3 protocol.
        """
        delta = float(delta)
        self.height_m += delta
        delta_cm = delta * 100.0

        command = (
            f"[LIFT CMD] move_by delta_m={delta:.3f} "
            f"delta_cm={delta_cm:.2f} new_height_m={self.height_m:.3f}"
        )

        payload = {
            "device": "lift",
            "action": "move_delta",
            "params": {
                "delta_cm": float(delta_cm),
            },
        }

        self._send_or_mock(payload, command)

    def stop(self) -> None:
        command = "[LIFT CMD] STOP"
        payload = {
            "device": "lift",
            "action": "stop",
            "params": {},
        }
        self._send_or_mock(payload, command)

    def get_height(self) -> float:
        return self.height_m

    def _send_or_mock(self, payload: dict, command_text: str) -> None:
        if self.tcp_server.has_client():
            self._send_payload(payload, command_text)
            return

        print(command_text)
        self.logger.info("%s (mock fallback: no TCP client)", command_text)

    def _send_payload(self, payload: dict, command_text: str) -> None:
        try:
            if self.require_ack:
                response = self.tcp_server.send_json_and_wait_ack(
                    payload,
                    timeout_s=self.ack_timeout_s,
                )
                self.logger.info("%s ack=%s", command_text, response)
            else:
                self.tcp_server.send_json(payload)
                self.logger.info("%s sent (no ACK required)", command_text)
        except (TCPServerError, TimeoutError) as exc:
            self.logger.exception("Failed to send lift command: %s", exc)
            raise

    def close(self) -> None:
        self.connected = False
        self.logger.info("Lift controller closed.")
