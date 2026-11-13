import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

from helpers import (
    get_jst_now,
    fetch_url,
    fetch_page_data,
    is_youtube_video_available,
    get_weather_advice,
    get_theme_color,
    extract_landscape_image,
    extract_podium_image,
    extract_event_date_info,
    parse_entry_datetime,
    extract_reception_time,
    extract_fee,
    extract_tournament_results_from_html,
    get_winner_congratulations_message,
    extract_videos_from_html,
    extract_entry_conditions,
    send_line_flex,
    send_result_line_flex,
    send_video_line_flex,
)

# ==========================================
# 安全制御・環境変数設定
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")

# ★★★ テスト・本番 切り替えスイッチ ★★★
# True : 管理者(LINE_USER_ID)のみにPush送信（安全確認用）
# False: 登録者全員へ一斉送信（本番運用用）
IS_TEST_MODE = False

DB_PATH = "tournaments.db"
MAX_NOTIFY_LIMIT = 5  # 大量通知ストッパー（1回の巡回でこれを超えたら通知を抑止）
EVENT_1D_HOUR_START = 18
EVENT_1D_HOUR_END = 22
NIGHT_MODE_START = 23
NIGHT_MODE_END = 9

# ==========================================
# データベース初期化
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
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
                notified_after_24h INTEGER,
                notified_result INTEGER DEFAULT 0,
                notified_video_interview INTEGER DEFAULT 0,
                notified_video_final INTEGER DEFAULT 0,
                winner_name TEXT DEFAULT '',
                result_detected_at DATETIME
            )
        """)
        conn.commit()
    else:
        column_names = [col[1] for col in columns]
        if "notified_result" not in column_names:
            c.execute("ALTER TABLE tournaments ADD COLUMN notified_result INTEGER DEFAULT 0")
        if "notified_video_interview" not in column_names:
            c.execute("ALTER TABLE tournaments ADD COLUMN notified_video_interview INTEGER DEFAULT 0")
        if "notified_video_final" not in column_names:
            c.execute("ALTER TABLE tournaments ADD COLUMN notified_video_final INTEGER DEFAULT 0")
        if "winner_name" not in column_names:
            c.execute("ALTER TABLE tournaments ADD COLUMN winner_name TEXT DEFAULT ''")
        if "result_detected_at" not in column_names:
            c.execute("ALTER TABLE tournaments ADD COLUMN result_detected_at DATETIME")
        conn.commit()

    c.execute("""
        CREATE TABLE IF NOT EXISTS tournament_winners (
            url TEXT,
            round_num TEXT,
            rank TEXT,
            player_name TEXT,
            PRIMARY KEY (url, rank, player_name)
        )
    """)
    c.execute("CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("SELECT value FROM system_config WHERE key = 'system_update_v6'")
    if not c.fetchone():
        c.execute("INSERT INTO system_config (key, value) VALUES ('system_update_v6', '1')")
    conn.commit()
    return conn, is_initial_setup

# ==========================================
# メイン巡回処理
# ==========================================
def main():
    now = get_jst_now()
    mode_label = "【テストモード (管理者のみ)】" if IS_TEST_MODE else "【本番運用モード (登録者全員)】"
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 全自動監視処理を開始します。 {mode_label}")

    conn, is_initial_setup = init_db()
    c = conn.cursor()

    current_year = now.year
    is_night_mode = (now.hour >= NIGHT_MODE_START or now.hour < NIGHT_MODE_END)
    urls_to_check = []

    print("🔍 RSSフィードから最新記事を取得中...")
    rss_url = "https://www.kanritsuriba.com/at/feed/"
    res = fetch_url(rss_url)
    if res:
        try:
            root = ET.fromstring(res.content)
            for item in root.findall(".//item"):
                link = item.find("link")
                if link is not None and link.text:
                    url = link.text.strip()
                    if "/at/" in url:
                        urls_to_check.append(url)
        except Exception as e:
            print(f"⚠️ RSS解析エラー: {e}")

    c.execute("SELECT url FROM tournaments")
    for row in c.fetchall():
        urls_to_check.append(row[0])

    urls_to_check = list(set(urls_to_check))
    print(f"📊 チェック対象URL数: {len(urls_to_check)}件")

    notify_queue = []
    db_updates = []

    for url in urls_to_check:
        try:
            c.execute("SELECT notified_video_interview, notified_video_final, event_datetime, is_cancelled, result_detected_at FROM tournaments WHERE url = ?", (url,))
            check_row = c.fetchone()
            if check_row:
                n_video_int, n_video_final, event_dt_str, is_cancelled, res_det_at_str = check_row
                # 動画通知が両方完了しているか、大会中止の場合は監視終了
                if (n_video_int == 1 and n_video_final == 1) or is_cancelled == 1:
                    continue

                # 結果検知後90日経過しても動画が来ない場合は監視終了
                if res_det_at_str:
                    try:
                        res_dt = datetime.strptime(res_det_at_str, "%Y-%m-%d %H:%M:%S")
                        if (now - res_dt).days > 90:
                            continue
                    except Exception:
                        pass
                else:
                    match_year = re.search(r"/at/(\d{4})_", url)
                    url_year = int(match_year.group(1)) if match_year else current_year
                    if url_year < current_year - 1:
                        continue

            print(f"🔍 ページ解析中: {url}")
            match_year = re.search(r"/at/(\d{4})_", url)
            url_year = int(match_year.group(1)) if match_year else current_year

            # サーバー負荷軽減の待機
            time.sleep(1.0)
            text_p1, html_p1, title_p1, lines_p1 = fetch_page_data(url)
            sub_url = url.rstrip("/") + "/2/"
            time.sleep(1.0)
            text_p2, html_p2, title_p2, lines_p2 = fetch_page_data(sub_url)

            combined_text = (text_p2 + " " + text_p1).strip()
            combined_html = html_p2 + html_p1
            combined_lines = lines_p1 + lines_p2
            if not combined_text:
                continue

            conditions_dict = extract_entry_conditions(BeautifulSoup(combined_html, "html.parser"))
            main_image_url = extract_landscape_image(html_p1, html_p2)

            is_cc = "/cc" in url
            active_entry_idx = 1
            target_title = title_p1 or combined_text

            if is_cc:
                match_cc = re.search(r"cc(\d+)", url)
                round_num = str(int(match_cc.group(1))) if match_cc else "不明"

                loc_match = re.search(r"第\d+回(.*)", target_title)
                raw_loc = loc_match.group(1).strip() if loc_match else target_title
                location = re.sub(r"【.*?】", "", raw_loc)
                location = re.sub(r"は.*?(選手が優勝|が優勝).*", "", location).strip()

                event_dt, event_date_str = extract_event_date_info(combined_text, url_year)

                entry_dates_str_list = []
                entry_dt_objs = []
                for i in range(1, 4):
                    num_char = {1: "[1１一]", 2: "[2２二]", 3: "[3３三]"}[i]
                    found_dt = None
                    for line in combined_lines:
                        if re.search(rf"{num_char}次", line):
                            dt_match = re.search(r"(\d{1,2})月(\d{1,2})日", line)
                            if dt_match:
                                time_match = re.search(r"([0-2]?[0-9])[:時](\d{2})?", line)
                                m = int(dt_match.group(1))
                                d = int(dt_match.group(2))
                                hh = int(time_match.group(1)) if time_match else 20
                                mm = int(time_match.group(2)) if (time_match and time_match.group(2)) else 0
                                found_dt = (m, d, hh, mm)
                                break

                    if found_dt:
                        m, d, hh, mm = found_dt
                        if event_dt and m > event_dt.month:
                            entry_year = event_dt.year - 1
                        else:
                            entry_year = url_year
                        try:
                            dt = datetime(entry_year, m, d, hh, mm)
                            w = ["月", "火", "水", "木", "金", "土", "日"][dt.weekday()]
                            entry_dates_str_list.append(f"{i}次: {m:02d}/{d:02d}({w}) {hh:02d}:{mm:02d}")
                            entry_dt_objs.append(dt)
                        except ValueError:
                            pass

                if entry_dates_str_list:
                    entry_str = "\n".join(entry_dates_str_list)
                    active_entry_dt = None
                    for idx, dt in enumerate(entry_dt_objs):
                        if dt + timedelta(days=1) > now:
                            active_entry_dt = dt
                            active_entry_idx = idx + 1
                            break
                    if not active_entry_dt and entry_dt_objs:
                        active_entry_dt = entry_dt_objs[-1]
                        active_entry_idx = len(entry_dt_objs)

                    entry_dt = active_entry_dt
                else:
                    entry_dt, entry_str = parse_entry_datetime(text_p2, url_year, event_dt, round_num)
                    if not entry_dt:
                        entry_dt, entry_str = parse_entry_datetime(text_p1, url_year, event_dt, round_num)
            else:
                match_round = re.search(r"第(\d+)戦", target_title) or re.search(r"第(\d+)戦", combined_text)
                round_num = match_round.group(1) if match_round else "不明"

                loc_match = re.search(r"第\d+戦([^\s【\[大会]+)", target_title) or re.search(r"第\d+戦([^\s【\[大会]+)", combined_text)
                location = loc_match.group(1).strip() if loc_match else "対象会場"

                event_dt, event_date_str = extract_event_date_info(combined_text, url_year)
                entry_dt, entry_str = parse_entry_datetime(text_p2, url_year, event_dt, round_num)
                if not entry_dt:
                    entry_dt, entry_str = parse_entry_datetime(text_p1, url_year, event_dt, round_num)

            reception_time = extract_reception_time(combined_text)
            fee = extract_fee(combined_text)
            theme_color = get_theme_color(location)
            cancel_keywords = ["見送る", "中止", "延期", "順延", "取りやめ", "開催を見送", "開催中止"]
            is_cancelled = 1 if any(kw in combined_text for kw in cancel_keywords) else 0

            extra_info_dict = {"reception": reception_time, "fee": fee}
            if is_cc and active_entry_idx in conditions_dict:
                extra_info_dict["entry_condition"] = f"[{active_entry_idx}次対象者]\n{conditions_dict[active_entry_idx]}"

            results_data = extract_tournament_results_from_html(combined_html)
            videos_data = extract_videos_from_html(combined_html, url_year)

            winner_name = ""
            for r in results_data:
                if r['rank'] == "優勝":
                    winner_name = r['name']
                    r['congrat_msg'] = get_winner_congratulations_message(c, winner_name, round_num)
                if r['rank'] != "ラーメン賞":
                    c.execute("INSERT OR REPLACE INTO tournament_winners (url, round_num, rank, player_name) VALUES (?, ?, ?, ?)", (url, round_num, r['rank'], r['name']))

            c.execute("SELECT * FROM tournaments WHERE url = ?", (url,))
            row = c.fetchone()

            if not row:
                new_notified_flag = 0 if (is_night_mode and not is_initial_setup) else 1
                c.execute(
                    """INSERT INTO tournaments 
                    (url, round_num, location, event_date, event_datetime, entry_datetime, entry_str, reception_time, fee, original_text, is_cancelled, notified_new, notified_1d, notified_1h, notified_15m, notified_event_1d, notified_just, notified_after_24h, notified_result, notified_video_interview, notified_video_final, winner_name, result_detected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, ?, NULL)""",
                    (url, round_num, location, event_date_str, event_dt.strftime("%Y-%m-%d %H:%M:%S") if event_dt else None, entry_dt.strftime("%Y-%m-%d %H:%M:%S") if entry_dt else None, entry_str, reception_time, fee, combined_text, is_cancelled, new_notified_flag, winner_name)
                )
                if not is_initial_setup and not is_night_mode:
                    notify_queue.append({"type": "info", "header": "🆕【新規大会開催予定】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": entry_str, "url": url, "theme_color": theme_color, "extra_info": extra_info_dict, "main_image_url": main_image_url})
            else:
                (db_url, db_round, db_loc, db_event_date, db_event_dt_str, db_entry_dt_str, db_entry_str, db_reception, db_fee, db_text, db_cancelled, n_new, n_1d, n_1h, n_15m, n_event_1d, n_just, n_after_24h, n_result, n_video_int, n_video_fin, db_winner, db_result_detected_at) = row

                current_entry_dt_str = entry_dt.strftime("%Y-%m-%d %H:%M:%S") if entry_dt else None
                if db_entry_dt_str and current_entry_dt_str and db_entry_dt_str != current_entry_dt_str:
                    n_1d, n_1h, n_15m, n_just, n_after_24h = 0, 0, 0, 0, 0
                    db_updates.append(("UPDATE tournaments SET notified_1d=0, notified_1h=0, notified_15m=0, notified_just=0, notified_after_24h=0 WHERE url=?", (url,)))

                if winner_name and db_winner != winner_name:
                    c.execute("UPDATE tournaments SET winner_name = ? WHERE url = ?", (winner_name, url))

                is_final_available = False
                if "final" in videos_data:
                    if n_video_fin == 1:
                        is_final_available = True
                    else:
                        video = videos_data["final"]
                        pub_dt = video.get("publish_dt")
                        if pub_dt:
                            if pub_dt <= now:
                                is_final_available = True
                                if not is_night_mode:
                                    notify_queue.append({"type": "video", "header": "🎥【決勝戦 動画公開】", "round_num": round_num, "location": location, "video_data": video, "url": url, "theme_color": theme_color, "main_image_url": main_image_url})
                                    db_updates.append(("UPDATE tournaments SET notified_video_final = 1 WHERE url = ?", (url,)))
                        else:
                            if is_youtube_video_available(video["url"]):
                                is_final_available = True
                                if not is_night_mode:
                                    notify_queue.append({"type": "video", "header": "🎥【決勝戦 動画公開】", "round_num": round_num, "location": location, "video_data": video, "url": url, "theme_color": theme_color, "main_image_url": main_image_url})
                                    db_updates.append(("UPDATE tournaments SET notified_video_final = 1 WHERE url = ?", (url,)))

                if "interview" in videos_data and n_video_int == 0:
                    video = videos_data["interview"]
                    pub_dt = video.get("publish_dt")
                    if pub_dt:
                        if pub_dt <= now:
                            if not is_night_mode:
                                notify_queue.append({"type": "video", "header": "🎤【優勝者インタビュー公開】", "round_num": round_num, "location": location, "video_data": video, "url": url, "theme_color": theme_color, "main_image_url": main_image_url})
                                db_updates.append(("UPDATE tournaments SET notified_video_interview = 1 WHERE url = ?", (url,)))
                    else:
                        if is_youtube_video_available(video["url"]):
                            if not is_night_mode:
                                notify_queue.append({"type": "video", "header": "🎤【優勝者インタビュー公開】", "round_num": round_num, "location": location, "video_data": video, "url": url, "theme_color": theme_color, "main_image_url": main_image_url})
                                db_updates.append(("UPDATE tournaments SET notified_video_interview = 1 WHERE url = ?", (url,)))

                if results_data:
                    main_results = [r for r in results_data if r['rank'] in ['優勝', '２位', '３位']]

                    if n_result == 0:
                        db_result_detected_at = now.strftime("%Y-%m-%d %H:%M:%S")
                        db_updates.append(("UPDATE tournaments SET result_detected_at = ?, notified_result = 1 WHERE url = ?", (db_result_detected_at, url)))

                        results_to_send_step1 = [r for r in results_data if r['rank'] != "ラーメン賞" or r.get('image_url')]
                        if not is_night_mode:
                            notify_queue.append({"type": "result", "header": "📣【大会結果 速報！】", "round_num": round_num, "location": location, "results": results_to_send_step1, "url": url, "theme_color": theme_color})

                    elif n_result == 1:
                        if not db_result_detected_at:
                            db_result_detected_at = now.strftime("%Y-%m-%d %H:%M:%S")
                            db_updates.append(("UPDATE tournaments SET result_detected_at = ? WHERE url = ?", (db_result_detected_at, url)))

                        detected_dt = datetime.strptime(db_result_detected_at, "%Y-%m-%d %H:%M:%S")
                        hours_since_detected = (now - detected_dt).total_seconds() / 3600

                        is_images_complete = len(main_results) > 0 and all(r.get('image_url') for r in main_results)
                        should_notify_photo = False

                        if is_images_complete:
                            should_notify_photo = True
                        elif hours_since_detected >= 48:
                            should_notify_photo = True
                        elif is_final_available:
                            should_notify_photo = True

                        if should_notify_photo:
                            podium_img_url = extract_podium_image(combined_html)
                            for r in results_data:
                                if not r.get('image_url') and podium_img_url and r['rank'] in ['優勝', '２位', '３位']:
                                    r['image_url'] = podium_img_url

                            final_results_to_send = [r for r in results_data if r['rank'] != "ラーメン賞" or r.get('image_url')]
                            if not is_night_mode:
                                notify_queue.append({"type": "result", "header": "📸【大会結果 写真追加！】", "round_num": round_num, "location": location, "results": final_results_to_send, "url": url, "theme_color": theme_color})
                                db_updates.append(("UPDATE tournaments SET notified_result = 2 WHERE url = ?", (url,)))

                if n_new == 0 and not is_night_mode:
                    notify_queue.append({"type": "info", "header": "🆕【新規大会開催予定】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": entry_str, "url": url, "theme_color": theme_color, "extra_info": extra_info_dict, "main_image_url": main_image_url})
                    c.execute("UPDATE tournaments SET notified_new = 1 WHERE url = ?", (url,))

                if is_cancelled == 1 and db_cancelled == 0:
                    notify_queue.append({"type": "info", "header": "🚨【緊急：開催中止・変更】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": "開催中止・変更が発生しました", "url": url, "theme_color": "#D32F2F", "main_image_url": main_image_url})
                    c.execute("UPDATE tournaments SET is_cancelled = 1 WHERE url = ?", (url,))
                    continue

                is_date_changed = (db_event_date != event_date_str or db_entry_str != entry_str)
                is_info_changed = (db_reception != reception_time or db_fee != fee)
                if is_date_changed or is_info_changed or (db_round != round_num) or (db_loc != location):
                    if is_date_changed and not is_night_mode:
                        if not is_initial_setup and (db_event_date == "開催日未定" or db_entry_str == "エントリー日時未定"):
                            notify_queue.append({"type": "info", "header": "📢【大会情報更新】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": entry_str, "url": url, "theme_color": theme_color, "extra_info": extra_info_dict, "main_image_url": main_image_url})
                    c.execute("UPDATE tournaments SET round_num = ?, location = ?, event_date = ?, entry_datetime = ?, entry_str = ?, reception_time = ?, fee = ?, original_text = ? WHERE url = ?", (round_num, location, event_date_str, current_entry_dt_str, entry_str, reception_time, fee, combined_text, url))

                if entry_dt and is_cancelled == 0:
                    if entry_dt > now:
                        time_diff = entry_dt - now
                        if timedelta(0) < time_diff <= timedelta(minutes=15):
                            if not n_15m:
                                if not is_cc:
                                    msg = "⏳ まもなく受付開始です！\nページを開いて待機をお願いします！"
                                    notify_queue.append({"type": "info", "header": "🔥【15分前直前リマインド】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": entry_str, "url": url, "theme_color": theme_color, "extra_info": extra_info_dict, "main_image_url": main_image_url, "custom_msg": msg})
                                db_updates.append(("UPDATE tournaments SET notified_15m=1, notified_1h=1, notified_1d=1 WHERE url=?", (url,)))
                        elif timedelta(0) < time_diff <= timedelta(hours=1):
                            if not n_1h:
                                db_updates.append(("UPDATE tournaments SET notified_1h=1, notified_1d=1 WHERE url=?", (url,)))
                        elif timedelta(0) < time_diff <= timedelta(days=1):
                            if not n_1d:
                                db_updates.append(("UPDATE tournaments SET notified_1d=1 WHERE url=?", (url,)))
                    else:
                        passed_time = now - entry_dt
                        target_10am = (entry_dt + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
                        if timedelta(0) <= passed_time <= timedelta(minutes=15) and not n_just:
                            msg = "📣 エントリーが開始されました！\n定員になる前にお急ぎください！"
                            notify_queue.append({"type": "info", "header": "🏁【エントリー開始！】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": entry_str, "url": url, "theme_color": theme_color, "extra_info": extra_info_dict, "main_image_url": main_image_url, "custom_msg": msg})
                            db_updates.append(("UPDATE tournaments SET notified_just = 1 WHERE url = ?", (url,)))
                        elif target_10am <= now <= target_10am + timedelta(hours=12) and not n_after_24h:
                            if not is_night_mode:
                                if not is_cc:
                                    msg = "💡 昨日からエントリーが始まっています！\nお申し込み忘れはございませんか？"
                                    notify_queue.append({"type": "info", "header": "⚠️【エントリー忘れ防止】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": entry_str, "url": url, "theme_color": theme_color, "extra_info": extra_info_dict, "main_image_url": main_image_url, "custom_msg": msg})
                                db_updates.append(("UPDATE tournaments SET notified_after_24h = 1 WHERE url = ?", (url,)))

                if event_dt and is_cancelled == 0:
                    is_day_before = (now.date() == (event_dt.date() - timedelta(days=1)))
                    is_in_target_hours = (EVENT_1D_HOUR_START <= now.hour < EVENT_1D_HOUR_END)
                    if is_day_before and is_in_target_hours and not n_event_1d:
                        weather_advice = get_weather_advice(location)
                        evt_extra = extra_info_dict.copy()
                        evt_extra["weather_advice"] = weather_advice
                        notify_queue.append({"type": "info", "header": "📅【明日大会開催！直前案内】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": entry_str, "url": url, "theme_color": theme_color, "extra_info": evt_extra, "main_image_url": main_image_url})
                        db_updates.append(("UPDATE tournaments SET notified_event_1d = 1 WHERE url = ?", (url,)))

        except Exception as e:
            print(f"⚠️ 予期せぬエラーが発生しました (URL: {url}): {e}")

    # 重複通知の排除
    unique_notify_queue = []
    seen = set()
    for item in notify_queue:
        identifier = f"{item.get('type')}_{item.get('round_num')}_{item.get('location')}"
        if identifier not in seen:
            seen.add(identifier)
            unique_notify_queue.append(item)
    notify_queue = unique_notify_queue

    # DB一括更新
    for query, params in db_updates:
        c.execute(query, params)
    conn.commit()
    conn.close()

    # LINE通知の実行（MAX制御ストッパー付き）
    if not is_initial_setup and notify_queue:
        if len(notify_queue) > MAX_NOTIFY_LIMIT:
            print(f"⚠️ 大量検知({len(notify_queue)}件)のため、LINEへの連続送信を安全装置により停止しました。")
        else:
            for item in notify_queue:
                if item.get("type") == "result":
                    send_result_line_flex(item["header"], item["round_num"], item["location"], item["results"], item["url"], item["theme_color"], LINE_CHANNEL_ACCESS_TOKEN, LINE_USER_ID, IS_TEST_MODE)
                elif item.get("type") == "video":
                    send_video_line_flex(item["header"], item["round_num"], item["location"], item["video_data"], item["url"], item["theme_color"], LINE_CHANNEL_ACCESS_TOKEN, LINE_USER_ID, IS_TEST_MODE, item.get("main_image_url"))
                else:
                    send_line_flex(item["header"], item["round_num"], item["location"], item["event_date_str"], item["entry_str"], item["url"], item["theme_color"], LINE_CHANNEL_ACCESS_TOKEN, LINE_USER_ID, IS_TEST_MODE, item.get("extra_info"), item.get("main_image_url"), item.get("custom_msg"))
                print(f"✅ LINE送信完了: {item['header']} / 第{item['round_num']}回/戦 {item['location']}")

    print("全自動監視処理が正常完了しました。")

if __name__ == "__main__":
    main()
