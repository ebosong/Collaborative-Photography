from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime.timeline_scheduler import TimelineScheduler


def main() -> int:
    parser = argparse.ArgumentParser(description="Run TimelineScript with S31/P4 TCP and checkpoint vision correction.")
    parser.add_argument("plan_json", help="Path to TimelineScript plan.json")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--wait-boards",
        default="s31,p4",
        help="Comma-separated boards to wait for before executing. Default: s31,p4. Future: s31,p4,s32,s33.",
    )
    parser.add_argument("--connect-timeout", type=float, default=0.0, help="Kept for compatibility; board waiting is indefinite.")
    parser.add_argument("--ack-timeout", type=float, default=20.0)
    parser.add_argument("--camera", type=int, default=1)
    parser.add_argument("--yolo-model", default="models/yolo26n.pt")
    parser.add_argument("--templates", default="", help="Semicolon-separated template image paths.")
    parser.add_argument("--imgsz", type=int, default=416)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--show-window", action="store_true")
    parser.add_argument("--mock-acks", action="store_true", help="Do not open TCP; print commands and simulate ACKs.")
    args = parser.parse_args()

    plan = json.loads(Path(args.plan_json).read_text(encoding="utf-8"))
    template_paths = [p.strip() for p in args.templates.split(";") if p.strip()]
    expected_board_ids = [b.strip() for b in args.wait_boards.split(",") if b.strip()]

    scheduler = TimelineScheduler(
        plan=plan,
        host=args.host,
        connect_timeout_s=args.connect_timeout,
        default_ack_timeout_s=args.ack_timeout,
        camera_index=args.camera,
        yolo_model_path=args.yolo_model,
        template_paths=template_paths,
        imgsz=args.imgsz,
        conf=args.conf,
        show_window=args.show_window,
        mock_acks=args.mock_acks,
        expected_board_ids=expected_board_ids,
    )
    return scheduler.run()


if __name__ == "__main__":
    raise SystemExit(main())
