from __future__ import annotations

import sqlite3
import time
from typing import List

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


DB_PATH = "lstep_users.db"
BASE_URL = "https://step.lme.jp"


def update_user_tags(user_id: int, tags: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET tags = ? WHERE id = ?",
        (tags, user_id),
    )
    conn.commit()
    conn.close()


def _extract_tags_from_table(soup: BeautifulSoup) -> List[str]:
    table = soup.select_one("table#table_choose_tag")
    if not table:
        return []

    tags = []
    for row in table.select("tbody tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        tag = cells[1].get_text(" ", strip=True)
        if not tag:
            continue
        tags.append(tag)
    return tags


def _wait_for_tag_panel(driver, timeout: int = 10) -> None:
    selectors = [
        "table#table_choose_tag",
        "#tab-tag",
    ]

    def _has_any(drv):
        return any(drv.find_elements(By.CSS_SELECTOR, sel) for sel in selectors)

    try:
        WebDriverWait(driver, timeout).until(_has_any)
    except Exception:
        pass


def scrape_tags(driver, logger, base_url: str = BASE_URL):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, href FROM users ORDER BY id ASC")
    users = cursor.fetchall()
    conn.close()

    for user_id, href in users:
        logger.message.emit(f"🟡 ユーザーID {user_id} のタグを取得中…")
        try:
            driver.get(base_url + href)
        except Exception as e:
            logger.message.emit(f"⚠️ ページ遷移失敗: {e}")
            continue

        try:
            tag_tab = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "li[data-name='tag'], [data-name='tag']")
                )
            )
            tag_tab.click()
            time.sleep(0.5)
        except Exception as e:
            logger.message.emit(f"⚠️ タブクリック失敗: {e}")
            continue

        _wait_for_tag_panel(driver)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        tags = _extract_tags_from_table(soup)
        if not tags:
            logger.message.emit(f"ℹ️ ユーザーID {user_id} のタグが見つかりませんでした")
            continue

        tag_string = ",".join(tags)
        update_user_tags(user_id, tag_string)

        logger.message.emit(
            f"✅ ユーザーID {user_id} のタグ取得: {len(tags)}件"
        )

    logger.message.emit("🎉 タグ取得が完了しました！")