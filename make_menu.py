# -*- coding: utf-8 -*-
from PIL import Image, ImageDraw, ImageFont

W, H = 2500, 1686
PAD, GAP = 48, 36
BG = (15, 23, 42)          # 深藍底
CARD = (248, 250, 252)     # 近白卡片
TITLE = (17, 24, 39)       # 深色標題
SUB = (100, 116, 139)      # 灰色副標

FONT = "C:/Windows/Fonts/msjhbd.ttc"
f_big = ImageFont.truetype(FONT, 150)
f_sub = ImageFont.truetype(FONT, 56)

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

cols, rows = 3, 2
cw = (W - 2 * PAD - (cols - 1) * GAP) / cols
ch = (H - 2 * PAD - (rows - 1) * GAP) / rows

# (大標, 副標, 強調色)
cells = [
    ("學單字", "排計畫學單字", (20, 184, 166)),
    ("繼續",   "學下一個單字", (59, 130, 246)),
    ("考試",   "測驗學過的字", (245, 158, 11)),
    ("文法",   "學多益文法",   (139, 92, 246)),
    ("複習",   "隨機抽考",     (236, 72, 153)),
    ("操作手冊", "完整使用說明", (100, 116, 139)),
]

def center_text(cx, y, text, font, fill):
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    d.text((cx - (r - l) / 2, y), text, font=font, fill=fill)

for i, (title, sub, accent) in enumerate(cells):
    c, r = i % cols, i // cols
    x0 = PAD + c * (cw + GAP)
    y0 = PAD + r * (ch + GAP)
    x1, y1 = x0 + cw, y0 + ch
    cx = x0 + cw / 2
    # 卡片
    d.rounded_rectangle([x0, y0, x1, y1], radius=48, fill=CARD)
    # 大標
    center_text(cx, y0 + ch * 0.28, title, f_big, TITLE)
    # 強調色小橫條
    bw = 150
    by = y0 + ch * 0.62
    d.rounded_rectangle([cx - bw / 2, by, cx + bw / 2, by + 16], radius=8, fill=accent)
    # 副標
    center_text(cx, by + 42, sub, f_sub, SUB)

out = "C:/Users/User/Desktop/line_english_bot/richmenu.png"
img.save(out, "PNG")
print("saved", out, img.size)
