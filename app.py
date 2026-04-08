"""Top-level entry point for the minimal CamBot intelligent filming MVP."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from chain.planner import Planner
from chain.prompt_builder import PromptBuilder
from chain.retriever import LocalJsonRetriever
from chain.validator import PlanValidator
from runtime.cambot_executor import CamBotExecutor
from utils.io import load_yaml
from utils.logger import setup_logging


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the MVP application."""
    parser = argparse.ArgumentParser(description="CamBot MVP runner")
    parser.add_argument(
        "--instruction",
        type=str,
        help="Natural-language filming instruction. If omitted, interactive input is used.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/default.yaml",
        help="Path to YAML config file.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the CamBot MVP from instruction to printed control commands."""
    args = parse_args()
    repo_root = Path(__file__).resolve().parent
    config = load_yaml(repo_root / args.config)
    log_file = setup_logging(repo_root / config["app"]["log_dir"])
    logger = logging.getLogger("app")

    instruction = args.instruction or input("Enter filming instruction: ").strip()
    if not instruction:
        print("Instruction is required.")
        return 1

    try:
        retriever = LocalJsonRetriever(repo_root / "rag")
        retrieved = retriever.retrieve(
            query=instruction,
            top_k=int(config["planner"].get("top_k", 2)),
        )

        prompt = PromptBuilder().build(
            user_instruction=instruction,
            retrieved_context=retrieved,
        )
        raw_plan = Planner(config).plan(prompt)
        validated_plan = PlanValidator(config).validate_and_clip(raw_plan)

        print("Final structured JSON plan:")
        print(json.dumps(validated_plan.model_dump(), ensure_ascii=False, indent=2))
        logger.info("Validated plan: %s", validated_plan.model_dump_json())

        executor = CamBotExecutor(config=config, repo_root=str(repo_root))
        executor.execute(validated_plan)

        print(f"Logs saved to: {log_file}")
        return 0
    except Exception as exc:
        logger.exception("CamBot app failed: %s", exc)
        print(f"CamBot app failed: {exc}")
        print(f"Check logs: {log_file}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
