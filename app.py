"""
LINE 英文小助手 (翻譯 / 多益文法 / 單字練習)
- LINE Messaging API (Reply 模式，使用者先傳你才回 → 免費)
- Google Gemini API (免費 tier, gemini-2.5-flash)

指令:
    /翻譯 <句子>   翻譯並解釋
    /文法 <句子>   針對句子講一個多益常考文法點
    /單字          出一個多益風格單字 + 例句 + 小測驗
    其他任何訊息    當一般英文家教對話
"""

import os

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

app = Flask(__name__)
configuration = Configuration(access_token=LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)
gemini = genai.Client(api_key=GEMINI_KEY)

MODEL = "gemini-2.5-flash"


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


# ---------- 指令路由 ----------
def route(text: str) -> str:
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

    if stripped in ("/help", "/說明", "help", "?"):
        return (
            "我可以幫你：\n"
            "• /翻譯 <句子> → 翻譯 + 重點\n"
            "• /文法 <句子> → 講一個多益文法點\n"
            "• /單字 → 出一個單字練習\n"
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
    reply = route(event.message.text)
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
