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
import time
import json
import random
import datetime
from urllib.parse import quote

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
    FlexMessage, FlexContainer,
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
def sheet_call(payload: dict, retries: int = 2):
    if not SHEET_URL or not SHEET_TOKEN:
        return None
    payload["token"] = SHEET_TOKEN
    for attempt in range(retries + 1):
        try:
            return requests.post(SHEET_URL, json=payload, timeout=30).json()
        except Exception as e:
            print(f"sheet_call error (try {attempt + 1}):", e)
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
    last = ""
    for attempt in range(3):
        try:
            resp = gemini.models.generate_content(
                model=MODEL, contents=user_text,
                config=types.GenerateContentConfig(system_instruction=system_prompt, temperature=temp),
            )
            return (resp.text or "").strip() or "（沒有回傳內容，再試一次）"
        except Exception as e:
            last = str(e)
            transient = any(k in last for k in
                            ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "overloaded", "high demand"))
            if transient and attempt < 2:
                time.sleep(1.5)
                continue
            break
    return f"⚠️ 呼叫 Gemini 失敗：{last}"


SHORT = "回答精簡、適合手機閱讀，最多 6 行，不要長篇大論。"

TUTOR = f"你是親切的多益英文家教，對象是台灣中級學習者。用繁體中文說明，英文保留英文。{SHORT}"
TRANSLATE = f"你是翻譯老師。中文就翻成自然英文、英文就翻成中文，翻完用中文點 1 個重點。{SHORT}"
GRAMMAR_ONE = f"你是多益文法老師，針對使用者的句子挑一個最常考的文法點簡短講解，有錯就指出正確寫法。{SHORT}"
VOCAB_ONE = ("你是多益單字老師，出一個多益中高頻單字。格式：\n📖 word (詞性) 中文\n例句：英文（中譯）\n"
             "🧠 克漏字一句，挖空該字，最後附『（答案：word）』。精簡。")
MEANING = "你是英漢字典，只回覆這個英文單字最常用的繁體中文意思，最多10字，不要例句詞性。"


def parse_json(s):
    try:
        return json.loads(s) if s else {}
    except Exception:
        return {}


def extract_int(s):
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


# ---------- 模式：學習計畫（教學）----------
def enter_plan(user):
    """打『教學』：有計畫就接著教下一個，沒計畫就開始問主題。"""
    mode, pending = get_state(user)
    if mode == "plan":
        plan = parse_json(pending)
        if plan.get("theme"):
            return plan_continue(user, plan)
    set_state(user, "setup", json.dumps({"step": "theme"}))
    return ("📚 我們來排個學習計畫！\n"
            "你想學什麼主題或方向？例如：\n"
            "• 出國旅遊　• 商業 email　• 面試英文\n"
            "• 字首 pre-　• 多益高頻字\n\n"
            "直接打你想學的主題 👇")


def handle_setup(user, text, pending):
    st = parse_json(pending)
    if st.get("step") == "theme":
        theme = text.strip()
        set_state(user, "setup", json.dumps({"step": "count", "theme": theme}))
        return f"好！主題就設定為【{theme}】。\n一天想學幾個單字呢？打數字就好（例如 5）👇"
    if st.get("step") == "count":
        n = extract_int(text)
        if not n:
            return "請打一個數字，例如 5 🙂"
        n = max(1, min(n, 20))
        theme = st.get("theme", "多益高頻字")
        plan = {"theme": theme, "count": n, "date": tw_today(), "done": 0}
        set_state(user, "plan", json.dumps(plan))
        intro = (f"📚 開始學【{theme}】，每天 {n} 個！\n"
                 "學完打「繼續」接下一個；隔天也是打「繼續」，主題會一直記住不會亂跳。\n"
                 "想換主題打「換主題」。")
        return [intro, teach_plan_word(user, plan)]


def tts_url(word):
    """Google 免費 TTS，任何英文字都能發音（合成 mp3）。"""
    return f"https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob&tl=en&q={quote(word)}"


