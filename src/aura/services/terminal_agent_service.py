from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from src.aura.models.agent_task import AgentSpecification, TerminalSession
from src.aura.services.agents_md_formatter import format_specification_for_codex

logger = logging.getLogger(__name__)


class TerminalAgentService:
    """
    Launches external coding agents in dedicated terminal sessions.

    Responsibilities:
    - Persist agent specifications to the workspace handoff directory.
    - Spawn terminal windows running the configured tooling command.
    - Track spawned session metadata for later monitoring.
    """

    SPEC_DIR_NAME = ".aura"
    STREAMING_EXECUTABLES = {
        "codex",
        "codex.exe",
        "claude",
        "claude-code",
        "claude.exe",
        "gemini",
        "gemini-cli",
    }

    def __init__(
        self,
        workspace_root: Path,
        default_command: Optional[Sequence[str]] = None,
        agent_command_template: Optional[str] = None,
        terminal_shell_preference: str = "auto",
    ) -> None:
        logger.info(
            "TerminalAgentService received terminal_shell_preference=%s",
            terminal_shell_preference,
        )
        self.workspace_root = Path(workspace_root)
        self.default_command = list(default_command) if default_command else None
        self.agent_command_template = agent_command_template or "cat {spec_path}"
        self.spec_dir = self.workspace_root / self.SPEC_DIR_NAME
        self.spec_dir.mkdir(parents=True, exist_ok=True)
        preference = (terminal_shell_preference or "auto").strip().lower()
        self.terminal_shell_preference = "powershell" if preference == "powershell" else "auto"
        self._powershell_executable: Optional[str] = None
        self._windows_terminal_path: Optional[str] = None
        self._windows_terminal_ignored_logged = False

        if sys.platform.startswith("win"):
            self._powershell_executable = self._resolve_powershell_executable()
            self._windows_terminal_path = self._detect_windows_terminal()
            if (
                self.terminal_shell_preference == "powershell"
                and self._windows_terminal_path
            ):
                logger.info(
                    "Windows Terminal detected at %s but PowerShell preference is active; will ignore Windows Terminal wrapping",
                    self._windows_terminal_path,
                )
        logger.info(
            "TerminalAgentService ready (spec dir: %s, template: %s)",
            self.spec_dir,
            self.agent_command_template,
        )
        logger.debug(
            "Terminal shell preference set to: %s (powershell_executable=%s, windows_terminal=%s)",
            self.terminal_shell_preference,
            self._powershell_executable,
            self._windows_terminal_path or "not-found",
        )

    def _resolve_powershell_executable(self) -> str:
        """Detect the preferred PowerShell executable on Windows systems."""
        candidates = [
            "pwsh.exe",
            "pwsh",
            "powershell.exe",
            "powershell",
        ]
        for candidate in candidates:
            resolved = shutil.which(candidate)
            if resolved:
                logger.info("Detected PowerShell executable: %s", resolved)
                return resolved
        raise RuntimeError(
            "PowerShell executable not found in PATH; cannot honor terminal_shell_preference='powershell'."
        )

    def _detect_windows_terminal(self) -> Optional[str]:
        """Detect Windows Terminal (wt.exe) for diagnostic logging."""
        if not sys.platform.startswith("win"):
            return None
        for candidate in ("wt.exe", "wt"):
            resolved = shutil.which(candidate)
            if resolved:
                logger.debug("Detected Windows Terminal executable: %s", resolved)
                return resolved
        return None

    def _build_terminal_command(self, spec_path: Path, project_root: Path, spec: AgentSpecification) -> List[str]:
        """
        Build a platform-specific command that opens a visible terminal and runs the agent.

        Args:
            spec_path: Path to the persisted specification file
            project_root: Path to the project root directory
            spec: Full agent specification used to hydrate command templates

        Returns:
            Command list ready for subprocess.Popen
        """
        def _coerce(value: Optional[str]) -> str:
            return value or ""

        template_args = {
            "spec_path": str(spec_path),
            "task_id": spec.task_id,
            "prompt": _coerce(spec.prompt),
            "project_name": _coerce(spec.project_name),
            "request": _coerce(spec.request),
        }

        try:
            agent_command_template = self.agent_command_template.format(**template_args).strip()
        except KeyError as exc:
            raise RuntimeError(
                f"Agent command template references unknown placeholder '{exc.args[0]}'"
            ) from exc

        # Format the agent command with the spec path
        is_windows = sys.platform.startswith("win")
        first_token = agent_command_template.split(maxsplit=1)[0].lower() if agent_command_template else ""
        require_stdin = first_token in self.STREAMING_EXECUTABLES
        agent_tokens = self._apply_autonomy_flags(agent_command_template, require_stdin=require_stdin)
        agent_command = " ".join(agent_tokens) if agent_tokens else agent_command_template

        if is_windows:
            prefer_powershell = self.terminal_shell_preference == "powershell"
            logger.info(
                "Terminal preference check: self.terminal_shell_preference=%s, prefer_powershell=%s",
                self.terminal_shell_preference,
                prefer_powershell,
            )

            # Codex: Use special Windows command with auto-approval bypass
            if agent_tokens and agent_tokens[0].lower() in {"codex", "codex.exe"}:
                windows_command = self._build_windows_codex_command(
                    agent_tokens,
                    project_root,
                    task_id=spec.task_id,
                )
                logger.debug("Constructed Windows Codex command: %s", windows_command)
                return windows_command

            # Claude Code: Launch interactive session with AGENTS.md context
            if agent_tokens and agent_tokens[0].lower() in {"claude", "claude-code", "claude.exe"}:
                windows_command = self._build_windows_claude_command(
                    tokens=agent_tokens,
                    project_root=project_root,
                    task_id=spec.task_id,
                )
                logger.debug("Constructed Windows streaming command for Claude Code: %s", windows_command)
                return windows_command

            # Gemini CLI: Launch in headless mode with -p and AGENTS.md content
            if agent_tokens and agent_tokens[0].lower() in {"gemini", "gemini-cli"}:
                windows_command = self._build_windows_gemini_command(
                    tokens=agent_tokens,
                    project_root=project_root,
                    task_id=spec.task_id,
                )
                logger.debug("Constructed Windows streaming command for Gemini CLI: %s", windows_command)
                return windows_command

            # Default: Use simple PowerShell passthrough for unknown agents
            logger.debug("Unknown agent type on Windows, using PowerShell passthrough")
            delayed_command = f"Start-Sleep -Seconds 2; {agent_command}"
            return self._build_windows_powershell_command(delayed_command)
        else:
            # Unix: Run agents while allowing non-Claude commands a short startup delay
            first_token = agent_tokens[0].lower() if agent_tokens else ""
            if first_token in {"claude", "claude-code"}:
                launch_cmd = agent_command
            else:
                # Delay other agents briefly to avoid race conditions with terminal startup
                launch_cmd = f"sleep 2 && {agent_command}"

            # Unix: Try to find an available terminal emulator
            terminal_emulators = [
                ("gnome-terminal", ["--", "bash", "-c", f"{launch_cmd}; exec bash"]),
                ("konsole", ["-e", "bash", "-c", f"{launch_cmd}; exec bash"]),
                ("xterm", ["-hold", "-e", "bash", "-c", launch_cmd]),
            ]

            # Try each terminal emulator until we find one that exists
            import shutil
            for emulator, args in terminal_emulators:
                if shutil.which(emulator):
                    logger.debug("Using terminal emulator: %s", emulator)
                    return [emulator] + args

            # Fallback: just run bash directly (won't be visible on Unix without terminal)
            logger.warning("No terminal emulator found, falling back to direct bash execution")
            return ["bash", "-c", launch_cmd]


    def _build_windows_claude_command(
        self,
        tokens: Sequence[str],
        project_root: Path,
        *,
        task_id: Optional[str] = None,
    ) -> List[str]:
        """Launch Claude Code with AGENTS.md streamed over stdin."""
        logger.info("Building Claude command with forced PowerShell shell")
        if not tokens:
            raise ValueError("Claude command tokens must not be empty")

        variants = self._deduplicate_variant_commands([list(tokens)])

        logger.debug("Claude Code streaming variants: %s", variants)
        script = self._render_streaming_script("Claude Code", variants, task_id=task_id)
        return self._wrap_windows_shell_command(script, project_root)

    def _build_windows_gemini_command(
        self,
        tokens: Sequence[str],
        project_root: Path,
        *,
        task_id: Optional[str] = None,
    ) -> List[str]:
        """Launch Gemini CLI with AGENTS.md streamed over stdin."""
        logger.info("Building Gemini command with forced PowerShell shell")
        if not tokens:
            raise ValueError("Gemini command tokens must not be empty")

        base_tokens = list(tokens)
        variants = [
            self._append_unique_tokens(base_tokens, ["--stream"]),
            list(base_tokens),
        ]
        variants = self._deduplicate_variant_commands(variants)

        logger.debug("Gemini CLI streaming variants: %s", variants)
        script = self._render_streaming_script("Gemini CLI", variants, task_id=task_id)
        return self._wrap_windows_shell_command(script, project_root)

    def _check_gemini_cli_installed(self) -> bool:
        """Check if Gemini CLI (gemini) is available on the system."""
        return shutil.which("gemini") is not None

    def _build_windows_codex_command(
        self,
        tokens: Sequence[str],
        project_root: Path,
        *,
        task_id: Optional[str] = None,
    ) -> List[str]:
        """Launch Codex with AGENTS.md streamed over stdin."""
        logger.info("Building Codex command with forced PowerShell shell")
        if not tokens:
            raise ValueError("Codex command tokens must not be empty")

        base_tokens = self._ensure_working_directory_flag(tokens, project_root)
        variants = [
            list(base_tokens),
            self._append_unique_tokens(base_tokens, ["--dangerously-bypass-approvals-and-sandbox"]),
            self._append_unique_tokens(base_tokens, ["-a", "never", "-s", "danger-full-access"]),
        ]
        variants = self._deduplicate_variant_commands(variants)
        logger.debug("Codex streaming variants: %s", variants)
        script = self._render_streaming_script("Codex", variants, task_id=task_id)
        return self._wrap_windows_shell_command(script, project_root)

    def _ensure_working_directory_flag(self, tokens: Sequence[str], project_root: Path) -> List[str]:
        """
        Ensure the Codex command declares its working directory to avoid path confusion.
        """
        tokens_with_dir = list(tokens)
        has_flag = any(token.startswith("--working-directory=") for token in tokens_with_dir)

        if not has_flag:
            for index, token in enumerate(tokens_with_dir):
                if token == "--working-directory" and index + 1 < len(tokens_with_dir):
                    has_flag = True
                    break

        if not has_flag:
            tokens_with_dir.append(f"--working-directory={project_root}")

        return tokens_with_dir

    def _append_unique_tokens(self, tokens: Sequence[str], extras: Sequence[str]) -> List[str]:
        """
        Append additional flags when they are not already present.
        """
        updated = list(tokens)
        for extra in extras:
            if extra not in updated:
                updated.append(extra)
        return updated

    def _ensure_streaming_output_flag(self, tokens: Sequence[str]) -> List[str]:
        """
        Ensure the command emits streaming JSON output.
        """
        sanitized: List[str] = []
        skip_next = False
        for token in tokens:
            if skip_next:
                skip_next = False
                continue
            lowered = token.lower()
            if lowered in {"--output-format", "-o"}:
                skip_next = True
                continue
            if lowered.startswith("--output-format=") or lowered.startswith("-o="):
                continue
            sanitized.append(token)
        if not sanitized:
            return sanitized
        command_name = Path(sanitized[0]).stem.lower()
        if command_name in {"gemini", "gemini-cli"}:
            sanitized.extend(["-o", "stream-json"])
        else:
            sanitized.extend(["--output-format", "stream-json"])
        return sanitized

    def _deduplicate_variant_commands(
        self,
        variants: Sequence[Sequence[str]],
    ) -> List[List[str]]:
        """Collapse duplicate command variants while preserving order."""
        unique: List[List[str]] = []
        seen: set[tuple[str, ...]] = set()
        for variant in variants:
            key = tuple(variant)
            if not variant or key in seen:
                continue
            seen.add(key)
            unique.append(list(variant))
        return unique

    def _render_streaming_script(
        self,
        agent_label: str,
        variants: Sequence[Sequence[str]],
        task_id: Optional[str] = None,
    ) -> str:
        """Render a PowerShell script that pipes AGENTS.md into each command variant.

        Args:
            agent_label: Display name for the agent
            variants: Command variants to try
            task_id: Optional task ID for output logging
        """
        if not variants:
            raise ValueError("At least one command variant is required")
        label = agent_label.replace("'", "''")
        normalized_variants: List[List[str]] = []
        for variant in variants:
            variant_tokens = list(variant)
            if variant_tokens and variant_tokens[0].lower() in self.STREAMING_EXECUTABLES:
                variant_tokens = self._ensure_streaming_output_flag(variant_tokens)
            normalized_variants.append(variant_tokens)
        command_arrays = ", ".join(self._format_powershell_array(cmd) for cmd in normalized_variants)

        # Build the output redirection part if task_id is provided
        tee_redirect = ""
        if task_id:
            log_file = f".aura/{task_id}.output.log"
            tee_redirect = f" | Tee-Object -FilePath '{log_file}' -Append"

        script_lines = [
            "$ErrorActionPreference='Stop';",
            "$agentsPath=Join-Path (Get-Location) 'AGENTS.md';",
            "if (-not (Test-Path -LiteralPath $agentsPath)) { throw \"AGENTS.md not found\" }",
            f"$commands=@({command_arrays});",
            "$lastError=$null;",
            "$launched=$false;",
            "foreach ($args in $commands) {",
            "  if ($launched) { break }",
            "  try {",
            f"    Write-Host 'Launching {label}: ' -NoNewline; Write-Host ($args -join ' ');",
            "    if ($args.Length -gt 1) {",
            f"      Get-Content -LiteralPath $agentsPath -Raw | & $args[0] @($args[1..($args.Length - 1)]){tee_redirect}",
            "    } else {",
            f"      Get-Content -LiteralPath $agentsPath -Raw | & $args[0]{tee_redirect}",
            "    }",
            "    $launched=$true",
            "  } catch {",
            "    $lastError=$_;",
            "    Write-Warning ('Launch failed: ' + ($args -join ' '))",
            "  }",
            "}",
            "if (-not $launched -and $lastError) { throw $lastError }",
        ]
        return "\n".join(script_lines)

    def _wrap_windows_shell_command(
        self,
        script: str,
        project_root: Path,
    ) -> List[str]:
        """Wrap a PowerShell script for execution in PowerShell."""
        logger.info("Windows Terminal disabled; launching PowerShell in %s", project_root)
        command = self._build_windows_powershell_command(script)
        logger.info("Final command will be: %s", " ".join(command[:3]))
        return command

    def _build_windows_powershell_command(self, powershell_command: str) -> List[str]:
        """Return a native PowerShell invocation that keeps the window open."""
        if not powershell_command.strip():
            raise ValueError("PowerShell command must not be empty")
        if not self._powershell_executable:
            raise RuntimeError("PowerShell executable not resolved; cannot build command")
        if (
            self.terminal_shell_preference == "powershell"
            and self._windows_terminal_path
            and not self._windows_terminal_ignored_logged
        ):
            logger.info(
                "Ignoring Windows Terminal at %s in favor of native PowerShell as requested",
                self._windows_terminal_path,
            )
            self._windows_terminal_ignored_logged = True
        logger.debug(
            "Building native PowerShell invocation: executable=%s command=%s",
            self._powershell_executable,
            powershell_command,
        )
        return [
            self._powershell_executable,
            "-NoExit",
            "-Command",
            powershell_command,
        ]

    def _format_powershell_array(self, tokens: Sequence[str]) -> str:
        """
        Format a sequence of tokens into a PowerShell array literal.
        """
        formatted_tokens = [self._powershell_quote_token(token) for token in tokens]
        return f"@({', '.join(formatted_tokens)})"

    def _powershell_quote_token(self, token: str) -> str:
        """
        Quote tokens for safe PowerShell consumption.
        """
        escaped = token.replace("'", "''")
        return f"'{escaped}'"

    def _apply_autonomy_flags(self, agent_command: str, *, require_stdin: bool) -> List[str]:
        """
        Ensure Codex, Claude Code, and Gemini CLI run autonomously with streamed input.
        """
        normalized = agent_command.strip()
        if not normalized:
            return []

        try:
            tokens = shlex.split(normalized, posix=not sys.platform.startswith("win"))
        except ValueError:
            # Fallback: naive split when quoting is invalid
            tokens = normalized.split()

        if not tokens:
            return []

        executable = tokens[0].lower()

        def _ensure_flag(flag: str) -> None:
            if flag not in tokens:
                tokens.insert(1, flag)

        def _ensure_stdin_marker() -> None:
            if "-" not in tokens:
                tokens.append("-")

        if executable in {"codex", "codex.exe"}:
            _ensure_flag("--full-auto")
            if require_stdin:
                _ensure_stdin_marker()
            self._ensure_codex_autonomy_config()
        elif executable in {"claude-code", "claude", "claude.exe"}:
            _ensure_flag("--dangerously-skip-permissions")
            if require_stdin:
                _ensure_stdin_marker()
        elif executable in {"gemini", "gemini-cli"}:
            cleaned: List[str] = [tokens[0]]
            skip_next = False
            for token in tokens[1:]:
                if skip_next:
                    skip_next = False
                    continue
                lowered = token.lower()
                if lowered in {"-p", "--prompt"}:
                    skip_next = True
                    continue
                if lowered in {"--output-format", "-o"}:
                    skip_next = True
                    continue
                if lowered.startswith("--output-format=") or lowered.startswith("-o="):
                    continue
                if lowered == "--dangerously-skip-permissions":
                    continue
                if token == "-":
                    continue
                cleaned.append(token)
            tokens = cleaned
            lowered_flags = {token.lower() for token in tokens[1:]}
            if "--yolo" not in lowered_flags and "-y" not in lowered_flags:
                tokens.append("--yolo")

        if tokens and tokens[0].lower() in self.STREAMING_EXECUTABLES:
            tokens = self._ensure_streaming_output_flag(tokens)

        return tokens

    def _validate_preference_or_raise(self, command: Sequence[str], source: str) -> None:
        """Validate that the constructed command honors the terminal shell preference."""
        if not command:
            raise ValueError(f"{source} produced an empty terminal command")
        if self.terminal_shell_preference != "powershell" or not sys.platform.startswith("win"):
            return

        executable_name = Path(command[0]).name.lower()
        if executable_name in {"wt", "wt.exe"}:
            message = (
                f"PowerShell preference enforced, but {source} attempted to launch Windows Terminal ({command[0]})."
            )
            logger.error("✗ ERROR: %s", message)
            raise RuntimeError(message)
        if executable_name not in {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
            message = (
                f"PowerShell preference enforced, but {source} requested non-PowerShell executable '{command[0]}'."
            )
            logger.error("✗ ERROR: %s", message)
            raise RuntimeError(message)
        logger.info(
            "✓ Native PowerShell command validated via %s (executable=%s)",
            source,
            command[0],
        )

    def spawn_agent(
        self,
        spec: AgentSpecification,
        *,
        command_override: Optional[Sequence[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> TerminalSession:
        """
        Persist the specification and launch the external agent.
        """
        project_root = self._resolve_project_root(spec)
        logger.debug(
            "Project root resolved for task %s: %s (project_name=%s)",
            spec.task_id,
            project_root,
            getattr(spec, "project_name", None),
        )
        spec_path = self._persist_specification(spec)

        self._write_agents_md(project_root, spec)

        command_source = "template_command"
        # Use command override if provided, otherwise build from template
        if command_override:
            command_source = "command_override"
            logger.info(
                "command_override provided; validating against terminal_shell_preference. command_override=%s",
                command_override,
            )
            command = list(command_override)
        elif self.default_command:
            command_source = "default_command"
            logger.info(
                "default_command configured; validating against terminal_shell_preference. default_command=%s",
                self.default_command,
            )
            command = list(self.default_command)
        else:
            # Build command using template and spec path
            logger.info("Building command from template (honors terminal_shell_preference)")
            command = self._build_terminal_command(spec_path, project_root, spec)
        self._validate_preference_or_raise(command, command_source)

        self._ensure_agent_config(project_root)
        agent_type = self._detect_agent_type()
        logger.info(
            "TerminalAgentService: launching %s agent for task %s",
            agent_type,
            spec.task_id,
        )

        session_env = os.environ.copy()
        session_env.update(env or {})
        session_env["AURA_AGENT_SPEC_PATH"] = str(spec_path)
        session_env["AURA_AGENT_TASK_ID"] = spec.task_id

        # Prepare subprocess creation flags for visible terminal windows
        # Note: Only stdin is piped - stdout/stderr go to terminal so it stays visible
        # Output is captured via file redirection (see _render_streaming_script)
        popen_kwargs = {
            "cwd": str(project_root),
            "env": session_env,
            "stdin": subprocess.PIPE,
        }

        if sys.platform.startswith("win"):
            # For PowerShell, create a new visible console window
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
            logger.debug("Using CREATE_NEW_CONSOLE flag for Windows PowerShell visibility")
        else:
            # On Unix-like systems, the terminal emulator command itself creates a visible window
            logger.debug("Using native terminal emulator for Unix terminal visibility")

        creationflags = popen_kwargs.get("creationflags", 0)
        create_new_console = bool(creationflags & subprocess.CREATE_NEW_CONSOLE) if creationflags else False
        executable_name = command[0] if command else "EMPTY"
        logger.info(
            "Prepared spawn (source=%s, preference=%s, executable=%s, CREATE_NEW_CONSOLE=%s)",
            command_source,
            self.terminal_shell_preference,
            executable_name,
            create_new_console,
        )
        logger.info("FINAL COMMAND TO EXECUTE: %s", command)

        try:
            process = subprocess.Popen(command, **popen_kwargs)
            if sys.platform.startswith("win") and self.terminal_shell_preference == "powershell":
                logger.info(
                    "✓ Native PowerShell spawned (task=%s, pid=%s, executable=%s, window_visible=%s)",
                    spec.task_id,
                    process.pid if process else None,
                    Path(executable_name).name,
                    create_new_console,
                )
            else:
                logger.info(
                    "Spawned terminal agent (task=%s, pid=%s, command=%s)",
                    spec.task_id,
                    process.pid if process else None,
                    command,
                )
        except Exception as exc:
            logger.error(
                "✗ ERROR: Failed to spawn terminal agent for task %s: %s",
                spec.task_id,
                exc,
                exc_info=True,
            )
            raise

        return TerminalSession(
            task_id=spec.task_id,
            command=command,
            spec_path=str(spec_path),
            process_id=process.pid if process else None,
            process=process,
        )

    def start_output_monitor(self, session: TerminalSession, project_root: Optional[Path] = None) -> None:
        """
        Start a background thread to monitor agent output file and detect questions.

        Args:
            session: TerminalSession containing the process to monitor
            project_root: Optional project root path. If not provided, uses workspace_root
        """
        if not session.process:
            logger.warning("Cannot start output monitor: no process object in session")
            return

        # Determine the log file path
        if project_root is None:
            project_root = self.workspace_root

        log_file_path = project_root / ".aura" / f"{session.task_id}.output.log"

        def monitor_output():
            """Background thread that watches the output file and detects question patterns."""
            # Common question patterns to detect
            question_patterns = [
                r'\(y/n\)',
                r'\(yes/no\)',
                r'Continue\?',
                r'Approve\?',
                r'Proceed\?',
                r'\[Y/n\]',
                r'\[y/N\]',
            ]
            combined_pattern = re.compile('|'.join(question_patterns), re.IGNORECASE)

            logger.info(f"Output monitor waiting for log file: {log_file_path}")

            # Wait for the log file to be created (up to 10 seconds)
            max_wait = 10
            waited = 0
            while not log_file_path.exists() and waited < max_wait:
                import time
                time.sleep(0.5)
                waited += 0.5

            if not log_file_path.exists():
                logger.warning(f"Log file not created after {max_wait}s: {log_file_path}")
                return

            logger.info(f"Output monitor found log file: {log_file_path}")

            try:
                # Open the file and start reading from the beginning
                with open(log_file_path, 'r', encoding='utf-8', errors='replace') as f:
                    # Start from the beginning to catch all output
                    # Don't seek - just start reading

                    # Tail the file for content (including existing lines)
                    while True:
                        line = f.readline()
                        if not line:
                            # No new data, sleep briefly and check again
                            import time
                            time.sleep(0.1)

                            # Check if process is still alive
                            if session.process and session.process.poll() is not None:
                                # Process ended, read any remaining lines and exit
                                remaining = f.read()
                                if remaining:
                                    for final_line in remaining.split('\n'):
                                        if final_line.strip():
                                            logger.info(f"AGENT OUTPUT [{session.task_id}]: {final_line.strip()}")
                                            if combined_pattern.search(final_line):
                                                logger.info(f"🔔 AGENT QUESTION DETECTED [{session.task_id}]: {final_line.strip()}")
                                logger.info(f"Agent process ended, stopping output monitor for task {session.task_id}")
                                break
                            continue

                        decoded_line = line.strip()
                        if not decoded_line:
                            continue

                        # Log all output
                        logger.info(f"AGENT OUTPUT [{session.task_id}]: {decoded_line}")

                        # Check for question patterns
                        if combined_pattern.search(decoded_line):
                            logger.info(f"🔔 AGENT QUESTION DETECTED [{session.task_id}]: {decoded_line}")

            except Exception as exc:
                logger.error(f"Error monitoring output for task {session.task_id}: {exc}", exc_info=True)
            finally:
                logger.info(f"Output monitor stopped for task {session.task_id}")

        # Start the monitoring thread
        monitor_thread = threading.Thread(
            target=monitor_output,
            name=f"OutputMonitor-{session.task_id}",
            daemon=True
        )
        monitor_thread.start()
        logger.info(f"Started output monitor thread for task {session.task_id}")

    def send_response(self, session: TerminalSession, response: str) -> bool:
        """
        Send a response to the agent's stdin.

        Args:
            session: TerminalSession containing the process to send to
            response: The response string to send (will have \\n appended if not present)

        Returns:
            True if response was sent successfully, False otherwise
        """
        if not session.process:
            logger.warning("Cannot send response: no process object in session")
            return False

        if not session.process.stdin:
            logger.warning("Cannot send response: process stdin is None")
            return False

        try:
            # Ensure response ends with newline
            if not response.endswith('\n'):
                response += '\n'

            # Encode and write to stdin
            session.process.stdin.write(response.encode('utf-8'))
            session.process.stdin.flush()

            logger.info(f"✅ Sent response to agent [{session.task_id}]: {response.strip()}")
            return True

        except Exception as exc:
            logger.error(f"Failed to send response to agent [{session.task_id}]: {exc}", exc_info=True)
            return False

    def _detect_agent_type(self) -> str:
        """
        Detect which terminal agent is being used based on command template.

        Returns:
            "codex", "claude_code", "gemini-cli", "powershell", or "unknown"
        """
        template = (self.agent_command_template or "").lower()

        if "codex" in template:
            return "codex"
        elif "claude" in template or "claude-code" in template:
            return "claude_code"
        elif "gemini-cli" in template:
            return "gemini-cli"
        else:
            return "unknown"

    def _create_codex_config(self) -> None:
        """
        Create ~/.codex/config.toml with auto-approve settings.
        Delegates to existing _ensure_codex_autonomy_config method.
        """
        self._ensure_codex_autonomy_config()

    def _create_claude_code_config(self, project_root: Path) -> None:
        """
        Create .claude/config.json with permission allowlist.

        Args:
            project_root: Project root directory
        """
        claude_dir = project_root / ".claude"
        config_file = claude_dir / "config.json"

        config_data = {
            "permissions": {
                "allowedTools": [
                    "Read",
                    "Write(src/**)",
                    "Write(tests/**)",
                    "Write(workspace/**)",
                    "Bash(git *)",
                    "Bash(npm *)",
                    "Bash(python *)",
                    "Bash(pip *)",
                    "Bash(pytest *)"
                ],
                "deny": [
                    "Bash(rm -rf *)",
                    "Bash(sudo *)",
                    "Write(.env*)",
                    "Write(**/secrets/**)"
                ]
            }
        }

        try:
            claude_dir.mkdir(parents=True, exist_ok=True)

            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2)

            logger.info("Created Claude Code permissions config at %s", config_file)
        except Exception as exc:
            logger.warning("Failed to create Claude Code config: %s", exc)

    def _create_gemini_cli_config(self, project_root: Path) -> None:
        """
        Create or update the .env file in the project root with the Gemini API key.

        This function safely reads an existing .env file, preserves its contents,
        and adds or updates the GEMINI_API_KEY.

        Args:
            project_root: The root directory of the project.

        Raises:
            ValueError: If the Gemini API key is not configured in user settings.
        """
        from src.aura.services.user_settings_manager import load_user_settings

        settings = load_user_settings()
        api_keys = settings.get("api_keys", {})
        gemini_api_key = api_keys.get("gemini")

        if not gemini_api_key:
            raise ValueError(
                "Gemini API key not found in user settings. "
                "Please add your Gemini API key in the settings to use the Gemini CLI agent. "
                "You can obtain a key from Google AI Studio."
            )

        env_file = project_root / ".env"
        env_vars = {}
        if env_file.exists():
            try:
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            key, value = line.split("=", 1)
                            env_vars[key.strip()] = value.strip()
            except Exception as exc:
                logger.warning("Failed to read existing .env file: %s", exc)

        env_vars["GEMINI_API_KEY"] = gemini_api_key

        try:
            with open(env_file, "w", encoding="utf-8") as f:
                for key, value in env_vars.items():
                    f.write(f"{key}={value}\n")
            logger.info("Updated Gemini CLI .env file at %s", env_file)
        except Exception as exc:
            logger.warning("Failed to create or update Gemini CLI .env file: %s", exc)

    def _ensure_agent_config(self, project_root: Path) -> None:
        """
        Create appropriate config file based on which agent is being used.

        Args:
            project_root: Project root directory for config file placement
        """
        agent_type = self._detect_agent_type()

        if agent_type == "codex":
            self._create_codex_config()
            logger.info("TerminalAgentService: ensured Codex auto-approval config")
        elif agent_type == "claude_code":
            self._create_claude_code_config(project_root)
            logger.info("TerminalAgentService: ensured Claude Code permissions config")
        elif agent_type == "gemini-cli":
            if not self._check_gemini_cli_installed():
                raise RuntimeError(
                    "Gemini CLI not found. Please install it globally using: "
                    "npm install -g @google/gemini-cli@latest"
                )
            self._create_gemini_cli_config(project_root)
            logger.info("TerminalAgentService: ensured Gemini CLI environment configuration")
        else:
            logger.warning("Unknown agent type '%s', skipping auto-config creation", agent_type)

    def _codex_config_candidates(self) -> List[Path]:
        home = Path.home()
        return [
            home / ".codex" / "config.toml",
            home / ".config" / "codex" / "config.toml",
        ]

    @staticmethod
    def _codex_config_template() -> str:
        return (
            "# Generated by Aura to enable Codex autonomous mode on Windows.\n"
            'approval_policy = "never"\n'
            'sandbox_mode = "danger-full-access"\n'
            "\n"
            "[sandbox_workspace_write]\n"
            "network_access = true\n"
            "\n"
            "[tui]\n"
            "notifications = false\n"
        )

    def _ensure_codex_autonomy_config(self) -> None:
        if not sys.platform.startswith("win"):
            return

        config_content = self._codex_config_template()
        candidates = self._codex_config_candidates()
        last_error: Optional[Exception] = None
        last_path: Optional[Path] = None

        for candidate in candidates:
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.error(
                    "Failed to prepare Codex config directory %s: %s",
                    candidate,
                    exc,
                    exc_info=True,
                )
                last_error = exc
                last_path = candidate
                continue

            existing_content: Optional[str] = None
            try:
                if candidate.exists():
                    existing_content = candidate.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning(
                    "Unable to read Codex config from %s prior to update: %s",
                    candidate,
                    exc,
                    exc_info=True,
                )

            if existing_content == config_content:
                logger.debug("Codex config already up to date at %s", candidate)
                return

            try:
                candidate.write_text(config_content, encoding="utf-8")
                if existing_content is None:
                    logger.info("Created Codex config for autonomy at %s", candidate)
                else:
                    logger.info("Updated Codex config for autonomy at %s", candidate)
                return
            except OSError as exc:
                logger.error(
                    "Failed to write Codex config to %s: %s",
                    candidate,
                    exc,
                    exc_info=True,
                )
                last_error = exc
                last_path = candidate

        if last_error is not None:
            message = (
                f"Unable to create Codex configuration for auto-approval at {last_path}"
                if last_path
                else "Unable to create Codex configuration for auto-approval"
            )
            raise RuntimeError(message) from last_error

    def _persist_specification(self, spec: AgentSpecification) -> Path:
        spec_file = self.spec_dir / f"{spec.task_id}.md"
        spec_file.write_text(spec.prompt, encoding="utf-8")
        logger.debug("Wrote agent specification for task %s to %s", spec.task_id, spec_file)
        return spec_file

    def _resolve_project_root(self, spec: AgentSpecification) -> Path:
        base_root = self.workspace_root
        name = (spec.project_name or "").strip() if getattr(spec, "project_name", None) else ""

        # Treat metadata directory names as invalid project roots
        if name in {self.SPEC_DIR_NAME}:
            logger.error(
                "Invalid project_name '%s' on specification; using workspace root instead.",
                name,
            )
            name = ""

        if name:
            candidate = (base_root / name).resolve()
            try:
                candidate.relative_to(base_root.resolve())
            except ValueError:
                logger.warning(
                    "Project name '%s' resolved outside workspace; defaulting to workspace root.",
                    name,
                )
                candidate = base_root
        else:
            candidate = base_root

        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    def _write_agents_md(self, project_root: Path, spec: AgentSpecification) -> Path:
        agents_md = project_root / "AGENTS.md"
        content = format_specification_for_codex(spec)
        try:
            agents_md.write_text(content, encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to write AGENTS.md for task %s: %s", spec.task_id, exc, exc_info=True)
            raise RuntimeError(f"Failed to write AGENTS.md for task {spec.task_id}") from exc

        logger.info("Wrote AGENTS.md for task %s to %s", spec.task_id, agents_md)
        return agents_md
