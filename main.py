import os
import re
import sqlite3
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
import requests
from bs4 import BeautifulSoup

# ==========================================
# 安全制御・環境変数設定
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")

DB_PATH = "tournaments.db"
MAX_NOTIFY_LIMIT = 5  # 大量通知ストッパー（安全装置）
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
                advice = f"☀️ 最高気温{int(max_temp)}℃の猛暑予報です。熱中症対策を！"
            elif max_temp <= 10:
                advice = f"❄️ 最高気温{int(max_temp)}℃の冷え込み予報です。防寒対策を！"
            else:
                advice = f"🌤 予想最高気温は{int(max_temp)}℃です。絶好のコンディション！"
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
    match = re.search(r"【?受\s*付】?[：:\s]*(\d{1,2}[:：]\d{2}\s*[\~～\-]\s*\d{1,2}[:：]\d{2}|\d{1,2}[:：]\d{2}\s*より|\d{1,2}[:：]\d{2})", text)
    return match.group(1).strip() if match else "情報参照"

def extract_fee(text):
    match = re.search(r"【?(?:参加費用|参加費|費用)】?[：:\s]*([\d,]+円(?:\s*[\(（][^\)）]*[\)）])?)", text)
    return match.group(1).strip() if match else "情報参照"

# ★修正点①：ジャンプ精度の向上と、インタビュー誤検知の排除
def extract_tournament_results_from_html(html_content):
    results = []
    if not html_content: return results
    soup = BeautifulSoup(html_content, "html.parser")
    
    rank_pattern = re.compile(r"^(優勝|[1-3１-３一二三]位)")
    
    for tag in soup.find_all(['h3', 'h4']):
        text = tag.get_text(strip=True)
        
        # 誤検知防止：「優勝者インタビュー」などはスキップ
        if "インタビュー" in text or "動画" in text:
            continue

        if rank_pattern.match(text):
            rank_match = rank_pattern.match(text)
            rank = rank_match.group(1)
            if "1" in rank or "一" in rank: rank = "優勝"
            elif "2" in rank or "二" in rank: rank = "２位"
            elif "3" in rank or "三" in rank: rank = "３位"

            cleaned_text = re.sub(r"^(優勝|[1-3１-３一二三]位)\s*(?:\[\d+\])?\s*", "", text)
            name_match = re.search(r"^([^\s/【選手]+)", cleaned_text)
            if name_match:
                name = name_match.group(1).strip()
            else:
                name = cleaned_text.strip()
            
            name = re.sub(r"[\s\u3000/・\-]", "", name)

            if not name or len(name) < 2 or "タックル" in name or "コメント" in name:
                continue
                
            # ★追加：リード文を回避するため、「順位[ゼッケン]名前」の部分までを丸ごと切り出す
            jump_text_match = re.search(r"^(優勝|[1-3１-３一二三]位)\s*(?:\[\d+\])?\s*[^\s/【選手]+", text)
            jump_text = jump_text_match.group(0).strip() if jump_text_match else name

            img_url = None
            nxt = tag.find_next_sibling()
            count = 0
            while nxt and count < 4:
                img = nxt.find('img') if hasattr(nxt, 'find') else None
                if not img and nxt.name == 'img':
                    img = nxt
                if img and img.get('src'):
                    img_url = img.get('src')
                    if img_url.startswith('/'):
                        img_url = "https://www.kanritsuriba.com" + img_url
                    break
                nxt = nxt.find_next_sibling()
                count += 1
                
            if not any(r['name'] == name for r in results):
                results.append({"rank": rank, "name": name, "image_url": img_url, "jump_text": jump_text})
                
    return results

