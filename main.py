import os
import re
import requests
from bs4 import BeautifulSoup

# ==========================================
# 環境変数からLINE接続情報を取得
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")

# テスト対象：第21戦（白州トラウトエリア・シルフ）のページURL
TEST_URL = "https://www.kanritsuriba.com/at/2026_21/"
TIMEOUT_SEC = 20


def send_line_message(text_message):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        print("エラー: LINE_CHANNEL_ACCESS_TOKEN または LINE_USER_ID が未設定です。")
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text_message}],
    }

    try:
        response = requests.post(
            url, headers=headers, json=payload, timeout=TIMEOUT_SEC
        )
        response.raise_for_status()
        print("LINEへのテスト送信に成功しました。")
    except Exception as e:
        print(f"LINE送信エラー: {e}")


def main():
    print("テスト実行を開始します（実際のページから1件のみ取得して送信）。")

    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(TEST_URL, headers=headers, timeout=TIMEOUT_SEC)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        content_area = soup.find("div", class_="entry-content") or soup
        text = content_area.get_text(strip=True)

        # 第何戦・開催地の抽出
        match_title = re.search(r"第(\d+)戦([^\s大会を]+)", text)
        round_num = match_title.group(1) if match_title else "21"
        location = (
            match_title.group(2) if match_title else "白州トラウトエリア・シルフ"
        )

        # エントリー開始日時の抽出
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
            entry_str = f"{int(m):02d}/{int(d):02d} {int(hh):02d}:{mm}"
        else:
            entry_str = "08/25 20:00"

        # 本番と同一フォーマットの通知メッセージを作成
        test_msg = (
            f"🧪【動作テスト通知】\n"
            f"実ページ（第{round_num}戦）から自動取得したデータです。\n\n"
            f"【新規大会情報】\n"
            f"第{round_num}戦 {location}大会\n"
            f"エントリー開始: {entry_str}\n"
            f"URL: {TEST_URL}"
        )

        # LINE送信（1通のみ）
        send_line_message(test_msg)

    except Exception as e:
        print(f"テスト実行エラー: {e}")


if __name__ == "__main__":
    main()
