import sys
import traceback
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

# 捕捉所有未處理的異常
def exception_handler(exctype, value, tb):
    print("\n" + "=" * 60)
    print("❌ 程式發生錯誤！")
    print("=" * 60)
    traceback.print_exception(exctype, value, tb)
    print("=" * 60)
    input("\n按 Enter 鍵退出...")


sys.excepthook = exception_handler

from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
from werkzeug.utils import secure_filename
from datetime import datetime, date
import os

from config import Config
from models import db, User, Menu, Order, LineMessage
from line_handler import OrderBot

# LINE Bot SDK v3
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent
)

# 初始化 Flask
app = Flask(__name__)
app.config.from_object(Config)

# 初始化資料庫
db.init_app(app)

# 初始化 LINE Bot (v3)
configuration = Configuration(access_token=app.config['LINE_CHANNEL_ACCESS_TOKEN'])
handler = WebhookHandler(app.config['LINE_CHANNEL_SECRET'])
order_bot = OrderBot(app.config)

# ==================== 定時任務 ====================

def send_daily_summary():
    """每日晚上8點發送未付款統計"""
    with app.app_context():
        summary = order_bot.generate_daily_unpaid_summary()
        if summary:
            # 發送到 LINE 群組
            group_id = app.config.get('LINE_GROUP_ID')
            if group_id and group_id != '請填入你的群組ID':
                success = order_bot.send_push_message(group_id, summary)
                if success:
                    print(f"✅ 每日統計已發送 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
                else:
                    print(f"❌ 每日統計發送失敗")
            else:
                print("⚠️  未設定 LINE_GROUP_ID，跳過推播")
        else:
            print(f"ℹ️  今日無未付款訂單 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")

# 初始化排程器
scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Taipei'))

# 確保只在非重新載入器程序中執行定時任務
if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    # 設定每天晚上 8 點執行
    scheduler.add_job(
        func=send_daily_summary,
        trigger=CronTrigger(hour=20, minute=0, timezone=pytz.timezone('Asia/Taipei')),
        id='daily_summary',
        name='每日未付款統計',
        replace_existing=True
    )

    # 啟動排程器
    scheduler.start()
    print("⏰ 定時任務已啟動：每日 20:00 發送未付款統計")
else:
    print("⏰ 定時任務：在重新載入器子程序中跳過啟動")

# 確保上傳資料夾存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 初始化資料庫和管理員
with app.app_context():
    db.create_all()

    # 檢查是否有管理員
    admin = User.query.filter_by(is_admin=True).first()
    if not admin:
        admin = User(
            user_code='admin',
            name='管理員',
            is_admin=True
        )
        db.session.add(admin)
        db.session.commit()
        print("\n" + "=" * 60)
        print(f"✅ 管理員已創建")
        print(f"管理員金鑰：{app.config['ADMIN_ACCESS_KEY']}")
        print("=" * 60 + "\n")


# ==================== 輔助函數 ====================

def get_current_user():
    """取得當前登入使用者"""
    if 'user_id' in session:
        return User.query.get(session['user_id'])
    return None


def login_required(admin_only=False):
    """登入驗證裝飾器"""

    def decorator(f):
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if not user:
                flash('請先登入', 'error')
                return redirect(url_for('login'))
            if admin_only and not user.is_admin:
                flash('需要管理員權限', 'error')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)

        wrapper.__name__ = f.__name__
        return wrapper

    return decorator


def allowed_file(filename):
    """檢查檔案類型"""
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


# ==================== 網頁路由 ====================

@app.route('/')
def index():
    """首頁，重導向到儀表板或登入頁"""
    if get_current_user():
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """登入頁面"""
    if request.method == 'POST':
        access_key = request.form.get('access_key', '').strip()

        # 檢查是否為管理員金鑰
        if access_key == app.config['ADMIN_ACCESS_KEY']:
            admin = User.query.filter_by(is_admin=True).first()
            session['user_id'] = admin.id
            session.permanent = True
            flash(f'歡迎，{admin.name}！', 'success')
            return redirect(url_for('dashboard'))

        flash('金鑰錯誤', 'error')

    return render_template('login.html')


