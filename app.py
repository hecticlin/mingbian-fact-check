"""
app.py - Flask 应用入口（生产部署版）
"""
from __future__ import annotations

import logging
import os
import warnings

warnings.filterwarnings('ignore')

from flask import Flask, render_template, redirect, url_for, session
from flask_mail import Mail

from routes.auth         import auth_bp
from routes.user_profile import profile_bp
from routes.news         import news_bp
from routes.detect       import detect_bp
from routes.llm_detect   import llm_detect_bp
from routes.reexamine    import reexamine_bp
from routes.admin        import admin_bp
from routes.chat         import chat_bp
from routes.mmr_detect   import mmr_detect_bp


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get('FLASK_SECRET_KEY') or os.urandom(32)

    app.config.update(
        MAIL_SERVER         = os.getenv('MAIL_SERVER', 'smtp.163.com'),
        MAIL_PORT           = int(os.getenv('MAIL_PORT', '465')),
        MAIL_USE_SSL        = True,
        MAIL_USE_TLS        = False,
        MAIL_USERNAME       = os.getenv('MAIL_USERNAME'),
        MAIL_PASSWORD       = os.getenv('MAIL_PASSWORD'),
        MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER') or os.getenv('MAIL_USERNAME'),
        MAIL_MAX_EMAILS     = None,
        MAIL_TIMEOUT        = 30,
    )
    Mail(app)

    os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
    os.makedirs(os.path.join(app.root_path, 'static', 'uploads', 'avatars'),     exist_ok=True)
    os.makedirs(os.path.join(app.root_path, 'static', 'uploads', 'news_images'), exist_ok=True)

    # 注册 Blueprint
    app.register_blueprint(auth_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(news_bp)
    app.register_blueprint(detect_bp)
    app.register_blueprint(llm_detect_bp)
    app.register_blueprint(reexamine_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(mmr_detect_bp)

    # 统一历史记录接口（使用 mmr_detect 的实现）
    from routes.mmr_detect import get_detection_history as _gh, get_detection_detail as _gd
    app.add_url_rule('/get_detection_history',
                     endpoint='get_detection_history',
                     view_func=_gh, methods=['GET'])
    app.add_url_rule('/get_detection_detail/<int:detect_id>',
                     endpoint='get_detection_detail',
                     view_func=_gd, methods=['GET'])

    # 首页
    @app.route('/')
    def index():
        return render_template('start_index.html')

    @app.route('/home')
    def home():
        if 'username' not in session:
            return redirect(url_for('auth.login_page'))
        role = session.get('user_type')
        if role == 'admin':
            return redirect(url_for('admin.admin_index'))
        elif role == 'experts':
            return redirect(url_for('auth.expert'))
        else:
            return redirect(url_for('auth.user'))

    # context_processor
    from utils.helpers import get_user_info_helper

    @app.context_processor
    def utility_processor():
        return dict(get_user_info=get_user_info_helper)

    # 连接池预热
    from utils.db import _get_pool
    try:
        _get_pool()
        print(" 数据库连接池初始化成功")
    except Exception as e:
        print(f"  数据库连接池初始化失败（请检查 MySQL 配置）: {e}")

    return app


application = create_app()

if __name__ == '__main__':
    application.run(debug=False, host=os.getenv('APP_HOST', '127.0.0.1'), port=int(os.getenv('APP_PORT', '3000')))