def get_winner_congratulations_message(cursor, winner_name, current_round_num):
    clean_winner_name = re.sub(r"[\s\u3000/・\-]", "", winner_name)

    cursor.execute("SELECT round_num, player_name FROM tournament_winners WHERE rank = '優勝'")
    rows = cursor.fetchall()

    past_wins = 0
    is_consecutive = False

    try:
        current_r = int(current_round_num)
        prev_r = current_r - 1
    except ValueError:
        current_r, prev_r = None, None

    for r_num, p_name in rows:
        clean_p_name = re.sub(r"[\s\u3000/・\-]", "", p_name)
        if str(r_num) == str(current_round_num):
            continue
            
        if clean_winner_name == clean_p_name:
            past_wins += 1
            if prev_r is not None and str(prev_r) in str(r_num):
                is_consecutive = True

    if is_consecutive:
        return f"🎉 圧巻の2連続優勝おめでとうございます！強すぎます！🔥"
    elif past_wins >= 1:
        return f"🎉 今季{past_wins + 1}勝目のお祝いを申し上げます！素晴らしい快進撃です！👏"
    else:
        return "🎉 優勝おめでとうございます！見事な勝利です！"

def normalize_youtube_url(url_str):
    if not url_str:
        return url_str
    if url_str.startswith("//"):
        url_str = "https:" + url_str
    embed_match = re.search(r"youtube\.com/embed/([a-zA-Z0-9_-]+)", url_str)
    if embed_match:
        video_id = embed_match.group(1)
        return f"https://www.youtube.com/watch?v={video_id}"
    return url_str

def extract_videos_from_html(html_content):
    videos = {}
    if not html_content: return videos
    soup = BeautifulSoup(html_content, "html.parser")
    
    for tag in soup.find_all(['h3', 'h4', 'h2']):
        text = tag.get_text(strip=True)
        is_interview = "インタビュー" in text
        is_final = "決勝戦" in text or "決勝動画" in text
        
        if is_interview or is_final:
            nxt = tag.find_next_sibling()
            count = 0
            while nxt and count < 3:
                vid_url = None
                iframe = nxt.find('iframe') if hasattr(nxt, 'find') else None
                if not iframe and nxt.name == 'iframe': iframe = nxt
                if iframe and iframe.get('src') and 'youtube' in iframe.get('src'):
                    vid_url = iframe.get('src')
                
                if not vid_url:
                    a_tags = nxt.find_all('a') if hasattr(nxt, 'find_all') else []
                    if nxt.name == 'a': a_tags.append(nxt)
                    for a_tag in a_tags:
                        href = a_tag.get('href', '')
                        if 'youtube.com' in href or 'youtu.be' in href:
                            vid_url = href
                            break
                            
                if vid_url:
                    normalized_url = normalize_youtube_url(vid_url)
                    if is_interview and "interview" not in videos:
                        videos["interview"] = {"title": text, "url": normalized_url}
                    if is_final and "final" not in videos:
                        videos["final"] = {"title": text, "url": normalized_url}
                    break
                nxt = nxt.find_next_sibling()
                count += 1
    return videos

