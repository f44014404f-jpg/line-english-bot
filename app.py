"""
LINE 英文小助手
- LINE Messaging API (Reply 模式，免費)
- Google Gemini (gemini-2.5-flash)
- Google 試算表 (透過 Apps Script Web App 存單字與狀態)

模式 (打這些字切換，會被記住)：
    教學 / 背單字     → 單字教學模式 (教你單字並自動記錄，避開學過的)
    文法             → 文法教學模式
    考試             → 考試模式 (用你記過的單字考你、計分)
    聊天 / 結束       → 一般家教模式

指令 (任何模式都能用)：
    /翻譯 <句子>   /文法 <句子>   /單字
    /記 <單字> [中文]   /今天   /複習   /help
"""

import os
import re
import json
import random
import datetime

import requests
from flask import Flask, request, abort

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent

from google import genai
from google.genai import types


# ---------- 金鑰 / 設定 ----------
LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_SECRET = os.environ["LINE_CHANNEL_SECRET"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
SHEET_URL = os.environ.get("SHEET_WEBAPP_URL")
SHEET_TOKEN = os.environ.get("SHEET_TOKEN")

app = Flask(__name__)
configuration = Configuration(access_token=LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)
gemini = genai.Client(api_key=GEMINI_KEY)
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")  # lite 免費每日額度大很多

TW = datetime.timezone(datetime.timedelta(hours=8))
tw_today = lambda: datetime.datetime.now(TW).date().isoformat()
tw_time = lambda: datetime.datetime.now(TW).strftime("%H:%M")


# ---------- 試算表 (呼叫 Apps Script) ----------
def sheet_call(payload: dict):
    if not SHEET_URL or not SHEET_TOKEN:
        return None
    try:
        payload["token"] = SHEET_TOKEN
        return requests.post(SHEET_URL, json=payload, timeout=20).json()
    except Exception as e:
        print("sheet_call error:", e)
        return None


def norm_date(v) -> str:
    """試算表可能把日期回成 ISO 時間字串，統一轉回台灣的 YYYY-MM-DD。"""
    s = str(v)
    if "T" in s:
        try:
            dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TW)
            return dt.date().isoformat()
        except Exception:
            return s[:10]
    return s[:10]


def record_word(user, word, meaning):
    return sheet_call({"action": "record", "user": user, "word": word,
                       "meaning": meaning, "date": tw_today(), "time": tw_time()})


def list_words(user):
    res = sheet_call({"action": "list", "user": user})
    if not res or not res.get("ok"):
        return []
    rows = res.get("rows", [])
    for r in rows:
        r["date"] = norm_date(r.get("date", ""))
    return rows


def get_state(user):
    res = sheet_call({"action": "getstate", "user": user})
    if not res or not res.get("ok"):
        return "chat", ""
    return (res.get("mode") or "chat"), (res.get("pending") or "")


def set_state(user, mode, pending=""):
    sheet_call({"action": "setstate", "user": user, "mode": mode, "pending": pending})


# ---------- Gemini ----------
def ask_gemini(system_prompt: str, user_text: str, temp=0.7) -> str:
    try:
        resp = gemini.models.generate_content(
            model=MODEL, contents=user_text,
            config=types.GenerateContentConfig(system_instruction=system_prompt, temperature=temp),
        )
        return (resp.text or "").strip() or "（沒有回傳內容，再試一次）"
    except Exception as e:
        return f"⚠️ 呼叫 Gemini 失敗：{e}"


SHORT = "回答精簡、適合手機閱讀，最多 6 行，不要長篇大論。"

TUTOR = f"你是親切的多益英文家教，對象是台灣中級學習者。用繁體中文說明，英文保留英文。{SHORT}"
TRANSLATE = f"你是翻譯老師。中文就翻成自然英文、英文就翻成中文，翻完用中文點 1 個重點。{SHORT}"
GRAMMAR_ONE = f"你是多益文法老師，針對使用者的句子挑一個最常考的文法點簡短講解，有錯就指出正確寫法。{SHORT}"
VOCAB_ONE = ("你是多益單字老師，出一個多益中高頻單字。格式：\n📖 word (詞性) 中文\n例句：英文（中譯）\n"
             "🧠 克漏字一句，挖空該字，最後附『（答案：word）』。精簡。")
MEANING = "你是英漢字典，只回覆這個英文單字最常用的繁體中文意思，最多10字，不要例句詞性。"


