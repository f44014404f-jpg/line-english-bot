# -*- coding: utf-8 -*-
import os, requests
from dotenv import load_dotenv
load_dotenv("C:/Users/User/Desktop/line_english_bot/.env")

TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
H = {"Authorization": f"Bearer {TOKEN}"}
IMG = "C:/Users/User/Desktop/line_english_bot/richmenu.png"

W, Ht = 2500, 1686
cw = [833, 833, 834]
x = [0, 833, 1666]
rh = 843

def area(cx, cy, w, h, text):
    return {"bounds": {"x": cx, "y": cy, "width": w, "height": h},
            "action": {"type": "message", "text": text}}

cells = [
    ("教學", 0), ("翻譯", 1), ("考試", 2),
    ("文法", 0), ("/複習", 1), ("操作手冊", 2),
]
areas = []
for i, (text, col) in enumerate(cells):
    row = i // 3
    areas.append(area(x[col], row * rh, cw[col], rh, text))

body = {
    "size": {"width": W, "height": Ht},
    "selected": True,
    "name": "toeic-menu-v1",
    "chatBarText": "功能選單 ▾",
    "areas": areas,
}

# 1) 先刪掉舊的（避免累積）
old = requests.get("https://api.line.me/v2/bot/richmenu/list", headers=H).json()
for m in old.get("richmenus", []):
    requests.delete(f"https://api.line.me/v2/bot/richmenu/{m['richMenuId']}", headers=H)
    print("刪除舊選單", m["richMenuId"])

# 2) 建立選單
r = requests.post("https://api.line.me/v2/bot/richmenu", headers={**H, "Content-Type": "application/json"}, json=body)
print("create:", r.status_code, r.text)
rid = r.json()["richMenuId"]

# 3) 上傳圖片
with open(IMG, "rb") as f:
    r2 = requests.post(f"https://api-data.line.me/v2/bot/richmenu/{rid}/content",
                       headers={**H, "Content-Type": "image/png"}, data=f.read())
print("upload:", r2.status_code, r2.text or "(ok)")

# 4) 設為所有人的預設選單
r3 = requests.post(f"https://api.line.me/v2/bot/user/all/richmenu/{rid}", headers=H)
print("setdefault:", r3.status_code, r3.text or "(ok)")
print("DONE richMenuId =", rid)
