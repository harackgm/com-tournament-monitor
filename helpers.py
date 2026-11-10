import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
import requests
from bs4 import BeautifulSoup

# 通信タイムアウト時間（10秒）
TIMEOUT_SEC = 10

# 主要釣り場の座標マッピング（Open-Meteo天気API用）
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
# 日本時間（JST）の取得
# ==========================================
def get_jst_now():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).replace(tzinfo=None)

# ==========================================
# ネットワーク・HTML取得処理（負荷軽減待機付き）
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
            return text_space, res.text, title_text, text_lines
        except Exception:
            pass
    return "", "", "", []

# ==========================================
# YouTube動画判定（oEmbed連携）
# ==========================================
def is_youtube_video_available(youtube_url):
    if not youtube_url:
        return False
    
    oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(youtube_url)}&format=json"
    try:
        oembed_res = requests.get(oembed_url, timeout=5)
        if oembed_res.status_code != 200:
            return False
    except Exception:
        return False

    res = fetch_url(youtube_url)
    if not res or res.status_code != 200:
        return False
    html = res.text
    
    unavailable_kws = [
        "isUpcoming", "公開予定", "ライブ配信まで", "プレミア公開", 
        "Private video", "非公開", "Video unavailable", "Sign in to confirm you"
    ]
    for kw in unavailable_kws:
        if kw in html:
            return False
            
    return True

# ==========================================
# 天気・コンディションアドバイス
# ==========================================
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

            if w_code in [51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99] or (precip > 1.0):
                w_text = f"🌧 雨予報 (降水量: {precip}mm)"
            elif w_code in [3, 45, 48]:
                w_text = "☁️ 曇り予報"
            else:
                w_text = "🌤 晴れ/概ね晴れ"
                
            advice = f"{w_text}\n🌡 気温: 最高{int(max_temp)}℃ / 最低{int(min_temp)}℃\n🌬 最大風速: {wind}m/s\n\n"
            
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
    elif any(kw in location_name for kw in ["三重", "岐阜", "滋賀", "サンクチュアリ", "サンク", "瑞浪", "平谷", "醒井", "千早川", "大阪"]):
        return "#009688"  
    return "#607D8B"

# ==========================================
# 画像・テキスト解析処理
# ==========================================
def extract_landscape_image(html_p1, html_p2):
    target_html = html_p2 if html_p2 else html_p1
    if not target_html:
        return None
    soup = BeautifulSoup(target_html, "html.parser")
    content_area = soup.find("div", class_="entry-content")
    if not content_area:
        return None
    
    for img in content_area.find_all('img'):
        src = img.get('src')
        if not src:
            continue
        alt = img.get('alt', '')
        if "優勝" in alt or "表彰台" in alt:
            continue
        width = img.get('width')
        height = img.get('height')
        if width and height:
            try:
                w = int(re.sub(r'\D', '', str(width)))
                h = int(re.sub(r'\D', '', str(height)))
                if w > h:
                    if src.startswith('/'):
                        src = "https://www.kanritsuriba.com" + src
                    return re.sub(r'-\d+x\d+(?=\.[a-zA-Z]+$)', '', src)
            except ValueError:
                pass
                
    for img in content_area.find_all('img'):
        src = img.get('src')
        if not src:
            continue
        alt = img.get('alt', '')
        if "優勝" in alt or "表彰台" in alt:
            continue
        if src.startswith('/'):
            src = "https://www.kanritsuriba.com" + src
        return re.sub(r'-\d+x\d+(?=\.[a-zA-Z]+$)', '', src)
    return None

def extract_podium_image(html_content):
    if not html_content:
        return None
    soup = BeautifulSoup(html_content, "html.parser")
    for li in soup.find_all('li'):
        img_tag = li.find('img')
        if img_tag and img_tag.get('src'):
            text = li.get_text(strip=True)
            if "表彰台" in text:
                src = img_tag.get('src')
                if src.startswith('/'):
                    src = "https://www.kanritsuriba.com" + src
                return re.sub(r'-\d+x\d+(?=\.[a-zA-Z]+$)', '', src)
    return None

