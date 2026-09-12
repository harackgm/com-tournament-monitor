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
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")  # ★ テスト配信用

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
                return None
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
            
            # ★HTML全体を返すことで、og:imageタグ等を検索できるように修正
            return text_space, res.text, title_text, text_lines
        except Exception: pass
    return "", "", "", []

# ==========================================
# テキスト解析ヘルパー関数群
# ==========================================
def extract_main_image(html_content):
    if not html_content: return None
    soup = BeautifulSoup(html_content, "html.parser")
    
    # 💡 優先1: OGP画像（SNS共有用の綺麗な横長アイキャッチ）
    og_img = soup.find('meta', property='og:image')
    if og_img and og_img.get('content'):
        src = og_img.get('content')
        return re.sub(r'-\d+x\d+(?=\.[a-zA-Z]+$)', '', src)
        
    # 💡 優先2: eye-catchクラス内の画像
    eye_catch = soup.find(class_='eye-catch')
    if eye_catch:
        img = eye_catch.find('img')
        if img and img.get('src'):
            src = img.get('src')
            if src.startswith('/'): src = "https://www.kanritsuriba.com" + src
            return re.sub(r'-\d+x\d+(?=\.[a-zA-Z]+$)', '', src)

    # 💡 優先3: entry-content内の最初の画像
    content_area = soup.find("div", class_="entry-content")
    if content_area:
        img = content_area.find('img')
        if img and img.get('src'):
            src = img.get('src')
            if src.startswith('/'):
                src = "https://www.kanritsuriba.com" + src
            return re.sub(r'-\d+x\d+(?=\.[a-zA-Z]+$)', '', src)
    return None

# ==========================================
# LINE Push Message (Flex Message カルーセル)
# ==========================================
# ★ テスト用：個別送信（push）仕様 ★
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

    bubble = {
        "type": "bubble", 
        "header": {"type": "box", "layout": "vertical", "backgroundColor": theme_color, "contents": [{"type": "text", "text": f"🎣 {header_title}", "color": "#FFFFFF", "weight": "bold", "size": "xs"}]}, 
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": body_contents}, 
        "footer": {"type": "box", "layout": "vertical", "contents": [{"type": "button", "action": {"type": "uri", "label": "🔗 詳細・エントリー", "uri": page_url}, "style": "primary", "color": theme_color}]}
    }

    # 💡 修正: 横長(16:9)の cover（枠いっぱいに広げる）設定で綺麗に収める
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
            formatted_name = f"🥉 ３位・{name}"
            header_text = "第３位"
        elif "ラーメン" in rank or "4" in rank or "４" in rank: 
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
        target_url = f"{page_url}#:~:text={urllib.parse.quote(jump_target)}"

        bubble = {
            "type": "bubble", 
            "header": {"type": "box", "layout": "vertical", "backgroundColor": bg_color, "contents": [{"type": "text", "text": header_text, "color": "#FFFFFF", "weight": "bold", "size": "md"}]}, 
            "body": {"type": "box", "layout": "vertical", "alignItems": "center", "contents": body_contents}, 
            "footer": {"type": "box", "layout": "vertical", "contents": [{"type": "button", "action": {"type": "uri", "label": "🔗 結果詳細を見る", "uri": target_url}, "style": "primary", "color": bg_color}]}
        }
        # 💡 修正: 元の全画面表示（cover）にし、縦長枠（3:4）に戻して額縁を排除
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
    
    # 💡 修正: 横長(16:9)の cover（枠いっぱいに広げる）設定
    if main_image_url:
        bubble["hero"] = {
            "type": "image",
            "url": main_image_url,
            "size": "full",
            "aspectRatio": "16:9",
            "aspectMode": "cover",
            "backgroundColor": "#FFFFFF"
        }

    flex_payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "flex", "altText": f"{header_title} {title_main} {title_sub}", "contents": bubble}]
    }
    try: requests.post(url, headers=headers, json=flex_payload, timeout=TIMEOUT_SEC)
    except Exception: pass


