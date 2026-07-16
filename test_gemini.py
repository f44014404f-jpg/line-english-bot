"""單獨測 Gemini key 有沒有通。用法：填好 .env 後執行 python test_gemini.py"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from google import genai

key = os.environ.get("GEMINI_API_KEY")
if not key:
    print("❌ 找不到 GEMINI_API_KEY，檢查 .env 檔有沒有填、檔名是不是 .env")
    raise SystemExit(1)

print(f"讀到 key，開頭是 {key[:6]}...（長度 {len(key)}）")

try:
    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="用一句繁體中文跟我打招呼，並說『金鑰測試成功』。",
    )
    print("✅ Gemini 回覆：", resp.text.strip())
except Exception as e:
    print("❌ 呼叫失敗：", e)