def word_card(word, pos, meaning, ex_en, ex_zh, tip, footer, accent="#0D9488"):
    """組一張 LINE Flex 單字卡。"""
    bubble = {
        "type": "bubble", "size": "mega",
        "header": {
            "type": "box", "layout": "vertical", "backgroundColor": accent,
            "paddingAll": "18px", "spacing": "xs",
            "contents": [
                {"type": "text", "text": word, "size": "3xl", "weight": "bold",
                 "color": "#FFFFFF", "wrap": True},
                {"type": "text", "text": f"{pos}　{meaning}", "size": "md",
                 "color": "#FFFFFFEE", "wrap": True},
            ],
        },
        "body": {
            "type": "box", "layout": "vertical", "spacing": "sm", "paddingAll": "18px",
            "contents": [
                {"type": "text", "text": "例句", "size": "xs", "weight": "bold", "color": accent},
                {"type": "text", "text": ex_en, "size": "md", "wrap": True, "color": "#222222"},
                {"type": "text", "text": ex_zh, "size": "sm", "wrap": True, "color": "#999999"},
                {"type": "separator", "margin": "lg"},
                {"type": "box", "layout": "baseline", "margin": "lg", "spacing": "sm",
                 "contents": [
                     {"type": "text", "text": "💡", "flex": 0, "size": "sm"},
                     {"type": "text", "text": tip, "size": "sm", "wrap": True, "color": "#555555"},
                 ]},
            ],
        },
        "footer": {
            "type": "box", "layout": "vertical", "paddingAll": "12px", "spacing": "sm",
            "contents": [
                {"type": "button", "style": "primary", "color": accent, "height": "sm",
                 "action": {"type": "uri", "label": "🔊 發音", "uri": tts_url(word)}},
                {"type": "text", "text": footer, "size": "xs", "color": "#AAAAAA",
                 "align": "center", "wrap": True},
            ],
        },
    }
    return {"flex": bubble, "alt": f"單字卡：{word}（{meaning}）"}


def teach_plan_word(user, plan):
    """依主題教下一個沒學過的單字，回傳 Flex 單字卡；自動記錄並更新進度。"""
    known = [r["word"] for r in list_words(user)][-100:]
    avoid = "、".join(known) if known else "（無）"
    theme = plan["theme"]
    sys = (
        f"你是多益單字老師，用「{theme}」這個主題循序漸進教單字，由常用到少用、由易到難，一次一個。"
        f"避開這些學過的字：{avoid}。\n"
        "只輸出一行，用半形直線 | 分隔六個欄位，不要多餘文字：\n"
        "英文單字|詞性(如 n./v./adj.)|中文意思|一句英文例句|該例句中文翻譯|一句超簡短記憶點或用法(繁中)"
    )
    out = ask_gemini(sys, f"主題：{theme}，教下一個單字")
    if out.startswith("⚠️"):
        return "⚠️ 剛剛系統有點忙，沒抓到單字，請再打一次「繼續」🙏"
    parts = [p.strip() for p in out.strip().splitlines()[0].split("|")]
    if len(parts) < 6:
        return f"{out}\n\n（今天第 {plan.get('done', 0) + 1}/{plan['count']} 個）打「繼續」"
    word, pos, meaning, ex_en, ex_zh, tip = parts[:6]
    record_word(user, word, meaning)
    plan["done"] = plan.get("done", 0) + 1
    set_state(user, "plan", json.dumps(plan))
    footer = f"今天第 {plan['done']}/{plan['count']} 個  · 打「繼續」學下一個"
    return word_card(word, pos, meaning, ex_en, ex_zh, tip, footer)


def plan_continue(user, plan, force=False):
    if plan.get("date") != tw_today():   # 新的一天：主題不變，只把今日進度歸零
        plan["date"] = tw_today()
        plan["done"] = 0
        set_state(user, "plan", json.dumps(plan))
    if plan.get("done", 0) >= plan["count"] and not force:
        return (f"🎉 今天【{plan['theme']}】的 {plan['count']} 個單字學完了！\n"
                f"明天打「繼續」接著學；想現在多學就打「更多」。\n"
                f"（打「考試」可以測驗今天學的字）")
    return teach_plan_word(user, plan)


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


def cmd_word():
    """/單字：隨機一個多益單字，做成單字卡（不記錄，附收藏提示）。"""
    sys = ("你是多益單字老師，隨機出一個多益中高頻單字。只輸出一行，用半形直線 | 分隔六欄，不要多餘文字：\n"
           "英文單字|詞性(如 n./v./adj.)|中文意思|一句英文例句|該例句中文翻譯|一句超簡短記憶點(繁中)")
    out = ask_gemini(sys, "出一個單字")
    if out.startswith("⚠️"):
        return out
    parts = [p.strip() for p in out.strip().splitlines()[0].split("|")]
    if len(parts) < 6:
        return out
    word, pos, meaning, ex_en, ex_zh, tip = parts[:6]
    return word_card(word, pos, meaning, ex_en, ex_zh, tip,
                     f"打「/記 {word} {meaning}」可收藏　·　「考試」測驗")


