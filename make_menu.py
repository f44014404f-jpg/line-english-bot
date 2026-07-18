# -*- coding: utf-8 -*-
"""畫 LINE 圖文選單圖：彩色字卡風。改按鈕就改 cells，重跑後再跑 setup_richmenu.py。"""
from PIL import Image, ImageDraw, ImageFont

W, H = 2500, 1686
PAD, GAP = 44, 32
BG = (15, 23, 42)

CN = "C:/Windows/Fonts/msjhbd.ttc"          # 微軟正黑體 粗
EMO = "C:/Windows/Fonts/seguiemj.ttf"        # 彩色 emoji
f_big = ImageFont.truetype(CN, 150)
f_sub = ImageFont.truetype(CN, 52)
f_emo = ImageFont.truetype(EMO, 150)

# (emoji, 大標, 副標, 卡片色)
cells = [
    ("📚", "學單字", "排計畫學單字", (13, 148, 136)),
    ("🌐", "翻譯",   "中英互譯",     (37, 99, 235)),
    ("📝", "考試",   "測驗學過的字", (234, 88, 12)),
    ("🔤", "文法",   "學多益文法",   (124, 58, 237)),
    ("🔁", "複習",   "隨機抽考",     (219, 39, 119)),
    ("📖", "手冊",   "完整使用說明", (71, 85, 105)),
]

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)
cols, rows = 3, 2
cw = (W - 2 * PAD - (cols - 1) * GAP) / cols
ch = (H - 2 * PAD - (rows - 1) * GAP) / rows


def ctext(cx, y, text, font, fill, emoji=False):
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    if emoji:
        d.text((cx - (r - l) / 2, y), text, font=font, embedded_color=True)
    else:
        d.text((cx - (r - l) / 2, y), text, font=font, fill=fill)


for i, (emo, title, sub, col) in enumerate(cells):
    c, r = i % cols, i // cols
    x0 = PAD + c * (cw + GAP)
    y0 = PAD + r * (ch + GAP)
    cx = x0 + cw / 2
    # 純色圓角卡片
    d.rounded_rectangle([x0, y0, x0 + cw, y0 + ch], radius=48, fill=col)
    # emoji + 標題 + 副標
    ctext(cx, y0 + ch * 0.14, emo, f_emo, None, emoji=True)
    ctext(cx, y0 + ch * 0.44, title, f_big, (255, 255, 255))
    ctext(cx, y0 + ch * 0.78, sub, f_sub, (240, 244, 255))

img.save("C:/Users/User/Desktop/line_english_bot/richmenu.png", "PNG")
print("saved richmenu.png", img.size)
