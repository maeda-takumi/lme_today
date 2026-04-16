# analysis_pipeline.py
# pip install google-generativeai
import os, json, sqlite3, math, statistics, re
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional

import google.generativeai as genai
from gemini_settings import pick_model, get_api_key

# ===== 設定 =====
DB_PATH = "lstep_users.db"
OUT_DIR = Path("analysis_out")
OUT_DIR.mkdir(exist_ok=True)
MODEL_NAME = "gemini-1.5-pro"
CUSTOMER_SENDER = "you"   # DBのsender値（ユーザー側）
SUPPORT_SENDER  = "me"    # DBのsender値（サポート側）

SYSTEM_PROMPT = """あなたはカスタマーサポート品質のアナリストです。
与えられた会話ログ（必要に応じて短縮済み）と事前集計(レスポンス時間など)を読み、
以下を日本語でJSONで出力してください。

{
 "score_communication": 0-5,
 "score_timeliness": 0-5,
 "score_overall": 0-5,
 "summary": "全体所感（100-200字）",
 "improvements": ["改善提案1", "改善提案2", "改善提案3"],
 "notable_examples": [{"type":"good|bad","quote":"抜粋","reason":"評価理由"}]
}
"""

# ====== 共通処理 ======
def _slug(text: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", text.strip())
    return re.sub(r"_+", "_", s).strip("_") or "unknown"

def _parse_time(ts: Optional[str]) -> Optional[datetime]:
    if not ts: return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _compute_response_metrics(messages: List[Dict]) -> Dict:
    lat = []
    prev = None
    for m in messages:
        t = _parse_time(m["time"])
        if t is None: 
            continue
        if prev and prev["sender"] == CUSTOMER_SENDER and m["sender"] == SUPPORT_SENDER:
            lat.append((t - prev["time"]).total_seconds())
        prev = {"sender": m["sender"], "time": t}
    if not lat:
        return {"count": 0}
    lat_sorted = sorted(lat)
    def pct(p):
        if not lat_sorted: return None
        k = (len(lat_sorted)-1)*p
        a = math.floor(k); b = math.ceil(k)
        if a == b: return lat_sorted[int(k)]
        return lat_sorted[a] + (lat_sorted[b]-lat_sorted[a])*(k-a)
    return {
        "count": len(lat),
        "avg_sec": sum(lat)/len(lat),
        "median_sec": statistics.median(lat),
        "p90_sec": pct(0.90),
        "p95_sec": pct(0.95),
        "min_sec": min(lat),
        "max_sec": max(lat),
    }

def _truncate_for_llm(messages: List[Dict], max_chars=12000) -> str:
    # 末尾（最近）優先で連結
    out, total = [], 0
    for m in reversed(messages):
        line = f"[{m['time']}] {m['sender']}: {m['text']}\n"
        if total + len(line) > max_chars: break
        out.append(line); total += len(line)
    return "".join(reversed(out))

# ====== 1) supportで絞ってJSONL生成 ======
def build_dataset_for_support(support_name: str,
                              db_path: str = DB_PATH,
                              out_dir: Path = OUT_DIR) -> Tuple[Path, int]:
    out_file = out_dir / f"conversations_{_slug(support_name)}.jsonl"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # 指定supportのユーザーのみ
    cur.execute("""
        SELECT u.id as user_id, u.line_name, u.href, u.support,
               m.id as msg_id, m.sender, m.message, m.time_sent
        FROM users u
        LEFT JOIN messages m ON u.id = m.user_id
        WHERE u.support = ?
        ORDER BY u.id ASC, m.time_sent ASC, m.id ASC
    """, (support_name,))
    rows = cur.fetchall()
    conn.close()

    # ユーザー単位にまとめる
    convs: Dict[int, Dict] = {}
    for r in rows:
        uid = r["user_id"]
        if uid not in convs:
            convs[uid] = {
                "user_id": uid,
                "line_name": r["line_name"],
                "href": r["href"],
                "support": r["support"],
                "messages": []
            }
        if r["msg_id"] is not None:
            convs[uid]["messages"].append({
                "msg_id": r["msg_id"],
                "sender": r["sender"],
                "text": (r["message"] or "").strip(),
                "time": r["time_sent"],
            })
    conv_list = list(convs.values())

    with out_file.open("w", encoding="utf-8") as fw:
        for conv in conv_list:
            stats = _compute_response_metrics(conv["messages"])
            llm_text = _truncate_for_llm(conv["messages"])
            rec = {
                "user_id": conv["user_id"],
                "line_name": conv["line_name"],
                "href": conv["href"],
                "support": conv["support"],
                "message_count": len(conv["messages"]),
                "response_metrics": stats,
                "llm_text": llm_text,
                "messages": conv["messages"],  # 重ければ消してOK
            }
            fw.write(json.dumps(rec, ensure_ascii=False) + "\n")

    return out_file, len(conv_list)

# ====== 2) JSONLをGeminiに投げて評価レポート生成 ======
def analyze_with_gemini(input_jsonl: Path,
                        out_dir: Path = OUT_DIR) -> Tuple[Path, int]:

    api_key = get_api_key()
    genai.configure(api_key=api_key)
    model_name = pick_model()  # ← 無料枠モデルをここで確定
    model = genai.GenerativeModel(model_name)

    out_file = out_dir / (input_jsonl.stem + "_gemini_reports.jsonl")
    n = 0
    with input_jsonl.open("r", encoding="utf-8") as fr, out_file.open("w", encoding="utf-8") as fw:
        for line in fr:
            rec = json.loads(line)
            prompt = (
f"""{SYSTEM_PROMPT}

【担当者】{rec.get('support') or '(未割当)'} / 【ユーザー】{rec.get('line_name')}
【事前集計】{json.dumps(rec.get('response_metrics', {}), ensure_ascii=False)}
【会話ログ（短縮版）】
{rec.get('llm_text','')}
""")
            try:
                res = model.generate_content(prompt)
                report = res.text or ""
            except Exception as e:
                report = f"ERROR: {e}"

            out = {
                "user_id": rec.get("user_id"),
                "line_name": rec.get("line_name"),
                "support": rec.get("support"),
                "report": report,
            }
            fw.write(json.dumps(out, ensure_ascii=False) + "\n")
            n += 1
    return out_file, n
