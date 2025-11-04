from __future__ import annotations

import logging
import os
import re
import shlex
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, TYPE_CHECKING

from src.aura.models.agent_task import AgentSpecification, TerminalSession
from src.aura.models.event_types import AGENT_OUTPUT
from src.aura.models.events import Event
from src.aura.services.agents_md_formatter import format_specification_for_codex

if TYPE_CHECKING:
    from src.aura.app.event_bus import EventBus
    from src.aura.services.llm_service import LLMService


logger = logging.getLogger(__name__)


class TerminalAgentService:
    """
    Spawn Claude Code inside a pseudo-terminal so Aura can supervise the session.

    Responsibilities:
    - Persist task specifications and AGENTS.md handoff documents.
    - Launch Claude Code with PTY control while keeping the native TUI visible.
    - Monitor PTY output to detect agent questions, answer them via the LLM, and log output.
    """

    SPEC_DIR_NAME = ".aura"
    _DEFAULT_LLM_AGENT = "architect_agent"
    _READ_TIMEOUT_SECONDS = 0.5

    def __init__(
        self,
        workspace_root: Path,
        llm_service: LLMService,
        event_bus: EventBus,
        *,
        agent_command_template: Optional[str] = None,
        question_agent_name: str = _DEFAULT_LLM_AGENT,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.spec_dir = self.workspace_root / self.SPEC_DIR_NAME
        self.spec_dir.mkdir(parents=True, exist_ok=True)

        self.llm_service = llm_service
        self.event_bus = event_bus
        self.question_agent_name = question_agent_name

        self.agent_command_template = agent_command_template or "C:\\Users\\carps\\AppData\\Roaming\\npm\\claude.cmd --dangerously-skip-permissions"
        self._expect = self._load_expect_module()
        self._question_patterns = self._compile_question_patterns()

        logger.info(
            "TerminalAgentService initialized with PTY support (workspace=%s, template=%s, llm_agent=%s)",
            self.workspace_root,
            self.agent_command_template,
            self.question_agent_name,
        )

    # ------------------------------------------------------------------ Public API

    def spawn_agent(
        self,
        spec: AgentSpecification,
        *,
        command_override: Optional[Sequence[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> TerminalSession:
        """
        Persist the specification, launch the PTY-backed agent, and begin monitoring output.
        """
        project_root = self._resolve_project_root(spec)
        spec_path = self._persist_specification(spec)
        agents_md_path = self._write_agents_md(project_root, spec)

        command = self._build_command(spec, command_override)

        session_env = os.environ.copy()
        if sys.platform.startswith("win"):
            # On Windows, prepend the virtual environment's Scripts path to PATH
            # so the spawned process can resolve our executables (claude, codex, etc.)
            venv_scripts_path = str(Path(sys.executable).parent)
            original_path = session_env.get("PATH", "")
            session_env["PATH"] = f"{venv_scripts_path}{os.pathsep}{original_path}"
            logger.info("Prepended venv Scripts path to agent's PATH: %s", venv_scripts_path)
        session_env.update(env or {})
        session_env["AURA_AGENT_SPEC_PATH"] = str(spec_path)
        session_env["AURA_AGENT_TASK_ID"] = spec.task_id
        if sys.platform.startswith("win"):
            session_env.setdefault("PYTHONUTF8", "1")
            session_env.setdefault("PYTHONIOENCODING", "utf-8")

        child = None
        log_path = project_root / self.SPEC_DIR_NAME / f"{spec.task_id}.output.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)

        try:
            child = self._spawn_with_pty(command, project_root, session_env)
            session = TerminalSession(
                task_id=spec.task_id,
                command=command,
                spec_path=str(spec_path),
                process_id=getattr(child, "pid", None),
                child=child,
                log_path=str(log_path),
            )
            # Send the initial prompt via stdin for all platforms (interactive mode)
            self._send_initial_prompt(session, agents_md_path)
            self._start_monitor_thread(session, log_path)
            return session
        except Exception as exc:
            logger.error(
                "Failed to spawn PTY session for task %s: %s",
                spec.task_id,
                exc,
                exc_info=True,
            )
            if child is not None:
                self._safe_close(child)
            raise

    # ------------------------------------------------------------------ PTY orchestration

    def _spawn_with_pty(self, command: Sequence[str], project_root: Path, env: Dict[str, str]):
        if not command:
            raise ValueError("Command must not be empty")

        logger.info("Spawning PTY session: %s", command)

        try:
            child = self._expect.spawn(
                command[0],
                command[1:],
                cwd=str(project_root),
                env=env,
                encoding="utf-8",
                timeout=self._READ_TIMEOUT_SECONDS,
            )
            return child
        except Exception as exc:
            raise RuntimeError(f"Failed to spawn PTY: {exc}") from exc

    def _send_initial_prompt(self, session: TerminalSession, agents_md_path: Path) -> None:
        """Send the initial AGENTS.md prompt to the agent."""
        time.sleep(0.5)

        try:
            prompt = agents_md_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to read AGENTS.md for task %s: %s", session.task_id, exc, exc_info=True)
            raise RuntimeError(f"Failed to read AGENTS.md for task {session.task_id}") from exc

        prompt = prompt.strip()
        if not prompt:
            logger.debug("AGENTS.md empty for task %s; skipping initial prompt injection", session.task_id)
            return

        child = session.child
        if child is None:
            logger.warning("Cannot send initial prompt; PTY child missing for task %s", session.task_id)
            return

        logger.info("Sending AGENTS.MD prompt to Claude Code for task %s", session.task_id)
        with session.write_lock:
            child.sendline(prompt)

    def _start_monitor_thread(self, session: TerminalSession, log_path: Path) -> None:
        thread = threading.Thread(
            target=self._monitor_pty_output,
            args=(session, log_path),
            name=f"pty-monitor-{session.task_id}",
            daemon=True,
        )
        session.monitor_thread = thread
        thread.start()
        logger.info("Started PTY monitor thread for task %s (log=%s)", session.task_id, log_path)

    def _monitor_pty_output(self, session: TerminalSession, log_path: Path) -> None:
        child = session.child
        if child is None:
            logger.warning("Monitor thread launched without PTY child for task %s", session.task_id)
            return

        try:
            with log_path.open("a", encoding="utf-8") as log_stream:
                while True:
                    try:
                        raw_line = child.readline()
                    except self._expect.TIMEOUT:
                        continue
                    except self._expect.EOF:
                        logger.info("PTY stream ended for task %s", session.task_id)
                        break
                    except Exception as exc:
                        logger.error("PTY read failed for task %s: %s", session.task_id, exc, exc_info=True)
                        break

                    if not raw_line:
                        logger.info("PTY readline returned empty for task %s", session.task_id)
                        break

                    line = raw_line.rstrip("\r\n")
                    if not line:
                        continue

                    log_stream.write(line + "\n")
                    log_stream.flush()

                    try:
                        self.event_bus.dispatch(Event(
                            event_type=AGENT_OUTPUT,
                            payload={
                                "task_id": session.task_id,
                                "text": line,
                                "timestamp": datetime.now().isoformat()
                            }
                        ))
                    except Exception as exc:
                        logger.debug("Failed to dispatch event for task %s: %s", session.task_id, exc)

                    question = self._detect_question(line)
                    if question:
                        self._handle_agent_question(session, question)

        except Exception as exc:
            logger.error("Monitor thread crashed for task %s: %s", session.task_id, exc, exc_info=True)
        finally:
            exit_code = getattr(child, "exitstatus", None)
            if exit_code is None:
                exit_code = getattr(child, "status", None)
            session.mark_exit(exit_code)
            self._safe_close(child)
            logger.info("PTY monitor stopped for task %s (exit_code=%s)", session.task_id, exit_code)

    def _handle_agent_question(self, session: TerminalSession, question: str) -> None:
        with session.answer_lock:
            if question in session.answered_questions:
                logger.debug("Skipping duplicate question for task %s: %s", session.task_id, question)
                return
            session.answered_questions.add(question)

        logger.info("Agent question detected for task %s: %s", session.task_id, question)

        try:
            answer = self._generate_answer(session, question)
        except Exception as exc:
            logger.error("LLM failed to answer question for task %s: %s", session.task_id, exc, exc_info=True)
            return

        if not answer:
            logger.warning("LLM returned empty answer for task %s; skipping response", session.task_id)
            return

        self._send_to_agent(session, answer)

    def _send_to_agent(self, session: TerminalSession, message: str) -> None:
        child = session.child
        if child is None:
            logger.warning("Cannot send response; PTY child missing for task %s", session.task_id)
            return

        payload = message.rstrip()
        with session.write_lock:
            try:
                child.sendline(payload)
            except Exception as exc:
                logger.error("Failed to send response to agent for task %s: %s", session.task_id, exc, exc_info=True)
                return

        logger.info("Sent LLM response to agent for task %s: %s", session.task_id, payload)

    def _generate_answer(self, session: TerminalSession, question: str) -> str:
        prompt = (
            "You are Aura, answering a question from your coding agent Claude Code.\n\n"
            f"Task ID: {session.task_id}\n"
            f"The agent asked: {question}\n\n"
            "Provide a clear, direct answer to keep the agent working. Be concise."
        )
        response = self.llm_service.run_for_agent(self.question_agent_name, prompt)
        return response.strip()

    # ------------------------------------------------------------------ Helpers

    def _resolve_claude_command(self) -> List[str]:
        """Resolve the executable used to launch Claude Code."""
        if not sys.platform.startswith("win"):
            return ["claude"]

        npm_prefix = Path(os.environ.get("APPDATA", "")) / "npm"
        claude_script = npm_prefix / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"

        if not claude_script.exists():
            raise RuntimeError(f"Claude Code script not found at: {claude_script}")

        logger.info("Resolved Claude Code script to node.exe %s", claude_script)
        return ["node.exe", str(claude_script)]

    def _build_command(
        self,
        spec: AgentSpecification,
        command_override: Optional[Sequence[str]],
        prompt: Optional[str] = None,
    ) -> List[str]:
        if command_override:
            tokens = list(command_override)
            logger.info("Using command override for task %s: %s", spec.task_id, tokens)
            return tokens

        tokens = self._resolve_claude_command()
        tokens.append("--dangerously-skip-permissions")
        logger.info("Built command for task %s: %s", spec.task_id, tokens)

        if sys.platform.startswith("win"):
            logger.info(
                "Using interactive mode for task %s (prompt will be sent via stdin)",
                spec.task_id,
            )

        return tokens

    def _render_template_command(self, spec: AgentSpecification) -> List[str]:
        template = (self.agent_command_template or "").strip()
        if not template:
            raise ValueError("agent_command_template resolved to empty command")

        args = {
            "spec_path": str(self.spec_dir / f"{spec.task_id}.md"),
            "task_id": spec.task_id,
            "prompt": spec.prompt,
            "project_name": (spec.project_name or "").strip(),
            "request": spec.request,
        }

        try:
            rendered = template.format(**args)
        except KeyError as exc:
            raise RuntimeError(f"Unknown placeholder in agent command template: {exc.args[0]}") from exc

        rendered = rendered.strip()
        if not rendered:
            raise ValueError("agent_command_template produced an empty command after formatting")

        return shlex.split(rendered, posix=not sys.platform.startswith("win"))

    def _ensure_claude_flags(self, tokens: Sequence[str]) -> List[str]:
        updated = list(tokens)
        if not updated:
            return updated

        executable = updated[0].lower()
        if "claude" in executable and "--dangerously-skip-permissions" not in {
            token.lower() for token in updated[1:]
        }:
            updated.append("--dangerously-skip-permissions")

        return updated

    def _detect_question(self, line: str) -> Optional[str]:
        text = line.strip()
        if not text:
            return None
        for pattern in self._question_patterns:
            if pattern.search(text):
                return text
        return None

    def _resolve_project_root(self, spec: AgentSpecification) -> Path:
        name = (spec.project_name or "").strip()
        if not name:
            return self.workspace_root

        project_root = (self.workspace_root / name).resolve()
        project_root.mkdir(parents=True, exist_ok=True)
        (project_root / self.SPEC_DIR_NAME).mkdir(parents=True, exist_ok=True)
        return project_root

    def _persist_specification(self, spec: AgentSpecification) -> Path:
        spec_path = self.spec_dir / f"{spec.task_id}.md"
        spec_path.write_text(spec.prompt, encoding="utf-8")
        logger.debug("Persisted agent specification for task %s to %s", spec.task_id, spec_path)
        return spec_path

    def _write_agents_md(self, project_root: Path, spec: AgentSpecification) -> Path:
        agents_md_path = project_root / "AGENTS.md"
        content = format_specification_for_codex(spec)
        try:
            agents_md_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to write AGENTS.md for task %s: %s", spec.task_id, exc, exc_info=True)
            raise RuntimeError(f"Failed to write AGENTS.md for task {spec.task_id}") from exc

        logger.info("Wrote AGENTS.md for task %s to %s", spec.task_id, agents_md_path)
        return agents_md_path

    def _safe_close(self, child) -> None:
        if child is None:
            return
        try:
            if child.isalive():
                child.close(force=True)
            else:
                child.close(force=False)
        except Exception:
            logger.debug("Failed to close PTY child cleanly", exc_info=True)

    def _compile_question_patterns(self):
        patterns = [
            r".*\?\s*$",
            r".*\bshould\s+i\b.*",
            r".*\bwould\s+you\b.*",
            r".*\bwhich\s+option\b.*",
            r".*\bchoose\s+between\b.*",
            r".*\bconfirm\b.*",
            r".*\bverify\b.*",
            r".*\bapprove\b.*",
            r".*\(y/n\).*",
            r".*\(yes/no\).*",
            r".*\[y/n\].*",
            r".*\[y/N\].*",
        ]
        return tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)

    def _load_expect_module(self):
        """Load the appropriate PTY module for this platform."""
        if sys.platform.startswith("win"):
            try:
                import pexpect
                from pexpect.popen_spawn import PopenSpawn

                # Create a wrapper that uses PopenSpawn (works on Windows)
                class WindowsPTY:
                    TIMEOUT = pexpect.TIMEOUT
                    EOF = pexpect.EOF

                    @staticmethod
                    def spawn(command, args, **kwargs):
                        # Use PopenSpawn which works reliably on Windows
                        full_cmd = [command] + list(args)
                        child = PopenSpawn(full_cmd, **kwargs)
                        return child

                logger.info("Loaded PTY backend: pexpect.PopenSpawn (Windows)")
                return WindowsPTY()
            except ImportError as exc:
                raise RuntimeError("pexpect required. Install via requirements.txt") from exc
        else:
            try:
                import pexpect
                logger.info("Loaded PTY backend: pexpect (Unix)")
                return pexpect
            except ImportError as exc:
                raise RuntimeError("pexpect required. Install via requirements.txt") from exc
