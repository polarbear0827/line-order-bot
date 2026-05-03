import os
import sys
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort, jsonify
from werkzeug.utils import secure_filename
from datetime import datetime, date
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from config import Config
from models import db, User, Shop, MenuItem, DailyMenu, Order, LineMessage, SystemSetting, IpBan, LoginLog

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
    # ── SQLite 欄位 Migration ───────────────────────────
    with db.engine.connect() as conn:
        for ddl in [
            'ALTER TABLE shops ADD COLUMN phone VARCHAR(30)',
            'ALTER TABLE shops ADD COLUMN business_days VARCHAR(7) DEFAULT "1111111"',
            'ALTER TABLE orders ADD COLUMN note VARCHAR(200)',
            'ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT "user"',
            'ALTER TABLE users ADD COLUMN username VARCHAR(50)',
            'ALTER TABLE users ADD COLUMN password_enc TEXT',
            'ALTER TABLE users ADD COLUMN must_change_pw BOOLEAN DEFAULT 1',
        ]:
            try:
                conn.execute(db.text(ddl))
                conn.commit()
            except Exception:
                pass
        # 修正舊 admin 角色
        try:
            conn.execute(db.text("UPDATE users SET role='admin' WHERE is_admin=1 AND (role IS NULL OR role='user')"))
            conn.commit()
        except Exception:
            pass

    # ── 資料 Migration：補帳號 / 密碼 / Provider 帳號 ──
    def _local_encrypt(pw: str) -> str:
        import base64 as _b64, hashlib as _hs
        from cryptography.fernet import Fernet as _F
        raw = os.environ.get('AES_KEY', 'fallback-key-please-set-in-env')
        key = _b64.urlsafe_b64encode(_hs.sha256(raw.encode()).digest())
        return _F(key).encrypt(pw.encode()).decode()

    import random as _rand
    admin_counter = 1
    for u in User.query.filter_by(role='admin').all():
        if not u.username:
            u.username = f'admin{admin_counter:03d}'
        admin_counter += 1
        if not u.password_enc:
            pw = ''.join([str(_rand.randint(0, 9)) for _ in range(4)])
            u.password_enc = _local_encrypt(pw)
            u.must_change_pw = True
    for u in User.query.filter(User.role == 'user', User.user_code != 'admin').all():
        if not u.username:
            try:
                u.username = f'user{int(u.user_code):03d}'
            except ValueError:
                u.username = f'user_{u.user_code}'
        if not u.password_enc:
            pw = ''.join([str(_rand.randint(0, 9)) for _ in range(4)])
            u.password_enc = _local_encrypt(pw)
            u.must_change_pw = True
    db.session.commit()

    # 建立 / 更新 Provider 帳號（支援多個：PROVIDER_USERNAME, PROVIDER_2_USERNAME...）
    _provider_slots = [('PROVIDER_USERNAME', 'PROVIDER_PASSWORD', 'provider')]
    for i in range(2, 10):
        _provider_slots.append((f'PROVIDER_{i}_USERNAME', f'PROVIDER_{i}_PASSWORD', f'provider{i}'))

    for ukey, pkey, code in _provider_slots:
        prov_username = os.environ.get(ukey, '')
        prov_password = os.environ.get(pkey, '')
        if not prov_username or not prov_password:
            continue
        provider = User.query.filter_by(username=prov_username).first()
        if not provider:
            provider = User(
                user_code=code, name='系統管理者',
                role='provider', is_admin=True,
                username=prov_username, must_change_pw=False,
            )
            db.session.add(provider)
        provider.password_enc = _local_encrypt(prov_password)
        provider.must_change_pw = False
        db.session.commit()

# ── AES 加解密 ──────────────────────────────────────────
import base64, hashlib, random, string
from cryptography.fernet import Fernet

def _get_fernet():
    raw = os.environ.get('AES_KEY', 'fallback-key-please-set-in-env')
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    return Fernet(key)

