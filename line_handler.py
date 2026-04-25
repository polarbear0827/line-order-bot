from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, PushMessageRequest,
    TextMessage as LineTextMessage,
    FlexMessage, FlexContainer,
    ImageMessage,
)
from models import db, User, Shop, MenuItem, DailyMenu, Order, SystemSetting
from config import Config
from datetime import datetime, date
import pytz
import re
from rapidfuzz import process, fuzz


class OrderBot:
    def __init__(self, config):
        self.config = config
        self.configuration = Configuration(access_token=config['LINE_CHANNEL_ACCESS_TOKEN'])

    # ─── 發送工具 ──────────────────────────────────────────────────
    def send_reply(self, reply_token, text):
        with ApiClient(self.configuration) as api:
            MessagingApi(api).reply_message(
                ReplyMessageRequest(reply_token=reply_token,
                                    messages=[LineTextMessage(text=text)])
            )

    def send_push_message(self, to, text):
        try:
            with ApiClient(self.configuration) as api:
                MessagingApi(api).push_message(
                    PushMessageRequest(to=to, messages=[LineTextMessage(text=text)])
                )
            return True
        except Exception as e:
            print(f'推播失敗: {e}')
            return False

    def send_flex_reply(self, reply_token, alt_text, flex_dict):
        import json
        with ApiClient(self.configuration) as api:
            MessagingApi(api).reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[FlexMessage(
                        alt_text=alt_text,
                        contents=FlexContainer.from_dict(flex_dict)
                    )]
                )
            )

    # ─── 時間判斷 ──────────────────────────────────────────────────
    def get_current_meal_type(self):
        tw = pytz.timezone('Asia/Taipei')
        now = datetime.now(tw)
        h = now.hour + now.minute / 60
        if 5 <= h < 10.5:
            return 'breakfast'
        elif 10.5 <= h < 14.5:
            return 'lunch'
        elif 14.5 <= h < 17.5:
            return 'snack'
        elif 17.5 <= h < 21:
            return 'dinner'
        return 'lunch'

    # ─── 品項模糊比對 ──────────────────────────────────────────────
    def match_menu_item(self, item_name, shop_id=None):
        """
        回傳 (MenuItem or None, confidence: 'exact'|'fuzzy'|'none')
        """
        query = MenuItem.query.filter_by(is_available=True)
        if shop_id:
            query = query.filter_by(shop_id=shop_id)
        items = query.all()
        if not items:
            return None, 'none'

        names = [i.name for i in items]
        # 精確比對
        for i in items:
            if i.name == item_name:
                return i, 'exact'
        # 模糊比對（threshold 75）
        result = process.extractOne(item_name, names, scorer=fuzz.ratio)
        if result and result[1] >= 75:
            matched = next(i for i in items if i.name == result[0])
            return matched, 'fuzzy'
        return None, 'none'

    # ─── !點 指令 ─────────────────────────────────────────────────
    def handle_order_command(self, message_text, reply_token, group_id=None):
        """
        格式：
        !點 [代墊人代號]
        2. 肉羹飯
        5. 沙茶牛肉炒麵
        """
        lines = message_text.strip().split('\n')
        first = lines[0].replace('!點', '').replace('！點', '').strip()

        # 解析代墊人
        payer_code = first.strip() if first.strip().isdigit() else \
            SystemSetting.get('default_payer_code', '')
        payer = User.query.filter_by(user_code=payer_code).first() if payer_code else None

        if payer_code and not payer:
            return f'❌ 代墊人代號 {payer_code} 不存在'

        # 取得餐別 & 今日 DailyMenu
        meal_type = self.get_current_meal_type()
        today = date.today()
        dm = DailyMenu.query.filter_by(menu_date=today, meal_type=meal_type).first()
        if not dm:
            dm = DailyMenu(menu_date=today, meal_type=meal_type)
            db.session.add(dm)
            db.session.commit()

        shop = dm.shop  # 可能是 None（未透過 Flex 流程選店）

        # 解析訂單行
        orders_info = []
        errors = []
        fuzzy_warnings = []

        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^(\d+)[.\s]+(.+)$', line)
            if not m:
                errors.append(f'無法解析：{line}')
                continue

            user_code, item_name = m.group(1), m.group(2).strip()
            user = User.query.filter_by(user_code=user_code).first()
            if not user:
                errors.append(f'代號 {user_code} 不存在')
                continue

            # 比對品項
            menu_item, confidence = self.match_menu_item(item_name, shop_id=shop.id if shop else None)
            amount = menu_item.price or 0.0 if menu_item else 0.0

            if confidence == 'fuzzy':
                fuzzy_warnings.append(f'⚠️ {user_code}. {user.name}：「{item_name}」→ 比對為「{menu_item.name}」，請確認')

            order = Order(
                user_id=user.id,
                daily_menu_id=dm.id,
                menu_item_id=menu_item.id if menu_item else None,
                items=menu_item.name if menu_item else item_name,
                amount=amount,
                payer_id=payer.id if payer else None,
            )
            db.session.add(order)
            orders_info.append({
                'code': user_code, 'name': user.name,
                'item': menu_item.name if menu_item else item_name,
                'amount': amount,
                'warning': confidence == 'fuzzy',
            })

        db.session.commit()

        # 回覆訊息
        meal_name = Config.MEAL_TYPES.get(meal_type, '未知')
        shop_name = shop.name if shop else '（未指定店家）'
        payer_info = f'{payer.user_code}. {payer.name}' if payer else '未指定'

        reply = f'✅ 已記錄 {len(orders_info)} 筆訂單\n'
        reply += f'【{meal_name} - {today.strftime("%m/%d")}】{shop_name}\n'
        reply += f'💳 代墊：{payer_info}\n'
        reply += '─' * 20 + '\n'

        total = 0
        for o in orders_info:
            flag = '⚠️' if o['warning'] else ''
            price_str = f'${int(o["amount"])}' if o['amount'] else '（待定）'
            reply += f'{flag}{o["code"]}. {o["name"]}  {o["item"]}  {price_str}\n'
            total += o['amount']

        reply += '─' * 20 + '\n'
        reply += f'共 ${int(total)}\n'

        if fuzzy_warnings:
            reply += '\n' + '\n'.join(fuzzy_warnings)
        if errors:
            reply += '\n⚠️ 錯誤：\n' + '\n'.join(f'• {e}' for e in errors)
        if any(o['amount'] == 0 for o in orders_info):
            reply += '\n💡 部分品項金額未知，請至後台補登'

        return reply

    # ─── !bill ────────────────────────────────────────────────────
    def handle_bill_query(self, message_text):
        code = re.sub(r'[！!]bill|[！!]帳單|[！!]結帳', '', message_text, flags=re.IGNORECASE).strip()
        if not code.isdigit():
            return '❌ 格式錯誤，例如：!bill 2 或直接輸入 2'

        user = User.query.filter_by(user_code=code).first()
        if not user:
            return f'❌ 代號 {code} 不存在'

        today = date.today()
        today_orders = Order.query.join(DailyMenu).filter(
            Order.user_id == user.id, DailyMenu.menu_date == today
        ).all()

        unpaid_all = Order.query.filter_by(user_id=user.id, paid=False).all()

        reply = f'📋 {code}號 {user.name} 的帳單\n'
        reply += '=' * 28 + '\n'

        if today_orders:
            reply += f'【今日 {today.strftime("%m/%d")}】\n'
            for o in today_orders:
                s = '✅' if o.paid else '⏳'
                meal = Config.MEAL_TYPES.get(o.daily_menu.meal_type, '?')
                reply += f'{s} {meal}：{o.items} ${int(o.amount)}\n'
            reply += '\n'

        if unpaid_all:
            total = sum(o.amount for o in unpaid_all)
            reply += f'【累計欠款】${int(total)}\n'
            reply += f'💡 輸入 !結清 {code} 結清所有欠款'
        else:
            reply += '✅ 目前沒有欠款'

        return reply

    # ─── !today ───────────────────────────────────────────────────
    def handle_today_summary(self):
        today = date.today()
        orders = Order.query.join(DailyMenu).filter(DailyMenu.menu_date == today).all()
        if not orders:
            return f'📋 今日 ({today.strftime("%m/%d")}) 還沒有訂單'

        by_meal = {}
        for o in orders:
            mt = o.daily_menu.meal_type
            by_meal.setdefault(mt, []).append(o)

        reply = f'📋 今日訂單 ({today.strftime("%m/%d")})\n\n'
        total = paid = 0
        for mt, os in by_meal.items():
            reply += f'【{Config.MEAL_TYPES.get(mt, mt)}】{len(os)} 筆\n'
            for o in os:
                s = '✅' if o.paid else '⏳'
                reply += f'{s} {o.user.user_code}. {o.user.name} - {o.items} (${int(o.amount)})\n'
                total += o.amount
                if o.paid:
                    paid += o.amount
            reply += '\n'

        reply += f'💰 總計 ${int(total)}｜已收 ${int(paid)}｜未收 ${int(total - paid)}'
        return reply

    # ─── !結清 ────────────────────────────────────────────────────
    def handle_checkout(self, message_text):
        code = re.sub(r'[！!](結清|checkout)', '', message_text, flags=re.IGNORECASE).strip()
        parts = code.split()
        if not parts or not parts[0].isdigit():
            return '❌ 格式：!結清 [代號]\n例：!結清 2'

        user_code = parts[0]
        user = User.query.filter_by(user_code=user_code).first()
        if not user:
            return f'❌ 代號 {user_code} 不存在'

        unpaid = Order.query.filter_by(user_id=user.id, paid=False).all()
        if not unpaid:
            return f'✅ {user_code}號 {user.name} 目前沒有未付款訂單'

        total = sum(o.amount for o in unpaid)
        for o in unpaid:
            o.paid = True
        db.session.commit()

        return (f'💰 結帳成功！\n'
                f'👤 {user.user_code}. {user.name}\n'
                f'🧾 {len(unpaid)} 筆，共 ${int(total)}\n'
                f'✅ 已全部標記為已付款')

    # ─── !help ────────────────────────────────────────────────────
    def handle_help(self):
        return """🍱 點餐機器人 V2

══════════════════
📝 點餐
══════════════════
!點 [代墊人代號]
[代號]. [品項名稱]
（每行一人）

例：
!點 18
2. 肉羹飯
5. 沙茶牛肉炒麵

══════════════════
🔍 查詢
══════════════════
!bill [代號] 或直接輸入代號
→ 查個人帳單

!today / !今日
→ 今日所有訂單

══════════════════
💰 結帳
══════════════════
!結清 [代號]
→ 結清該人所有欠款

══════════════════
每晚 20:30 自動推播未付款提醒"""

    # ─── 每日統計 ─────────────────────────────────────────────────
    def generate_daily_unpaid_summary(self):
        users_with_unpaid = (db.session.query(User)
                             .join(Order, User.id == Order.user_id)
                             .filter(Order.paid == False, User.is_admin == False)
                             .distinct()
                             .order_by(db.cast(User.user_code, db.Integer))
                             .all())
        if not users_with_unpaid:
            return None

        today = date.today()
        reply = f'📊 帳務提醒 ({today.strftime("%Y/%m/%d")} 20:30)\n\n'
        for user in users_with_unpaid:
            unpaid = Order.query.filter_by(user_id=user.id, paid=False).all()
            total = sum(o.amount for o in unpaid)
            if total > 0:
                reply += f'{user.user_code}. {user.name}  未付 ${int(total)}\n'
        return reply
