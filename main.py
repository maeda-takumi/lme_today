from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from bs4 import BeautifulSoup
import sqlite3
import time
import re
from urllib.parse import urljoin
from typing import Optional
from datetime import datetime
from message import initialize_message_table, scrape_messages

DB_PATH = "lstep_users.db"
BASE_URL = "https://step.lme.jp/"  # href が相対パスでも OK にする
DT_RE = re.compile(r"(\d{4}[./-]\d{2}[./-]\d{2})\s+(\d{2}:\d{2})(?::\d{2})?")


def normalize_new_message_date(raw: Optional[str]) -> Optional[str]:
    """
    一覧の新規メッセージ日付を YYYY-MM-DD に正規化する。
    """
    if raw is None:
        return None

    text = raw.strip()
    if not text:
        return None

    m = re.search(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})", text)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2))
        d = int(m.group(3))
        return f"{y:04d}-{mo:02d}-{d:02d}"

    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2))
        d = int(m.group(3))
        return f"{y:04d}-{mo:02d}-{d:02d}"

    return None

# -------------------------
# DB初期化（新規作成 + 既存DBのカラム追加にも対応）
# -------------------------
def ensure_users_columns(conn):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(users)")
    cols = {row[1] for row in cur.fetchall()}

    if "friend_registered_at" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN friend_registered_at TEXT")
        conn.commit()

    if "support" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN support TEXT")
        conn.commit()

    if "tags" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN tags TEXT")
        conn.commit()

    if "display_name" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
        conn.commit()

    if "friend_value" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN friend_value TEXT")
        conn.commit()

    if "new_message_date" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN new_message_date DATE")
        conn.commit()

def initialize_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            line_name TEXT,
            href TEXT,
            support TEXT,
            friend_registered_at TEXT,
            tags TEXT,
            display_name TEXT,
            friend_value TEXT,
            new_message_date DATE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_update_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            line_name TEXT,
            update_date TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    conn.commit()
    ensure_users_columns(conn)
    conn.close()

def clear_tables():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users")
    cursor.execute("DELETE FROM messages")
    conn.commit()
    conn.close()

def save_to_db(
    name,
    href,
    friend_registered_at=None,
    support=None,
    display_name=None,
    new_message_date=None,
):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    existing_id = None
    existing_values = None
    if href:
        cursor.execute(
            """
            SELECT id, line_name, friend_registered_at, support, display_name, new_message_date
            FROM users
            WHERE href = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (href,),
        )
        row = cursor.fetchone()
        if row:
            existing_id = row[0]
            existing_values = {
                "line_name": row[1],
                "friend_registered_at": row[2],
                "support": row[3],
                "display_name": row[4],
                "new_message_date": row[5],
            }

    if existing_id:
        has_change = (
            existing_values["line_name"] != name
            or existing_values["friend_registered_at"] != friend_registered_at
            or existing_values["support"] != support
            or existing_values["display_name"] != display_name
            or existing_values["new_message_date"] != new_message_date
        )
        cursor.execute(
            """
            UPDATE users
            SET line_name = ?, href = ?, friend_registered_at = ?, support = ?, display_name = ?, new_message_date = ?
            WHERE id = ?
            """,
            (name, href, friend_registered_at, support, display_name, new_message_date, existing_id)
        )
        if has_change:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                """
                INSERT INTO user_update_history (user_id, line_name, update_date)
                VALUES (?, ?, ?)
                """,
                (existing_id, name, now_str),
            )
    else:
        cursor.execute(
            """
            INSERT INTO users (line_name, href, friend_registered_at, support, display_name, new_message_date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, href, friend_registered_at, support, display_name, new_message_date)
        )

    conn.commit()
    conn.close()

# -------------------------
# 詳細ページ(href先)から「友だち追加日付」を取得（時刻込み）
# 添付画像の: table.tbl_info_df の「友だち追加日付」tdの隣のtd
# -------------------------
DT_RE = re.compile(r"(\d{4}[./-]\d{2}[./-]\d{2})\s+(\d{2}:\d{2})")

