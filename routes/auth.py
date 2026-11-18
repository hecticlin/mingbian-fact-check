"""
routes/auth.py - 认证相关 Blueprint

包含原 app.py 中的：
  GET/POST /login              登录页面与登录处理
  GET      /logout             登出
  POST     /register           注册
  GET      /refresh-captcha    刷新验证码
  POST     /forgot-password    忘记密码（发送重置邮件）
  GET/POST /reset-password/<token>  重置密码页面
  GET      /expert             专家首页跳转
  GET      /user               普通用户首页跳转
  GET      /expert/first_login 专家首次登录页
  GET      /check_login        判断是否登录
"""

import re
import random
import string
import time

from flask import (
    Blueprint, render_template, request, session,
    redirect, url_for, jsonify, current_app
)
from flask_mail import Mail, Message

from utils.db import db

auth_bp = Blueprint('auth', __name__)


# 工具函数

def generate_reset_token():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=64))


def verify_user_credentials(username, email):
    """验证用户名和邮箱是否匹配，返回 (user_type, username) 或 (None, None)"""
    checks = [
        ("SELECT user_name FROM users   WHERE user_name   = %s AND user_email   = %s", '普通用户'),
        ("SELECT export_name FROM experts WHERE export_name = %s AND export_email = %s", 'experts'),
        ("SELECT admin_name FROM admin   WHERE admin_name  = %s AND admin_email  = %s", 'admin'),
    ]
    for sql, utype in checks:
        r = db.fetch_one(sql, (username, email))
        if r:
            return utype, username
    return None, None


def save_reset_token(username, email, user_type, token):
    expires_at = int(time.time()) + 30 * 60
    db.execute_query(
        "INSERT INTO password_resets (username,email,user_type,token,expires_at,used) "
        "VALUES (%s,%s,%s,%s,%s,0)",
        (username, email, user_type, token, expires_at)
    )
    return True


def send_reset_email(recipient_email, reset_link):
    try:
        mail = Mail(current_app)
        msg = Message(
            subject='明辨新闻检测系统 - 密码重置',
            recipients=[recipient_email]
        )
        msg.html = f'''
<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
body{{font-family:Arial,sans-serif;color:#1f4462;max-width:600px;margin:0 auto;padding:20px;}}
.container{{background:linear-gradient(145deg,#f0f7ff,#e3f0fd);border-radius:30px;padding:40px;box-shadow:0 10px 30px rgba(0,30,60,0.1);}}
h2{{color:#1a4f7a;}}
.button{{display:inline-block;background:linear-gradient(135deg,#1a4f7a,#2d6fc4);color:white;text-decoration:none;padding:14px 40px;border-radius:40px;margin:20px 0;font-weight:600;}}
.footer{{margin-top:30px;font-size:.9rem;color:#6f8fae;}}
</style></head><body><div class="container">
<h2>🔐 密码重置请求</h2>
<p>您好，请点击下方按钮重置密码（链接30分钟内有效）：</p>
<div style="text-align:center;"><a href="{reset_link}" class="button">重置密码</a></div>
<p>如无法点击，请复制链接：<small style="color:#2d6fc4;">{reset_link}</small></p>
<div class="footer"><p>明辨团队</p></div>
</div></body></html>'''
        mail.send(msg)
        return True
    except Exception as e:
        print(f"邮件发送失败: {e}")
        return False


# 登录
@auth_bp.route('/login', methods=['GET', 'POST'])
def login_page():
    from utils.captcha import captcha_gen

    if request.method == 'GET':
        code, img_str = captcha_gen.generate_captcha()
        session['captcha']      = code
        session['captcha_time'] = time.time()
        return render_template('login.html', captcha_image=img_str)

    username      = request.form.get('username')
    password      = request.form.get('password')
    user_type     = request.form.get('userType')
    captcha_input = request.form.get('captcha')

    def regen(error):
        code, img_str = captcha_gen.generate_captcha()
        session['captcha']      = code
        session['captcha_time'] = time.time()
        return render_template('login.html', error=error, captcha_image=img_str,
                               last_username=username, last_password=password,
                               last_user_type=user_type)

    if not captcha_input:
        return regen('请输入验证码！')
    if time.time() - session.get('captcha_time', 0) > 300:
        return regen('验证码已过期，请刷新重试！')
    if captcha_input.upper() != session.get('captcha', '').upper():
        return regen('验证码错误！')

    try:
        db.connect()
        sql_map = {
            'admin':   "SELECT * FROM admin   WHERE admin_name  = %s AND admin_password  = %s",
            'experts': "SELECT * FROM experts WHERE export_name = %s AND export_password = %s",
        }
        sql = sql_map.get(user_type,
              "SELECT * FROM users WHERE user_name = %s AND user_password = %s")
        results = db.fetch_all(sql, (username, password))
        db.close()

        if not results:
            return regen('用户名或密码错误！')

        user = results[0]
        session['username']  = username
        session['user_type'] = user_type
        session.pop('captcha', None)
        session.pop('captcha_time', None)

        db.connect()
        if user_type == 'experts':
            if user['certification_status'] == '已认证' and user['approval_status'] == '通过':
                db.execute_query("UPDATE experts SET login_count=login_count+1 WHERE export_name=%s", (username,))
                db.close()
                return redirect(url_for('auth.expert') if user['login_count'] > 0 else url_for('auth.expert_first_login'))
            db.close()
            return redirect(url_for('auth.expert_first_login'))

        if user_type == 'admin':
            db.execute_query("UPDATE admin SET login_count=login_count+1 WHERE admin_name=%s", (username,))
            db.close()
            return redirect(url_for('admin.admin_index'))

        db.execute_query("UPDATE users SET login_count=login_count+1 WHERE user_name=%s", (username,))
        db.close()
        return redirect(url_for('auth.user'))

    except Exception as e:
        return regen(f'登录失败: {e}')


