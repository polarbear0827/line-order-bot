# 🍱 LINE 點餐機器人 (Order Bot) V2

專為辦公室與消防局設計的 LINE 點餐與記帳機器人。支援快速批次點餐、金額記錄、自動化催帳推播，以及整合 Gemini AI 解析菜單的後台管理系統。

## ✨ V2 全新功能

- **🚀 Docker 自架 + Cloudflare Tunnel**：無需購買伺服器，資料不外流，安全又穩定。
- **🤖 Gemini AI 菜單解析**：上傳菜單照片，AI 自動辨識品項與價格，省去手動輸入麻煩。
- **💳 自動帶入金額**：替代役男只需一行指令，系統自動從資料庫比對品項並填入金額。
- **📊 現代化管理後台**：美觀的淡色調管理介面，支援 Excel 批次匯入人員資料。
- **⏰ 自動催帳排程**：每日 20:30 自動推播未付款提醒，拯救代墊人的記憶。

---

## 🛠️ 技術架構

- **後端框架**：Flask 3.0 / Python 3.12
- **資料庫**：SQLite (透過 SQLAlchemy ORM)
- **環境部署**：Docker / Docker Compose
- **網路穿透**：Cloudflare Tunnel
- **AI 整合**：Google Gemini 2.0 Flash
- **排程**：APScheduler

---

## 🚀 快速部署教學

### 1. 準備工作

1. 安裝 [Docker Desktop](https://docs.docker.com/desktop/)
2. 準備以下 API Keys：
   - **LINE Messaging API** (Channel Secret & Access Token)
   - **Google Gemini API Key** ([在此申請](https://aistudio.google.com/apikey))
   - **Cloudflare Tunnel Token** ([Cloudflare Zero Trust 後台取得](https://dash.cloudflare.com/))

### 2. 下載與設定

```bash
# 1. 複製專案
git clone https://github.com/polarbear0827/line-order-bot.git
cd line-order-bot

# 2. 設定環境變數
cp .env.example .env
```

請編輯 `.env` 檔案，填入你的 API Key、Cloudflare Token 及管理員密碼：
```ini
LINE_CHANNEL_SECRET=your_secret
LINE_CHANNEL_ACCESS_TOKEN=your_token
GEMINI_API_KEY=your_gemini_key
CLOUDFLARE_TUNNEL_TOKEN=your_cloudflare_token
ADMIN_ACCESS_KEY=你的後台登入密碼
SECRET_KEY=隨便打一串亂碼
```

### 3. 一鍵啟動

```bash
docker compose up -d --build
```
> 執行完畢後，伺服器與 Cloudflare Tunnel 會在背景自動運行。資料庫與上傳的圖片會永久保存在 `./data` 與 `./static` 資料夾中。

---

## 📱 LINE 核心指令

| 指令 | 說明 |
|------|------|
| `!點 [代墊人代號]` | **主要點餐**（換行輸入: `代號. 品項名稱`） |
| `!bill [代號]` 或直接輸數字 | 查該員**個人帳單**與欠款 |
| `!今日` | 查看今日全體訂單摘要 |
| `!結清 [代號]` | 將該員的欠款全部標記為「已付款」 |
| `!說明` | 顯示指令教學 |

**點餐指令範例：**
```
!點 18
2. 肉羹飯
5. 沙茶牛肉炒麵
7. 雞腿便當
```
*(18 號為本次代墊人，2、5、7 號為點餐人員)*

---

## ⚙️ 管理員後台

啟動後，請訪問你的伺服器網址（或 Cloudflare Tunnel 綁定的網址）。

預設登入密碼為你在 `.env` 中設定的 `ADMIN_ACCESS_KEY`。

**後台功能：**
- **人員管理**：新增/編輯/刪除，支援上傳 Excel 批次匯入（A欄:代號, B欄:姓名）。
- **店家與菜單**：上傳菜單照片，AI 會幫你自動填好品項與價格表。
- **每日記帳**：快速查看當日欠款，一鍵切換付款狀態。
- **補登訂單**：漏掉的訂單可隨時透過表單補回。
