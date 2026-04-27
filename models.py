from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date
import json

db = SQLAlchemy()


class User(db.Model):
    """使用者（代號識別，代號可隨時更動）"""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    user_code = db.Column(db.String(10), nullable=False, unique=True)  # 代號（如 "2", "18"）
    name = db.Column(db.String(100), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)      # 保留相容，role 為主
    created_date = db.Column(db.DateTime, default=datetime.utcnow)

    # ── 帳號權限欄位 ──────────────────────────────────────
    role = db.Column(db.String(20), default='user')      # 'provider' | 'admin' | 'user'
    username = db.Column(db.String(50), unique=True)     # 登入帳號
    password_enc = db.Column(db.Text)                    # AES 加密後的密碼
    must_change_pw = db.Column(db.Boolean, default=True) # 首次登入強制改密碼

    orders = db.relationship(
        'Order', backref='user', lazy=True,
        cascade='all, delete-orphan',
        foreign_keys='Order.user_id'
    )
    paid_orders = db.relationship(
        'Order', backref='payer', lazy=True,
        foreign_keys='Order.payer_id'
    )

    def __repr__(self):
        return f'<User {self.user_code}: {self.name} [{self.role}]>'


class Shop(db.Model):
    """店家"""
    __tablename__ = 'shops'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50))        # bento / noodle / dumpling / snack / breakfast / drink
    meal_types_json = db.Column(db.String(200), default='["lunch"]')  # JSON list
    menu_image = db.Column(db.String(200))     # 菜單照片路徑
    phone = db.Column(db.String(30))           # 店家電話
    business_days = db.Column(db.String(7), default='1111111')  # Mon-Sun, 1=open, 0=closed
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    created_date = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship(
        'MenuItem', backref='shop', lazy=True,
        cascade='all, delete-orphan'
    )
    daily_menus = db.relationship('DailyMenu', backref='shop', lazy=True)

    @property
    def meal_types(self):
        try:
            return json.loads(self.meal_types_json or '["lunch"]')
        except Exception:
            return ['lunch']

    @meal_types.setter
    def meal_types(self, value):
        self.meal_types_json = json.dumps(value)

    def __repr__(self):
        return f'<Shop {self.name}>'


class MenuItem(db.Model):
    """菜單品項（含預設價格）"""
    __tablename__ = 'menu_items'

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey('shops.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=True)   # None = 尚未確認
    is_available = db.Column(db.Boolean, default=True)
    created_date = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<MenuItem {self.name} ${self.price}>'


class DailyMenu(db.Model):
    """每日菜單（記錄哪天點哪家）"""
    __tablename__ = 'daily_menus'

    id = db.Column(db.Integer, primary_key=True)
    menu_date = db.Column(db.Date, nullable=False, default=date.today)
    meal_type = db.Column(db.String(20), nullable=False)  # breakfast/lunch/dinner/drink/snack
    shop_id = db.Column(db.Integer, db.ForeignKey('shops.id'), nullable=True)
    created_date = db.Column(db.DateTime, default=datetime.utcnow)

    orders = db.relationship(
        'Order', backref='daily_menu', lazy=True,
        cascade='all, delete-orphan'
    )

    __table_args__ = (
        db.UniqueConstraint('menu_date', 'meal_type', name='unique_daily_meal'),
    )

    def __repr__(self):
        return f'<DailyMenu {self.menu_date} {self.meal_type}>'


class Order(db.Model):
    """訂單"""
    __tablename__ = 'orders'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    daily_menu_id = db.Column(db.Integer, db.ForeignKey('daily_menus.id'), nullable=False)
    menu_item_id = db.Column(db.Integer, db.ForeignKey('menu_items.id'), nullable=True)  # 自動比對的品項
    items = db.Column(db.String(200), nullable=False)    # 品項名稱（顯示用）
    amount = db.Column(db.Float, default=0.0)            # 從 MenuItem 自動帶入
    paid = db.Column(db.Boolean, default=False)
    payer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # 代墊人
    note = db.Column(db.String(200))
    created_date = db.Column(db.DateTime, default=datetime.utcnow)

    menu_item = db.relationship('MenuItem', foreign_keys=[menu_item_id])

    def __repr__(self):
        return f'<Order {self.user.user_code if self.user else "?"}: {self.items}>'


class LineMessage(db.Model):
    """LINE 訊息紀錄（偵錯用）"""
    __tablename__ = 'line_messages'

    id = db.Column(db.Integer, primary_key=True)
    message_type = db.Column(db.String(50))
    message_content = db.Column(db.Text)
    user_id = db.Column(db.String(100))
    group_id = db.Column(db.String(100))
    processed = db.Column(db.Boolean, default=False)
    created_date = db.Column(db.DateTime, default=datetime.utcnow)


class SystemSetting(db.Model):
    """系統設定（key-value 存放）"""
    __tablename__ = 'settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)
    updated_date = db.Column(db.DateTime, default=datetime.utcnow)

    @staticmethod
    def get(key, default=None):
        row = SystemSetting.query.filter_by(key=key).first()
        return row.value if row else default

    @staticmethod
    def set(key, value):
        row = SystemSetting.query.filter_by(key=key).first()
        if row:
            row.value = str(value)
            row.updated_date = datetime.utcnow()
        else:
            row = SystemSetting(key=key, value=str(value))
            db.session.add(row)


class IpBan(db.Model):
    """IP 登入失敗封鎖記錄"""
    __tablename__ = 'ip_bans'

    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(50), unique=True, nullable=False, index=True)
    fail_count = db.Column(db.Integer, default=0)      # 累計失敗次數（永不清零）
    banned_until = db.Column(db.DateTime, nullable=True)  # None = 目前未封鎖
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<IpBan {self.ip} fails={self.fail_count}>'


class LoginLog(db.Model):
    """登入紀錄（Provider 可查閱）"""
    __tablename__ = 'login_logs'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50))          # 輸入的帳號
    role = db.Column(db.String(20))              # 登入成功後的角色
    ip = db.Column(db.String(50))
    success = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<LoginLog {self.username} {"OK" if self.success else "FAIL"}>'
