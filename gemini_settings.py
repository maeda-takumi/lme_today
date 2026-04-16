# gemini_settings.py
import os

# ここで「無料で使いたいモデル」を既定にする
DEFAULT_MODEL = "gemini-1.5-flash"     # 無料枠向け
# さらに安価/軽量なら（必要に応じて）
# DEFAULT_MODEL = "gemini-1.5-flash-8b"

# 利用を許可するモデルのホワイトリスト
ALLOWED_MODELS = {
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    # 有料を使う場合にだけ下記をON
    # "gemini-1.5-pro",
}

def pick_model() -> str:
    return DEFAULT_MODEL

def get_api_key(raise_if_missing=True) -> str:
    key = "AIzaSyDiWuoMpkghSTD5fTrhdjsKzQuSZ3dsW7U"
    return key
