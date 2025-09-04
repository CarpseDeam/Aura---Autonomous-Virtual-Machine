import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from src.aura.config import LOGS_DIR


class ColorFormatter(logging.Formatter):
    """A logging formatter that adds color to console output."""

    GREY = "\x1b[38;20m"
    YELLOW = "\x1b[33;20m"
    RED = "\x1b[31;20m"
    BOLD_RED = "\x1b[31;1m"
    CYAN = "\x1b[36;20m"
    RESET = "\x1b[0m"

    # Define the format for each log level
    FORMATS = {
        logging.DEBUG: f"{CYAN}%(asctime)s - %(levelname)s - %(name)s:%(funcName)s:%(lineno)d - %(message)s{RESET}",
        logging.INFO: f"{GREY}%(asctime)s - %(levelname)s - %(name)s:%(funcName)s:%(lineno)d - %(message)s{RESET}",
        logging.WARNING: f"{YELLOW}%(asctime)s - %(levelname)s - %(name)s:%(funcName)s:%(lineno)d - %(message)s{RESET}",
        logging.ERROR: f"{RED}%(asctime)s - %(levelname)s - %(name)s:%(funcName)s:%(lineno)d - %(message)s{RESET}",
        logging.CRITICAL: f"{BOLD_RED}%(asctime)s - %(levelname)s - %(name)s:%(funcName)s:%(lineno)d - %(message)s{RESET}"
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


class LoggingService:
    """
    A service to configure centralized logging for the application.
    """
    LOG_FILE = "aura.log"

    @staticmethod
    def setup_logging():
        """
        Configures the root logger for file and console output.
        This should be called once when the application starts.
        """
        # Ensure the log directory exists
        LOGS_DIR.mkdir(exist_ok=True)
        log_file_path = LOGS_DIR / LoggingService.LOG_FILE

        # Get the root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)

        # Create console handler with color formatter
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(ColorFormatter())

        # Create rotating file handler
        file_handler = RotatingFileHandler(
            log_file_path, maxBytes=10 * 1024 * 1024, backupCount=5
        )
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(name)s:%(funcName)s:%(lineno)d - %(message)s'
        )
        file_handler.setFormatter(file_formatter)

        # Add handlers to the root logger
        root_logger.addHandler(console_handler)
        root_logger.addHandler(file_handler)

        logging.info("Logging service initialized.")