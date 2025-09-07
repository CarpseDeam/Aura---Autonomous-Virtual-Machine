import logging
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import QTimer, QPropertyAnimation, QEasingCurve, Qt, QRect
from PySide6.QtGui import QPainter, QColor, QPen, QBrush

logger = logging.getLogger(__name__)


class KnightRiderWidget(QWidget):
    """
    Knight Rider style scanning animation widget in AURA's amber color scheme.
    Shows a moving light bar to indicate AI processing/thinking state.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)  # Thin bar
        self.setMinimumWidth(300)
        
        # Animation properties
        self.dot_count = 7
        self.current_position = 0
        self.direction = 1  # 1 for right, -1 for left
        self.dot_width = 20
        self.dot_spacing = 8
        
        # Colors - matching AURA's amber theme
        self.active_color = QColor("#FFB74D")  # Bright amber
        self.dim_color = QColor("#FF8F00")     # Darker amber
        self.background_color = QColor("#2c2c2c")  # Dark background
        
        # Animation timer
        self.timer = QTimer()
        self.timer.timeout.connect(self._update_animation)
        self.animation_speed = 120  # milliseconds
        
        # State tracking
        self.is_animating = False
        
        # Set up widget properties
        self.setStyleSheet("background-color: transparent;")
        
        logger.debug("KnightRiderWidget initialized")

    def start_animation(self):
        """Start the Knight Rider scanning animation."""
        if not self.is_animating:
            self.is_animating = True
            self.current_position = 0
            self.direction = 1
            self.timer.start(self.animation_speed)
            self.show()
            logger.debug("Knight Rider animation started")

    def stop_animation(self):
        """Stop the animation and hide the widget."""
        if self.is_animating:
            self.is_animating = False
            self.timer.stop()
            self.hide()
            logger.debug("Knight Rider animation stopped")

    def _update_animation(self):
        """Update animation frame - move the scanning light."""
        # Update position
        self.current_position += self.direction
        
        # Check boundaries and reverse direction
        if self.current_position >= self.dot_count - 1:
            self.direction = -1
        elif self.current_position <= 0:
            self.direction = 1
            
        # Trigger repaint
        self.update()

    def paintEvent(self, event):
        """Custom paint event to draw the Knight Rider animation."""
        if not self.is_animating:
            return
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Fill background
        painter.fillRect(self.rect(), self.background_color)
        
        # Calculate dimensions
        widget_width = self.width()
        total_dots_width = (self.dot_count * self.dot_width) + ((self.dot_count - 1) * self.dot_spacing)
        start_x = (widget_width - total_dots_width) // 2
        
        # Draw dots
        for i in range(self.dot_count):
            x = start_x + i * (self.dot_width + self.dot_spacing)
            y = (self.height() - 4) // 2  # Center vertically, 4px height
            
            # Calculate brightness based on distance from current position
            distance = abs(i - self.current_position)
            
            if distance == 0:
                # Active dot - brightest
                color = self.active_color
                height = 4
            elif distance == 1:
                # Adjacent dots - medium brightness
                color = QColor(self.active_color.red(), self.active_color.green(), 
                             self.active_color.blue(), 180)
                height = 3
            elif distance == 2:
                # Far dots - dim
                color = QColor(self.active_color.red(), self.active_color.green(), 
                             self.active_color.blue(), 100)
                height = 2
            else:
                # Very far dots - very dim
                color = QColor(self.dim_color.red(), self.dim_color.green(), 
                             self.dim_color.blue(), 60)
                height = 1
            
            # Draw the dot
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(color))
            painter.drawRoundedRect(x, y, self.dot_width, height, 2, 2)

    def set_speed(self, speed_ms: int):
        """
        Set the animation speed.
        
        Args:
            speed_ms: Animation update interval in milliseconds
        """
        self.animation_speed = speed_ms
        if self.is_animating:
            self.timer.setInterval(speed_ms)

    def set_colors(self, active_color: str, dim_color: str = None, background_color: str = None):
        """
        Customize the animation colors.
        
        Args:
            active_color: Hex color for the active scanning dot
            dim_color: Hex color for dimmed dots (optional)
            background_color: Hex color for background (optional)
        """
        self.active_color = QColor(active_color)
        if dim_color:
            self.dim_color = QColor(dim_color)
        if background_color:
            self.background_color = QColor(background_color)
        
        if self.is_animating:
            self.update()


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