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
        "--execute-after-confirm",
        action="store_true",
        help="Run the CamBot executor after a confirmed plan is available.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the CamBot interactive planning loop."""
    args = parse_args()
    repo_root = Path(__file__).resolve().parent
    config = load_yaml(repo_root / args.config)
    log_file = setup_logging(repo_root / config["app"]["log_dir"])
    logger = logging.getLogger("app")

    instruction = args.instruction or input("请输入初始拍摄需求: ").strip()
    if not instruction:
        print("需要先输入拍摄需求。")
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

            command = user_input.lower()
            if command in {"/quit", "/exit"}:
                print("已退出。")
                break
            if command in {"/help", "help"}:
                _print_help()
                continue
            if command == "/review":
                response = service.review_plan(response.session_id)
                print(response.review)
                continue
            if command == "/confirm":
                response = service.confirm_plan(response.session_id)
                logger.info("Confirmed plan: %s", json.dumps(response.plan, ensure_ascii=False))
                print(response.message)
                print(response.review)
                if args.execute_after_confirm:
                    final_plan = service.execute_confirmed_plan(response.session_id)
                    executor = CamBotExecutor(config=config, repo_root=str(repo_root))
                    executor.execute(final_plan)
                    break
                print("你可以继续输入修改意见来取消确认并更新方案，或输入 /quit 结束。")
                continue
            if command == "/unconfirm":
                response = service.unconfirm_plan(response.session_id)
                print(response.message)
                continue

            response = service.send_message(response.session_id, user_input)
            print(response.message)
            if response.review:
                print(response.review)

        print(f"主日志保存到: {log_file}")
        print(f"会话日志目录: {repo_root / 'logs' / 'sessions' / response.session_id}")
        return 0
    except Exception as exc:
        logger.exception("CamBot app failed: %s", exc)
        print(f"CamBot 运行失败: {exc}")
        print(f"请检查日志: {log_file}")
        return 1


def _print_help() -> None:
    """Print CLI commands for the interactive agent."""
    print(
        "\n可用命令：\n"
        "  /review     查看当前自然语言拍摄方案\n"
        "  /confirm    确认并保存当前方案\n"
        "  /unconfirm  取消确认，继续修改\n"
        "  /quit       退出\n"
        "直接输入自然语言，即可让 Agent 修改当前方案。"
    )


if __name__ == "__main__":
    raise SystemExit(main())
