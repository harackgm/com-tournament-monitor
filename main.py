import os
import re
import time
from datetime import datetime
import requests
from bs4 import BeautifulSoup

# ==========================================
# 安全制御・環境変数設定
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")

TAG_URL = "https://www.kanritsuriba.com/at/tag/areatournament2026/"
TIMEOUT_SEC = 20
TARGET_YEAR = 2026


# ==========================================
# 都道府県・細分化テーマカラー判定ロジック
# ==========================================
def get_theme_color(location_name):
    if any(kw in location_name for kw in ["栃木", "キングフィッシャー", "上永野", "みどり", "なら山", "大芦", "増井", "宇都宮", "アメイズ"]):
        return "#03A9F4"  # 🔵 ライトブルー（栃木エリア）
    elif any(kw in location_name for kw in ["群馬", "中之沢", "赤城", "川場", "沼田", "宮城", "ベリーズ", "イワナ"]):
        return "#E91E63"  # 🩷 ローズピンク（群馬エリア）
    elif any(kw in location_name for kw in ["静岡", "浜名湖", "東山湖", "すその", "柿田川"]):
        return "#FF9800"  # 🟠 オレンジ（静岡エリア）
    elif any(kw in location_name for kw in ["山梨", "長野", "白州", "シルフ", "竜華池", "鹿島槍"]):
        return "#4CAF50"  # 🟢 グリーン（山梨・長野エリア）
    elif any(kw in location_name for kw in ["千葉", "ジョイバレー", "けんた", "千葉川すそ"]):
        return "#FF5722"  # 🟧 レッドオレンジ（千葉エリア）
    elif any(kw in location_name for kw in ["三重", "岐阜", "滋賀", "サンクチュアリ", "サンク", "瑞浪", "平谷", "醒井"]):
        return "#009688"  # 翡翠色/ティール（中京・近畿エリア）
    elif any(kw in location_name for kw in ["茨城", "埼玉", "神奈川", "座間", "高萩", "上浜", "エリアJ"]):
        return "#9C27B0"  # 🟪 パープル（茨城・埼玉・神奈川エリア）
    else:
        return "#607D8B"  # 🩶 ブルーグレー（その他エリア）


# ==========================================
# 大会開催日の抽出ロジック（曜日付き）
# ==========================================
def extract_event_date_str(text, year=TARGET_YEAR):
    match = re.search(r"(?:(\d{4})年)?\s*(\d{1,2})月(\d{1,2})日", text)
    if match:
        y = int(match.group(1)) if match.group(1) else year
        m = int(match.group(2))
        d = int(match.group(3))
        try:
            dt = datetime(y, m, d)
            w = ["月", "火", "水", "木", "金", "土", "日"][dt.weekday()]
            return f"{y}年{m:02d}月{d:02d}日({w})"
        except ValueError:
            return f"{y}年{m:02d}月{d:02d}日"
    return "開催日不明"


# ==========================================
# エントリー開始日時の抽出ロジック（曜日付き）
# ==========================================
def extract_datetime_from_text(text, year=TARGET_YEAR):
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]

    pattern_strict = re.search(
        r"(?:インターネットエントリー|エントリー|受付|募集)[^\d\n]{0,50}?(?:(\d{1,2})月(\d{1,2})日|(\d{1,2})[/.-](\d{1,2}))[^\d\n]{0,30}?(\d{1,2}):(\d{2})",
        text,
    )
    pattern = pattern_strict

    if not pattern:
        pattern = re.search(
            r"(?:(\d{1,2})月(\d{1,2})日|(\d{1,2})[/.-](\d{1,2}))[^\d\n]{0,20}?(\d{1,2}):(\d{2})",
            text,
        )

    if pattern:
        m = int(pattern.group(1) or pattern.group(3))
        d = int(pattern.group(2) or pattern.group(4))
        hh = int(pattern.group(5))
        mm = int(pattern.group(6))

        entry_year = year - 1 if m >= 11 else year
        try:
            dt = datetime(entry_year, m, d, hh, mm)
            w = weekdays[dt.weekday()]
            return f"{m:02d}月{d:02d}日({w}) {hh:02d}:{mm:02d}"
        except ValueError:
            return f"{m:02d}月{d:02d}日 {hh:02d}:{mm:02d}"

    return None


def fetch_page_text(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=TIMEOUT_SEC)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            content_area = soup.find("div", class_="entry-content") or soup
            return content_area.get_text(strip=True)
    except Exception:
        pass
    return ""


