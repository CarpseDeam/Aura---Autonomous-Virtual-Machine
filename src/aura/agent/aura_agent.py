from __future__ import annotations

import logging
from typing import Any, Dict, List, TypedDict, Optional

from langgraph.graph import END, StateGraph

from src.aura.brain import AuraBrain
from src.aura.executor import AuraExecutor
from src.aura.models.action import Action, ActionType
from src.aura.models.events import Event
from src.aura.models.project_context import ProjectContext
from src.aura.context import ContextManager
from src.aura.agent import IterationController
from src.aura.models.context_models import ContextConfig, ContextMode
from src.aura.models.iteration_models import IterationConfig, IterationState


logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    """Mutable state that flows through the agent's LangGraph pipeline.

    This state supports a cyclical think-act-observe loop where the agent
    continuously plans, executes, and observes until the task is complete.
    """

    input: str  # Original user request
    messages: List[Dict[str, Any]]  # Full conversation history including tool outputs
    context: ProjectContext  # Project context for execution
    current_action: Action  # The action being executed in current iteration
    iteration_count: int  # Number of plan-execute cycles (safeguard against infinite loops)
    blueprint: Dict[str, Any]  # Special state for blueprint generation
    iteration_state: IterationState  # Smart iteration control state
    tool_output: Optional[str]  # Last tool output for reflection


