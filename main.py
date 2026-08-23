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
# エントリー日時抽出ロジック（正規表現）
# ==========================================
def extract_datetime_from_text(text):
    pattern = re.search(
        r"(?:エントリー|受付|募集).*?(?:(\d{1,2})月(\d{1,2})日|(\d{1,2})[/.-](\d{1,2})).*?(\d{1,2}):(\d{2})",
        text,
        re.DOTALL,
    )
    if pattern:
        m = pattern.group(1) or pattern.group(3)
        d = pattern.group(2) or pattern.group(4)
        hh = pattern.group(5)
        mm = pattern.group(6)
        return f"{int(m):02d}月{int(d):02d}日 {int(hh):02d}:{mm}"

    # 条件緩和検索
    pattern_loose = re.search(
        r"(?:(\d{1,2})月(\d{1,2})日|(\d{1,2})[/.-](\d{1,2})).*?(\d{1,2}):(\d{2})",
        text,
        re.DOTALL,
    )
    if pattern_loose:
        m = pattern_loose.group(1) or pattern_loose.group(3)
        d = pattern_loose.group(2) or pattern_loose.group(4)
        hh = pattern_loose.group(5)
        mm = pattern_loose.group(6)
        return f"{int(m):02d}月{int(d):02d}日 {int(hh):02d}:{mm}"

    return None


# ==========================================
# LINE Push Message (地域カラー連動 Flex Message)
# ==========================================
def send_line_flex_carousel(
    round_num, location, entry_str, page_url, theme_color
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
                                                "text": "⏰ エントリー開始日時",
                                                "size": "xs",
                                                "color": "#888888",
                                            },
                                            {
                                                "type": "text",
                                                "text": entry_str,
                                                "size": "sm",
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
            url, headers=headers, json=payload_bytes if 'payload_bytes' in locals() else flex_payload, timeout=TIMEOUT_SEC
        )
        if response.status_code != 200:
            print(f"送信失敗詳細 ({response.status_code}): {response.text}")
        response.raise_for_status()
        print("第14戦のカルーセル送信に成功しました。")
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
    print("第14戦ページの多重巡回解析を開始します。")

    # 1. まず本ページ（1ページ目）を取得
    text_p1 = fetch_page_text(TEST_URL)

    # 第何戦・開催地の抽出
    match_title = re.search(r"第(\d+)戦([^\s大会を]+)", text_p1)
    round_num = match_title.group(1) if match_title else "14"
    location = match_title.group(2) if match_title else "アメイズトラウトエリア"

    # 1ページ目から日時を検索
    entry_str = extract_datetime_from_text(text_p1)

    # 2. 1ページ目で見つからない場合、2ページ目（/2/）を巡回するフォールバック処理
    final_url = TEST_URL
    if not entry_str:
        print("1ページ目に日時が見つからないため、2ページ目(/2/)を確認します...")
        sub_url = TEST_URL.rstrip("/") + "/2/"
        text_p2 = fetch_page_text(sub_url)
        entry_str = extract_datetime_from_text(text_p2)
        if entry_str:
            final_url = sub_url

    if not entry_str:
        entry_str = "日時不明"

    # テーマカラー取得
    theme_color = get_theme_color(location)

    # LINE送信実行
    send_line_flex_carousel(
        round_num, location, entry_str, final_url, theme_color
    )


if __name__ == "__main__":
    main()
