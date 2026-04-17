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
    
    QDateEdit, QTimeEdit, QGroupBox, QGridLayout
)
from PySide6.QtCore import Qt, Signal, QObject, Slot, QDate, QTime

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

LOGIN_PROFILE_DIR = os.path.join(os.getcwd(), ".chrome_profile", "lstep_login")


def create_chrome_options(detach: bool = False) -> Options:
    """ログインセッションを永続化する Chrome オプションを作成する。"""
    os.makedirs(LOGIN_PROFILE_DIR, exist_ok=True)
    options = Options()
    options.add_argument(f"--user-data-dir={LOGIN_PROFILE_DIR}")
    options.add_argument("--profile-directory=Default")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    if detach:
        options.add_experimental_option("detach", True)
    return options

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
    def __init__(self, title: str, instructions: str, proceed_text: str = "続行", parent=None):
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
        btns.button(QDialogButtonBox.Ok).setText(proceed_text)
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
    # (title, instructions, proceed_event, cancel_event, proceed_text)
    open_gate = Signal(str, str, object, object, str)

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
        options = create_chrome_options()
        driver = webdriver.Chrome(options=options)
        driver.get("https://step.lme.jp/")
        driver.get("https://step.lme.jp/")


        # ---- UIゲート（OKで続行 / キャンセルで中断）----
        proceed_event = threading.Event()
        cancel_event = threading.Event()
        instructions = (
            "1) ブラウザでLステップにログインしてください。\n"
            "2) 対象の『友達リスト』まで手動で移動してください。\n"
            "3) 画面が開けたら、このポップアップの［続行］を押してください。\n\n"
            "※［キャンセル］を押すと処理を中断します。"
        )
        logger.open_gate.emit("ログイン＆移動のお願い", instructions, proceed_event, cancel_event, "続行")

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

def run_polling(logger: UILogger, execute_time: QTime, stop_event: threading.Event):
    """指定時刻になったら当日分スクレイピングを毎日実行する。"""
    last_executed_date = None
    execute_time_text = execute_time.toString("HH:mm")
    logger.message.emit(f"🟢 ポーリング開始: 毎日 {execute_time_text} に当日分を取得します。")

    while not stop_event.is_set():
        now = datetime.now()
        today = now.date()
        current_time = QTime(now.hour, now.minute, now.second)

        should_run = (
            current_time >= execute_time
            and last_executed_date != today
        )
        if should_run:
            target_date = today.strftime("%Y-%m-%d")
            logger.message.emit(
                f"🟡 ポーリング実行時刻に到達: 当日({target_date})のデータ取得を開始します。"
            )
            run_scraping(logger, target_date=target_date)
            last_executed_date = today
            logger.message.emit("🟢 ポーリング待機に戻ります。")

        stop_event.wait(timeout=15)

    logger.message.emit("🛑 ポーリングを停止しました。")

def run_tag_scraping(logger: UILogger):
    driver = None
    try:
        logger.enable_ui.emit(False)
        logger.message.emit("🟡 初期化中…")
        initialize_db()
        logger.message.emit("🟡 既存データをクリアします（users）")
        # clear_tables(include_messages=False)

        logger.message.emit("🟡 ブラウザを起動します…")
        options = create_chrome_options()
        driver = webdriver.Chrome(options=options)
        driver.get("https://step.lme.jp/")
        driver.get("https://step.lme.jp/")

        # ---- UIゲート（OKで続行 / キャンセルで中断）----
        proceed_event = threading.Event()
        cancel_event = threading.Event()
        instructions = (
            "1) ブラウザでLステップにログインしてください。\n"
            "2) 対象の『友達リスト』まで手動で移動してください。\n"
            "3) 画面が開けたら、このポップアップの［続行］を押してください。\n\n"
            "※［キャンセル］を押すと処理を中断します。"
        )
        logger.open_gate.emit("ログイン＆移動のお願い", instructions, proceed_event, cancel_event, "続行")
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