@app.route('/logout')
def logout():
    """登出"""
    session.pop('user_id', None)
    flash('已登出', 'success')
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required(admin_only=True)
def dashboard():
    """管理員儀表板"""
    user = get_current_user()
    today = date.today()

    # 今日統計
    today_orders = Order.query.join(Menu).filter(
        Menu.menu_date == today
    ).all()

    today_total = sum(o.amount for o in today_orders)
    today_paid = sum(o.amount for o in today_orders if o.paid)
    today_unpaid = today_total - today_paid

    # 按餐別統計
    by_meal = {}
    for order in today_orders:
        meal_type = order.menu.meal_type
        if meal_type not in by_meal:
            by_meal[meal_type] = {
                'orders': [],
                'total': 0,
                'count': 0
            }
        by_meal[meal_type]['orders'].append(order)
        by_meal[meal_type]['total'] += order.amount
        by_meal[meal_type]['count'] += 1

    # 累計欠款
    all_unpaid = Order.query.filter_by(paid=False).all()
    total_unpaid = sum(o.amount for o in all_unpaid)

    # 使用者統計
    user_count = User.query.filter_by(is_admin=False).count()

    return render_template('admin_dashboard.html',
                           user=user,
                           today=today,
                           today_total=today_total,
                           today_paid=today_paid,
                           today_unpaid=today_unpaid,
                           by_meal=by_meal,
                           total_unpaid=total_unpaid,
                           user_count=user_count,
                           meal_types=app.config['MEAL_TYPES'])


@app.route('/users')
@login_required(admin_only=True)
def manage_users():
    """使用者管理"""
    user = get_current_user()
    users = User.query.filter_by(is_admin=False).order_by(
        db.cast(User.user_code, db.Integer)
    ).all()

    return render_template('manage_users.html', user=user, users=users)


@app.route('/users/add', methods=['POST'])
@login_required(admin_only=True)
def add_user():
    """新增使用者"""
    user_code = request.form.get('user_code', '').strip()
    name = request.form.get('name', '').strip()

    if not user_code or not name:
        flash('請填寫代號和姓名', 'error')
        return redirect(url_for('manage_users'))

    # 檢查代號是否已存在
    if User.query.filter_by(user_code=user_code).first():
        flash(f'代號 {user_code} 已存在', 'error')
        return redirect(url_for('manage_users'))

    new_user = User(user_code=user_code, name=name)
    db.session.add(new_user)
    db.session.commit()

    flash(f'✅ 已新增：代號 {user_code} - {name}', 'success')
    return redirect(url_for('manage_users'))


@app.route('/users/edit/<int:user_id>', methods=['POST'])
@login_required(admin_only=True)
def edit_user(user_id):
    """編輯使用者"""
    user_to_edit = User.query.get_or_404(user_id)

    new_code = request.form.get('user_code', '').strip()
    new_name = request.form.get('name', '').strip()

    if not new_code or not new_name:
        flash('請填寫代號和姓名', 'error')
        return redirect(url_for('manage_users'))

    # 檢查新代號是否與其他使用者衝突
    existing = User.query.filter_by(user_code=new_code).first()
    if existing and existing.id != user_id:
        flash(f'代號 {new_code} 已被使用', 'error')
        return redirect(url_for('manage_users'))

    user_to_edit.user_code = new_code
    user_to_edit.name = new_name
    db.session.commit()

    flash(f'✅ 已更新：代號 {new_code} - {new_name}', 'success')
    return redirect(url_for('manage_users'))


@app.route('/users/delete/<int:user_id>', methods=['POST'])
@login_required(admin_only=True)
def delete_user(user_id):
    """刪除使用者"""
    user_to_delete = User.query.get_or_404(user_id)

    if user_to_delete.is_admin:
        flash('無法刪除管理員', 'error')
        return redirect(url_for('manage_users'))

    name = user_to_delete.name
    db.session.delete(user_to_delete)
    db.session.commit()

    flash(f'✅ 已刪除：{name}', 'success')
    return redirect(url_for('manage_users'))


