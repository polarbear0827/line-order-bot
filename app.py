import os
import sys
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, jsonify
from werkzeug.utils import secure_filename
from datetime import datetime, date
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from config import Config
from models import db, User, Shop, MenuItem, DailyMenu, Order, LineMessage, SystemSetting

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent

# ── 初始化 ──────────────────────────────────────────────
app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

configuration = Configuration(access_token=app.config['LINE_CHANNEL_ACCESS_TOKEN'])
handler = WebhookHandler(app.config['LINE_CHANNEL_SECRET'])

from line_handler import OrderBot
order_bot = OrderBot(app.config)

# ── 排程 ────────────────────────────────────────────────
def send_daily_summary():
    with app.app_context():
        summary = order_bot.generate_daily_unpaid_summary()
        group_id = app.config.get('LINE_GROUP_ID')
        if summary and group_id:
            order_bot.send_push_message(group_id, summary)

scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Taipei'))
if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    h = app.config['DAILY_PUSH_HOUR']
    m = app.config['DAILY_PUSH_MINUTE']
    scheduler.add_job(send_daily_summary, CronTrigger(hour=h, minute=m, timezone=pytz.timezone('Asia/Taipei')),
                      id='daily_summary', replace_existing=True)
    scheduler.start()

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

with app.app_context():
    db.create_all()
    if not User.query.filter_by(is_admin=True).first():
        db.session.add(User(user_code='admin', name='管理員', is_admin=True))
        db.session.commit()

# ── 輔助 ────────────────────────────────────────────────
def get_current_user():
    if 'user_id' in session:
        return db.session.get(User, session['user_id'])
    return None

