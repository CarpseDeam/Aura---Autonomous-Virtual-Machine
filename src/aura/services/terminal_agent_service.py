from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
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
    _TASK_SENTINEL = "__AURA_CODEX_TASK__"

    def __init__(
        self,
        workspace_root: Path,
        default_command: Optional[Sequence[str]] = None,
        agent_command_template: Optional[str] = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.default_command = list(default_command) if default_command else None
        self.agent_command_template = agent_command_template or "cat {spec_path}"
        self.spec_dir = self.workspace_root / self.SPEC_DIR_NAME
        self.spec_dir.mkdir(parents=True, exist_ok=True)
        logger.info("TerminalAgentService ready (spec dir: %s, template: %s)",
                   self.spec_dir, self.agent_command_template)

    def _has_windows_terminal(self) -> bool:
        """
        Check if Windows Terminal (wt.exe) is available on the system.

        Returns:
            True if Windows Terminal is found, False otherwise.
        """
        import shutil
        wt_path = shutil.which("wt") or shutil.which("wt.exe")
        return wt_path is not None

    def _build_terminal_command(self, spec_path: Path, project_root: Path) -> List[str]:
        """
        Build a platform-specific command that opens a visible terminal and runs the agent.

        Args:
            spec_path: Path to the persisted specification file
            project_root: Path to the project root directory

        Returns:
            Command list ready for subprocess.Popen
        """
        # Construct AGENTS.md path in project root
        agents_md_path = project_root / "AGENTS.md"

        # Format the agent command with the spec path
        agent_command_template = self.agent_command_template.format(
            spec_path=str(spec_path),
            task_id=spec_path.stem,
        ).strip()

        is_windows = sys.platform.startswith("win")
        first_token = agent_command_template.split(maxsplit=1)[0].lower() if agent_command_template else ""
        runs_codex = first_token in {"codex", "codex.exe"}
        require_stdin = not (is_windows and runs_codex)
        agent_tokens = self._apply_autonomy_flags(agent_command_template, require_stdin=require_stdin)
        agent_command = " ".join(agent_tokens) if agent_tokens else agent_command_template

        if is_windows:
            # Codex: Use special Windows command with auto-approval bypass
            if agent_tokens and agent_tokens[0].lower() in {"codex", "codex.exe"}:
                windows_command = self._build_windows_codex_command(
                    agent_tokens,
                    agents_md_path,
                    project_root,
                )
                logger.debug("Constructed Windows Codex command: %s", windows_command)
                return windows_command

            # Claude Code: Launch in headless mode with -p and AGENTS.md content
            if agent_tokens and agent_tokens[0].lower() in {"claude", "claude-code", "claude.exe"}:
                windows_command = self._build_windows_claude_headless_command(
                    agents_md_path=agents_md_path,
                    project_root=project_root,
                )
                logger.debug("Constructed Windows headless command for Claude Code: %s", windows_command)
                return windows_command

            # Gemini CLI: Launch in headless mode with -p and AGENTS.md content
            if agent_tokens and agent_tokens[0].lower() in {"gemini", "gemini-cli"}:
                windows_command = self._build_windows_gemini_command(
                    agents_md_path=agents_md_path,
                    project_root=project_root,
                )
                logger.debug("Constructed Windows headless command for Gemini CLI: %s", windows_command)
                return windows_command

            # Default: Try Windows Terminal, fallback to PowerShell
            logger.debug("Unknown agent type on Windows, attempting Windows Terminal")
            return self._build_windows_terminal_command(agent_command, agents_md_path, project_root)
        else:
            # Unix: Launch Claude in headless mode with -p and AGENTS.md content when applicable
            first_token = agent_tokens[0].lower() if agent_tokens else ""
            agents_md_quoted = shlex.quote(str(agents_md_path))
            if first_token in {"claude", "claude-code"}:
                headless_cmd = f"CLAUDE_PROMPT=$(cat {agents_md_quoted}) && claude -p \"$CLAUDE_PROMPT\" --dangerously-skip-permissions"
            else:
                # Default behaviour for non-Claude agents: keep previous pipe with slight delay
                headless_cmd = f"sleep 2 && cat {agents_md_quoted} | {agent_command}"

            # Unix: Try to find an available terminal emulator
            terminal_emulators = [
                ("gnome-terminal", ["--", "bash", "-c", f"{headless_cmd}; exec bash"]),
                ("konsole", ["-e", "bash", "-c", f"{headless_cmd}; exec bash"]),
                ("xterm", ["-hold", "-e", "bash", "-c", headless_cmd]),
            ]

            # Try each terminal emulator until we find one that exists
            import shutil
            for emulator, args in terminal_emulators:
                if shutil.which(emulator):
                    logger.debug("Using terminal emulator: %s", emulator)
                    return [emulator] + args

            # Fallback: just run bash directly (won't be visible on Unix without terminal)
            logger.warning("No terminal emulator found, falling back to direct bash execution")
            return ["bash", "-c", headless_cmd]

    def _build_windows_passthrough_command(self, agent_command: str, agents_md_path: Path) -> List[str]:
        """
        Build the legacy Windows command that streams AGENTS.md into the agent process.
        """
        agents_md_literal = str(agents_md_path).replace("'", "''")
        reader_command = f"Get-Content -Raw -Encoding UTF8 '{agents_md_literal}'"
        delayed_command = f"Start-Sleep -Seconds 2; {reader_command} | {agent_command}"
        return [
            "pwsh.exe",
            "-NoExit",
            "-Command",
            delayed_command,
        ]

    def _build_windows_terminal_command(
        self,
        agent_command: str,
        agents_md_path: Path,
        project_root: Path,
    ) -> List[str]:
        """
        Build command using Windows Terminal for interactive TUI apps.

        For Claude Code, DO NOT pipe AGENTS.md into stdin because piping breaks
        raw mode and causes "Raw mode not supported". Instead, launch the agent
        directly in the project root and let it read AGENTS.md itself.

        Args:
            agent_command: The full command to execute (e.g., "claude")
            agents_md_path: Path to AGENTS.md (used only for non-TUI agents)
            project_root: Project root directory (working directory for the session)

        Returns:
            Command list for Windows Terminal execution
        """
        if not self._has_windows_terminal():
            logger.warning(
                "Windows Terminal (wt.exe) not found. Install from: https://aka.ms/terminal"
            )
            logger.info(
                "Falling back to PowerShell passthrough; interactive TUIs may fail. "
                "Consider running in WSL2 for better terminal support."
            )
            return self._build_windows_passthrough_command(agent_command, agents_md_path)

        # Detect Claude Code to preserve stdin for raw mode TUI
        agent_tokens = agent_command.split()
        first_token = agent_tokens[0].lower() if agent_tokens else ""
        is_claude_code = first_token in {"claude", "claude-code", "claude.exe"}

        if is_claude_code:
            # Prefer headless mode for Claude: pass AGENTS.md content via -p
            return self._build_windows_claude_headless_command(
                agents_md_path=agents_md_path,
                project_root=project_root,
            )

        # For other agents that don't require raw mode, keep the pipe behavior
        agents_md_literal = str(agents_md_path).replace('"', '""')
        delayed_command = (
            f'timeout /t 2 /nobreak > nul && type "{agents_md_literal}" | {agent_command}'
        )
        logger.debug("Launching non-TUI agent with piped AGENTS.md via Windows Terminal")
        return [
            "wt.exe",
            "-d",
            str(project_root),
            "cmd",
            "/c",
            delayed_command,
        ]

    def _build_windows_claude_headless_command(self, agents_md_path: Path, project_root: Path) -> List[str]:
        """
        Build a Windows Terminal command that invokes Claude Code with task from AGENTS.md.

        Uses simple cmd.exe to avoid PowerShell PATH issues.
        Reads AGENTS.md and passes content via -p flag for headless execution.
        """
        # Path escaping not required; CLI reads AGENTS.md directly

        # Simple approach: Let claude read AGENTS.md itself, just tell it to in the -p prompt
        cmd_command = (
            'claude -p "Read and execute all tasks in the AGENTS.md file in the current directory. '
            'Work autonomously without asking for confirmation. When complete, write .aura/{task_id}.summary.json '
            'and .aura/{task_id}.done files as specified in the completion protocol." '
            '--dangerously-skip-permissions'
        )

        logger.debug("Launching Claude Code with simple cmd.exe command")
        return [
            "wt.exe",
            "-d",
            str(project_root),
            "cmd",
            "/c",
            cmd_command,
        ]

    def _build_windows_gemini_command(self, agents_md_path: Path, project_root: Path) -> List[str]:
        """
        Build a Windows Terminal command that invokes Gemini CLI with a task from AGENTS.md.

        This method constructs a command to be run in a new Windows Terminal (`wt.exe`)
        session. It is carefully designed to ensure reliable, autonomous execution
        of the Gemini CLI agent on Windows.

        Architecture Flow:
        1. `wt.exe`: The command starts with Windows Terminal to provide a visible,
           isolated terminal environment for the agent.
        2. `-d str(project_root)`: Sets the starting directory of the terminal to the
           project's root, ensuring the agent runs in the correct context.
        3. `cmd /c ...`: Inside the terminal, `cmd.exe` is used as the shell to execute
           the agent command. `cmd.exe` is chosen over PowerShell for simplicity and
           robustness, as it avoids potential issues with PowerShell's Execution Policy
           and complex PATH variable resolution that can vary between user setups.
        4. `gemini -p "..."`: The actual Gemini CLI command. The `-p` flag passes a
           detailed prompt that instructs the agent to read the `AGENTS.md` file.
        5. `--dangerously-skip-permissions`: This flag is crucial for autonomous
           operation. It tells the Gemini CLI to proceed without interactive permission
           prompts, which would otherwise halt the execution of the agent.

        Completion Protocol:
        The prompt given to the agent includes instructions for a completion protocol.
        Upon finishing its tasks, the agent is expected to write two files to the
        `.aura/` directory:
        - `.aura/{task_id}.summary.json`: A JSON file summarizing the results.
        - `.aura/{task_id}.done`: An empty file that acts as a sentinel, signaling
          that the task is complete.
        """
        # Path escaping not required; CLI reads AGENTS.md directly

        cmd_command = (
            'gemini -p "Read and execute all tasks in the AGENTS.md file in the current directory. '
            'Work autonomously without asking for confirmation. When complete, write .aura/{task_id}.summary.json '
            'and .aura/{task_id}.done files as specified in the completion protocol." '
            '--dangerously-skip-permissions'
        )

        logger.debug("Launching Gemini CLI with simple cmd.exe command")
        return [
            "wt.exe",
            "-d",
            str(project_root),
            "cmd",
            "/c",
            cmd_command,
        ]

    def _check_gemini_cli_installed(self) -> bool:
        """Check if Gemini CLI (gemini) is available on the system."""
        return shutil.which("gemini") is not None


    def _build_windows_codex_command(
        self,
        tokens: Sequence[str],
        agents_md_path: Path,
        project_root: Path,
    ) -> List[str]:
        """
        Build a Windows command that bypasses the Codex approval menu by passing the task directly.
        """
        if not tokens:
            raise ValueError("Codex command tokens must not be empty")

        task_description = self._build_codex_task_description(agents_md_path)
        base_tokens = self._ensure_working_directory_flag(tokens, project_root)

        variants: List[List[str]] = [
            list(base_tokens),
            self._append_unique_tokens(base_tokens, ["-a", "never", "-s", "danger-full-access"]),
            self._append_unique_tokens(base_tokens, ["--dangerously-bypass-approvals-and-sandbox"]),
        ]

        script_variants = [
            self._format_powershell_array([*variant, self._TASK_SENTINEL]) for variant in variants
        ]
        script = self._render_codex_launch_script(script_variants, task_description)

        readable_variants = [[*variant, task_description] for variant in variants]
        logger.debug("Codex command variants for Windows: %s", readable_variants)

        return [
            "pwsh.exe",
            "-NoExit",
            "-Command",
            script,
        ]

    def _build_codex_task_description(self, agents_md_path: Path) -> str:
        """
        Build the Codex task description passed as a direct CLI argument.
        """
        return (
            f"Open the AGENTS.md file located at {agents_md_path} and execute the instructions it contains."
        )

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
        if token == self._TASK_SENTINEL:
            return "$task"
        escaped = token.replace("'", "''")
        return f"'{escaped}'"

    def _render_codex_launch_script(self, script_variants: Sequence[str], task_description: str) -> str:
        """
        Render the PowerShell command that attempts multiple Codex launch strategies.
        """
        commands_block = ",\n    ".join(script_variants)
        fallback_message = (
            "Codex approval bypass failed on Windows. Known issue: https://github.com/openai/codex/issues/2828 "
            "Recommended: Install WSL2 and run Codex from Linux environment"
        )
        return (
            "$task = @'\n"
            f"{task_description}\n"
            "'@\n"
            "$commands = @(\n"
            f"    {commands_block}\n"
            ")\n"
            "$launched = $false\n"
            "$lastError = $null\n"
            "foreach ($args in $commands) {\n"
            "    if ($launched) { break }\n"
            "    try {\n"
            "        Write-Host ('Launching Codex: ' + ($args -join ' '))\n"
            "        if ($args.Length -gt 1) {\n"
            "            & $args[0] @($args[1..($args.Length - 1)])\n"
            "        } else {\n"
            "            & $args[0]\n"
            "        }\n"
            "        $launched = $true\n"
            "    } catch {\n"
            "        $lastError = $_\n"
            "        Write-Warning ('Codex launch failed with configuration: ' + ($args -join ' '))\n"
            "    }\n"
            "}\n"
            "if (-not $launched) {\n"
            f"    Write-Error \"{fallback_message}\"\n"
            "    if ($lastError) {\n"
            "        Write-Error $lastError\n"
            "    }\n"
            "}\n"
        )

    def _apply_autonomy_flags(self, agent_command: str, *, require_stdin: bool) -> List[str]:
        """
        Ensure Codex and Claude Code run in autonomous mode and handle specification input correctly.
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

        return tokens

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

        # Use command override if provided, otherwise build from template
        if command_override:
            command = list(command_override)
        elif self.default_command:
            command = list(self.default_command)
        else:
            # Build command using template and spec path
            command = self._build_terminal_command(spec_path, project_root)

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
        popen_kwargs = {
            "cwd": str(project_root),
            "env": session_env,
        }

        if sys.platform.startswith("win"):
            # Check if using Windows Terminal (wt.exe)
            # Windows Terminal creates its own window, so CREATE_NEW_CONSOLE is not needed
            uses_windows_terminal = command and len(command) > 0 and command[0].lower() in {"wt.exe", "wt"}

            if uses_windows_terminal:
                logger.debug("Using Windows Terminal - CREATE_NEW_CONSOLE not required")
            else:
                # For PowerShell/cmd without wt.exe, create a new visible console window
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
                logger.debug("Using CREATE_NEW_CONSOLE flag for Windows terminal visibility")
        else:
            # On Unix-like systems, the terminal emulator command itself creates a visible window
            logger.debug("Using native terminal emulator for Unix terminal visibility")

        try:
            process = subprocess.Popen(command, **popen_kwargs)
            logger.info(
                "Spawned terminal agent (task=%s, pid=%s, command=%s)",
                spec.task_id,
                process.pid if process else None,
                command,
            )
        except Exception as exc:
            logger.error("Failed to spawn terminal agent for task %s: %s", spec.task_id, exc, exc_info=True)
            raise

        return TerminalSession(
            task_id=spec.task_id,
            command=command,
            spec_path=str(spec_path),
            process_id=process.pid if process else None,
        )


    def _detect_agent_type(self) -> str:
        """
        Detect which terminal agent is being used based on command template.

        Returns:
            "codex", "claude_code", or "unknown"
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

    def _ensure_codex_config_for_command(self, command: Sequence[str]) -> None:
        if not sys.platform.startswith("win"):
            return

        try:
            lowered_tokens = [str(token).lower() for token in command]
        except Exception:
            return

        if any("codex" in token for token in lowered_tokens):
            self._ensure_codex_autonomy_config()

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
