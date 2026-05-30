"""Top-level entry point for the interactive CamBot planning agent.

This version performs a hardware connection precheck before asking for the
initial prompt:

    1. Start S3 TCP server on 2345.
    2. Start P4 TCP server on 2346.
    3. Wait until ESP32-S3 and ESP32-P4 are connected.
    4. Then ask the user for the filming prompt / or use --instruction.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

from agent import PlanAgentService
from runtime.base_controller import BaseController
from runtime.lift_controller import LiftController
from runtime.p4_arm_controller import P4ArmController
from utils.io import load_yaml
from utils.logger import setup_logging


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the interactive planning application."""
    parser = argparse.ArgumentParser(description="CamBot interactive agent runner")
    parser.add_argument(
        "--instruction",
        type=str,
        help="Initial natural-language filming instruction. If omitted, interactive input is used.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/default.yaml",
        help="Path to YAML config file.",
    )
    parser.add_argument(
        "--no-execute-after-confirm",
        action="store_true",
        help="Only save the confirmed plan and do not run the CamBot executor.",
    )
    parser.add_argument(
        "--skip-device-precheck",
        action="store_true",
        help="Skip waiting for ESP32-S3/P4 before asking for the prompt.",
    )
    parser.add_argument(
        "--device-wait-timeout",
        type=float,
        default=60.0,
        help="Seconds to wait for each ESP32 client during startup precheck.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the CamBot interactive planning loop."""
    args = parse_args()
    repo_root = Path(__file__).resolve().parent
    config = load_yaml(repo_root / args.config)
    log_file = setup_logging(repo_root / config["app"]["log_dir"])
    logger = logging.getLogger("app")

    precheck_handles: dict[str, Any] = {}

    try:
        if not args.skip_device_precheck:
            precheck_handles = wait_for_esp32_clients(
                config=config,
                timeout_s=float(args.device_wait_timeout),
            )

        # Important: the device precheck intentionally happens before reading
        # the prompt, so the user sees the hardware status first.
        instruction = args.instruction or input("Enter initial filming request: ").strip()
        if not instruction:
            print("Initial filming request is required.")
            return 1

        service = PlanAgentService(config=config, repo_root=repo_root)
        response = service.create_session(instruction)
        print(response.message)
        print(response.review)
        _print_help()

        while True:
            user_input = input("\nCamBot> ").strip()
            if not user_input:
                continue

            command = _normalize_command(user_input)
            if command in {"quit", "exit"}:
                print("Exited.")
                break
            if command == "help":
                _print_help()
                continue
            if command == "review":
                response = service.review_plan(response.session_id)
                print(response.review)
                continue
            if command == "confirm":
                if args.no_execute_after_confirm:
                    response = service.confirm_plan_only(response.session_id)
                else:
                    response = service.confirm_plan(response.session_id)
                logger.info("Confirmed plan: %s", json.dumps(response.plan, ensure_ascii=False))
                print(response.message)
                if args.no_execute_after_confirm:
                    print(response.review)
                    print("Plan confirmed and saved. Execution is disabled.")
                    continue
                break
            if command == "unconfirm":
                response = service.unconfirm_plan(response.session_id)
                print(response.message)
                continue

            response = service.send_message(response.session_id, user_input)
            print(response.message)
            if response.review:
                print(response.review)

        print(f"Main log saved to: {log_file}")
        print(f"Session log directory: {repo_root / 'logs' / 'sessions' / response.session_id}")
        return 0
    except Exception as exc:
        logger.exception("CamBot app failed: %s", exc)
        print(f"CamBot app failed: {exc}")
        print(f"Check log: {log_file}")
        return 1
    finally:
        # Do not aggressively stop shared TCP servers before normal process exit.
        # These controller objects are kept alive mainly so shared server singletons
        # stay initialized for the executor used after /confirm.
        for name, handle in precheck_handles.items():
            try:
                close = getattr(handle, "close", None)
                if callable(close):
                    close()
            except Exception:
                logger.debug("Ignoring close error for %s.", name, exc_info=True)


def wait_for_esp32_clients(config: dict[str, Any], timeout_s: float = 60.0) -> dict[str, Any]:
    """
    Start TCP servers and wait for S3/P4 clients before asking for the user prompt.

    Returns the controller handles so their shared TCP server singletons remain
    initialized until the process exits.
    """
    print("\n================ ESP32 connection precheck ================")
    print("Please connect devices before entering the filming prompt:")
    print("  ESP32-S3 -> PC hotspot IP : 2345   (base + lift)")
    print("  ESP32-P4 -> PC hotspot IP : 2346   (arm)")
    print("P4 should send ready if its firmware supports it:")
    print('  {"device":"p4","event":"ready"}')
    print("===========================================================\n")

    base = BaseController()
    lift = LiftController(initial_height=float(config["limits"]["height_m"]["default"]))
    p4 = P4ArmController()

    print("[1/3] Starting S3 TCP server on port 2345...")
    base.connect()
    lift.connect()
    print("[S3] TCP server is listening on 0.0.0.0:2345")

    print("[2/3] Starting P4 TCP server on port 2346...")
    p4.connect()
    print("[P4] TCP server is listening on 0.0.0.0:2346")

    print(f"[3/3] Waiting for ESP32-S3 client, timeout={timeout_s:.1f}s ...")
    if not _wait_controller_client(base, timeout_s=timeout_s):
        raise RuntimeError(
            "ESP32-S3 did not connect during startup precheck. "
            "Check Wi-Fi hotspot, PC IP, and port 2345."
        )
    print("[S3] connected.")

    print(f"[3/3] Waiting for ESP32-P4 client, timeout={timeout_s:.1f}s ...")
    if not _wait_p4_ready_or_client(p4, timeout_s=timeout_s):
        raise RuntimeError(
            "ESP32-P4 did not connect during startup precheck. "
            "Check Wi-Fi hotspot, PC IP, and port 2346."
        )
    print("[P4] connected.")

    print("\nAll required ESP32 clients are connected. You can now enter the filming prompt.\n")
    return {"base": base, "lift": lift, "p4": p4}


def _wait_controller_client(controller: Any, timeout_s: float) -> bool:
    """Wait for a controller's TCP client using whichever API exists."""
    if hasattr(controller, "wait_for_client"):
        return bool(controller.wait_for_client(timeout_s=timeout_s))

    tcp_server = getattr(controller, "tcp_server", None)
    if tcp_server is not None and hasattr(tcp_server, "wait_for_client"):
        return bool(tcp_server.wait_for_client(timeout_s=timeout_s))

    if hasattr(controller, "has_client"):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if controller.has_client():
                return True
            time.sleep(0.1)
    return False


def _wait_p4_ready_or_client(p4: Any, timeout_s: float) -> bool:
    """
    Prefer P4's ready handshake when available, otherwise accept plain TCP client.

    Some earlier P4 firmware only connected and sent raw messages; newer firmware
    may send {"device":"p4","event":"ready"}. This function stays compatible.
    """
    if hasattr(p4, "wait_for_ready"):
        try:
            if bool(p4.wait_for_ready(timeout_s=timeout_s)):
                return True
        except Exception:
            # Fall back to client-only wait below.
            pass

    return _wait_controller_client(p4, timeout_s=timeout_s)


def _print_help() -> None:
    """Print CLI commands for the interactive agent."""
    print(
        "\nCommands:\n"
        "  /review     show the current natural-language filming plan\n"
        "  /confirm    confirm, save, and execute the current plan\n"
        "  /unconfirm  cancel confirmation and keep editing\n"
        "  /quit       exit\n"
        "Type natural-language feedback to revise the current plan."
    )


def _normalize_command(text: str) -> str:
    """Normalize CLI commands from ASCII, full-width, and Chinese aliases."""
    command = text.strip().lower()
    command = command.replace("／", "/")
    command = command.lstrip("/").strip()
    compact = "".join(command.split())

    aliases = {
        "unconfirm": {"unconfirm", "cancel", "cancelconfirm", "取消确认", "取消確認"},
        "confirm": {"confirm", "确认", "確認"},
        "review": {"review", "show", "查看", "预览", "預覽"},
        "quit": {"quit", "exit", "退出"},
        "help": {"help", "帮助", "幫助"},
    }
    for normalized, values in aliases.items():
        if compact in values:
            return normalized

    # Some terminals mangle full-width command prefixes but keep the ASCII word.
    if len(compact) <= 12:
        for alias in ("unconfirm", "confirm", "review", "quit", "exit", "help"):
            if alias in compact:
                return "quit" if alias == "exit" else alias
    return command


if __name__ == "__main__":
    raise SystemExit(main())
