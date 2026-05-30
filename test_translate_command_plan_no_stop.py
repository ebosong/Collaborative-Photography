from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def compact_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def translate_command(command: dict[str, Any], current_lift_height_m: float) -> tuple[list[dict[str, Any]], float]:
    target = command.get("target")
    action = command.get("action")
    cmd_id = command.get("id", "")

    outputs: list[dict[str, Any]] = []

    if action == "stop" and target in {"base", "lift", "arm"}:
        outputs.append({"note": f"{target}.stop is a local scheduling marker only; no TCP payload sent"})
        return outputs, current_lift_height_m

    if target == "base":
        if action == "connect":
            outputs.append({"note": "base.connect: no hardware payload; TCP server should already be started"})
            return outputs, current_lift_height_m

        if action == "move":
            linear_x = float(command.get("linear_x") or 0.0)
            angular_z = float(command.get("angular_z") or 0.0)
            duration_s = float(command.get("duration_s") or 0.0)

            if abs(linear_x) >= abs(angular_z):
                distance_m = linear_x * duration_s
                speed_m_s = abs(linear_x)
                outputs.append({
                    "port": 2345,
                    "payload": {
                        "device": "base",
                        "action": "move_longitudinal",
                        "cmd_id": cmd_id,
                        "params": {
                            "distance_m": round(distance_m, 4),
                            "speed_m_s": round(speed_m_s, 4),
                        },
                    },
                })
                return outputs, current_lift_height_m

            angle_deg = math.degrees(angular_z * duration_s)
            outputs.append({
                "port": 2345,
                "payload": {
                    "device": "base",
                    "action": "rotate",
                    "cmd_id": cmd_id,
                    "params": {
                        "radius_m": 0.0,
                        "angular_speed_rad_s": round(abs(angular_z), 4),
                        "angle_deg": round(angle_deg, 3),
                    },
                },
            })
            return outputs, current_lift_height_m

    if target == "lift":
        if action == "connect":
            outputs.append({"note": "lift.connect: no hardware payload; S3 uses the same 2345 connection"})
            return outputs, current_lift_height_m

        if action == "move_by":
            delta_m = float(command.get("delta_m") or 0.0)
            current_lift_height_m += delta_m
            outputs.append({
                "port": 2345,
                "payload": {
                    "device": "lift",
                    "action": "move_delta",
                    "cmd_id": cmd_id,
                    "params": {
                        "delta_cm": round(delta_m * 100.0, 3),
                    },
                },
            })
            return outputs, current_lift_height_m

        if action == "move_to":
            target_height_m = float(command.get("height_m") or current_lift_height_m)
            delta_m = target_height_m - current_lift_height_m
            current_lift_height_m = target_height_m
            outputs.append({
                "port": 2345,
                "payload": {
                    "device": "lift",
                    "action": "move_delta",
                    "cmd_id": cmd_id,
                    "params": {
                        "delta_cm": round(delta_m * 100.0, 3),
                    },
                },
            })
            return outputs, current_lift_height_m

    if target == "arm":
        if action == "connect":
            outputs.append({"note": "arm.connect: no hardware payload; P4 should connect to port 2346 and send ready"})
            return outputs, current_lift_height_m

        if action == "preset":
            preset = str(command.get("preset") or "ready").lower()
            if preset not in {"ready", "home", "init", "initial", "reset"}:
                outputs.append({"warning": f"unsupported arm preset={preset!r}; fallback to ready"})
            outputs.append({
                "port": 2346,
                "payload": {
                    "device": "arm",
                    "action": "forward_raw",
                    "cmd_id": cmd_id,
                    "params": {
                        "raw_command": compact_json({"T": 100}) + "\n",
                    },
                },
            })
            return outputs, current_lift_height_m

    if target == "wait" or action == "wait":
        outputs.append({"note": f"wait locally for {float(command.get('duration_s') or 0.0):.3f}s"})
        return outputs, current_lift_height_m

    outputs.append({"warning": f"unsupported command: target={target!r}, action={action!r}"})
    return outputs, current_lift_height_m


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run old command-format plan -> S3/P4 hardware protocol")
    parser.add_argument("plan_json", help="Path to plan.json using script+commands format")
    parser.add_argument("--initial-lift-height-m", type=float, default=1.0)
    args = parser.parse_args()

    path = Path(args.plan_json)
    plan = json.loads(path.read_text(encoding="utf-8"))

    commands = plan.get("commands", [])
    print(f"[PLAN] {plan.get('script', {}).get('title', path.name)}")
    print(f"[COMMANDS] {len(commands)}")
    print()

    current_lift_height_m = float(args.initial_lift_height_m)

    for index, command in enumerate(commands, start=1):
        print("=" * 80)
        print(f"[{index:02d}] {command.get('id')} | {command.get('target')}.{command.get('action')}")
        print(f"desc: {command.get('description', '')}")

        outputs, current_lift_height_m = translate_command(command, current_lift_height_m)

        for item in outputs:
            if "payload" in item:
                print(f"PORT {item['port']} SEND:")
                print(compact_json(item["payload"]))
            elif "note" in item:
                print("NOTE:", item["note"])
            elif "warning" in item:
                print("WARNING:", item["warning"])

    print("=" * 80)
    print(f"[DONE] final cached lift height = {current_lift_height_m:.3f} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