# ==========================================
# LINE Push Message (Flex Message カルーセル)
# ==========================================
def send_line_flex(header_title, round_num, location, event_date_str, entry_str, page_url, theme_color, extra_info=None):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    body_contents = [
        {"type": "text", "text": f"第{round_num}戦", "weight": "bold", "size": "xl", "color": "#333333"},
        {"type": "text", "text": f"{location}大会", "weight": "bold", "size": "md", "color": "#555555", "wrap": True},
        {"type": "separator"},
        {"type": "box", "layout": "vertical", "spacing": "xs", "contents": [{"type": "text", "text": "📅 大会開催日", "size": "xs", "color": "#888888"}, {"type": "text", "text": event_date_str, "size": "xl", "color": "#333333"}]},
        {"type": "box", "layout": "vertical", "spacing": "xs", "contents": [{"type": "text", "text": "⏰ エントリー開始日時", "size": "xs", "color": "#888888"}, {"type": "text", "text": entry_str, "size": "xl", "color": "#E53935"}]}
    ]
    if extra_info:
        body_contents.append({"type": "separator"})
        body_contents.append({"type": "box", "layout": "vertical", "spacing": "xs", "contents": [{"type": "text", "text": f"📋 受付時間: {extra_info.get('reception', '情報参照')}", "size": "sm", "color": "#555555"}, {"type": "text", "text": f"💰 参加費用: {extra_info.get('fee', '情報参照')}", "size": "sm", "color": "#555555"}]})
        if "weather_advice" in extra_info:
            body_contents.append({"type": "separator"})
            body_contents.append({"type": "box", "layout": "vertical", "spacing": "xs", "contents": [{"type": "text", "text": "🌤 明日の天候・応援", "size": "xs", "color": "#888888"}, {"type": "text", "text": extra_info["weather_advice"], "size": "sm", "color": "#333333", "wrap": True}]})

    flex_payload = {"to": LINE_USER_ID, "messages": [{"type": "flex", "altText": f"【{header_title}】第{round_num}戦 {location}大会", "contents": {"type": "carousel", "contents": [{"type": "bubble", "header": {"type": "box", "layout": "vertical", "backgroundColor": theme_color, "contents": [{"type": "text", "text": f"🎣 {header_title}", "color": "#FFFFFF", "weight": "bold", "size": "xs"}]}, "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": body_contents}, "footer": {"type": "box", "layout": "vertical", "contents": [{"type": "button", "action": {"type": "uri", "label": "🔗 詳細・エントリー", "uri": page_url}, "style": "primary", "color": theme_color}]}}]}}]}
    try: requests.post(url, headers=headers, json=flex_payload, timeout=TIMEOUT_SEC)
    except Exception: pass

# ★修正点②：ジャンプ先URLを「順位[番号]名前」に最適化
def send_result_line_flex(header_title, round_num, location, results, page_url, theme_color):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    bubbles = []
    for res in results[:10]:
        rank = res['rank']
        name = res['name']
        img_url = res.get('image_url')
        bg_color, icon_emoji = theme_color, "🏅"
        
        is_winner = False
        if "優勝" in rank or "1" in rank or "１" in rank: 
            bg_color, icon_emoji = "#D4AF37", "🏆"
            is_winner = True
        elif "2" in rank or "２" in rank: bg_color, icon_emoji = "#C0C0C0", "🥈"
        elif "3" in rank or "３" in rank: bg_color, icon_emoji = "#CD7F32", "🥉"

        body_contents = [
            {"type": "text", "text": icon_emoji, "size": "4xl", "margin": "md"},
            {"type": "text", "text": name, "weight": "bold", "size": "xl", "margin": "md", "color": "#333333", "wrap": True},
            {"type": "text", "text": f"第{round_num}戦 {location}", "size": "xs", "color": "#888888", "margin": "sm", "wrap": True}
        ]

        if is_winner and res.get('congrat_msg'):
            body_contents.append({"type": "separator", "margin": "md"})
            body_contents.append({"type": "text", "text": res['congrat_msg'], "size": "xs", "color": "#D32F2F", "weight": "bold", "margin": "md", "wrap": True})

        # 抽出した「順位＋ゼッケン＋名前」を使用してジャンプURLを生成（リード文の誤爆を防止）
        jump_text = res.get('jump_text', name)
        target_url = f"{page_url}#:~:text={urllib.parse.quote(jump_text)}"

        bubble = {
            "type": "bubble", 
            "header": {"type": "box", "layout": "vertical", "backgroundColor": bg_color, "contents": [{"type": "text", "text": f"{rank}", "color": "#FFFFFF", "weight": "bold", "size": "md"}]}, 
            "body": {"type": "box", "layout": "vertical", "alignItems": "center", "contents": body_contents}, 
            "footer": {"type": "box", "layout": "vertical", "contents": [{"type": "button", "action": {"type": "uri", "label": "🔗 結果詳細を見る", "uri": target_url}, "style": "primary", "color": bg_color}]}
        }
        
        if img_url:
            bubble["hero"] = {
                "type": "image", 
                "url": img_url, 
                "size": "full", 
                "aspectRatio": "3:4", 
                "aspectMode": "fit", 
                "backgroundColor": "#FFFFFF"
            }
            bubble["body"]["contents"].pop(0)
            
        bubbles.append(bubble)

    try: requests.post(url, headers=headers, json={"to": LINE_USER_ID, "messages": [{"type": "flex", "altText": f"【大会結果】第{round_num}戦 {location}", "contents": {"type": "carousel", "contents": bubbles}}]}, timeout=TIMEOUT_SEC)
    except Exception: pass