def extract_event_date_info(text, year):
    matches = re.finditer(r"(?:(\d{4})[年/.-])?\s*(\d{1,2})[月/.-](\d{1,2})[日]?", text)
    for match in matches:
        end_idx = match.end()
        if re.search(r"^\s*(\d{1,2}[:時]|公開|更新|締切|開始|まで)", text[end_idx:end_idx+10]):
            continue
            
        y = int(match.group(1)) if match.group(1) else year
        m = int(match.group(2))
        d = int(match.group(3))
        if 1 <= m <= 12 and 1 <= d <= 31:
            try:
                dt = datetime(y, m, d)
                w = ["月", "火", "水", "木", "金", "土", "日"][dt.weekday()]
                return dt, f"{y}年{m:02d}月{d:02d}日({w})"
            except ValueError:
                pass
    return None, "開催日未定"

# ★ 千早川戦の年ズレ（2025年化）を完全解決した判定関数
def parse_entry_datetime(text, year, event_dt=None, round_num=None):
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    pattern_strict = re.search(r"(?:インターネットエントリー|エントリー|受付|募集)[^\d\n]{0,50}?(?:(\d{1,2})月(\d{1,2})日|(\d{1,2})[/.-](\d{1,2}))[^\d\n]{0,30}?(\d{1,2}):(\d{2})", text)
    pattern = pattern_strict or re.search(r"(?:(\d{1,2})月(\d{1,2})日|(\d{1,2})[/.-](\d{1,2}))[^\d\n]{0,20}?(\d{1,2}):(\d{2})", text)

    if pattern:
        m = int(pattern.group(1) or pattern.group(3))
        d = int(pattern.group(2) or pattern.group(4))
        hh = int(pattern.group(5))
        mm = int(pattern.group(6))
        
        # 開催日が判明している場合は開催月との比較で年度を正確判定
        if event_dt:
            if m > event_dt.month:
                entry_year = event_dt.year - 1
            else:
                entry_year = event_dt.year
        else:
            try:
                r = int(re.sub(r'\D', '', str(round_num)))
                if r <= 3 and m >= 11:
                    entry_year = year - 1
                else:
                    entry_year = year
            except Exception:
                entry_year = year

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
    if not html_content:
        return results
    soup = BeautifulSoup(html_content, "html.parser")
    
    rank_pattern = re.compile(r"^(優勝|準優勝|[1-3１-３一二三]位)")
    
    for li in soup.find_all('li'):
        text = li.get_text(strip=True)
        if "インタビュー" in text or "動画" in text:
            continue

        img_tag = li.find('img')
        src = None
        if img_tag and img_tag.get('src'):
            src = img_tag.get('src')
            if src.startswith('/'):
                src = "https://www.kanritsuriba.com" + src
            src = re.sub(r'-\d+x\d+(?=\.[a-zA-Z]+$)', '', src)

        match = re.search(r"^(優勝|準優勝|[1-3１-３一二三]位)[:：\s]*([^\s/【選手]+)", text)
        if match:
            rank = match.group(1)
            name = match.group(2).strip()
            name = re.sub(r"[\s\u3000/・\-]", "", name)
            if "1" in rank or "一" in rank:
                rank = "優勝"
            elif "2" in rank or "二" in rank or "準優勝" in rank:
                rank = "２位"
            elif "3" in rank or "三" in rank:
                rank = "３位"
            
            if name and len(name) >= 2 and "タックル" not in name:
                if not any(r['name'] == name for r in results):
                    results.append({"rank": rank, "name": name, "image_url": src, "jump_target": f"{name}選手のタックル"})
        
        if "ラーメン賞" in text or "４位" in text or "4位" in text:
            if not any(r['rank'] == "ラーメン賞" for r in results):
                results.append({"rank": "ラーメン賞", "name": "", "image_url": src, "jump_target": "ラーメン賞"})
    
    for tag in soup.find_all(['h3', 'h4']):
        exact_heading = tag.get_text(strip=True)
        if "インタビュー" in exact_heading or "動画" in exact_heading:
            continue
        if rank_pattern.match(exact_heading):
            rank_match = rank_pattern.match(exact_heading)
            rank = rank_match.group(1)
            if "1" in rank or "一" in rank:
                rank = "優勝"
            elif "2" in rank or "二" in rank or "準優勝" in rank:
                rank = "２位"
            elif "3" in rank or "三" in rank:
                rank = "３位"

            cleaned_text = re.sub(r"^(優勝|準優勝|[1-3１-３一二三]位)\s*(?:\[\d+\])?\s*", "", exact_heading)
            name_match = re.search(r"^([^\s/【選手]+)", cleaned_text)
            name = name_match.group(1).strip() if name_match else cleaned_text.strip()
            name = re.sub(r"[\s\u3000/・\-]", "", name)

            if not name or len(name) < 2 or "タックル" in name or "コメント" in name:
                continue

            img_url = None
            nxt = tag.find_next_sibling()
            count = 0
            while nxt and count < 4:
                img = nxt.find('img') if hasattr(nxt, 'find') else None
                if not img and nxt.name == 'img':
                    img = nxt
                if img and img.get('src'):
                    src = img.get('src')
                    if src.startswith('/'):
                        src = "https://www.kanritsuriba.com" + src
                    img_url = re.sub(r'-\d+x\d+(?=\.[a-zA-Z]+$)', '', src)
                    break
                nxt = nxt.find_next_sibling()
                count += 1
                
            eng_match = re.search(r'[A-Za-z][A-Za-z\s]{3,}$', exact_heading)
            if eng_match:
                safe_target = eng_match.group(0).strip()
            else:
                safe_target = exact_heading
                
            existing = next((r for r in results if r['name'] == name), None)
            if existing:
                existing['jump_target'] = safe_target
                if not existing.get('image_url') and img_url:
                    existing['image_url'] = img_url
            else:
                results.append({"rank": rank, "name": name, "image_url": img_url, "jump_target": safe_target})
                
    def get_rank_order(rank):
        if "優勝" in rank:
            return 1
        if "２位" in rank or "2位" in rank:
            return 2
        if "３位" in rank or "3位" in rank:
            return 3
        if "ラーメン賞" in rank:
            return 4
        return 99
    
    results.sort(key=lambda x: get_rank_order(x['rank']))
    return results

