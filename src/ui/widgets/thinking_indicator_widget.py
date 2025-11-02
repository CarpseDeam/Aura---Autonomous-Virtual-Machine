from __future__ import annotations

from src.ui.widgets.knight_rider_widget import ThinkingIndicator


class ThinkingIndicatorWidget(ThinkingIndicator):
    """
    Thin wrapper around the Knight Rider thinking indicator for explicit naming.
    """

    @property
    def is_animating(self) -> bool:
        return self.knight_rider.is_animating
