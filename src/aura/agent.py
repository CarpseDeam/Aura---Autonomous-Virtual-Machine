from __future__ import annotations

import logging
from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, StateGraph

from src.aura.brain import AuraBrain
from src.aura.executor import AuraExecutor
from src.aura.models.action import Action, ActionType
from src.aura.models.events import Event
from src.aura.models.project_context import ProjectContext


logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    """Mutable state that flows through the agent's LangGraph pipeline."""

    request: str
    plan: List[Action]
    messages: List[Dict[str, str]]
    context: ProjectContext
    blueprint: Dict[str, Any]
    results: List[Any]


class AuraAgent:
    """High-level agent that orchestrates planning and execution via LangGraph."""

    def __init__(self, brain: AuraBrain, executor: AuraExecutor) -> None:
        self.brain = brain
        self.executor = executor
        self._tool_name_by_action = {
            ActionType.SIMPLE_REPLY: "execute_simple_reply",
            ActionType.DESIGN_BLUEPRINT: "execute_design_blueprint",
            ActionType.REFINE_CODE: "execute_refine_code",
            ActionType.LIST_FILES: "execute_list_files",
            ActionType.READ_FILE: "execute_read_file",
            ActionType.WRITE_FILE: "execute_write_file",
            ActionType.RESEARCH: "execute_research",
        }

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
            next_action = self.brain.decide(request, context)
        except Exception as exc:
            logger.error("Failed to generate plan via brain: %s", exc, exc_info=True)
            state["plan"] = []
            return state

        state["plan"] = [next_action] if next_action else []
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
            result = self._invoke_executor_tool(action, context)
        except Exception as exc:
            logger.error("Executor tool failed for action %s: %s", action.type, exc, exc_info=True)
            try:
                self.executor.event_bus.dispatch(Event(
                    event_type="MODEL_ERROR",
                    payload={"message": f"Failed while running tool for action '{action.type.value}'."},
                ))
            except Exception:
                logger.debug("Failed to dispatch MODEL_ERROR event after tool failure.", exc_info=True)
            state["plan"] = []
            return state

        try:
            self._handle_tool_result(action, result, state)
        except Exception as exc:
            logger.error("Post-processing failed for action %s: %s", action.type, exc, exc_info=True)
            try:
                self.executor.event_bus.dispatch(Event(
                    event_type="MODEL_ERROR",
                    payload={"message": f"Post-processing failed for action '{action.type.value}'."},
                ))
            except Exception:
                logger.debug("Failed to dispatch MODEL_ERROR event after post-processing failure.", exc_info=True)
            state["plan"] = []
            return state

        state["plan"] = plan
        return state

    def should_continue(self, state: AgentState) -> bool:
        """Decide whether the graph should execute another action step."""
        remaining = state.get("plan") or []
        return bool(remaining)

    def _invoke_executor_tool(self, action: Action, context: ProjectContext) -> Any:
        tool_name = self._tool_name_by_action.get(action.type)
        if not tool_name:
            raise ValueError(f"No executor tool configured for action type {action.type}")

        tool = getattr(self.executor, tool_name, None)
        if not callable(tool):
            raise AttributeError(f"Executor tool '{tool_name}' is not callable")

        return tool(action, context)

    def _handle_tool_result(self, action: Action, result: Any, state: AgentState) -> None:
        if action.type == ActionType.SIMPLE_REPLY:
            reply_text = str(result or "")
            if not reply_text:
                raise RuntimeError("Simple reply tool returned empty response")
            state.setdefault("messages", []).append({"role": "assistant", "content": reply_text})
            try:
                self.executor.event_bus.dispatch(Event(event_type="MODEL_CHUNK_RECEIVED", payload={"chunk": reply_text}))
                self.executor.event_bus.dispatch(Event(event_type="MODEL_STREAM_ENDED", payload={}))
            except Exception:
                logger.debug("Failed to dispatch conversation events for simple reply.", exc_info=True)
            return

        if action.type == ActionType.DESIGN_BLUEPRINT:
            blueprint = result if isinstance(result, dict) else {}
            if not blueprint:
                raise RuntimeError("Design blueprint tool returned no data")
            state["blueprint"] = blueprint
            try:
                self.executor.event_bus.dispatch(Event(event_type="BLUEPRINT_GENERATED", payload=blueprint))
            except Exception:
                logger.debug("Failed to dispatch BLUEPRINT_GENERATED event.", exc_info=True)

            files = self.executor._files_from_blueprint(blueprint)
            user_request = action.get_param("request", "")
            for spec in files:
                try:
                    file_result = self.executor.execute_generate_code_for_spec(spec, user_request)
                    if file_result:
                        state.setdefault("results", []).append(file_result)
                except Exception as exc:
                    logger.error("Failed to generate code for spec %s: %s", spec.get("file_path"), exc, exc_info=True)
            try:
                self.executor.event_bus.dispatch(Event(event_type="BUILD_COMPLETED", payload={}))
            except Exception:
                logger.debug("Failed to dispatch BUILD_COMPLETED event.", exc_info=True)
            return

        if action.type == ActionType.RESEARCH:
            summary = result.get("summary", "I couldn't find anything on that topic.")
            sources = result.get("sources", [])

            # Format the response with sources for clarity
            response_text = summary
            if sources:
                source_links = "\n".join(f"- [{s.get('title')}]({s.get('url')})" for s in sources)
                response_text += f"\n\n**Sources:**\n{source_links}"

            state.setdefault("messages", []).append({"role": "assistant", "content": response_text})
            try:
                self.executor.event_bus.dispatch(Event(event_type="MODEL_CHUNK_RECEIVED", payload={"chunk": response_text}))
                self.executor.event_bus.dispatch(Event(event_type="MODEL_STREAM_ENDED", payload={}))
            except Exception:
                logger.debug("Failed to dispatch conversation events for research result.", exc_info=True)
            return

        # Default: stash result for downstream consumers
        state.setdefault("results", []).append(result)