def get_winner_congratulations_message(cursor, winner_name, current_round_num):
    if not winner_name:
        return ""
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
    embed_match = re.search(r"(?:youtube\.com/embed/|youtu\.be/|youtube\.com/watch\?v=)([a-zA-Z0-9_-]+)", url_str)
    if embed_match:
        return f"https://www.youtube.com/watch?v={embed_match.group(1)}"
    return url_str

def extract_videos_from_html(html_content, url_year):
    videos = {}
    if not html_content:
        return videos
    soup = BeautifulSoup(html_content, "html.parser")
    
    interview_kws = ["インタビュー", "優勝者の声", "コメント", "ヒーロー", "winner"]
    final_kws = ["決勝", "ファイナル", "優勝決定戦", "final"]
    exclude_kws = ["準決勝", "予選", "セミファイナル", "3位決定戦", "三位決定戦", "準々決勝", "semi"]

    for tag in soup.find_all(['h2', 'h3', 'h4']):
        text = tag.get_text(strip=True)
        text_lower = text.lower()
        is_interview = any(kw in text_lower for kw in interview_kws)
        is_final = any(kw in text_lower for kw in final_kws) and not any(kw in text_lower for kw in exclude_kws)
        
        if is_interview or is_final:
            vid_url = None
            nxt = tag.find_next_sibling()
            
            while nxt and nxt.name not in ['h2', 'h3', 'h4']:
                if nxt.name == 'iframe':
                    src = nxt.get('src', '')
                    if 'youtube' in src or 'youtu.be' in src:
                        vid_url = src
                else:
                    iframe = nxt.find('iframe') if hasattr(nxt, 'find') else None
                    if iframe:
                        src = iframe.get('src', '')
                        if 'youtube' in src or 'youtu.be' in src:
                            vid_url = src
                
                if not vid_url:
                    if nxt.name == 'a':
                        target_str = f"{nxt.get('href', '')} {nxt.get('title', '')} {nxt.get_text()}"
                        if 'youtube.com' in target_str or 'youtu.be' in target_str:
                            vid_url = nxt.get('href', '') or nxt.get('title', '')
                    else:
                        for a_tag in (nxt.find_all('a') if hasattr(nxt, 'find_all') else []):
                            target_str = f"{a_tag.get('href', '')} {a_tag.get('title', '')} {a_tag.get_text()}"
                            if 'youtube.com' in target_str or 'youtu.be' in target_str:
                                vid_url = a_tag.get('href', '') or a_tag.get('title', '')
                                break
                if vid_url:
                    break
                nxt = nxt.find_next_sibling()
            
            if vid_url:
                normalized_url = normalize_youtube_url(vid_url)
                publish_dt = None
                match_time = re.search(r"(\d{1,2})[/月](\d{1,2})[^0-9]*?(\d{1,2})[:時](\d{2})", text)
                if match_time:
                    try:
                        m = int(match_time.group(1))
                        d = int(match_time.group(2))
                        hh = int(match_time.group(3))
                        mm = int(match_time.group(4))
                        pub_year = url_year
                        if m < 3 and datetime.now().month >= 11:
                            pub_year += 1
                        elif m > 10 and datetime.now().month <= 2:
                            pub_year -= 1
                        publish_dt = datetime(pub_year, m, d, hh, mm)
                    except ValueError:
                        pass

                video_info = {"title": text, "url": normalized_url, "publish_dt": publish_dt}
                if is_interview and "interview" not in videos:
                    videos["interview"] = video_info
                if is_final and "final" not in videos:
                    videos["final"] = video_info
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
            if len(text_lines) < 2:
                continue
            
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

