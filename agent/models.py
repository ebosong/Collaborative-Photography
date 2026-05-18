"""Session models for the interactive CamBot planning agent."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from schemas.script_schema import ScriptPlan


ConversationRole = Literal["user", "assistant", "system"]


class ConversationMessage(BaseModel):
    """One persisted message in an agent planning session."""

    role: ConversationRole
    content: str
    kind: str = "message"
    created_at: str = Field(default_factory=lambda: _now_iso())


class AgentResponse(BaseModel):
    """Web-facing response returned by agent service calls."""

    session_id: str
    status: Literal["draft", "needs_clarification", "confirmed"]
    message: str
    review: str | None = None
    plan: dict[str, Any] | None = None
    confirmed: bool = False


class AgentSession(BaseModel):
    """Mutable state for one interactive filming-plan session."""

    session_id: str
    initial_instruction: str
    current_plan: ScriptPlan | None = None
    current_review: str = ""
    conversation: list[ConversationMessage] = Field(default_factory=list)
    confirmed: bool = False
    created_at: str = Field(default_factory=lambda: _now_iso())
    updated_at: str = Field(default_factory=lambda: _now_iso())

    def add_message(self, role: ConversationRole, content: str, kind: str = "message") -> None:
        """Append one message and refresh the update timestamp."""
        self.conversation.append(ConversationMessage(role=role, content=content, kind=kind))
        self.touch()

    def touch(self) -> None:
        """Refresh the update timestamp."""
        self.updated_at = _now_iso()


def _now_iso() -> str:
    """Return a UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()
