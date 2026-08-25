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
MAX_NOTIFY_LIMIT = 5  # 大量通知ストッパー
TIMEOUT_SEC = 15  # 通信タイムアウト時間

# --- 大会前日リマインドの通知時間帯指定 (前夜19時〜23時の間) ---
EVENT_1D_HOUR_START = 19
EVENT_1D_HOUR_END = 23

# --- 🌙 おやすみモード（深夜通知防止）設定 ---
NIGHT_MODE_START = 23  # 23時以降は通知を保留
NIGHT_MODE_END = 9     # 翌朝9時まで（8時59分まで）通知を保留

# 主要釣り場の座標マッピング（天気API用）
LOCATION_COORDS = {
    "サンクチュアリ": (35.15, 136.52),
    "浜名湖": (34.72, 137.60),
    "東山湖": (35.28, 138.95),
    "キングフィッシャー": (36.80, 140.02),
    "赤城山": (36.48, 139.18),
    "中之沢": (36.52, 139.18),
    "白州": (35.80, 138.31),
    "上浜": (39.51, 139.95),
}


# ==========================================
# 強化版 ネットワーク接続ヘルパー (自動リトライ＆ウェイト)
# ==========================================
def fetch_url(url, retries=5):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    for i in range(retries):
        try:
            res = requests.get(url, headers=headers, timeout=TIMEOUT_SEC)
            res.raise_for_status()
            return res
        except Exception as e:
            if i == retries - 1:
                print(f"⚠️ 接続失敗 (上限到達 {retries}回): {url} -> {e}")
                return None
            wait_time = (i + 1) * 3
            print(f"💡 リトライ待ち ({i+1}/{retries}回目, {wait_time}秒後): {url}")
            time.sleep(wait_time)