def login_required(admin_only=False):
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
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# ── 認證 ────────────────────────────────────────────────
@app.route('/')
def index():
    return redirect(url_for('dashboard') if get_current_user() else url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        key = request.form.get('access_key', '').strip()
        if key == app.config['ADMIN_ACCESS_KEY']:
            admin = User.query.filter_by(is_admin=True).first()
            session['user_id'] = admin.id
            session.permanent = True
            return redirect(url_for('dashboard'))
        flash('金鑰錯誤', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/health')
def health():
    return 'OK', 200

# ── 儀表板 ──────────────────────────────────────────────
@app.route('/dashboard')
@login_required(admin_only=True)
def dashboard():
    user = get_current_user()
    today = date.today()
    today_orders = Order.query.join(DailyMenu).filter(DailyMenu.menu_date == today).all()
    total = sum(o.amount for o in today_orders)
    paid = sum(o.amount for o in today_orders if o.paid)
    unpaid_all = sum(o.amount for o in Order.query.filter_by(paid=False).all())
    return render_template('dashboard.html', user=user, today=today,
                           today_orders=today_orders, total=total,
                           paid=paid, unpaid=total - paid, unpaid_all=unpaid_all,
                           meal_types=app.config['MEAL_TYPES'])

# ── 使用者管理 ───────────────────────────────────────────
@app.route('/users')
@login_required(admin_only=True)
def manage_users():
    users = User.query.filter_by(is_admin=False).order_by(
        db.cast(User.user_code, db.Integer)
    ).all()
    return render_template('manage_users.html', user=get_current_user(), users=users)

@app.route('/users/add', methods=['POST'])
@login_required(admin_only=True)
def add_user():
    code = request.form.get('user_code', '').strip()
    name = request.form.get('name', '').strip()
    if not code or not name:
        flash('請填寫代號和姓名', 'error')
    elif User.query.filter_by(user_code=code).first():
        flash(f'代號 {code} 已存在', 'error')
    else:
        db.session.add(User(user_code=code, name=name))
        db.session.commit()
        flash(f'✅ 已新增：{code}. {name}', 'success')
    return redirect(url_for('manage_users'))

@app.route('/users/edit/<int:uid>', methods=['POST'])
@login_required(admin_only=True)
def edit_user(uid):
    u = db.get_or_404(User, uid)
    code = request.form.get('user_code', '').strip()
    name = request.form.get('name', '').strip()
    if not code or not name:
        flash('請填寫代號和姓名', 'error')
    else:
        exist = User.query.filter_by(user_code=code).first()
        if exist and exist.id != uid:
            flash(f'代號 {code} 已被使用', 'error')
        else:
            u.user_code, u.name = code, name
            db.session.commit()
            flash(f'✅ 已更新：{code}. {name}', 'success')
    return redirect(url_for('manage_users'))

@app.route('/users/delete/<int:uid>', methods=['POST'])
@login_required(admin_only=True)
def delete_user(uid):
    u = db.get_or_404(User, uid)
    if u.is_admin:
        flash('無法刪除管理員', 'error')
    else:
        name = u.name
        db.session.delete(u)
        db.session.commit()
        flash(f'✅ 已刪除：{name}', 'success')
    return redirect(url_for('manage_users'))

@app.route('/users/import', methods=['POST'])
@login_required(admin_only=True)
def import_users():
    import openpyxl, io
    f = request.files.get('excel_file')
    if not f:
        flash('請選擇 Excel 檔案', 'error')
        return redirect(url_for('manage_users'))
    wb = openpyxl.load_workbook(io.BytesIO(f.read()))
    ws = wb.active
    added, skipped = 0, 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[0] or not row[1]:
            continue
        code, name = str(row[0]).strip(), str(row[1]).strip()
        if User.query.filter_by(user_code=code).first():
            skipped += 1
        else:
            db.session.add(User(user_code=code, name=name))
            added += 1
    db.session.commit()
    flash(f'✅ 匯入完成：新增 {added} 人，跳過 {skipped} 人（代號重複）', 'success')
    return redirect(url_for('manage_users'))

# ── 店家管理 ────────────────────────────────────────────
@app.route('/shops')
@login_required(admin_only=True)
def manage_shops():
    shops = Shop.query.order_by(Shop.name).all()
    return render_template('manage_shops.html', user=get_current_user(), shops=shops,
                           categories=app.config['SHOP_CATEGORIES'])

@app.route('/guide')
@login_required(admin_only=True)
def guide():
    return render_template('guide.html', user=get_current_user())

@app.route('/shops/add', methods=['POST'])
@login_required(admin_only=True)
def add_shop():
    import json
    name = request.form.get('name', '').strip()
    category = request.form.get('category', '')
    meal_types = request.form.getlist('meal_types')
    if not name:
        flash('請填寫店家名稱', 'error')
    else:
        s = Shop(name=name, category=category)
        s.meal_types = meal_types or ['lunch']
        db.session.add(s)
        db.session.commit()
        flash(f'✅ 已新增：{name}', 'success')
    return redirect(url_for('manage_shops'))

@app.route('/shops/toggle/<int:sid>', methods=['POST'])
@login_required(admin_only=True)
def toggle_shop(sid):
    s = db.get_or_404(Shop, sid)
    s.is_active = not s.is_active
    db.session.commit()
    flash(f'{"✅ 已啟用" if s.is_active else "⏸️ 已停用"}：{s.name}', 'success')
    return redirect(url_for('manage_shops'))

@app.route('/shops/delete/<int:sid>', methods=['POST'])
@login_required(admin_only=True)
def delete_shop(sid):
    s = db.get_or_404(Shop, sid)
    name = s.name
    db.session.delete(s)
    db.session.commit()
    flash(f'✅ 已刪除：{name}', 'success')
    return redirect(url_for('manage_shops'))

# ── 菜單管理（含 OCR）────────────────────────────────────
@app.route('/shops/<int:sid>/menu')
@login_required(admin_only=True)
def manage_menu(sid):
    s = db.get_or_404(Shop, sid)
    return render_template('manage_menu.html', user=get_current_user(), shop=s,
                           items=s.items)

@app.route('/shops/<int:sid>/menu/ocr', methods=['POST'])
@login_required(admin_only=True)
def ocr_menu(sid):
    s = db.get_or_404(Shop, sid)
    f = request.files.get('menu_image')
    if not f or not allowed_file(f.filename):
        flash('請上傳 JPG/PNG 圖片', 'error')
        return redirect(url_for('manage_menu', sid=sid))

    # 儲存圖片
    filename = secure_filename(f.filename)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S_')
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], ts + filename)
    f.save(filepath)
    s.menu_image = ts + filename
    s.last_updated = datetime.utcnow()
    db.session.commit()

    # 呼叫 OpenRouter AI (Baidu Qianfan OCR Fast)
    try:
        from openai import OpenAI
        import base64
        import json, re

        with open(filepath, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')

        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=app.config['OPENROUTER_API_KEY'],
        )
        
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "請從這張菜單圖片中，精準提取所有品項名稱與對應價格，保留原本繁體中文字。以 JSON 陣列回傳，格式如下：\n[{\"name\": \"大腸臭臭鍋\", \"price\": 160}, {\"name\": \"海鮮香香鍋\", \"price\": 160}]\n如果價格看不清楚，price 填 null。只回傳純 JSON 陣列，不要任何 markdown 語法或其他說明文字。"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                            },
                        },
                    ],
                }
            ],
            model="baidu/qianfan-ocr-fast:free",
            temperature=0,
        )
        
        raw = chat_completion.choices[0].message.content.strip()
        
        parsed = []
        try:
            # 嘗試找尋標準 JSON 陣列 [ ... ]
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
            else:
                # 嘗試當作單一 JSON 解析
                clean_raw = re.sub(r'^```[a-z]*\n?', '', raw)
                clean_raw = re.sub(r'\n?```$', '', clean_raw).strip()
                parsed = json.loads(clean_raw)
        except Exception:
            # 如果失敗（例如遇到 Extra data），可能是模型回傳了 JSONL (多行 JSON 物件)
            # 使用正規表達式硬抓所有的 { ... }
            objects = re.findall(r'\{[^{}]*\}', raw)
            for obj_str in objects:
                try:
                    obj = json.loads(obj_str)
                    if 'name' in obj:
                        parsed.append(obj)
                except:
                    continue
            
            if not parsed:
                raise ValueError(f"無法解析回傳格式，回傳內容前 100 字元: {raw[:100]}")
            
        session['ocr_result'] = parsed
        session['ocr_shop_id'] = sid
        flash(f'✅ OCR 辨識完成，共 {len(parsed)} 個品項，請確認後儲存', 'success')
    except Exception as e:
        flash(f'❌ OCR 辨識失敗：{e}', 'error')

    return redirect(url_for('manage_menu', sid=sid))