class AuraAgent:
    """High-level agent that orchestrates planning and execution via LangGraph."""

    def __init__(
        self,
        brain: AuraBrain,
        executor: AuraExecutor,
        context_manager: Optional[ContextManager] = None,
        iteration_controller: Optional[IterationController] = None
    ) -> None:
        self.brain = brain
        self.executor = executor
        self.context_manager = context_manager
        self.iteration_controller = iteration_controller

        self._tool_name_by_action = {
            ActionType.SIMPLE_REPLY: "execute_simple_reply",
            ActionType.DESIGN_BLUEPRINT: "execute_design_blueprint",
            ActionType.REFINE_CODE: "execute_refine_code",
            ActionType.DISCUSS: "execute_discuss",
            ActionType.LIST_FILES: "execute_list_files",
            ActionType.READ_FILE: "execute_read_file",
            ActionType.WRITE_FILE: "execute_write_file",
            ActionType.RESEARCH: "execute_research",
        }

        # Build a cyclical think-act-observe graph
        graph = StateGraph(AgentState)
        graph.add_node("plan", self.plan)
        graph.add_node("execute_tool", self.execute_tool)

        # Start with planning
        graph.set_entry_point("plan")

        # After planning, execute the tool
        graph.add_edge("plan", "execute_tool")

        # After execution, decide whether to continue or end
        graph.add_conditional_edges(
            "execute_tool",
            self.should_continue,
            {
                "continue": "plan",  # Loop back to plan for next action
                "end": END,  # Task complete or max iterations reached
            },
        )
        self._graph_app = graph.compile()

    def invoke(self, request: str, context: ProjectContext) -> AgentState:
        """Kick off the cyclical think-act-observe loop with the user request."""
        # Initialize state with user input as the first message
        messages = list(context.conversation_history or [])
        latest_images = (context.extras or {}).get("latest_user_images")
        user_message = {"role": "user", "content": request}
        if latest_images:
            user_message["images"] = latest_images
        messages.append(user_message)

        # Detect mode for iteration controller
        mode = "iterate" if (context.active_project and context.active_files) else "bootstrap"

        # Initialize iteration state if controller is available
        iteration_state = None
        if self.iteration_controller:
            iteration_state = self.iteration_controller.initialize_state(request, mode)
            logger.info(f"Initialized iteration controller in {mode} mode")

        state: AgentState = {
            "input": request,
            "messages": messages,
            "context": context,
            "iteration_count": 0,
            "iteration_state": iteration_state,
        }

        final_state = self._graph_app.invoke(state)
        final_state.pop("context", None)
        final_state.pop("current_action", None)  # Don't expose internal action state
        final_state.pop("iteration_state", None)  # Don't expose internal iteration state
        return final_state

    def plan(self, state: AgentState) -> AgentState:
        """Think step: Analyze current state and decide next action.

        This node examines the conversation history (including tool outputs)
        and uses the brain to determine what action to take next.
        """
        input_request = state.get("input", "")
        context = state.get("context")
        messages = state.get("messages", [])

        if not input_request or context is None:
            logger.warning("Plan step missing input or context; cannot proceed.")
            state["current_action"] = None
            return state

        # Increment iteration counter for loop safeguard
        state["iteration_count"] = state.get("iteration_count", 0) + 1

        # Enrich context with smart context loading if available
        enriched_context = context
        if self.context_manager:
            try:
                # Determine mode based on project state
                mode = ContextMode.ITERATE if (context.active_project and context.active_files) else ContextMode.BOOTSTRAP

                # Load smart context
                context_window = self.context_manager.load_context(
                    input_request,
                    context,
                    mode
                )

                # Add context window info to context extras for brain to use
                enriched_context = ProjectContext(
                    active_project=context.active_project,
                    active_files=context.active_files,
                    conversation_history=context.conversation_history,
                    extras={
                        **(context.extras or {}),
                        "context_window": {
                            "loaded_files": [f.file_path for f in context_window.loaded_files],
                            "total_tokens": context_window.total_tokens,
                            "mode": context_window.mode.value,
                            "relevance_scores": {
                                f.file_path: f.relevance_score
                                for f in context_window.loaded_files
                            }
                        }
                    }
                )

                logger.info(
                    f"[ContextManager] Loaded {len(context_window.loaded_files)} files "
                    f"({context_window.total_tokens} tokens, {context_window.mode.value} mode)"
                )
            except Exception as exc:
                logger.warning(f"Context enrichment failed: {exc}", exc_info=True)
                # Fall back to original context
                enriched_context = context

        try:
            # The brain analyzes the full message history to decide next action
            next_action = self.brain.decide(input_request, enriched_context)
            logger.info(f"[Plan] Iteration {state['iteration_count']}: Next action = {next_action.type if next_action else None}")
        except Exception as exc:
            logger.error("Failed to generate action via brain: %s", exc, exc_info=True)
            # Create a fallback simple reply action on error
            next_action = Action(
                type=ActionType.SIMPLE_REPLY,
                params={"response": f"I encountered an error while planning: {str(exc)}"}
            )

        state["current_action"] = next_action
        return state

    def execute_tool(self, state: AgentState) -> AgentState:
        """Act step: Execute the planned action and observe the result.

        This node takes the current_action, executes it via the executor,
        and appends the observation to the message history.
        """
        context = state.get("context")
        action = state.get("current_action")

        if action is None:
            logger.warning("No action to execute; ending cycle.")
            return state

        if context is None:
            logger.warning("Execution step missing context; cannot execute.")
            return state

        logger.info(f"[Execute] Running action: {action.type}")

        try:
            result = self._invoke_executor_tool(action, context)
            # Capture tool output for iteration controller reflection
            state["tool_output"] = str(result)[:500] if result else None
        except Exception as exc:
            logger.error("Executor tool failed for action %s: %s", action.type, exc, exc_info=True)
            # Add error observation to messages
            error_msg = f"Tool execution failed for {action.type.value}: {str(exc)}"
            state["tool_output"] = error_msg
            state.setdefault("messages", []).append({
                "role": "system",
                "content": error_msg,
                "action_type": action.type.value,
            })
            try:
                self.executor.event_bus.dispatch(Event(
                    event_type="MODEL_ERROR",
                    payload={"message": error_msg},
                ))
            except Exception:
                logger.debug("Failed to dispatch MODEL_ERROR event.", exc_info=True)
            return state

        # Handle and observe the result
        try:
            self._handle_tool_result(action, result, state)
            logger.info(f"[Observe] Action {action.type} completed successfully")
        except Exception as exc:
            logger.error("Post-processing failed for action %s: %s", action.type, exc, exc_info=True)
            error_msg = f"Post-processing failed for {action.type.value}: {str(exc)}"
            state["tool_output"] = error_msg
            state.setdefault("messages", []).append({
                "role": "system",
                "content": error_msg,
                "action_type": action.type.value,
            })
            try:
                self.executor.event_bus.dispatch(Event(
                    event_type="MODEL_ERROR",
                    payload={"message": error_msg},
                ))
            except Exception:
                logger.debug("Failed to dispatch MODEL_ERROR event.", exc_info=True)

        return state

    def should_continue(self, state: AgentState) -> str:
        """Decide whether to loop back to planning or end the conversation.

        Returns:
            "continue": Loop back to plan node for next iteration
            "end": Complete the current turn and return to user
        """
        action = state.get("current_action")
        iteration_count = state.get("iteration_count", 0)
        iteration_state = state.get("iteration_state")
        tool_output = state.get("tool_output")

        # Use IterationController if available
        if self.iteration_controller and iteration_state:
            try:
                should_continue = self.iteration_controller.should_continue_iteration(
                    iteration_state,
                    action,
                    tool_output
                )

                if not should_continue:
                    # Log stopping reason
                    if iteration_state.stopping_condition:
                        logger.info(
                            f"[IterationController] Stopping: {iteration_state.stopping_condition.value}"
                        )

                        # Add system message about why we stopped
                        stop_messages = {
                            "task_complete": "Task completed successfully.",
                            "max_iterations": f"Maximum iteration limit ({iteration_state.max_iterations}) reached.",
                            "loop_detected": f"Loop detected: repeated action '{iteration_state.loop_state.loop_action_type}'.",
                            "no_progress": "No progress detected in recent iterations.",
                            "final_action": f"Final action {action.type.value if action else 'N/A'} completed."
                        }

                        stop_msg = stop_messages.get(
                            iteration_state.stopping_condition.value,
                            "Iteration stopped."
                        )

                        state.setdefault("messages", []).append({
                            "role": "system",
                            "content": stop_msg,
                        })

                    return "end"

                # Continue iterating
                logger.debug(
                    f"[IterationController] Continuing: iteration {iteration_state.current_iteration}/"
                    f"{iteration_state.max_iterations}"
                )
                return "continue"

            except Exception as exc:
                logger.error(f"IterationController error: {exc}", exc_info=True)
                # Fall through to default logic

        # Fall back to original logic if controller not available
        MAX_ITERATIONS = 10

        # Safeguard: Prevent infinite loops
        if iteration_count >= MAX_ITERATIONS:
            logger.warning(f"Maximum iterations ({MAX_ITERATIONS}) reached. Ending cycle.")
            state.setdefault("messages", []).append({
                "role": "system",
                "content": f"Maximum iteration limit ({MAX_ITERATIONS}) reached. The agent has been stopped to prevent infinite loops.",
            })
            return "end"

        # If no action was planned, end the cycle
        if action is None:
            logger.info("[Router] No current action, ending cycle.")
            return "end"

        # Final actions that complete the user's turn
        FINAL_ACTIONS = {
            ActionType.SIMPLE_REPLY,
            ActionType.RESEARCH,
            ActionType.DESIGN_BLUEPRINT,
            ActionType.DISCUSS,
        }

        if action.type in FINAL_ACTIONS:
            logger.info(f"[Router] Final action {action.type} completed, ending cycle.")
            return "end"

        # For tool actions (LIST_FILES, READ_FILE, WRITE_FILE, etc.), continue the loop
        logger.info(f"[Router] Tool action {action.type} completed, continuing to next iteration.")
        return "continue"

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

        if action.type == ActionType.DISCUSS:
            discuss_text = str(result or "")
            if not discuss_text:
                raise RuntimeError("Discuss tool returned empty response")

            message_payload = {
                "role": "assistant",
                "content": discuss_text,
                "action_type": ActionType.DISCUSS.value,
                "clarifying_questions": action.get_param("questions", []),
                "unclear_aspects": action.get_param("unclear_aspects", []),
                "original_action": action.get_param("original_action"),
                "original_confidence": action.get_param("original_confidence"),
            }
            state.setdefault("messages", []).append(message_payload)
            try:
                self.executor.event_bus.dispatch(Event(event_type="MODEL_CHUNK_RECEIVED", payload={"chunk": discuss_text}))
                self.executor.event_bus.dispatch(Event(event_type="MODEL_STREAM_ENDED", payload={}))
            except Exception:
                logger.debug("Failed to dispatch conversation events for discuss reply.", exc_info=True)
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

        # Default case: Tool actions (LIST_FILES, READ_FILE, WRITE_FILE, REFINE_CODE)
        # Add the tool result as an observation in the message history
        # This allows the agent to see what the tool returned in the next planning cycle
        observation = {
            "role": "tool",
            "action_type": action.type.value,
            "content": str(result),
            "result": result,  # Keep structured data for potential future use
        }
        state.setdefault("messages", []).append(observation)
        logger.debug(f"Added tool observation for {action.type} to message history")
