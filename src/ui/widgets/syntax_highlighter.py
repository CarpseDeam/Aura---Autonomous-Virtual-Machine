from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont


class PythonSyntaxHighlighter(QSyntaxHighlighter):
    """
    A syntax highlighter for Python code, designed for a dark theme.
    """

    def __init__(self, parent=None):
        """Initializes the PythonSyntaxHighlighter."""
        super().__init__(parent)
        self.highlighting_rules = []

        # Keywords format (e.g., def, class, import)
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#FF79C6"))  # Pink
        keyword_format.setFontWeight(QFont.Weight.Bold)
        keywords = [
            "\\bdef\\b", "\\bclass\\b", "\\bimport\\b", "\\bfrom\\b", "\\bfor\\b",
            "\\bin\\b", "\\bif\\b", "\\belif\\b", "\\belse\\b", "\\bwhile\\b",
            "\\breturn\\b", "\\bself\\b", "\\b__init__\\b", "\\bwith\\b", "\\bas\\b",
            "\\bTrue\\b", "\\bFalse\\b", "\\bNone\\b", "\\btry\\b", "\\bexcept\\b",
            "\\bfinally\\b", "\\braise\\b", "\\bpass\\b", "\\bcontinue\\b", "\\bbreak\\b"
        ]
        self.highlighting_rules.extend([(QRegularExpression(pattern), keyword_format) for pattern in keywords])

        # Decorators format (e.g., @staticmethod)
        decorator_format = QTextCharFormat()
        decorator_format.setForeground(QColor("#50FA7B"))  # Green
        self.highlighting_rules.append((QRegularExpression("@.*\\b"), decorator_format))

        # Operators format
        operator_format = QTextCharFormat()
        operator_format.setForeground(QColor("#FF79C6"))  # Pink
        self.highlighting_rules.append((QRegularExpression("[=+\\-*<>!/|&%^]"), operator_format))

        # Braces
        brace_format = QTextCharFormat()
        brace_format.setForeground(QColor("#dcdcdc"))  # Light Grey
        self.highlighting_rules.append((QRegularExpression("[\\{\\}\\(\\)\\[\\]]"), brace_format))

        # Strings format (single and double quoted)
        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#F1FA8C"))  # Yellow
        self.highlighting_rules.append((QRegularExpression("\".*\""), string_format))
        self.highlighting_rules.append((QRegularExpression("'.*'"), string_format))

        # Comments format
        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("#6272A4"))  # Greyish blue
        comment_format.setFontItalic(True)
        self.highlighting_rules.append((QRegularExpression("#[^\n]*"), comment_format))

        # Numbers format
        number_format = QTextCharFormat()
        number_format.setForeground(QColor("#BD93F9"))  # Purple
        self.highlighting_rules.append((QRegularExpression("\\b[0-9]+\\.?[0-9]*\\b"), number_format))

    def highlightBlock(self, text: str):
        """
        Applies syntax highlighting to the given block of text.

        Args:
            text: The block of text to highlight.
        """
        for pattern, format_rule in self.highlighting_rules:
            expression = QRegularExpression(pattern)
            it = expression.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), format_rule)