@app.route('/shops/<int:sid>/menu/delete_all', methods=['POST'])
@login_required(admin_only=True)
def delete_all_menu_items(sid):
    shop = db.get_or_404(Shop, sid)
    MenuItem.query.filter_by(shop_id=shop.id).delete()
    db.session.commit()
    flash('✅ 已清空所有品項', 'success')
    return redirect(url_for('manage_menu', sid=sid))

@app.route('/shops/<int:sid>/menu/save_ocr', methods=['POST'])
@login_required(admin_only=True)
def save_ocr_menu(sid):
    names = request.form.getlist('name')
    prices = request.form.getlist('price')
    for name, price_str in zip(names, prices):
        name = name.strip()
        if not name:
            continue
        try:
            price = float(price_str) if price_str.strip() else None
        except ValueError:
            price = None
        db.session.add(MenuItem(shop_id=sid, name=name, price=price))
    db.session.commit()
    session.pop('ocr_result', None)
    flash('✅ 菜單品項已儲存', 'success')
    return redirect(url_for('manage_menu', sid=sid))

@app.route('/shops/<int:sid>/menu/add', methods=['POST'])
@login_required(admin_only=True)
def add_menu_item(sid):
    name = request.form.get('name', '').strip()
    price_str = request.form.get('price', '').strip()
    price = float(price_str) if price_str else None
    if not name:
        flash('請填寫品項名稱', 'error')
    else:
        db.session.add(MenuItem(shop_id=sid, name=name, price=price))
        db.session.commit()
        flash(f'✅ 已新增：{name}', 'success')
    return redirect(url_for('manage_menu', sid=sid))

