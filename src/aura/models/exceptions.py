"""
Custom exceptions raised by the LLMService.

These errors are designed to provide actionable feedback about why a
language-model request failed so the caller can offer a graceful user
experience while surfacing the root cause to operators.
"""
from __future__ import annotations

from typing import Optional


class LLMServiceError(Exception):
    """
    Base exception for failures that originate from the LLM service layer.

    Args:
        message: Human-readable description of the error.
        agent_name: Optional agent identifier associated with the failure.
        cause: Optional underlying exception that triggered the failure.
    """

    def __init__(
        self,
        message: str,
        *,
        agent_name: Optional[str] = None,
        cause: Optional[Exception] = None,
    ) -> None:
        super().__init__(message)
        self.agent_name = agent_name
        self.__cause__ = cause


class LLMRateLimitError(LLMServiceError):
    """
    Raised when an LLM provider signals that the client exceeded a rate limit
    or quota threshold.
    """


class LLMTimeoutError(LLMServiceError):
    """
    Raised when an LLM provider request exceeds the allotted timeout window.
    """


class LLMConnectionError(LLMServiceError):
    """
    Raised when the client cannot reach the LLM provider due to network
    connectivity issues.
    """

