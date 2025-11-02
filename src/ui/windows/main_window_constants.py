from __future__ import annotations

from typing import Any, Dict, Sequence

AURA_ASCII_BANNER = """
        ����ۻ �ۻ   �ۻ�����ۻ  ����ۻ
       ������ۻ�ۺ   �ۺ������ۻ������ۻ
       ������ۺ�ۺ   �ۺ������ɼ������ۺ
       ������ۺ�ۺ   �ۺ������ۻ������ۺ
       �ۺ  �ۺ�������ɼ�ۺ  �ۺ�ۺ  �ۺ
       �ͼ  �ͼ �����ͼ �ͼ  �ͼ�ͼ  �ͼ
      A U T O N O M O U S  V I R T U A L  M A C H I N E
    """

AURA_STYLESHEET = """
        QMainWindow, QWidget {
            background-color: #000000;
            color: #dcdcdc;
            font-family: "JetBrains Mono", "Courier New", Courier, monospace;
        }
        QLabel#aura_banner {
            color: #FFB74D;
            font-weight: bold;
            font-size: 10px;
            padding-bottom: 10px;
        }
        QLabel#auto_accept_label {
            color: #64B5F6;
            font-weight: bold;
            padding-left: 12px;
        }
        QLabel#token_usage_label {
            color: #66BB6A;
            font-weight: bold;
            padding-left: 12px;
        }
        QTextBrowser#chat_display, QTextEdit#chat_display {
            background-color: #000000;
            border-top: 1px solid #4a4a4a;
            border-bottom: none;
            color: #dcdcdc;
            font-size: 14px;
        }
        QTextEdit#chat_input {
            background-color: #2c2c2c;
            border: 1px solid #4a4a4a;
            color: #dcdcdc;
            font-size: 14px;
            padding: 8px;
            border-radius: 5px;
            max-height: 80px;
        }
        QTextEdit#chat_input:focus { border: 1px solid #4a4a4a; }
        QPushButton#top_bar_button {
            background-color: #2c2c2c;
            border: 1px solid #4a4a4a;
            color: #dcdcdc;
            font-size: 14px;
            font-weight: bold;
            padding: 8px 12px;
            border-radius: 5px;
            min-width: 150px;
        }
        QPushButton#top_bar_button:hover { background-color: #3a3a3a; }
    """

BOOT_SEQUENCE: Sequence[Dict[str, Any]] = (
    {"text": "[SYSTEM] AURA Command Deck Initialized"},
    {"text": "Status: READY"},
    {"text": "System: Online"},
    {"text": "Mode: Interactive"},
    {"text": "Enter your commands..."},
)
