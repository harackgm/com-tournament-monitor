import os
import re
import sqlite3
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

# ==========================================
# 安全制御・環境変数設定
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")

DB_PATH = "tournaments.db"
MAX_NOTIFY_LIMIT = 5  # 大量通知ストッパー（裏側の安全装置：5件以上はLINE非送信でDB更新のみ）
TIMEOUT_SEC = 10  # 通信タイムアウト時間(10秒)

# --- 大会前日リマインドの通知時間帯指定 ---
EVENT_1D_HOUR_START = 18
EVENT_1D_HOUR_END = 22

# --- 🌙 おやすみモード（深夜通知防止）設定 ---
# 23:00〜9:00の間の通知を自動で保留する本番設定
NIGHT_MODE_START = 23
NIGHT_MODE_END = 9

# 主要釣り場の座標マッピング
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
# 日本時間（JST）の強制取得関数
# ==========================================
def get_jst_now():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).replace(tzinfo=None)

# ==========================================
# 強化版 ネットワーク接続ヘルパー（サーバー負荷軽減）
# ==========================================
def fetch_url(url, retries=3):
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
                print(f"⚠️ 接続失敗 (上限到達): {url} -> {e}")
                return None
            wait_time = 2
            print(f"💡 リトライ待ち ({i+1}/{retries}回目, {wait_time}秒後): {url}")
            time.sleep(wait_time)