def review_card(word, meaning, date):
    bubble = {
        "type": "bubble", "size": "kilo",
        "header": {
            "type": "box", "layout": "vertical", "backgroundColor": "#DB2777",
            "paddingAll": "16px", "spacing": "xs",
            "contents": [
                {"type": "text", "text": "🧠 複習", "size": "sm", "color": "#FFFFFFDD"},
                {"type": "text", "text": meaning, "size": "xxl", "weight": "bold",
                 "color": "#FFFFFF", "wrap": True},
            ],
        },
        "body": {
            "type": "box", "layout": "vertical", "spacing": "sm", "paddingAll": "16px",
            "contents": [
                {"type": "text", "text": "答案", "size": "xs", "weight": "bold", "color": "#DB2777"},
                {"type": "text", "text": word, "size": "xl", "weight": "bold",
                 "color": "#222222", "wrap": True},
                {"type": "text", "text": f"學於 {date}", "size": "xs", "color": "#AAAAAA"},
                {"type": "button", "style": "primary", "color": "#DB2777", "height": "sm", "margin": "md",
                 "action": {"type": "uri", "label": "🔊 發音", "uri": tts_url(word)}},
            ],
        },
    }
    return {"flex": bubble, "alt": f"複習：{meaning} = {word}"}


# ---------- SRS 間隔複習 ----------
# 每答對一次，下次複習間隔拉長（天）；答錯則縮回、隔天再考
SRS_DAYS = [1, 2, 4, 7, 15, 30, 60, 120]


def srs_key(uid):
    return f"{uid}::srs"


def get_srs(uid):
    _, pending = get_state(srs_key(uid))
    return parse_json(pending)


def save_srs(uid, data):
    set_state(srs_key(uid), "srs", json.dumps(data, ensure_ascii=False))


def compute_due(words, srs, today):
    """回傳到期(或從未複習)的單字，去重。"""
    due, seen = [], set()
    for r in words:
        w = r["word"]
        if w in seen:
            continue
        seen.add(w)
        s = srs.get(w)
        if s is None or s.get("next", "") <= today:
            due.append(r)
    return due


def apply_grade(srs, word, correct, today):
    s = srs.get(word, {"lv": 0, "seen": 0, "miss": 0})
    s["seen"] = s.get("seen", 0) + 1
    if correct:
        s["lv"] = min(s.get("lv", 0) + 1, len(SRS_DAYS) - 1)
    else:
        s["miss"] = s.get("miss", 0) + 1
        s["lv"] = max(0, s.get("lv", 0) - 1)
    days = SRS_DAYS[s["lv"]] if correct else 1
    s["next"] = (datetime.date.fromisoformat(today) + datetime.timedelta(days=days)).isoformat()
    srs[word] = s
    return srs


def start_review(uid):
    words = list_words(uid)
    if not words:
        return "還沒有記錄的單字，先打「教學」學幾個吧 📖"
    srs = get_srs(uid)
    due = compute_due(words, srs, tw_today())
    if not due:
        return "🎉 目前沒有到期要複習的字，記得很棒！\n想加強可打「考試」隨機測，或「教學」學新的。"
    r = due[0]
    set_state(uid, "review", json.dumps({"answer": r["word"], "meaning": r["meaning"], "n": 0, "ok": 0}))
    return (f"🔁 智慧複習開始！有 {len(due)} 個到期的字。答錯沒關係，打「結束」可停。\n\n"
            f"Q1：「{r['meaning']}」的英文是？")


def review_answer(uid, text, pending):
    try:
        st = json.loads(pending)
    except Exception:
        return start_review(uid)
    today = tw_today()
    correct = text.strip().lower() == st["answer"].strip().lower()
    srs = apply_grade(get_srs(uid), st["answer"], correct, today)
    save_srs(uid, srs)
    n = st.get("n", 0) + 1
    ok = st.get("ok", 0) + (1 if correct else 0)
    fb = f"✅ 答對！{st['answer']}" if correct else f"❌ 答案是 {st['answer']}（{st['meaning']}）"

    due = [r for r in compute_due(list_words(uid), srs, today) if r["word"] != st["answer"]]
    if not due:
        set_state(uid, "chat", "")
        return f"{fb}\n\n🏁 複習完成！這輪答對 {ok}/{n}，到期的字都複習完了 👏"
    r = due[0]
    set_state(uid, "review", json.dumps({"answer": r["word"], "meaning": r["meaning"], "n": n, "ok": ok}))
    return f"{fb}　(答對 {ok}/{n})\n\nQ{n + 1}：「{r['meaning']}」的英文是？"