def encrypt_password(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()

def decrypt_password(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except Exception:
        return '（解密失敗）'

def generate_password(length=4) -> str:
    return ''.join(random.choices(string.digits, k=length))

# ── 輔助 ────────────────────────────────────────────────
def get_current_user():
    if 'user_id' in session:
        return db.session.get(User, session['user_id'])
    return None

def login_required(roles=None, admin_only=False):
    """
    roles: list of allowed roles, e.g. ['provider','admin']
           None = any authenticated user
    admin_only: legacy compat — treats as roles=['provider','admin']
    """
    def decorator(f):
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if not user:
                flash('請先登入', 'error')
                return redirect(url_for('login'))
            allowed = roles or (['provider', 'admin'] if admin_only else None)
            if allowed and (user.role or 'user') not in allowed:
                flash('權限不足', 'error')
                dest = url_for('user_portal') if user.role == 'user' else url_for('dashboard')
                return redirect(dest)
            return f(*args, **kwargs)
        wrapper.__name__ = f.__name__
        return wrapper
    return decorator

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# ── IP 封鎖工具 ─────────────────────────────────────────
from datetime import timedelta

# 累計失敗次數 → 封鎖時長（只在剛好踩到門檻時觸發）
_BAN_THRESHOLDS = {
    5:  timedelta(hours=1),
    8:  timedelta(hours=6),
    11: timedelta(days=1),
    14: timedelta(days=7),
    17: timedelta(days=30),
}

def _get_client_ip():
    """取得真實 IP（支援 ngrok / 反向代理的 X-Forwarded-For）"""
    return request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()

def _ban_duration_for(fail_count):
    """根據累計失敗次數回傳應封鎖時長，不是門檻時回傳 None"""
    if fail_count in _BAN_THRESHOLDS:
        return _BAN_THRESHOLDS[fail_count]
    # fail >= 20，之後每 3 次封鎖 6 個月
    if fail_count >= 20 and (fail_count - 17) % 3 == 0:
        return timedelta(days=180)
    return None

def _check_ip_banned(ip):
    """回傳 (is_banned: bool, banned_until: datetime|None)"""
    record = IpBan.query.filter_by(ip=ip).first()
    if not record or not record.banned_until:
        return False, None
    if record.banned_until > datetime.utcnow():
        return True, record.banned_until
    return False, None

def _record_fail(ip):
    """記錄一次失敗，若踩到門檻就寫入封鎖時間，回傳 IpBan record"""
    record = IpBan.query.filter_by(ip=ip).first()
    if not record:
        record = IpBan(ip=ip, fail_count=0)
        db.session.add(record)
    record.fail_count += 1
    record.updated_at = datetime.utcnow()
    duration = _ban_duration_for(record.fail_count)
    if duration:
        record.banned_until = datetime.utcnow() + duration
    db.session.commit()
    return record

def _record_success(ip):
    """登入成功：重置封鎖（但保留 fail_count 作為記錄）"""
    record = IpBan.query.filter_by(ip=ip).first()
    if record:
        record.banned_until = None
        record.fail_count = 0
        record.updated_at = datetime.utcnow()
        db.session.commit()

# ── 認證 ────────────────────────────────────────────────
def _login_redirect(user):
    """依角色跳轉到對應首頁"""
    if user.role == 'user':
        return redirect(url_for('user_portal'))
    return redirect(url_for('dashboard'))

@app.route('/')
def index():
    u = get_current_user()
    if u:
        return _login_redirect(u)
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    ip = _get_client_ip()
    if request.method == 'POST':
        is_banned, banned_until = _check_ip_banned(ip)
        if is_banned:
            tw_tz = pytz.timezone('Asia/Taipei')
            until_tw = banned_until.replace(tzinfo=pytz.utc).astimezone(tw_tz)
            flash(f'此 IP 已被封鎖，解鎖時間：{until_tw.strftime("%Y/%m/%d %H:%M")}', 'error')
            return render_template('login.html')

        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        user = User.query.filter_by(username=username).first()
        login_ok = False
        if user and user.password_enc:
            try:
                login_ok = (decrypt_password(user.password_enc) == password)
            except Exception:
                login_ok = False

        if login_ok:
            session['user_id'] = user.id
            session.permanent = True
            _record_success(ip)
            db.session.add(LoginLog(username=username, role=user.role, ip=ip, success=True))
            db.session.commit()
            if user.must_change_pw:
                flash('請先修改初始密碼', 'warning')
                return redirect(url_for('change_password'))
            return _login_redirect(user)

        # 失敗
        record = _record_fail(ip)
        db.session.add(LoginLog(username=username, role=None, ip=ip, success=False))
        db.session.commit()
        if record.banned_until and record.banned_until > datetime.utcnow():
            tw_tz = pytz.timezone('Asia/Taipei')
            until_tw = record.banned_until.replace(tzinfo=pytz.utc).astimezone(tw_tz)
            flash(f'錯誤次數過多，IP 已封鎖至 {until_tw.strftime("%Y/%m/%d %H:%M")}', 'error')
        elif record.fail_count < 5:
            flash(f'帳號或密碼錯誤（還有 {5 - record.fail_count} 次機會）', 'error')
        else:
            flash(f'帳號或密碼錯誤（累計 {record.fail_count} 次失敗）', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/change-password', methods=['GET', 'POST'])
@login_required(roles=['provider', 'admin', 'user'])
def change_password():
    user = get_current_user()
    if request.method == 'POST':
        old_pw = request.form.get('old_password', '').strip()
        new_pw = request.form.get('new_password', '').strip()
        confirm = request.form.get('confirm_password', '').strip()
        # 驗證舊密碼
        current = decrypt_password(user.password_enc) if user.password_enc else ''
        if current != old_pw:
            flash('目前密碼錯誤', 'error')
            return render_template('change_password.html', user=user)
        # 驗證新密碼規則：4-8 位，數字或英數
        if not (4 <= len(new_pw) <= 8) or not new_pw.isalnum():
            flash('新密碼需為 4–8 位英文或數字', 'error')
            return render_template('change_password.html', user=user)
        if new_pw != confirm:
            flash('兩次密碼不一致', 'error')
            return render_template('change_password.html', user=user)
        user.password_enc = encrypt_password(new_pw)
        user.must_change_pw = False
        db.session.commit()
        flash('密碼修改成功！', 'success')
        return _login_redirect(user)
    return render_template('change_password.html', user=user)

@app.route('/favicon.ico')
def favicon():
    return '', 204

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
@login_required(roles=['provider', 'admin'])
def guide():
    return render_template('guide.html', user=get_current_user())

@app.route('/guide/admin')
@login_required(roles=['provider', 'admin'])
def guide_admin():
    return render_template('guide_admin.html', user=get_current_user())

@app.route('/guide/user')
@login_required(roles=['provider', 'admin', 'user'])
def guide_user():
    return render_template('guide_user.html', user=get_current_user())

@app.route('/guide/provider')
@login_required(roles=['provider'])
def guide_provider():
    return render_template('guide_provider.html', user=get_current_user())

# ── User 個人入口 ────────────────────────────────────────
@app.route('/user-portal')
@login_required(roles=['provider', 'admin', 'user'])
def user_portal():
    user = get_current_user()
    today = date.today()
    today_orders = Order.query.join(DailyMenu).filter(
        DailyMenu.menu_date == today,
        Order.user_id == user.id
    ).all()
    # 未付款
    unpaid = [o for o in Order.query.filter_by(user_id=user.id, paid=False).all()]
    unpaid_total = sum(o.amount for o in unpaid)
    return render_template('user_portal.html', user=user,
                           today_orders=today_orders,
                           unpaid=unpaid, unpaid_total=unpaid_total,
                           today=today)

@app.route('/user-portal/history')
@login_required(roles=['provider', 'admin', 'user'])
def user_portal_history():
    user = get_current_user()
    start_str = request.args.get('start', '')
    end_str   = request.args.get('end', '')
    try:
        start_date = datetime.strptime(start_str, '%Y-%m-%d').date() if start_str else date(date.today().year, 1, 1)
        end_date   = datetime.strptime(end_str,   '%Y-%m-%d').date() if end_str   else date.today()
    except ValueError:
        start_date, end_date = date(date.today().year, 1, 1), date.today()
    orders = Order.query.join(DailyMenu).filter(
        Order.user_id == user.id,
        DailyMenu.menu_date >= start_date,
        DailyMenu.menu_date <= end_date,
    ).order_by(DailyMenu.menu_date.desc()).all()
    total = sum(o.amount for o in orders)
    paid  = sum(o.amount for o in orders if o.paid)
    return jsonify({
        'orders': [{
            'date': o.daily_menu.menu_date.strftime('%Y/%m/%d'),
            'meal_type': o.daily_menu.meal_type,
            'items': o.items,
            'amount': o.amount,
            'paid': o.paid,
            'shop': o.daily_menu.shop.name if o.daily_menu.shop_id else '未記錄',
        } for o in orders],
        'total': total, 'paid': paid, 'unpaid': total - paid,
        'start': start_date.strftime('%Y/%m/%d'),
        'end':   end_date.strftime('%Y/%m/%d'),
    })

# ── Provider 後台 ────────────────────────────────────────
@app.route('/provider/panel')
@login_required(roles=['provider'])
def provider_panel():
    import pytz as _tz
    tw = _tz.timezone('Asia/Taipei')
    bans = IpBan.query.order_by(IpBan.updated_at.desc()).all()
    now_utc = datetime.utcnow()
    ban_list = []
    for b in bans:
        banned = b.banned_until and b.banned_until > now_utc
        until_tw = b.banned_until.replace(tzinfo=_tz.utc).astimezone(tw).strftime('%Y/%m/%d %H:%M') if b.banned_until else None
        ban_list.append({'id': b.id, 'ip': b.ip, 'fail_count': b.fail_count,
                         'banned': banned, 'until': until_tw})
    logs = LoginLog.query.order_by(LoginLog.created_at.desc()).limit(200).all()
    admins = User.query.filter_by(role='admin').order_by(User.username).all()
    users  = User.query.filter_by(role='user').order_by(User.username).all()
    return render_template('provider_panel.html', user=get_current_user(),
                           ban_list=ban_list, logs=logs, admins=admins, users=users,
                           decrypt_password=decrypt_password)

@app.route('/provider/unban/<int:ban_id>', methods=['POST'])
@login_required(roles=['provider'])
def provider_unban(ban_id):
    ban = db.get_or_404(IpBan, ban_id)
    ban.banned_until = None
    ban.fail_count = 0
    db.session.commit()
    flash(f'✅ 已解鎖 {ban.ip}', 'success')
    return redirect(url_for('provider_panel'))

@app.route('/provider/add-admin', methods=['POST'])
@login_required(roles=['provider'])
def provider_add_admin():
    name = request.form.get('name', '').strip()
    if not name:
        flash('請輸入姓名', 'error')
        return redirect(url_for('provider_panel'))
    # 計算下一個 admin 編號
    count = User.query.filter_by(role='admin').count() + 1
    username = f'admin{count:03d}'
    while User.query.filter_by(username=username).first():
        count += 1
        username = f'admin{count:03d}'
    pw = generate_password(4)
    u = User(user_code=f'adm{count}', name=name, role='admin', is_admin=True,
             username=username, password_enc=encrypt_password(pw), must_change_pw=True)
    db.session.add(u)
    db.session.commit()
    flash(f'✅ 已新增 Admin：{username}，初始密碼：{pw}', 'success')
    return redirect(url_for('provider_panel'))

@app.route('/provider/reset-password/<int:uid>', methods=['POST'])
@login_required(roles=['provider'])
def provider_reset_password(uid):
    u = db.get_or_404(User, uid)
    pw = generate_password(4)
    u.password_enc = encrypt_password(pw)
    u.must_change_pw = True
    db.session.commit()
    flash(f'✅ {u.username} 密碼已重設為：{pw}', 'success')
    return redirect(url_for('provider_panel'))

@app.route('/provider/delete-admin/<int:uid>', methods=['POST'])
@login_required(roles=['provider'])
def provider_delete_admin(uid):
    u = db.get_or_404(User, uid)
    if u.role != 'admin':
        flash('只能刪除 Admin 帳號', 'error')
    else:
        db.session.delete(u)
        db.session.commit()
        flash(f'✅ 已刪除 {u.username}', 'success')
    return redirect(url_for('provider_panel'))

@app.route('/shops/add', methods=['POST'])
@login_required(admin_only=True)
def add_shop():
    name = request.form.get('name', '').strip()
    category = request.form.get('category', '')
    meal_types = request.form.getlist('meal_types')
    phone = request.form.get('phone', '').strip()
    business_days = ''.join(
        '1' if request.form.get(f'day_{i}') else '0' for i in range(7)
    )
    if not name:
        flash('請填寫店家名稱', 'error')
    else:
        s = Shop(name=name, category=category, phone=phone, business_days=business_days)
        s.meal_types = meal_types or ['lunch']
        db.session.add(s)
        db.session.commit()
        flash(f'✅ 已新增：{name}', 'success')
    return redirect(url_for('manage_shops'))

@app.route('/shops/edit/<int:sid>', methods=['POST'])
@login_required(admin_only=True)
def edit_shop(sid):
    s = db.get_or_404(Shop, sid)
    s.name = request.form.get('name', s.name).strip()
    s.category = request.form.get('category', s.category)
    s.phone = request.form.get('phone', '').strip()
    meal_types = request.form.getlist('meal_types')
    s.meal_types = meal_types or ['lunch']
    s.business_days = ''.join(
        '1' if request.form.get(f'day_{i}') else '0' for i in range(7)
    )
    db.session.commit()
    flash(f'✅ 已更新：{s.name}', 'success')
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

@app.route('/orders/<int:oid>/toggle_paid', methods=['POST'])
@login_required(admin_only=True)
def toggle_paid(oid):
    o = db.get_or_404(Order, oid)
    o.paid = not o.paid
    db.session.commit()
    return redirect(request.referrer or url_for('accounting'))

@app.route('/debug_menu')
@login_required(admin_only=True)
def debug_menu():
    items = MenuItem.query.all()
    result = []
    for i in items:
        result.append({
            'id': i.id,
            'shop_id': i.shop_id,
            'name': repr(i.name),
            'price': repr(i.price),
            'is_available': i.is_available
        })
    return jsonify(result)

@app.route('/orders/<int:oid>/amount', methods=['POST'])
@login_required(admin_only=True)
def update_order_amount(oid):
    o = db.get_or_404(Order, oid)
    try:
        new_amount = float(request.form.get('amount', 0))
        o.amount = new_amount
        db.session.commit()
        flash('✅ 已更新金額', 'success')
    except ValueError:
        flash('❌ 金額格式錯誤', 'error')
    return redirect(request.referrer or url_for('accounting'))

@app.route('/orders/<int:oid>/delete', methods=['POST'])
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
    from sqlalchemy import distinct
    # 所有有訂單的日期
    dates_with_orders = [
        row[0] for row in
        db.session.query(distinct(DailyMenu.menu_date))
        .join(Order, Order.daily_menu_id == DailyMenu.id)
        .order_by(DailyMenu.menu_date.desc())
        .all()
    ]

    # 選擇的日期（預設今天，若無訂單則取最近有訂單的日期）
    date_str = request.args.get('date')
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = date.today()
    else:
        selected_date = dates_with_orders[0] if dates_with_orders else date.today()

    # 該日訂單
    orders = (Order.query.join(DailyMenu)
              .filter(DailyMenu.menu_date == selected_date)
              .order_by(DailyMenu.meal_type, Order.created_date)
              .all())

    # 日曆用：本月的有訂單日期 set
    cal_year  = request.args.get('year',  selected_date.year,  type=int)
    cal_month = request.args.get('month', selected_date.month, type=int)
    order_dates_set = {d.strftime('%Y-%m-%d') for d in dates_with_orders}

    import calendar
    cal = calendar.monthcalendar(cal_year, cal_month)

    return render_template('history.html',
                           user=get_current_user(),
                           orders=orders,
                           selected_date=selected_date,
                           cal_year=cal_year,
                           cal_month=cal_month,
                           cal=cal,
                           order_dates_set=order_dates_set,
                           now=date.today(),
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
    elif text.lower().startswith(('!today', '！today', '!今日', '！今日', '!今天', '！今天')) and not text.lower().startswith(('!今天吃', '！今天吃')):
        reply = order_bot.handle_today_summary()
    elif text.lower().startswith(('!今天吃什麼', '！今天吃什麼', '!吃什麼', '！吃什麼', '!隨機', '！隨機')):
        reply = order_bot.handle_suggest_shops()
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
