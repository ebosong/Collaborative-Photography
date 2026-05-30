"""Minimal local RAG retriever backed by JSON knowledge files."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from utils.io import load_json


class LocalJsonRetriever:
    """Keyword-based retriever that can later be replaced by vector search."""

    def __init__(self, rag_dir: str | Path):
        self.rag_dir = Path(rag_dir)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.collections = {
            "shot_templates": load_json(self.rag_dir / "shot_templates.json"),
            "skill_rules": load_json(self.rag_dir / "skill_rules.json"),
            "safety_rules": load_json(self.rag_dir / "safety_rules.json"),
        }

    def retrieve(self, query: str, top_k: int = 2) -> dict[str, list[str]]:
        """Return the top matching entries from each knowledge collection."""
        return {
            name: self._retrieve_collection(entries, query, top_k)
            for name, entries in self.collections.items()
        }

    def _retrieve_collection(
        self,
        entries: list[dict[str, Any]],
        query: str,
        top_k: int,
    ) -> list[str]:
        scored = []
        query_terms = self._tokenize(query)
        for entry in entries:
            haystack = " ".join(str(value) for value in entry.values())
            score = self._score_text(query_terms, haystack)
            scored.append((score, self._format_entry(entry)))

        scored.sort(key=lambda item: item[0], reverse=True)
        selected = [text for score, text in scored[:top_k] if score > 0]
        if not selected:
            selected = [text for _, text in scored[:1]]
        return selected

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"[a-zA-Z_]+", text.lower())

    @staticmethod
    def _score_text(query_terms: list[str], haystack: str) -> int:
        lowered = haystack.lower()
        return sum(lowered.count(term) for term in query_terms)

    @staticmethod
    def _format_entry(entry: dict[str, Any]) -> str:
        return str(entry)
