from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from runtime.checkpoint_correction_planner import CheckpointCorrectionPlanner
from runtime.dual_camera_display import DualCameraDisplay
from runtime.subject_match_detector import SubjectMatchVisionDetector
from runtime.timeline_command_translator import TimelineCommandTranslator, compact_json


@dataclass
class TcpEndpoint:
    name: str
    board_id: str
    host: str
    port: int
    server_socket: Optional[socket.socket] = None
    client_socket: Optional[socket.socket] = None
    client_addr: Optional[tuple] = None
    connected_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    messages_by_cmd: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    ack_events_by_cmd: dict[str, threading.Event] = field(default_factory=dict)
    ready_payloads: list[dict[str, Any]] = field(default_factory=list)

    def start(self) -> None:
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(1)
        print(f"[{self.name}] listening on {self.host}:{self.port}")
        conn, addr = self.server_socket.accept()
        self.client_socket = conn
        self.client_addr = addr
        self.connected_event.set()
        print(f"[{self.name}] client connected from {addr}")
        threading.Thread(target=self._recv_loop, daemon=True).start()

    def _recv_loop(self) -> None:
        assert self.client_socket is not None
        buffer = ""
        while True:
            try:
                data = self.client_socket.recv(4096)
            except OSError as exc:
                print(f"[{self.name}] recv error: {exc}")
                break
            if not data:
                print(f"[{self.name}] client disconnected")
                break
            buffer += data.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                print(f"[{self.name} RECV] {line}")
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("type") == "ready":
                    with self.lock:
                        self.ready_payloads.append(payload)
                    continue
                cmd_id = payload.get("cmd_id")
                if payload.get("type") == "ack" and cmd_id:
                    cmd_id = str(cmd_id)
                    with self.lock:
                        self.messages_by_cmd.setdefault(cmd_id, []).append(payload)
                        event = self.ack_events_by_cmd.setdefault(cmd_id, threading.Event())
                        event.set()

    def wait_connected(self, timeout_s: float) -> bool:
        return self.connected_event.wait(timeout_s)

    def send_cmd(self, payload: dict[str, Any]) -> None:
        if self.client_socket is None:
            raise RuntimeError(f"{self.name} has no connected client")
        cmd_id = str(payload["cmd_id"])
        with self.lock:
            self.ack_events_by_cmd[cmd_id] = threading.Event()
            self.messages_by_cmd[cmd_id] = []
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        print(f"[{self.name} SEND] {text.rstrip()}")
        self.client_socket.sendall(text.encode("utf-8"))

    def read_messages(self, cmd_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.messages_by_cmd.get(cmd_id, []))

    def close(self) -> None:
        for sock in [self.client_socket, self.server_socket]:
            try:
                if sock is not None:
                    sock.close()
            except Exception:
                pass


