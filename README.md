# 📈 台股自動化交易計畫 TG Bot

每天自動在 **08:00（盤前）** 和 **15:00（盤後）** 發送台股分析到你的 Telegram。
包含：加權指數、熱門 ETF、熱門個股、美股指數、Claude AI 分析、市場情緒圖。

---

## 🚀 設定步驟（只需做一次，約 15 分鐘）

### 第一步：建立 Telegram Bot

1. 打開 Telegram，搜尋 **@BotFather**
2. 傳送 `/newbot`，按照提示取名
3. BotFather 會給你一串 **Bot Token**（例如 `7123456789:AAHxxx...`），先複製存好
4. 搜尋 **@userinfobot**，傳送任意訊息，它會回傳你的 **Chat ID**（例如 `123456789`），複製存好

### 第二步：取得 Gemini API Key （免費）

1. 前往 https://aistudio.google.com/
2. 登入 Google 帳號
3. 點選左側 **Get API Key** → **Create API key**
4. 複製 API Key 存好（只顯示一次）

### 第三步：上傳程式碼到 GitHub

1. 登入 https://github.com/，點右上角 **+** → **New repository**
2. 取任意名稱（例如 `taiwan-stock-bot`），選 **Private**，按 **Create repository**
3. 把收到的這 4 個檔案上傳到 repo 根目錄：
   - `main.py`
   - `requirements.txt`
   - `.github/workflows/stock_bot.yml`（注意：`.github` 資料夾要一起上傳）
   - `README.md`（本檔）

> **提示：** 在 GitHub repo 頁面點 **Add file → Upload files** 可以直接拖曳上傳。
> `.github/workflows/stock_bot.yml` 需要先在電腦建立資料夾結構再上傳，或用 GitHub 網頁介面建立檔案。

### 第四步：設定 GitHub Secrets（最重要！）

1. 進入你的 GitHub repo
2. 點選上方 **Settings** 標籤
3. 左側選單點 **Secrets and variables → Actions**
4. 點 **New repository secret**，依序新增以下 3 個：

| Name | Value |
|------|-------|
| `TELEGRAM_BOT_TOKEN` | 第一步複製的 Bot Token |
| `TELEGRAM_CHAT_ID` | 第一步複製的 Chat ID |
| `GEMINI_API_KEY` | 第二步複製的 API Key |

### 第五步：啟用 GitHub Actions

1. 點選 repo 上方 **Actions** 標籤
2. 如果看到提示「Workflows aren't being run on this repository」，點 **I understand my workflows, go ahead and enable them**
3. 完成！之後會在每週一到週五自動執行

---

## 🧪 立即測試

不想等到明天早上 8 點？可以手動觸發：

1. 點 **Actions** 標籤
2. 左側點選 **台股 TG Bot**
3. 右側點 **Run workflow**
4. 選擇 `premarket` 或 `postmarket`
5. 點 **Run workflow** 按鈕
6. 約 1~2 分鐘後，Telegram 會收到通知 ✅

---

## 📋 自動執行時間

| 時間 | 台灣時間 | 內容 |
|------|----------|------|
| 週一到週五 08:00 | 盤前 | 交易計畫：關注標的、支撐壓力、操作建議 |
| 週一到週五 15:00 | 盤後 | 分析報告：今日強弱、資金動向、明日展望 |

---

## 💰 費用估算

| 服務 | 費用 |
|------|------|
| GitHub Actions | 免費（每月 2000 分鐘，本 Bot 每月約用 20 分鐘） |
| Telegram Bot | 免費 |
| yfinance 股市資料 | 免費 |
| Gemini API | 免費（每天 1500 次，本 Bot 每天只用 2 次） |

---

## ❓ 常見問題

**Q: Telegram 沒收到訊息？**
先手動觸發一次，在 GitHub Actions 頁面查看 log，確認 3 個 Secrets 都填對了。

**Q: 加權指數數值看起來怪怪的？**
yfinance 台股資料有時會有時差，通常盤後 15:00 後資料最準確。

**Q: 想新增其他股票？**
打開 `main.py`，找到 `STOCKS = {` 這段，參考格式新增即可。台股代號加 `.TW`，例如台達電是 `2308.TW`。

**Q: 想改變通知時間？**
打開 `.github/workflows/stock_bot.yml`，修改 `cron` 那兩行。注意時間是 UTC，台灣時間要減 8 小時。
