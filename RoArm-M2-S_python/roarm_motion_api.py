import json
import math
import time
from typing import Dict, Optional

import serial


class RoArm3D:
    """
    RoArm-M2-S 机械臂串口控制接口

    特性：
    1. 保留基础六方向移动接口
    2. 支持二维/三维组合运动
    3. 使用三次多项式插值，实现平滑起停
    4. 默认保持末端姿态 t 不变
    5. 支持查询真实反馈并同步缓存位姿
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        timeout: float = 0.2,
        init_x_mm: float = 150.0,
        init_y_mm: float = 0.0,
        init_z_mm: float = 200.0,
        init_t_rad: float = 3.14,
        sample_period_s: float = 0.05,
        min_steps: int = 10,
        verbose: bool = True,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.sample_period_s = float(sample_period_s)
        self.min_steps = int(min_steps)
        self.verbose = verbose

        self.ser: Optional[serial.Serial] = None

        # 软件缓存的末端位姿（单位：mm / rad）
        self.x_mm = float(init_x_mm)
        self.y_mm = float(init_y_mm)
        self.z_mm = float(init_z_mm)
        self.t_rad = float(init_t_rad)

    # =========================
    # 串口基础
    # =========================
    def connect(self):
        if self.ser and self.ser.is_open:
            return

        self.ser = serial.Serial(
            self.port,
            baudrate=self.baudrate,
            timeout=self.timeout,
            dsrdtr=None,
        )
        self.ser.setRTS(False)
        self.ser.setDTR(False)
        time.sleep(0.2)

        if self.verbose:
            print(f"[INFO] Connected to {self.port}")

    def disconnect(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            if self.verbose:
                print(f"[INFO] Disconnected from {self.port}")

    def _ensure_connected(self):
        if not self.ser or not self.ser.is_open:
            raise RuntimeError("Serial port is not connected.")

    def _send_json(self, data: Dict):
        self._ensure_connected()
        msg = json.dumps(data, ensure_ascii=False)
        self.ser.write(msg.encode("utf-8") + b"\n")
        if self.verbose:
            print(f"[SEND] {msg}")

    def _read_line(self) -> str:
        self._ensure_connected()
        raw = self.ser.readline()
        if not raw:
            return ""

        try:
            text = raw.decode("utf-8", errors="ignore").strip()
        except Exception:
            text = str(raw)

        return text

    def _clear_input_buffer(self):
        self._ensure_connected()
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass


    def wait_until_quiet(self, quiet_time_s: float = 1.0, max_wait_s: float = 20.0):
        """
        等待串口安静下来：
        连续 quiet_time_s 时间没有读到“有效新内容”，就认为设备基本稳定

        这里会过滤掉大量 T=1041 的轨迹点回显，避免刷屏。
        """
        self._ensure_connected()

        start = time.time()
        last_rx_time = time.time()

        while time.time() - start < max_wait_s:
            text = self._read_line()
            if text:
                ignore_this_line = False

                # 尝试解析 JSON，过滤掉不重要的命令回显
                try:
                    data = json.loads(text)
                    if isinstance(data, dict):
                        # 这些通常只是命令/轨迹回显，不作为“设备仍在忙”的依据
                        if data.get("T") in (1041, 100, 105):
                            ignore_this_line = True
                except json.JSONDecodeError:
                    pass

                if not ignore_this_line:
                    last_rx_time = time.time()
                    if self.verbose:
                        print(f"[BOOT] {text}")
            else:
                if time.time() - last_rx_time >= quiet_time_s:
                    if self.verbose:
                        print("[INFO] Serial output is quiet now.")
                    return True

        if self.verbose:
            print("[WARN] wait_until_quiet timeout reached.")
        return False

    def prepare_after_init_pose(self, wait_first_s: float = 6.0, quiet_time_s: float = 1.0, max_wait_s: float = 20.0):
        """
        在发送 T=100 回初始位姿后调用：
        1. 先给机械臂留足动作/启动时间
        2. 再等串口日志刷完
        3. 清空输入缓冲区
        """
        if self.verbose:
            print(f"[INFO] Waiting {wait_first_s:.1f}s after init pose command...")
        time.sleep(wait_first_s)

        self.wait_until_quiet(quiet_time_s=quiet_time_s, max_wait_s=max_wait_s)
        self._clear_input_buffer()

        if self.verbose:
            print("[INFO] Device is prepared after init pose.")

    # =========================
    # 机械臂协议
    # =========================
    def _send_pose(self, x_mm: float, y_mm: float, z_mm: float, t_rad: float):
        """
        发送末端直控命令 T=1041
        """
        cmd = {
            "T": 1041,
            "x": round(float(x_mm), 2),
            "y": round(float(y_mm), 2),
            "z": round(float(z_mm), 2),
            "t": round(float(t_rad), 4),
        }
        self._send_json(cmd)

    def move_init_pose(self, wait_s: float = 6.0):
        """
        回到机械臂初始位姿
        对应命令：T=100
        """
        self._send_json({"T": 100})
        if self.verbose:
            print("[INFO] Sent init pose command (T=100).")
        time.sleep(wait_s)

    def query_feedback(self, retries: int = 30, delay_s: float = 0.08) -> Dict:
        """
        查询机械臂反馈：
        发送 T=105，期望收到 T=1051
        """

        self._ensure_connected()

        # 查询前先清缓存，尽量避免旧日志/旧回显干扰
        self._clear_input_buffer()

        self._send_json({"T": 105})

        for _ in range(retries):
            time.sleep(delay_s)
            text = self._read_line()
            if not text:
                continue

            if self.verbose:
                print(f"[RECV] {text}")

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue

            if not isinstance(data, dict):
                continue

            # 只认 1051，其他一律跳过
            if data.get("T") == 1051:
                return data

        raise RuntimeError("Failed to get valid feedback from arm.")

    def sync_pose_from_feedback(self) -> Dict:
        """
        用真实反馈同步当前末端位置缓存
        """
        data = self.query_feedback()

        if "x" in data:
            self.x_mm = float(data["x"])
        if "y" in data:
            self.y_mm = float(data["y"])
        if "z" in data:
            self.z_mm = float(data["z"])
        if "t" in data:
            self.t_rad = float(data["t"])

        pose = self.get_cached_pose()
        if self.verbose:
            print(f"[INFO] Synced pose: {pose}")
        return pose

    # =========================
    # 位姿管理
    # =========================
    def get_cached_pose(self) -> Dict:
        return {
            "x_mm": round(self.x_mm, 2),
            "y_mm": round(self.y_mm, 2),
            "z_mm": round(self.z_mm, 2),
            "t_rad": round(self.t_rad, 4),
        }

    def set_pose_mm(self, x=None, y=None, z=None, t=None):
        if x is not None:
            self.x_mm = float(x)
        if y is not None:
            self.y_mm = float(y)
        if z is not None:
            self.z_mm = float(z)
        if t is not None:
            self.t_rad = float(t)

        self._send_pose(self.x_mm, self.y_mm, self.z_mm, self.t_rad)

    # =========================
    # 插值与轨迹
    # =========================
    @staticmethod
    def _cubic_blend(s: float) -> float:
        """
        三次多项式时间缩放：
        f(s) = 3s^2 - 2s^3
        s ∈ [0,1]
        起点/终点速度均为 0
        """
        return 3.0 * s * s - 2.0 * s * s * s

    @staticmethod
    def _clamp_nonnegative(value: float, name: str):
        if value < 0:
            raise ValueError(f"{name} must be >= 0")

    @staticmethod
    def _require_positive(value: float, name: str):
        if value <= 0:
            raise ValueError(f"{name} must be > 0")

    def _plan_steps(self, path_length_mm: float, speed_mm_s: float) -> int:
        """
        按真实轨迹长度和期望速度规划步数
        """
        self._require_positive(speed_mm_s, "speed_mm_s")

        if path_length_mm <= 1e-9:
            return 0

        total_time_s = path_length_mm / speed_mm_s
        steps = max(self.min_steps, int(math.ceil(total_time_s / self.sample_period_s)))
        return steps

    def _execute_cubic_trajectory(
        self,
        start_x: float,
        start_y: float,
        start_z: float,
        target_x: float,
        target_y: float,
        target_z: float,
        t_rad: float,
        speed_cm_s: float,
    ) -> Dict:
        """
        用三次多项式插值执行空间直线轨迹
        """
        dx = target_x - start_x
        dy = target_y - start_y
        dz = target_z - start_z

        path_length_mm = math.sqrt(dx * dx + dy * dy + dz * dz)
        speed_mm_s = float(speed_cm_s) * 10.0
        self._require_positive(speed_mm_s, "speed_cm_s")

        steps = self._plan_steps(path_length_mm, speed_mm_s)
        if steps == 0:
            if self.verbose:
                print("[INFO] Zero displacement, no motion executed.")
            return self.get_cached_pose()

        if self.verbose:
            print(
                f"[INFO] Executing cubic trajectory: "
                f"path={path_length_mm:.2f} mm, steps={steps}, speed={speed_cm_s:.2f} cm/s"
            )

        for i in range(1, steps + 1):
            s = i / steps
            blend = self._cubic_blend(s)

            x = start_x + dx * blend
            y = start_y + dy * blend
            z = start_z + dz * blend

            self._send_pose(x, y, z, t_rad)
            time.sleep(self.sample_period_s)

        self.x_mm = target_x
        self.y_mm = target_y
        self.z_mm = target_z
        self.t_rad = t_rad

        pose = self.get_cached_pose()
        if self.verbose:
            print(f"[INFO] Motion done. New pose: {pose}")
        return pose

    # =========================
    # 方向与增量映射
    # =========================
    @staticmethod
    def _direction_to_delta_cm(direction: str, distance_cm: float):
        """
        方向到笛卡尔位移的映射
        当前约定：
        front -> x+
        back  -> x-
        left  -> y+
        right -> y-
        up    -> z+
        down  -> z-
        """
        direction = direction.strip().lower()

        if direction == "front":
            return distance_cm, 0.0, 0.0
        elif direction == "back":
            return -distance_cm, 0.0, 0.0
        elif direction == "left":
            return 0.0, distance_cm, 0.0
        elif direction == "right":
            return 0.0, -distance_cm, 0.0
        elif direction == "up":
            return 0.0, 0.0, distance_cm
        elif direction == "down":
            return 0.0, 0.0, -distance_cm
        else:
            raise ValueError(f"Unsupported direction: {direction}")

    # =========================
    # 核心接口
    # =========================
    def move_xyz(
        self,
        dx_cm: float = 0.0,
        dy_cm: float = 0.0,
        dz_cm: float = 0.0,
        speed_cm_s: float = 5.0,
        sync_first: bool = True,
    ) -> Dict:
        """
        三维组合增量运动：
        dx_cm, dy_cm, dz_cm 为相对当前位置的增量（单位 cm）
        使用三次多项式插值，同时驱动三个方向
        """
        self._require_positive(speed_cm_s, "speed_cm_s")

        if sync_first:
            self.sync_pose_from_feedback()

        start_x, start_y, start_z = self.x_mm, self.y_mm, self.z_mm
        target_x = start_x + float(dx_cm) * 10.0
        target_y = start_y + float(dy_cm) * 10.0
        target_z = start_z + float(dz_cm) * 10.0

        return self._execute_cubic_trajectory(
            start_x=start_x,
            start_y=start_y,
            start_z=start_z,
            target_x=target_x,
            target_y=target_y,
            target_z=target_z,
            t_rad=self.t_rad,
            speed_cm_s=speed_cm_s,
        )

    def move(
        self,
        direction: str,
        distance_cm: float,
        speed_cm_s: float,
        sync_first: bool = True,
    ) -> Dict:
        """
        基础六方向移动接口
        """
        self._clamp_nonnegative(distance_cm, "distance_cm")
        self._require_positive(speed_cm_s, "speed_cm_s")

        dx_cm, dy_cm, dz_cm = self._direction_to_delta_cm(direction, float(distance_cm))
        return self.move_xyz(
            dx_cm=dx_cm,
            dy_cm=dy_cm,
            dz_cm=dz_cm,
            speed_cm_s=speed_cm_s,
            sync_first=sync_first,
        )

    def move_combo(
        self,
        right_cm: float = 0.0,
        left_cm: float = 0.0,
        up_cm: float = 0.0,
        down_cm: float = 0.0,
        front_cm: float = 0.0,
        back_cm: float = 0.0,
        speed_cm_s: float = 5.0,
        sync_first: bool = True,
    ) -> Dict:
        """
        语义组合接口
        例如：
        - 右上：right_cm=5, up_cm=5
        - 右上前：right_cm=5, up_cm=5, front_cm=5
        """
        for name, value in {
            "right_cm": right_cm,
            "left_cm": left_cm,
            "up_cm": up_cm,
            "down_cm": down_cm,
            "front_cm": front_cm,
            "back_cm": back_cm,
        }.items():
            self._clamp_nonnegative(value, name)

        dx_cm = float(front_cm) - float(back_cm)
        dy_cm = float(left_cm) - float(right_cm)
        dz_cm = float(up_cm) - float(down_cm)

        return self.move_xyz(
            dx_cm=dx_cm,
            dy_cm=dy_cm,
            dz_cm=dz_cm,
            speed_cm_s=speed_cm_s,
            sync_first=sync_first,
        )

    # =========================
    # 便捷别名
    # =========================
    def move_left(self, distance_cm: float, speed_cm_s: float, sync_first: bool = True) -> Dict:
        return self.move("left", distance_cm, speed_cm_s, sync_first=sync_first)

    def move_right(self, distance_cm: float, speed_cm_s: float, sync_first: bool = True) -> Dict:
        return self.move("right", distance_cm, speed_cm_s, sync_first=sync_first)

    def move_up(self, distance_cm: float, speed_cm_s: float, sync_first: bool = True) -> Dict:
        return self.move("up", distance_cm, speed_cm_s, sync_first=sync_first)

    def move_down(self, distance_cm: float, speed_cm_s: float, sync_first: bool = True) -> Dict:
        return self.move("down", distance_cm, speed_cm_s, sync_first=sync_first)

    def move_front(self, distance_cm: float, speed_cm_s: float, sync_first: bool = True) -> Dict:
        return self.move("front", distance_cm, speed_cm_s, sync_first=sync_first)

    def move_back(self, distance_cm: float, speed_cm_s: float, sync_first: bool = True) -> Dict:
        return self.move("back", distance_cm, speed_cm_s, sync_first=sync_first)