# ==========================================
# LINE送信部（テスト/本番 安全スイッチ対応）
# ==========================================
def dispatch_line_message(payload_messages, access_token, user_id, is_test_mode):
    if not access_token:
        return
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
    
    if is_test_mode:
        if not user_id:
            print("⚠️ IS_TEST_MODE=True ですが LINE_USER_ID が未設定のため送信をスキップしました。")
            return
        url = "https://api.line.me/v2/bot/message/push"
        body = {"to": user_id, "messages": payload_messages}
        print(f"🔒 [テスト送信モード] 管理者 ({user_id}) 宛てにPush送信します。")
    else:
        url = "https://api.line.me/v2/bot/message/broadcast"
        body = {"messages": payload_messages}
        print("📢 [本番送信モード] 登録者全員へ一斉送信（broadcast）します。")
        
    try:
        res = requests.post(url, headers=headers, json=body, timeout=TIMEOUT_SEC)
        res.raise_for_status()
    except Exception as e:
        print(f"⚠️ LINE送信エラー: {e}")

def send_line_flex(header_title, round_num, location, event_date_str, entry_str, page_url, theme_color, access_token, user_id, is_test_mode, extra_info=None, main_image_url=None, custom_msg=None):
    is_cc = "/cc" in page_url
    title_main = f"第{round_num}回" if is_cc else f"第{round_num}戦"
    title_sub = location if is_cc else f"{location}大会"
    
    is_day_before_notice = "明日大会開催" in header_title

    body_contents = [
        {"type": "text", "text": title_main, "weight": "bold", "size": "xl", "color": "#333333"},
        {"type": "text", "text": title_sub, "weight": "bold", "size": "md", "color": "#555555", "wrap": True},
        {"type": "separator", "margin": "md"}
    ]
    
    if custom_msg:
        body_contents.append({
            "type": "text", 
            "text": custom_msg, 
            "weight": "bold", 
            "size": "lg", 
            "color": "#D32F2F", 
            "wrap": True, 
            "margin": "md"
        })
        body_contents.append({"type": "separator", "margin": "md"})
    
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
                "contents": [
                    {"type": "text", "text": f"📋 受付時間: {extra_info.get('reception', '情報参照')}", "size": "md", "color": "#D32F2F", "weight": "bold"},
                    {"type": "text", "text": f"💰 参加費用: {extra_info.get('fee', '情報参照')}", "size": "md", "color": "#D32F2F", "weight": "bold"}
                ]
            })
            if "weather_advice" in extra_info:
                body_contents.append({"type": "separator", "margin": "md"})
                body_contents.append({
                    "type": "box", "layout": "vertical", "spacing": "sm", "margin": "md",
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

    flex_payload = [{"type": "flex", "altText": f"【{header_title}】{title_main} {title_sub}", "contents": {"type": "carousel", "contents": [bubble]}}]
    dispatch_line_message(flex_payload, access_token, user_id, is_test_mode)

def send_result_line_flex(header_title, round_num, location, results, page_url, theme_color, access_token, user_id, is_test_mode):
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
        dispatch_line_message([{"type": "flex", "altText": f"【大会結果】{title_text_str}", "contents": {"type": "carousel", "contents": bubbles}}], access_token, user_id, is_test_mode)

def send_video_line_flex(header_title, round_num, location, video_data, page_url, theme_color, access_token, user_id, is_test_mode, main_image_url=None):
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

    dispatch_line_message([{"type": "flex", "altText": f"{header_title} {title_main} {title_sub}", "contents": bubble}], access_token, user_id, is_test_mode)