def get_weather_advice(location_name):
    lat, lon = 36.5, 139.8
    for name, coords in LOCATION_COORDS.items():
        if name in location_name:
            lat, lon = coords
            break
    try:
        # 最高/最低気温、降水量、最大風速を取得
        api_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max&timezone=Asia%2FTokyo"
        res = requests.get(api_url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            max_temp = data["daily"]["temperature_2m_max"][1]
            min_temp = data["daily"]["temperature_2m_min"][1]
            precip = data["daily"]["precipitation_sum"][1]
            wind = data["daily"]["windspeed_10m_max"][1]
            w_code = data["daily"]["weathercode"][1]

            # 基本の天気テキスト
            if w_code in [51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99] or (precip > 1.0):
                w_text = f"🌧 雨予報 (降水量: {precip}mm)"
            elif w_code in [3, 45, 48]:
                w_text = "☁️ 曇り予報"
            else:
                w_text = "🌤 晴れ/概ね晴れ"
                
            advice = f"{w_text}\n🌡 気温: 最高{int(max_temp)}℃ / 最低{int(min_temp)}℃\n🌬 最大風速: {wind}m/s\n\n"
            
            # コンディションに応じたアドバイス
            if precip > 1.0:
                advice += "レインウェアと防水対策をお忘れなく！"
            elif max_temp >= 30:
                advice += "猛暑が予想されます。熱中症対策を万全に！"
            elif max_temp <= 10 or min_temp <= 5:
                advice += "冷え込みが予想されます。防寒・防風対策をしっかりと！"
            elif wind >= 5.0:
                advice += "風が少し強そうです。キャスト時のラインメンディングに注意しましょう！"
            else:
                advice += "絶好の釣り日和になりそうです！"
                
            return f"{advice}\n🔥 日頃の練習の成果を発揮し、優勝を目指してください！"
    except Exception:
        pass
    return "🎣 体調管理を万全にして大会に挑みましょう！優勝目指してファイトです！"

# ==========================================
# 1. データベース初期化
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
                notified_after_24h INTEGER,
                notified_result INTEGER DEFAULT 0,
                notified_video_interview INTEGER DEFAULT 0,
                notified_video_final INTEGER DEFAULT 0,
                winner_name TEXT DEFAULT ''
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
    conn.commit()
    c.execute("CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY, value TEXT)")
    
    c.execute("SELECT value FROM system_config WHERE key = 'system_update_v6'")
    if not c.fetchone():
        c.execute("INSERT INTO system_config (key, value) VALUES ('system_update_v6', '1')")
        conn.commit()

    return conn, is_initial_setup

def get_theme_color(location_name):
    if any(kw in location_name for kw in ["栃木", "群馬", "キングフィッシャー", "上永野", "みどり", "なら山", "大芦", "増井", "宇都宮", "アメイズ", "中之沢", "赤城", "川場", "沼田", "宮城", "ベリーズ", "イワナ"]):
        return "#03A9F4"  
    elif any(kw in location_name for kw in ["千葉", "茨城", "ジョイバレー", "けんた", "千葉川すそ", "座間", "高萩", "エリアJ"]):
        return "#FF5722"  
    elif any(kw in location_name for kw in ["埼玉", "朝霞", "吉羽園", "しらこばと", "川越"]):
        return "#E91E63"  
    elif any(kw in location_name for kw in ["神奈川", "上浜", "王禅寺", "開成", "足柄", "ベリーパーク"]):
        return "#9C27B0"  
    elif any(kw in location_name for kw in ["東京", "浅川", "秋川"]):
        return "#3F51B5"  
    elif any(kw in location_name for kw in ["静岡", "浜名湖", "東山湖", "すその", "柿田川"]):
        return "#FF9800"  
    elif any(kw in location_name for kw in ["山梨", "長野", "白州", "シルフ", "竜華池", "鹿島槍"]):
        return "#4CAF50"  
    elif any(kw in location_name for kw in ["三重", "岐阜", "滋賀", "サンクチュアリ", "サンク", "瑞浪", "平谷", "醒井"]):
        return "#009688"  
    else:
        return "#607D8B"  

# ==========================================
# テキスト解析ヘルパー関数群
# ==========================================
def extract_event_date_info(text, year):
    match = re.search(r"(?:(\d{4})[年/.-])?\s*(\d{1,2})[月/.-](\d{1,2})[日]?", text)
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
    match = re.search(r"【?受\s*付】?[：:\s]*([0-2]?[0-9][:：][0-5][0-9](?:\s*[\~～\-]\s*[0-2]?[0-9][:：][0-5][0-9])?|[^。、\n]{2,10}より)", text)
    return match.group(1).strip() if match else "情報参照"

def extract_fee(text):
    match = re.search(r"【?(?:参加費用|参加費|費用)】?[：:\s]*([^。、\n]{2,20}円(?:\s*[\(（][^\)）]*[\)）])?)", text)
    return match.group(1).strip() if match else "情報参照"

def extract_tournament_results_from_html(html_content):
    results = []
    if not html_content: return results
    soup = BeautifulSoup(html_content, "html.parser")
    
    rank_pattern = re.compile(r"^(優勝|準優勝|[1-3１-３一二三]位)")
    
    for tag in soup.find_all(['h3', 'h4']):
        exact_heading = tag.get_text(strip=True)
        if "インタビュー" in exact_heading or "動画" in exact_heading: continue
        if rank_pattern.match(exact_heading):
            rank_match = rank_pattern.match(exact_heading)
            rank = rank_match.group(1)
            if "1" in rank or "一" in rank: rank = "優勝"
            elif "2" in rank or "二" in rank or "準優勝" in rank: rank = "２位"
            elif "3" in rank or "三" in rank: rank = "３位"

            cleaned_text = re.sub(r"^(優勝|準優勝|[1-3１-３一二三]位)\s*(?:\[\d+\])?\s*", "", exact_heading)
            name_match = re.search(r"^([^\s/【選手]+)", cleaned_text)
            name = name_match.group(1).strip() if name_match else cleaned_text.strip()
            name = re.sub(r"[\s\u3000/・\-]", "", name)

            if not name or len(name) < 2 or "タックル" in name or "コメント" in name: continue
            jump_target = exact_heading[:20]

            img_url = None
            nxt = tag.find_next_sibling()
            count = 0
            while nxt and count < 4:
                img = nxt.find('img') if hasattr(nxt, 'find') else None
                if not img and nxt.name == 'img': img = nxt
                if img and img.get('src'):
                    img_url = img.get('src')
                    if img_url.startswith('/'): img_url = "https://www.kanritsuriba.com" + img_url
                    break
                nxt = nxt.find_next_sibling()
                count += 1
                
            if not any(r['name'] == name for r in results):
                results.append({"rank": rank, "name": name, "image_url": img_url, "jump_target": jump_target})
    return results

def get_winner_congratulations_message(cursor, winner_name, current_round_num):
    clean_winner_name = re.sub(r"[\s\u3000/・\-]", "", winner_name)
    cursor.execute("SELECT round_num, player_name FROM tournament_winners WHERE rank = '優勝'")
    rows = cursor.fetchall()

    past_wins = 0
    is_consecutive = False
    try:
        current_r = int(re.sub(r"\D", "", str(current_round_num))) if re.sub(r"\D", "", str(current_round_num)) else 0
        prev_r = current_r - 1
    except ValueError:
        current_r, prev_r = None, None

    for r_num, p_name in rows:
        clean_p_name = re.sub(r"[\s\u3000/・\-]", "", p_name)
        if str(r_num) == str(current_round_num): continue
        if clean_winner_name == clean_p_name:
            past_wins += 1
            if prev_r is not None and str(prev_r) in str(r_num):
                is_consecutive = True

    if is_consecutive: return f"🎉 圧巻の2連続優勝おめでとうございます！強すぎます！🔥"
    elif past_wins >= 1: return f"🎉 今季{past_wins + 1}勝目のお祝いを申し上げます！素晴らしい快進撃です！👏"
    else: return "🎉 優勝おめでとうございます！見事な勝利です！"

def normalize_youtube_url(url_str):
    if not url_str: return url_str
    if url_str.startswith("//"): url_str = "https:" + url_str
    embed_match = re.search(r"(?:youtube\.com/embed/|youtu\.be/|youtube\.com/watch\?v=)([a-zA-Z0-9_-]+)", url_str)
    if embed_match: return f"https://www.youtube.com/watch?v={embed_match.group(1)}"
    return url_str

def extract_videos_from_html(html_content):
    videos = {}
    if not html_content: return videos
    soup = BeautifulSoup(html_content, "html.parser")
    headings = soup.find_all(['h2', 'h3', 'h4'])
    interview_kws = ["インタビュー", "優勝者の声", "コメント", "ヒーロー", "winner"]
    final_kws = ["決勝", "ファイナル", "優勝決定戦", "final"]
    exclude_kws = ["準決勝", "予選", "セミファイナル", "3位決定戦", "三位決定戦", "準々決勝", "semi"]

    for i, tag in enumerate(headings):
        text = tag.get_text(strip=True).lower()
        is_interview = any(kw in text for kw in interview_kws)
        is_final = any(kw in text for kw in final_kws) and not any(kw in text for kw in exclude_kws)
        
        if is_interview or is_final:
            block_elements = []
            curr = tag.next_element
            while curr and curr != (headings[i+1] if i+1 < len(headings) else None):
                block_elements.append(curr)
                curr = curr.next_element
            block_soup = BeautifulSoup("".join([str(e) for e in block_elements]), "html.parser")
            
            vid_url = None
            for iframe in block_soup.find_all('iframe'):
                src = iframe.get('src', '')
                if 'youtube' in src or 'youtu.be' in src:
                    vid_url = src
                    break
            if not vid_url:
                for a_tag in block_soup.find_all('a'):
                    target_str = f"{a_tag.get('href', '')} {a_tag.get('title', '')} {a_tag.get_text()}"
                    if 'youtube.com' in target_str or 'youtu.be' in target_str:
                        vid_url = a_tag.get('href', '') or a_tag.get('title', '')
                        break
            if vid_url:
                normalized_url = normalize_youtube_url(vid_url)
                if is_interview and "interview" not in videos: videos["interview"] = {"title": tag.get_text(strip=True), "url": normalized_url}
                if is_final and "final" not in videos: videos["final"] = {"title": tag.get_text(strip=True), "url": normalized_url}
    return videos

def extract_entry_conditions(soup):
    conditions = {
        1: "今回初めて「エリアトラウトのルアー大会」に参加する方",
        2: "「エリアトラウトのルアー大会」参加経験がある方で3位以内の入賞経験のない方",
        3: "「エリアトラウトのルアー大会」参加経験がある方で過去2年間、優勝経験のない方"
    }
    try:
        boxes = soup.find_all("div", class_="success-box")
        for box in boxes:
            for br in box.find_all("br"):
                br.replace_with("\n")
            text_lines = [line.strip() for line in box.get_text().split("\n") if line.strip()]
            if len(text_lines) < 2: continue
            
            for i in range(1, 4):
                num_char_list = [str(i), ["１", "２", "３"][i-1], ["一", "二", "三"][i-1]]
                if any(f"{nc}次" in text_lines[0] for nc in num_char_list):
                    cond_text = text_lines[1]
                    cond_text = re.sub(r"[/／].*", "", cond_text).strip()
                    cond_text = re.sub(r"は\s*(?:\d+月|\d+[/.-]\d+).*", "", cond_text).strip()
                    if cond_text:
                        conditions[i] = cond_text
    except Exception:
        pass
    return conditions

def fetch_page_data(url):
    res = fetch_url(url)
    if res and res.status_code == 200:
        try:
            soup = BeautifulSoup(res.text, "html.parser")
            h1_tag = soup.find('h1', class_='entry-title')
            title_text = h1_tag.get_text(strip=True) if h1_tag else ""
            content_area = soup.find("div", class_="entry-content") or soup
            
            text_space = content_area.get_text(separator=" ", strip=True)
            text_lines = [line.strip() for line in content_area.get_text(separator="\n", strip=True).split("\n") if line.strip()]
            
            return text_space, str(content_area), title_text, text_lines
        except Exception: pass
    return "", "", "", []

# ==========================================
# LINE Push Message (Flex Message カルーセル)
# ==========================================
def send_line_flex(header_title, round_num, location, event_date_str, entry_str, page_url, theme_color, extra_info=None):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    
    is_cc = "/cc" in page_url
    title_main = f"第{round_num}回" if is_cc else f"第{round_num}戦"
    title_sub = location if is_cc else f"{location}大会"
    
    # 開催前日（直前案内）かどうかの判定
    is_day_before_notice = "明日大会開催" in header_title

    body_contents = [
        {"type": "text", "text": title_main, "weight": "bold", "size": "xl", "color": "#333333"},
        {"type": "text", "text": title_sub, "weight": "bold", "size": "md", "color": "#555555", "wrap": True},
        {"type": "separator", "margin": "md"}
    ]
    
    if is_day_before_notice:
        # ■ 前日案内専用レイアウト
        body_contents.append({
            "type": "box", "layout": "vertical", "spacing": "sm", "margin": "md",
            "contents": [
                {"type": "text", "text": "📅 大会開催日", "size": "sm", "color": "#888888", "weight": "bold"},
                {"type": "text", "text": event_date_str, "size": "xl", "color": "#333333", "weight": "bold"}
            ]
        })
        body_contents.append({"type": "separator", "margin": "md"})
        
        if extra_info:
            body_contents.append({
                "type": "box", 
                "layout": "vertical", 
                "spacing": "sm",
                "margin": "md", 
                "contents": [
                    {"type": "text", "text": f"📋 受付時間: {extra_info.get('reception', '情報参照')}", "size": "md", "color": "#D32F2F", "weight": "bold"}, 
                    {"type": "text", "text": f"💰 参加費用: {extra_info.get('fee', '情報参照')}", "size": "md", "color": "#D32F2F", "weight": "bold"}
                ]
            })
            if "weather_advice" in extra_info:
                body_contents.append({"type": "separator", "margin": "md"})
                body_contents.append({
                    "type": "box", 
                    "layout": "vertical", 
                    "spacing": "sm", 
                    "margin": "md",
                    "contents": [
                        {"type": "text", "text": "🌤 明日の天候・コンディション", "size": "sm", "color": "#888888", "weight": "bold"}, 
                        {"type": "text", "text": extra_info["weather_advice"], "size": "md", "color": "#333333", "wrap": True, "weight": "bold"}
                    ]
                })
    else:
        # ■ 通常のエントリー関連・お知らせレイアウト
        body_contents.append({"type": "box", "layout": "vertical", "spacing": "xs", "margin": "md", "contents": [{"type": "text", "text": "📅 大会開催日", "size": "xs", "color": "#888888"}, {"type": "text", "text": event_date_str, "size": "xl", "color": "#333333"}]})
        body_contents.append({"type": "box", "layout": "vertical", "spacing": "xs", "contents": [{"type": "text", "text": "⏰ エントリー開始日時", "size": "xs", "color": "#888888"}, {"type": "text", "text": entry_str, "size": "md", "color": "#E53935", "wrap": True}]})
        
        if extra_info:
            body_contents.append({"type": "separator", "margin": "md"})
            if "entry_condition" in extra_info:
                body_contents.append({
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "xs",
                    "margin": "md",
                    "contents": [
                        {"type": "text", "text": "✅ エントリー参加条件", "size": "xs", "color": "#888888", "weight": "bold"},
                        {"type": "text", "text": extra_info["entry_condition"], "size": "sm", "color": "#D32F2F", "wrap": True, "weight": "bold"}
                    ]
                })
                body_contents.append({"type": "separator", "margin": "md"})

            body_contents.append({
                "type": "box", 
                "layout": "vertical", 
                "spacing": "xs",
                "margin": "md", 
                "contents": [
                    {"type": "text", "text": f"📋 受付時間: {extra_info.get('reception', '情報参照')}", "size": "sm", "color": "#555555"}, 
                    {"type": "text", "text": f"💰 参加費用: {extra_info.get('fee', '情報参照')}", "size": "sm", "color": "#555555"}
                ]
            })

    flex_payload = {"to": LINE_USER_ID, "messages": [{"type": "flex", "altText": f"【{header_title}】{title_main} {title_sub}", "contents": {"type": "carousel", "contents": [{"type": "bubble", "header": {"type": "box", "layout": "vertical", "backgroundColor": theme_color, "contents": [{"type": "text", "text": f"🎣 {header_title}", "color": "#FFFFFF", "weight": "bold", "size": "xs"}]}, "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": body_contents}, "footer": {"type": "box", "layout": "vertical", "contents": [{"type": "button", "action": {"type": "uri", "label": "🔗 詳細・エントリー", "uri": page_url}, "style": "primary", "color": theme_color}]}}]}}]}
    try: requests.post(url, headers=headers, json=flex_payload, timeout=TIMEOUT_SEC)
    except Exception: pass

