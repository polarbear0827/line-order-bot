from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date

db = SQLAlchemy()


class User(db.Model):
    """使用者（用代號識別）"""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    user_code = db.Column(db.String(10), nullable=False, unique=True)  # 代號，例如："2", "3", "12"
    name = db.Column(db.String(100), nullable=False)  # 姓名
    is_admin = db.Column(db.Boolean, default=False)
    created_date = db.Column(db.DateTime, default=datetime.utcnow)

    # 關聯
    # 該使用者點的訂單
    orders = db.relationship('Order',
                             backref='user',
                             lazy=True,
                             cascade='all, delete-orphan',
                             foreign_keys='Order.user_id')

    # 🔥 新增：該使用者代墊的訂單
    paid_orders = db.relationship('Order',
                                  backref='payer',
                                  lazy=True,
                                  foreign_keys='Order.payer_id')

    def __repr__(self):
        return f'<User {self.user_code}: {self.name}>'


class Menu(db.Model):
    """菜單"""
    __tablename__ = 'menus'

    id = db.Column(db.Integer, primary_key=True)
    meal_type = db.Column(db.String(20), nullable=False)  # breakfast, lunch, dinner, drink, snack
    menu_date = db.Column(db.Date, nullable=False, default=date.today)
    description = db.Column(db.String(200))  # 菜單描述
    filename = db.Column(db.String(200))  # 圖片檔名（選填）
    created_date = db.Column(db.DateTime, default=datetime.utcnow)

    # 關聯
    orders = db.relationship('Order', backref='menu', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Menu {self.menu_date} {self.meal_type}>'


class Order(db.Model):
    """訂單"""
    __tablename__ = 'orders'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    menu_id = db.Column(db.Integer, db.ForeignKey('menus.id'), nullable=False)
    items = db.Column(db.Text, nullable=False)
    amount = db.Column(db.Float, default=0.0)
    paid = db.Column(db.Boolean, default=False)
    note = db.Column(db.String(200))
    payer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # 🔥 新增這行
    created_date = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Order {self.user.user_code}: {self.items}>'


class LineMessage(db.Model):
    """LINE 訊息記錄（用於偵錯和審計）"""
    __tablename__ = 'line_messages'

    id = db.Column(db.Integer, primary_key=True)
    message_type = db.Column(db.String(50))  # text, image, etc.
    message_content = db.Column(db.Text)
    user_id = db.Column(db.String(100))  # LINE user ID
    group_id = db.Column(db.String(100))  # LINE group ID
    processed = db.Column(db.Boolean, default=False)
    created_date = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<LineMessage {self.message_type}>'