def send_video_line_flex(header_title, round_num, location, video_data, page_url, theme_color):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    
    vid_title = video_data.get("title", "動画が公開されました")
    vid_url = video_data.get("url", page_url)
    
    body_contents = [
        {"type": "text", "text": f"第{round_num}戦", "weight": "bold", "size": "xl", "color": "#333333"},
        {"type": "text", "text": f"{location}大会", "weight": "bold", "size": "md", "color": "#555555", "wrap": True},
        {"type": "separator"},
        {"type": "box", "layout": "vertical", "spacing": "xs", "margin": "md", "contents": [{"type": "text", "text": vid_title, "weight": "bold", "size": "sm", "color": "#E53935", "wrap": True}]}
    ]
    flex_payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "flex", "altText": f"{header_title} 第{round_num}戦 {location}", "contents": {"type": "bubble", "header": {"type": "box", "layout": "vertical", "backgroundColor": theme_color, "contents": [{"type": "text", "text": f"▶️ {header_title}", "color": "#FFFFFF", "weight": "bold", "size": "xs"}]}, "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": body_contents}, "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [{"type": "button", "action": {"type": "uri", "label": "▶️ 動画を見る", "uri": vid_url}, "style": "primary", "color": "#D32F2F"}, {"type": "button", "action": {"type": "uri", "label": "🔗 大会ページへ", "uri": page_url}, "style": "secondary"}]}}}]
    }
    try: requests.post(url, headers=headers, json=flex_payload, timeout=TIMEOUT_SEC)
    except Exception: pass