@app.route('/shops/<int:sid>/menu/edit/<int:iid>', methods=['POST'])
@login_required(admin_only=True)
def edit_menu_item(sid, iid):
    item = db.get_or_404(MenuItem, iid)
    item.name = request.form.get('name', item.name).strip()
    price_str = request.form.get('price', '').strip()
    item.price = float(price_str) if price_str else None
    item.is_available = 'is_available' in request.form
    db.session.commit()
    flash('✅ 已更新品項', 'success')
    return redirect(url_for('manage_menu', sid=sid))

@app.route('/shops/<int:sid>/menu/delete/<int:iid>', methods=['POST'])
@login_required(admin_only=True)
def delete_menu_item(sid, iid):
    item = db.get_or_404(MenuItem, iid)
    db.session.delete(item)
    db.session.commit()
    flash('✅ 已刪除品項', 'success')
    return redirect(url_for('manage_menu', sid=sid))

# ── 補登訂單 ────────────────────────────────────────────
@app.route('/orders/add', methods=['GET', 'POST'])
@login_required(admin_only=True)
def add_order():
    if request.method == 'POST':
        user_code = request.form.get('user_code', '').strip()
        payer_code = request.form.get('payer_code', '').strip()
        meal_type = request.form.get('meal_type', 'lunch')
        item_name = request.form.get('item_name', '').strip()
        amount_str = request.form.get('amount', '').strip()
        date_str = request.form.get('order_date', date.today().strftime('%Y-%m-%d'))

        user = User.query.filter_by(user_code=user_code).first()
        payer = User.query.filter_by(user_code=payer_code).first() if payer_code else None

        if not user:
            flash(f'代號 {user_code} 不存在', 'error')
        elif not item_name:
            flash('請填寫品項', 'error')
        else:
            try:
                order_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                order_date = date.today()

            dm = DailyMenu.query.filter_by(menu_date=order_date, meal_type=meal_type).first()
            if not dm:
                dm = DailyMenu(menu_date=order_date, meal_type=meal_type)
                db.session.add(dm)
                db.session.commit()

            amount = float(amount_str) if amount_str else 0.0
            order = Order(user_id=user.id, daily_menu_id=dm.id,
                          items=item_name, amount=amount,
                          payer_id=payer.id if payer else None)
            db.session.add(order)
            db.session.commit()
            flash(f'✅ 已補登：{user.name} - {item_name}', 'success')
            return redirect(url_for('add_order'))

    users = User.query.filter_by(is_admin=False).order_by(db.cast(User.user_code, db.Integer)).all()
    return render_template('add_order.html', user=get_current_user(), users=users,
                           meal_types=app.config['MEAL_TYPES'])

# ── 記帳 ────────────────────────────────────────────────
@app.route('/accounting')
@login_required(admin_only=True)
def accounting():
    date_str = request.args.get('date', date.today().strftime('%Y-%m-%d'))
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        target_date = date.today()

    orders = (Order.query
              .join(DailyMenu)
              .join(User, Order.user_id == User.id)
              .filter(DailyMenu.menu_date == target_date)
              .order_by(db.cast(User.user_code, db.Integer))
              .all())

    by_user = {}
    for o in orders:
        if o.user_id not in by_user:
            by_user[o.user_id] = {'user': o.user, 'orders': [], 'total': 0, 'paid': 0}
        by_user[o.user_id]['orders'].append(o)
        by_user[o.user_id]['total'] += o.amount
        if o.paid:
            by_user[o.user_id]['paid'] += o.amount

    total = sum(o.amount for o in orders)
    paid = sum(o.amount for o in orders if o.paid)
    return render_template('accounting.html', user=get_current_user(),
                           target_date=target_date, by_user=by_user,
                           total=total, paid=paid, unpaid=total - paid,
                           meal_types=app.config['MEAL_TYPES'])

@app.route('/accounting/toggle_paid/<int:oid>', methods=['POST'])
@login_required(admin_only=True)
def toggle_paid(oid):
    o = db.get_or_404(Order, oid)
    o.paid = not o.paid
    db.session.commit()
    return redirect(request.referrer or url_for('accounting'))

