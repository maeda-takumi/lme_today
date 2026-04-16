# ui_main.py
import sys
import sqlite3
import threading
import csv, os
import json
from datetime import datetime
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QPlainTextEdit, QMessageBox, QDialog, QDialogButtonBox, QTextEdit,
    QDateEdit
)
from PySide6.QtCore import Qt, Signal, QObject, Slot, QDate

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# 既存ロジック
from main import initialize_db, scrape_user_list
from message import initialize_message_table, scrape_messages
from tags import scrape_tags
# from tags import initialize_tag_table, scrape_tags

# スタイル
from style import app_stylesheet, apply_card_shadow
import threading
from uploader import upload_db_ftps               # ← 既存のFTPSアップローダ
import pprint
from update_support_from_sheet import main as update_support_sync_main
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def export_tables_to_csv(db_path: str = "lstep_users.db", out_dir: str = "exports") -> dict:
    """
    users と messages を CSV 出力（UTF-8 with BOM）する。
    戻り値: {"users": <path>, "messages": <path>}
    """
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_users = os.path.join(out_dir, f"users_{ts}.csv")
    out_messages = os.path.join(out_dir, f"messages_{ts}.csv")

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        # users
        cur.execute("SELECT * FROM users")
        cols_u = [d[0] for d in cur.description]
        rows_u = cur.fetchall()
        friend_value_idx = cols_u.index("friend_value") if "friend_value" in cols_u else None
        friend_value_labels = []
        friend_value_label_set = set()
        parsed_friend_values = []

        if friend_value_idx is not None:
            for row in rows_u:
                raw = row[friend_value_idx]
                parsed = {}
                if raw:
                    try:
                        json_obj = json.loads(raw)
                        if isinstance(json_obj, dict):
                            parsed = {str(k): v for k, v in json_obj.items()}
                    except (json.JSONDecodeError, TypeError):
                        parsed = {}

                parsed_friend_values.append(parsed)
                for label in parsed.keys():
                    if label not in friend_value_label_set:
                        friend_value_label_set.add(label)
                        friend_value_labels.append(label)

            cols_u_export = [c for c in cols_u if c != "friend_value"] + friend_value_labels
            rows_u_export = []
            for row, parsed in zip(rows_u, parsed_friend_values):
                base = [v for i, v in enumerate(row) if i != friend_value_idx]
                extra = [parsed.get(label, "") for label in friend_value_labels]
                rows_u_export.append(base + extra)
        else:
            cols_u_export = cols_u
            rows_u_export = rows_u

        with open(out_users, "w", encoding="utf-8-sig", newline="") as fw:
            w = csv.writer(fw)
            w.writerow(cols_u_export)
            w.writerows(rows_u_export)

        # messages
        cur.execute("SELECT * FROM messages")
        cols_m = [d[0] for d in cur.description]
        rows_m = cur.fetchall()
        with open(out_messages, "w", encoding="utf-8-sig", newline="") as fw:
            w = csv.writer(fw)
            w.writerow(cols_m)
            w.writerows(rows_m)

        return {"users": out_users, "messages": out_messages, "users_count": len(rows_u_export), "messages_count": len(rows_m)}
    finally:
        conn.close()