def send_result_line_flex(header_title, round_num, location, results, page_url, theme_color):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    
    is_cc = "/cc" in page_url
    title_text_str = f"第{round_num}回 {location}" if is_cc else f"第{round_num}戦 {location}"

    bubbles = []
    for res in results[:10]:
        rank = res['rank']
        name = res['name']
        img_url = res.get('image_url')
        bg_color = theme_color
        
        is_winner = False
        if "優勝" in rank or "1" in rank or "１" in rank: 
            bg_color = "#D4AF37"
            formatted_name = f"🏆 優勝！{name}"
            header_text = "優勝"
            is_winner = True
        elif "2" in rank or "２" in rank or "準" in rank: 
            bg_color = "#C0C0C0"
            formatted_name = f"🥈 準優勝・{name}"
            header_text = "第２位"
        elif "3" in rank or "三" in rank: 
            bg_color = "#CD7F32"
            formatted_name = f"🥉 準々優勝・{name}"
            header_text = "第３位"
        else:
            formatted_name = f"🏅 {rank}・{name}"
            header_text = rank

        body_contents = [
            {"type": "text", "text": formatted_name, "weight": "bold", "size": "xl", "margin": "md", "color": "#333333", "wrap": True},
            {"type": "text", "text": title_text_str, "size": "xs", "color": "#888888", "margin": "sm", "wrap": True}
        ]
        
        if is_winner and res.get('congrat_msg'):
            body_contents.append({"type": "separator", "margin": "md"})
            body_contents.append({"type": "text", "text": res['congrat_msg'], "size": "xs", "color": "#D32F2F", "weight": "bold", "margin": "md", "wrap": True})

        jump_target = res.get('jump_target', name)
        target_url = f"{page_url}#:~:text={urllib.parse.quote(jump_target)}"

        bubble = {
            "type": "bubble", 
            "header": {"type": "box", "layout": "vertical", "backgroundColor": bg_color, "contents": [{"type": "text", "text": header_text, "color": "#FFFFFF", "weight": "bold", "size": "md"}]}, 
            "body": {"type": "box", "layout": "vertical", "alignItems": "center", "contents": body_contents}, 
            "footer": {"type": "box", "layout": "vertical", "contents": [{"type": "button", "action": {"type": "uri", "label": "🔗 結果詳細を見る", "uri": target_url}, "style": "primary", "color": bg_color}]}
        }
        if img_url:
            bubble["hero"] = {"type": "image", "url": img_url, "size": "full", "aspectRatio": "3:4", "aspectMode": "fit", "backgroundColor": "#FFFFFF"}
        bubbles.append(bubble)
    
    try: requests.post(url, headers=headers, json={"to": LINE_USER_ID, "messages": [{"type": "flex", "altText": f"【大会結果】{title_text_str}", "contents": {"type": "carousel", "contents": bubbles}}]}, timeout=TIMEOUT_SEC)
    except Exception: pass

