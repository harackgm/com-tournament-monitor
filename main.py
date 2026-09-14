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
TIMEOUT_SEC = 10  # 通信タイムアウト時間(10秒)

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
            return text_space, res.text, title_text, text_lines
        except Exception: pass
    return "", "", "", []

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
            except ValueError: pass
                
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

def extract_tournament_results_from_html(html_content):
    results = []
    if not html_content: return results
    soup = BeautifulSoup(html_content, "html.parser")
    
    rank_pattern = re.compile(r"^(優勝|準優勝|[1-3１-３一二三]位)")
    
    # <li> タグからの抽出（速報用）
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
    
    # <h3> <h4> タグからの抽出（詳細情報の紐付け）
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
                # ★ 修正: ページ先頭でジャンプが止まるのを防ぐため、ジャンプ先を見出しの「全文」に上書きする
                existing['jump_target'] = exact_heading
                if not existing.get('image_url') and img_url:
                    existing['image_url'] = img_url
            else:
                results.append({"rank": rank, "name": name, "image_url": img_url, "jump_target": exact_heading})
                
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

# ==========================================
# LINE Push Message (Flex Message カルーセル)
# ==========================================
# ★ テスト配信用：個別送信（push）仕様 ★
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

        jump_target = res.get('jump_target', name or rank)
        separator = "&" if "?" in page_url else "?"
        # ★ ここで jump_target（見出し全文）がURLにエンコードされて組み込まれる
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


# ==========================================
# ★ テスト環境用：実データ取得＆送信先を強制変更
# ==========================================
def main():
    print("=== 実際のHPデータを用いたテスト通知（あなた専用） ===")
    conn, _ = init_db()
    c = conn.cursor()
    
    # 💡 実際のシルフ大会のURLをスクレイピング
    url = "https://www.kanritsuriba.com/at/2026_21/"
    round_num = "21"
    location = "白州トラウトエリア・シルフ"
    theme_color = get_theme_color(location)
    
    print(f"🔍 ページ解析中: {url}")
    text_p1, html_p1, title_p1, lines_p1 = fetch_page_data(url)
    sub_url = url.rstrip("/") + "/2/"
    text_p2, html_p2, title_p2, lines_p2 = fetch_page_data(sub_url)

    combined_html = html_p2 + html_p1
    
    results_data = extract_tournament_results_from_html(combined_html)
    
    if results_data:
        podium_img_url = extract_podium_image(combined_html)
        for r in results_data:
            if not r.get('image_url') and podium_img_url and r['rank'] in ['優勝', '２位', '３位']:
                r['image_url'] = podium_img_url
                
        for r in results_data:
            if r['rank'] == "優勝":
                r['congrat_msg'] = get_winner_congratulations_message(c, r['name'], round_num)
                
        print(f"✅ 抽出成功: {len(results_data)}件のデータを送信します。")
        send_result_line_flex("📸【大会結果 リンク動作テスト】", round_num, location, results_data, url, theme_color)
    else:
        print("⚠️ 大会結果が見つかりませんでした。")
        
    conn.close()
    print("=== テスト通知完了 ===")

if __name__ == "__main__":
    main()
