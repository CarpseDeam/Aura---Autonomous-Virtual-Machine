"""Output monitoring strategies for terminal agent sessions."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)


class OutputMonitor(ABC):
    """Abstract base for output monitoring strategies."""

    @abstractmethod
    def start_monitoring(self, output_path: Path, on_line: Callable[[str], None]) -> None:
        """Start monitoring output and call on_line for each new line."""
        pass

    @abstractmethod
    def stop_monitoring(self) -> None:
        """Stop monitoring and cleanup resources."""
        pass

    @abstractmethod
    def is_running(self) -> bool:
        """Check if monitoring is active."""
        pass


class FileStreamMonitor(OutputMonitor):
    """Monitors a log file for new content (file tailing)."""

    def __init__(self, poll_interval: float = 0.1, child_process: Optional[Any] = None) -> None:
        """Initialize file monitor with polling interval in seconds and optional child process."""
        self.poll_interval = poll_interval
        self.child_process = child_process
        self._monitoring = False
        self._last_position = 0
        self._idle_cycles = 0
        self._max_idle_cycles = 30

    def start_monitoring(self, output_path: Path, on_line: Callable[[str], None]) -> None:
        """Start tailing the output file and invoke callback for each line."""
        self._monitoring = True
        self._last_position = 0
        self._idle_cycles = 0

        logger.info("Starting file monitoring: %s", output_path)

        wait_count = 0
        while not output_path.exists() and wait_count < 50:
            time.sleep(0.1)
            wait_count += 1

        if not output_path.exists():
            logger.warning("Output file did not appear: %s", output_path)
            return

        try:
            with output_path.open("r", encoding="utf-8", errors="replace") as f:
                while self._monitoring:
                    if self.child_process and hasattr(self.child_process, 'exitstatus'):
                        if self.child_process.exitstatus is not None:
                            logger.info("Child process exited, stopping file monitoring")
                            break

                    current_size = output_path.stat().st_size

                    if current_size > self._last_position:
                        f.seek(self._last_position)
                        new_content = f.read()

                        for line in new_content.splitlines():
                            if line.strip():
                                on_line(line)

                        self._last_position = f.tell()
                        self._idle_cycles = 0
                    else:
                        self._idle_cycles += 1
                        if self._idle_cycles >= self._max_idle_cycles:
                            logger.info("File monitoring idle timeout reached")
                            break

                    time.sleep(self.poll_interval)

        except Exception as exc:
            logger.error("File monitoring error: %s", exc, exc_info=True)
        finally:
            logger.info("File monitoring stopped: %s", output_path)

    def stop_monitoring(self) -> None:
        """Stop monitoring the file."""
        self._monitoring = False

    def is_running(self) -> bool:
        """Check if monitoring is active."""
        return self._monitoring


class PipeStreamMonitor(OutputMonitor):
    """Monitors a process pipe directly (Unix/existing approach)."""

    def __init__(self, child_process: Any) -> None:
        """Initialize with child process that has readline() method."""
        self.child = child_process
        self._monitoring = False

    def start_monitoring(self, output_path: Path, on_line: Callable[[str], None]) -> None:
        """Read from child process pipe and invoke callback for each line."""
        self._monitoring = True
        logger.info("Starting pipe monitoring")

        try:
            while self._monitoring:
                try:
                    line = self.child.readline()
                    if line and line.strip():
                        on_line(line.rstrip('\r\n'))
                except Exception as exc:
                    if "TIMEOUT" in str(type(exc).__name__):
                        continue
                    elif "EOF" in str(type(exc).__name__):
                        break
                    else:
                        raise
        except Exception as exc:
            logger.error("Pipe monitoring error: %s", exc, exc_info=True)
        finally:
            logger.info("Pipe monitoring stopped")

    def stop_monitoring(self) -> None:
        """Stop monitoring the pipe."""
        self._monitoring = False

    def is_running(self) -> bool:
        """Check if monitoring is active."""
        return self._monitoring