# ===================== モーダル：続行ゲート =====================
class ContinueDialog(QDialog):
    def __init__(self, title: str, instructions: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(520, 360)

        lay = QVBoxLayout(self)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("TitleLabel")
        lay.addWidget(title_lbl)

        card = QFrame(); card.setObjectName("Card")
        v = QVBoxLayout(card)
        tip = QLabel("以下の手順を完了したら［続行］を押してください。")
        v.addWidget(tip)

        inst = QTextEdit()
        inst.setReadOnly(True)
        inst.setPlainText(instructions)
        inst.setMinimumHeight(180)
        v.addWidget(inst)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("続行")
        btns.button(QDialogButtonBox.Cancel).setText("キャンセル")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

        lay.addWidget(card)

# ===================== ロガー/シグナル =====================
class UILogger(QObject):
    message = Signal(str)
    enable_ui = Signal(bool)
    show_info = Signal(str, str)
    show_error = Signal(str, str)
    # (title, instructions, proceed_event, cancel_event)
    open_gate = Signal(str, str, object, object)

# ===================== ユーティリティ =====================
def clear_tables(include_messages: bool = True):
    """users / messages テーブルの中身をクリア"""
    conn = sqlite3.connect("lstep_users.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM users")
    if include_messages:
        cur.execute("DELETE FROM messages")
    conn.commit()
    conn.close()

# ===================== スクレイピング処理（別スレッド） =====================
def run_scraping(logger: UILogger, target_date: str | None = None):
    driver = None
    try:
        logger.enable_ui.emit(False)
        logger.message.emit("🟡 初期化中…")
        initialize_db()
        initialize_message_table()

        logger.message.emit("🟡 既存データをクリアします（users / messages）")
        # clear_tables()

        logger.message.emit("🟡 ブラウザを起動します…")
        options = Options()
        options.add_experimental_option("detach", True)
        driver = webdriver.Chrome(options=options)
        driver.get("https://step.lme.jp/")
        driver.get("https://step.lme.jp/")

        # ▼▼▼ ログインフォーム自動入力（ボタン押下なし） ▼▼▼
        try:
            logger.message.emit("🟡 ログインID・パスワードを自動入力しています…")

            # ログインフォームの要素が出るまで待機
            wait = WebDriverWait(driver, 20)

            # id="email_login" の入力欄を取得
            login_id = wait.until(
                EC.presence_of_element_located((By.ID, "email_login"))
            )

            # id="password_login" の入力欄を取得
            login_pw = wait.until(
                EC.presence_of_element_located((By.ID, "password_login"))
            )

            # 値を入力（とりあえずダミー）
            login_id.clear()
            login_id.send_keys("miomama0605@gmail.com")

            login_pw.clear()
            login_pw.send_keys("20250606@Mio")
            # 値を入力（とりあえずダミー）
            # login_id.clear()
            # login_id.send_keys("systemsoufu9@gmail.com")

            # login_pw.clear()
            # login_pw.send_keys("Kannai555@")
            
            logger.message.emit("🟡 ID・パスワードの入力が完了しました。ログイン操作は手動で行ってください。")

        except Exception as e:
            logger.message.emit(f"⚠️ ログイン自動入力に失敗: {e}")

        # ▲▲▲ 自動入力ここまで ▲▲▲

        # ---- UIゲート（OKで続行 / キャンセルで中断）----
        proceed_event = threading.Event()
        cancel_event = threading.Event()
        instructions = (
            "1) ブラウザでLステップにログインしてください。\n"
            "2) 対象の『友達リスト』まで手動で移動してください。\n"
            "3) 画面が開けたら、このポップアップの［続行］を押してください。\n\n"
            "※［キャンセル］を押すと処理を中断します。"
        )
        logger.open_gate.emit("ログイン＆移動のお願い", instructions, proceed_event, cancel_event)

        # どちらかが押されるまで待つ（ポーリングで両方監視）
        while True:
            if proceed_event.wait(timeout=0.1):
                break
            if cancel_event.is_set():
                logger.message.emit("🛑 ユーザー操作によりキャンセルされました。")
                return  # finally へ

        logger.message.emit("🟡 一覧を取得中…")
        scrape_user_list(driver)

        if target_date:
            logger.message.emit(f"🟡 メッセージ取得を開始します（対象日: {target_date}）…")
        else:
            logger.message.emit("🟡 メッセージ取得を開始します（全期間）…")
        scrape_messages(driver, logger, target_date=target_date)

        logger.message.emit("🟢 スクレイピング完了。サポート担当の同期を開始します…")
        try:
            # スプレッドシート → users.support を更新（B列=LINE名、F列=担当者）
            update_support_sync_main()   # ← 添付の main() をそのまま実行
            logger.message.emit("✅ サポート担当の同期が完了しました。")
        except Exception as e:
            logger.message.emit(f"❌ サポート担当の同期に失敗: {e}")
            # 続行は可能なので、アプリは止めずにログだけ出す
            
        logger.message.emit("🎉 全処理が完了しました！")
    except Exception as e:
        logger.message.emit(f"❌ エラー: {e}")
        logger.show_error.emit("エラー", f"{e}")
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass
        logger.enable_ui.emit(True)

def run_tag_scraping(logger: UILogger):
    driver = None
    try:
        logger.enable_ui.emit(False)
        logger.message.emit("🟡 初期化中…")
        initialize_db()
        logger.message.emit("🟡 既存データをクリアします（users）")
        # clear_tables(include_messages=False)

        logger.message.emit("🟡 ブラウザを起動します…")
        options = Options()
        options.add_experimental_option("detach", True)
        driver = webdriver.Chrome(options=options)
        driver.get("https://step.lme.jp/")
        driver.get("https://step.lme.jp/")

        # ▼▼▼ ログインフォーム自動入力（ボタン押下なし） ▼▼▼
        try:
            logger.message.emit("🟡 ログインID・パスワードを自動入力しています…")

            # ログインフォームの要素が出るまで待機
            wait = WebDriverWait(driver, 20)

            # id="email_login" の入力欄を取得
            login_id = wait.until(
                EC.presence_of_element_located((By.ID, "email_login"))
            )

            # id="password_login" の入力欄を取得
            login_pw = wait.until(
                EC.presence_of_element_located((By.ID, "password_login"))
            )

            # 値を入力（とりあえずダミー）
            login_id.clear()
            login_id.send_keys("miomama0605@gmail.com")

            login_pw.clear()
            login_pw.send_keys("20250606@Mio")

            logger.message.emit("🟡 ID・パスワードの入力が完了しました。ログイン操作は手動で行ってください。")

        except Exception as e:
            logger.message.emit(f"⚠️ ログイン自動入力に失敗: {e}")

        # ▲▲▲ 自動入力ここまで ▲▲▲

        # ---- UIゲート（OKで続行 / キャンセルで中断）----
        proceed_event = threading.Event()
        cancel_event = threading.Event()
        instructions = (
            "1) ブラウザでLステップにログインしてください。\n"
            "2) 対象の『友達リスト』まで手動で移動してください。\n"
            "3) 画面が開けたら、このポップアップの［続行］を押してください。\n\n"
            "※［キャンセル］を押すと処理を中断します。"
        )
        logger.open_gate.emit("ログイン＆移動のお願い", instructions, proceed_event, cancel_event)

        # どちらかが押されるまで待つ（ポーリングで両方監視）
        while True:
            if proceed_event.wait(timeout=0.1):
                break
            if cancel_event.is_set():
                logger.message.emit("🛑 ユーザー操作によりキャンセルされました。")
                return  # finally へ

        logger.message.emit("🟡 一覧を取得中…")
        scrape_user_list(driver)

        logger.message.emit("🟡 タグ取得を開始します…")
        scrape_tags(driver, logger)

        logger.message.emit("🎉 タグ取得の処理が完了しました！")
    except Exception as e:
        logger.message.emit(f"❌ エラー: {e}")
        logger.show_error.emit("エラー", f"{e}")
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass
        logger.enable_ui.emit(True)

# ===================== メインウィンドウ =====================
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LSTEP ユーティリティ")
        self.setMinimumSize(720, 520)
        self.setStyleSheet(app_stylesheet())
        self.logger = UILogger()
        self.logger.message.connect(self.append_log)
        self.logger.enable_ui.connect(self.set_controls_enabled)
        
        self.analysis_window = None   # ← GC対策で保持
        self.logger.show_info.connect(self.on_show_info)
        self.logger.show_error.connect(self.on_show_error)
        self.logger.open_gate.connect(self.on_open_gate)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)

        # タイトル
        title = QLabel("LSTEP ユーティリティ")
        title.setObjectName("TitleLabel")
        root.addWidget(title)

        # カード：操作ボタン
        actions_card = QFrame()
        actions_card.setObjectName("Card")
        actions = QVBoxLayout(actions_card)

        row1 = QHBoxLayout()
        self.btn_scrape = QPushButton("スクレイピング実行")
        self.btn_scrape.clicked.connect(self.on_click_scrape)
        row1.addWidget(self.btn_scrape)

        self.date_input = QDateEdit()
        self.date_input.setDisplayFormat("yyyy-MM-dd")
        self.date_input.setCalendarPopup(True)
        self.date_input.setSpecialValueText("未指定（全期間）")
        self.date_input.setMinimumDate(QDate(2000, 1, 1))
        self.date_input.setDate(self.date_input.minimumDate())
        self.date_input.setToolTip("対象日を指定すると、その日のメッセージのみ取得します。未指定なら全期間を取得します。")
        row1.addWidget(QLabel("対象日"))
        row1.addWidget(self.date_input)

        self.btn_tag_scrape = QPushButton("タグ取得実行")
        self.btn_tag_scrape.clicked.connect(self.on_click_tag_scrape)
        row1.addWidget(self.btn_tag_scrape)
        
        row2 = QHBoxLayout()
        self.btn_upload = QPushButton("サーバーアップロード実行")
        self.btn_upload.clicked.connect(self.on_click_upload)
        row2.addWidget(self.btn_upload)

        row3 = QHBoxLayout()
        self.btn_analysis = QPushButton("分析（別UI起動）")
        # self.btn_analysis.clicked.connect(self.on_click_analysis)
        # row3.addWidget(self.btn_analysis)

        # ▼ 追加：CSVエクスポートボタン
        self.btn_export = QPushButton("CSVエクスポート（users / messages）")
        self.btn_export.clicked.connect(self.on_click_export)
        row3.addWidget(self.btn_export)

        actions.addLayout(row1)
        actions.addLayout(row2)
        actions.addLayout(row3)
        root.addWidget(actions_card)
        apply_card_shadow(actions_card)  # ← カードに影

        # カード：ログビュー（白背景＋濃い文字）
        log_card = QFrame()
        log_card.setObjectName("Card")
        log_layout = QVBoxLayout(log_card)
        log_label = QLabel("ログ")
        log_layout.addWidget(log_label)
        self.log = QPlainTextEdit()
        self.log.setObjectName("LogView")
        self.log.setReadOnly(True)
        log_layout.addWidget(self.log)
        root.addWidget(log_card)
        apply_card_shadow(log_card)  # ← カードに影

        root.addStretch(1)
    def run_upload(self):
        try:
            self.logger.enable_ui.emit(False)
            self.logger.message.emit("🟡 サーバーへアップロードを開始します…")
            debug = upload_db_ftps(
                user="ss911157",
                password="fmmrsumv",
                hosts=["ss911157.stars.ne.jp"],  # ← ホストはそのままでOK
                remote_dir="/totalappworks.com/public_html/support/",  # ← ★ここを変更
                remote_name="lstep_users.db",
                local_file="lstep_users.db",
            )

            # 成否で分岐表示
            if debug.get("success"):
                self.logger.message.emit("✅ アップロード完了（安全な置換方式）")
                self.logger.message.emit(pprint.pformat(debug, width=100))
                self.logger.show_info.emit("完了", "アップロードが完了しました。")
            else:
                self.logger.message.emit("❌ アップロード失敗（詳細は下記）")
                self.logger.message.emit(pprint.pformat(debug, width=100))
                self.logger.show_error.emit("アップロード失敗", debug.get("error", "原因不明"))
        except Exception as e:
            self.logger.message.emit(f"❌ 例外: {e}")
            self.logger.show_error.emit("アップロード失敗", f"{e}")
        finally:
            self.logger.enable_ui.emit(True)
    # ---------- UI slots ----------
    def set_controls_enabled(self, enabled: bool):
        self.btn_scrape.setEnabled(enabled)
        self.btn_tag_scrape.setEnabled(enabled)
        self.btn_upload.setEnabled(enabled)
        # self.btn_analysis.setEnabled(enabled)
        self.btn_export.setEnabled(enabled)   # ← 追加

    def append_log(self, text: str):
        self.log.appendPlainText(text)

    def run_export(self):
        try:
            self.logger.enable_ui.emit(False)
            self.logger.message.emit("🟡 CSVエクスポートを開始します…")
            result = export_tables_to_csv(db_path="lstep_users.db", out_dir="exports")
            self.logger.message.emit(f"✅ エクスポート完了: users={result['users_count']}件, messages={result['messages_count']}件")
            self.logger.message.emit(f"📄 保存先: {result['users']}\n📄 保存先: {result['messages']}")
            self.logger.show_info.emit("完了", f"CSVを出力しました。\n{result['users']}\n{result['messages']}")
        except Exception as e:
            self.logger.message.emit(f"❌ エクスポート失敗: {e}")
            self.logger.show_error.emit("エクスポート失敗", f"{e}")
        finally:
            self.logger.enable_ui.emit(True)

    def on_click_export(self):
        t = threading.Thread(target=self.run_export, daemon=True)
        t.start()

    @Slot(str, str)
    def on_show_info(self, title, text):
        QMessageBox.information(self, title, text)

    @Slot(str, str)
    def on_show_error(self, title, text):
        QMessageBox.critical(self, title, text)

    @Slot(str, str, object, object)
    def on_open_gate(self, title: str, instructions: str, proceed_event: object, cancel_event: object):
        dlg = ContinueDialog(title, instructions, self)
        dlg.setStyleSheet(app_stylesheet())
        res = dlg.exec()
        if res == QDialog.Accepted:
            proceed_event.set()
        else:
            cancel_event.set()             # ← キャンセルを明示
            self.set_controls_enabled(True)  # 念のため即座にUIを戻す

    # ---------- Actions ----------
    def on_click_scrape(self):
        selected_date = None
        if self.date_input.date() != self.date_input.minimumDate():
            selected_date = self.date_input.date().toString("yyyy-MM-dd")
        t = threading.Thread(target=run_scraping, args=(self.logger, selected_date), daemon=True)
        t.start()

    def on_click_tag_scrape(self):
        t = threading.Thread(target=run_tag_scraping, args=(self.logger,), daemon=True)
        t.start()
        
    def on_click_upload(self):
        t = threading.Thread(target=self.run_upload, daemon=True)
        t.start()

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SUP-ADMIN")
    app.setWindowIcon(QIcon("icons/icon.png"))  # exe化時は相対/同梱パスに合わせる
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
