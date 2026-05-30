from __future__ import annotations

import logging
import os
from typing import Optional

from runtime.tcp_server import ProjectTCPServer


_SHARED_P4_TCP_SERVER: Optional[ProjectTCPServer] = None


def get_shared_p4_tcp_server(
    host: str | None = None,
    port: int | None = None,
) -> ProjectTCPServer:
    """
    Dedicated TCP server for ESP32-P4 arm forwarding.

    Keep it separate from the S3 server by using another port.
    Default:
      - host: 0.0.0.0
      - port: 2346
    """
    global _SHARED_P4_TCP_SERVER
    if _SHARED_P4_TCP_SERVER is None:
        _SHARED_P4_TCP_SERVER = ProjectTCPServer(
            host=host or os.getenv("PROJECT_P4_TCP_HOST", "0.0.0.0"),
            port=int(port or int(os.getenv("PROJECT_P4_TCP_PORT", "2346"))),
        )
    return _SHARED_P4_TCP_SERVER


class P4ArmController:
    """
    PC-side controller for ESP32-P4 arm forwarding.

    Responsibilities:
    - start / reuse a dedicated TCP server for P4
    - send already-translated raw arm commands to P4
    - let P4 forward them over UART unchanged

    Current default mode:
    - compatibility mode, no ACK required
    - one JSON payload per line
    """

    def __init__(
        self,
        tcp_host: str | None = None,
        tcp_port: int | None = None,
        require_ack: bool = False,
        ack_timeout_s: float = 3.0,
    ) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self.connected = False
        self.require_ack = bool(require_ack)
        self.ack_timeout_s = float(ack_timeout_s)
        self.tcp_server = get_shared_p4_tcp_server(tcp_host, tcp_port)

    def connect(self) -> None:
        self.tcp_server.start()
        self.connected = True
        self.logger.info(
            "P4 arm controller connected. TCP server ready on %s:%s. client_connected=%s",
            self.tcp_server.host,
            self.tcp_server.port,
            self.tcp_server.has_client(),
        )

    def has_client(self) -> bool:
        return self.tcp_server.has_client()

    def wait_for_client(self, timeout_s: float = 30.0) -> bool:
        return self.tcp_server.wait_for_client(timeout_s=timeout_s)

    def send_raw_command(self, raw_command: str) -> None:
        payload = {
            "device": "arm",
            "action": "forward_raw",
            "params": {
                "raw_command": raw_command,
            },
        }
        self._send_payload(payload, f"[P4 ARM CMD] forward_raw {raw_command}")

    def stop(self) -> None:
        # Keep the stop format simple and raw-command based.
        # Adjust later if the P4 side wants another action type.
        self.send_raw_command('{"T":100}')

    def _send_payload(self, payload: dict, command_text: str) -> None:
        if not self.tcp_server.has_client():
            raise RuntimeError("ESP32-P4 is not connected to the PC TCP server.")

        if self.require_ack:
            response = self.tcp_server.send_json_and_wait_ack(
                payload,
                timeout_s=self.ack_timeout_s,
            )
            self.logger.info("%s ack=%s", command_text, response)
        else:
            self.tcp_server.send_json(payload)
            self.logger.info("%s sent (compat mode: no ACK required)", command_text)

    def close(self) -> None:
        self.connected = False
        self.logger.info("P4 arm controller closed.")