def send_simple_text(text_message):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    try: requests.post(url, headers=headers, json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": text_message}]}, timeout=TIMEOUT_SEC)
    except Exception: pass

def fetch_page_data(url):
    res = fetch_url(url)
    if res and res.status_code == 200:
        try:
            soup = BeautifulSoup(res.text, "html.parser")
            content_area = soup.find("div", class_="entry-content") or soup
            return content_area.get_text(separator=" ", strip=True), str(content_area)
        except Exception: pass
    return "", ""

# ==========================================
# メイン監視処理（ダイレクトジャンプ・テスト再配信用）
# ==========================================
def main():
    now = get_jst_now()
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 全自動監視処理（ジャンプ精度向上・テスト再配信モード）を開始します。")
    
    conn, is_initial_setup = init_db()
    c = conn.cursor()

    # ★テスト用：第19戦の通知フラグを強制的に未送信(0)に戻し、誤検知データを削除
    c.execute("UPDATE tournaments SET notified_result = 0 WHERE round_num = '19'")
    c.execute("DELETE FROM tournament_winners WHERE player_name = '者インタビュー'")
    conn.commit()
    print("🧪 【テスト発火】第19戦の通知フラグをリセットしました（ジャンプ精度確認用）。")

    current_year = now.year
    is_night_mode = False # テスト実行のためおやすみモードを強制解除
    
    target_years = [current_year]
    if now.month <= 3: target_years.append(current_year - 1)
    if now.month >= 9: target_years.append(current_year + 1)
    target_years = sorted(list(set(target_years)))

    urls_to_check = []
    for year in target_years:
        tag_url = f"https://www.kanritsuriba.com/at/tag/areatournament{year}/"
        res = fetch_url(tag_url)
        if not res: continue
        try:
            soup = BeautifulSoup(res.text, "html.parser")
            pattern = re.compile(rf"/at/{year}_\d+/")
            links = soup.find_all("a", href=pattern)
            for a in links:
                href = a["href"]
                full_url = href if href.startswith("http") else f"https://www.kanritsuriba.com{href}"
                urls_to_check.append(full_url)
        except Exception: pass

    urls_to_check = list(set(urls_to_check))
    notify_queue = []
    db_updates = []

    for url in urls_to_check:
        try:
            c.execute("SELECT notified_video_final, event_datetime FROM tournaments WHERE url = ?", (url,))
            check_row = c.fetchone()
            if check_row:
                n_video_final = check_row[0]
                event_dt_str = check_row[1]
                # テスト対象(第19戦)以外はスキップ
                if "2026_19" not in url and n_video_final == 1:
                    continue

            match_year = re.search(r"/at/(\d{4})_", url)
            url_year = int(match_year.group(1)) if match_year else current_year

            time.sleep(0.5)
            text_p1, html_p1 = fetch_page_data(url)
            sub_url = url.rstrip("/") + "/2/"
            time.sleep(0.5)
            text_p2, html_p2 = fetch_page_data(sub_url)

            combined_text = (text_p2 + " " + text_p1).strip()
            combined_html = html_p2 + html_p1
            if not combined_text: continue

            match_title = re.search(r"第(\d+)戦([^\s大会を]+)", combined_text)
            round_num = match_title.group(1) if match_title else "不明"
            location = match_title.group(2) if match_title else "対象会場"

            event_dt, event_date_str = extract_event_date_info(combined_text, url_year)
            entry_dt, entry_str = parse_entry_datetime(text_p2, url_year)
            if not entry_dt: entry_str = parse_entry_datetime(text_p1, url_year)

            reception_time = extract_reception_time(combined_text)
            fee = extract_fee(combined_text)
            theme_color = get_theme_color(location)

            results_data = extract_tournament_results_from_html(combined_html)
            videos_data = extract_videos_from_html(combined_html)

            winner_name = ""
            for r in results_data:
                if r['rank'] == "優勝":
                    winner_name = r['name']
                    r['congrat_msg'] = get_winner_congratulations_message(c, winner_name, round_num)

                c.execute(
                    "INSERT OR REPLACE INTO tournament_winners (url, round_num, rank, player_name) VALUES (?, ?, ?, ?)",
                    (url, round_num, r['rank'], r['name'])
                )

            c.execute("SELECT * FROM tournaments WHERE url = ?", (url,))
            row = c.fetchone()

            if row:
                (db_url, db_round, db_loc, db_event_date, db_event_dt_str, db_entry_dt_str, db_entry_str, db_reception, db_fee, db_text, db_cancelled, n_new, n_1d, n_1h, n_15m, n_event_1d, n_just, n_after_24h, n_result, n_video_int, n_video_fin, db_winner) = row

                # テスト用に第19戦の送信済みフラグを再確認
                if "2026_19" in url and results_data:
                    # 強制的に結果をキューに追加
                    notify_queue.append({"type": "result", "header": "🎊【大会結果発表！】", "round_num": db_round, "location": db_loc, "results": results_data, "url": url, "theme_color": theme_color})
                    db_updates.append(("UPDATE tournaments SET notified_result = 1 WHERE url = ?", (url,)))

        except Exception as e:
            pass

    for query, params in db_updates:
        c.execute(query, params)

    conn.commit()
    conn.close()

    if not is_initial_setup and notify_queue:
        if len(notify_queue) > MAX_NOTIFY_LIMIT:
            send_simple_text("⚠️【システム通知】多数の新着・更新を検知したため連続送信をストップしました。サイトをご確認ください。")
        else:
            for item in notify_queue:
                if item.get("type") == "result":
                    send_result_line_flex(item["header"], item["round_num"], item["location"], item["results"], item["url"], item["theme_color"])
                elif item.get("type") == "video":
                    send_video_line_flex(item["header"], item["round_num"], item["location"], item["video_data"], item["url"], item["theme_color"])
                else:
                    send_line_flex(item["header"], item["round_num"], item["location"], item["event_date_str"], item["entry_str"], item["url"], item["theme_color"], item.get("extra_info"))

    print("テスト配信処理が完了しました。確認後、必ず【本番運用版】のコードに戻してください！")

if __name__ == "__main__":
    main()
