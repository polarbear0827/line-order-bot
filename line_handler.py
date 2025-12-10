from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage as LineTextMessage,
    ImageMessage,  # New: 新增 ImageMessage
    PushMessageRequest
)
from models import db, User, Menu, Order, LineMessage
from datetime import datetime, date
from config import Config
import re
import os       # New: 用於讀取檔案
import random   # New: 用於隨機選取
from urllib.parse import quote

class OrderBot:
    def __init__(self, config):
        self.config = config
        self.configuration = Configuration(access_token=config['LINE_CHANNEL_ACCESS_TOKEN'])

    def send_reply(self, reply_token, text):
        """發送回覆訊息"""
        with ApiClient(self.configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[LineTextMessage(text=text)]
                )
            )

    def parse_order_line_with_payer(self, line):
        """
        解析訂單行（支援個別指定代墊人）
        支援格式：
        - 2. 雞腿便當          → (2, 雞腿便當, None)
        - 2. 雞腿便當 15       → (2, 雞腿便當, 15)
        - 2 雞腿便當 3         → (2, 雞腿便當, 3)

        回傳：(user_code, items, payer_code) 或 None
        """
        # 正則表達式：捕捉 代號、餐點、代墊人（選填）
        patterns = [
            # 格式：2. 雞腿便當 [代墊人]
            r'^(\d+)\.?\s+(.+?)(?:\s+(\d+))?$',
            # 格式：2號 雞腿便當 [代墊人]
            r'^(\d+)號\s+(.+?)(?:\s+(\d+))?$',
            # 格式：代號2 雞腿便當 [代墊人]
            r'^代號\s*(\d+)\s+(.+?)(?:\s+(\d+))?$',
        ]

        for pattern in patterns:
            match = re.match(pattern, line)
            if match:
                user_code = match.group(1).strip()
                items_and_payer = match.group(2).strip()
                explicit_payer = match.group(3)  # 可能是 None

                # 🔥 處理特殊情況：餐點內容後面接數字
                # 例如："雞腿便當 3" → 需判斷 3 是代墊人還是餐點的一部分

                # 如果明確捕捉到第三組（代墊人），就使用它
                if explicit_payer:
                    payer_code = explicit_payer.strip()
                    # items 就是第二組
                    items = items_and_payer
                else:
                    # 沒有明確的代墊人，檢查 items_and_payer 最後是否有數字
                    parts = items_and_payer.rsplit(None, 1)  # 從右邊分割一次
                    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) <= 2:
                        # 最後是數字，且長度 <= 2（代號通常不超過兩位）
                        items = parts[0]
                        payer_code = parts[1]
                    else:
                        # 沒有代墊人
                        items = items_and_payer
                        payer_code = None

                if items:
                    return (user_code, items, payer_code)

        return None

    def handle_order_command(self, message_text, group_id=None):
        """
        處理 !order 指令（支援代墊人）
        格式：
        !order 午餐 [代墊人代號]
        2. 雞腿便當
        3. 魚便當 [個別代墊人]
        5. 滷肉飯

        範例1（預設代墊）：!order 午餐 → 預設15號代墊
        範例2（指定代墊）：!order 午餐 3 → 3號代墊所有訂單
        範例3（混合代墊）：!order 午餐 3
                           2. 雞腿便當
                           5. 滷肉飯 15  → 5號的訂單由15號代墊
        """
        lines = message_text.strip().split('\n')

        # ===== 1. 解析第一行：餐別 + 代墊人 =====
        first_line = lines[0].strip()

        # 移除指令前綴
        first_line = first_line.replace('!order', '').replace('！order', '') \
            .replace('!點餐', '').replace('！點餐', '').strip()

        # 分割餐別和代墊人
        parts = first_line.split()

        # 解析餐別（第一個參數）
        meal_type = self.parse_meal_type(f"!order {parts[0] if parts else ''}")

        # 解析代墊人（第二個參數，如果沒有則預設 15）
        default_payer_code = "15"  # 預設代墊人
        if len(parts) >= 2 and parts[1].isdigit():
            default_payer_code = parts[1]

        # 查找預設代墊人
        default_payer = User.query.filter_by(user_code=default_payer_code).first()
        if not default_payer:
            return f"❌ 代墊人代號 {default_payer_code} 不存在！\n\n請檢查代號是否正確"

        # ===== 2. 檢查或創建今日菜單 =====
        today = date.today()
        menu = Menu.query.filter_by(menu_date=today, meal_type=meal_type).first()

        if not menu:
            menu = Menu(
                meal_type=meal_type,
                menu_date=today,
                description=f"{today.strftime('%Y/%m/%d')} {Config.MEAL_TYPES[meal_type]}"
            )
            db.session.add(menu)
            db.session.commit()

        # ===== 3. 解析訂單（支援個別指定代墊人）=====
        orders_added = []
        errors = []

        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue

            # 🔥 解析格式：代號 + 餐點內容 [+ 代墊人]
            # 例如："2. 雞腿便當" 或 "2. 雞腿便當 3"
            result = self.parse_order_line_with_payer(line)

            if result:
                user_code, items, individual_payer_code = result

                # 查找點餐者
                user = User.query.filter_by(user_code=user_code).first()

                if not user:
                    errors.append(f"代號 {user_code} 不存在")
                    continue

                # 🔥 決定代墊人（個別指定 > 預設）
                if individual_payer_code:
                    payer = User.query.filter_by(user_code=individual_payer_code).first()
                    if not payer:
                        errors.append(f"代墊人 {individual_payer_code} 不存在")
                        continue
                else:
                    payer = default_payer

                # 建立訂單（加入 payer_id）
                order = Order(
                    user_id=user.id,
                    menu_id=menu.id,
                    items=items,
                    payer_id=payer.id  # 🔥 記錄代墊人
                )
                db.session.add(order)

                # 記錄訂單資訊（含代墊人）
                payer_info = f"[代墊: {payer.user_code}]" if payer.id != default_payer.id else ""
                orders_added.append({
                    'text': f"{user_code}. {user.name} - {items} {payer_info}",
                    'payer': payer
                })
            else:
                errors.append(f"無法解析：{line}")

        db.session.commit()

        # ===== 4. 生成回覆訊息 =====
        reply = f"✅ 已記錄 {len(orders_added)} 筆訂單\n"
        reply += f"【{Config.MEAL_TYPES[meal_type]} - {today.strftime('%m/%d')}】\n"
        reply += f"💳 代墊人：{default_payer.user_code}. {default_payer.name}\n\n"

        for order in orders_added:
            reply += f"{order['text']}\n"

        if errors:
            reply += f"\n⚠️ 錯誤：\n"
            for error in errors:
                reply += f"• {error}\n"

        reply += f"\n💡 請至網頁後台輸入金額"

        return reply

    def parse_meal_type(self, first_line):
        """解析餐別"""
        # 移除 !order
        from datetime import datetime
        import pytz
        text = first_line.replace('!order', '').replace('！order', '').replace('!點餐', '').replace('！點餐', '').strip()

        # 取第一個詞（可能是餐別，也可能是代墊人代號）
        first_word = text.split()[0] if text.split() else ''

        # 對應餐別
        meal_mapping = {
            '早餐': 'breakfast',
            '早': 'breakfast',
            '午餐': 'lunch',
            '午': 'lunch',
            '中餐': 'lunch',
            '中': 'lunch',
            '晚餐': 'dinner',
            '晚': 'dinner',
            '飲料': 'drink',
            '點心': 'snack',
            '下午茶': 'snack'
        }

        for key, value in meal_mapping.items():
            if key in text:
                return value

        # 🔥 如果沒有指定餐別，根據當前時間自動判斷
        tw_tz = pytz.timezone('Asia/Taipei')
        now = datetime.now(tw_tz)
        hour = now.hour
        minute = now.minute
        current_time = hour + minute / 60  # 轉換成小數點時間，例如 10:30 = 10.5

        if 5 <= current_time < 10.5:
            return 'breakfast'
        elif 10.5 <= current_time < 14.5:
            return 'lunch'
        elif 14.5 <= current_time < 17.5:
            return 'snack'
        elif 17.5 <= current_time < 21:
            return 'dinner'
        else:
            return 'lunch'  # 深夜預設午餐

    def parse_order_line(self, line):
        """
        解析訂單行
        支援格式：
        - 2. 雞腿便當
        - 2 雞腿便當
        - 2.雞腿便當
        - 2號 雞腿便當
        - 代號2 雞腿便當
        """
        # 正則表達式匹配
        patterns = [
            r'^(\d+)\.?\s*(.+)$',  # 2. 雞腿便當 或 2.雞腿便當
            r'^(\d+)\s+(.+)$',  # 2 雞腿便當
            r'^(\d+)號\s*(.+)$',  # 2號 雞腿便當
            r'^代號\s*(\d+)\s*(.+)$',  # 代號2 雞腿便當
        ]

        for pattern in patterns:
            match = re.match(pattern, line)
            if match:
                user_code = match.group(1).strip()
                items = match.group(2).strip()
                if items:
                    return (user_code, items)

        return None

    def handle_add_command(self, message_text):
        """
        處理 !add 指令（快速新增單筆訂單）
        格式：!add 2 雞腿便當
        """
        parts = message_text.replace('!add', '').replace('！add', '').replace('!加點', '').replace('！加點', '').strip().split(None, 1)

        if len(parts) != 2:
            return "❌ 格式錯誤！\n\n正確格式：\n!add 2 雞腿便當"

        user_code, items = parts

        # 查找使用者
        user = User.query.filter_by(user_code=user_code).first()

        if not user:
            return f"❌ 代號 {user_code} 不存在"

        # 🔥 根據時間自動判斷餐別
        meal_type = self.parse_meal_type("!order")

        # 取得或創建今日菜單
        today = date.today()
        menu = Menu.query.filter_by(menu_date=today, meal_type=meal_type).first()

        if not menu:
            menu = Menu(
                meal_type=meal_type,
                menu_date=today,
                description=f"{today.strftime('%Y/%m/%d')} {Config.MEAL_TYPES[meal_type]}"
            )
            db.session.add(menu)
            db.session.commit()

        # 🔥 預設代墊人（15號）
        default_payer = User.query.filter_by(user_code="15").first()

        # 建立訂單（包含代墊人）
        order = Order(
            user_id=user.id,
            menu_id=menu.id,
            items=items,
            payer_id=default_payer.id if default_payer else None
        )
        db.session.add(order)
        db.session.commit()

        meal_name = Config.MEAL_TYPES.get(meal_type, '未知')
        payer_info = f"\n💳 代墊人：15號" if default_payer else ""
        return f"✅ 已新增訂單\n\n【{meal_name}】\n{user_code}. {user.name} - {items}{payer_info}"

    def handle_bill_query(self, message_text):
        """
        處理帳單查詢（增強：顯示代墊人資訊）
        格式：!bill 2 或直接輸入 2
        """
        # 提取代號
        user_code = message_text.replace('!bill', '').replace('！bill', '') \
            .replace('!結帳', '').replace('！結帳', '') \
            .replace('!帳單', '').replace('！帳單', '').strip()

        if not user_code.isdigit():
            return "❌ 請輸入正確的代號\n\n範例：!bill 2 或直接輸入 2"

        # 查找使用者
        user = User.query.filter_by(user_code=user_code).first()

        if not user:
            return f"❌ 代號 {user_code} 不存在"

        # 查詢今日訂單
        today = date.today()
        today_orders = Order.query.join(Menu).filter(
            Order.user_id == user.id,
            Menu.menu_date == today
        ).all()

        # 今日統計
        today_total = sum(o.amount for o in today_orders)
        today_unpaid = sum(o.amount for o in today_orders if not o.paid)
        today_paid = today_total - today_unpaid

        # 🔥 累計欠款（按代墊人分組）
        unpaid_orders = Order.query.filter_by(user_id=user.id, paid=False).all()

        # 按代墊人分組統計
        debt_by_payer = {}
        for order in unpaid_orders:
            if order.payer_id:
                payer = User.query.get(order.payer_id)
                if payer:
                    payer_key = f"{payer.user_code}. {payer.name}"
                    if payer_key not in debt_by_payer:
                        debt_by_payer[payer_key] = 0
                    debt_by_payer[payer_key] += order.amount

        total_unpaid = sum(debt_by_payer.values())
        total_paid = sum(o.amount for o in Order.query.filter_by(user_id=user.id, paid=True).all())

        # 組合回覆訊息
        reply = f"📋 {user_code}號 {user.name} 的帳單\n"
        reply += "=" * 30 + "\n\n"

        # 今日消費
        if today_orders:
            reply += f"【今日消費 {today.strftime('%m/%d')}】\n"
            for order in today_orders:
                status = "✅" if order.paid else "⏳"
                # 🔥 顯示代墊人
                payer_info = ""
                if order.payer_id:
                    payer = User.query.get(order.payer_id)
                    if payer:
                        payer_info = f" (代墊: {payer.user_code}號)"

                menu_name = Config.MEAL_TYPES.get(order.menu.meal_type, '未知')
                reply += f"{status} {menu_name}: ${int(order.amount)}{payer_info}\n"

            reply += "-" * 30 + "\n"
            reply += f"今日已付：${int(today_paid)}\n"
            reply += f"今日未付：${int(today_unpaid)}\n"
            reply += "\n"

        # 🔥 總欠款（按代墊人列出）
        if debt_by_payer:
            reply += "【總欠款】\n"
            for payer_name, amount in debt_by_payer.items():
                reply += f"欠 {payer_name}：${int(amount)}\n"
            reply += "-" * 30 + "\n"
            reply += f"💰 總計：${int(total_unpaid)}\n\n"
        else:
            reply += "✅ 目前沒有欠款\n\n"

        if total_unpaid > 0:
            reply += f"\n💡 使用 !結清 {user_code} 進行付款"

        return reply

    def handle_today_summary(self):
        """顯示今日訂單摘要"""
        today = date.today()
        today_orders = Order.query.join(Menu).filter(
            Menu.menu_date == today
        ).all()

        if not today_orders:
            return f"📋 今日 ({today.strftime('%m/%d')}) 還沒有訂單"

        # 按餐別統計
        by_meal = {}
        for order in today_orders:
            meal_type = order.menu.meal_type
            if meal_type not in by_meal:
                by_meal[meal_type] = []
            by_meal[meal_type].append(order)

        reply = f"📋 今日訂單摘要 ({today.strftime('%m/%d')})\n\n"

        total_amount = 0
        total_paid = 0

        for meal_type, orders in by_meal.items():
            meal_name = Config.MEAL_TYPES.get(meal_type, '未知')
            reply += f"【{meal_name}】共 {len(orders)} 筆\n"

            for order in orders:
                status = "✅" if order.paid else "⏳"
                reply += f"{status} {order.user.user_code}. {order.user.name} - {order.items} (${order.amount})\n"
                total_amount += order.amount
                if order.paid:
                    total_paid += order.amount

            reply += "\n"

        reply += f"💰 今日總計：${total_amount}\n"
        reply += f"✅ 已收款：${total_paid}\n"
        reply += f"⏳ 未收款：${total_amount - total_paid}"

        return reply

    def handle_help(self):
        """顯示說明"""
        help_text = """🍱 點餐機器人使用說明

══════════════════
📝 點餐相關
══════════════════

!order [餐別] [代墊人]
!點餐 [餐別] [代墊人]
► 批次點餐（每行：代號. 餐點）
► 代墊人可省略，預設15號
範例：
!order 午餐
2. 雞腿便當
3. 魚便當

!add [代號] [餐點]
!加點 [代號] [餐點]
► 快速新增單筆訂單
範例：!add 2 雞腿便當

!enter [日期] [餐別] [代號] [餐點] [代墊人]
!補登 [日期] [餐別] [代號] [餐點] [代墊人]
► 補登過去的訂單
範例：!enter 10/24 午餐 2 牛肉飯

══════════════════
💰 金額 / 結帳
══════════════════

!amount [日期] [餐別]
!金額 [日期] [餐別]
► 批次輸入金額（每行：代號. 金額）
範例：
!amount 午餐
2. 100
3. 85

!checkout [代號] [日期] [餐別]
!結清 [代號] [日期] [餐別]
► 結清欠款（日期/餐別可省略）
範例：
!結清 2 → 結清2號所有欠款
!結清 2 10/24 → 結清2號該日欠款
!結清 2 10/24 午餐 → 結清特定餐別

══════════════════
🔍 查詢相關
══════════════════

!bill [代號] 或直接輸入代號
!帳單 [代號]
► 查詢個人帳單與欠款
範例：!bill 2 或 2

!today
!今日 / !今天
► 查看今日所有訂單

!show [日期] [餐別]
!查詢 / !看單
► 查看指定日期餐別的訂單
範例：!show 10/24 午餐

!show payer [代號]
!代墊 [代號]
► 查詢代墊統計
範例：
!代墊 → 所有代墊人統計
!代墊 15 → 15號代墊明細

!show debt [代號]
!欠款 [代號]
► 查詢某人欠款明細
範例：!欠款 2

══════════════════
🍽️ 菜單相關
══════════════════

!menu [關鍵字]
!菜單 [關鍵字]
► 搜尋特定店家菜單
範例：!menu 米糕

!eat what [餐別]
!吃什麼 [餐別]
► 隨機推薦菜單
範例：!吃什麼 午餐

══════════════════
⚙️ 其他
══════════════════

!help / !說明 / !指令
► 顯示此說明

💡 小提示：
• 餐別可用：早餐/午餐/晚餐/飲料/點心
• 日期可用：10/24 或 2025/10/24
• 每晚 20:00 自動發送未付款提醒
"""
        return help_text

    def send_push_message(self, to_id, text):
        """發送推播訊息到群組或個人"""
        try:
            with ApiClient(self.configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.push_message(
                    PushMessageRequest(
                        to=to_id,
                        messages=[LineTextMessage(text=text)]
                    )
                )
            return True
        except Exception as e:
            print(f"推播訊息失敗: {e}")
            return False

    def generate_daily_unpaid_summary(self):
        """生成每日未付款統計"""
        # 取得所有有未付款訂單的使用者
        users_with_unpaid = db.session.query(User).join(
            Order, User.id == Order.user_id
        ).filter(
            Order.paid == False,
            User.is_admin == False
        ).distinct().order_by(db.cast(User.user_code, db.Integer)).all()

        if not users_with_unpaid:
            return None  # 沒有未付款的訂單

        today = date.today()
        reply = f"📊 每日帳務提醒 ({today.strftime('%Y/%m/%d')} 20:00)\n\n"
        #reply += "【未付款名單】\n\n"

        total_all_unpaid = 0

        for user in users_with_unpaid:
            # 計算該使用者的總欠款
            unpaid_orders = Order.query.filter_by(
                user_id=user.id,
                paid=False
            ).all()

            user_total_unpaid = sum(o.amount for o in unpaid_orders)

            if user_total_unpaid > 0:
                total_all_unpaid += user_total_unpaid
                reply += f"{user.user_code}. {user.name} - 未付款 ${user_total_unpaid}\n"

        #reply += f"\n💰 總計未收款：${total_all_unpaid}"
        #reply += f"\n\n💡 輸入「代號」或「!bill 代號」查詢明細"

        return reply

    # New: 新增發送圖片的方法
    def send_image_reply(self, reply_token, original_content_url, preview_image_url):
        """發送圖片回覆"""
        with ApiClient(self.configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        ImageMessage(
                            original_content_url=original_content_url,
                            preview_image_url=preview_image_url
                        )
                    ]
                )
            )

    # New: 處理 !eat what 指令
    def handle_eat_what(self, message_text, base_url):
        """
        處理 !eat what [餐別]
        隨機挑選一張圖片回傳
        """
        # === 修改開始 ===
        # 1. 先把指令前綴全部清掉，只留下後面的參數
        clean_text = message_text.lower() \
            .replace('!eat what', '') \
            .replace('!吃什麼', '') \
            .replace('！吃什麼', '') \
            .strip()

        # 2. 直接判斷剩餘的文字
        target_meal = None

        # 如果使用者有輸入東西 (例如 "午餐")
        if clean_text:
            if '早' in clean_text: target_meal = 'breakfast'
            elif '午' in clean_text or '中' in clean_text: target_meal = 'lunch'
            elif '晚' in clean_text: target_meal = 'dinner'
            elif '飲' in clean_text or '喝' in clean_text: target_meal = 'drink'
            elif '點' in clean_text: target_meal = 'snack'
        # === 修改結束 ===

        if not target_meal:
            return "text", "❌ 請指定餐別！\n格式：!eat what 午餐\n(支援：早餐、午餐、晚餐、飲料)"

        # 2. 檢查資料夾路徑
        # 注意：這裡假設資料夾結構為 static/random_menus/[meal_type]
        folder_path = os.path.join('static', 'random_menus', target_meal)

        if not os.path.exists(folder_path):
            return "text", f"❌ 找不到 {target_meal} 的圖片資料夾，請確認後台設定。"

        # 3. 讀取所有圖片檔案
        valid_extensions = ('.jpg', '.jpeg', '.png')
        images = [f for f in os.listdir(folder_path) if f.lower().endswith(valid_extensions)]

        if not images:
            return "text", f"📂 {clean_text} 資料夾內沒有圖片，請放入菜單圖片！"

        # 4. 隨機選取一張
        selected_image = random.choice(images)

        # 修正 1: 對檔名進行 URL 編碼 (處理中文和空白鍵)
        # 例如 "雞腿飯.jpg" 會變成 "%E9%9B%9E%E8%85%BF%E9%A3%AF.jpg"
        safe_filename = quote(selected_image)

        # 修正 2: 確保 base_url 是 https 且結尾有斜線
        if base_url.startswith('http://'):
            base_url = base_url.replace('http://', 'https://', 1)
        if not base_url.endswith('/'):
            base_url += '/'

        # 修正 3: 手動組裝網址，強制使用正斜線 / (避免 Windows 的反斜線 \)
        # 最終網址類似: https://xxxx.ngrok-free.app/static/random_menus/lunch/%E9%9B%9E.jpg
        image_url = f"{base_url}static/random_menus/{target_meal}/{safe_filename}"

        # Debug 用：印出網址看對不對 (你可以看 Terminal 的輸出)
        print(f"[DEBUG] 圖片網址: {image_url}")

        # 回傳類型為 image，並附帶 URL
        return "image", image_url

    def handle_menu_query(self, message_text, base_url):
        """
        處理 !menu [關鍵字]
        搜尋特定菜單圖片
        """
        try:
            # 1. 解析指令,取得關鍵字
            parts = message_text.strip().split(None, 1)

            if len(parts) < 2:
                return "text", "❌ 請輸入想查詢的菜單關鍵字!\n範例:!menu 米糕"

            keyword_original = parts[1].strip()  # 保留原始大小寫用於顯示
            keyword = keyword_original.lower()  # 轉小寫用於比對

            print(f"[DEBUG] !menu 關鍵字: '{keyword}'")

            # 2. 定義要搜尋的根目錄
            base_folder = os.path.join('static', 'random_menus')

            if not os.path.exists(base_folder):
                return "text", f"❌ 系統資料夾尚未建立\n請先建立 {base_folder}"

            target_filename = None
            found_folder = None

            # 3. 策略一:先查 Config 裡的別名字典
            aliases = self.config.get('MENU_ALIASES', {})
            print(f"[DEBUG] 別名字典有 {len(aliases)} 個項目")

            if keyword in aliases:
                target_filename = aliases[keyword]
                print(f"[DEBUG] 從別名找到: {keyword} -> {target_filename}")

                # 遍歷所有餐別資料夾找這個檔案
                for meal_type in ['breakfast', 'lunch', 'dinner', 'drink', 'snack']:
                    check_path = os.path.join(base_folder, meal_type, target_filename)
                    if os.path.exists(check_path):
                        found_folder = meal_type
                        print(f"[DEBUG] 在 {meal_type} 資料夾找到檔案")
                        break

                if not found_folder:
                    return "text", f"⚠️ 設定檔中有 '{keyword_original}' 對應到 '{target_filename}'\n但在資料夾中找不到該圖片!"

            # 4. 策略二:如果字典沒找到,進行檔案系統模糊搜尋
            if not target_filename:
                print(f"[DEBUG] 別名未找到,進行檔案搜尋...")

                for meal_type in ['breakfast', 'lunch', 'dinner', 'drink', 'snack']:
                    meal_folder = os.path.join(base_folder, meal_type)

                    if not os.path.exists(meal_folder):
                        continue

                    try:
                        files = os.listdir(meal_folder)
                        for file in files:
                            # 檢查檔名是否包含關鍵字 (忽略大小寫)
                            if keyword in file.lower() and file.lower().endswith(('.jpg', '.jpeg', '.png')):
                                target_filename = file
                                found_folder = meal_type
                                print(f"[DEBUG] 搜尋找到: {file} 在 {meal_type}")
                                break
                    except Exception as e:
                        print(f"[DEBUG] 讀取 {meal_folder} 失敗: {e}")
                        continue

                    if target_filename:
                        break

            # 5. 結果處理
            if target_filename and found_folder:
                # URL 編碼 (處理中文檔名)
                safe_filename = quote(target_filename)

                # 處理 HTTPS 和結尾斜線
                if base_url.startswith('http://'):
                    base_url = base_url.replace('http://', 'https://', 1)
                if not base_url.endswith('/'):
                    base_url += '/'

                image_url = f"{base_url}static/random_menus/{found_folder}/{safe_filename}"

                print(f"[DEBUG] !menu 最終網址: {image_url}")
                return "image", image_url

            else:
                # 列出可用的關鍵字提示
                available_aliases = list(aliases.keys())[:10]  # 只顯示前10個
                hint = ""
                if available_aliases:
                    hint = f"\n\n💡 可用關鍵字範例:\n" + "、".join(available_aliases[:5])

                return "text", f"❌ 找不到與「{keyword_original}」相關的菜單。{hint}"

        except Exception as e:
            print(f"[ERROR] handle_menu_query 發生錯誤: {e}")
            import traceback
            traceback.print_exc()
            return "text", f"❌ 處理指令時發生錯誤: {str(e)}"

    def handle_show_command(self, message_text):
        """
        處理 !show 指令
        格式：!show 2025/10/24 中餐t
        """
        try:
            # 解析指令
            parts = message_text.replace('!show', '').replace('！show', '').replace('!查詢', '').replace('！查詢', '').replace('!看單', '').replace('！看單', '').strip().split()

            if len(parts) < 1:
                return "❌ 格式錯誤！\n\n正確格式：\n!show 2025/10/24 中餐\n或\n!show 10/24 午餐"

            # 解析日期
            date_str = parts[0]
            try:
                # 嘗試完整日期格式 YYYY/MM/DD
                if date_str.count('/') == 2:
                    target_date = datetime.strptime(date_str, '%Y/%m/%d').date()
                # 嘗試簡短格式 MM/DD（使用今年）
                elif date_str.count('/') == 1:
                    current_year = date.today().year
                    target_date = datetime.strptime(f"{current_year}/{date_str}", '%Y/%m/%d').date()
                else:
                    return "❌ 日期格式錯誤！\n請使用：2025/10/24 或 10/24"
            except ValueError:
                return "❌ 日期格式錯誤！\n請使用：2025/10/24 或 10/24"

            # 解析餐別（如果有提供）
            meal_type = 'lunch'  # 預設午餐
            if len(parts) >= 2:
                meal_keyword = parts[1]
                meal_type = self.parse_meal_type(f"!order {meal_keyword}")

            # 查詢該日該餐的菜單
            menu = Menu.query.filter_by(
                menu_date=target_date,
                meal_type=meal_type
            ).first()

            if not menu:
                meal_name = Config.MEAL_TYPES.get(meal_type, '未知')
                return f"📋 {target_date.strftime('%Y/%m/%d')} {meal_name}\n\n尚無訂單記錄"

            # 查詢該菜單的所有訂單
            orders = Order.query.join(User, Order.user_id == User.id).filter(Order.menu_id == menu.id).order_by(
                db.cast(User.user_code, db.Integer)
            ).all()

            if not orders:
                meal_name = Config.MEAL_TYPES.get(meal_type, '未知')
                return f"📋 {target_date.strftime('%Y/%m/%d')} {meal_name}\n\n尚無訂單"

            # 生成回覆
            meal_name = Config.MEAL_TYPES.get(meal_type, '未知')
            reply = f"📋 {target_date.strftime('%Y/%m/%d')} {meal_name}\n\n"

            total_amount = 0
            total_paid = 0
            total_unpaid = 0

            for order in orders:
                status = "✅" if order.paid else "⏳"
                reply += f"{status} {order.user.user_code}. {order.user.name}\n"
                reply += f"   {order.items} - ${order.amount}\n"

                total_amount += order.amount
                if order.paid:
                    total_paid += order.amount
                else:
                    total_unpaid += order.amount

            reply += f"\n💰 總計：${total_amount}"
            reply += f"\n✅ 已付：${total_paid}"
            reply += f"\n⏳ 未付：${total_unpaid}"

            return reply

        except Exception as e:
            print(f"!show 指令錯誤: {e}")
            import traceback
            traceback.print_exc()
            return "❌ 處理指令時發生錯誤，請檢查格式"

    def handle_enter_command(self, message_text):
        """
        處理 !enter 指令（補登記）
        格式：!enter 2025/10/24 中餐 20 牛肉飯
        或：!enter 2025/10/24 中餐 20 牛肉飯 9（9為代墊人）
        """
        try:
            # 解析指令
            parts = message_text.replace('!enter', '').replace('！enter', '') \
                .replace('!補登', '').replace('！補登', '') \
                .replace('!輸入', '').replace('！輸入', '') \
                .strip().split(None, 4)

            if len(parts) < 4:
                return "❌ 格式錯誤！\n\n正確格式：\n!enter 2025/10/24 中餐 20 牛肉飯\n或\n!enter 10/24 午餐 20 牛肉飯 9"

            date_str = parts[0]
            meal_keyword = parts[1]
            user_code = parts[2]
            items = parts[3]

            # 🔥 檢查第五個參數（代墊人）
            payer_user = None
            if len(parts) == 5 and parts[4].isdigit():
                payer_code = parts[4]
                payer_user = User.query.filter_by(user_code=payer_code).first()
                if not payer_user:
                    return f"❌ 代墊人代號 {payer_code} 不存在"

            # 解析日期
            try:
                # 嘗試完整日期格式 YYYY/MM/DD
                if date_str.count('/') == 2:
                    target_date = datetime.strptime(date_str, '%Y/%m/%d').date()
                # 嘗試簡短格式 MM/DD（使用今年）
                elif date_str.count('/') == 1:
                    current_year = date.today().year
                    target_date = datetime.strptime(f"{current_year}/{date_str}", '%Y/%m/%d').date()
                else:
                    return "❌ 日期格式錯誤！\n請使用：2025/10/24 或 10/24"
            except ValueError:
                return "❌ 日期格式錯誤！\n請使用：2025/10/24 或 10/24"

            # 解析餐別
            meal_type = self.parse_meal_type(f"!order {meal_keyword}")
            if not meal_type:
                return "❌ 無法識別餐別！\n請使用：早餐、午餐、中餐、晚餐、飲料、點心"

            # 查找使用者
            user = User.query.filter_by(user_code=user_code).first()
            if not user:
                return f"❌ 代號 {user_code} 不存在"

            # 檢查或創建該日該餐的菜單
            menu = Menu.query.filter_by(
                menu_date=target_date,
                meal_type=meal_type
            ).first()

            if not menu:
                menu = Menu(
                    meal_type=meal_type,
                    menu_date=target_date,
                    description=f"{target_date.strftime('%Y/%m/%d')} {Config.MEAL_TYPES[meal_type]}"
                )
                db.session.add(menu)
                db.session.commit()

            # 🔥 建立訂單，包含代墊人
            order = Order(
                user_id=user.id,
                menu_id=menu.id,
                items=items,
                amount=0.0,
                payer_id=payer_user.id if payer_user else None
            )
            db.session.add(order)
            db.session.commit()

            meal_name = Config.MEAL_TYPES.get(meal_type, '未知')
            reply = f"✅ 已補登記訂單\n\n"
            reply += f"📅 日期：{target_date.strftime('%Y/%m/%d')}\n"
            reply += f"🍽️ 餐別：{meal_name}\n"
            reply += f"👤 {user_code}. {user.name}\n"
            reply += f"🍱 {items}\n"

            # 🔥 顯示代墊人
            if payer_user:
                reply += f"💳 代墊人：{payer_user.user_code}. {payer_user.name}\n"

            #reply += f"\n💡 請至網頁後台輸入金額"

            return reply

        except Exception as e:
            print(f"!enter 指令錯誤: {e}")
            import traceback
            traceback.print_exc()
            return "❌ 處理指令時發生錯誤，請檢查格式"

    def handle_checkout_command(self, message_text):
        """
        處理 !Checkout 指令 (快速結帳)
        模式 1: !Checkout [代號] -> 結清該人所有欠款
        模式 2: !Checkout [代號] [日期] -> 結清該人該日欠款
        模式 3: !Checkout [代號] [日期] [餐別] -> 結清該人該日特定餐別欠款
        """
        try:
            # 1. 清理並分割指令
            # 支援: Checkout, checkout, 結清, 收款
            parts = message_text.lower() \
                .replace('!checkout', '').replace('！checkout', '') \
                .replace('!結清', '').replace('！結清', '') \
                .replace('!收款', '').replace('！收款', '') \
                .strip().split()

            if len(parts) < 1:
                return "❌ 格式錯誤！\n請輸入代號，例如：!結清 2"

            user_code = parts[0]
            target_date = None
            target_meal = None

            # 2. 解析參數
            # 如果有第二個參數，通常是日期
            if len(parts) >= 2:
                date_str = parts[1]
                try:
                    if date_str.count('/') == 2:
                        target_date = datetime.strptime(date_str, '%Y/%m/%d').date()
                    elif date_str.count('/') == 1:
                        current_year = date.today().year
                        target_date = datetime.strptime(f"{current_year}/{date_str}", '%Y/%m/%d').date()
                    else:
                        return "❌ 日期格式錯誤 (請用 11/26 或 2025/11/26)"
                except ValueError:
                    return "❌ 日期解析失敗，請檢查格式"

            # 如果有第三個參數，就是餐別
            if len(parts) >= 3:
                meal_keyword = parts[2]
                # 借用既有的 parse_meal_type (需加前綴讓它判斷)
                target_meal = self.parse_meal_type(f"!order {meal_keyword}")

            # 3. 查找使用者
            user = User.query.filter_by(user_code=user_code).first()
            if not user:
                return f"❌ 代號 {user_code} 不存在"

            # 4. 建構查詢 (Base Query)
            # 搜尋該使用者、尚未付款 (paid=False) 的訂單
            query = Order.query.join(Menu).filter(
                Order.user_id == user.id,
                Order.paid == False
            )

            scope_msg = ""  # 用於回覆訊息，描述這次結了什麼

            # 依照條件篩選
            if target_date:
                query = query.filter(Menu.menu_date == target_date)
                date_str = target_date.strftime('%m/%d')

                if target_meal:
                    # 模式 3: 指定日期 + 餐別
                    query = query.filter(Menu.meal_type == target_meal)
                    meal_name = Config.MEAL_TYPES.get(target_meal, target_meal)
                    scope_msg = f"「{date_str} {meal_name}」"
                else:
                    # 模式 2: 指定日期 (整天)
                    scope_msg = f"「{date_str} 全天」"
            else:
                # 模式 1: 全部 (歷史欠款)
                scope_msg = "「所有歷史欠款」"

            # 5. 執行搜尋
            unpaid_orders = query.all()

            if not unpaid_orders:
                return f"✅ 代號 {user_code} 在 {scope_msg} 範圍內沒有未付款訂單。"

            # 6. 執行結帳 (Update)
            total_amount = 0
            count = 0
            for order in unpaid_orders:
                order.paid = True
                total_amount += order.amount
                count += 1

            db.session.commit()

            # 7. 回傳成功訊息
            reply = f"💰 結帳成功！\n"
            reply += f"👤 對象：{user.user_code}. {user.name}\n"
            reply += f"範圍：{scope_msg}\n"
            reply += f"🧾 筆數：{count} 筆\n"
            reply += f"💵 總金額：${total_amount}\n"
            reply += f"✅ 狀態已更新為 [已付款]"

            return reply

        except Exception as e:
            print(f"結帳指令錯誤: {e}")
            return "❌ 系統發生錯誤，結帳失敗"

    def handle_amount_command(self, message_text):
        """
        處理 !Amount 指令 (批次輸入金額)
        格式：
        !Amount [日期] [餐別]
        2. 100
        3. 120
        """
        lines = message_text.strip().split('\n')
        first_line = lines[0]

        # 1. 解析指令參數 (!Amount 日期 餐別)
        parts = first_line.lower() \
            .replace('!amount', '').replace('！amount', '') \
            .replace('!金額', '').replace('！金額', '') \
            .replace('!價錢', '').replace('！價錢', '') \
            .strip().split()

        target_date = date.today()
        meal_type = 'lunch'  # 預設午餐

        # 嘗試解析日期與餐別
        for part in parts:
            # 檢查是否為日期
            if '/' in part:
                try:
                    if part.count('/') == 2:
                        target_date = datetime.strptime(part, '%Y/%m/%d').date()
                    elif part.count('/') == 1:
                        current_year = date.today().year
                        target_date = datetime.strptime(f"{current_year}/{part}", '%Y/%m/%d').date()
                except ValueError:
                    pass
            # 檢查是否為餐別 (利用現有的 parse_meal_type 邏輯)
            else:
                parsed_meal = self.parse_meal_type(f"!order {part}")
                # parse_meal_type 預設回傳 lunch，所以我們要確認它真的有解析到關鍵字
                # 簡單判斷：如果 part 是 '午餐' 或 'lunch' 等關鍵字
                if parsed_meal != 'lunch' or '午' in part or '中' in part or 'lunch' in part:
                    meal_type = parsed_meal

        # 2. 取得該日該餐的 Menu
        menu = Menu.query.filter_by(
            menu_date=target_date,
            meal_type=meal_type
        ).first()

        meal_name = Config.MEAL_TYPES.get(meal_type, '未知')
        date_str = target_date.strftime('%Y/%m/%d')

        if not menu:
            return f"❌ 找不到菜單\n日期：{date_str}\n餐別：{meal_name}\n請先建立訂單後再輸入金額。"

        # 3. 逐行解析 (代號 金額)
        updated_count = 0
        errors = []
        result_msg = ""

        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue

            # 正則解析： "2. 100" 或 "2 100" 或 "2.100"
            # Group 1: 代號, Group 2: 金額
            match = re.match(r'^(\d+)[.\s]+(\d+(?:\.\d+)?)$', line)

            if match:
                user_code = match.group(1)
                amount = float(match.group(2))

                # 找使用者
                user = User.query.filter_by(user_code=user_code).first()
                if not user:
                    errors.append(f"代號 {user_code} 不存在")
                    continue

                # 找訂單
                order = Order.query.filter_by(menu_id=menu.id, user_id=user.id).first()
                if order:
                    order.amount = amount
                    updated_count += 1
                    result_msg += f"✅ {user_code}. {user.name}: ${int(amount)}\n"
                else:
                    errors.append(f"{user_code}. {user.name} 沒點餐")
            else:
                # 略過無法解析的行，或是視為錯誤
                if any(char.isdigit() for char in line):  # 如果這行有數字才報錯，避免讀到空行或備註
                    errors.append(f"格式錯誤：{line}")

        db.session.commit()

        # 4. 組合回覆
        reply = f"💰 金額更新完成\n"
        reply += f"📅 {date_str} {meal_name}\n"
        reply += f"----------------\n"
        reply += result_msg

        if updated_count == 0 and not errors:
            reply += "⚠️ 沒有讀取到任何金額資料"

        if errors:
            reply += f"\n⚠️ 異常：\n"
            for err in errors:
                reply += f"• {err}\n"

        return reply

    def handle_show_payer(self, message_text):
        """
        查詢代墊統計
        格式：
        !show payer        → 顯示所有代墊統計
        !show payer 3      → 顯示 3 號代墊的明細
        """
        parts = message_text.lower().replace('!show', '').replace('！show', '') \
            .replace('!查詢', '').replace('！查詢', '') \
            .replace('payer', '').replace('代墊', '').strip().split()

        # 如果有指定代號，顯示該代墊人的明細
        if parts and parts[0].isdigit():
            payer_code = parts[0]
            payer = User.query.filter_by(user_code=payer_code).first()

            if not payer:
                return f"❌ 代號 {payer_code} 不存在"

            # 查詢該代墊人的所有未收款訂單
            unpaid_orders = Order.query.filter_by(payer_id=payer.id, paid=False) \
                .join(Menu).order_by(Menu.menu_date.desc()).all()

            if not unpaid_orders:
                return f"✅ {payer.user_code}號 {payer.name} 目前沒有未收款的代墊訂單"

            # 按日期分組
            orders_by_date = {}
            total = 0
            for order in unpaid_orders:
                date_key = order.menu.menu_date
                if date_key not in orders_by_date:
                    orders_by_date[date_key] = []
                orders_by_date[date_key].append(order)
                total += order.amount

            # 組合回覆
            reply = f"💳 代墊統計 - {payer.user_code}號 {payer.name}\n"
            reply += "=" * 30 + "\n\n"
            reply += "【未收款明細】\n"

            for order_date, orders in sorted(orders_by_date.items(), reverse=True):
                date_str = order_date.strftime('%m/%d')
                reply += f"\n📅 {date_str}\n"
                for order in orders:
                    user = User.query.get(order.user_id)
                    meal_name = Config.MEAL_TYPES.get(order.menu.meal_type, '未知')
                    reply += f"  • {user.user_code}號 {user.name}: ${int(order.amount)} ({order.items})\n"

            reply += "\n" + "=" * 30 + "\n"
            reply += f"💰 總計未收：${int(total)}"

            return reply

        # 如果沒有指定代號，顯示所有代墊人的統計
        else:
            # 查詢所有未付款訂單，按代墊人統計
            unpaid_orders = Order.query.filter_by(paid=False).all()

            if not unpaid_orders:
                return "✅ 目前沒有未付款的訂單"

            # 按代墊人統計
            payer_stats = {}
            for order in unpaid_orders:
                if order.payer_id:
                    payer = User.query.get(order.payer_id)
                    if payer:
                        payer_key = f"{payer.user_code}. {payer.name}"
                        if payer_key not in payer_stats:
                            payer_stats[payer_key] = {'amount': 0, 'count': 0}
                        payer_stats[payer_key]['amount'] += order.amount
                        payer_stats[payer_key]['count'] += 1

            if not payer_stats:
                return "✅ 目前沒有代墊記錄"

            # 組合回覆
            reply = "💳 代墊統計總覽\n"
            reply += "=" * 30 + "\n\n"

            for payer_name, stats in sorted(payer_stats.items(), key=lambda x: x[1]['amount'], reverse=True):
                reply += f"👤 {payer_name}\n"
                reply += f"   未收：${int(stats['amount'])} ({stats['count']}筆)\n\n"

            reply += "💡 使用 !show payer [代號] 查看明細"

            return reply

    def handle_show_debt(self, message_text):
        """
        查詢某人的欠款明細（欠誰多少錢）
        格式：!show debt 2  → 查詢 2 號欠款明細
        """
        parts = message_text.lower().replace('!show', '').replace('！show', '') \
            .replace('!查詢', '').replace('！查詢', '') \
            .replace('debt', '').replace('欠款', '').strip().split()

        if not parts or not parts[0].isdigit():
            return "❌ 請指定代號\n\n範例：!show debt 2"

        user_code = parts[0]
        user = User.query.filter_by(user_code=user_code).first()

        if not user:
            return f"❌ 代號 {user_code} 不存在"

        # 查詢該使用者的所有未付款訂單
        unpaid_orders = Order.query.filter_by(user_id=user.id, paid=False) \
            .join(Menu).order_by(Menu.menu_date.desc()).all()

        if not unpaid_orders:
            return f"✅ {user.user_code}號 {user.name} 目前沒有欠款"

        # 按代墊人分組
        debt_by_payer = {}
        total_debt = 0

        for order in unpaid_orders:
            if order.payer_id:
                payer = User.query.get(order.payer_id)
                if payer:
                    payer_key = f"{payer.user_code}. {payer.name}"
                    if payer_key not in debt_by_payer:
                        debt_by_payer[payer_key] = {'amount': 0, 'orders': []}
                    debt_by_payer[payer_key]['amount'] += order.amount
                    debt_by_payer[payer_key]['orders'].append(order)
                    total_debt += order.amount

        if not debt_by_payer:
            return f"✅ {user.user_code}號 {user.name} 沒有代墊欠款"

        # 組合回覆
        reply = f"📋 欠款明細 - {user.user_code}號 {user.name}\n"
        reply += "=" * 30 + "\n\n"

        for payer_name, data in sorted(debt_by_payer.items(), key=lambda x: x[1]['amount'], reverse=True):
            reply += f"💳 欠 {payer_name}：${int(data['amount'])}\n"

            # 顯示明細
            for order in data['orders']:
                date_str = order.menu.menu_date.strftime('%m/%d')
                meal_name = Config.MEAL_TYPES.get(order.menu.meal_type, '未知')
                reply += f"  • {date_str} {meal_name}: ${int(order.amount)}\n"

            reply += "\n"

        reply += "=" * 30 + "\n"
        reply += f"💰 總欠款：${int(total_debt)}\n\n"
        reply += f"💡 使用 !結清 {user_code} 進行付款"

        return reply