@app.route('/accounting/delete/<int:oid>', methods=['POST'])
@login_required(admin_only=True)
def delete_order(oid):
    o = db.get_or_404(Order, oid)
    db.session.delete(o)
    db.session.commit()
    flash('✅ 已刪除訂單', 'success')
    return redirect(request.referrer or url_for('accounting'))

# ── 歷史 ────────────────────────────────────────────────
@app.route('/history')
@login_required(admin_only=True)
def history():
    page = request.args.get('page', 1, type=int)
    pagination = (Order.query.join(DailyMenu)
                  .order_by(DailyMenu.menu_date.desc(), Order.created_date.desc())
                  .paginate(page=page, per_page=50, error_out=False))
    return render_template('history.html', user=get_current_user(),
                           orders=pagination.items, pagination=pagination,
                           meal_types=app.config['MEAL_TYPES'])

# ── 設定 ────────────────────────────────────────────────
@app.route('/settings', methods=['GET', 'POST'])
@login_required(admin_only=True)
def settings():
    if request.method == 'POST':
        SystemSetting.set('push_hour', request.form.get('push_hour', '20'))
        SystemSetting.set('push_minute', request.form.get('push_minute', '30'))
        SystemSetting.set('default_payer_code', request.form.get('default_payer_code', ''))
        SystemSetting.set('breakfast_time', request.form.get('breakfast_time', '08:00'))
        SystemSetting.set('lunch_time', request.form.get('lunch_time', '10:30'))
        SystemSetting.set('dinner_time', request.form.get('dinner_time', '17:00'))
        db.session.commit()
        flash('✅ 設定已儲存', 'success')
        return redirect(url_for('settings'))

    s = {
        'push_hour': SystemSetting.get('push_hour', '20'),
        'push_minute': SystemSetting.get('push_minute', '30'),
        'default_payer_code': SystemSetting.get('default_payer_code', ''),
        'breakfast_time': SystemSetting.get('breakfast_time', '08:00'),
        'lunch_time': SystemSetting.get('lunch_time', '10:30'),
        'dinner_time': SystemSetting.get('dinner_time', '17:00'),
    }
    users = User.query.filter_by(is_admin=False).order_by(db.cast(User.user_code, db.Integer)).all()
    return render_template('settings.html', user=get_current_user(), s=s, users=users)

# ── LINE Webhook ─────────────────────────────────────────
@app.route('/callback', methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    text = event.message.text.strip()
    user_id = event.source.user_id
    group_id = getattr(event.source, 'group_id', None)

    # 記錄訊息
    log = LineMessage(message_type='text', message_content=text,
                      user_id=user_id, group_id=group_id)
    db.session.add(log)
    db.session.commit()

    reply = None

    if text.lower().startswith(('!點', '！點')):
        reply = order_bot.handle_order_command(text, event.reply_token, group_id)
        if reply is None:  # Flex 已直接發送
            return
    elif text == '!groupid' and group_id:
        reply = f'群組 ID：{group_id}'
    elif text.lower().startswith(('!菜單', '！菜單', '!menu', '！menu')):
        messages = order_bot.handle_menu_query(text, request.host_url)
        order_bot.send_messages(event.reply_token, messages)
        return
    elif text.isdigit() or text.lower().startswith(('!bill', '！bill')):
        reply = order_bot.handle_bill_query(text)
    elif text.lower().startswith(('!today', '！today', '!今日', '！今日', '!今天', '！今天')):
        reply = order_bot.handle_today_summary()
    elif text.lower().startswith(('!結清', '！結清', '!checkout', '！checkout')):
        reply = order_bot.handle_checkout(text)
    elif text.lower().startswith(('!help', '！help', '!說明', '！說明')):
        reply = order_bot.handle_help()
    elif text.lower().startswith(('!test_daily', '!測試統計')):
        summary = order_bot.generate_daily_unpaid_summary()
        reply = ('【測試預覽】\n\n' + summary) if summary else '目前無未付款訂單'

    if reply:
        order_bot.send_reply(event.reply_token, reply)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