# ==========================================
# カルーセル用 Bubble（単体カード）作成
# ==========================================
def create_card_bubble(
    round_num, location, event_date_str, entry_str, page_url, theme_color
):
    return {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": theme_color,
            "contents": [
                {
                    "type": "text",
                    "text": "🎣 エリアトーナメント2026",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "xs",
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
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
                        {
                            "type": "text",
                            "text": "📅 大会開催日",
                            "size": "xs",
                            "color": "#888888",
                        },
                        {
                            "type": "text",
                            "text": event_date_str,
                            "size": "xl",
                            "color": "#333333",
                        },
                    ],
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "xs",
                    "contents": [
                        {
                            "type": "text",
                            "text": "⏰ エントリー開始日時",
                            "size": "xs",
                            "color": "#888888",
                        },
                        {
                            "type": "text",
                            "text": entry_str,
                            "size": "xl",
                            "color": "#E53935",
                        },
                    ],
                },
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "action": {
                        "type": "uri",
                        "label": "🔗 詳細・エントリー",
                        "uri": page_url,
                    },
                    "style": "primary",
                    "color": theme_color,
                }
            ],
        },
    }


# ==========================================
# LINE Push Message (10件ずつまとめてカルーセル送信)
# ==========================================
def send_line_carousel_batch(card_bubbles):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        print("エラー: LINE接続情報が未設定です。")
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }

    chunk_size = 10
    for i in range(0, len(card_bubbles), chunk_size):
        chunk = card_bubbles[i : i + chunk_size]

        flex_payload = {
            "to": LINE_USER_ID,
            "messages": [
                {
                    "type": "flex",
                    "altText": f"【全大会一覧】エリアトーナメント2026 ({i+1}~{i+len(chunk)}件目)",
                    "contents": {"type": "carousel", "contents": chunk},
                }
            ],
        }

        try:
            response = requests.post(
                url, headers=headers, json=flex_payload, timeout=TIMEOUT_SEC
            )
            if response.status_code != 200:
                print(
                    f"一括送信エラー ({response.status_code}): {response.text}"
                )
            response.raise_for_status()
            print(f"カルーセル送信成功: {i+1}〜{i+len(chunk)}件目のカード")
            time.sleep(1)
        except Exception as e:
            print(f"送信エラー: {e}")


def main():
    print("2026年全大会のデータ取得・一括テスト送信を開始します。")

    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(TAG_URL, headers=headers, timeout=TIMEOUT_SEC)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        links = soup.find_all("a", href=re.compile(r"/at/2026_\d+/"))
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
        print(f"一覧ページ取得エラー: {e}")
        return

    def get_round_number(u):
        m = re.search(r"2026_(\d+)", u)
        return int(m.group(1)) if m else 999

    urls_to_check.sort(key=get_round_number)

    all_card_bubbles = []

    for url in urls_to_check:
        try:
            sub_url = url.rstrip("/") + "/2/"
            text_p2 = fetch_page_text(sub_url)
            text_p1 = fetch_page_text(url)
            combined_text = (text_p2 + " " + text_p1).strip()

            if not combined_text:
                continue

            match_title = re.search(r"第(\d+)戦([^\s大会を]+)", combined_text)
            round_num = (
                match_title.group(1)
                if match_title
                else str(get_round_number(url))
            )
            location = match_title.group(2) if match_title else "対象会場"

            event_date_str = extract_event_date_str(combined_text)
            entry_str = extract_datetime_from_text(
                text_p2
            ) or extract_datetime_from_text(text_p1)

            if not entry_str:
                entry_str = "日時不明"

            final_url = sub_url if extract_datetime_from_text(text_p2) else url
            theme_color = get_theme_color(location)

            bubble = create_card_bubble(
                round_num,
                location,
                event_date_str,
                entry_str,
                final_url,
                theme_color,
            )
            all_card_bubbles.append(bubble)
            print(f"取得完了: 第{round_num}戦 {location}")

        except Exception as e:
            print(f"解析失敗 ({url}): {e}")

    if all_card_bubbles:
        print(
            f"合計 {len(all_card_bubbles)} 件の大会データを取得しました。LINE送信を行います。"
        )
        send_line_carousel_batch(all_card_bubbles)
    else:
        print("送信対象の大会データが見つかりませんでした。")


if __name__ == "__main__":
    main()
