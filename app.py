"""Top-level entry point for the interactive CamBot planning agent."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from agent import PlanAgentService
from runtime.cambot_executor import CamBotExecutor
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
    return parser.parse_args()


def main() -> int:
    """Run the CamBot interactive planning loop."""
    args = parse_args()
    repo_root = Path(__file__).resolve().parent
    config = load_yaml(repo_root / args.config)
    log_file = setup_logging(repo_root / config["app"]["log_dir"])
    logger = logging.getLogger("app")

    instruction = args.instruction or input("Enter initial filming request: ").strip()
    if not instruction:
        print("Initial filming request is required.")
        return 1

    try:
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
                response = service.confirm_plan(response.session_id)
                logger.info("Confirmed plan: %s", json.dumps(response.plan, ensure_ascii=False))
                print(response.message)
                if args.no_execute_after_confirm:
                    print(response.review)
                    print("Plan confirmed and saved. Execution is disabled.")
                    continue

                final_plan = service.execute_confirmed_plan(response.session_id)
                executor = CamBotExecutor(config=config, repo_root=str(repo_root))
                executor.execute(final_plan)
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
