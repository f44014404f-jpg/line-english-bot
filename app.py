"""
LINE 英文小助手 (翻譯 / 多益文法 / 單字練習 / 單字記錄)
- LINE Messaging API (Reply 模式，使用者先傳你才回 → 免費)
- Google Gemini API (免費 tier, gemini-2.5-flash)
- Google 試算表 (記錄每天學的單字，可隨時打開複習)

指令:
    /翻譯 <句子>       翻譯並解釋
    /文法 <句子>       針對句子講一個多益常考文法點
    /單字              出一個多益風格單字 + 例句 + 小測驗
    /記 <單字> [中文]  記錄一個單字 (沒填中文會自動查)
    /今天              看今天記錄了哪些單字
    /複習              從你記過的單字裡隨機抽一個考你
    其他任何訊息        當一般英文家教對話
"""

import os
import json
import random
import datetime

from flask import Flask, request, abort

# 本機開發時從 .env 讀金鑰 (雲端上會用平台的環境變數，沒有 .env 也不會報錯)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from google import genai
from google.genai import types


# ---------- 讀取金鑰 ----------
LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_SECRET = os.environ["LINE_CHANNEL_SECRET"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]

# Google 試算表 (選用；沒設定時記錄類指令會提示尚未設定)
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")

app = Flask(__name__)
configuration = Configuration(access_token=LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)
gemini = genai.Client(api_key=GEMINI_KEY)

MODEL = "gemini-2.5-flash"

# 台灣時區 (Render 伺服器是 UTC，要 +8 才是台灣的「今天」)
TW = datetime.timezone(datetime.timedelta(hours=8))


def tw_today() -> str:
    return datetime.datetime.now(TW).date().isoformat()


def tw_time() -> str:
    return datetime.datetime.now(TW).strftime("%H:%M")


# ---------- Google 試算表 ----------
_worksheet = None


def get_sheet():
    """回傳試算表工作表；沒設定或連不上時回 None。"""
    global _worksheet
    if _worksheet is not None:
        return _worksheet
    if not GOOGLE_SHEET_ID or not GOOGLE_CREDENTIALS_JSON:
        return None
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDENTIALS_JSON),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        _worksheet = gspread.authorize(creds).open_by_key(GOOGLE_SHEET_ID).sheet1
        return _worksheet
    except Exception as e:
        print("Google 試算表連線失敗：", e)
        return None


def my_rows(sheet, user_id: str):
    """回傳這個使用者記過的所有單字列 [日期, 單字, 中文, 時間, user_id]。"""
    rows = sheet.get_all_values()
    return [r for r in rows if len(r) >= 5 and r[4] == user_id]


# ---------- 呼叫 Gemini ----------
def ask_gemini(system_prompt: str, user_text: str) -> str:
    try:
        resp = gemini.models.generate_content(
            model=MODEL,
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.7,
            ),
        )
        return (resp.text or "").strip() or "（Gemini 沒有回傳內容，再試一次）"
    except Exception as e:
        return f"⚠️ 呼叫 Gemini 失敗：{e}"


# ---------- 各種模式的 system prompt ----------
TUTOR = (
    "你是一位親切但專業的多益(TOEIC)英文家教，對象是台灣的中級學習者。"
    "用繁體中文說明，英文例句保留英文。回答精簡、重點條列，適合在手機上看。"
)

TRANSLATE = (
    "你是英文翻譯老師。使用者給你一段文字：若是中文就翻成自然的英文，若是英文就翻成中文。"
    "格式：先給翻譯結果，再用繁體中文條列 1~2 個值得注意的用字或文法點。保持精簡。"
)

GRAMMAR = (
    "你是多益文法老師。針對使用者給的英文句子，挑出「一個」多益最常考的文法重點來講解"
    "（例如時態、介系詞、主動被動、關係代名詞等）。用繁體中文，先點出重點名稱，"
    "再簡短說明並給一個對照例句。若句子有錯也順便指出正確寫法。"
)

VOCAB = (
    "你是多益單字老師。隨機出一個多益常見的中高頻單字，格式如下（用繁體中文）：\n"
    "📖 單字：word (詞性)\n"
    "意思：...\n"
    "例句：一句英文例句 + 中文翻譯\n"
    "🧠 小測驗：造一個克漏字句子，把該單字挖空，並在最後用『（答案：word）』附上答案。"
)