@app.route('/accounting')
@login_required(admin_only=True)
def daily_accounting():
    """每日記帳"""
    user = get_current_user()

    # 取得日期參數
    date_str = request.args.get('date', date.today().strftime('%Y-%m-%d'))
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except:
        target_date = date.today()

    # 取得該日期的所有訂單
    orders = Order.query.join(Menu).join(
        User, Order.user_id == User.id
    ).filter(
        Menu.menu_date == target_date
    ).order_by(
        db.cast(User.user_code, db.Integer),
        Menu.meal_type
    ).all()

    # 按使用者分組
    by_user = {}
    for order in orders:
        user_id = order.user_id
        if user_id not in by_user:
            by_user[user_id] = {
                'user': order.user,
                'orders': [],
                'total': 0,
                'paid': 0,
                'unpaid': 0
            }
        by_user[user_id]['orders'].append(order)
        by_user[user_id]['total'] += order.amount
        if order.paid:
            by_user[user_id]['paid'] += order.amount
        else:
            by_user[user_id]['unpaid'] += order.amount

    # 總計
    total_amount = sum(o.amount for o in orders)
    total_paid = sum(o.amount for o in orders if o.paid)
    total_unpaid = total_amount - total_paid

    return render_template('daily_accounting.html',
                           user=user,
                           target_date=target_date,
                           by_user=by_user,
                           total_amount=total_amount,
                           total_paid=total_paid,
                           total_unpaid=total_unpaid,
                           meal_types=app.config['MEAL_TYPES'])


@app.route('/accounting/update/<int:order_id>', methods=['POST'])
@login_required(admin_only=True)
def update_amount(order_id):
    """更新訂單金額"""
    order = Order.query.get_or_404(order_id)

    try:
        amount = float(request.form.get('amount', 0))
        order.amount = amount
        db.session.commit()
        flash('金額已更新', 'success')
    except ValueError:
        flash('請輸入有效的金額', 'error')

    return redirect(request.referrer or url_for('daily_accounting'))


@app.route('/accounting/toggle_paid/<int:order_id>', methods=['POST'])
@login_required(admin_only=True)
def toggle_paid(order_id):
    """切換付款狀態"""
    order = Order.query.get_or_404(order_id)
    order.paid = not order.paid
    db.session.commit()

    status = '已付款' if order.paid else '未付款'
    flash(f'{order.user.name} 的訂單已標記為：{status}', 'success')

    return redirect(request.referrer or url_for('daily_accounting'))


@app.route('/accounting/delete/<int:order_id>', methods=['POST'])
@login_required(admin_only=True)
def delete_order(order_id):
    """刪除訂單"""
    order = Order.query.get_or_404(order_id)
    user_name = order.user.name

    db.session.delete(order)
    db.session.commit()

    flash(f'已刪除 {user_name} 的訂單', 'success')
    return redirect(request.referrer or url_for('daily_accounting'))


@app.route('/history')
@login_required(admin_only=True)
def history():
    """歷史記錄"""
    user = get_current_user()

    # 分頁
    page = request.args.get('page', 1, type=int)
    per_page = 50

    # 取得所有訂單，按日期降序
    pagination = Order.query.join(Menu).order_by(
        Menu.menu_date.desc(),
        Order.created_date.desc()
    ).paginate(page=page, per_page=per_page, error_out=False)

    orders = pagination.items

    return render_template('history.html',
                           user=user,
                           orders=orders,
                           pagination=pagination,
                           meal_types=app.config['MEAL_TYPES'])


@app.route('/upload_menu', methods=['POST'])
@login_required(admin_only=True)
def upload_menu():
    """上傳菜單圖片（選用功能）"""
    if 'menu_file' not in request.files:
        flash('請選擇檔案', 'error')
        return redirect(url_for('dashboard'))

    file = request.files['menu_file']
    meal_type = request.form.get('meal_type', 'lunch')
    menu_date_str = request.form.get('menu_date', date.today().strftime('%Y-%m-%d'))

    if file.filename == '':
        flash('未選擇檔案', 'error')
        return redirect(url_for('dashboard'))

    try:
        menu_date = datetime.strptime(menu_date_str, '%Y-%m-%d').date()
    except:
        menu_date = date.today()

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
        filename = timestamp + filename
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # 檢查是否已有該日該餐別的菜單
        menu = Menu.query.filter_by(menu_date=menu_date, meal_type=meal_type).first()

        if menu:
            # 更新現有菜單
            menu.filename = filename
        else:
            # 創建新菜單
            menu = Menu(
                meal_type=meal_type,
                menu_date=menu_date,
                filename=filename,
                description=f"{menu_date.strftime('%Y/%m/%d')} {app.config['MEAL_TYPES'][meal_type]}"
            )
            db.session.add(menu)

        db.session.commit()
        flash(f'✅ {app.config["MEAL_TYPES"][meal_type]}菜單已上傳', 'success')
    else:
        flash('只接受 jpg, jpeg, png 格式', 'error')

    return redirect(url_for('dashboard'))


