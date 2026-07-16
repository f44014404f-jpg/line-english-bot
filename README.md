# LINE 英文小助手

翻譯 / 多益文法 / 單字練習。用 LINE + Google Gemini（都免費）。

---

## 一、先拿三把金鑰

### 1. Gemini API Key（教英文的大腦）
1. 去 https://aistudio.google.com/apikey
2. 用 Google 帳號登入 →「Create API key」
3. 複製那串 key

### 2. LINE 的兩把 key
1. 去 https://developers.line.biz/console/ 用 LINE 帳號登入
2. 建一個 Provider（隨便取名，例如你的名字）
3. 在裡面建一個 **Messaging API channel**
4. 在該 channel：
   - 「Basic settings」找 **Channel secret** → 這是 `LINE_CHANNEL_SECRET`
   - 「Messaging API」頁最下面 issue **Channel access token (long-lived)** → 這是 `LINE_CHANNEL_ACCESS_TOKEN`
5. 同一頁把 **Auto-reply messages / Greeting** 關掉（不然會跟你的 bot 打架）

---

## 二、本機先測會不會動（電腦要開著）

```powershell
# 在這個資料夾開 PowerShell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

把 `.env.example` 複製一份改名成 `.env`，填入剛剛三把金鑰，然後：

```powershell
python app.py
```

看到 `Running on http://0.0.0.0:5001` 就代表程式活著。

### 用 ngrok 讓 LINE 連得到你
LINE 在雲端，連不到你家電腦，需要 ngrok 打一條臨時通道：

1. 去 https://ngrok.com 註冊，下載 ngrok
2. 另開一個 PowerShell：
   ```powershell
   ngrok http 5001
   ```
3. 會出現一行 `https://xxxx.ngrok-free.app` → 複製它

### 把 webhook 填回 LINE
回 LINE console →「Messaging API」→ Webhook URL 填：
```
https://xxxx.ngrok-free.app/callback
```
按 **Verify** 應該成功，並把 **Use webhook** 打開。

### 測試
用手機加這個 bot 為好友（LINE console 有 QR code），傳訊息給它：
- `/單字`
- `/翻譯 我明天要開會`
- `/文法 He have gone to school.`
- 直接問 `TOEIC 的 make 和 do 差在哪`

> ⚠️ ngrok 的網址每次重開會變，要重填 webhook。這只是測試階段，正式上雲端就固定了。

---

## 三、部署到 Render（電腦關機也能用）

1. 把這個資料夾推到一個 GitHub repo（`.env` 已被 `.gitignore` 擋掉，不會外洩）
2. Render → New → **Web Service** → 選這個 repo
3. 設定：
   - Build Command：`pip install -r requirements.txt`
   - Start Command：`gunicorn app:app`
4. 在 Render 的 **Environment** 頁，把三個環境變數加進去：
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `LINE_CHANNEL_SECRET`
   - `GEMINI_API_KEY`
5. 部署完會給你一個固定網址 `https://yyyy.onrender.com`
6. 回 LINE console 把 Webhook URL 改成：
   ```
   https://yyyy.onrender.com/callback
   ```

完成。之後你電腦關機也能用。

> 免費版閒置 15 分鐘會休眠，休眠後第一句話會慢約 30 秒喚醒，之後正常。個人自用夠了。

---

## 常見問題

- **傳訊息沒反應** → 檢查 LINE console 的 Auto-reply 有沒有關、Webhook 有沒有打開、Verify 有沒有過。
- **回「呼叫 Gemini 失敗」** → GEMINI_API_KEY 錯了，或今天免費額度用完了。
- **想每天早上自動推單字** → 那需要另外做排程 + LINE Push（每月 200 則免費），跟我說再加。