MEANING = (
    "你是英漢字典。使用者給你一個英文單字，只回覆它最常用的繁體中文意思，"
    "越簡短越好（最多 10 個字），不要例句、不要詞性、不要任何多餘的字。"
)


# ---------- 單字記錄指令 ----------
def cmd_record(user_id: str, body: str) -> str:
    sheet = get_sheet()
    if sheet is None:
        return "⚠️ 還沒設定好 Google 試算表，暫時無法記錄單字。"
    if not body:
        return "用法：/記 單字 中文\n例如：/記 procrastinate 拖延\n（中文可省略，我會自動幫你查）"

    parts = body.split(maxsplit=1)
    word = parts[0]
    if len(parts) > 1:
        meaning = parts[1].strip()
    else:
        meaning = ask_gemini(MEANING, word)  # 沒給中文就自動查

    try:
        sheet.append_row([tw_today(), word, meaning, tw_time(), user_id])
    except Exception as e:
        return f"⚠️ 寫入試算表失敗：{e}"

    today_count = sum(1 for r in my_rows(sheet, user_id) if r[0] == tw_today())
    return f"✅ 已記錄！{word}（{meaning}）\n今天是你的第 {today_count} 個單字 💪"


def cmd_today(user_id: str) -> str:
    sheet = get_sheet()
    if sheet is None:
        return "⚠️ 還沒設定好 Google 試算表。"
    today = tw_today()
    words = [r for r in my_rows(sheet, user_id) if r[0] == today]
    if not words:
        return "今天還沒記錄單字喔！用「/記 單字 中文」開始吧 📖"
    lines = [f"{i}. {r[1]}（{r[2]}）" for i, r in enumerate(words, 1)]
    return f"📅 今天你記了 {len(words)} 個單字：\n" + "\n".join(lines)


def cmd_review(user_id: str) -> str:
    sheet = get_sheet()
    if sheet is None:
        return "⚠️ 還沒設定好 Google 試算表。"
    words = my_rows(sheet, user_id)
    if not words:
        return "你還沒記錄任何單字，先用「/記 單字 中文」累積一些吧！"
    r = random.choice(words)
    return (
        "🧠 複習時間！還記得這個中文對應的英文單字嗎？\n\n"
        f"「{r[2]}」\n\n"
        f"（答案：{r[1]}　—　{r[0]} 記錄的）"
    )


# ---------- 指令路由 ----------
def route(text: str, user_id: str) -> str:
    stripped = text.strip()

    if stripped.startswith("/翻譯"):
        body = stripped[3:].strip()
        if not body:
            return "用法：/翻譯 你想翻譯的句子"
        return ask_gemini(TRANSLATE, body)

    if stripped.startswith("/文法"):
        body = stripped[3:].strip()
        if not body:
            return "用法：/文法 一句英文，我幫你看文法重點"
        return ask_gemini(GRAMMAR, body)

    if stripped.startswith("/單字"):
        return ask_gemini(VOCAB, "出一題")

    if stripped.startswith("/記"):
        return cmd_record(user_id, stripped[2:].strip())

    if stripped.startswith("/今天"):
        return cmd_today(user_id)

    if stripped.startswith("/複習"):
        return cmd_review(user_id)

    if stripped in ("/help", "/說明", "help", "?"):
        return (
            "我可以幫你：\n"
            "• /翻譯 <句子> → 翻譯 + 重點\n"
            "• /文法 <句子> → 講一個多益文法點\n"
            "• /單字 → 出一個單字練習\n"
            "• /記 <單字> <中文> → 記錄單字\n"
            "• /今天 → 看今天記了哪些單字\n"
            "• /複習 → 隨機抽考你記過的單字\n"
            "• 直接打任何問題 → 當英文家教聊"
        )

    # 沒有指令 → 當一般家教
    return ask_gemini(TUTOR, stripped)


# ---------- LINE Webhook ----------
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def on_message(event):
    user_id = getattr(event.source, "user_id", "unknown")
    reply = route(event.message.text, user_id)
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)],
            )
        )


# 給 Render / 瀏覽器確認服務活著用
@app.route("/")
def health():
    return "LINE English Bot is running."


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))  # 5001 避開你股票網站的 5000
    app.run(host="0.0.0.0", port=port)
