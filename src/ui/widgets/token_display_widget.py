from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel


class TokenDisplayWidget(QLabel):
    """
    Displays token usage with color-coded thresholds.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("token_usage_label")
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.update_usage(0, 1, 0.0)

    def update_usage(self, current: int, limit: int, percent: Optional[float]) -> None:
        """
        Update the token usage label with formatted counts and warning colors.
        """
        safe_limit = max(limit, 1)
        safe_percent = float(percent) if percent is not None else current / safe_limit
        safe_percent = max(safe_percent, 0.0)

        if safe_percent < 0.70:
            color = "#66BB6A"
        elif safe_percent < 0.85:
            color = "#FFEE58"
        else:
            color = "#EF5350"

        current_text = self._format_token_count(max(current, 0))
        limit_text = self._format_token_count(safe_limit)
        text = f"{current_text} / {limit_text} tokens ({safe_percent * 100:.0f}%)"
        self.setText(f"<span style='color: {color}; font-weight:bold;'>{text}</span>")

    @staticmethod
    def _format_token_count(tokens: int) -> str:
        absolute = abs(tokens)
        if absolute >= 1_000_000:
            value = tokens / 1_000_000
            return f"{value:.1f}m" if not value.is_integer() else f"{int(value)}m"
        if absolute >= 1_000:
            value = tokens / 1_000
            return f"{value:.1f}k" if not value.is_integer() else f"{int(value)}k"
        return str(tokens)