# ==========================================
# ★ デザイン確認用：全パターン強制テスト送信
# ==========================================
def run_design_test_only():
    print("=== 全パターンのテスト通知を送信します（あなた専用） ===")
    theme_color = "#4CAF50" # シルフの色
    url = "https://www.kanritsuriba.com/at/dummy"
    
    # 💡 シルフの横長風景画のURLをテスト用に指定
    main_img = "https://www.kanritsuriba.com/at/wp-content/uploads/2026/05/2617fff.jpg"
    
    extra_info_base = {
        "reception": "6:00-6:30", 
        "fee": "8,200円", 
        "entry_condition": "[1次対象者]\n今回初めて「エリアトラウトのルアー大会」に参加する方"
    }
    
    extra_info_weather = extra_info_base.copy()
    extra_info_weather["weather_advice"] = "🌧 雨予報 (降水量: 1.7mm)\n🌡 気温: 最高25℃ / 最低18℃\n🌬 最大風速: 9.2m/s\n\nレインウェアと防水対策をお忘れなく！\n🔥 日頃の練習の成果を発揮し、優勝を目指してください！"

    # 1. 新規・更新・中止
    send_line_flex("🆕【新規大会開催予定】", "21", "白州トラウトエリア・シルフ", "2026年09月12日(土)", "10/01(木) 20:00", url, theme_color, extra_info_base, main_img)
    time.sleep(1)
    send_line_flex("📢【大会情報更新】", "21", "白州トラウトエリア・シルフ", "2026年09月12日(土)", "10/01(木) 20:00", url, theme_color, extra_info_base, main_img)
    time.sleep(1)
    send_line_flex("🚨【緊急：開催中止・変更】", "21", "白州トラウトエリア・シルフ", "2026年09月12日(土)", "開催中止・変更が発生しました", url, "#D32F2F", None, main_img)
    time.sleep(1)

    # 2. エントリーリマインド系
    send_line_flex("【明日エントリー開始】", "21", "白州トラウトエリア・シルフ", "2026年09月12日(土)", "10/01(木) 20:00", url, theme_color, extra_info_base, main_img)
    time.sleep(1)
    send_line_flex("⏰【1時間前リマインド】", "21", "白州トラウトエリア・シルフ", "2026年09月12日(土)", "10/01(木) 20:00", url, theme_color, extra_info_base, main_img)
    time.sleep(1)
    send_line_flex("🔥【15分前直前リマインド】", "21", "白州トラウトエリア・シルフ", "2026年09月12日(土)", "10/01(木) 20:00", url, theme_color, extra_info_base, main_img)
    time.sleep(1)
    send_line_flex("🏁【エントリー開始！】", "21", "白州トラウトエリア・シルフ", "2026年09月12日(土)", "10/01(木) 20:00", url, theme_color, extra_info_base, main_img)
    time.sleep(1)
    send_line_flex("⚠️【エントリー忘れ防止】エントリーが開始されています！", "21", "白州トラウトエリア・シルフ", "2026年09月12日(土)", "10/01(木) 20:00", url, theme_color, extra_info_base, main_img)
    time.sleep(1)

    # 3. 大会前日案内（天気付き）
    send_line_flex("📅【明日大会開催！直前案内】", "21", "白州トラウトエリア・シルフ", "2026年09月12日(土)", "10/01(木) 20:00", url, theme_color, extra_info_weather, main_img)
    time.sleep(1)

    # 4. 大会結果（写真は結果専用の画像URL）
    result_img = "https://www.kanritsuriba.com/at/wp-content/uploads/2026/05/2617inomata_kouki.jpg"
    dummy_results = [
        {"rank": "優勝", "name": "猪俣 広希", "image_url": result_img, "jump_target": "猪俣 広希", "congrat_msg": "🎉 優勝おめでとうございます！見事な勝利です！"},
        {"rank": "２位", "name": "阿久津 達也", "image_url": result_img, "jump_target": "阿久津 達也"},
        {"rank": "３位", "name": "佐藤 実", "image_url": result_img, "jump_target": "佐藤 実"},
        {"rank": "ラーメン賞", "name": "", "image_url": result_img, "jump_target": "ラーメン賞"}
    ]
    send_result_line_flex("📣【大会結果 速報！】", "21", "白州トラウトエリア・シルフ", dummy_results, url, theme_color)
    time.sleep(1)
    send_result_line_flex("📸【大会結果 写真追加！】", "21", "白州トラウトエリア・シルフ", dummy_results, url, theme_color)
    time.sleep(1)

    # 5. 動画
    dummy_video_int = {"title": "優勝者インタビュー", "url": "https://www.youtube.com/watch?v=dummy"}
    dummy_video_fin = {"title": "決勝戦", "url": "https://www.youtube.com/watch?v=dummy"}
    send_video_line_flex("🎤【優勝者インタビュー公開】", "21", "白州トラウトエリア・シルフ", dummy_video_int, url, theme_color, main_img)
    time.sleep(1)
    send_video_line_flex("🎥【決勝戦 動画公開】", "21", "白州トラウトエリア・シルフ", dummy_video_fin, url, theme_color, main_img)
    time.sleep(1)
    
    print("=== テスト通知完了 ===")

def main():
    run_design_test_only()

if __name__ == "__main__":
    main()