def end_review(uid, pending):
    try:
        st = json.loads(pending)
        n, ok = st.get("n", 0), st.get("ok", 0)
    except Exception:
        n, ok = 0, 0
    set_state(uid, "chat", "")
    if n == 0:
        return "複習結束，回到一般模式 🙂"
    return f"🏁 複習結束！這輪答對 {ok}/{n}。到期的字之後會再排給你複習。"


HELP = (
    "🔤 模式（打這些字切換，會記住）：\n"
    "• 教學 → 排學習計畫，照主題每天教你單字\n"
    "　（打「繼續」學下一個、「換主題」換方向）\n"
    "• 文法 → 文法教學\n"
    "• 考試 → 用你學過的單字考你計分\n"
    "• 聊天 → 一般家教\n\n"
    "⚡ 指令（隨時可用）：\n"
    "• /翻譯 句子　• /文法 句子　• /單字\n"
    "• /記 單字 中文　• /今天　• /複習\n\n"
    "💡 隨時打「選單」可再看到這份說明。"
)

WELCOME = (
    "👋 歡迎使用「多益學習小助手」！\n"
    "我會幫你排學習計畫、照主題每天教你單字，還會自動記錄、幫你考試 📚\n\n"
    "🔤 打這些字切換模式（會記住）：\n"
    "• 教學 → 排計畫學單字（我先問你主題和一天幾個）\n"
    "• 文法 → 教你多益文法\n"
    "• 考試 → 用你學過的字考你、算分\n"
    "• 聊天 → 一般英文家教，什麼都能問\n\n"
    "🔁 學單字時：打「繼續」接下一個、「換主題」換方向\n"
    "⚡ 指令：/翻譯 句子　/單字　/今天　/複習\n\n"
    "💡 現在就打「教學」開始排你的計畫吧！\n"
    "（隨時打「操作手冊」看完整教學）"
)

MANUAL = (
    "📖 操作手冊 ─ 多益學習小助手\n"
    "━━━━━━━━━━━━━━\n"
    "用法只有兩種：①打「模式」的名字切換　②打「/指令」\n\n"

    "【① 四種模式】打名字就切換，會一直記住\n\n"
    "▎教學（幫你排計畫學單字）\n"
    "打「教學」→ 我問你想學的主題→ 再問一天幾個→ 開始一個一個教，還會自動幫你記起來。\n"
    "　例：\n"
    "　你：教學\n"
    "　我：想學什麼主題？\n"
    "　你：出國旅遊\n"
    "　我：一天幾個？\n"
    "　你：5\n"
    "　我：📖 destination 目的地…（第1/5個）\n"
    "　你：繼續 → 教下一個\n"
    "　• 隔天再打「繼續」就接著學，主題不會跑掉\n"
    "　• 想換方向 → 打「換主題」\n"
    "　• 當天想多學 → 打「更多」\n\n"
    "▎文法\n"
    "打「文法」→ 貼一句英文我幫你抓文法重點；打「下一個」教你新的多益文法點。\n\n"
    "▎考試\n"
    "打「考試」→ 用你學過的單字考你，直接打英文作答、即時算分；打「結束」看成績。\n\n"
    "▎聊天\n"
    "打「聊天」→ 一般英文家教，任何問題都能問。\n\n"

    "【② 隨時能用的指令】記得加斜線 /\n"
    "• /翻譯 我明天開會 → 中英互譯\n"
    "• /文法 一句英文 → 看這句的文法\n"
    "• /單字 → 隨機一個多益單字\n"
    "• /記 apple 蘋果 → 手動記一個字\n"
    "• /今天 → 看今天學了哪些\n"
    "• /複習 → 智慧複習：優先考到期、常錯的字；答對拉長間隔、答錯隔天再考\n\n"

    "【小提醒】\n"
    "• 每個人資料分開，朋友加同一個 bot 也不會混在一起\n"
    "• 剛睡醒的第一句可能慢 30 秒，之後就快了\n"
    "• 隨時打「操作手冊」或「選單」看說明"
)

