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
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")  # ★ テスト配信用に復活

DB_PATH = "tournaments.db"
MAX_NOTIFY_LIMIT = 5  # 大量通知ストッパー
TIMEOUT_SEC = 10  # 通信タイムアウト時間(10秒)

# --- 大会前日リマインドの通知時間帯指定 ---
EVENT_1D_HOUR_START = 18
EVENT_1D_HOUR_END = 22

# --- 🌙 おやすみモード（深夜通知防止）設定 ---
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
# 強化版 ネットワーク接続ヘルパー
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
        except Exception:
            if i == retries - 1: return None
            time.sleep(2)

def is_youtube_video_available(youtube_url):
    if not youtube_url: return False
    res = fetch_url(youtube_url)
    if not res or res.status_code != 200: return False
    html = res.text
    upcoming_keywords = ["isUpcoming", "公開予定", "ライブ配信まで", "プレミア公開"]
    for kw in upcoming_keywords:
        if kw in html: return False
    return True

def get_weather_advice(location_name):
    lat, lon = 36.5, 139.8
    for name, coords in LOCATION_COORDS.items():
        if name in location_name:
            lat, lon = coords
            break
    try:
        api_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max&timezone=Asia%2FTokyo"
        res = requests.get(api_url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            max_temp = data["daily"]["temperature_2m_max"][1]
            min_temp = data["daily"]["temperature_2m_min"][1]
            precip = data["daily"]["precipitation_sum"][1]
            wind = data["daily"]["windspeed_10m_max"][1]
            w_code = data["daily"]["weathercode"][1]

            if w_code in [51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99] or (precip > 1.0): w_text = f"🌧 雨予報 (降水量: {precip}mm)"
            elif w_code in [3, 45, 48]: w_text = "☁️ 曇り予報"
            else: w_text = "🌤 晴れ/概ね晴れ"
                
            advice = f"{w_text}\n🌡 気温: 最高{int(max_temp)}℃ / 最低{int(min_temp)}℃\n🌬 最大風速: {wind}m/s\n\n"
            
            if precip > 1.0: advice += "レインウェアと防水対策をお忘れなく！"
            elif max_temp >= 30: advice += "猛暑が予想されます。熱中症対策を万全に！"
            elif max_temp <= 10 or min_temp <= 5: advice += "冷え込みが予想されます。防寒・防風対策をしっかりと！"
            elif wind >= 5.0: advice += "風が少し強そうです。キャスト時のラインメンディングに注意しましょう！"
            else: advice += "絶好の釣り日和になりそうです！"
                
            return f"{advice}\n🔥 日頃の練習の成果を発揮し、優勝を目指してください！"
    except Exception: pass
    return "🎣 体調管理を万全にして大会に挑みましょう！優勝目指してファイトです！"

# ==========================================
# 1. データベース初期化
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
        if "notified_result" not in column_names: c.execute("ALTER TABLE tournaments ADD COLUMN notified_result INTEGER DEFAULT 0")
        if "notified_video_interview" not in column_names: c.execute("ALTER TABLE tournaments ADD COLUMN notified_video_interview INTEGER DEFAULT 0")
        if "notified_video_final" not in column_names: c.execute("ALTER TABLE tournaments ADD COLUMN notified_video_final INTEGER DEFAULT 0")
        if "winner_name" not in column_names: c.execute("ALTER TABLE tournaments ADD COLUMN winner_name TEXT DEFAULT ''")
        if "result_detected_at" not in column_names: c.execute("ALTER TABLE tournaments ADD COLUMN result_detected_at DATETIME")
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

def get_theme_color(location_name):
    if any(kw in location_name for kw in ["栃木", "群馬", "キングフィッシャー", "上永野", "みどり", "なら山", "大芦", "増井", "宇都宮", "アメイズ", "中之沢", "赤城", "川場", "沼田", "宮城", "ベリーズ", "イワナ"]): return "#03A9F4"  
    elif any(kw in location_name for kw in ["千葉", "茨城", "ジョイバレー", "けんた", "千葉川すそ", "座間", "高萩", "エリアJ"]): return "#FF5722"  
    elif any(kw in location_name for kw in ["埼玉", "朝霞", "吉羽園", "しらこばと", "川越"]): return "#E91E63"  
    elif any(kw in location_name for kw in ["神奈川", "上浜", "王禅寺", "開成", "足柄", "ベリーパーク"]): return "#9C27B0"  
    elif any(kw in location_name for kw in ["東京", "浅川", "秋川"]): return "#3F51B5"  
    elif any(kw in location_name for kw in ["静岡", "浜名湖", "東山湖", "すその", "柿田川"]): return "#FF9800"  
    elif any(kw in location_name for kw in ["山梨", "長野", "白州", "シルフ", "竜華池", "鹿島槍"]): return "#4CAF50"  
    elif any(kw in location_name for kw in ["三重", "岐阜", "滋賀", "サンクチュアリ", "サンク", "瑞浪", "平谷", "醒井"]): return "#009688"  
    return "#607D8B"

# ==========================================
# テキスト解析ヘルパー関数群
# ==========================================
def extract_landscape_image(html_p1, html_p2):
    target_html = html_p2 if html_p2 else html_p1
    if not target_html: return None
    
    soup = BeautifulSoup(target_html, "html.parser")
    content_area = soup.find("div", class_="entry-content")
    if not content_area: return None
    
    for img in content_area.find_all('img'):
        src = img.get('src')
        if not src: continue
        
        alt = img.get('alt', '')
        if "優勝" in alt or "表彰台" in alt: continue
        
        width = img.get('width')
        height = img.get('height')
        if width and height:
            try:
                w = int(re.sub(r'\D', '', str(width)))
                h = int(re.sub(r'\D', '', str(height)))
                if w > h:
                    if src.startswith('/'): src = "https://www.kanritsuriba.com" + src
                    return re.sub(r'-\d+x\d+(?=\.[a-zA-Z]+$)', '', src)
            except ValueError:
                pass
                
    for img in content_area.find_all('img'):
        src = img.get('src')
        if not src: continue
        alt = img.get('alt', '')
        if "優勝" in alt or "表彰台" in alt: continue
        if src.startswith('/'): src = "https://www.kanritsuriba.com" + src
        return re.sub(r'-\d+x\d+(?=\.[a-zA-Z]+$)', '', src)
        
    return None

def extract_podium_image(html_content):
    if not html_content: return None
    soup = BeautifulSoup(html_content, "html.parser")
    for li in soup.find_all('li'):
        img_tag = li.find('img')
        if img_tag and img_tag.get('src'):
            text = li.get_text(strip=True)
            if "表彰台" in text:
                src = img_tag.get('src')
                if src.startswith('/'): src = "https://www.kanritsuriba.com" + src
                return re.sub(r'-\d+x\d+(?=\.[a-zA-Z]+$)', '', src)
    return None

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
    
    for li in soup.find_all('li'):
        text = li.get_text(strip=True)
        if "インタビュー" in text or "動画" in text: continue

        img_tag = li.find('img')
        src = None
        if img_tag and img_tag.get('src'):
            src = img_tag.get('src')
            if src.startswith('/'): src = "https://www.kanritsuriba.com" + src
            src = re.sub(r'-\d+x\d+(?=\.[a-zA-Z]+$)', '', src)

        match = re.search(r"^(優勝|準優勝|[1-3１-３一二三]位)[:：\s]*([^\s/【選手]+)", text)
        if match:
            rank = match.group(1)
            name = match.group(2).strip()
            name = re.sub(r"[\s\u3000/・\-]", "", name)
            if "1" in rank or "一" in rank: rank = "優勝"
            elif "2" in rank or "二" in rank or "準優勝" in rank: rank = "２位"
            elif "3" in rank or "三" in rank: rank = "３位"
            
            if name and len(name) >= 2 and "タックル" not in name:
                if not any(r['name'] == name for r in results):
                    results.append({"rank": rank, "name": name, "image_url": src, "jump_target": name})
        
        if "ラーメン賞" in text or "４位" in text or "4位" in text:
            if not any(r['rank'] == "ラーメン賞" for r in results):
                results.append({"rank": "ラーメン賞", "name": "", "image_url": src, "jump_target": "ラーメン賞"})
    
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

            img_url = None
            nxt = tag.find_next_sibling()
            count = 0
            while nxt and count < 4:
                img = nxt.find('img') if hasattr(nxt, 'find') else None
                if not img and nxt.name == 'img': img = nxt
                if img and img.get('src'):
                    src = img.get('src')
                    if src.startswith('/'): src = "https://www.kanritsuriba.com" + src
                    img_url = re.sub(r'-\d+x\d+(?=\.[a-zA-Z]+$)', '', src)
                    break
                nxt = nxt.find_next_sibling()
                count += 1
                
            existing = next((r for r in results if r['name'] == name), None)
            if existing:
                if not existing.get('image_url') and img_url:
                    existing['image_url'] = img_url
            else:
                results.append({"rank": rank, "name": name, "image_url": img_url, "jump_target": name})
                
    def get_rank_order(rank):
        if "優勝" in rank: return 1
        if "２位" in rank or "2位" in rank: return 2
        if "３位" in rank or "3位" in rank: return 3
        if "ラーメン賞" in rank: return 4
        return 99
    
    results.sort(key=lambda x: get_rank_order(x['rank']))
    return results

def get_winner_congratulations_message(cursor, winner_name, current_round_num):
    if not winner_name: return ""
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

def extract_videos_from_html(html_content, url_year):
    videos = {}
    if not html_content: return videos
    soup = BeautifulSoup(html_content, "html.parser")
    headings = soup.find_all(['h2', 'h3', 'h4'])
    interview_kws = ["インタビュー", "優勝者の声", "コメント", "ヒーロー", "winner"]
    final_kws = ["決勝", "ファイナル", "優勝決定戦", "final"]
    exclude_kws = ["準決勝", "予選", "セミファイナル", "3位決定戦", "三位決定戦", "準々決勝", "semi"]

    for i, tag in enumerate(headings):
        text = tag.get_text(strip=True)
        text_lower = text.lower()
        is_interview = any(kw in text_lower for kw in interview_kws)
        is_final = any(kw in text_lower for kw in final_kws) and not any(kw in text_lower for kw in exclude_kws)
        
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
                
                publish_dt = None
                match_time = re.search(r"(\d{1,2})[/月](\d{1,2})[日\s]*(\d{1,2})[:時](\d{2})", text)
                if match_time:
                    try:
                        m = int(match_time.group(1))
                        d = int(match_time.group(2))
                        hh = int(match_time.group(3))
                        mm = int(match_time.group(4))
                        pub_year = url_year
                        if m < 3 and datetime.now().month >= 11: pub_year += 1
                        elif m > 10 and datetime.now().month <= 2: pub_year -= 1
                        publish_dt = datetime(pub_year, m, d, hh, mm)
                    except ValueError: pass

                video_info = {"title": text, "url": normalized_url, "publish_dt": publish_dt}
                if is_interview and "interview" not in videos: videos["interview"] = video_info
                if is_final and "final" not in videos: videos["final"] = video_info
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
    except Exception: pass
    return conditions

# ==========================================
# LINE Push Message (Flex Message カルーセル)
# ==========================================
# ★ テスト配信用：個別送信（push）仕様 ★
def send_line_flex(header_title, round_num, location, event_date_str, entry_str, page_url, theme_color, extra_info=None, main_image_url=None):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    
    is_cc = "/cc" in page_url
    title_main = f"第{round_num}回" if is_cc else f"第{round_num}戦"
    title_sub = location if is_cc else f"{location}大会"
    
    is_day_before_notice = "明日大会開催" in header_title

    body_contents = [
        {"type": "text", "text": title_main, "weight": "bold", "size": "xl", "color": "#333333"},
        {"type": "text", "text": title_sub, "weight": "bold", "size": "md", "color": "#555555", "wrap": True},
        {"type": "separator", "margin": "md"}
    ]
    
    if is_day_before_notice:
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
                "type": "box", "layout": "vertical", "spacing": "sm", "margin": "md", 
                "contents": [{"type": "text", "text": f"📋 受付時間: {extra_info.get('reception', '情報参照')}", "size": "md", "color": "#D32F2F", "weight": "bold"}, {"type": "text", "text": f"💰 参加費用: {extra_info.get('fee', '情報参照')}", "size": "md", "color": "#D32F2F", "weight": "bold"}]
            })
            if "weather_advice" in extra_info:
                body_contents.append({"type": "separator", "margin": "md"})
                body_contents.append({
                    "type": "box", "layout": "vertical", "spacing": "sm", "margin": "md",
                    "contents": [{"type": "text", "text": "🌤 明日の天候・コンディション", "size": "sm", "color": "#888888", "weight": "bold"}, {"type": "text", "text": extra_info["weather_advice"], "size": "md", "color": "#333333", "wrap": True, "weight": "bold"}]
                })
    else:
        body_contents.append({"type": "box", "layout": "vertical", "spacing": "xs", "margin": "md", "contents": [{"type": "text", "text": "📅 大会開催日", "size": "xs", "color": "#888888"}, {"type": "text", "text": event_date_str, "size": "xl", "color": "#333333"}]})
        body_contents.append({"type": "box", "layout": "vertical", "spacing": "xs", "contents": [{"type": "text", "text": "⏰ エントリー開始日時", "size": "xs", "color": "#888888"}, {"type": "text", "text": entry_str, "size": "md", "color": "#E53935", "wrap": True}]})
        if extra_info:
            body_contents.append({"type": "separator", "margin": "md"})
            if "entry_condition" in extra_info:
                body_contents.append({
                    "type": "box", "layout": "vertical", "spacing": "xs", "margin": "md",
                    "contents": [{"type": "text", "text": "✅ エントリー参加条件", "size": "xs", "color": "#888888", "weight": "bold"}, {"type": "text", "text": extra_info["entry_condition"], "size": "sm", "color": "#D32F2F", "wrap": True, "weight": "bold"}]
                })
                body_contents.append({"type": "separator", "margin": "md"})
            body_contents.append({
                "type": "box", "layout": "vertical", "spacing": "xs", "margin": "md", 
                "contents": [{"type": "text", "text": f"📋 受付時間: {extra_info.get('reception', '情報参照')}", "size": "sm", "color": "#555555"}, {"type": "text", "text": f"💰 参加費用: {extra_info.get('fee', '情報参照')}", "size": "sm", "color": "#555555"}]
            })

    bubble = {
        "type": "bubble", 
        "header": {"type": "box", "layout": "vertical", "backgroundColor": theme_color, "contents": [{"type": "text", "text": f"🎣 {header_title}", "color": "#FFFFFF", "weight": "bold", "size": "xs"}]}, 
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": body_contents}, 
        "footer": {"type": "box", "layout": "vertical", "contents": [{"type": "button", "action": {"type": "uri", "label": "🔗 詳細・エントリー", "uri": page_url}, "style": "primary", "color": theme_color}]}
    }

    if main_image_url:
        bubble["hero"] = {
            "type": "image",
            "url": main_image_url,
            "size": "full",
            "aspectRatio": "16:9",
            "aspectMode": "cover"
        }

    flex_payload = {"to": LINE_USER_ID, "messages": [{"type": "flex", "altText": f"【{header_title}】{title_main} {title_sub}", "contents": {"type": "carousel", "contents": [bubble]}}]}
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
        
        if img_url:
            img_url = re.sub(r'-\d+x\d+(?=\.[a-zA-Z]+$)', '', img_url)

        bg_color = theme_color
        
        is_winner = False
        if any(c in rank for c in ["1", "１", "一", "優勝"]): 
            bg_color = "#D4AF37"
            formatted_name = f"🏆 優勝！{name}"
            header_text = "優勝"
            is_winner = True
        elif any(c in rank for c in ["2", "２", "二", "準"]): 
            bg_color = "#C0C0C0"
            formatted_name = f"🥈 準優勝・{name}"
            header_text = "第２位"
        elif any(c in rank for c in ["3", "３", "三"]): 
            bg_color = "#CD7F32"
            formatted_name = f"🥉 第３位・{name}"
            header_text = "第３位"
        elif any(c in rank for c in ["4", "４", "四", "ラーメン"]): 
            bg_color = theme_color
            formatted_name = "🍜 ラーメン賞"
            header_text = "ラーメン賞"
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

        # ★ 修正: LINE内ブラウザを回避して外部ブラウザ(Safari/Chrome)で強制的に開かせるパラメータを追加
        jump_target = res.get('jump_target', name or rank)
        separator = "&" if "?" in page_url else "?"
        target_url = f"{page_url}{separator}openExternalBrowser=1#:~:text={urllib.parse.quote(jump_target)}"

        bubble = {
            "type": "bubble", 
            "header": {"type": "box", "layout": "vertical", "backgroundColor": bg_color, "contents": [{"type": "text", "text": header_text, "color": "#FFFFFF", "weight": "bold", "size": "md"}]}, 
            "body": {"type": "box", "layout": "vertical", "alignItems": "center", "contents": body_contents}, 
            "footer": {"type": "box", "layout": "vertical", "contents": [{"type": "button", "action": {"type": "uri", "label": "🔗 結果詳細を見る", "uri": target_url}, "style": "primary", "color": bg_color}]}
        }
        
        if img_url:
            bubble["hero"] = {"type": "image", "url": img_url, "size": "full", "aspectRatio": "3:4", "aspectMode": "cover", "backgroundColor": "#FFFFFF"}
        bubbles.append(bubble)
    
    if bubbles:
        try: requests.post(url, headers=headers, json={"to": LINE_USER_ID, "messages": [{"type": "flex", "altText": f"【大会結果】{title_text_str}", "contents": {"type": "carousel", "contents": bubbles}}]}, timeout=TIMEOUT_SEC)
        except Exception: pass