def send_video_line_flex(header_title, round_num, location, video_data, page_url, theme_color):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    
    is_cc = "/cc" in page_url
    title_main = f"第{round_num}回" if is_cc else f"第{round_num}戦"
    title_sub = location if is_cc else f"{location}大会"

    vid_title = video_data.get("title", "動画が公開されました")
    vid_url = video_data.get("url", page_url)
    
    body_contents = [
        {"type": "text", "text": title_main, "weight": "bold", "size": "xl", "color": "#333333"},
        {"type": "text", "text": title_sub, "weight": "bold", "size": "md", "color": "#555555", "wrap": True},
        {"type": "separator"},
        {"type": "box", "layout": "vertical", "spacing": "xs", "margin": "md", "contents": [{"type": "text", "text": vid_title, "weight": "bold", "size": "sm", "color": "#E53935", "wrap": True}]}
    ]
    flex_payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "flex", "altText": f"{header_title} {title_main} {title_sub}", "contents": {"type": "bubble", "header": {"type": "box", "layout": "vertical", "backgroundColor": theme_color, "contents": [{"type": "text", "text": f"▶️ {header_title}", "color": "#FFFFFF", "weight": "bold", "size": "xs"}]}, "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": body_contents}, "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [{"type": "button", "action": {"type": "uri", "label": "▶️ 動画を見る", "uri": vid_url}, "style": "primary", "color": "#D32F2F"}, {"type": "button", "action": {"type": "uri", "label": "🔗 大会ページへ", "uri": page_url}, "style": "secondary"}]}}}]
    }
    try: requests.post(url, headers=headers, json=flex_payload, timeout=TIMEOUT_SEC)
    except Exception: pass

