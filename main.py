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
            url, headers=headers, json=flex_payload, timeout=TIMEOUT_SEC
        )
        if response.status_code != 200:
            print(f"送信失敗詳細 ({response.status_code}): {response.text}")
        response.raise_for_status()
        print("第14戦のカルーセル送信に成功しました。")
    except Exception as e:
        print(f"送信エラー: {e}")


def main():
    print("第14戦ページの取得・テスト送信を開始します。")

    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(TEST_URL, headers=headers, timeout=TIMEOUT_SEC)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        content_area = soup.find("div", class_="entry-content") or soup
        text = content_area.get_text(strip=True)

        match_title = re.search(r"第(\d+)戦([^\s大会を]+)", text)
        round_num = match_title.group(1) if match_title else "14"
        location = match_title.group(2) if match_title else "対象会場"

        match_entry = re.search(
            r"(?:エントリー).*?(?:(\d{1,2})月(\d{1,2})日|(\d{1,2})/(\d{1,2})).*?(\d{1,2}):(\d{2})",
            text,
        )
        if match_entry:
            if match_entry.group(1):
                m, d = match_entry.group(1), match_entry.group(2)
            else:
                m, d = match_entry.group(3), match_entry.group(4)
            hh, mm = match_entry.group(5), match_entry.group(6)
            entry_str = f"{int(m):02d}月{int(d):02d}日 {int(hh):02d}:{mm}"
        else:
            entry_str = "日時不明"

        # 開催場所に応じたテーマカラーを取得
        theme_color = get_theme_color(location)

        # 送信実行
        send_line_flex_carousel(
            round_num, location, entry_str, TEST_URL, theme_color
        )

    except Exception as e:
        print(f"テスト実行エラー: {e}")


if __name__ == "__main__":
    main()
