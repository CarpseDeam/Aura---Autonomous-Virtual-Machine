import logging
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import (
    QTimer, QPropertyAnimation, QEasingCurve, Qt, Property, QSequentialAnimationGroup,
    QPauseAnimation
)
from PySide6.QtGui import QPainter, QColor, QLinearGradient

logger = logging.getLogger(__name__)


class KnightRiderWidget(QWidget):
    """
    A Knight Rider KITT-style scanning animation widget.
    Uses QPropertyAnimation for smooth movement and QLinearGradient for the trail effect.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scan_position = 0.0
        self.is_animating = False

        self.setFixedHeight(8)  # A bit taller for a better gradient effect
        self.setMinimumWidth(300)

        # Core color for the scanner's "head"
        self.active_color = QColor("#FFB74D")
        # The background can be transparent to blend with the parent widget
        self.background_color = QColor(Qt.GlobalColor.transparent)

        self._setup_animation()

    @Property(float)
    def scan_position(self):
        return self._scan_position

    @scan_position.setter
    def scan_position(self, value):
        self._scan_position = value
        self.update()  # Trigger a repaint every time the position changes

    def _setup_animation(self):
        """Sets up the sequential animation for the back-and-forth scanning."""
        # Forward animation (left to right)
        forward_anim = QPropertyAnimation(self, b"scan_position", self)
        forward_anim.setStartValue(0.0)
        forward_anim.setEndValue(1.0)
        forward_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        forward_anim.setDuration(1000)

        # Backward animation (right to left)
        backward_anim = QPropertyAnimation(self, b"scan_position", self)
        backward_anim.setStartValue(1.0)
        backward_anim.setEndValue(0.0)
        backward_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        backward_anim.setDuration(1000)

        # Create a sequence
        self.animation = QSequentialAnimationGroup()
        self.animation.addAnimation(forward_anim)
        self.animation.addAnimation(QPauseAnimation(100)) # Brief pause at the end
        self.animation.addAnimation(backward_anim)
        self.animation.addAnimation(QPauseAnimation(100)) # Brief pause at the end
        self.animation.setLoopCount(-1)  # Loop indefinitely

    def start_animation(self):
        """Starts the Knight Rider scanning animation."""
        if not self.is_animating:
            self.is_animating = True
            self.animation.start()
            self.show()
            logger.debug("Knight Rider animation started")

    def stop_animation(self):
        """Stops the animation and hides the widget."""
        if self.is_animating:
            self.is_animating = False
            self.animation.stop()
            self.hide()
            logger.debug("Knight Rider animation stopped")

    def paintEvent(self, event):
        """Paints the scanner using a QLinearGradient for a smooth trail effect."""
        if not self.is_animating:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Clear the background
        painter.fillRect(self.rect(), self.background_color)

        # --- Gradient Logic ---
        widget_width = self.width()
        widget_height = self.height()

        # The current center position of the scanner's head
        current_x = widget_width * self._scan_position

        # Width of the scanner's trail and head
        trail_length = widget_width * 0.4  # Trail is 40% of the widget width
        head_width = 5.0  # The bright head is a few pixels wide

        # Define the gradient area
        gradient_start_x = current_x - (trail_length / 2)
        gradient_end_x = current_x + (trail_length / 2)

        gradient = QLinearGradient(gradient_start_x, 0, gradient_end_x, 0)

        # Define the color stops for the gradient
        # Transparent at the very start of the trail
        gradient.setColorAt(0.0, Qt.GlobalColor.transparent)

        # Fade in to a dim amber
        dim_amber = self.active_color.darker(150)
        dim_amber.setAlphaF(0.5)
        gradient.setColorAt(0.4, dim_amber)

        # The bright head of the scanner
        # We create a sharp peak in the middle of the gradient
        head_pos_in_gradient = 0.5
        gradient.setColorAt(head_pos_in_gradient - (head_width / trail_length), self.active_color)
        gradient.setColorAt(head_pos_in_gradient, self.active_color)
        gradient.setColorAt(head_pos_in_gradient + (head_width / trail_length), self.active_color)

        # Fade out to dim amber again
        gradient.setColorAt(0.6, dim_amber)

        # Transparent at the very end of the trail
        gradient.setColorAt(1.0, Qt.GlobalColor.transparent)

        # --- Drawing ---
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(self.rect())


class ThinkingIndicator(QWidget):
    """
    Complete thinking indicator widget with Knight Rider animation and optional text.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self):
        """Set up the UI layout."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(10)

        # Thinking text label
        self.thinking_label = QLabel("AURA is thinking...")
        self.thinking_label.setStyleSheet("""
            QLabel {
                color: #FFB74D;
                font-family: "JetBrains Mono", "Courier New", Courier, monospace;
                font-size: 12px;
                font-weight: bold;
            }
        """)

        # Knight Rider animation
        self.knight_rider = KnightRiderWidget()

        # Add to layout
        layout.addWidget(self.thinking_label)
        layout.addWidget(self.knight_rider, 1)  # Stretch factor of 1

        # Initially hidden
        self.hide()

    def start_thinking(self, message: str = "AURA is thinking..."):
        """
        Start the thinking animation with optional custom message.

        Args:
            message: Custom thinking message to display
        """
        self.thinking_label.setText(message)
        self.knight_rider.start_animation()
        self.show()
        logger.info(f"Thinking indicator started: {message}")

    def stop_thinking(self):
        """Stop the thinking animation and hide the indicator."""
        self.knight_rider.stop_animation()
        self.hide()
        logger.info("Thinking indicator stopped")

    def set_thinking_message(self, message: str):
        """Update the thinking message while animation is running."""
        self.thinking_label.setText(message)
