print("開始測試...")

try:
    print("1. 導入 Flask...")
    from flask import Flask

    print("   ✓ Flask 導入成功")

    print("2. 導入 config...")
    from config import Config

    print("   ✓ Config 導入成功")

    print("3. 導入 models...")
    from models import db, User, Menu, Order, LineMessage

    print("   ✓ Models 導入成功")

    print("4. 導入 line_handler...")
    from line_handler import OrderBot

    print("   ✓ LineHandler 導入成功")

    print("5. 導入 LINE SDK...")
    from linebot.v3 import WebhookHandler
    from linebot.v3.messaging import Configuration, ApiClient, MessagingApi

    print("   ✓ LINE SDK 導入成功")

    print("6. 初始化 Flask app...")
    app = Flask(__name__)
    app.config.from_object(Config)
    print("   ✓ Flask app 初始化成功")

    print("7. 初始化資料庫...")
    db.init_app(app)
    print("   ✓ 資料庫初始化成功")

    print("8. 初始化 LINE Bot...")
    configuration = Configuration(access_token=app.config['LINE_CHANNEL_ACCESS_TOKEN'])
    handler = WebhookHandler(app.config['LINE_CHANNEL_SECRET'])
    order_bot = OrderBot(app.config)
    print("   ✓ LINE Bot 初始化成功")

    print("9. 創建資料庫表格...")
    with app.app_context():
        db.create_all()
        print("   ✓ 資料庫表格創建成功")

    print("\n✅ 所有測試通過！")
    print("可以嘗試啟動完整的 app.py")

except Exception as e:
    print(f"\n❌ 錯誤發生在上面的步驟")
    print(f"錯誤訊息: {e}")
    import traceback

    traceback.print_exc()

input("\n按 Enter 鍵退出...")