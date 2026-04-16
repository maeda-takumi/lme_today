# ui_analysis.py
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QFrame, QPushButton, QHBoxLayout,
    QComboBox, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QObject, Slot, QThread
from style import app_stylesheet, apply_card_shadow
from sheets_support import get_support_members
from analysis_pipeline import build_dataset_for_support, analyze_with_gemini
from pathlib import Path
import os
# 先頭の import 群に追加
import json, re
from pathlib import Path
from PySide6.QtWidgets import QScrollArea, QListWidget, QListWidgetItem, QTextBrowser
# 先頭の import 群に追加
from PySide6.QtWidgets import QSizePolicy


SPREADSHEET_ID = "1mDccfeN9sR8OJdWLv6wPN0DzRr5Y5OfLSmrjjHOvMI"  # ← こちらは担当プルダウン用の正しいIDをセット

# 変更箇所だけ

class FetchWorker(QObject):
    finished = Signal(list, str)  # (items, err_message)

    def run(self):
        try:
            # ← ここでデバッグ情報も受け取り、失敗時に詳細を渡す
            items, dbg = get_support_members(
                "1mDccfeN9sR8OJdWLv6wPN0DzRr5Y5OfLSmrjjHOvMIs",
                sheet_title="サポート担当一覧",
            )
            if items:
                self.finished.emit(items, "")
            else:
                # 失敗時は試行したレンジと存在するタブ名をエラーに含める
                tried = "\n".join([str(t) for t in dbg.get("tried", [])])
                titles = ", ".join(dbg.get("sheet_titles", []))
                msg = "指定レンジが見つかりませんでした。\n" \
                      f"[tried]\n{tried}\n" \
                      f"[available sheet titles]\n{titles or '(取得失敗)'}"
                self.finished.emit([], msg)
        except Exception as e:
            self.finished.emit([], str(e))


class AnalysisWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("分析パネル")
        self.setMinimumSize(600, 460)
        self.setStyleSheet(app_stylesheet())
        self.last_reports: Path | None = None  # 直近のGemini結果パス
        self.last_jsonl: Path | None = None  # 直近に生成したデータ
        self._build()
        self._fetch_supports()

    def _build(self):
        root = QVBoxLayout(self)
        title = QLabel("分析ツール"); title.setObjectName("TitleLabel")
        root.addWidget(title)

        # --- カード：担当選択 + 操作 ---
        card = QFrame(); card.setObjectName("Card")
        cv = QVBoxLayout(card)

        row = QHBoxLayout()
        self.cmb_support = QComboBox(); self.cmb_support.setMinimumWidth(260)
        self.btn_reload = QPushButton("再読込"); self.btn_reload.clicked.connect(self._fetch_supports)
        row.addWidget(QLabel("サポート担当：")); row.addWidget(self.cmb_support); row.addStretch(1); row.addWidget(self.btn_reload)
        cv.addLayout(row)

        op = QHBoxLayout()
        self.btn_build = QPushButton("この担当のデータ生成（JSONL）")
        self.btn_build.clicked.connect(self.on_build_clicked)
        self.btn_gemini = QPushButton("Geminiで評価生成")
        self.btn_gemini.clicked.connect(self.on_gemini_clicked)
        self.btn_show = QPushButton("レポート一覧を表示")      # ← 追加
        self.btn_show.clicked.connect(self.on_show_reports)   # ← 追加
        op.addWidget(self.btn_build); op.addWidget(self.btn_gemini); op.addWidget(self.btn_show)
        cv.addLayout(op)

        root.addWidget(card); apply_card_shadow(card)

        # --- カード：レポート一覧（スクロール） ---
        self.report_area = QScrollArea()
        self.report_area.setWidgetResizable(True)
        self.report_container = QWidget()
        self.report_layout = QVBoxLayout(self.report_container)
        # AnalysisWindow._build() の「レポート一覧（スクロール）」作成後に追加
        self.report_layout.setContentsMargins(10, 10, 10, 10)  # ← ★カード群の外周余白
        self.report_layout.setSpacing(16)                      # ← ★カード間の縦間隔
        self.report_area.setMinimumHeight(420)                 # ← ★表示領域の最低高さ（任意）
        self.report_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.report_area.setWidget(self.report_container)

        list_card = QFrame(); list_card.setObjectName("Card")
        lc = QVBoxLayout(list_card)
        lc.addWidget(QLabel("レポート一覧"))
        lc.addWidget(self.report_area)
        root.addWidget(list_card); apply_card_shadow(list_card)

        root.addStretch(1)


    # ------- 取得系（省略：前回どおり） -------
    # ... get_support_members 呼び出し実装は省略（あなたの最新版のままでOK）

    # ------- 生成（DB→JSONL） -------
    def on_build_clicked(self):
        support = self.cmb_support.currentText().strip()
        if not support or "読み込み" in support or "取得失敗" in support:
            QMessageBox.warning(self, "未選択", "サポート担当を選択してください。")
            return
        try:
            jsonl_path, n = build_dataset_for_support(support)
            self.last_jsonl = jsonl_path
            QMessageBox.information(self, "生成完了", f"{support} の会話 {n} 件を\n{jsonl_path}\nに出力しました。")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"データ生成に失敗しました:\n{e}")

    # ------- Gemini（JSONL→レポート） -------
    def on_gemini_clicked(self):
        if not self.last_jsonl or not self.last_jsonl.exists():
            QMessageBox.warning(self, "データ未生成", "先に『この担当のデータ生成』を実行してください。")
            return
        # if not os.environ.get("GEMINI_API_KEY"):
        #     QMessageBox.warning(self, "APIキー未設定", "環境変数 GEMINI_API_KEY を設定してください。")
        #     return
        try:
            out_path, n = analyze_with_gemini(self.last_jsonl)
            self.last_reports = out_path     # ← 生成したJSONLを記憶
            QMessageBox.information(self, "評価完了", f"Geminiレポート {n} 件を\n{out_path}\nに出力しました。")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"Gemini分析に失敗しました:\n{e}")

    # ----- 分析ボタン（暫定） -----
    def _on_analyze_placeholder(self):
        name = self.cmb_support.currentText().strip()
        if not name:
            QMessageBox.warning(self, "未選択", "サポート担当を選んでください。")
            return
        QMessageBox.information(self, "分析（仮）", f"担当者: {name}\nここに分析処理を追加します。")

    # ----- スプシ取得 -----
    def _fetch_supports(self):
        self.cmb_support.clear()
        self.cmb_support.addItem("読み込み中…")
        self.cmb_support.setEnabled(False)
        self.btn_reload.setEnabled(False)

        self.th = QThread(self)         # スレッド保持（GC対策）
        self.worker = FetchWorker()
        self.worker.moveToThread(self.th)
        self.th.started.connect(self.worker.run)
        self.worker.finished.connect(self._on_fetch_finished)
        self.worker.finished.connect(self.th.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.th.finished.connect(self.th.deleteLater)
        self.th.start()

    def on_show_reports(self):
        # 直近のGemini出力を使用（無ければ担当名から推測）
        path = self.last_reports
        if not path or not Path(path).exists():
            # 直近担当名から推測: conversations_<担当>_gemini_reports.jsonl
            support = self.cmb_support.currentText().strip()
            guess = Path("analysis_out") / f"conversations_{support}_gemini_reports.jsonl"
            if guess.exists():
                path = guess
                self.last_reports = guess
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "未検出", "表示できるレポートファイルが見つかりません。先に『Geminiで評価生成』を実行してください。")
            return
        
    def on_show_reports(self):
        # 直近のGemini出力を使用（無ければ担当名から推測）
        path = self.last_reports
        if not path or not Path(path).exists():
            # 直近担当名から推測: conversations_<担当>_gemini_reports.jsonl
            support = self.cmb_support.currentText().strip()
            guess = Path("analysis_out") / f"conversations_{support}_gemini_reports.jsonl"
            if guess.exists():
                path = guess
                self.last_reports = guess
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "未検出", "表示できるレポートファイルが見つかりません。先に『Geminiで評価生成』を実行してください。")
            return

        # 一覧をクリア
        while self.report_layout.count():
            item = self.report_layout.takeAt(0)
            w = item.widget()
            if w: w.deleteLater()

        # 読み込み & カード描画
        count = 0
        with Path(path).open("r", encoding="utf-8") as fr:
            for line in fr:
                line = line.strip()
                if not line: continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                parsed = parse_gemini_report_text(rec.get("report",""))
                card_data = {
                    "line_name": rec.get("line_name"),
                    "support": rec.get("support"),
                    "score_comm": parsed.get("score_comm"),
                    "score_time": parsed.get("score_time"),
                    "score_overall": parsed.get("score_overall"),
                    "summary": parsed.get("summary"),
                    "improvements": parsed.get("improvements"),
                    "_raw": parsed.get("_raw"),
                }
                card = ReportCard(card_data, self.report_container)
                self.report_layout.addWidget(card)
                apply_card_shadow(card)
                count += 1

        self.report_layout.addStretch(1)
        QMessageBox.information(self, "表示完了", f"{count} 件のレポートを表示しました。")
    @Slot(list, str)
    def _on_fetch_finished(self, items, err):
        self.cmb_support.clear()
        if err:
            self.cmb_support.addItem("（取得失敗）")
            QMessageBox.critical(self, "取得エラー", f"担当者一覧を取得できませんでした。\n\n{err}")
        else:
            if not items:
                self.cmb_support.addItem("（データなし）")
            else:
                self.cmb_support.addItems(items)
        self.cmb_support.setEnabled(True)
        self.btn_reload.setEnabled(True)