# ==================== LINE Bot 路由 ====================

@app.route("/callback", methods=['POST'])
def callback():
    """LINE Bot Webhook"""
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.error("Invalid signature")
        abort(400)

    return 'OK'


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    """處理文字訊息"""
    message_text = event.message.text.strip()
    user_id = event.source.user_id
    group_id = getattr(event.source, 'group_id', None)

    # 測試：顯示群組 ID
    if message_text == '!groupid' and group_id:
        order_bot.send_reply(event.reply_token, f"群組 ID：{group_id}")
        return

    # 記錄訊息到資料庫
    line_msg = LineMessage(
        message_type='text',
        message_content=message_text,
        user_id=user_id,
        group_id=group_id
    )
    db.session.add(line_msg)
    db.session.commit()

    reply_text = None

    # 處理指令
    if message_text.lower().startswith('!order') or message_text.lower().startswith('！order') or \
         message_text.startswith('!點餐') or message_text.startswith('！點餐'): # 🔥 新增中文指令:
        reply_text = order_bot.handle_order_command(message_text, group_id)
        line_msg.processed = True
        db.session.commit()

    elif message_text.lower().startswith('!add') or message_text.lower().startswith('！add')or \
         message_text.startswith('!加點') or message_text.startswith('！加點'): # 🔥 新增中文指令:
        reply_text = order_bot.handle_add_command(message_text)
        line_msg.processed = True
        db.session.commit()

    elif message_text.lower().startswith('!bill') or message_text.lower().startswith('！bill')or \
         message_text.startswith('!結帳') or message_text.startswith('！結帳') or \
         message_text.startswith('!帳單') or message_text.startswith('！帳單'): # 🔥 新增中文指令:
        reply_text = order_bot.handle_bill_query(message_text)
        line_msg.processed = True
        db.session.commit()

    elif message_text.lower().startswith('!today') or message_text.lower().startswith('！today')or \
         message_text.startswith('!今日') or message_text.startswith('！今日') or \
         message_text.startswith('!今天') or message_text.startswith('！今天'): # 🔥 新增中文指令:
        reply_text = order_bot.handle_today_summary()
        line_msg.processed = True
        db.session.commit()

    elif message_text.lower().startswith('!help') or message_text.lower().startswith('！help') or \
            message_text == '說明' or message_text == '指令' or \
            message_text == '!說明' or message_text == '！說明' or \
            message_text == '!指令' or message_text == '！指令': # 🔥 新增有 ! 的中文指令
        reply_text = order_bot.handle_help()
        line_msg.processed = True
        db.session.commit()

    # 🔥 查詢代墊統計（必須放在 !show 之前，否則會被 !show 攔截）
    elif message_text.lower().startswith(('!show payer', '！show payer')) or \
            message_text.startswith(('!代墊', '！代墊')):
        reply_text = order_bot.handle_show_payer(message_text)
        line_msg.processed = True
        db.session.commit()

    # 🔥 查詢欠款明細（必須放在 !show 之前，否則會被 !show 攔截）
    elif message_text.lower().startswith(('!show debt', '！show debt')) or \
            message_text.startswith(('!欠款', '！欠款')):
        reply_text = order_bot.handle_show_debt(message_text)
        line_msg.processed = True
        db.session.commit()

    elif message_text.lower().startswith('!show') or message_text.lower().startswith('！show') or \
         message_text.startswith('!查詢') or message_text.startswith('！查詢') or \
         message_text.startswith('!看單') or message_text.startswith('！看單'):
        reply_text = order_bot.handle_show_command(message_text)
        line_msg.processed = True
        db.session.commit()

    # 🔥 !enter 指令
    elif message_text.lower().startswith('!enter') or message_text.lower().startswith('!enter')or \
         message_text.startswith('!補登') or message_text.startswith('！補登') or \
         message_text.startswith('!輸入') or message_text.startswith('！輸入'): # 🔥 新增中文指令
        reply_text = order_bot.handle_enter_command(message_text)
        line_msg.processed = True
        db.session.commit()

    elif message_text.lower().startswith(('!checkout', '！checkout', '!結清', '！結清', '!收款', '！收款')):
        reply_text = order_bot.handle_checkout_command(message_text)
        line_msg.processed = True
        db.session.commit()

    elif message_text.lower().startswith('!amount') or message_text.lower().startswith('！amount') or \
            message_text.startswith('!金額') or message_text.startswith('！金額') or \
            message_text.startswith('!價錢') or message_text.startswith('！價錢'):
        reply_text = order_bot.handle_amount_command(message_text)
        line_msg.processed = True
        db.session.commit()

    elif message_text.lower().startswith('!menu') or message_text.lower().startswith('！menu')or \
         message_text.startswith('!菜單') or message_text.startswith('！菜單')or \
         message_text.startswith('!蔡單') or message_text.startswith('！蔡單') or \
         message_text.startswith('!看菜單') or message_text.startswith('！看菜單'): # 🔥 新增中文指令:
        # 強制轉 HTTPS
        base_url = request.url_root.replace('http://', 'https://')

        msg_type, content = order_bot.handle_menu_query(message_text, base_url)

        if msg_type == 'image':
            order_bot.send_image_reply(event.reply_token, content, content)
        else:
            order_bot.send_reply(event.reply_token, content)

        line_msg.processed = True
        db.session.commit()
        return

    elif message_text.lower().startswith('!eat what') or \
         message_text.startswith('!吃什麼') or message_text.startswith('！吃什麼'): # 🔥 新增中文指令:
        # 取得目前的伺服器網址 (例如 https://xxx.ngrok.io/)
        base_url = request.url_root.replace('http://', 'https://')

        # 呼叫處理函式，回傳 (類型, 內容)
        msg_type, content = order_bot.handle_eat_what(message_text, base_url)

        if msg_type == 'image':
            # 如果是圖片，傳送圖片
            # content 在這裡是 image_url
            order_bot.send_image_reply(event.reply_token, content, content)
        else:
            # 如果是文字(錯誤訊息)，傳送文字
            order_bot.send_reply(event.reply_token, content)

        line_msg.processed = True
        db.session.commit()
        return # 結束函式，避免跑到下面去

    elif message_text.isdigit():
        # 直接輸入數字視為查詢帳單
        reply_text = order_bot.handle_bill_query(message_text)
        line_msg.processed = True
        db.session.commit()

    elif message_text == '!test_daily' or message_text == '!測試統計':
        # 1. 生成統計資料
        summary = order_bot.generate_daily_unpaid_summary()

        # 2. 加上測試標記
        if summary:
            reply_content = "【這是測試預覽，不會發送到群組】\n\n" + summary
        else:
            reply_content = "【測試模式】目前沒有未付款訂單，所以不會發送通知。"

        # 3. 直接回覆給測試者 (使用 send_reply 而不是 send_push_message)
        order_bot.send_reply(event.reply_token, reply_content)

        line_msg.processed = True
        db.session.commit()
        return

    # 回覆訊息
    if reply_text:
        order_bot.send_reply(event.reply_token, reply_text)


