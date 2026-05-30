"""Chassis controller with shared TCP-server transport."""

from __future__ import annotations

import logging
import os
from typing import Optional

from runtime.tcp_server import ProjectTCPServer, TCPServerError


_SHARED_TCP_SERVER: Optional[ProjectTCPServer] = None


def get_shared_tcp_server(
    host: str | None = None,
    port: int | None = None,
) -> ProjectTCPServer:
    """Return a process-wide shared TCP server instance."""
    global _SHARED_TCP_SERVER
    if _SHARED_TCP_SERVER is None:
        _SHARED_TCP_SERVER = ProjectTCPServer(
            host=host or os.getenv("PROJECT_TCP_HOST", "0.0.0.0"),
            port=int(port or int(os.getenv("PROJECT_TCP_PORT", "2345"))),
        )
    return _SHARED_TCP_SERVER


class BaseController:
    """
    Chassis controller.

    Current ESP32-S3 behavior:
    - starts / reuses the shared PC-side TCP server
    - sends finite base JSON actions without requiring ACK
    - keeps message format aligned with the ESP team's current protocol
    - falls back to mock print/log behavior if no client is connected yet
    """

    def __init__(
        self,
        tcp_host: str | None = None,
        tcp_port: int | None = None,
    ) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self.connected = False
        self.tcp_server = get_shared_tcp_server(tcp_host, tcp_port)

    def connect(self) -> None:
        self.tcp_server.start()
        self.connected = True
        self.logger.info(
            "Base controller connected. TCP server ready on %s:%s. client_connected=%s",
            self.tcp_server.host,
            self.tcp_server.port,
            self.tcp_server.has_client(),
        )

    def move(self, linear_x: float, angular_z: float) -> None:
        """
        Local diagnostic velocity print.

        TimelineScript execution uses move_longitudinal or rotate instead.
        """
        command = f"[BASE CMD] linear_x={linear_x:.3f} angular_z={angular_z:.3f}"
        print(command)
        self.logger.info("%s (diagnostic only: velocity command not sent over TCP)", command)

    def move_longitudinal(self, distance_m: float, speed_m_s: float) -> None:
        command = (
            f"[BASE CMD] move_longitudinal distance_m={distance_m:.3f} "
            f"speed_m_s={speed_m_s:.3f}"
        )

        if self.tcp_server.has_client():
            payload = {
                "device": "base",
                "action": "move_longitudinal",
                "params": {
                    "distance_m": float(distance_m),
                    "speed_m_s": float(speed_m_s),
                },
            }
            self._send_payload(payload, command)
            return

        print(command)
        self.logger.info("%s (mock fallback: no TCP client)", command)

    def move_lateral(self, distance_m: float, speed_m_s: float) -> None:
        """
        The current ESP32-S3 protocol does not support move_lateral yet.
        Keep it local/mock for now.
        """
        command = (
            f"[BASE CMD] move_lateral distance_m={distance_m:.3f} "
            f"speed_m_s={speed_m_s:.3f}"
        )
        print(command)
        self.logger.info("%s (compat mode: ESP32-S3 protocol does not support move_lateral yet)", command)

    def rotate(self, radius_m: float, angular_speed_rad_s: float, angle_deg: float) -> None:
        command = (
            f"[BASE CMD] rotate radius_m={radius_m:.3f} "
            f"angular_speed_rad_s={angular_speed_rad_s:.3f} angle_deg={angle_deg:.3f}"
        )

        if self.tcp_server.has_client():
            payload = {
                "device": "base",
                "action": "rotate",
                "params": {
                    "radius_m": float(radius_m),
                    "angular_speed_rad_s": float(angular_speed_rad_s),
                    "angle_deg": float(angle_deg),
                },
            }
            self._send_payload(payload, command)
            return

        print(command)
        self.logger.info("%s (mock fallback: no TCP client)", command)

    def stop(self) -> None:
        command = "[BASE CMD] STOP"

        if self.tcp_server.has_client():
            payload = {
                "device": "base",
                "action": "stop",
                "params": {},
            }
            self._send_payload(payload, command)
            return

        print(command)
        self.logger.info("%s (mock fallback: no TCP client)", command)

    def _send_payload(self, payload: dict, command_text: str) -> None:
        try:
            self.tcp_server.send_json(payload)
            self.logger.info("%s sent (compat mode: no ACK required)", command_text)
        except TCPServerError as exc:
            self.logger.exception("Failed to send base command: %s", exc)
            raise

    def close(self) -> None:
        self.connected = False
        self.logger.info("Base controller closed.")
