from __future__ import annotations

import json
import logging
import socket
import threading
from collections import deque
from typing import Any, Callable


class TCPServerError(RuntimeError):
    """Raised when the TCP server cannot complete an operation."""


class ProjectTCPServer:
    """
    Persistent newline-delimited JSON TCP server for the PC side.

    Design:
    - PC acts as TCP server; ESP32 connects as TCP client.
    - One ProjectTCPServer instance keeps one latest active client.
    - Two instances can be used at the same time:
        2345 -> ESP32-S3
        2346 -> ESP32-P4
    - The server is intended to stay alive during the whole PC control session.
    - It supports:
        wait_for_client(...)
        wait_for_payload(...)
        send_json(...)
        send_json_and_wait_ack(...)
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 2345,
        logger: logging.Logger | None = None,
        history_size: int = 50,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.logger = logger or logging.getLogger(self.__class__.__name__)

        self.server_socket: socket.socket | None = None
        self.client_socket: socket.socket | None = None
        self.client_address: tuple[str, int] | None = None

        self.socket_lock = threading.RLock()
        self.response_condition = threading.Condition()
        self.last_response: dict[str, Any] | None = None
        self.payload_history: deque[dict[str, Any]] = deque(maxlen=max(1, int(history_size)))

        self.is_running = False
        self.accept_thread: threading.Thread | None = None
        self.recv_thread: threading.Thread | None = None

    # --------------------------
    # lifecycle
    # --------------------------
    def start(self) -> None:
        if self.is_running:
            self.logger.info("TCP server already running on %s:%s", self.host, self.port)
            return

        self.is_running = True
        self.accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.accept_thread.start()
        self.logger.info("TCP server started on %s:%s", self.host, self.port)

    def stop(self) -> None:
        self.logger.info("Stopping TCP server on %s:%s...", self.host, self.port)
        self.is_running = False

        with self.socket_lock:
            if self.client_socket is not None:
                try:
                    self.client_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    self.client_socket.close()
                except OSError:
                    pass
                self.client_socket = None
                self.client_address = None

            if self.server_socket is not None:
                try:
                    self.server_socket.close()
                except OSError:
                    pass
                self.server_socket = None

        with self.response_condition:
            self.response_condition.notify_all()

        if self.accept_thread is not None:
            self.accept_thread.join(timeout=2.0)
        if self.recv_thread is not None:
            self.recv_thread.join(timeout=2.0)

        self.logger.info("TCP server stopped on %s:%s.", self.host, self.port)

    # --------------------------
    # connection management
    # --------------------------
    def has_client(self) -> bool:
        with self.socket_lock:
            return self.client_socket is not None

    def wait_for_client(self, timeout_s: float = 10.0) -> bool:
        import time

        end_time = time.monotonic() + float(timeout_s)
        while time.monotonic() < end_time:
            if self.has_client():
                return True
            time.sleep(0.1)
        return False

    def wait_for_payload(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        timeout_s: float = 10.0,
    ) -> dict[str, Any] | None:
        """
        Wait until a received JSON payload matches predicate.

        This checks payload_history first. Therefore, if P4 sends ready before
        wait_for_payload() is called, the ready message will not be missed.
        """
        with self.response_condition:
            for payload in list(self.payload_history):
                if predicate(payload):
                    return payload

            ok = self.response_condition.wait_for(
                lambda: any(predicate(p) for p in self.payload_history),
                timeout=float(timeout_s),
            )
            if not ok:
                return None

            for payload in reversed(self.payload_history):
                if predicate(payload):
                    return payload
            return None

    def _accept_loop(self) -> None:
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(1)
            self.server_socket.settimeout(1.0)
        except OSError as exc:
            self.is_running = False
            self.logger.exception("Failed to start TCP server: %s", exc)
            raise TCPServerError(f"Failed to start TCP server on {self.host}:{self.port}: {exc}") from exc

        while self.is_running:
            try:
                client_socket, client_address = self.server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            self.logger.info("New client connected on %s:%s from %s", self.host, self.port, client_address)

            with self.socket_lock:
                if self.client_socket is not None:
                    try:
                        self.client_socket.close()
                    except OSError:
                        pass
                    self.logger.info("Previous client disconnected: %s", self.client_address)

                self.client_socket = client_socket
                self.client_address = client_address
                self.client_socket.settimeout(1.0)

            with self.response_condition:
                self.last_response = None
                self.payload_history.clear()
                self.response_condition.notify_all()

            self.recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
            self.recv_thread.start()

    def _recv_loop(self) -> None:
        buffer = ""

        while self.is_running:
            with self.socket_lock:
                sock = self.client_socket
                addr = self.client_address

            if sock is None:
                return

            try:
                data = sock.recv(4096)
                if not data:
                    self.logger.warning("Client disconnected from %s:%s: %s", self.host, self.port, addr)
                    self._clear_client(sock)
                    return
            except socket.timeout:
                continue
            except (ConnectionResetError, OSError) as exc:
                self.logger.warning("Client receive failed from %s on %s:%s: %s", addr, self.host, self.port, exc)
                self._clear_client(sock)
                return

            buffer += data.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                self._handle_line(line)

    def _clear_client(self, sock: socket.socket) -> None:
        with self.socket_lock:
            if self.client_socket is sock:
                try:
                    self.client_socket.close()
                except OSError:
                    pass
                self.client_socket = None
                self.client_address = None

        with self.response_condition:
            self.response_condition.notify_all()

    def _handle_line(self, line: str) -> None:
        self.logger.info("TCP recv on %s:%s: %s", self.host, self.port, line)

        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            self.logger.warning("Ignoring non-JSON message: %s", line)
            return

        with self.response_condition:
            self.last_response = payload
            self.payload_history.append(payload)
            self.response_condition.notify_all()

    # --------------------------
    # sending
    # --------------------------
    def send_text(self, text: str) -> None:
        if not text.endswith("\n"):
            text += "\n"

        with self.socket_lock:
            if self.client_socket is None or not self.is_running:
                raise TCPServerError("No active client connection.")

            sock = self.client_socket
            try:
                sock.sendall(text.encode("utf-8"))
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                self._clear_client(sock)
                raise TCPServerError(f"Failed to send message: {exc}") from exc

        self.logger.info("TCP send on %s:%s: %s", self.host, self.port, text.strip())

    def send_json(self, payload: dict[str, Any]) -> None:
        self.send_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    def send_json_and_wait_ack(
        self,
        payload: dict[str, Any],
        timeout_s: float = 3.0,
    ) -> dict[str, Any]:
        with self.response_condition:
            self.last_response = None
            self.payload_history.clear()

        self.send_json(payload)

        with self.response_condition:
            ok = self.response_condition.wait_for(
                lambda: self.last_response is not None,
                timeout=float(timeout_s),
            )
            if not ok:
                raise TimeoutError(f"Timed out waiting for ack within {timeout_s:.1f}s")

            response = self.last_response or {}

        if response.get("ok") is not True:
            raise TCPServerError(f"ESP32 returned non-ok response: {response}")
        return response

    # --------------------------
    # convenience helpers for future ACK-based base/lift protocol
    # --------------------------
    def send_base_longitudinal(self, distance_m: float, speed_m_s: float, timeout_s: float = 3.0) -> dict[str, Any]:
        payload = {
            "device": "base",
            "action": "move_longitudinal",
            "params": {"distance_m": distance_m, "speed_m_s": speed_m_s},
        }
        return self.send_json_and_wait_ack(payload, timeout_s=timeout_s)

    def send_base_lateral(self, distance_m: float, speed_m_s: float, timeout_s: float = 3.0) -> dict[str, Any]:
        payload = {
            "device": "base",
            "action": "move_lateral",
            "params": {"distance_m": distance_m, "speed_m_s": speed_m_s},
        }
        return self.send_json_and_wait_ack(payload, timeout_s=timeout_s)

    def send_base_rotate(
        self,
        radius_m: float,
        angular_speed_rad_s: float,
        angle_deg: float,
        timeout_s: float = 3.0,
    ) -> dict[str, Any]:
        payload = {
            "device": "base",
            "action": "rotate",
            "params": {
                "radius_m": radius_m,
                "angular_speed_rad_s": angular_speed_rad_s,
                "angle_deg": angle_deg,
            },
        }
        return self.send_json_and_wait_ack(payload, timeout_s=timeout_s)

    def send_lift_delta(self, delta_cm: float, timeout_s: float = 3.0) -> dict[str, Any]:
        payload = {
            "device": "lift",
            "action": "move_delta",
            "params": {"delta_cm": delta_cm},
        }
        return self.send_json_and_wait_ack(payload, timeout_s=timeout_s)
