import os
from datetime import timedelta

# ==========================================
# 1. 將字典定義移出 Class 外面，避免 500 錯誤
# ==========================================
RAW_MENU_MAPS = {
    'Fangyuan Snack Bar.jpg': ['芳苑小吃部', '芳苑小吃', '小吃部'],
    'Eight Chefs.jpg': ['八廚', '八除', '8廚', '8除'],
    'Ah Jun Meat Soup.jpg': ['阿俊肉羹', '阿峻肉羹', '阿駿肉羹', '阿俊', '阿峻', '阿駿'],
    'Fangyuan Hotel.jpg': ['芳苑大飯店', '大飯店', '大乾麵'],
    'Gathering dumplings.jpg': ['聚餃子', '水餃'],
    'For your convenience.jpg': ['方便當', '乎你方', '乎您方', '乎你芳', '乎您芳'],
    'Gathering from all directions.jpg': ['八方雲集', '八方', '8方雲集', '8方'],
    'Specialty Bento Boxes.jpg': ['食分', '十分', '食分便當', '十分便當'],
    'Enlightenment.jpg': ['悟饕', '物饕', '誤饕', '誤掏', '悟掏'],
    'Heavenly Army Delicacies.jpg': ['天軍名饌', '有特餐那個', '天軍名傳', '天君名饌', '天君名傳', '特餐'],
    'Eureka.jpg': ['尤里卡', '由里卡'],
    'Hao Ji Food.jpg': ['豪記美食', '豪記', '豪紀'],
    'Wang Ji.jpg': ['王記', '王紀'],
    'Hong Chaoshou.jpg': ['洪炒手', '宏炒手', '鴨賞', '洪抄手', '宏抄手', '洪妙手', '宏妙手'],
    'Traditional Rice Cake Restaurant.jpg': ['古早味', '古早味米糕', '古早味米糕食堂', '米糕'],
    'Taichung breakfast shop.jpg': ['台中美', '前面那家', '早餐店', '711隔壁', '常吃的早餐', '常吃的那家'],
    'Guiji.jpg': ['龜記', '規記', '龜紀', '歸記'],
    'T4.jpg': ['T4', 'T four', '踢4', '梯4', 't4'],
    'KEBUKE.jpg': ['KEBUKE', '可不可', '渴不可', '可不渴', '渴不渴', 'kebuke'],
    'Mikesha.jpg': ['迷克夏', 'Mikesha', '迷克下', '謎克夏', 'mikesha'],
    'OOLONG TEA.jpg': ['得正', '德政', '負負'],
    'Morning Kitchen.jpg': ['誠間廚房', '晨間廚房'],
    'ABO.jpg': ['ABO', 'abo', '阿寶'],
    'Hongye.jpg': ['弘爺', '宏爺', '洪爺', '鴻爺', '弘爺漢堡', '宏爺漢堡', '洪爺漢堡', '鴻爺漢堡'],
    'steamed stuffed bun.jpg': ['包子店', '飯糰店', '包子', '飯糰', '饅頭店', '饅頭']

}

# 自動生成別名對照表
GENERATED_ALIASES = {
    alias.lower(): filename
    for filename, aliases in RAW_MENU_MAPS.items()
    for alias in aliases
}


class Config:
    # Flask 設定
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-secret-key-change-this-in-production'

    # 資料庫設定
    SQLALCHEMY_DATABASE_URI = 'sqlite:///orders.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Session 設定
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    # 檔案上傳設定
    UPLOAD_FOLDER = 'static/uploads'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

    # LINE Bot 設定（如果沒有就先用假的）
    LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET') or '748ac77466ba2e7bd2aecaf8f299b7cb'
    LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN') or 'ond6EnKwZ18G/mW8wqU8W4CgzeiIvbmZzR7SpcUzajiBTHhsWQgZrxCojGaWjUPkKx5j7kXQpaulBqDSJMHCtuqluWH3nhY98vyu7/Dipc50c/dJvVC6GFXVze88/9zOaNa+wrXSEVOc7IPqAFVcNwdB04t89/1O/w1cDnyilFU='
    # LINE 群組 ID（用於推播訊息）
    LINE_GROUP_ID = os.environ.get('LINE_GROUP_ID') or 'C8eb62b9ffa880d642beec8037cc28628'  # 需要從 LINE Bot 取得

    # 餐別設定
    MEAL_TYPES = {
        'breakfast': '早餐',
        'lunch': '午餐',
        'dinner': '晚餐',
        'drink': '飲料',
        'snack': '點心'
    }

    # 將生成好的字典放入 Config 中
    MENU_MAPS = RAW_MENU_MAPS
    MENU_ALIASES = GENERATED_ALIASES

    # 管理員設定
    ADMIN_ACCESS_KEY = os.environ.get('ADMIN_ACCESS_KEY') or 'admin123456'