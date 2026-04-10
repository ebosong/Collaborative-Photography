"""Minimal LangChain planning pipeline that returns raw model text."""

from __future__ import annotations

import logging
from typing import Any

from providers.llm_provider import LLMProvider


class Planner:
    """Thin planning wrapper around the provider and LangChain prompt chain."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.provider = LLMProvider(config)
        self.logger = logging.getLogger(self.__class__.__name__)

    def plan(self, prompt: str) -> str:
        """Call the provider and return the raw text response."""
        self.logger.info("Requesting filming plan from LLM provider.")

        try:
            from langchain_core.output_parsers import StrOutputParser
            from langchain_core.prompts import ChatPromptTemplate

            chat_prompt = ChatPromptTemplate.from_messages([("human", "{prompt}")])
            model = self.provider.build_chat_model()
        except Exception as exc:
            self.logger.info(
                "Using direct provider fallback because LangChain/live model setup is unavailable: %s",
                exc,
            )
            return self.provider.generate(prompt)

        chain = chat_prompt | model | StrOutputParser()
        try:
            return chain.invoke({"prompt": prompt})
        except Exception as exc:
            self.logger.warning(
                "LangChain planner request failed, using provider fallback: %s",
                exc,
            )
            return self.provider.handle_generation_error(exc)