# スタイル・影は既存を使用
def _score_chip(text: str, val: float | None) -> QLabel:
    chip = QLabel(text)
    chip.setAlignment(Qt.AlignCenter)
    chip.setFixedHeight(26)
    chip.setMinimumWidth(90)
    chip.setStyleSheet("""
        QLabel { border-radius: 13px; padding: 2px 10px; color: white; }
    """)
    # カラー判定
    if val is None:
        bg = "#9E9E9E"
        chip.setText(f"{text}: N/A")
    else:
        if val >= 4.0: bg = "#2E7D32"      # 良
        elif val >= 3.0: bg = "#F9A825"    # ふつう
        else: bg = "#C62828"               # 要改善
        chip.setText(f"{text}: {val:.2f}")
    chip.setStyleSheet(f"QLabel {{ border-radius: 13px; padding: 2px 10px; color: white; background:{bg}; }}")
    return chip

class ReportCard(QFrame):
    def __init__(self, record: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        
        self.setMinimumHeight(450)  # ← ★カードの最低高さを確保（好みに応じて 220〜280）
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)  # 横は広く、縦は必要分＋min

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 20)  # ← ★内側余白を広めに
        lay.setSpacing(10)  # ← セクション間のスペース

        # ヘッダ
        hdr = QHBoxLayout()
        title = QLabel(f"{record.get('line_name','(No name)')}  /  担当: {record.get('support') or '(未割当)'}")
        title.setObjectName("TitleLabel")
        hdr.addWidget(title)
        hdr.addStretch(1)
        # スコア
        sc = record.get("score_comm"); st = record.get("score_time"); so = record.get("score_overall")
        hdr.addWidget(_score_chip("コミュ", sc))
        hdr.addWidget(_score_chip("タイム", st))
        hdr.addWidget(_score_chip("総合", so))
        lay.addLayout(hdr)

        # サマリー
        summary = record.get("summary") or ""
        if summary:
            s = QLabel(summary)
            s.setWordWrap(True)
            lay.addWidget(s)

        # 改善案
        imps = record.get("improvements") or []
        if isinstance(imps, str): imps = [imps]
        if imps:
            lbl = QLabel("改善提案")
            lbl.setStyleSheet("font-weight:600;")
            lay.addWidget(lbl)
            ul = QVBoxLayout()
            for it in imps[:5]:
                li = QLabel(f"• {it}")
                li.setWordWrap(True)
                ul.addWidget(li)
            lay.addLayout(ul)

        # 展開用（生テキストを参考表示）
        raw = record.get("_raw")
        if raw:
            tb = QTextBrowser()
            tb.setOpenExternalLinks(True)
            tb.setMinimumHeight(100)  # ← ★折りたたみ領域が潰れないように
            tb.setHtml(f"<details><summary>全文を見る</summary><pre style='white-space:pre-wrap'>{raw}</pre></details>")
            lay.addWidget(tb)
        lay.addStretch(1)
def parse_gemini_report_text(txt: str) -> dict:
    # JSONならそのまま
    try:
        obj = json.loads(txt)
        return {
            "score_comm": obj.get("score_communication"),
            "score_time": obj.get("score_timeliness"),
            "score_overall": obj.get("score_overall"),
            "summary": obj.get("summary"),
            "improvements": obj.get("improvements"),
            "examples": obj.get("notable_examples"),
            "_raw": None,  # JSONなら原文は省略可（必要なら残す）
        }
    except Exception:
        # 正規表現で最低限を抽出
        sc = re.search(r"score_communication[^0-9]*([0-5](?:\.\d+)?)", txt)
        st = re.search(r"score_timeliness[^0-9]*([0-5](?:\.\d+)?)", txt)
        so = re.search(r"score_overall[^0-9]*([0-5](?:\.\d+)?)", txt)
        sm = re.search(r'"summary"\s*:\s*"([^"]+)"', txt)
        im = re.findall(r'"improvements"\s*:\s*\[(.*?)\]', txt, flags=re.S)
        imps = []
        if im:
            imps = re.findall(r'"([^"]+)"', im[0])
        return {
            "score_comm": float(sc.group(1)) if sc else None,
            "score_time": float(st.group(1)) if st else None,
            "score_overall": float(so.group(1)) if so else None,
            "summary": sm.group(1) if sm else None,
            "improvements": imps,
            "_raw": txt[:4000],  # 長すぎ防止
        }