# ==========================================
# 天気自動取得＆アドバイス生成ロジック（Open-Meteo API）
# ==========================================
def get_weather_advice(location_name):
    lat, lon = 36.5, 139.8
    for name, coords in LOCATION_COORDS.items():
        if name in location_name:
            lat, lon = coords
            break

    try:
        api_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=weathercode,temperature_2m_max,precipitation_sum&timezone=Asia%2FTokyo"
        res = requests.get(api_url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            max_temp = data["daily"]["temperature_2m_max"][1]
            precip = data["daily"]["precipitation_sum"][1]
            w_code = data["daily"]["weathercode"][1]

            advice = ""
            if w_code in [51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99] or (precip > 1.0):
                advice = "🌧 雨の予報です。レインウェアと防水対策をお忘れなく！"
            elif max_temp >= 30:
                advice = f"☀️ 最高気温{int(max_temp)}℃の猛暑予報です。熱中症対策と水分補給を万全に！"
            elif max_temp <= 10:
                advice = f"❄️ 最高気温{int(max_temp)}℃の冷え込み予報です。十分な防寒対策をして挑みましょう！"
            else:
                advice = f"🌤 予想最高気温は{int(max_temp)}℃です。絶好のコンディションで大会に臨みましょう！"

            return f"{advice}\n🔥 日頃の練習の成果を発揮し、優勝を目指して全力を尽くしてください！応援しています！"
    except Exception:
        pass

    return "🎣 体調管理を万全にして大会に挑みましょう！日頃の練習成果を発揮して優勝目指してファイトです！"


# ==========================================
# 1. データベース初期化 (18カラム)
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("PRAGMA table_info(tournaments)")
    columns = c.fetchall()

    is_initial_setup = False
    if not columns or len(columns) < 18:
        is_initial_setup = True
        c.execute("DROP TABLE IF EXISTS tournaments")
        c.execute("""
            CREATE TABLE tournaments (
                url TEXT PRIMARY KEY,
                round_num TEXT,
                location TEXT,
                event_date TEXT,
                event_datetime DATETIME,
                entry_datetime DATETIME,
                entry_str TEXT,
                reception_time TEXT,
                fee TEXT,
                original_text TEXT,
                is_cancelled INTEGER,
                notified_new INTEGER,
                notified_1d INTEGER,
                notified_1h INTEGER,
                notified_15m INTEGER,
                notified_event_1d INTEGER,
                notified_just INTEGER,
                notified_after_24h INTEGER
            )
        """)
        conn.commit()

    return conn, is_initial_setup


# ==========================================
# 指定都道府県グループ別テーマカラー判定ロジック
# ==========================================
def get_theme_color(location_name):
    if any(kw in location_name for kw in ["栃木", "群馬", "キングフィッシャー", "上永野", "みどり", "なら山", "大芦", "増井", "宇都宮", "アメイズ", "中之沢", "赤城", "川場", "沼田", "宮城", "ベリーズ", "イワナ"]):
        return "#03A9F4"  # 🔵 ライトブルー
    elif any(kw in location_name for kw in ["千葉", "茨城", "ジョイバレー", "けんた", "千葉川すそ", "座間", "高萩", "エリアJ"]):
        return "#FF5722"  # 🟧 レッドオレンジ
    elif any(kw in location_name for kw in ["埼玉", "朝霞", "吉羽園", "しらこばと", "川越"]):
        return "#E91E63"  # 🩷 ローズピンク
    elif any(kw in location_name for kw in ["神奈川", "上浜", "王禅寺", "開成", "足柄", "ベリーパーク"]):
        return "#9C27B0"  # 🟪 ディープパープル
    elif any(kw in location_name for kw in ["東京", "浅川", "秋川"]):
        return "#3F51B5"  # 🔷 インディゴブルー
    elif any(kw in location_name for kw in ["静岡", "浜名湖", "東山湖", "すその", "柿田川"]):
        return "#FF9800"  # 🟠 オレンジ
    elif any(kw in location_name for kw in ["山梨", "長野", "白州", "シルフ", "竜華池", "鹿島槍"]):
        return "#4CAF50"  # 🟢 グリーン
    elif any(kw in location_name for kw in ["三重", "岐阜", "滋賀", "サンクチュアリ", "サンク", "瑞浪", "平谷", "醒井"]):
        return "#009688"  # 翡翠色/ティール
    else:
        return "#607D8B"  # 🩶 ブルーグレー


# ==========================================
# テキスト解析ヘルパー関数群
# ==========================================
def extract_event_date_info(text, year):
    match = re.search(r"(?:(\d{4})年)?\s*(\d{1,2})月(\d{1,2})日", text)
    if match:
        y = int(match.group(1)) if match.group(1) else year
        m = int(match.group(2))
        d = int(match.group(3))
        try:
            dt = datetime(y, m, d)
            w = ["月", "火", "水", "木", "金", "土", "日"][dt.weekday()]
            return dt, f"{y}年{m:02d}月{d:02d}日({w})"
        except ValueError:
            pass
    return None, "開催日未定"


def parse_entry_datetime(text, year):
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    pattern_strict = re.search(r"(?:インターネットエントリー|エントリー|受付|募集)[^\d\n]{0,50}?(?:(\d{1,2})月(\d{1,2})日|(\d{1,2})[/.-](\d{1,2}))[^\d\n]{0,30}?(\d{1,2}):(\d{2})", text)
    pattern = pattern_strict or re.search(r"(?:(\d{1,2})月(\d{1,2})日|(\d{1,2})[/.-](\d{1,2}))[^\d\n]{0,20}?(\d{1,2}):(\d{2})", text)

    if pattern:
        m = int(pattern.group(1) or pattern.group(3))
        d = int(pattern.group(2) or pattern.group(4))
        hh = int(pattern.group(5))
        mm = int(pattern.group(6))
        entry_year = year - 1 if m >= 11 else year
        try:
            dt = datetime(entry_year, m, d, hh, mm)
            w = weekdays[dt.weekday()]
            return dt, f"{m:02d}月{d:02d}日({w}) {hh:02d}:{mm:02d}"
        except ValueError:
            pass
    return None, "エントリー日時未定"


def extract_reception_time(text):
    match = re.search(r"(?:受付|受\s*付)[：:\s]*(\d{1,2}:\d{2}\s*[\~～-]\s*\d{1,2}:\d{2}|\d{1,2}:\d{2}\s*より|\d{1,2}:\d{2})", text)
    return match.group(1).strip() if match else "情報参照"


def extract_fee(text):
    match = re.search(r"(?:参加費用|参加費|費用)[：:\s]*([\d,]+円[^\n]*|\d+,\d+円|\d+円)", text)
    return match.group(1).strip() if match else "情報参照"


# ==========================================
# LINE Push Message (Flex Message カルーセル)
# ==========================================
def send_line_flex(
    header_title,
    round_num,
    location,
    event_date_str,
    entry_str,
    page_url,
    theme_color,
    extra_info=None,
):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        print(f"[テスト出力]: {header_title} 第{round_num}戦 {location}")
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }

    body_contents = [
        {
            "type": "text",
            "text": f"第{round_num}戦",
            "weight": "bold",
            "size": "xl",
            "color": "#333333",
        },
        {
            "type": "text",
            "text": f"{location}大会",
            "weight": "bold",
            "size": "md",
            "color": "#555555",
            "wrap": True,
        },
        {"type": "separator"},
        {
            "type": "box",
            "layout": "vertical",
            "spacing": "xs",
            "contents": [
                {"type": "text", "text": "📅 大会開催日", "size": "xs", "color": "#888888"},
                {"type": "text", "text": event_date_str, "size": "xl", "color": "#333333"},
            ],
        },
        {
            "type": "box",
            "layout": "vertical",
            "spacing": "xs",
            "contents": [
                {"type": "text", "text": "⏰ エントリー開始日時", "size": "xs", "color": "#888888"},
                {"type": "text", "text": entry_str, "size": "xl", "color": "#E53935"},
            ],
        },
    ]

    if extra_info:
        body_contents.append({"type": "separator"})
        body_contents.append({
            "type": "box",
            "layout": "vertical",
            "spacing": "xs",
            "contents": [
                {"type": "text", "text": f"📋 受付時間: {extra_info.get('reception', '情報参照')}", "size": "sm", "color": "#555555"},
                {"type": "text", "text": f"💰 参加費用: {extra_info.get('fee', '情報参照')}", "size": "sm", "color": "#555555"},
            ],
        })
        if "weather_advice" in extra_info:
            body_contents.append({"type": "separator"})
            body_contents.append({
                "type": "box",
                "layout": "vertical",
                "spacing": "xs",
                "contents": [
                    {"type": "text", "text": "🌤 明日の天候・応援", "size": "xs", "color": "#888888"},
                    {"type": "text", "text": extra_info["weather_advice"], "size": "sm", "color": "#333333", "wrap": True},
                ],
            })

    flex_payload = {
        "to": LINE_USER_ID,
        "messages": [
            {
                "type": "flex",
                "altText": f"【{header_title}】第{round_num}戦 {location}大会",
                "contents": {
                    "type": "carousel",
                    "contents": [
                        {
                            "type": "bubble",
                            "header": {
                                "type": "box",
                                "layout": "vertical",
                                "backgroundColor": theme_color,
                                "contents": [
                                    {"type": "text", "text": f"🎣 {header_title}", "color": "#FFFFFF", "weight": "bold", "size": "xs"}
                                ],
                            },
                            "body": {
                                "type": "box",
                                "layout": "vertical",
                                "spacing": "md",
                                "contents": body_contents,
                            },
                            "footer": {
                                "type": "box",
                                "layout": "vertical",
                                "contents": [
                                    {
                                        "type": "button",
                                        "action": {"type": "uri", "label": "🔗 詳細・エントリー", "uri": page_url},
                                        "style": "primary",
                                        "color": theme_color,
                                    }
                                ],
                            },
                        }
                    ],
                },
            }
        ],
    }

    try:
        response = requests.post(url, headers=headers, json=flex_payload, timeout=TIMEOUT_SEC)
        response.raise_for_status()
        print(f"LINE送信成功: {header_title} (第{round_num}戦)")
    except Exception as e:
        print(f"LINE送信エラー: {e}")