@auth_bp.route('/refresh-captcha')
def refresh_captcha():
    from utils.captcha import captcha_gen
    code, img_str = captcha_gen.generate_captcha()
    session['captcha']      = code
    session['captcha_time'] = time.time()
    return jsonify({'image': img_str})


@auth_bp.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('user_type', None)
    return redirect(url_for('auth.login_page'))


# 注册

def _nickname_exists(nick):
    """检查昵称在所有用户表中是否已存在"""
    for sql in [
        "SELECT 1 FROM users   WHERE user_nickname   = %s LIMIT 1",
        "SELECT 1 FROM experts WHERE export_nickname = %s LIMIT 1",
        "SELECT 1 FROM admin   WHERE admin_nickname  = %s LIMIT 1",
    ]:
        if db.fetch_one(sql, (nick,)):
            return True
    return False


def _gen_nickname(username):
    """自动生成不重复的昵称：用户名前缀 + 4位随机数"""
    prefix = username[:6] if len(username) > 6 else username
    for _ in range(20):
        nick = f"{prefix}_{random.randint(1000, 9999)}"
        if not _nickname_exists(nick):
            return nick
    # 极端情况：用 uuid 短串
    import uuid
    return f"u_{uuid.uuid4().hex[:8]}"


@auth_bp.route('/register', methods=['POST'])
def register():
    username  = request.form.get('username')
    password  = request.form.get('password')
    user_type = request.form.get('userType')
    nickname  = request.form.get('nickname', '').strip()
    email     = request.form.get('email', '')
    phone     = request.form.get('phone', '')
    age       = request.form.get('age')
    gender    = request.form.get('gender', '')
    address   = request.form.get('address', '')

    def err(msg):
        return render_template('login.html', error=msg)

    if user_type not in {'普通用户', 'experts'}:
        return err('仅可注册普通用户或专家账号！')
    if not username or not password:
        return err('用户名和密码不能为空！')
    if not re.match(r'^[a-zA-Z0-9_]{3,20}$', username):
        return err('用户名必须为3-20位字母、数字或下划线！')
    if not re.match(r'^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d]{6,20}$', password):
        return err('密码必须为6-20位，且包含字母和数字！')
    if nickname and not re.match(r'^.{2,20}$', nickname):
        return err('昵称长度必须在2-20个字符之间！')
    if email and not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return err('邮箱格式不正确！')
    if phone and not re.match(r'^1[3-9]\d{9}$', phone):
        return err('手机号码格式不正确！')
    if age:
        try:
            age = int(age)
            if not (1 <= age <= 120):
                return err('年龄必须在1-120岁之间！')
        except ValueError:
            return err('年龄必须是数字！')
    if gender and gender not in ['男', '女']:
        return err('性别选择无效！')

    try:
        db.connect()
        check_map = {
            '普通用户': "SELECT * FROM users   WHERE user_name   = %s",
            'experts':  "SELECT * FROM experts WHERE export_name = %s",
        }
        if user_type not in check_map:
            return err('无效的用户类型！')
        if db.fetch_all(check_map[user_type], (username,)):
            db.close()
            return err('用户名已存在！')

        # 昵称处理：未填则自动生成，已填则检查重复
        if not nickname:
            nickname = _gen_nickname(username)
        else:
            if _nickname_exists(nickname):
                db.close()
                return err('该昵称已被使用，请换一个！')

        avatar = f"{random.randint(1, 50)}.jpg"
        result = None  # 用于检查 INSERT 是否成功

        if user_type == '普通用户':
            result = db.execute_query(
                "INSERT INTO users (user_name,user_password,user_nickname,user_email,user_phone,"
                "user_address,create_time,user_age,user_gender,login_count,user_avatar) "
                "VALUES (%s,%s,%s,%s,%s,%s,NOW(),%s,%s,0,%s)",
                (username, password,
                 nickname, email or None, phone or None, address or None,
                 age if age else None, gender or None, avatar)
            )
            msg = '普通用户注册成功！'

        elif user_type == 'experts':
            result = db.execute_query(
                "INSERT INTO experts (export_name,export_password,login_count,export_nickname,"
                "export_email,export_phone,export_age,export_gender,certification_status,"
                "approval_status,export_avatar,create_time) "
                "VALUES (%s,%s,0,%s,%s,%s,%s,%s,'未认证','待审核',%s,NOW())",
                (username, password,
                 nickname, email or None, phone or None,
                 age if age else None, gender or None, avatar)
            )
            msg = '专家注册成功，请等待管理员审核！'


        db.close()

        # 检查 INSERT 是否真正执行成功
        if result is None:
            return err('注册失败：数据写入数据库时出错，请检查服务器日志或联系管理员！')

        return render_template('login.html', success=msg)

    except Exception as e:
        return err(f'注册失败: {e}')