# ==========================================
# メイン監視処理（一般公開・本番運用モード）
# ==========================================
def main():
    now = get_jst_now()
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 全自動監視処理（本番運用モード）を開始します。")
    
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
                    if "/at/" in url: urls_to_check.append(url)
        except Exception as e: print(f"⚠️ RSS解析エラー: {e}")

    c.execute("SELECT url FROM tournaments")
    for row in c.fetchall(): urls_to_check.append(row[0])

    urls_to_check = list(set(urls_to_check))
    print(f"📊 チェック対象URL数: {len(urls_to_check)}件")

    notify_queue = []
    db_updates = []

    for url in urls_to_check:
        try:
            c.execute("SELECT notified_video_interview, notified_video_final, event_datetime FROM tournaments WHERE url = ?", (url,))
            check_row = c.fetchone()
            if check_row:
                n_video_int, n_video_final, event_dt_str = check_row
                if n_video_int == 1 and n_video_final == 1: continue
                if event_dt_str:
                    try:
                        event_dt_db = datetime.strptime(event_dt_str, "%Y-%m-%d %H:%M:%S")
                        if (now - event_dt_db).days > 30: continue
                    except Exception: pass

            print(f"🔍 ページ解析中: {url}")
            match_year = re.search(r"/at/(\d{4})_", url)
            url_year = int(match_year.group(1)) if match_year else current_year

            time.sleep(0.5)
            text_p1, html_p1, title_p1, lines_p1 = fetch_page_data(url)
            sub_url = url.rstrip("/") + "/2/"
            time.sleep(0.5)
            text_p2, html_p2, title_p2, lines_p2 = fetch_page_data(sub_url)

            combined_text = (text_p2 + " " + text_p1).strip()
            combined_html = html_p2 + html_p1
            combined_lines = lines_p1 + lines_p2
            if not combined_text: continue

            conditions_dict = extract_entry_conditions(BeautifulSoup(combined_html, "html.parser"))

            is_cc = "/cc" in url
            active_entry_idx = 1

            if is_cc:
                match_cc = re.search(r"cc(\d+)", url)
                round_num = str(int(match_cc.group(1))) if match_cc else "不明"
                
                loc_match = re.search(r"第\d+回(.*)", title_p1)
                raw_loc = loc_match.group(1).strip() if loc_match else (title_p1 or "チャレンジカップ")
                location = re.sub(r"【.*?】", "", raw_loc)
                location = re.sub(r"は.*?(選手が優勝|が優勝).*", "", location).strip()
                
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
                        entry_year = url_year - 1 if m >= 11 else url_year
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
                    entry_dt, entry_str = parse_entry_datetime(text_p2, url_year)
                    if not entry_dt: entry_dt, entry_str = parse_entry_datetime(text_p1, url_year)
            else:
                match_title = re.search(r"第(\d+)戦([^\s大会を]+)", combined_text)
                round_num = match_title.group(1) if match_title else "不明"
                location = match_title.group(2) if match_title else "対象会場"
                entry_dt, entry_str = parse_entry_datetime(text_p2, url_year)
                if not entry_dt: entry_dt, entry_str = parse_entry_datetime(text_p1, url_year)

            event_dt, event_date_str = extract_event_date_info(combined_text, url_year)
            reception_time = extract_reception_time(combined_text)
            fee = extract_fee(combined_text)
            theme_color = get_theme_color(location)
            cancel_keywords = ["見送る", "中止", "延期", "順延", "取りやめ", "開催を見送", "開催中止"]
            is_cancelled = 1 if any(kw in combined_text for kw in cancel_keywords) else 0

            extra_info_dict = {"reception": reception_time, "fee": fee}
            if is_cc and active_entry_idx in conditions_dict:
                extra_info_dict["entry_condition"] = f"[{active_entry_idx}次対象者]\n{conditions_dict[active_entry_idx]}"

            results_data = extract_tournament_results_from_html(combined_html)
            videos_data = extract_videos_from_html(combined_html)

            winner_name = ""
            for r in results_data:
                if r['rank'] == "優勝":
                    winner_name = r['name']
                    r['congrat_msg'] = get_winner_congratulations_message(c, winner_name, round_num)
                c.execute("INSERT OR REPLACE INTO tournament_winners (url, round_num, rank, player_name) VALUES (?, ?, ?, ?)", (url, round_num, r['rank'], r['name']))

            c.execute("SELECT * FROM tournaments WHERE url = ?", (url,))
            row = c.fetchone()

            if not row:
                new_notified_flag = 0 if (is_night_mode and not is_initial_setup) else 1
                c.execute(
                    """INSERT INTO tournaments 
                    (url, round_num, location, event_date, event_datetime, entry_datetime, entry_str, reception_time, fee, original_text, is_cancelled, notified_new, notified_1d, notified_1h, notified_15m, notified_event_1d, notified_just, notified_after_24h, notified_result, notified_video_interview, notified_video_final, winner_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, ?)""",
                    (url, round_num, location, event_date_str, event_dt.strftime("%Y-%m-%d %H:%M:%S") if event_dt else None, entry_dt.strftime("%Y-%m-%d %H:%M:%S") if entry_dt else None, entry_str, reception_time, fee, combined_text, is_cancelled, new_notified_flag, winner_name)
                )
                if not is_initial_setup and not is_night_mode:
                    notify_queue.append({"type": "info", "header": "🆕【新規大会開催予定】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": entry_str, "url": url, "theme_color": theme_color, "extra_info": extra_info_dict})
                    print(f"🚀 【送信キュー追加】新規大会: 第{round_num}回/戦 {location}")
            else:
                (db_url, db_round, db_loc, db_event_date, db_event_dt_str, db_entry_dt_str, db_entry_str, db_reception, db_fee, db_text, db_cancelled, n_new, n_1d, n_1h, n_15m, n_event_1d, n_just, n_after_24h, n_result, n_video_int, n_video_fin, db_winner) = row

                current_entry_dt_str = entry_dt.strftime("%Y-%m-%d %H:%M:%S") if entry_dt else None
                if db_entry_dt_str and current_entry_dt_str and db_entry_dt_str != current_entry_dt_str:
                    n_1d, n_1h, n_15m, n_just, n_after_24h = 0, 0, 0, 0, 0
                    db_updates.append(("UPDATE tournaments SET notified_1d=0, notified_1h=0, notified_15m=0, notified_just=0, notified_after_24h=0 WHERE url=?", (url,)))
                    print(f"🔄 【フェーズ移行】エントリー日時が更新されたため通知フラグをリセット: 第{round_num}回/戦")

                if winner_name and db_winner != winner_name:
                    c.execute("UPDATE tournaments SET winner_name = ? WHERE url = ?", (winner_name, url))
                if "interview" in videos_data and n_video_int == 0:
                    if not is_night_mode:
                        notify_queue.append({"type": "video", "header": "🎤【優勝者インタビュー公開】", "round_num": round_num, "location": location, "video_data": videos_data["interview"], "url": url, "theme_color": theme_color})
                        db_updates.append(("UPDATE tournaments SET notified_video_interview = 1 WHERE url = ?", (url,)))
                if "final" in videos_data and n_video_fin == 0:
                    if not is_night_mode:
                        notify_queue.append({"type": "video", "header": "🎥【決勝戦 動画公開】", "round_num": round_num, "location": location, "video_data": videos_data["final"], "url": url, "theme_color": theme_color})
                        db_updates.append(("UPDATE tournaments SET notified_video_final = 1 WHERE url = ?", (url,)))
                if results_data and n_result == 0:
                    days_since_event = (now - event_dt).days if event_dt else 999
                    if days_since_event > 14: db_updates.append(("UPDATE tournaments SET notified_result = 1 WHERE url = ?", (url,)))
                    else:
                        if not is_night_mode:
                            notify_queue.append({"type": "result", "header": "🎊【大会結果発表！】", "round_num": round_num, "location": location, "results": results_data, "url": url, "theme_color": theme_color})
                            db_updates.append(("UPDATE tournaments SET notified_result = 1 WHERE url = ?", (url,)))
                if n_new == 0 and not is_night_mode:
                    notify_queue.append({"type": "info", "header": "🆕【新規大会開催予定】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": entry_str, "url": url, "theme_color": theme_color, "extra_info": extra_info_dict})
                    c.execute("UPDATE tournaments SET notified_new = 1 WHERE url = ?", (url,))
                if is_cancelled == 1 and db_cancelled == 0:
                    notify_queue.append({"type": "info", "header": "🚨【緊急：開催中止・変更】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": "開催中止・変更が発生しました", "url": url, "theme_color": "#D32F2F"})
                    c.execute("UPDATE tournaments SET is_cancelled = 1 WHERE url = ?", (url,))
                    continue

                is_date_changed = (db_event_date != event_date_str or db_entry_str != entry_str)
                is_info_changed = (db_reception != reception_time or db_fee != fee)
                if is_date_changed or is_info_changed or (db_round != round_num) or (db_loc != location):
                    if is_date_changed and not is_night_mode:
                        if not is_initial_setup and (db_event_date == "開催日未定" or db_entry_str == "エントリー日時未定"):
                            notify_queue.append({"type": "info", "header": "📢【大会情報更新】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": entry_str, "url": url, "theme_color": theme_color, "extra_info": extra_info_dict})
                    c.execute("UPDATE tournaments SET round_num = ?, location = ?, event_date = ?, entry_datetime = ?, entry_str = ?, reception_time = ?, fee = ?, original_text = ? WHERE url = ?", (round_num, location, event_date_str, current_entry_dt_str, entry_str, reception_time, fee, combined_text, url))

                if entry_dt and is_cancelled == 0:
                    if entry_dt > now:
                        time_diff = entry_dt - now
                        if timedelta(0) < time_diff <= timedelta(minutes=15):
                            if not n_15m:
                                if not is_cc:
                                    notify_queue.append({"type": "info", "header": "🔥【15分前直前リマインド】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": entry_str, "url": url, "theme_color": theme_color, "extra_info": extra_info_dict})
                                db_updates.append(("UPDATE tournaments SET notified_15m=1, notified_1h=1, notified_1d=1 WHERE url=?", (url,)))
                        elif timedelta(0) < time_diff <= timedelta(hours=1):
                            if not n_1h:
                                if not is_cc:
                                    notify_queue.append({"type": "info", "header": "⏰【1時間前リマインド】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": entry_str, "url": url, "theme_color": theme_color, "extra_info": extra_info_dict})
                                db_updates.append(("UPDATE tournaments SET notified_1h=1, notified_1d=1 WHERE url=?", (url,)))
                        elif timedelta(0) < time_diff <= timedelta(days=1):
                            if not n_1d:
                                if not is_night_mode:
                                    is_today = (entry_dt.date() == now.date())
                                    notify_queue.append({"type": "info", "header": "【本日エントリー開始】" if is_today else "【明日エントリー開始】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": entry_str, "url": url, "theme_color": theme_color, "extra_info": extra_info_dict})
                                    db_updates.append(("UPDATE tournaments SET notified_1d=1 WHERE url=?", (url,)))
                    else:
                        passed_time = now - entry_dt
                        target_10am = (entry_dt + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
                        if timedelta(0) <= passed_time <= timedelta(minutes=15) and not n_just:
                            notify_queue.append({"type": "info", "header": "🏁【エントリー開始！】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": entry_str, "url": url, "theme_color": theme_color, "extra_info": extra_info_dict})
                            db_updates.append(("UPDATE tournaments SET notified_just = 1 WHERE url = ?", (url,)))
                        elif target_10am <= now <= target_10am + timedelta(hours=12) and not n_after_24h:
                            if not is_night_mode:
                                if not is_cc:
                                    notify_queue.append({"type": "info", "header": "⚠️【エントリー忘れ防止】エントリーが開始されています！", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": entry_str, "url": url, "theme_color": theme_color, "extra_info": extra_info_dict})
                                db_updates.append(("UPDATE tournaments SET notified_after_24h = 1 WHERE url = ?", (url,)))

                if event_dt and is_cancelled == 0:
                    is_day_before = (now.date() == (event_dt.date() - timedelta(days=1)))
                    is_in_target_hours = (EVENT_1D_HOUR_START <= now.hour < EVENT_1D_HOUR_END)
                    if is_day_before and is_in_target_hours and not n_event_1d:
                        weather_advice = get_weather_advice(location)
                        evt_extra = extra_info_dict.copy()
                        evt_extra["weather_advice"] = weather_advice
                        notify_queue.append({"type": "info", "header": "📅【明日大会開催！直前案内】", "round_num": round_num, "location": location, "event_date_str": event_date_str, "entry_str": entry_str, "url": url, "theme_color": theme_color, "extra_info": evt_extra})
                        db_updates.append(("UPDATE tournaments SET notified_event_1d = 1 WHERE url = ?", (url,)))

        except Exception as e: pass

    unique_notify_queue = []
    seen = set()
    for item in notify_queue:
        identifier = f"{item.get('type')}_{item.get('round_num')}_{item.get('location')}"
        if identifier not in seen:
            seen.add(identifier)
            unique_notify_queue.append(item)
    notify_queue = unique_notify_queue

    for query, params in db_updates: c.execute(query, params)
    conn.commit()
    conn.close()

    if not is_initial_setup and notify_queue:
        if len(notify_queue) > MAX_NOTIFY_LIMIT:
            print(f"⚠️ 大量検知({len(notify_queue)}件)のため、LINEへの連続送信をストップしました。")
        else:
            for item in notify_queue:
                if item.get("type") == "result":
                    send_result_line_flex(item["header"], item["round_num"], item["location"], item["results"], item["url"], item["theme_color"])
                elif item.get("type") == "video":
                    send_video_line_flex(item["header"], item["round_num"], item["location"], item["video_data"], item["url"], item["theme_color"])
                else:
                    send_line_flex(item["header"], item["round_num"], item["location"], item["event_date_str"], item["entry_str"], item["url"], item["theme_color"], item.get("extra_info"))
                print(f"✅ LINE送信完了: {item['header']} / 第{item['round_num']}回/戦 {item['location']}")

    print("全自動監視処理が正常完了しました。")

if __name__ == "__main__":
    main()
