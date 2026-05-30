"""Filesystem logging for interactive agent sessions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.models import AgentSession
from utils.io import ensure_dir


class SessionLogStore:
    """Persist session plan, review, metadata, and conversation logs."""

    def __init__(self, root_dir: str | Path):
        self.root_dir = ensure_dir(root_dir)

    def save(self, session: AgentSession) -> Path:
        """Write the current session snapshot and return its directory."""
        session_dir = ensure_dir(self.root_dir / session.session_id)

        metadata = {
            "session_id": session.session_id,
            "initial_instruction": session.initial_instruction,
            "confirmed": session.confirmed,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }
        self._write_json(session_dir / "metadata.json", metadata)

        plan_payload: dict[str, Any] = {}
        if session.current_plan is not None:
            plan_payload = session.current_plan.model_dump()
        self._write_json(session_dir / "plan.json", plan_payload)

        (session_dir / "review.md").write_text(session.current_review, encoding="utf-8")

        with (session_dir / "conversation.jsonl").open("w", encoding="utf-8") as handle:
            for message in session.conversation:
                handle.write(message.model_dump_json() + "\n")

        return session_dir

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