def run_login_session_save(logger: UILogger):
    """手動ログイン後にセッションを保存する専用フロー。"""
    driver = None
    try:
        logger.enable_ui.emit(False)
        logger.message.emit("🟡 ログイン保存モードを開始します…")
        logger.message.emit(f"🟡 保存先プロファイル: {LOGIN_PROFILE_DIR}")

        options = create_chrome_options()
        driver = webdriver.Chrome(options=options)
        driver.get("https://step.lme.jp/")

        proceed_event = threading.Event()
        cancel_event = threading.Event()
        instructions = (
            "1) 開いたブラウザで手動ログインしてください。\n"
            "2) ログイン完了を確認してください。\n"
            "3) このポップアップで［ログイン情報を保存して終了］を押してください。\n\n"
            "※［キャンセル］を押すと保存せず終了します。"
        )
        logger.open_gate.emit(
            "ログイン情報保存",
            instructions,
            proceed_event,
            cancel_event,
            "ログイン情報を保存して終了",
        )

        while True:
            if proceed_event.wait(timeout=0.1):
                break
            if cancel_event.is_set():
                logger.message.emit("🛑 ログイン保存をキャンセルしました。")
                return

        logger.message.emit("✅ ログイン情報を保存しました。")
    except Exception as e:
        logger.message.emit(f"❌ ログイン保存中にエラー: {e}")
        logger.show_error.emit("ログイン保存エラー", f"{e}")
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
        self.polling_stop_event = None
        self.polling_thread = None
        self.polling_active = False
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setSpacing(14)

        # タイトル
        title = QLabel("LSTEP ユーティリティ")
        title.setObjectName("TitleLabel")
        root.addWidget(title)

        sub_title = QLabel("スクレイピング・アップロード・エクスポートを用途別にまとめました。")
        sub_title.setObjectName("SubTitleLabel")
        root.addWidget(sub_title)

        # カード：操作ボタン
        actions_card = QFrame()
        actions_card.setObjectName("Card")
        actions = QVBoxLayout(actions_card)
        actions.setSpacing(14)
        
        run_group = QGroupBox("データ取得")
        run_grid = QGridLayout(run_group)
        self.btn_scrape = QPushButton("スクレイピング実行")
        self.btn_scrape.clicked.connect(self.on_click_scrape)
        run_grid.addWidget(self.btn_scrape, 0, 0, 1, 2)

        self.date_input = QDateEdit()
        self.date_input.setDisplayFormat("yyyy-MM-dd")
        self.date_input.setCalendarPopup(True)
        self.date_input.setSpecialValueText("未指定（全期間）")
        self.date_input.setMinimumDate(QDate(2000, 1, 1))
        self.date_input.setDate(self.date_input.minimumDate())
        self.date_input.setToolTip("対象日を指定すると、その日のメッセージのみ取得します。未指定なら全期間を取得します。")
        run_grid.addWidget(QLabel("対象日"), 1, 0)
        run_grid.addWidget(self.date_input, 1, 1)

        self.btn_tag_scrape = QPushButton("タグ取得実行")
        self.btn_tag_scrape.clicked.connect(self.on_click_tag_scrape)
        run_grid.addWidget(self.btn_tag_scrape, 2, 0, 1, 2)
        
        self.btn_login_save = QPushButton("ログイン保存実行")
        self.btn_login_save.clicked.connect(self.on_click_login_save)
        run_grid.addWidget(self.btn_login_save, 3, 0, 1, 2)

        polling_group = QGroupBox("ポーリング")
        polling_grid = QGridLayout(polling_group)
        polling_grid.addWidget(QLabel("実行時刻"), 0, 0)
        self.polling_time_input = QTimeEdit()
        self.polling_time_input.setDisplayFormat("HH:mm")
        self.polling_time_input.setTime(QTime.currentTime())
        self.polling_time_input.setToolTip("毎日この時刻に当日分のスクレイピングを実行します。")
        polling_grid.addWidget(self.polling_time_input, 0, 1)

        self.btn_polling_start = QPushButton("ポーリング開始")
        self.btn_polling_start.clicked.connect(self.on_click_polling_start)
        polling_grid.addWidget(self.btn_polling_start, 1, 0)
        self.btn_polling_stop = QPushButton("ポーリング停止")
        self.btn_polling_stop.setObjectName("SecondaryButton")
        self.btn_polling_stop.clicked.connect(self.on_click_polling_stop)
        self.btn_polling_stop.setEnabled(False)
        polling_grid.addWidget(self.btn_polling_stop, 1, 1)

        self.polling_status_label = QLabel("停止中")
        self.polling_status_label.setObjectName("StatusIdle")
        polling_grid.addWidget(self.polling_status_label, 2, 0, 1, 2)

        maintenance_group = QGroupBox("メンテナンス")
        maintenance_row = QHBoxLayout(maintenance_group)
        self.btn_upload = QPushButton("サーバーアップロード実行")
        self.btn_upload.clicked.connect(self.on_click_upload)
        maintenance_row.addWidget(self.btn_upload)

        export_group = QGroupBox("出力")
        export_row = QHBoxLayout(export_group)
        self.btn_analysis = QPushButton("分析（別UI起動）")
        # self.btn_analysis.clicked.connect(self.on_click_analysis)
        # row3.addWidget(self.btn_analysis)

        # ▼ 追加：CSVエクスポートボタン
        self.btn_export = QPushButton("CSVエクスポート（users / messages）")
        self.btn_export.clicked.connect(self.on_click_export)
        export_row.addWidget(self.btn_export)

        actions.addWidget(run_group)
        actions.addWidget(polling_group)
        actions.addWidget(maintenance_group)
        actions.addWidget(export_group)
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
        self.btn_login_save.setEnabled(enabled)
        # self.btn_analysis.setEnabled(enabled)
        self.btn_export.setEnabled(enabled)   # ← 追加
        self.date_input.setEnabled(enabled)
        self.polling_time_input.setEnabled(enabled and not self.polling_active)
        self.btn_polling_start.setEnabled(enabled and not self.polling_active)
        self.btn_polling_stop.setEnabled(self.polling_active)

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
    @Slot(str, str, object, object, str)
    def on_open_gate(
        self,
        title: str,
        instructions: str,
        proceed_event: object,
        cancel_event: object,
        proceed_text: str = "続行",
    ):
        dlg = ContinueDialog(title, instructions, proceed_text, self)

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

    def on_click_polling_start(self):
        if self.polling_active:
            self.logger.message.emit("ℹ️ すでにポーリング実行中です。")
            return

        self.polling_stop_event = threading.Event()
        execute_time = self.polling_time_input.time()
        self.polling_thread = threading.Thread(
            target=run_polling,
            args=(self.logger, execute_time, self.polling_stop_event),
            daemon=True,
        )
        self.polling_active = True
        self.polling_status_label.setText(f"稼働中（毎日 {execute_time.toString('HH:mm')} 実行）")
        self.polling_status_label.setObjectName("StatusRunning")
        self.polling_status_label.style().unpolish(self.polling_status_label)
        self.polling_status_label.style().polish(self.polling_status_label)
        self.set_controls_enabled(True)
        self.polling_thread.start()

    def on_click_polling_stop(self):
        if not self.polling_active:
            return
        self.polling_stop_event.set()
        self.polling_active = False
        self.polling_status_label.setText("停止中")
        self.polling_status_label.setObjectName("StatusIdle")
        self.polling_status_label.style().unpolish(self.polling_status_label)
        self.polling_status_label.style().polish(self.polling_status_label)
        self.set_controls_enabled(True)
        
    def on_click_tag_scrape(self):
        t = threading.Thread(target=run_tag_scraping, args=(self.logger,), daemon=True)
        t.start()
        
    def on_click_upload(self):
        t = threading.Thread(target=self.run_upload, daemon=True)
        t.start()

    def on_click_login_save(self):
        t = threading.Thread(target=run_login_session_save, args=(self.logger,), daemon=True)
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