def _clean_display_name(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    cleaned = raw.replace('"', "").strip()
    return cleaned or None


def fetch_user_detail_info(driver, href, timeout=12, debug=False):
    detail_url = urljoin(BASE_URL, href)
    original_handle = driver.current_window_handle
    before_handles = set(driver.window_handles)

    driver.execute_script("window.open(arguments[0], '_blank');", detail_url)

    WebDriverWait(driver, timeout).until(
        lambda d: len(set(d.window_handles) - before_handles) == 1
    )
    new_handle = list(set(driver.window_handles) - before_handles)[0]
    driver.switch_to.window(new_handle)

    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.tbl_info_df"))
        )

        raw = None
        display_name = None

        display_name_elem = driver.find_elements(By.CSS_SELECTOR, "#show_real_info_custom div.title-bg")
        if display_name_elem:
            display_name = _clean_display_name(display_name_elem[0].text)
        else:
            soup = BeautifulSoup(driver.page_source, "html.parser")
            display_name_tag = soup.select_one("#show_real_info_custom div.title-bg")
            if display_name_tag:
                display_name = _clean_display_name(display_name_tag.get_text(" ", strip=True))

        # ✅ ラベル揺れ吸収：友だち追加「日付/日時」どちらもOK
        els = driver.find_elements(
            By.XPATH,
            "//table[contains(@class,'tbl_info_df')]"
            "//td[contains(normalize-space(.),'友だち追加')]/following-sibling::td[1]"
        )
        if els:
            raw = els[0].text.strip()
        else:
            # フォールバック（念のため）
            soup = BeautifulSoup(driver.page_source, "html.parser")
            table = soup.select_one("table.tbl_info_df")
            if table:
                td = table.find("td", string=lambda s: s and "友だち追加" in s)
                if td and td.find_next_sibling("td"):
                    raw = td.find_next_sibling("td").get_text(" ", strip=True)

        if debug and not raw:
            print("[DEBUG] label td not found url=", driver.current_url)

        if not raw:
            return {
                "friend_registered_at": None,
                "display_name": display_name,
            }

        m = DT_RE.search(raw)
        if not m:
            if debug:
                print("[DEBUG] datetime not matched url=", driver.current_url)
                print("[DEBUG] raw=", raw)
            return {
                "friend_registered_at": None,
                "display_name": display_name,
            }

        date_part = m.group(1).replace(".", "-").replace("/", "-")
        time_part = m.group(2)
        return {
            "friend_registered_at": f"{date_part} {time_part}",
            "display_name": display_name,
        }

    finally:
        driver.close()
        driver.switch_to.window(original_handle)

# -------------------------
# 一覧ページからユーザーとhrefを取り、詳細ページから日付を取って保存
# -------------------------
def scrape_current_page(driver):
    html = driver.page_source
    soup = BeautifulSoup(html, "html.parser")

    rows = soup.select("table tr")
    for row in rows:
        name_tag = row.select_one("a[href*='/basic/friendlist/my_page/']")
        if not name_tag:
            continue

        href = name_tag.get("href", "")
        name = name_tag.get_text(strip=True)

        friend_registered_at = None

        # 一覧の固定列から display_name を取得
        # 例画像ベース: 5列目(td index=4) が display_name
        tds = row.select("td")
        new_message_date = None
        if len(tds) >= 3:
            raw_new_message_date = tds[2].get_text(strip=True)
            if raw_new_message_date:
                new_message_date = normalize_new_message_date(raw_new_message_date)
        display_name = None
        if len(tds) >= 5:
            raw_display_name = tds[4].get_text(strip=True)
            if raw_display_name == "":
                display_name = None
            else:
                # "-" はそのまま保存
                display_name = raw_display_name

        print(
            f"{name}: {href} / friend_registered_at={friend_registered_at} "
            f"/ new_message_date={new_message_date} / display_name={display_name}"
        )
        save_to_db(
            name,
            href,
            friend_registered_at=friend_registered_at,
            display_name=display_name,
            new_message_date=new_message_date,
        )

        time.sleep(0.2)

def has_next_page(driver):
    try:
        next_button = driver.find_element(By.CSS_SELECTOR, ".glyphicon.glyphicon-menu-right")
        parent_li = next_button.find_element(By.XPATH, "./ancestor::li")
        class_attr = parent_li.get_attribute("class")
        return "disabled" not in class_attr
    except:
        return False

def go_to_next_page(driver):
    next_button = driver.find_element(By.CSS_SELECTOR, ".glyphicon.glyphicon-menu-right")
    next_button.click()
    time.sleep(2)

def scrape_user_list(driver):
    initialize_db()
    while True:
        scrape_current_page(driver)
        if has_next_page(driver):
            go_to_next_page(driver)
        else:
            break
    print("✅ 全ページのデータ取得が完了しました。")

# メイン処理
if __name__ == "__main__":
    options = Options()
    options.add_experimental_option("detach", True)
    driver = webdriver.Chrome(options=options)

    driver.get("https://step.lme.jp/")
    input("ログインが完了したら Enter を押してください → ")

    print("🟡 既存データはクリアせず、href基準で更新/新規追加します...")

    print("🟡 一覧を取得中...")
    scrape_user_list(driver)

    print("🟡 メッセージテーブルを初期化中...")
    initialize_message_table()

    print("🟡 メッセージを取得中...")
    scrape_messages(driver)

    print("🎉 全処理が完了しました！")
    driver.quit()