class TimelineScheduler:
    def __init__(
        self,
        plan: dict[str, Any],
        host: str = "0.0.0.0",
        connect_timeout_s: float = 60.0,
        default_ack_timeout_s: float = 20.0,
        camera_index: int = 1,
        yolo_model_path: str = "models/yolo26n.pt",
        template_paths: list[str] | None = None,
        imgsz: int = 416,
        conf: float = 0.25,
        show_window: bool = False,
        mock_acks: bool = False,
        cam_width: int = 1280,
        cam_height: int = 720,
        display_width: int = 1600,
        display_height: int = 720,
        expected_board_ids: list[str] | None = None,
    ) -> None:
        self.plan = plan
        self.timeline = list(plan.get("timeline", []))
        self.lighting_plan = list(plan.get("lighting_plan", []))
        self.host = host
        self.connect_timeout_s = float(connect_timeout_s)
        self.default_ack_timeout_s = float(default_ack_timeout_s)
        self.camera_index = int(camera_index)
        self.yolo_model_path = yolo_model_path
        self.template_paths = template_paths or []
        self.imgsz = int(imgsz)
        self.conf = float(conf)
        self.show_window = bool(show_window)
        self.mock_acks = bool(mock_acks)
        self.cam_width = int(cam_width)
        self.cam_height = int(cam_height)
        self.display_width = int(display_width)
        self.display_height = int(display_height)
        self.expected_board_ids = [str(b).strip() for b in (expected_board_ids or ["s31", "p4"]) if str(b).strip()]

        self.translator = TimelineCommandTranslator()
        self.correction_planner = CheckpointCorrectionPlanner()
        self.display: DualCameraDisplay | None = None

        self.endpoints: dict[str, TcpEndpoint] = {}
        self.pending = {str(a.get("id")): a for a in self.timeline}
        self.done: set[str] = set()
        self.failed: set[str] = set()
        self.step_index = 0

    def run(self) -> int:
        """
        Run timeline scheduler.

        When show_window=True, OpenCV window rendering stays on the main thread.
        The actual timeline execution runs in a background worker thread.
        This keeps the 1280x720 dual-camera preview smooth and avoids Windows
        OpenCV gray-window/freezing issues.
        """
        print(f"[PLAN] {self.plan.get('name')} | version={self.plan.get('version')} mode={self.plan.get('mode')}")
        print(f"[SUMMARY] {self.plan.get('summary', '')}")
        print(f"[TIMELINE] {len(self.timeline)}")
        print(f"[LIGHTING] {len(self.lighting_plan)}")
        print(f"[MOCK_ACKS] {self.mock_acks}\n")

        self._validate_basic()
        self._start_display_if_needed()

        # If there is no visible window, run directly in the current thread.
        if self.display is None or not self.show_window:
            try:
                return self._run_timeline_worker()
            finally:
                for endpoint in self.endpoints.values():
                    endpoint.close()
                if self.display is not None:
                    self.display.stop()

        result_holder: dict[str, Any] = {"result": None, "exc": None}

        def worker() -> None:
            try:
                result_holder["result"] = self._run_timeline_worker()
            except Exception as exc:
                result_holder["exc"] = exc

        worker_thread = threading.Thread(target=worker, name="TimelineSchedulerWorker", daemon=True)
        worker_thread.start()

        try:
            # Main thread owns OpenCV imshow/waitKey.
            while worker_thread.is_alive():
                if not self.display.tick():
                    print("[DISPLAY CLOSED] User closed display window. Stopping scheduler.")
                    result_holder["result"] = 130
                    break
                time.sleep(1.0 / 60.0)

            worker_thread.join(timeout=1.0)

            if result_holder["exc"] is not None:
                raise result_holder["exc"]

            result = result_holder["result"]
            if result is None:
                result = 1

            if self.display is not None:
                self.display.set_normal("Timeline finished")
                self.display.pump(2.0)

            return int(result)
        finally:
            for endpoint in self.endpoints.values():
                endpoint.close()
            if self.display is not None:
                self.display.stop()

    def _run_timeline_worker(self) -> int:
        """Background execution body. Never calls cv2.imshow/waitKey."""
        self._start_required_endpoints()
        self._wait_user_start_confirmation()

        while self.pending:
            ready = self._ready_actions()
            if not ready:
                print("[SCHEDULER ERROR] No runnable action. Possible circular or missing dependency.")
                return 2

            ready.sort(key=self._action_sort_key)
            action = ready[0]
            ok = self._run_action(action)
            if not ok:
                self.failed.add(str(action.get("id")))
                if str(action.get("on_fail", "stop_all")) == "continue":
                    self.done.add(str(action.get("id")))
                    self.pending.pop(str(action.get("id")), None)
                    continue
                print(f"[STOP] action {action.get('id')} failed; stopping timeline.")
                return 3

        self._print_lighting_plan()
        print("=" * 80)
        print("[TIMELINE SCHEDULER DONE] all timeline actions completed.")
        print(f"done={sorted(self.done)}")
        return 0

    def _tick_display(self, duration_s: float = 0.0) -> bool:
        if self.display is None:
            return True
        if duration_s > 0:
            return self.display.pump(duration_s)
        return self.display.tick()

    def _start_display_if_needed(self) -> None:
        has_checkpoint = any(a.get("type") == "checkpoint" for a in self.timeline)
        if not (self.show_window or has_checkpoint):
            return
        self.display = DualCameraDisplay(
            camera_index=self.camera_index,
            cam_width=self.cam_width,
            cam_height=self.cam_height,
            display_width=self.display_width,
            display_height=self.display_height,
            enabled=self.show_window,
        )
        self.display.start()
        self.display.set_normal("Timeline running")

    def _validate_basic(self) -> None:
        ids = [str(action.get("id")) for action in self.timeline]
        if len(ids) != len(set(ids)):
            print("[PLAN WARNING] duplicate timeline action id exists")
        id_set = set(ids)
        for action in self.timeline:
            for dep in action.get("start_after", []) or []:
                if dep not in id_set:
                    print(f"[PLAN WARNING] action {action.get('id')!r} references missing dependency {dep!r}")

    def _required_boards(self) -> set[str]:
        """
        Boards required before timeline execution.

        expected_board_ids is the startup precheck list. By default we wait for:
            s31: camera car base + lift
            p4 : arm bridge

        Future expansion can pass:
            ["s31", "p4", "s32", "s33"]

        In addition, boards inferred from the plan are included automatically.
        """
        boards: set[str] = set(self.expected_board_ids)

        for action in self.timeline:
            for item in self.translator.translate_action(action):
                if item.kind == "send" and item.board_id:
                    boards.add(item.board_id)

        if any(a.get("type") == "checkpoint" for a in self.timeline):
            boards.add("s31")

        return boards

    def _start_required_endpoints(self) -> None:
        if self.mock_acks:
            print("[INFO] mock ACK mode enabled; no TCP server will be opened.")
            print(f"[INFO] startup board precheck skipped. expected_board_ids={self.expected_board_ids}")
            return

        board_to_port = {
            "s31": 2345,
            "p4": 2346,
            "s32": 2347,
            "s33": 2348,
        }

        required = self._required_boards()
        print(f"[INFO] waiting for boards before execution: {sorted(required)}")

        for board_id in sorted(required):
            if board_id not in board_to_port:
                print(f"[WARN] unknown board_id={board_id}; no TCP endpoint mapping, skipped.")
                continue

            port = board_to_port[board_id]
            endpoint = TcpEndpoint(
                name=f"{board_id.upper()}:{port}",
                board_id=board_id,
                host=self.host,
                port=port,
            )
            self.endpoints[board_id] = endpoint
            endpoint.start()

        remaining = set(self.endpoints.keys())
        start_time = time.monotonic()
        last_print_s = -1.0

        while remaining:
            for board_id in list(remaining):
                endpoint = self.endpoints[board_id]
                if endpoint.wait_connected(0.05):
                    print(f"\n[CONNECTED] {board_id} on port {endpoint.port}")
                    remaining.remove(board_id)

            if remaining:
                waited = time.monotonic() - start_time
                waiting_text = ", ".join(sorted(remaining))
                if waited - last_print_s >= 1.0:
                    print(f"[WAIT BOARDS] still waiting: {waiting_text} | elapsed={waited:.1f}s")
                    last_print_s = waited
                if self.display is not None:
                    self.display.set_normal(f"Waiting for boards: {waiting_text}")
                time.sleep(0.05)

        if self.display is not None:
            self.display.set_normal("All boards connected. Starting timeline.")

        print("[INFO] all required boards connected. Starting timeline execution.")

    def _wait_user_start_confirmation(self) -> None:
        """
        After all required boards are connected, wait for the operator to confirm
        before sending the first motion command.

        This gives the team time to check the robot, camera view, cables, and
        surrounding safety area. The display window stays alive because the
        scheduler worker waits for input while the main thread keeps refreshing UI.
        """
        print()
        print("=" * 80)
        print("[READY TO SHOOT]")
        print("All required boards are connected.")
        print("Check the robot surroundings, camera preview, lift/arm clearance, and emergency stop.")
        print("Press ENTER to start shooting and send the first command.")
        print("Type q then ENTER to cancel.")
        print("=" * 80)

        if self.display is not None:
            self.display.set_normal("Ready. Press ENTER in terminal to start.")

        user_input = input("[START CONFIRM] Press ENTER to start, or type q to cancel: ").strip().lower()
        if user_input in {"q", "quit", "exit", "cancel", "取消"}:
            raise RuntimeError("Shooting cancelled by operator before first command.")

        if self.display is not None:
            self.display.set_normal("Shooting started")

    def _ready_actions(self) -> list[dict[str, Any]]:
        return [a for a in self.pending.values() if all(str(dep) in self.done for dep in (a.get("start_after", []) or []))]

    def _action_sort_key(self, action: dict[str, Any]) -> tuple[float, int]:
        start_at = action.get("start_at_s")
        start_at_s = float(start_at) if start_at is not None else 0.0
        try:
            original_index = self.timeline.index(action)
        except ValueError:
            original_index = 999999
        return start_at_s, original_index

    def _run_action(self, action: dict[str, Any]) -> bool:
        self.step_index += 1
        action_id = str(action.get("id"))
        action_type = str(action.get("type"))
        if self.display is not None and action_type != "checkpoint":
            self.display.set_normal(f"Executing {action_id}: {action_type}")

        print("=" * 80)
        print(f"[STEP {self.step_index:02d}] RUN {action_id} | {action_type}")
        print(f"desc: {action.get('description', '')}")
        print(f"deps: {action.get('start_after', [])} start_at_s={action.get('start_at_s')}")
        print(f"blocking={action.get('blocking')} timeout_s={action.get('timeout_s')}")

        ok = self._run_checkpoint(action) if action_type == "checkpoint" else self._run_translated_action(action)
        if ok:
            self.done.add(action_id)
            self.pending.pop(action_id, None)
            print(f"[DONE] {action_id}")
            return True
        return False

    def _run_translated_action(self, action: dict[str, Any]) -> bool:
        action_type = str(action.get("type"))
        for item in self.translator.translate_action(action):
            if item.kind == "send" and item.payload:
                if not self._send_and_wait(item.payload, timeout_s=float(action.get("timeout_s") or self.default_ack_timeout_s)):
                    return False
            elif item.kind == "local":
                print("LOCAL ACTION:")
                print(item.note or "")
                if action_type == "wait":
                    duration_s = float((action.get("params") or {}).get("duration_s", 0.0))
                    if duration_s > 0 and not self._tick_display(duration_s):
                        return False
            elif item.kind == "warning":
                print("WARNING:", item.note)
                return False
            else:
                print(f"{item.kind.upper()}:", item.as_dict())
        return True

    def _send_and_wait(self, payload: dict[str, Any], timeout_s: float) -> bool:
        print("SEND CMD:")
        print(compact_json(payload))

        if self.mock_acks:
            mock_ms = self._mock_duration_ms(payload)
            if mock_ms > 0:
                # Sleep in worker only; main thread continues rendering the display.
                time.sleep(min(max(mock_ms / 1000.0, 0.25), 2.0))

            ack = {
                "type": "ack",
                "ok": True,
                "cmd_id": payload.get("cmd_id"),
                "board_id": payload.get("board_id"),
                "device": payload.get("device"),
                "action": payload.get("action"),
                "status": "done",
                "duration_ms": mock_ms,
                "msg": "mock done",
            }
            print("MOCK ACK:")
            print(compact_json(ack))
            return True

        board_id = str(payload.get("board_id"))
        endpoint = self.endpoints.get(board_id)
        if endpoint is None:
            print(f"[ERROR] no endpoint for board_id={board_id}")
            return False

        endpoint.send_cmd(payload)
        print(f"[WAIT DONE ACK] cmd_id={payload.get('cmd_id')} timeout={timeout_s:.1f}s")
        deadline = time.monotonic() + timeout_s
        cmd_id = str(payload.get("cmd_id"))

        while time.monotonic() < deadline:
            for ack in endpoint.read_messages(cmd_id):
                if str(ack.get("board_id")) != str(payload.get("board_id")):
                    continue
                if str(ack.get("device")) != str(payload.get("device")):
                    continue
                if str(ack.get("action")) != str(payload.get("action")):
                    continue
                if ack.get("ok") is True and ack.get("status") == "done":
                    print("DONE ACK:")
                    print(compact_json(ack))
                    return True
                if ack.get("status") == "started":
                    continue
                if ack.get("status") == "error" or ack.get("ok") is False:
                    print("ERROR ACK:")
                    print(compact_json(ack))
                    return False
            time.sleep(0.03)

        print(f"[ACK TIMEOUT] cmd_id={cmd_id}")
        return False

    def _run_checkpoint(self, action: dict[str, Any]) -> bool:
        checkpoint_id = str(action.get("id", "cp"))
        expected_frame = action.get("expected_frame", {}) or {}
        servo = action.get("servo", {}) or {}
        max_iters = int(servo.get("max_iters", 8))
        timeout_s = float(action.get("timeout_s") or 30.0)
        target_class = expected_frame.get("target_class", "person")

        print(f"[CHECKPOINT] id={checkpoint_id}")
        print(f"[EXPECTED_FRAME] {compact_json(expected_frame)}")
        print(f"[SERVO] {compact_json(servo)}")

        if self.display is None:
            self._start_display_if_needed()
        assert self.display is not None

        detector = SubjectMatchVisionDetector(
            yolo_model_path=self.yolo_model_path,
            target_class=target_class,
            template_paths=self.template_paths,
            conf=self.conf,
            imgsz=self.imgsz,
            fallback_to_yolo=True,
        )
        detector.setup()

        start_time = time.monotonic()
        try:
            for iter_index in range(1, max_iters + 1):
                if time.monotonic() - start_time > timeout_s:
                    print(f"[CHECKPOINT TIMEOUT] {checkpoint_id}")
                    return False

                frame = self.display.get_frame()
                if frame is None:
                    time.sleep(0.05)
                    continue

                # YOLO runs in worker thread. Display remains smooth because
                # the main thread keeps refreshing the latest camera frame.
                result = detector.detect_frame(frame)
                print("-" * 80)
                print(f"[CHECKPOINT ITER {iter_index:02d}] found={result.found} source={result.source} score={result.confidence:.3f}")

                if not result.found:
                    self.display.set_checkpoint(None, expected_frame, None, False, 0.0, "Subject not found")
                    time.sleep(0.15)
                    continue

                error = self.correction_planner.compute_error(result.bbox, expected_frame)
                satisfied = self.correction_planner.is_satisfied(error, expected_frame)
                error_dict = error.as_dict()

                print(f"[DETECTED_BBOX] {compact_json(result.bbox)}")
                print(f"[ERROR] {compact_json(error_dict)}")
                print(f"[SATISFIED] {satisfied}")

                self.display.set_checkpoint(
                    result.bbox,
                    expected_frame,
                    error_dict,
                    satisfied,
                    result.confidence,
                    f"checkpoint={checkpoint_id}, iter={iter_index}",
                )

                if satisfied:
                    print("[CHECKPOINT OK] No correction needed.")
                    self.display.set_normal("Checkpoint finished")
                    time.sleep(0.6)
                    return True

                commands = self.correction_planner.make_correction_commands(
                    checkpoint_id,
                    iter_index,
                    error,
                    expected_frame,
                    servo,
                )
                if not commands:
                    return False

                for cmd in commands:
                    if not self._send_and_wait(cmd, self.default_ack_timeout_s):
                        return False

                time.sleep(0.25)

            print(f"[CHECKPOINT MAX_ITERS] {checkpoint_id} did not satisfy after {max_iters} iterations.")
            self.display.set_normal("Checkpoint max iterations reached")
            time.sleep(0.8)
            return str(action.get("on_vision_fail", "continue")) == "continue"
        finally:
            detector.close()

    @staticmethod
    def _mock_duration_ms(payload: dict[str, Any]) -> int:
        action = payload.get("action")
        params = payload.get("params") or {}
        if action == "move_longitudinal":
            distance = abs(float(params.get("distance_m", 0.0)))
            speed = max(abs(float(params.get("speed_m_s", 0.1))), 1e-6)
            return int(distance / speed * 1000)
        if action == "rotate":
            angle = abs(float(params.get("angle_deg", 0.0)))
            return max(500, int(angle / 15.0 * 1000))
        if action == "move_delta":
            return max(500, int(abs(float(params.get("delta_cm", 0.0))) * 250))
        if action == "forward_raw":
            return 2000
        return 0

    def _print_lighting_plan(self) -> None:
        if not self.lighting_plan:
            return
        print("=" * 80)
        print("[LIGHTING INTENT]")
        for light in self.lighting_plan:
            print(compact_json(light))
