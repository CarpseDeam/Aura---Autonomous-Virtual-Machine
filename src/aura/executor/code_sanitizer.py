"""Utilities for cleaning and parsing generated code."""

from __future__ import annotations

import json
import re
from typing import Any, Dict


class CodeSanitizer:
    """Strip formatting artifacts and safely parse structured payloads."""

    _LEADING_FENCE_PATTERN = re.compile(r"^```\w*\s*\n?", re.MULTILINE)
    _TRAILING_FENCE_PATTERN = re.compile(r"\n?```\s*$", re.MULTILINE)

    def strip_code_fences(self, text: str) -> str:
        """Remove triple backtick fences from a block of text."""
        cleaned = (text or "").strip()
        cleaned = self._LEADING_FENCE_PATTERN.sub("", cleaned)
        cleaned = self._TRAILING_FENCE_PATTERN.sub("", cleaned)
        return cleaned.strip()

    def sanitize_code(self, code: str) -> str:
        """Remove code fences and stray fence markers from generated code."""
        cleaned = self._LEADING_FENCE_PATTERN.sub("", code or "")
        cleaned = self._TRAILING_FENCE_PATTERN.sub("", cleaned)
        return cleaned.replace("```", "").strip()

    def parse_json_safely(self, text: str) -> Dict[str, Any]:
        """Attempt to parse a JSON payload, returning an empty dict on failure."""
        try:
            return json.loads(self.strip_code_fences(text))
        except Exception:
            return {}
