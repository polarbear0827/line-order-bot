import os
from datetime import timedelta

class Config:
    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    # 資料庫（存在 /app/data/ 讓 Docker Volume 持久化）
    SQLALCHEMY_DATABASE_URI = 'sqlite:////app/data/orders.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 檔案上傳
    UPLOAD_FOLDER = 'static/uploads'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

    # LINE Bot
    LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')
    LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
    LINE_GROUP_ID = os.environ.get('LINE_GROUP_ID')

    # OpenRouter AI（OCR 菜單辨識）
    OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')

    # 管理員金鑰
    ADMIN_ACCESS_KEY = os.environ.get('ADMIN_ACCESS_KEY') or 'admin123456'

    # 排程推播時間（每日）
    DAILY_PUSH_HOUR = int(os.environ.get('DAILY_PUSH_HOUR', 20))
    DAILY_PUSH_MINUTE = int(os.environ.get('DAILY_PUSH_MINUTE', 30))

    # 餐別設定
    MEAL_TYPES = {
        'breakfast': '早餐',
        'lunch': '午餐',
        'dinner': '晚餐',
        'drink': '飲料',
        'snack': '點心',
    }

    # 店家類別
    SHOP_CATEGORIES = {
        'bento': '便當',
        'noodle': '麵/湯',
        'dumpling': '水餃',
        'snack': '小吃',
        'fried_chicken': '炸雞/速食',
        'pasta': '義大利麵/燉飯',
        'hotpot': '熱炒/鍋物',
        'burger': '漢堡/輕食',
        'breakfast': '早餐',
        'drink': '飲料',
        'dessert': '咖啡/甜點',
        'others': '其他',
    }
