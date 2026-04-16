# sheets_support.py
from __future__ import annotations
from typing import List, Optional, Tuple
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import os

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

def _service(credentials_path: Optional[str] = None):
    credentials_path = credentials_path or os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
    if not os.path.exists(credentials_path):
        raise FileNotFoundError(f"credentials.json が見つかりません: {credentials_path}")
    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)

def list_sheet_titles(spreadsheet_id: str, credentials_path: Optional[str] = None) -> List[str]:
    svc = _service(credentials_path)
    meta = svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = meta.get("sheets", [])
    return [s["properties"]["title"] for s in sheets if "properties" in s]

def _get_values(svc, spreadsheet_id: str, a1: str) -> List[List[str]]:
    return svc.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=a1).execute().get("values", [])

def get_support_members(
    spreadsheet_id: str,
    sheet_title: str = "サポート担当一覧",
    credentials_path: Optional[str] = None,
) -> Tuple[List[str], dict]:
    """
    指定シートの A列から担当者名一覧を返す。
    - まず 'サポート担当一覧'!A3:A（見出しが2行目の場合）を試行
    - ダメなら A2:A をフォールバック
    戻り値: (items, debug)
    """
    svc = _service(credentials_path)
    debug = {"tried": [], "sheet_titles": []}

    # シート名はシングルクォートで囲む（日本語/スペース/記号対策）
    quoted = f"'{sheet_title}'"

    for rng in ("A3:A", "A2:A"):
        a1 = f"{quoted}!{rng}"
        try:
            values = _get_values(svc, spreadsheet_id, a1)
            debug["tried"].append({"range": a1, "rows": len(values)})
            if values:
                items = []
                seen = set()
                for row in values:
                    name = (row[0] if row else "").strip()
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    items.append(name)
                return items, debug
        except Exception as e:
            debug["tried"].append({"range": a1, "error": str(e)})

    # ここまで来たらタブ名違いの可能性が高いので一覧を返す
    try:
        titles = list_sheet_titles(spreadsheet_id, credentials_path)
        debug["sheet_titles"] = titles
    except Exception as e:
        debug["sheet_titles_error"] = str(e)

    return [], debug
