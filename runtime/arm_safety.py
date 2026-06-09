from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ArmSafetyResult:
    ok: bool
    reason: str = ""
    raw_command: str | None = None
    adjusted: bool = False


class ArmSafetyChecker:
    """
    Configurable safety layer for RoArm raw commands before sending them to P4.

    Config file example, in project-root config.yaml:

    runtime:
      arm_safety:
        enabled: true
        mode: "clamp"        # clamp / warn_only / off
        x_min: 140.0
        x_max: 230.0
        y_min: -50.0
        y_max: 50.0
        z_min: 170.0
        z_max: 235.0
        t_min: 2.70
        t_max: 3.30
        spd_min: 0.05
        spd_max: 0.18
        reach_min: 205.0
        reach_max: 305.0
        min_x_at_high_z: 155.0
        max_z_at_low_x: 215.0

    Modes:
      - clamp:
          Unsafe T104 xyzt values are adjusted into the configured safe envelope.
      - warn_only:
          Print warning but send original raw command.
      - off:
          Disable safety checking and send original raw command.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        mode: str = "clamp",
        clamp_speed: bool = True,
        x_min: float = 140.0,
        x_max: float = 230.0,
        y_min: float = -50.0,
        y_max: float = 50.0,
        z_min: float = 170.0,
        z_max: float = 235.0,
        t_min: float = 2.70,
        t_max: float = 3.30,
        spd_min: float = 0.05,
        spd_max: float = 0.18,
        reach_min: float = 205.0,
        reach_max: float = 305.0,
        min_x_at_high_z: float = 155.0,
        max_z_at_low_x: float = 215.0,
    ) -> None:
        self.enabled = bool(enabled)
        self.mode = str(mode or "clamp").strip().lower()
        if self.mode not in {"clamp", "warn_only", "off"}:
            print(f"[ARM SAFETY CONFIG] Unknown mode={self.mode!r}; fallback to 'clamp'.")
            self.mode = "clamp"

        self.clamp_speed = bool(clamp_speed)

        self.x_min = float(x_min)
        self.x_max = float(x_max)
        self.y_min = float(y_min)
        self.y_max = float(y_max)
        self.z_min = float(z_min)
        self.z_max = float(z_max)
        self.t_min = float(t_min)
        self.t_max = float(t_max)
        self.spd_min = float(spd_min)
        self.spd_max = float(spd_max)

        self.reach_min = float(reach_min)
        self.reach_max = float(reach_max)

        self.min_x_at_high_z = float(min_x_at_high_z)
        self.max_z_at_low_x = float(max_z_at_low_x)

        self.safe_presets = {
            "init": '{"T":100}\n',
            "low": '{"T":104,"x":170,"y":0,"z":190,"t":3.05,"spd":0.15}\n',
            "mid": '{"T":104,"x":180,"y":0,"z":205,"t":3.05,"spd":0.15}\n',
            "high": '{"T":104,"x":185,"y":0,"z":220,"t":3.00,"spd":0.15}\n',
        }

    @classmethod
    def from_config_file(cls, config_path: str | Path = "config.yaml") -> "ArmSafetyChecker":
        path = Path(config_path)
        if not path.exists():
            return cls()

        try:
            import yaml  # type: ignore
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            print(f"[ARM SAFETY CONFIG] Failed to load {path}: {exc}; using default safety config.")
            return cls()

        cfg = data.get("runtime", {}).get("arm_safety", {})
        if not isinstance(cfg, dict):
            return cls()

        allowed_keys = {
            "enabled",
            "mode",
            "clamp_speed",
            "x_min",
            "x_max",
            "y_min",
            "y_max",
            "z_min",
            "z_max",
            "t_min",
            "t_max",
            "spd_min",
            "spd_max",
            "reach_min",
            "reach_max",
            "min_x_at_high_z",
            "max_z_at_low_x",
        }
        kwargs = {k: v for k, v in cfg.items() if k in allowed_keys}
        checker = cls(**kwargs)

        presets = cfg.get("safe_presets")
        if isinstance(presets, dict):
            for name, raw in presets.items():
                if isinstance(raw, str):
                    checker.safe_presets[str(name).strip().lower()] = raw if raw.endswith("\n") else raw + "\n"

        print(
            "[ARM SAFETY CONFIG] "
            f"enabled={checker.enabled}, mode={checker.mode}, "
            f"x=[{checker.x_min},{checker.x_max}], y=[{checker.y_min},{checker.y_max}], "
            f"z=[{checker.z_min},{checker.z_max}], t=[{checker.t_min},{checker.t_max}], "
            f"spd=[{checker.spd_min},{checker.spd_max}]"
        )
        return checker

    @staticmethod
    def _clamp(value: float, lower: float, upper: float) -> float:
        return max(lower, min(upper, float(value)))

    def sanitize_raw_command(self, raw_command: str, *, source_type: str = "") -> ArmSafetyResult:
        if not self.enabled or self.mode == "off":
            return ArmSafetyResult(True, "arm safety disabled by config", raw_command=self._ensure_newline(raw_command), adjusted=False)

        text = raw_command.strip()

        try:
            cmd = json.loads(text)
        except json.JSONDecodeError as exc:
            return ArmSafetyResult(False, f"raw_command is not valid JSON: {exc}")

        t_code = cmd.get("T")

        if t_code == 100:
            return ArmSafetyResult(True, "T100 init allowed", raw_command='{"T":100}\n', adjusted=(text != '{"T":100}'))

        if t_code != 104:
            return ArmSafetyResult(False, f"unsupported arm raw command T={t_code}; cannot safely clamp")

        missing = [key for key in ["x", "y", "z", "t"] if key not in cmd]
        if missing:
            return ArmSafetyResult(False, f"missing T104 field(s): {missing}")

        try:
            original_x = float(cmd["x"])
            original_y = float(cmd["y"])
            original_z = float(cmd["z"])
            original_t = float(cmd["t"])
            original_spd = float(cmd.get("spd", 0.15))
        except Exception as exc:
            return ArmSafetyResult(False, f"T104 field cannot be converted to float: {exc}")

        if self.mode == "warn_only":
            clamp_result = self._clamp_t104_values(original_x, original_y, original_z, original_t, original_spd)
            if clamp_result["adjusted"]:
                reason = self._format_adjust_reason(original_x, original_y, original_z, original_t, original_spd, clamp_result)
                return ArmSafetyResult(True, "[WARN_ONLY] " + reason, raw_command=self._ensure_newline(raw_command), adjusted=False)
            return ArmSafetyResult(True, "T104 command inside safety envelope", raw_command=self._ensure_newline(raw_command), adjusted=False)

        safe_values = self._clamp_t104_values(original_x, original_y, original_z, original_t, original_spd)

        safe_cmd = {
            "T": 104,
            "x": round(float(safe_values["x"]), 2),
            "y": round(float(safe_values["y"]), 2),
            "z": round(float(safe_values["z"]), 2),
            "t": round(float(safe_values["t"]), 4),
            "spd": round(float(safe_values["spd"]), 4),
        }

        safe_raw = json.dumps(safe_cmd, ensure_ascii=False, separators=(",", ":")) + "\n"

        if safe_values["adjusted"]:
            reason = self._format_adjust_reason(original_x, original_y, original_z, original_t, original_spd, safe_cmd)
        else:
            reason = "T104 command already inside safe envelope"

        return ArmSafetyResult(True, reason=reason, raw_command=safe_raw, adjusted=bool(safe_values["adjusted"]))

    def _clamp_t104_values(self, original_x: float, original_y: float, original_z: float, original_t: float, original_spd: float) -> dict[str, float | bool]:
        x = self._clamp(original_x, self.x_min, self.x_max)
        y = self._clamp(original_y, self.y_min, self.y_max)
        z = self._clamp(original_z, self.z_min, self.z_max)
        wrist_t = self._clamp(original_t, self.t_min, self.t_max)

        if original_spd <= 0:
            spd = self.spd_min
        elif self.clamp_speed:
            spd = self._clamp(original_spd, self.spd_min, self.spd_max)
        else:
            spd = original_spd

        reach = math.sqrt(x * x + z * z)
        if reach > 1e-6 and reach > self.reach_max:
            scale = self.reach_max / reach
            x *= scale
            z *= scale
            x = self._clamp(x, self.x_min, self.x_max)
            z = self._clamp(z, self.z_min, self.z_max)
        elif reach > 1e-6 and reach < self.reach_min:
            scale = self.reach_min / reach
            x *= scale
            z *= scale
            x = self._clamp(x, self.x_min, self.x_max)
            z = self._clamp(z, self.z_min, self.z_max)

        if x <= self.min_x_at_high_z and z > self.max_z_at_low_x:
            z = self.max_z_at_low_x

        adjusted = (
            abs(x - original_x) > 1e-6
            or abs(y - original_y) > 1e-6
            or abs(z - original_z) > 1e-6
            or abs(wrist_t - original_t) > 1e-6
            or abs(spd - original_spd) > 1e-6
        )

        return {
            "x": x,
            "y": y,
            "z": z,
            "t": wrist_t,
            "spd": spd,
            "adjusted": adjusted,
        }

    @staticmethod
    def _format_adjust_reason(original_x: float, original_y: float, original_z: float, original_t: float, original_spd: float, safe_values: dict[str, Any]) -> str:
        return (
            f"adjusted T104 from "
            f"x={original_x:.2f}, y={original_y:.2f}, z={original_z:.2f}, t={original_t:.4f}, spd={original_spd:.4f} "
            f"to x={float(safe_values['x']):.2f}, y={float(safe_values['y']):.2f}, z={float(safe_values['z']):.2f}, "
            f"t={float(safe_values['t']):.4f}, spd={float(safe_values['spd']):.4f}"
        )

    @staticmethod
    def _ensure_newline(text: str) -> str:
        return text if text.endswith("\n") else text + "\n"

    def check_raw_command(self, raw_command: str, *, source_type: str = "") -> ArmSafetyResult:
        return self.sanitize_raw_command(raw_command, source_type=source_type)

    def get_preset_raw_command(self, preset: str) -> str | None:
        return self.safe_presets.get(str(preset).strip().lower())