SWITCH = {
    "grammar": {"文法", "文法模式", "學文法"},
    "translate": {"翻譯", "翻譯模式"},
    "exam": {"考試", "測驗", "考我"},
    "chat": {"聊天", "一般", "回家教", "家教"},
}

PLAN_ENTER = {"教學", "學單字", "背單字", "單字教學", "計畫", "學習計畫", "排課"}
PLAN_NEXT = {"繼續", "下一個", "next", "繼續學", "再一個"}
PLAN_MORE = {"更多", "多學", "再多一個", "加碼"}
PLAN_RESET = {"換主題", "重新設定", "換方向", "重設計畫"}


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
        return cmd_word()
    if s.startswith("/記"):
        return cmd_record(user, s[2:].strip())
    if s.startswith("/今天"):
        return cmd_today(user)
    if s.startswith("/複習") or s in ("複習", "智慧複習"):
        return start_review(user)
    if s in ("/help", "/說明", "help", "?"):
        return HELP
    if s in ("選單", "開始", "menu", "使用說明", "怎麼用"):
        return WELCOME
    if s in ("操作手冊", "手冊", "說明書", "教學手冊", "怎麼玩", "使用手冊"):
        return MANUAL

    # 2) 進入學習計畫（教學）
    if s in PLAN_ENTER:
        return enter_plan(user)

    # 3) 模式切換（整句剛好是關鍵字才切換）
    for mode, kws in SWITCH.items():
        if s in kws:
            if mode == "exam":
                return start_exam(user)
            set_state(user, mode, "")
            names = {"grammar": "文法教學", "translate": "翻譯", "chat": "一般家教"}
            tip = {"grammar": "貼一句英文我幫你看文法，或打「下一個」教你新重點。",
                   "translate": "直接打中文或英文，我就幫你翻譯並點重點。",
                   "chat": "有什麼英文問題都可以問我。"}[mode]
            return f"已切換到「{names[mode]}」模式 ✅\n{tip}\n（打「教學」「考試」等可再切換）"

    # 4) 依目前模式處理
    mode, pending = get_state(user)

    if mode == "setup":
        return handle_setup(user, s, pending)

    if mode == "exam":
        if s in ("結束", "停", "stop", "退出"):
            return end_exam(user, pending)
        return exam_answer(user, s, pending)

    if mode == "review":
        if s in ("結束", "停", "stop", "退出"):
            return end_review(user, pending)
        return review_answer(user, s, pending)

    if mode == "plan":
        plan = parse_json(pending)
        if s in PLAN_RESET:
            set_state(user, "setup", json.dumps({"step": "theme"}))
            return "好，換主題！告訴我新的主題或方向（例如：出國旅遊 / 字首 pre-）👇"
        if s in PLAN_MORE:
            return plan_continue(user, plan, force=True)
        return plan_continue(user, plan)   # 繼續 或其他任何輸入 → 教下一個

    if s in PLAN_NEXT or s in PLAN_MORE or s in PLAN_RESET:
        return "你還沒有學習計畫，打「教學」開始排一個 📚"

    if mode == "grammar":
        return grammar_teach(s)
    if mode == "translate":
        return ask_gemini(TRANSLATE, s)
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


def to_messages(result):
    """route() 可能回傳文字、Flex 卡片(dict)、或它們的列表 → 轉成 LINE 訊息物件。"""
    items = result if isinstance(result, list) else [result]
    msgs = []
    for it in items:
        if isinstance(it, dict) and it.get("flex"):
            msgs.append(FlexMessage(alt_text=it.get("alt", "單字卡"),
                                    contents=FlexContainer.from_dict(it["flex"])))
        else:
            msgs.append(TextMessage(text=str(it)))
    return msgs[:5]  # LINE 一次最多 5 則


@handler.add(MessageEvent, message=TextMessageContent)
def on_message(event):
    user_id = getattr(event.source, "user_id", "unknown")
    reply = route(event.message.text, user_id)
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(reply_token=event.reply_token,
                                messages=to_messages(reply))
        )


@handler.add(FollowEvent)
def on_follow(event):
    """有人加 bot 好友時，自動送上使用說明。"""
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(reply_token=event.reply_token,
                                messages=[TextMessage(text=WELCOME),
                                          TextMessage(text=MANUAL)])
        )


@app.route("/")
def health():
    return "LINE English Bot is running."


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