def send_video_line_flex(header_title, round_num, location, video_data, page_url, theme_color, main_image_url=None):
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
        {"type": "box", "layout": "vertical", "spacing": "xs", "margin": "md", "contents": [{"type": "text", "text": vid_title, "weight": "bold", "size": "sm", "color": "#333333", "wrap": True}]}
    ]
    
    bubble = {
        "type": "bubble", 
        "header": {"type": "box", "layout": "vertical", "backgroundColor": theme_color, "contents": [{"type": "text", "text": f"▶️ {header_title}", "color": "#FFFFFF", "weight": "bold", "size": "xs"}]}, 
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": body_contents}, 
        "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [{"type": "button", "action": {"type": "uri", "label": "▶️ 動画を見る", "uri": vid_url}, "style": "primary", "color": theme_color}, {"type": "button", "action": {"type": "uri", "label": "🔗 大会ページへ", "uri": page_url}, "style": "secondary"}]}
    }
    
    if main_image_url:
        bubble["hero"] = {
            "type": "image",
            "url": main_image_url,
            "size": "full",
            "aspectRatio": "16:9",
            "aspectMode": "cover"
        }

    flex_payload = {"to": LINE_USER_ID, "messages": [{"type": "flex", "altText": f"{header_title} {title_main} {title_sub}", "contents": bubble}]}
    try: requests.post(url, headers=headers, json=flex_payload, timeout=TIMEOUT_SEC)
    except Exception: pass

