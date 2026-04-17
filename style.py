# style.py
# 共通スタイル（QSS）とカラーパレット + カード影付与ヘルパー

from PySide6.QtWidgets import QGraphicsDropShadowEffect
from PySide6.QtGui import QColor

PRIMARY_WHITE = "#FFFFFF"
NEUTRAL_GRAY_LIGHT = "#F5F6F8"
NEUTRAL_TEXT = "#1A1F36"
ACCENT_BLUE = "#2979FF"
ACCENT_BLUE_DARK = "#1E5ED4"
BORDER_GRAY = "#E2E7F0"

CARD_RADIUS = 16

BASE_QSS = f"""
QWidget {{
    background-color: {NEUTRAL_GRAY_LIGHT};
    color: {NEUTRAL_TEXT};
    font-family: "Segoe UI", "Hiragino Kaku Gothic ProN", "Yu Gothic UI", sans-serif;
    font-size: 14px;
}}

/* タイトル */
#TitleLabel {{
    font-weight: 700;
    font-size: 24px;
}}

#SubTitleLabel {{
    color: #5A6478;
    font-size: 13px;
}}

/* カード風コンテナ（枠線なし） */
.QFrame#Card {{
    background-color: {PRIMARY_WHITE};
    border: none;
    border-radius: {CARD_RADIUS}px;
    padding: 18px;
}}

QGroupBox {{
    border: 1px solid {BORDER_GRAY};
    border-radius: 12px;
    margin-top: 8px;
    padding: 14px 12px 10px 12px;
    background-color: #FCFDFF;
    font-weight: 600;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 6px;
}}

QDateEdit, QTimeEdit, QTextEdit, QPlainTextEdit {{
    background-color: {PRIMARY_WHITE};
    border: 1px solid {BORDER_GRAY};
    border-radius: 10px;
    padding: 8px;
}}

QLabel#StatusRunning {{
    background-color: #EAF9EF;
    background: #EAF9EF;
    border: 1px solid #B5E2C1;
    border-radius: 8px;
    padding: 6px 10px;
    font-weight: 600;
}}

QLabel#StatusIdle {{
    color: #5A6478;
    background: #F3F5F9;
    border: 1px solid #D5DCE8;
    border-radius: 8px;
    padding: 6px 10px;
}}

/* すべてのボタンを面色ブルー（プライマリ）に統一 */
QPushButton {{
    background-color: {ACCENT_BLUE};
    color: white;
    border-radius: 10px;
    padding: 10px 14px;
    border: none;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {ACCENT_BLUE_DARK};
}}
QPushButton:disabled {{
    background-color: #9BB8FF;
}}

QPushButton#SecondaryButton {{
    background-color: #6B7280;
}}
QPushButton#SecondaryButton:hover {{
    background-color: #4B5563;
}}
/* ログ表示：背景白＋見やすい文字色 */
QPlainTextEdit#LogView {{
    background-color: {PRIMARY_WHITE};
    color: {NEUTRAL_TEXT};
    border-radius: 10px;
    padding: 10px;
    font-family: Consolas, "SFMono-Regular", Menlo, Monaco, monospace;
    min-height: 200px;
}}
"""

def app_stylesheet() -> str:
    return BASE_QSS

def apply_card_shadow(widget, radius: int = 24, alpha: int = 30):
    """カード用のソフトなドロップシャドウを付与"""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(radius)
    effect.setOffset(0, 8)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
