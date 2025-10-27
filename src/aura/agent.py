from __future__ import annotations

import logging
from typing import Dict, List, TypedDict

from langgraph.graph import END, StateGraph

from src.aura.brain import AuraBrain
from src.aura.executor import AuraExecutor
from src.aura.models.action import Action
from src.aura.models.project_context import ProjectContext


logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    """Mutable state that flows through the agent's LangGraph pipeline."""

    request: str
    plan: List[Action]
    messages: List[Dict[str, str]]
    context: ProjectContext


class AuraAgent:
    """High-level agent that orchestrates planning and execution via LangGraph."""

    def __init__(self, brain: AuraBrain, executor: AuraExecutor) -> None:
        self.brain = brain
        self.executor = executor

        graph = StateGraph(AgentState)
        graph.add_node("plan", self.plan)
        graph.add_node("act", self.execute_step)
        graph.set_entry_point("plan")
        graph.add_edge("plan", "act")
        graph.add_conditional_edges(
            "act",
            self.should_continue,
            {True: "act", False: END},
        )
        self._graph_app = graph.compile()

    def invoke(self, request: str, context: ProjectContext) -> AgentState:
        """Kick off the graph with the latest user request and project context."""
        state: AgentState = {
            "request": request,
            "plan": [],
            "messages": list(context.conversation_history or []),
            "context": context,
        }
        final_state = self._graph_app.invoke(state)
        final_state.pop("context", None)
        return final_state

    def plan(self, state: AgentState) -> AgentState:
        """Call into the brain to craft a plan of Actions."""
        request = state.get("request", "")
        context = state.get("context")

        if not request or context is None:
            logger.warning("Plan step missing request or context; returning empty plan.")
            state["plan"] = []
            return state

        try:
            plan = self.brain.decide(request, context)
        except Exception as exc:
            logger.error("Failed to generate plan via brain: %s", exc, exc_info=True)
            state["plan"] = []
            return state

        state["plan"] = plan or []
        return state

    def execute_step(self, state: AgentState) -> AgentState:
        """Execute the next action in the plan via the executor."""
        context = state.get("context")
        plan = state.get("plan") or []

        if not plan:
            logger.debug("No further actions to execute.")
            return state

        if context is None:
            logger.warning("Execution step missing context; skipping remaining actions.")
            state["plan"] = []
            return state

        action = plan.pop(0)
        try:
            self.executor.execute(action, context)
        except Exception as exc:
            logger.error("Executor failed while handling action %s: %s", action, exc, exc_info=True)
            state["plan"] = []
        finally:
            state["plan"] = plan
        return state

    def should_continue(self, state: AgentState) -> bool:
        """Decide whether the graph should execute another action step."""
        remaining = state.get("plan") or []
        return bool(remaining)
