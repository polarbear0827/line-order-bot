芳苑點餐機器人
ngork
35shyvOOSJg9IIHO5eMXuLVuejg_5pZUZKBirAMZFdHmm9PTC

Channel secret
748ac77466ba2e7bd2aecaf8f299b7cb

Channel access token
ond6EnKwZ18G/mW8wqU8W4CgzeiIvbmZzR7SpcUzajiBTHhsWQgZrxCojGaWjUPkKx5j7kXQpaulBqDSJMHCtuqluWH3nhY98vyu7/Dipc50c/dJvVC6GFXVze88/9zOaNa+wrXSEVOc7IPqAFVcNwdB04t89/1O/w1cDnyilFU=


LINE 辦公室點餐系統 v2.0 啟動與操作手冊
==============================================

本手冊指導您如何啟動並配置本地開發環境，確保 LINE 機器人 (Bot) 和管理後台 (Admin Panel) 正常運作。

---

## ⚙️ 第一部分：系統啟動與連線設定 (每次啟動 Bot 必做)

啟動 Bot 服務需要兩個獨立的程序：Python Flask 伺服器與 Ngrok 隧道。

步驟 1：啟動 Flask 應用程式 (Python Server)

1.  開啟您的終端機（或 PyCharm 內建的 Terminal）。
2.  進入您的專案資料夾 (order_system_v2)。
3.  執行 app.py 檔案：
    python app.py
4.  檢查輸出：程式啟動後，您應該看到類似以下訊息：
    > 🍱 辦公室點餐系統 v2.0
    > ✅ 系統初始化完成
    > 📝 管理員金鑰: [您的金鑰]
    > 🌐 本機訪問: http://127.0.0.1:5000


步驟 2：啟動 Ngrok 隧道與複製網址

每次 Ngrok 啟動都會產生新的網址，必須將此網址同步更新到 LINE Developers Console。

1.  另開一個新的終端機視窗，操作方式為"D:\MyTools\ngrok-v3-stable-windows-amd64" 對該ngrok.exe點兩下
2.  若需登入帳號請輸入 ngrok config add-authtoken 35smnwYSwjD39kXyYIozWT6ByFt_3LZ6ZLhd7ExDhuxey1MTH
3.  執行 Ngrok 指令：
    ngrok http 5000
4.  複製 HTTPS 網址：找到 Forwarding 行，複製 https:// 開頭的網址。
    > 範例：https://[亂碼].ngrok-free.app


步驟 3：更新 LINE Developers Webhook URL (重要！每次 Ngrok 換網址都要做)

1.  前往 LINE Developers Console，登入並選擇您的 Bot Channel。
2.  切換到 Messaging API 分頁。
3.  找到 Webhook settings 區塊。
4.  將 步驟 2 複製的 Ngrok 網址貼入 Webhook URL 欄位，並在網址尾端加上 /callback。
    > 範例：https://[亂碼].ngrok-free.app/callback
5.  點擊 Verify 確認連線成功。
6.  確認 Use webhook 開關為 ✅ 綠色 (ON)。

---

## 🔒 第二部分：一次性基礎配置檢查 (僅需初次設定)

以下設定只需在第一次建置時檢查，之後通常無需變動。

| 檢查項目 | 檢查位置 | 正確狀態 | 備註 |
| :--- | :--- | :--- | :--- |
| **Channel Secret/Token** | config.py 檔案 | 必須替換為您 Bot 的真實密碼。 | 如果使用範例密碼，Bot 將無法回覆。 |
| **回應模式** | LINE OA Manager > ⚙️設定 > 回應設定 | ✅ Bot | |
| **自動回應訊息** | LINE OA Manager > ⚙️設定 > 回應設定 | ❌ 停用 (Off) | 避免 LINE 的罐頭訊息干擾。 |
| **Webhook 啟用** | LINE OA Manager > ⚙️設定 > 回應設定 | ✅ 啟用 (On) | |

---

## 🤖 第三部分：Bot 核心指令與範例

| 指令 | 說明 | 格式範例 |
| :--- | :--- | :--- |
| **統整點餐** | 用於輸入多筆訂單。指令後為餐點類型，接著每行一筆訂單。 | !order 午餐<br>2. 雞腿便當<br>3. 魚便當 |
| **快速新增** | 用於快速新增單一訂單。 | !add 2 雞腿便當 |
| **查詢帳單** | 查詢自己的訂單總額。 | 輸入自己的**使用者代號**（例如：2） |
| **顯示說明** | 顯示 Bot 使用說明。 | 說明 或 指令 |
| **查詢摘要** | 查詢今日訂單總覽（僅管理員可用）。 | !summary |