# ---------- 模式：教學 / 文法 ----------
def vocab_teach(user, user_text):
    known = [r["word"] for r in list_words(user)][-60:]
    avoid = "、".join(known) if known else "（無）"
    sys = (
        "你是多益單字老師。使用者若指定某個單字就教那個，否則教一個新的多益中高頻單字，"
        f"避開這些學過的字：{avoid}。\n"
        "嚴格照這個格式輸出，第一行是給程式讀的資料，之後才是給人看的：\n"
        "DATA: 英文單字|中文意思\n"
        "📖 單字 (詞性) 中文\n例句：一句英文（附中譯）\n一句超簡短用法或記憶提示。\n"
        "整體最多 5 行。"
    )
    out = ask_gemini(sys, user_text or "教我一個新單字")
    # 解析第一行 DATA: word|meaning 來自動記錄
    m = re.search(r"DATA:\s*(.+?)\|(.+)", out)
    shown = re.sub(r"^DATA:.*\n?", "", out, count=1).strip()
    if m:
        word, meaning = m.group(1).strip(), m.group(2).strip()
        record_word(user, word, meaning)
        return f"{shown}\n\n✅ 已幫你記錄「{word}」（打「考試」可複習）"
    return shown or out


def grammar_teach(user_text):
    sys = ("你是多益文法老師。使用者給句子就講解該句最常考的一個文法點並修正錯誤；"
           f"若只是說「下一個」或沒給句子，就教一個多益常考文法重點並給例句。{SHORT}")
    return ask_gemini(sys, user_text or "教我一個文法重點")


# ---------- 模式：考試 ----------
def next_question(user, score, n):
    words = list_words(user)
    r = random.choice(words)
    pending = json.dumps({"answer": r["word"], "meaning": r["meaning"], "score": score, "n": n})
    return pending, f'Q{n + 1}：「{r["meaning"]}」的英文是？'


def start_exam(user):
    if not list_words(user):
        set_state(user, "chat", "")
        return "考試需要先有記錄的單字。先打「教學」學幾個，或用 /記 新增，再來考試 📖"
    pending, q = next_question(user, 0, 0)
    set_state(user, "exam", pending)
    return "📝 考試開始！答錯沒關係，打「結束」可以停。\n\n" + q


def exam_answer(user, text, pending):
    try:
        st = json.loads(pending)
    except Exception:
        return start_exam(user)
    ans, meaning = st["answer"], st["meaning"]
    score, n = st["score"], st["n"]
    n += 1
    if text.strip().lower() == ans.strip().lower():
        score += 1
        fb = f"✅ 答對！{ans}（{meaning}）"
    else:
        fb = f"❌ 答案是 {ans}（{meaning}）"
    pending, q = next_question(user, score, n)
    set_state(user, "exam", pending)
    return f"{fb}\n目前 {score}/{n} 分\n\n{q}"


def end_exam(user, pending):
    try:
        st = json.loads(pending)
        score, n = st["score"], st["n"]
    except Exception:
        score, n = 0, 0
    set_state(user, "chat", "")
    if n == 0:
        return "考試結束，回到一般模式 🙂"
    return f"🏁 考試結束！你答對 {score}/{n} 題。回到一般模式，隨時打「考試」再來一輪。"


# ---------- 記錄類指令 ----------
def cmd_record(user, body):
    if not SHEET_URL:
        return "⚠️ 還沒設定好試算表。"
    if not body:
        return "用法：/記 單字 中文（中文可省略，我會自動查）\n例如：/記 procrastinate 拖延"
    parts = body.split(maxsplit=1)
    word = parts[0]
    meaning = parts[1].strip() if len(parts) > 1 else ask_gemini(MEANING, word)
    res = record_word(user, word, meaning)
    if not res or not res.get("ok"):
        return "⚠️ 寫入試算表失敗，稍後再試。"
    today_n = sum(1 for r in list_words(user) if r["date"] == tw_today())
    return f"✅ 已記錄！{word}（{meaning}）\n今天第 {today_n} 個單字 💪"


def cmd_today(user):
    words = [r for r in list_words(user) if r["date"] == tw_today()]
    if not words:
        return "今天還沒記單字，打「教學」或用 /記 開始吧 📖"
    lines = [f'{i}. {r["word"]}（{r["meaning"]}）' for i, r in enumerate(words, 1)]
    return f"📅 今天記了 {len(words)} 個：\n" + "\n".join(lines)