def send_simple_text(text_message):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        print(f"[システム通知]: {text_message}")
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": text_message}]}
    try:
        requests.post(url, headers=headers, json=payload, timeout=TIMEOUT_SEC)
    except Exception as e:
        print(f"システム通知送信エラー: {e}")


def fetch_page_text(url):
    res = fetch_url(url)
    if res and res.status_code == 200:
        try:
            soup = BeautifulSoup(res.text, "html.parser")
            content_area = soup.find("div", class_="entry-content") or soup
            return content_area.get_text(strip=True)
        except Exception:
            pass
    return ""


# ==========================================
# メイン監視処理
# ==========================================
def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 全自動監視処理（最適化版）を開始します。")
    conn, is_initial_setup = init_db()
    c = conn.cursor()

    now = datetime.now()
    current_year = now.year
    
    # 🌙 おやすみモード（深夜判定）
    is_night_mode = (now.hour >= NIGHT_MODE_START or now.hour < NIGHT_MODE_END)
    if is_night_mode:
        print("🌙 現在はおやすみモード（通知保留時間帯）です。一部の通知は朝9時まで保留されます。")

    # 🔄 自動年切り替えロジック（スマート最適化版）
    target_years = [current_year]
    if now.month <= 3:
        target_years.append(current_year - 1) # 1〜3月は前年シーズンのMASTERS等に対応
    if now.month >= 9:
        target_years.append(current_year + 1) # 9月以降は来年シーズンの準備に対応
    target_years = sorted(list(set(target_years)))

    urls_to_check = []
    
    for year in target_years:
        tag_url = f"https://www.kanritsuriba.com/at/tag/areatournament{year}/"
        res = fetch_url(tag_url)
        if not res:
            print(f"⚠️ {year}年度一覧ページの取得に失敗しました。")
            continue

        try:
            soup = BeautifulSoup(res.text, "html.parser")
            pattern = re.compile(rf"/at/{year}_\d+/")
            links = soup.find_all("a", href=pattern)
            for a in links:
                href = a["href"]
                full_url = href if href.startswith("http") else f"https://www.kanritsuriba.com{href}"
                urls_to_check.append(full_url)
        except Exception as e:
            print(f"一覧解析エラー({year}): {e}")

    urls_to_check = list(set(urls_to_check))

    notify_queue = []
    db_updates = []

    for url in urls_to_check:
        try:
            match_year = re.search(r"/at/(\d{4})_", url)
            url_year = int(match_year.group(1)) if match_year else current_year

            time.sleep(0.5)
            text_p1 = fetch_page_text(url)

            sub_url = url.rstrip("/") + "/2/"
            time.sleep(0.5)
            text_p2 = fetch_page_text(sub_url)

            combined_text = (text_p2 + " " + text_p1).strip()
            if not combined_text:
                continue

            match_title = re.search(r"第(\d+)戦([^\s大会を]+)", combined_text)
            round_num = match_title.group(1) if match_title else "不明"
            location = match_title.group(2) if match_title else "対象会場"

            event_dt, event_date_str = extract_event_date_info(combined_text, url_year)
            entry_dt, entry_str = parse_entry_datetime(text_p2, url_year)
            if not entry_dt:
                entry_dt, entry_str = parse_entry_datetime(text_p1, url_year)

            reception_time = extract_reception_time(combined_text)
            fee = extract_fee(combined_text)
            theme_color = get_theme_color(location)

            # "順延" が含まれる場合も即座に中止・変更（is_cancelled=1）として扱う
            cancel_keywords = ["見送る", "中止", "延期", "順延", "取りやめ", "開催を見送"]
            is_cancelled = 1 if any(kw in combined_text for kw in cancel_keywords) else 0

            c.execute("SELECT * FROM tournaments WHERE url = ?", (url,))
            row = c.fetchone()

            if not row:
                new_notified_flag = 0 if (is_night_mode and not is_initial_setup) else 1
                c.execute(
                    """
                    INSERT INTO tournaments 
                    (url, round_num, location, event_date, event_datetime, entry_datetime, entry_str, reception_time, fee, original_text, is_cancelled, notified_new, notified_1d, notified_1h, notified_15m, notified_event_1d, notified_just, notified_after_24h)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0)
                """,
                    (
                        url, round_num, location, event_date_str,
                        event_dt.strftime("%Y-%m-%d %H:%M:%S") if event_dt else None,
                        entry_dt.strftime("%Y-%m-%d %H:%M:%S") if entry_dt else None,
                        entry_str, reception_time, fee, combined_text, is_cancelled, new_notified_flag
                    ),
                )

                if not is_initial_setup and not is_night_mode:
                    notify_queue.append({
                        "header": "🆕【新規大会開催予定】", "round_num": round_num, "location": location,
                        "event_date_str": event_date_str, "entry_str": entry_str, "url": final_url, "theme_color": theme_color,
                    })
            else:
                (
                    db_url, db_round, db_loc, db_event_date, db_event_dt_str, db_entry_dt_str, db_entry_str,
                    db_reception, db_fee, db_text, db_cancelled, n_new, n_1d, n_1h, n_15m, n_event_1d, n_just, n_after_24h
                ) = row

                # 🌙 1. 保留されていた新規大会の通知
                if n_new == 0 and not is_night_mode:
                    notify_queue.append({
                        "header": "🆕【新規大会開催予定】", "round_num": db_round, "location": db_loc,
                        "event_date_str": event_date_str, "entry_str": entry_str, "url": final_url, "theme_color": theme_color,
                    })
                    c.execute("UPDATE tournaments SET notified_new = 1 WHERE url = ?", (url,))

                # 🚨 2. 緊急中止・変更通知
                if is_cancelled == 1 and db_cancelled == 0:
                    notify_queue.append({
                        "header": "🚨【緊急：開催中止・変更】", "round_num": db_round, "location": db_loc,
                        "event_date_str": event_date_str, "entry_str": "開催中止・変更が発生しました", "url": final_url, "theme_color": "#D32F2F",
                    })
                    c.execute("UPDATE tournaments SET is_cancelled = 1 WHERE url = ?", (url,))
                    continue

                # 🌙 3. 日時更新検知
                if db_event_date != event_date_str or db_entry_str != entry_str:
                    if not is_night_mode:
                        if not is_initial_setup and (db_event_date == "開催日未定" or db_entry_str == "エントリー日時未定"):
                            notify_queue.append({
                                "header": "📢【大会情報更新】", "round_num": db_round, "location": db_loc,
                                "event_date_str": event_date_str, "entry_str": entry_str, "url": final_url, "theme_color": theme_color,
                            })
                        c.execute(
                            "UPDATE tournaments SET event_date = ?, entry_datetime = ?, entry_str = ?, reception_time = ?, fee = ?, original_text = ? WHERE url = ?",
                            (event_date_str, entry_dt.strftime("%Y-%m-%d %H:%M:%S") if entry_dt else None, entry_str, reception_time, fee, combined_text, url),
                        )

                # 4. エントリー時刻に関するリマインド処理
                if entry_dt and is_cancelled == 0:
                    if entry_dt > now:
                        time_diff = entry_dt - now
                        # 🌙 本日/明日の通知
                        if timedelta(0) < time_diff <= timedelta(days=1) and not n_1d:
                            if not is_night_mode:
                                is_today = (entry_dt.date() == now.date())
                                header_text = "【本日エントリー開始】" if is_today else "【明日エントリー開始】"
                                notify_queue.append({"header": header_text, "round_num": db_round, "location": db_loc, "event_date_str": event_date_str, "entry_str": entry_str, "url": final_url, "theme_color": theme_color})
                                db_updates.append(("UPDATE tournaments SET notified_1d = 1 WHERE url = ?", (url,)))
                        
                        # 🚨 1時間前
                        elif timedelta(0) < time_diff <= timedelta(hours=1) and not n_1h:
                            notify_queue.append({"header": "⏰【1時間前リマインド】", "round_num": db_round, "location": db_loc, "event_date_str": event_date_str, "entry_str": entry_str, "url": final_url, "theme_color": theme_color})
                            db_updates.append(("UPDATE tournaments SET notified_1h = 1 WHERE url = ?", (url,)))
                        
                        # 🚨 15分前
                        elif timedelta(0) < time_diff <= timedelta(minutes=15) and not n_15m:
                            notify_queue.append({"header": "🔥【15分前直前リマインド】", "round_num": db_round, "location": db_loc, "event_date_str": event_date_str, "entry_str": entry_str, "url": final_url, "theme_color": theme_color})
                            db_updates.append(("UPDATE tournaments SET notified_15m = 1 WHERE url = ?", (url,)))
                    else:
                        passed_time = now - entry_dt
                        # 🚨 ちょうど開始
                        if timedelta(0) <= passed_time <= timedelta(minutes=15) and not n_just:
                            notify_queue.append({"header": "🏁【エントリー開始！】", "round_num": db_round, "location": db_loc, "event_date_str": event_date_str, "entry_str": entry_str, "url": final_url, "theme_color": theme_color})
                            db_updates.append(("UPDATE tournaments SET notified_just = 1 WHERE url = ?", (url,)))
                        
                        # 🌙 24時間後忘れ防止
                        elif timedelta(days=1) <= passed_time <= timedelta(days=2) and not n_after_24h:
                            if not is_night_mode:
                                notify_queue.append({"header": "⚠️【エントリー忘れ防止】昨日からエントリーが開始されています！", "round_num": db_round, "location": db_loc, "event_date_str": event_date_str, "entry_str": entry_str, "url": final_url, "theme_color": theme_color})
                                db_updates.append(("UPDATE tournaments SET notified_after_24h = 1 WHERE url = ?", (url,)))

                # 大会前日リマインド（前夜19:00〜23:00・天気＋応援文付き）
                if event_dt and is_cancelled == 0:
                    is_day_before = (now.date() == (event_dt.date() - timedelta(days=1)))
                    is_in_target_hours = (EVENT_1D_HOUR_START <= now.hour < EVENT_1D_HOUR_END)

                    if is_day_before and is_in_target_hours and not n_event_1d:
                        weather_advice = get_weather_advice(db_loc)
                        notify_queue.append({
                            "header": "📅【明日大会開催！直前案内】", "round_num": db_round, "location": db_loc,
                            "event_date_str": event_date_str, "entry_str": entry_str, "url": final_url, "theme_color": theme_color,
                            "extra_info": {"reception": reception_time, "fee": fee, "weather_advice": weather_advice},
                        })
                        db_updates.append(("UPDATE tournaments SET notified_event_1d = 1 WHERE url = ?", (url,)))

        except Exception as e:
            print(f"詳細解析エラー ({url}): {e}")

    # 大量通知ストッパー制御
    if not is_initial_setup and notify_queue:
        if len(notify_queue) > MAX_NOTIFY_LIMIT:
            print(f"⚠️ 通知件数が制限({MAX_NOTIFY_LIMIT}件)を超えたため連続送信をストップしました。")
            send_simple_text("⚠️【システム通知】多数の新着・更新を検知したため連続送信をストップしました。サイトをご確認ください。")
        else:
            for item in notify_queue:
                send_line_flex(
                    item["header"], item["round_num"], item["location"], item["event_date_str"], item["entry_str"],
                    item["url"], item["theme_color"], item.get("extra_info"),
                )

    for query, params in db_updates:
        c.execute(query, params)

    conn.commit()
    conn.close()
    print("全自動監視処理が正常完了しました。")


if __name__ == "__main__":
    main()
