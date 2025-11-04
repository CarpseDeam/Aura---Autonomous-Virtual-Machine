from __future__ import annotations

import importlib
import logging
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TYPE_CHECKING, Tuple

from src.aura.models.agent_task import AgentSpecification, TerminalSession
from src.aura.services.agents_md_formatter import format_specification_for_codex

if TYPE_CHECKING:
    from src.aura.services.llm_service import LLMService


logger = logging.getLogger(__name__)


class _StdoutRelay:
    """Mirror PTY output to the controlling terminal so the TUI remains visible."""

    def __init__(self) -> None:
        self._stream = getattr(sys, "stdout", None)

    def write(self, data: str) -> int:  # pragma: no cover - thin wrapper
        if not data or self._stream is None:
            return 0
        self._stream.write(data)
        self._stream.flush()
        return len(data)

    def flush(self) -> None:  # pragma: no cover - thin wrapper
        if self._stream is not None:
            self._stream.flush()


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
        *,
        default_command: Optional[Sequence[str]] = None,
        agent_command_template: Optional[str] = None,
        question_agent_name: str = _DEFAULT_LLM_AGENT,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.spec_dir = self.workspace_root / self.SPEC_DIR_NAME
        self.spec_dir.mkdir(parents=True, exist_ok=True)

        self.llm_service = llm_service
        self.question_agent_name = question_agent_name

        self.default_command = list(default_command) if default_command else None
        self.agent_command_template = agent_command_template or "claude-code --dangerously-skip-permissions"
        self._expect = self._load_expect_module()
        self._stdout_relay = _StdoutRelay()
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
        session_env.update(env or {})
        session_env["AURA_AGENT_SPEC_PATH"] = str(spec_path)
        session_env["AURA_AGENT_TASK_ID"] = spec.task_id

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

    def _spawn_with_pty(
        self,
        command: Sequence[str],
        project_root: Path,
        env: Dict[str, str],
    ):
        if not command:
            raise ValueError("PTY command must not be empty")

        spawn_command, spawn_kwargs = self._prepare_spawn_command(command)

        executable, *args = spawn_command
        if spawn_command != list(command):
            logger.info(
                "Spawning PTY session via PowerShell wrapper (original=%s, effective=%s)",
                command,
                spawn_command,
            )
        else:
            logger.info("Spawning PTY session: %s", spawn_command)

        try:
            child = self._expect.spawn(
                executable,
                args,
                cwd=str(project_root),
                env=env,
                encoding="utf-8",
                codec_errors="replace",
                timeout=self._READ_TIMEOUT_SECONDS,
                **spawn_kwargs,
            )
        except Exception as exc:  # pragma: no cover - expect library surfaces platform-specific exceptions
            raise RuntimeError(
                f"Failed to spawn PTY for command {spawn_command} (original={command}): {exc}"
            ) from exc

        child.delaybeforesend = 0.05
        child.logfile_read = self._stdout_relay
        return child

    def _prepare_spawn_command(self, command: Sequence[str]) -> Tuple[List[str], Dict[str, Any]]:
        spawn_command = list(command)
        spawn_kwargs: Dict[str, Any] = {}

        if sys.platform.startswith("win") and getattr(self._expect, "__name__", "") == "wexpect":
            command_line = subprocess.list2cmdline(spawn_command)
            powershell_command = [
                "powershell.exe",
                "-NoExit",
                "-Command",
                f"& {command_line}",
            ]
            spawn_command = powershell_command
            spawn_kwargs["interact"] = True
            logger.debug(
                "Wrapped Windows command for separate PowerShell window: %s -> %s",
                command,
                spawn_command,
            )

        return spawn_command, spawn_kwargs

    def _send_initial_prompt(self, session: TerminalSession, agents_md_path: Path) -> None:
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

        logger.info("Sending AGENTS.md prompt to Claude Code for task %s", session.task_id)
        # Allow the Claude Code TUI to finish bootstrapping before injecting the initial prompt.
        time.sleep(0.5)
        with session.write_lock:
            child.send(prompt + "\n\n")

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
                        logger.info("PTY stream closed for task %s", session.task_id)
                        break
                    except Exception as exc:  # pragma: no cover - defensive fallback
                        logger.error("PTY read failed for task %s: %s", session.task_id, exc, exc_info=True)
                        break

                    if not raw_line:
                        continue

                    line = raw_line.rstrip("\r\n")
                    if not line:
                        continue

                    log_stream.write(line + "\n")
                    log_stream.flush()

                    question = self._detect_question(line)
                    if question:
                        self._handle_agent_question(session, question)
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

    def _build_command(
        self,
        spec: AgentSpecification,
        command_override: Optional[Sequence[str]],
    ) -> List[str]:
        if command_override:
            tokens = list(command_override)
            logger.info("Using command override for task %s: %s", spec.task_id, tokens)
        elif self.default_command:
            tokens = list(self.default_command)
            logger.info("Using default command for task %s: %s", spec.task_id, tokens)
        else:
            tokens = self._render_template_command(spec)
            logger.info("Rendered command template for task %s: %s", spec.task_id, tokens)

        return self._ensure_claude_flags(tokens)

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
        module_name = "wexpect" if sys.platform.startswith("win") else "pexpect"
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise RuntimeError(
                f"{module_name} is required for PTY terminal control. Install it via requirements.txt."
            ) from exc
        logger.info("Loaded PTY backend module: %s", module_name)
        return module