# ==================== 啟動 ====================

if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("🍱 辦公室點餐系統 v2.0")
    print("=" * 60)

    try:
        # 顯示管理員資訊
        with app.app_context():
            admin = User.query.filter_by(is_admin=True).first()
            if admin:
                print(f"✅ 系統初始化完成")
                print(f"📝 管理員金鑰: {app.config['ADMIN_ACCESS_KEY']}")
                print(f"🌐 本機訪問: http://127.0.0.1:5000")
                print(f"🌐 區域網路: http://192.168.1.107:5000")
                print(f"⏰ 定時提醒: 每日 20:00")
                print("=" * 60)

        print("\n⚡ 伺服器啟動中...\n")

        # 啟動 Flask 開發伺服器
        app.run(
            host='0.0.0.0',
            port=5000,
            debug=True,
            use_reloader=True
        )

    except KeyboardInterrupt:
        print("\n\n👋 正在關閉伺服器...")
        scheduler.shutdown()  # 關閉排程器
        print("\n\n👋 伺服器已停止")

    except Exception as e:
        print(f"\n❌ 啟動失敗!")
        print(f"錯誤訊息: {e}\n")
        import traceback

        traceback.print_exc()

    finally:
        print("\n" + "=" * 60)
        input("按 Enter 鍵關閉視窗...")