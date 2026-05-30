"""Web-ready service layer for the interactive CamBot planning agent."""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Any, Callable

from agent.json_repair import JsonRepairer
from agent.log_store import SessionLogStore
from agent.models import AgentResponse, AgentSession
from agent.reviewer import PlanReviewer
from chain.planner import Planner
from chain.prompt_builder import PromptBuilder
from chain.retriever import LocalJsonRetriever
from runtime.cambot_executor import CamBotExecutor
from schemas.script_schema import ScriptPlan


ExecutorFactory = Callable[..., Any]


class PlanAgentService:
    """Stateful API for CLI and future web interactions."""

    CLARIFICATION_PATTERNS = [
        re.compile(pattern, re.IGNORECASE)
        for pattern in [
            r"\bmore\s+(advanced|cinematic|professional|premium|better)\b",
            r"\bmake\s+it\s+(better|nicer|cooler)\b",
            r"\bimprove\b",
            r"\boptimi[sz]e\b",
            r"更高级",
            r"更好",
            r"优化",
            r"高级一点",
            r"好看一点",
            r"酷一点",
        ]
    ]

    def __init__(
        self,
        config: dict[str, Any],
        repo_root: str | Path,
        log_root: str | Path | None = None,
        executor_factory: ExecutorFactory | None = None,
    ):
        self.config = config
        self.repo_root = Path(repo_root)
        self.executor_factory = executor_factory
        self.retriever = LocalJsonRetriever(self.repo_root / "rag")
        self.prompt_builder = PromptBuilder()
        self.planner = Planner(config)
        self.repairer = JsonRepairer(config)
        self.reviewer = PlanReviewer()
        self.log_store = SessionLogStore(log_root or self.repo_root / "logs" / "sessions")
        self.sessions: dict[str, AgentSession] = {}
        self.logger = logging.getLogger(self.__class__.__name__)

    def create_session(self, initial_instruction: str) -> AgentResponse:
        """Create a session, generate the first JSON plan, and return its review."""
        instruction = initial_instruction.strip()
        if not instruction:
            raise ValueError("Initial filming instruction is required.")

        session = AgentSession(
            session_id=self._new_session_id(),
            initial_instruction=instruction,
        )
        session.add_message("user", instruction, kind="initial_instruction")

        prompt = self.prompt_builder.build(
            user_instruction=instruction,
            retrieved_context=self._retrieve(instruction),
        )
        raw_plan = self.planner.plan(prompt)
        plan = self.repairer.repair_and_validate(raw_plan)
        self._update_plan(session, plan)
        session.add_message("assistant", session.current_review, kind="review")

        self.sessions[session.session_id] = session
        self.log_store.save(session)
        return self._response(session, "draft", "Generated the first filming plan.", include_plan=True)

    def send_message(self, session_id: str, user_message: str) -> AgentResponse:
        """Apply user feedback to the current plan, or ask for clarification."""
        session = self._get_session(session_id)
        message = user_message.strip()
        if not message:
            raise ValueError("User message is required.")

        session.add_message("user", message, kind="feedback")
        if session.confirmed:
            session.confirmed = False
            session.add_message("system", "User resumed editing; confirmation was cleared.", kind="unconfirm")

        clarification = self._clarification_question(message)
        if clarification:
            session.add_message("assistant", clarification, kind="clarification")
            self.log_store.save(session)
            return self._response(session, "needs_clarification", clarification)

        if session.current_plan is None:
            return self.create_session(message)

        prompt = self.prompt_builder.build_revision(
            current_plan=session.current_plan.model_dump(),
            user_feedback=message,
            retrieved_context=self._retrieve(message),
        )
        raw_plan = self.planner.plan(prompt)
        revised_plan = self.repairer.repair_and_validate(
            raw_text=raw_plan,
            previous_plan=session.current_plan,
        )
        self._update_plan(session, revised_plan)
        session.add_message("assistant", session.current_review, kind="review")
        self.log_store.save(session)
        return self._response(session, "draft", "Updated the filming plan from your feedback.", include_plan=True)

    def review_plan(self, session_id: str) -> AgentResponse:
        """Return the current natural-language review."""
        session = self._get_session(session_id)
        session.add_message("system", "User reviewed the current filming plan.", kind="review_request")
        self.log_store.save(session)
        status = "confirmed" if session.confirmed else "draft"
        return self._response(session, status, "Current filming plan.")

    def confirm_plan(self, session_id: str) -> AgentResponse:
        """Confirm, persist, and execute the current plan."""
        return self.confirm_and_execute_plan(session_id)

    def confirm_and_execute_plan(self, session_id: str) -> AgentResponse:
        """Confirm and dispatch the current plan through the existing executor."""
        session = self._confirm_session(session_id)
        executor = self._build_executor()
        executor.execute(session.current_plan)
        session.add_message("system", "Confirmed plan executed by CamBot executor.", kind="execute")
        self.log_store.save(session)
        return self._response(session, "executed", "Plan confirmed and executed.", include_plan=True)

    def confirm_plan_only(self, session_id: str) -> AgentResponse:
        """Confirm and persist the current plan without execution."""
        session = self._confirm_session(session_id)
        return self._response(session, "confirmed", "Plan confirmed and saved.", include_plan=True)

    def unconfirm_plan(self, session_id: str) -> AgentResponse:
        """Cancel confirmation so the user can continue editing."""
        session = self._get_session(session_id)
        session.confirmed = False
        session.touch()
        session.add_message("system", "User canceled confirmation and can continue editing.", kind="unconfirm")
        self.log_store.save(session)
        return self._response(session, "draft", "Confirmation canceled; editing can continue.", include_plan=True)

    def get_current_plan(self, session_id: str) -> AgentResponse:
        """Return the current structured JSON plan and review."""
        session = self._get_session(session_id)
        status = "confirmed" if session.confirmed else "draft"
        return self._response(session, status, "Current JSON filming plan.", include_plan=True)

    def execute_confirmed_plan(self, session_id: str) -> ScriptPlan:
        """Return a confirmed plan for lower-level integrations."""
        session = self._get_session(session_id)
        if not session.confirmed:
            raise ValueError("Plan must be confirmed before execution.")
        if session.current_plan is None:
            raise ValueError("No plan is available for execution.")
        return session.current_plan

    def _confirm_session(self, session_id: str) -> AgentSession:
        session = self._get_session(session_id)
        if session.current_plan is None:
            raise ValueError("No plan is available to confirm.")
        session.confirmed = True
        session.touch()
        session.add_message("system", "User confirmed the current filming plan.", kind="confirm")
        self.log_store.save(session)
        return session

    def _retrieve(self, query: str) -> dict[str, list[str]]:
        return self.retriever.retrieve(
            query=query,
            top_k=int(self.config["planner"].get("top_k", 2)),
        )

    def _update_plan(self, session: AgentSession, plan: ScriptPlan) -> None:
        session.current_plan = plan
        session.current_review = self.reviewer.render(plan)
        session.touch()

    def _build_executor(self) -> Any:
        if self.executor_factory is not None:
            return self.executor_factory(config=self.config, repo_root=str(self.repo_root))
        return CamBotExecutor(config=self.config, repo_root=str(self.repo_root))

    def _response(
        self,
        session: AgentSession,
        status: str,
        message: str,
        include_plan: bool = False,
    ) -> AgentResponse:
        plan_payload = None
        if include_plan and session.current_plan is not None:
            plan_payload = session.current_plan.model_dump()
        return AgentResponse(
            session_id=session.session_id,
            status=status,
            message=message,
            review=session.current_review,
            plan=plan_payload,
            confirmed=session.confirmed,
        )

    def _clarification_question(self, message: str) -> str | None:
        if any(pattern.search(message) for pattern in self.CLARIFICATION_PATTERNS):
            return (
                "This revision is broad. Which part should change most: camera movement, "
                "framing, distance, height, duration, or safety constraints?"
            )
        return None

    def _get_session(self, session_id: str) -> AgentSession:
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"Unknown session_id: {session_id}") from exc

    @staticmethod
    def _new_session_id() -> str:
        return uuid.uuid4().hex
