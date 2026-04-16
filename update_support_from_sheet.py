# -*- coding: utf-8 -*-
"""
update_support_from_sheet.py
- Googleスプレッドシートの「ChatGPT」シート B7:F を読み込み
- B列（LINE名）と F列（サポート担当）でマッピングを作成
- lstep_users.db の users.support を更新（line_name が一致した行のみ）
使い方:
    python update_support_from_sheet.py
    # もしくは他コードから import して update_users_support(...) を呼ぶ
必要パッケージ:
    pip install google-auth google-api-python-client
前提:
    - カレントに credentials.json がある（または環境変数 GOOGLE_APPLICATION_CREDENTIALS で指定）
    - スプレッドシートがサービスアカウントに共有済み（閲覧可）
"""

from __future__ import annotations
import os
import sqlite3
from typing import Dict, Optional, Tuple, List

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ==== 設定（必要に応じて変更）====
SPREADSHEET_ID = "1mDccfeN9sR8OJdWLv6wPN0DzRr5Y5OfLSmrjjHOvMIs"
SHEET_TITLE    = "ChatGPT"
# ヘッダーは6行目、データは7行目〜
RANGE_A1       = f"'{SHEET_TITLE}'!B7:F"   # B=LINE名, F=サポート担当
DB_PATH        = "lstep_users.db"
CREDENTIALS    = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
# ==================================

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def _build_sheets_service(credentials_path: str):
    if not os.path.exists(credentials_path):
        raise FileNotFoundError(f"credentials.json が見つかりません: {credentials_path}")
    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def fetch_name_support_map(spreadsheet_id: str, range_a1: str) -> Dict[str, str]:
    """
    シートから B..F を取得し、B=LINE名、F=サポート担当 のマッピングを返す。
    空行・空値は除外。
    """
    svc = _build_sheets_service(CREDENTIALS)
    sheet = svc.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range_a1).execute()
    values: List[List[str]] = sheet.get("values", [])

    mapping: Dict[str, str] = {}
    for row in values:
        # B..F を想定（欠損があっても安全に取り出す）
        line_name = (row[0] if len(row) > 0 else "").strip()  # B
        support   = (row[4] if len(row) > 4 else "").strip()  # F
        if not line_name:
            continue
        if not support:
            # 値が空の行は更新対象にしない（DB側をそのまま維持）
            continue
        mapping[line_name] = support
    return mapping


def ensure_support_column(conn: sqlite3.Connection) -> None:
    """
    users テーブルに support カラムが無ければ追加（保険）
    """
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(users)")
    cols = [r[1] for r in cur.fetchall()]  # name 列
    if "support" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN support TEXT")
        conn.commit()


def update_users_support(
    db_path: str = DB_PATH,
    spreadsheet_id: str = SPREADSHEET_ID,
    range_a1: str = RANGE_A1,
) -> Tuple[int, int]:
    """
    users.support を更新する。
    戻り値: (一致して更新した件数, users の総件数)
    """
    mapping = fetch_name_support_map(spreadsheet_id, range_a1)

    conn = sqlite3.connect(db_path)
    try:
        ensure_support_column(conn)

        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        total_users = cur.fetchone()[0] or 0

        updated = 0
        # line_name 一致で更新
        for line_name, support in mapping.items():
            cur.execute("UPDATE users SET support = ? WHERE line_name = ?", (support, line_name))
            if cur.rowcount > 0:
                updated += cur.rowcount

        conn.commit()
        return updated, total_users
    finally:
        conn.close()


def main():
    try:
        print("🟡 スプレッドシートから担当者情報を取得中…")
        updated, total = update_users_support()
        print(f"✅ 更新完了: {updated} / {total} 件（line_name一致のみ更新）")
    except Exception as e:
        print(f"❌ エラー: {e}")


if __name__ == "__main__":
    main()