# ==========================================
# ★ デザイン・リンク動作確認用テスト送信
# ==========================================
def run_design_test_only():
    print("=== 全パターンのテスト通知を送信します（あなた専用） ===")
    theme_color = "#4CAF50"
    url = "https://www.kanritsuriba.com/at/2026_21/"
    main_img = "https://www.kanritsuriba.com/at/wp-content/uploads/2026/05/kamihama_1200.jpg"
    
    # リンクジャンプのテスト用ダミーデータ（シルフ大会の選手名）
    result_img = "https://www.kanritsuriba.com/at/wp-content/uploads/2026/05/2617inomata_kouki.jpg"
    dummy_results = [
        {"rank": "優勝", "name": "山下 晃平", "image_url": result_img, "jump_target": "山下晃平", "congrat_msg": "🎉 優勝おめでとうございます！見事な勝利です！"},
        {"rank": "２位", "name": "花森 麟太朗", "image_url": result_img, "jump_target": "花森麟太朗"},
        {"rank": "３位", "name": "向井 一真", "image_url": result_img, "jump_target": "向井一真"},
        {"rank": "ラーメン賞", "name": "", "image_url": result_img, "jump_target": "ラーメン賞"}
    ]
    
    send_result_line_flex("📸【大会結果 リンク動作テスト】", "21", "白州トラウトエリア・シルフ", dummy_results, url, theme_color)
    print("=== テスト通知完了 ===")

def main():
    run_design_test_only()

if __name__ == "__main__":
    main()
