# uploader.py
from ftplib import FTP_TLS, error_perm
from pathlib import Path
import socket
import time

DEFAULT_HOSTS = ["sv1108.star.ne.jp", "ss911157.stars.ne.jp"]
DEFAULT_PORT = 21

def _pwd(ftps: FTP_TLS) -> str:
    try:
        return ftps.pwd()
    except Exception:
        return "(pwd取得失敗)"

def _listdir(ftps: FTP_TLS) -> list[str]:
    items = []
    try:
        ftps.retrlines("LIST", items.append)
    except Exception as e:
        items.append(f"(LIST失敗: {e})")
    return items

def _ensure_dir_strict(ftps: FTP_TLS, remote_dir: str):
    """渡された remote_dir のみを使って遷移/作成（候補展開しない“厳格モード”）"""
    path = remote_dir.strip()
    if not path:
        return
    # 絶対パスなら一度ルートへ
    if path.startswith("/"):
        try:
            ftps.cwd("/")
        except Exception:
            pass
    parts = [p for p in path.strip("/").split("/") if p]
    for part in parts:
        try:
            ftps.cwd(part)
        except Exception:
            ftps.mkd(part)
            ftps.cwd(part)

def _walk_find(ftps: FTP_TLS, target_name: str, max_depth=6) -> list[str]:
    """ホーム直下から再帰的に探索して一致パスを返す（簡易版）"""
    found = []
    def _walk(cur_path:str, depth:int):
        if depth > max_depth: 
            return
        # 一覧を NLST で取得
        try:
            entries = ftps.nlst()
        except Exception:
            entries = []
        # まずファイル一致を見る
        if target_name in [e.split("/")[-1] for e in entries]:
            found.append(cur_path.rstrip("/") + "/" + target_name)
        # ディレクトリに降りる（. と .. を除外）
        for e in entries:
            name = e.split("/")[-1]
            if name in (".", ".."):
                continue
            # ディレクトリか判定（CWDできるかで判定）
            try:
                ftps.cwd(e)
                nxt = _pwd(ftps)
                _walk(nxt, depth+1)
                ftps.cwd(cur_path)  # 戻る
            except Exception:
                continue
    start = _pwd(ftps)
    _walk(start, 0)
    return found

def upload_db_ftps(
    user: str,
    password: str,
    hosts: list[str] = None,
    port: int = DEFAULT_PORT,
    remote_dir: str = "public_html/support/",  # ← 相対推奨
    remote_name: str = "lstep_users.db",
    local_file: str = "lstep_users.db",
    timeout: int = 60,
    verify_after_upload: bool = True,
    search_if_not_visible: bool = True,
) -> dict:
    """
    戻り値: デバッグ情報を辞書で返す（UIログにも表示可）
    """
    hosts = hosts or DEFAULT_HOSTS
    lf = Path(local_file)
    if not lf.exists():
        raise FileNotFoundError(f"ローカルに {local_file} が見つかりません。")

    debug = {"trials": []}
    last_err = None

    # ユーザー名候補（@host 付きも試す）
    user_candidates = [user]
    if "@" not in user:
        user_candidates.append(f"{user}@{hosts[0]}")

    for host in hosts:
        try:
            ip = socket.gethostbyname(host)
        except Exception as e:
            last_err = e
            debug["trials"].append({"host": host, "stage": "resolve", "ok": False, "error": str(e)})
            continue

        for u in user_candidates:
            trial = {"host": host, "ip": ip, "user": u}
            try:
                ftps = FTP_TLS(timeout=timeout)
                ftps.connect(host=host, port=port)
                ftps.login(user=u, passwd=password)
                ftps.prot_p()

                trial["login_pwd"] = _pwd(ftps)

                # remote_dir に厳格遷移（自動補完しない）
                _ensure_dir_strict(ftps, remote_dir)
                trial["target_pwd"] = _pwd(ftps)

                # 一時名でアップロード → rename
                tmp_name = remote_name + ".tmp"
                with lf.open("rb") as f:
                    ftps.storbinary(f"STOR " + tmp_name, f)
                try:
                    ftps.rename(tmp_name, remote_name)
                except error_perm:
                    try:
                        ftps.delete(remote_name)
                    except Exception:
                        pass
                    ftps.rename(tmp_name, remote_name)

                # 直後の一覧
                if verify_after_upload:
                    trial["post_list_pwd"] = _pwd(ftps)
                    trial["post_list"] = _listdir(ftps)

                # 同名が見えないときは探索
                if verify_after_upload:
                    visible = any(remote_name in line for line in trial["post_list"])
                    if not visible and search_if_not_visible:
                        # ホームに戻って探索
                        ftps.cwd(trial["login_pwd"])
                        found = _walk_find(ftps, remote_name, max_depth=6)
                        trial["search_results"] = found

                try:
                    ftps.quit()
                except Exception:
                    pass

                trial["ok"] = True
                debug["trials"].append(trial)
                debug["success"] = True
                return debug
            except Exception as e:
                last_err = e
                trial["ok"] = False
                trial["error"] = str(e)
                debug["trials"].append(trial)

    debug["success"] = False
    debug["error"] = f"全候補でアップロード失敗。最後のエラー: {last_err}"
    return debug
