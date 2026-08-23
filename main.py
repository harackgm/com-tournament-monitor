import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup

# ==========================================
# 安全制御・環境変数設定
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")

DB_PATH = "tournaments.db"
TARGET_YEAR = 2026
TAG_URL = f"https://www.kanritsuriba.com/at/tag/areatournament{TARGET_YEAR}/"
MAX_NOTIFY_LIMIT = 5  # 大量通知ストッパー（5件を超えた場合は自動送信中止）
TIMEOUT_SEC = 20  # タイムアウト時間を20秒に延長


# ==========================================
# ネットワーク接続ヘパー（自動再試行機能）
# ==========================================
def fetch_url(url, retries=3):
    headers = {"User-Agent": "Mozilla/5.0"}
    for i in range(retries):
        try:
            res = requests.get(url, headers=headers, timeout=TIMEOUT_SEC)
            res.raise_for_status()
            return res
        except Exception as e:
            if i == retries - 1:
                raise e
            time.sleep(3)  # 3秒待って再試行


# ==========================================
# 1. データベース初期化
# ==========================================
def init_db():
    is_initial_setup = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS tournaments (
            url TEXT PRIMARY KEY,
            round_num TEXT,
            location TEXT,
            entry_datetime DATETIME,
            original_text TEXT,
            is_cancelled INTEGER,
            notified_1d INTEGER,
            notified_1h INTEGER,
            notified_10m INTEGER
        )
    """)
    conn.commit()
    return conn, is_initial_setup


# ==========================================
# 2. 大会情報の解析ロジック
# ==========================================
def parse_tournament_info(text, year):
    info = {
        "round_num": "不明",
        "location": "不明",
        "entry_datetime": None,
        "is_cancelled": 0,
    }

    # 大会名・開催場所の取得
    match_title = re.search(r"第(\d+)戦([^\s大会を]+)", text)
    if match_title:
        info["round_num"] = match_title.group(1)
        info["location"] = match_title.group(2)

    # エントリー開始日時の取得
    match_entry = re.search(
        r"(?:エントリー).*?(?:(\d{1,2})月(\d{1,2})日|(\d{1,2})/(\d{1,2})).*?(\d{1,2}):(\d{2})",
        text,
    )
    if match_entry:
        if match_entry.group(1):
            m, d = int(match_entry.group(1)), int(match_entry.group(2))
        else:
            m, d = int(match_entry.group(3)), int(match_entry.group(4))
        hh, mm = int(match_entry.group(5)), int(match_entry.group(6))

        entry_year = year - 1 if m >= 11 else year
        try:
            info["entry_datetime"] = datetime(entry_year, m, d, hh, mm)
        except ValueError:
            pass

    # 中止・見送り・変更の検知
    cancel_keywords = ["見送る", "中止", "延期", "開催を見送"]
    if any(kw in text for kw in cancel_keywords):
        info["is_cancelled"] = 1

    return info


# ==========================================
# 3. LINE Push Message 送信処理
# ==========================================
def send_line_message(text_message):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        print(f"[テスト出力（TOKEN未設定）]:\n{text_message}")
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text_message}],
    }

    try:
        response = requests.post(
            url, headers=headers, json=payload, timeout=TIMEOUT_SEC
        )
        response.raise_for_status()
        print("LINEメッセージ送信成功")
    except Exception as e:
        print(f"LINEメッセージ送信失敗: {e}")


# ==========================================
# 4. メイン監視処理
# ==========================================
def main():
    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 本番監視処理を開始します。"
    )
    conn, is_initial_setup = init_db()
    c = conn.cursor()

    if is_initial_setup:
        print(
            "【安全装置発動】DB初回作成のため、全件既読化処理を行います（LINE通知は送信しません）。"
        )

    notify_queue = []
    db_updates = []

    try:
        res = fetch_url(TAG_URL)
        soup = BeautifulSoup(res.text, "html.parser")

        links = soup.find_all("a", href=re.compile(r"/at/\d{4}_\d+/"))
        urls_to_check = list(
            set(
                [
                    (
                        a["href"]
                        if a["href"].startswith("http")
                        else f"https://www.kanritsuriba.com{a['href']}"
                    )
                    for a in links
                ]
            )
        )
    except Exception as e:
        print(f"一覧取得エラー（通信失敗）: {e}")
        return

    now = datetime.now()

    for url in urls_to_check:
        try:
            res_detail = fetch_url(url)
            soup_detail = BeautifulSoup(res_detail.text, "html.parser")

            content_area = (
                soup_detail.find("div", class_="entry-content") or soup_detail
            )
            text = content_area.get_text(strip=True)

            info = parse_tournament_info(text, TARGET_YEAR)
            entry_dt = info["entry_datetime"]

            c.execute("SELECT * FROM tournaments WHERE url = ?", (url,))
            row = c.fetchone()

            if not row:
                # 新規登録（初回セットアップ時は通知キューに入れない）
                c.execute(
                    """
                    INSERT INTO tournaments 
                    (url, round_num, location, entry_datetime, original_text, is_cancelled, notified_1d, notified_1h, notified_10m)
                    VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0)
                """,
                    (
                        url,
                        info["round_num"],
                        info["location"],
                        entry_dt,
                        text,
                        info["is_cancelled"],
                    ),
                )

                if not is_initial_setup and entry_dt:
                    notify_queue.append(
                        f"【新規大会情報】\n第{info['round_num']}戦 {info['location']}大会\nエントリー開始: {entry_dt.strftime('%m/%d %H:%M')}\nURL: {url}"
                    )
            else:
                (
                    db_url,
                    db_round,
                    db_loc,
                    db_entry_dt_str,
                    db_text,
                    db_cancelled,
                    n_1d,
                    n_1h,
                    n_10m,
                ) = row

                # 1. 急な変更・中止の検知
                if info["is_cancelled"] == 1 and db_cancelled == 0:
                    notify_queue.append(
                        f"🚨【開催中止・変更通知】\n第{db_round}戦 {db_loc}大会に中止・変更が発生しました。\nURL: {url}"
                    )
                    c.execute(
                        "UPDATE tournaments SET is_cancelled = 1, original_text = ? WHERE url = ?",
                        (text, url),
                    )
                    continue

                # 2. 定期時間通知判定
                if entry_dt and entry_dt > now and info["is_cancelled"] == 0:
                    time_diff = entry_dt - now

                    # 1日前通知 (24時間以内)
                    if (
                        timedelta(0) < time_diff <= timedelta(days=1)
                        and not n_1d
                    ):
                        notify_queue.append(
                            f"【明日エントリー開始】\n第{db_round}戦 {db_loc}大会\n時間: {entry_dt.strftime('%m/%d %H:%M')}\nURL: {url}"
                        )
                        db_updates.append(
                            (
                                "UPDATE tournaments SET notified_1d = 1 WHERE url = ?",
                                (url,),
                            )
                        )

                    # 1時間前通知
                    elif (
                        timedelta(0) < time_diff <= timedelta(hours=1)
                        and not n_1h
                    ):
                        notify_queue.append(
                            f"⏰【1時間前】\n第{db_round}戦 {db_loc}大会\n間もなくエントリー開始です。\nURL: {url}"
                        )
                        db_updates.append(
                            (
                                "UPDATE tournaments SET notified_1h = 1 WHERE url = ?",
                                (url,),
                            )
                        )

                    # 10分前通知
                    elif (
                        timedelta(0) < time_diff <= timedelta(minutes=10)
                        and not n_10m
                    ):
                        notify_queue.append(
                            f"🔥【10分前直前通知】\n第{db_round}戦 {db_loc}大会\nエントリー準備をしてください。\nURL: {url}"
                        )
                        db_updates.append(
                            (
                                "UPDATE tournaments SET notified_10m = 1 WHERE url = ?",
                                (url,),
                            )
                        )

        except Exception as e:
            print(f"詳細ページ解析失敗 ({url}): {e}")

    # ==========================================
    # 5. 大量通知ストッパーの安全制御
    # ==========================================
    if not is_initial_setup and notify_queue:
        if len(notify_queue) > MAX_NOTIFY_LIMIT:
            print(
                f"⚠️ 通知件数が制限({MAX_NOTIFY_LIMIT}件)を超えたため個別送信をスキップします。"
            )
            send_line_message(
                "⚠️【システム通知】多数の変更を検知したため大量送信を抑止しました。サイトをご確認ください。"
            )
        else:
            for msg in notify_queue:
                send_line_message(msg)

    # 状態の更新をDBに適用
    for query, params in db_updates:
        c.execute(query, params)

    conn.commit()
    conn.close()
    print("本番監視処理が正常に完了しました。")


if __name__ == "__main__":
    main()
