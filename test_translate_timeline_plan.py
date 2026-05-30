from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def compact_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def translate_timeline_action(action: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Dry-run one TimelineScript action into the lower-layer protocol shape.

    This helper does not open TCP, connect to S3/P4, run YOLO, or move lighting.
    It only shows the abstract payloads a lower scheduler could derive later.
    """
    action_type = action.get("type")
    cmd_id = action.get("id", "")
    params = action.get("params", {}) if isinstance(action.get("params"), dict) else {}

    if action_type == "base_longitudinal":
        return [
            {
                "port": 2345,
                "payload": {
                    "cmd_id": cmd_id,
                    "device": "base",
                    "action": "move_longitudinal",
                    "params": {
                        "distance_m": round(float(params.get("distance_m") or 0.0), 4),
                        "speed_m_s": round(float(params.get("speed_m_s") or 0.0), 4),
                    },
                },
            }
        ]

    if action_type == "base_rotate":
        return [
            {
                "port": 2345,
                "payload": {
                    "cmd_id": cmd_id,
                    "device": "base",
                    "action": "rotate",
                    "params": {
                        "angle_deg": round(float(params.get("angle_deg") or 0.0), 3),
                        "angular_speed_rad_s": round(float(params.get("angular_speed_rad_s") or 0.0), 4),
                    },
                },
            }
        ]

    if action_type == "lift_delta":
        return [
            {
                "port": 2345,
                "payload": {
                    "cmd_id": cmd_id,
                    "device": "lift",
                    "action": "move_delta",
                    "params": {
                        "delta_cm": round(float(params.get("delta_cm") or 0.0), 3),
                    },
                },
            }
        ]

    if action_type == "arm_init_pose":
        return [
            {
                "port": 2346,
                "payload": {
                    "cmd_id": cmd_id,
                    "device": "arm",
                    "action": "init_pose",
                    "params": {
                        "wait_first_s": round(float(params.get("wait_first_s") or 0.0), 3),
                    },
                },
            }
        ]

    if action_type == "arm_move_delta":
        return [
            {
                "port": 2346,
                "payload": {
                    "cmd_id": cmd_id,
                    "device": "arm",
                    "action": "move_delta",
                    "params": params,
                },
            }
        ]

    if action_type == "arm_move_xyz":
        return [
            {
                "port": 2346,
                "payload": {
                    "cmd_id": cmd_id,
                    "device": "arm",
                    "action": "move_xyz",
                    "params": params,
                },
            }
        ]

    if action_type == "wait":
        return [{"note": f"wait locally for {float(params.get('duration_s') or 0.0):.3f}s"}]

    if action_type == "checkpoint":
        return [
            {
                "note": (
                    "checkpoint: pause timeline, run YOLO expected_frame evaluation, "
                    "and let visual servo generate correction actions"
                )
            }
        ]

    if action_type == "follow_mode":
        return [
            {
                "note": (
                    f"follow_mode: run closed-loop visual servo for "
                    f"{float(action.get('duration_s') or 0.0):.3f}s"
                )
            }
        ]

    return [{"warning": f"unsupported timeline action type: {action_type!r}"}]


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run TimelineScript -> lower-layer protocol preview")
    parser.add_argument("plan_json", help="Path to plan.json using TimelineScript format")
    args = parser.parse_args()

    path = Path(args.plan_json)
    plan = json.loads(path.read_text(encoding="utf-8"))

    timeline = plan.get("timeline", [])
    lighting_plan = plan.get("lighting_plan", [])
    print(f"[PLAN] {plan.get('name', path.name)} | version={plan.get('version')} mode={plan.get('mode')}")
    print(f"[TIMELINE] {len(timeline)}")
    print(f"[LIGHTING] {len(lighting_plan)}")
    print()

    for index, action in enumerate(timeline, start=1):
        print("=" * 80)
        print(f"[{index:02d}] {action.get('id')} | {action.get('type')}")
        print(f"desc: {action.get('description', '')}")
        print(f"start_after: {action.get('start_after', [])} start_at_s: {action.get('start_at_s')}")

        for item in translate_timeline_action(action):
            if "payload" in item:
                print(f"PORT {item['port']} SEND:")
                print(compact_json(item["payload"]))
            elif "note" in item:
                print("NOTE:", item["note"])
            elif "warning" in item:
                print("WARNING:", item["warning"])

    if lighting_plan:
        print("=" * 80)
        print("[LIGHTING INTENT]")
        for light in lighting_plan:
            print(compact_json(light))

    print("=" * 80)
    print("[DONE] lower-layer execution is not performed by this dry-run helper")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
