# style.py
# 共通スタイル（QSS）とカラーパレット + カード影付与ヘルパー

from PySide6.QtWidgets import QGraphicsDropShadowEffect
from PySide6.QtGui import QColor

PRIMARY_WHITE = "#FFFFFF"
NEUTRAL_GRAY_LIGHT = "#F5F6F8"
NEUTRAL_TEXT = "#1A1F36"   # ログ等で背景白に乗る濃いテキスト色
ACCENT_BLUE = "#2979FF"
ACCENT_BLUE_DARK = "#1E5ED4"

CARD_RADIUS = 16

BASE_QSS = f"""
/* 全体 */
QWidget {{
    background: {NEUTRAL_GRAY_LIGHT};
    color: {NEUTRAL_TEXT};
    font-family: "Segoe UI", "Hiragino Kaku Gothic ProN", "Yu Gothic UI", sans-serif;
    font-size: 14px;
}}

/* タイトル */
#TitleLabel {{
    font-weight: 700;
    font-size: 18px;
}}

/* カード風コンテナ（枠線なし） */
.QFrame#Card {{
    background: {PRIMARY_WHITE};
    border: none;               /* ← 枠線なし */
    border-radius: {CARD_RADIUS}px;
    padding: 20px;
}}

/* すべてのボタンを面色ブルー（プライマリ）に統一 */
QPushButton {{
    background: {ACCENT_BLUE};
    color: white;
    border-radius: 10px;
    padding: 10px 14px;
    border: none;
}}
QPushButton:hover {{
    background: {ACCENT_BLUE_DARK};
}}
QPushButton:disabled {{
    background: #9BB8FF;
}}

/* ログ表示：背景白＋見やすい文字色 */
QPlainTextEdit#LogView {{
    background: {PRIMARY_WHITE};
    color: {NEUTRAL_TEXT};
    border-radius: 10px;
    padding: 10px;
    font-family: Consolas, "SFMono-Regular", Menlo, Monaco, monospace;
    min-height: 160px;
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
