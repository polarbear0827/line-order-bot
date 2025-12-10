# 🍱 LINE 辦公室點餐系統 v2.0

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Flask-3.0-green?logo=flask&logoColor=white" alt="Flask">
  <img src="https://img.shields.io/badge/LINE-Bot-00C300?logo=line&logoColor=white" alt="LINE Bot">
  <img src="https://img.shields.io/badge/Deploy-Render-purple?logo=render&logoColor=white" alt="Render">
</p>

<p align="center">
  <b>一個專為辦公室設計的 LINE 點餐機器人，讓團購訂餐變得輕鬆簡單！</b>
</p>

---

## ✨ 功能特色

| 功能 | 說明 |
|------|------|
| 📝 **批次點餐** | 一次輸入多人訂單，快速又方便 |
| 💰 **代墊記帳** | 自動記錄誰幫誰代墊，帳目清清楚楚 |
| 📊 **帳務統計** | 每日自動統計，欠款一目了然 |
| ⏰ **定時提醒** | 每晚 8 點自動發送未付款通知 |
| 🎲 **隨機選餐** | 不知道吃什麼？讓機器人幫你決定！ |
| 🖼️ **菜單查詢** | 快速查詢各店家菜單圖片 |

---

## 🚀 指令一覽

### 📝 點餐相關

```
!order [餐別] [代墊人]    批次點餐
!點餐 [餐別] [代墊人]

範例：
!order 午餐
2. 雞腿便當
3. 魚便當
5. 滷肉飯
```

```
!add [代號] [餐點]        快速加點
!加點 [代號] [餐點]

範例：!add 2 雞腿便當
```

```
!enter [日期] [餐別] [代號] [餐點]    補登訂單
!補登 [日期] [餐別] [代號] [餐點]

範例：!enter 10/24 午餐 2 牛肉飯
```

### 💰 金額 / 結帳

```
!amount [餐別]           批次輸入金額
!金額 [餐別]

範例：
!amount 午餐
2. 100
3. 85
```

```
!checkout [代號]         結清欠款
!結清 [代號]

範例：!結清 2
```

### 🔍 查詢相關

```
!bill [代號]             查詢個人帳單
或直接輸入代號

範例：!bill 2 或 2
```

```
!today                   查看今日訂單
!今日
```

```
!show [日期] [餐別]      查看指定日期訂單
!查詢 [日期] [餐別]

範例：!show 10/24 午餐
```

```
!代墊 [代號]             查詢代墊統計
!欠款 [代號]             查詢欠款明細
```

### 🍽️ 菜單相關

```
!menu [關鍵字]           搜尋菜單
!菜單 [關鍵字]

範例：!menu 米糕
```

```
!吃什麼 [餐別]           隨機推薦
!eat what [餐別]

範例：!吃什麼 午餐
```

### ⚙️ 其他

```
!help / !說明 / !指令    顯示說明
```

---

## 🛠️ 技術架構

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   LINE App      │────▶│   Render        │────▶│   SQLite DB     │
│   (使用者)      │◀────│   (Flask)       │◀────│   (資料儲存)    │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

- **後端框架**：Flask 3.0
- **資料庫**：SQLite + SQLAlchemy
- **LINE SDK**：line-bot-sdk v3
- **排程器**：APScheduler
- **部署平台**：Render

---

## 📦 專案結構

```
line-order-bot/
├── app.py                 # 主程式
├── config.py              # 設定檔
├── models.py              # 資料庫模型
├── line_handler.py        # LINE Bot 處理邏輯
├── requirements.txt       # Python 套件
├── Procfile               # Render 啟動指令
├── .python-version        # Python 版本
├── static/
│   ├── style.css          # 網頁樣式
│   └── random_menus/      # 菜單圖片
│       ├── breakfast/
│       ├── lunch/
│       ├── dinner/
│       └── drink/
└── templates/             # 網頁模板
    ├── login.html
    ├── admin_dashboard.html
    ├── manage_users.html
    ├── daily_accounting.html
    └── history.html
```

---

## 🚀 部署指南

### 1️⃣ Fork 此專案

### 2️⃣ 在 Render 建立 Web Service

1. 前往 [Render](https://render.com) 並連結 GitHub
2. 選擇此 Repository
3. 設定環境變數：

| Key | Value |
|-----|-------|
| `LINE_CHANNEL_SECRET` | 你的 Channel Secret |
| `LINE_CHANNEL_ACCESS_TOKEN` | 你的 Channel Access Token |
| `LINE_GROUP_ID` | 你的群組 ID |
| `ADMIN_ACCESS_KEY` | 管理員密碼 |

### 3️⃣ 設定 LINE Webhook

將 Render 提供的網址 + `/callback` 設定到 LINE Developers Console

```
https://your-app.onrender.com/callback
```

---

## 📸 截圖預覽

### LINE Bot 對話
```
👤 使用者：!order 午餐
            2. 雞腿便當
            3. 魚便當

🤖 機器人：✅ 已記錄 2 筆訂單
            【午餐 - 12/10】
            💳 代墊人：15. 小明
            
            2. 小華 - 雞腿便當
            3. 小美 - 魚便當
```

### 帳單查詢
```
👤 使用者：2

🤖 機器人：📋 2號 小華 的帳單
            ==============================
            
            【今日消費 12/10】
            ⏳ 午餐: $100 (代墊: 15號)
            ------------------------------
            今日已付：$0
            今日未付：$100
            
            【總欠款】
            欠 15. 小明：$100
            ------------------------------
            💰 總計：$100
```

---

## 📄 授權

此專案僅供學習與內部使用。

---

<p align="center">
  Made with ❤️ for 芳苑辦公室
</p>