def cmd_review(user):
    words = list_words(user)
    if not words:
        return "還沒有記錄的單字，先學幾個吧！"
    r = random.choice(words)
    return f'🧠 複習：「{r["meaning"]}」的英文是？\n\n（答案：{r["word"]}）'


HELP = (
    "🔤 模式（打這些字切換，會記住）：\n"
    "• 教學 → 教你單字並自動記錄\n"
    "• 文法 → 文法教學\n"
    "• 考試 → 用你記過的單字考你計分\n"
    "• 聊天 → 一般家教\n\n"
    "⚡ 指令（隨時可用）：\n"
    "• /翻譯 句子　• /文法 句子　• /單字\n"
    "• /記 單字 中文　• /今天　• /複習\n\n"
    "💡 隨時打「選單」可再看到這份說明。"
)

WELCOME = (
    "👋 歡迎使用「多益學習小助手」！\n"
    "我能陪你背單字、學文法、考試，還會自動記錄你學過的字 📚\n\n"
    "🔤 打這些字切換模式（會記住）：\n"
    "• 教學 → 我教你單字並自動記錄\n"
    "• 文法 → 教你多益文法\n"
    "• 考試 → 用你記過的字考你、算分\n"
    "• 聊天 → 一般英文家教，什麼都能問\n\n"
    "⚡ 常用指令：\n"
    "• /翻譯 句子　• /單字　• /今天　• /複習\n\n"
    "💡 現在就打「教學」開始背第一個單字吧！\n"
    "（隨時打「選單」可再看到說明）"
)

SWITCH = {
    "vocab": {"教學", "背單字", "單字模式", "單字教學"},
    "grammar": {"文法", "文法模式", "學文法"},
    "exam": {"考試", "測驗", "考我"},
    "chat": {"聊天", "一般", "回家教", "家教"},
}


# ---------- 路由 ----------
def route(text, user):
    s = text.strip()

    # 1) 斜線指令：任何模式都能用
    if s.startswith("/翻譯"):
        b = s[3:].strip()
        return ask_gemini(TRANSLATE, b) if b else "用法：/翻譯 你想翻譯的句子"
    if s.startswith("/文法"):
        b = s[3:].strip()
        return ask_gemini(GRAMMAR_ONE, b) if b else "用法：/文法 一句英文"
    if s.startswith("/單字"):
        return ask_gemini(VOCAB_ONE, "出一題")
    if s.startswith("/記"):
        return cmd_record(user, s[2:].strip())
    if s.startswith("/今天"):
        return cmd_today(user)
    if s.startswith("/複習"):
        return cmd_review(user)
    if s in ("/help", "/說明", "help", "?"):
        return HELP
    if s in ("選單", "開始", "menu", "使用說明", "怎麼用"):
        return WELCOME

    # 2) 模式切換（整句剛好是關鍵字才切換）
    for mode, kws in SWITCH.items():
        if s in kws:
            if mode == "exam":
                return start_exam(user)
            set_state(user, mode, "")
            names = {"vocab": "單字教學", "grammar": "文法教學", "chat": "一般家教"}
            tip = {"vocab": "直接打任何字我就教你新單字，或打某個單字教那個。",
                   "grammar": "貼一句英文我幫你看文法，或打「下一個」教你新重點。",
                   "chat": "有什麼英文問題都可以問我。"}[mode]
            return f"已切換到「{names[mode]}」模式 ✅\n{tip}\n（打「考試」或「文法」等可再切換）"

    # 3) 依目前模式處理
    mode, pending = get_state(user)

    if mode == "exam":
        if s in ("結束", "停", "stop", "退出"):
            return end_exam(user, pending)
        return exam_answer(user, s, pending)
    if mode == "vocab":
        return vocab_teach(user, s)
    if mode == "grammar":
        return grammar_teach(s)
    return ask_gemini(TUTOR, s)  # chat


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
            ReplyMessageRequest(reply_token=event.reply_token,
                                messages=[TextMessage(text=reply)])
        )


@handler.add(FollowEvent)
def on_follow(event):
    """有人加 bot 好友時，自動送上使用說明。"""
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(reply_token=event.reply_token,
                                messages=[TextMessage(text=WELCOME)])
        )


@app.route("/")
def health():
    return "LINE English Bot is running."


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