# 忘记密码
@auth_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    try:
        db.connect()
        data     = request.get_json()
        username = data.get('username', '').strip()
        email    = data.get('email', '').strip()

        if not username:
            return jsonify({'success': False, 'message': '请输入用户名'})
        if not email:
            return jsonify({'success': False, 'message': '请输入邮箱地址'})
        if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
            return jsonify({'success': False, 'message': '邮箱格式不正确'})

        user_type, verified_username = verify_user_credentials(username, email)
        if not user_type:
            return jsonify({'success': False, 'message': '用户名与邮箱不匹配，请核对后重试'})

        token = generate_reset_token()
        save_reset_token(verified_username, email, user_type, token)

        reset_link = url_for('auth.reset_password_page', token=token, _external=True)
        if send_reset_email(email, reset_link):
            return jsonify({'success': True, 'message': '重置链接已发送至您的邮箱'})
        return jsonify({'success': False, 'message': '邮件发送失败，请稍后重试'})

    except Exception as e:
        print(f"忘记密码错误: {e}")
        return jsonify({'success': False, 'message': '系统错误，请稍后重试'})
    finally:
        db.close()


# 重置密码
@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password_page(token):
    try:
        db.connect()
        record = db.fetch_one(
            "SELECT username,email,user_type FROM password_resets "
            "WHERE token=%s AND used=0 AND expires_at>%s",
            (token, int(time.time()))
        )
        if not record:
            return render_template('reset_password.html', error='重置链接无效或已过期', valid=False)

        username  = record['username']
        email     = record['email']
        user_type = record['user_type']

        if request.method == 'POST':
            new_pw  = request.form.get('password')
            conf_pw = request.form.get('confirm_password')
            if not new_pw or len(new_pw) < 6:
                return render_template('reset_password.html', error='密码长度至少6位', token=token, valid=True)
            if new_pw != conf_pw:
                return render_template('reset_password.html', error='两次密码不一致', token=token, valid=True)

            sql_map = {
                '普通用户': "UPDATE users   SET user_password   = %s WHERE user_name   = %s AND user_email   = %s",
                'experts':  "UPDATE experts SET export_password = %s WHERE export_name = %s AND export_email = %s",
                'admin':    "UPDATE admin   SET admin_password  = %s WHERE admin_name  = %s AND admin_email  = %s",
            }
            db.execute_query(sql_map[user_type], (new_pw, username, email))
            db.execute_query("UPDATE password_resets SET used=1 WHERE token=%s", (token,))
            return render_template('reset_password.html', success='密码重置成功，请返回登录', valid=False)

        return render_template('reset_password.html', token=token, valid=True)

    except Exception as e:
        return render_template('reset_password.html', error='系统错误', valid=False)
    finally:
        db.close()


@auth_bp.route('/expert')
def expert():
    if 'username' not in session or session.get('user_type') != 'experts':
        return redirect(url_for('auth.login_page'))
    try:
        db.connect()
        r = db.fetch_one("SELECT * FROM experts WHERE export_name=%s", (session['username'],))
        db.close()
        if r and r['approval_status'] == '通过' and r['certification_status'] == '已认证':
            return render_template('index.html')
        return redirect(url_for('auth.expert_first_login'))
    except Exception:
        return redirect(url_for('auth.login_page'))


@auth_bp.route('/user')
def user():
    if 'username' in session and session.get('user_type') not in ['admin', 'experts']:
        return render_template('index.html')
    return redirect(url_for('auth.login_page'))


@auth_bp.route('/expert/first_login')
def expert_first_login():
    if 'username' not in session or session.get('user_type') != 'experts':
        return redirect(url_for('auth.login_page'))
    try:
        db.connect()
        r = db.fetch_one(
            "SELECT e.*,a.admin_phone FROM experts e "
            "LEFT JOIN admin a ON e.admin_name=a.admin_name "
            "WHERE e.export_name=%s",
            (session['username'],)
        )
        db.close()
        if r:
            return render_template('expert_first_login.html', expert=r)
        return redirect(url_for('auth.login_page'))
    except Exception:
        return redirect(url_for('auth.login_page'))


@auth_bp.route('/check_login')
def check_login():
    if 'username' in session:
        role = session.get('user_type')
        if role not in ['普通用户', 'experts', 'admin']:
            session.clear()
            return jsonify({'login': False})
        return jsonify({'login': True, 'username': session['username'], 'role': role})
    return jsonify({'login': False})