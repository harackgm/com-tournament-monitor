import os
import re
import requests
from bs4 import BeautifulSoup

# ==========================================
# 安全制御・環境変数設定
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")

# テスト対象：第14戦のページURL
TEST_URL = "https://www.kanritsuriba.com/at/2026_14/"
TIMEOUT_SEC = 20
TARGET_YEAR = 2026


# ==========================================
# 地域別のテーマカラー判定ロジック
# ==========================================
def get_theme_color(location_name):
    if any(kw in location_name for kw in ["浜名湖", "東海", "静岡"]):
        return "#FF9800"  # 🟠 オレンジ（静岡・東海エリア）
    elif any(kw in location_name for kw in ["白州", "山梨", "長野", "甲信越"]):
        return "#4CAF50"  # 🟢 グリーン（山梨・甲信越エリア）
    elif any(kw in location_name for kw in ["キングフィッシャー", "東山湖", "関東", "栃木"]):
        return "#03A9F4"  # 🔵 ブルー（関東エリア）
    else:
        return "#9C27B0"  # 🟣 パープル（その他・未登録エリア）


# ==========================================
# 大会開催日の抽出ロジック
# ==========================================
def extract_event_date_str(text, year=TARGET_YEAR):
    match = re.search(r"(?:(\d{4})年)?\s*(\d{1,2})月(\d{1,2})日", text)
    if match:
        y = match.group(1) if match.group(1) else str(year)
        m = int(match.group(2))
        d = int(match.group(3))
        return f"{y}年{m:02d}月{d:02d}日"
    return "開催日不明"


# ==========================================
# エントリー開始日時の抽出ロジック（近接検索）
# ==========================================
def extract_datetime_from_text(text):
    pattern_strict = re.search(
        r"(?:インターネットエントリー|エントリー|受付|募集)[^\d\n]{0,50}?(?:(\d{1,2})月(\d{1,2})日|(\d{1,2})[/.-](\d{1,2}))[^\d\n]{0,30}?(\d{1,2}):(\d{2})",
        text,
    )
    if pattern_strict:
        m = pattern_strict.group(1) or pattern_strict.group(3)
        d = pattern_strict.group(2) or pattern_strict.group(4)
        hh = pattern_strict.group(5)
        mm = pattern_strict.group(6)
        return f"{int(m):02d}月{int(d):02d}日 {int(hh):02d}:{mm}"

    pattern_near = re.search(
        r"(?:(\d{1,2})月(\d{1,2})日|(\d{1,2})[/.-](\d{1,2}))[^\d\n]{0,20}?(\d{1,2}):(\d{2})",
        text,
    )
    if pattern_near:
        m = pattern_near.group(1) or pattern_near.group(3)
        d = pattern_near.group(2) or pattern_near.group(4)
        hh = pattern_near.group(5)
        mm = pattern_near.group(6)
        return f"{int(m):02d}月{int(d):02d}日 {int(hh):02d}:{mm}"

    return None


# ==========================================
# LINE Push Message (文字サイズ調整版 Flex Message)
# ==========================================
def send_line_flex_carousel(
    round_num, location, event_date_str, entry_str, page_url, theme_color
):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        print("エラー: LINE接続情報が未設定です。")
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }

    flex_payload = {
        "to": LINE_USER_ID,
        "messages": [
            {
                "type": "flex",
                "altText": f"【新規大会情報】第{round_num}戦 {location}大会",
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
                                        "size": "xl",  # 変更なし
                                        "color": "#333333",
                                    },
                                    {
                                        "type": "text",
                                        "text": f"{location}大会",
                                        "weight": "bold",
                                        "size": "md",  # 変更なし
                                        "color": "#555555",
                                        "wrap": True,
                                    },
                                    {"type": "separator"},
                                    # --- 大会開催日（文字サイズ拡大: lg） ---
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
                                                "size": "lg",  # sm -> lg へ拡大
                                                "weight": "bold",
                                                "color": "#333333",
                                            },
                                        ],
                                    },
                                    # --- エントリー開始日時（文字サイズ拡大: lg） ---
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
                                                "size": "lg",  # sm -> lg へ拡大
                                                "weight": "bold",
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
                    ],
                },
            }
        ],
    }

    try:
        response = requests.post(
            url, headers=headers, json=flex_payload, timeout=TIMEOUT_SEC
        )
        if response.status_code != 200:
            print(f"送信失敗詳細 ({response.status_code}): {response.text}")
        response.raise_for_status()
        print("文字拡大版カルーセルメッセージの送信に成功しました。")
    except Exception as e:
        print(f"送信エラー: {e}")


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


def main():
    print("文字サイズ拡大テストを開始します。")

    sub_url = TEST_URL.rstrip("/") + "/2/"
    text_p2 = fetch_page_text(sub_url)
    text_p1 = fetch_page_text(TEST_URL)

    combined_text = (text_p2 + " " + text_p1).strip()

    match_title = re.search(r"第(\d+)戦([^\s大会を]+)", combined_text)
    round_num = match_title.group(1) if match_title else "14"
    location = match_title.group(2) if match_title else "アメイズトラウトエリア"

    event_date_str = extract_event_date_str(combined_text)
    entry_str = extract_datetime_from_text(text_p2) or extract_datetime_from_text(text_p1)
    if not entry_str:
        entry_str = "日時不明"

    final_url = sub_url if extract_datetime_from_text(text_p2) else TEST_URL
    theme_color = get_theme_color(location)

    send_line_flex_carousel(
        round_num, location, event_date_str, entry_str, final_url, theme_color
    )


if __name__ == "__main__":
    main()
