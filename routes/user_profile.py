"""
routes/user_profile.py - 个人信息 Blueprint

"""
from __future__ import annotations

import os
import re
import uuid
import random
from datetime import datetime

from flask import (
    Blueprint, request, jsonify, render_template,
    session, redirect, url_for
)
from werkzeug.utils import secure_filename

from utils.db import db

profile_bp = Blueprint('profile', __name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
ALLOWED_ROLES      = ['普通用户', 'experts', 'admin']


def _allowed(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _build_avatar_url(avatar, is_custom):
    if is_custom:
        return url_for('static', filename=f'uploads/avatars/{avatar}')
    return url_for('static', filename=f'touxiang_image/{avatar}')


# 个人信息
@profile_bp.route('/self_information', methods=['GET', 'POST'])
def self_information():
    if 'username' not in session or session.get('user_type') not in ALLOWED_ROLES:
        return redirect(url_for('auth.login_page'))

    username = session['username']
    role     = session['user_type']

    if request.method == 'POST':
        try:
            nickname = request.form.get('nickname', '').strip()
            email    = request.form.get('email')
            phone    = request.form.get('phone')
            address  = request.form.get('address')
            age      = request.form.get('age')
            gender   = request.form.get('gender')

            if nickname and not re.match(r'^.{2,20}$', nickname):
                return jsonify({'success': False, 'message': '昵称长度必须在2-20个字符之间！'})
            if email and not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
                return jsonify({'success': False, 'message': '邮箱格式不正确！'})
            if phone and not re.match(r'^1[3-9]\d{9}$', phone):
                return jsonify({'success': False, 'message': '手机号码格式不正确！'})
            if age:
                try:
                    age = int(age)
                    if not (1 <= age <= 120):
                        return jsonify({'success': False, 'message': '年龄必须在1-120岁之间！'})
                except ValueError:
                    return jsonify({'success': False, 'message': '年龄必须是数字！'})
            if gender and gender not in ['男', '女']:
                return jsonify({'success': False, 'message': '性别选择无效！'})

            # 昵称重复检查（排除自己当前昵称）
            if nickname:
                # 查询所有表中是否有同名昵称（排除当前用户自己）
                dup_sqls = [
                    ("SELECT user_name FROM users WHERE user_nickname=%s AND user_name!=%s LIMIT 1", (nickname, username)),
                    ("SELECT export_name FROM experts WHERE export_nickname=%s AND export_name!=%s LIMIT 1", (nickname, username)),
                    ("SELECT admin_name FROM admin WHERE admin_nickname=%s AND admin_name!=%s LIMIT 1", (nickname, username)),
                ]
                for sql, params in dup_sqls:
                    if db.fetch_one(sql, params):
                        return jsonify({'success': False, 'message': '该昵称已被其他用户使用，请换一个！'})

            if role == '普通用户':
                db.execute_query(
                    "UPDATE users SET user_nickname=%s,user_email=%s,user_phone=%s,"
                    "user_address=%s,user_age=%s,user_gender=%s WHERE user_name=%s",
                    (nickname, email, phone, address, age, gender, username)
                )
            elif role == 'experts':
                db.execute_query(
                    "UPDATE experts SET export_nickname=%s,export_email=%s,export_phone=%s,"
                    "export_age=%s,export_gender=%s WHERE export_name=%s",
                    (nickname, email, phone, age, gender, username)
                )
            else:
                db.execute_query(
                    "UPDATE admin SET admin_nickname=%s,admin_email=%s,admin_phone=%s,"
                    "admin_gender=%s WHERE admin_name=%s",
                    (nickname, email, phone, gender, username)
                )
            return jsonify({'success': True, 'message': '个人信息更新成功！'})
        except Exception as e:
            return jsonify({'success': False, 'message': f'更新失败: {e}'})

    # GET
    try:
        sql_map = {
            '普通用户': "SELECT * FROM users   WHERE user_name   = %s",
            'experts':  "SELECT * FROM experts WHERE export_name = %s",
            'admin':    "SELECT * FROM admin   WHERE admin_name  = %s",
        }
        results = db.fetch_all(sql_map[role], (username,))

        if not results:
            return redirect(url_for('auth.login_page'))

        u = results[0]
        if role == '普通用户':
            nickname, email, phone = u.get('user_nickname'), u.get('user_email'), u.get('user_phone')
            age, gender, avatar    = u.get('user_age'), u.get('user_gender'), u.get('user_avatar', '1.jpg')
            is_custom              = u.get('is_custom_avatar', False)
        elif role == 'experts':
            nickname, email, phone = u.get('export_nickname'), u.get('export_email'), u.get('export_phone')
            age, gender, avatar    = u.get('export_age'), u.get('export_gender'), u.get('export_avatar', '1.jpg')
            is_custom              = u.get('is_custom_avatar', False)
        else:
            nickname, email, phone = u.get('admin_nickname'), u.get('admin_email'), u.get('admin_phone')
            age, gender, avatar    = None, u.get('admin_gender'), u.get('admin_avatar', '1.jpg')
            is_custom              = u.get('is_custom_avatar', False)

        if 'create_time' in u and u['create_time']:
            u['create_time'] = u['create_time'].strftime('%Y-%m-%d %H:%M:%S')

        avatar_url = _build_avatar_url(avatar, is_custom)

        return render_template('self_information.html',
                               user=u, role=role,
                               nickname=nickname, email=email, phone=phone,
                               age=age, gender=gender,
                               avatar_file=avatar, avatar_url=avatar_url,
                               is_custom_avatar=is_custom)
    except Exception as e:
        print(f"获取用户信息失败: {e}")
        return redirect(url_for('auth.login_page'))


# 修改密码
@profile_bp.route('/change_password', methods=['POST'])
def change_password():
    if 'username' not in session or session.get('user_type') not in ALLOWED_ROLES:
        return jsonify({'success': False, 'message': '未登录，请先登录'})

    username   = session['username']
    role       = session['user_type']
    old_pw     = request.form.get('old_password')
    new_pw     = request.form.get('new_password')

    if not old_pw or not new_pw:
        return jsonify({'success': False, 'message': '原密码和新密码不能为空！'})
    if not re.match(r'^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d]{6,20}$', new_pw):
        return jsonify({'success': False, 'message': '新密码必须为6-20位，且包含字母和数字！'})

    try:
        field_map = {
            '普通用户': ('user_password',   'users',   'user_name'),
            'experts':  ('export_password', 'experts', 'export_name'),
            'admin':    ('admin_password',  'admin',   'admin_name'),
        }
        pw_field, table, name_field = field_map[role]
        r = db.fetch_all(f"SELECT `{pw_field}` FROM `{table}` WHERE `{name_field}`=%s", (username,))
        if not r:
            return jsonify({'success': False, 'message': '用户不存在'})
        if r[0][pw_field] != old_pw:
            return jsonify({'success': False, 'message': '原密码错误'})
        db.execute_query(f"UPDATE `{table}` SET `{pw_field}`=%s WHERE `{name_field}`=%s", (new_pw, username))
        return jsonify({'success': True, 'message': '密码修改成功！'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'修改密码失败: {e}'})


# 更新头像
@profile_bp.route('/update_avatar', methods=['POST'])
def update_avatar():
    if 'username' not in session or session.get('user_type') not in ALLOWED_ROLES:
        return jsonify({'success': False, 'message': '未登录'})

    username = session['username']
    role     = session['user_type']

    sql_map = {
        '普通用户': ("UPDATE users   SET user_avatar=%s,   is_custom_avatar=%s WHERE user_name=%s",
                     "UPDATE users   SET user_avatar=%s,   is_custom_avatar=FALSE WHERE user_name=%s"),
        'experts':  ("UPDATE experts SET export_avatar=%s, is_custom_avatar=%s WHERE export_name=%s",
                     "UPDATE experts SET export_avatar=%s, is_custom_avatar=FALSE WHERE export_name=%s"),
        'admin':    ("UPDATE admin   SET admin_avatar=%s,  is_custom_avatar=%s WHERE admin_name=%s",
                     "UPDATE admin   SET admin_avatar=%s,  is_custom_avatar=FALSE WHERE admin_name=%s"),
    }
    if role not in sql_map:
        return jsonify({'success': False, 'message': '无效的用户角色'})

    avatar_type = request.form.get('avatar_type')

    try:
        if avatar_type == 'default':
            avatar_file = request.form.get('avatar_file', '1.jpg')
            if not re.match(r'^[1-9][0-9]?\.jpg$', avatar_file):
                avatar_file = '1.jpg'
            _, default_sql = sql_map[role]
            db.execute_query(default_sql, (avatar_file, username))
            return jsonify({
                'success': True, 'message': '头像更新成功',
                'avatar_url': url_for('static', filename=f'touxiang_image/{avatar_file}'),
                'avatar_file': avatar_file,
            })

        # 自定义上传
        if 'avatar_file' not in request.files:
            return jsonify({'success': False, 'message': '没有上传文件'})
        file = request.files['avatar_file']
        if not file.filename or not _allowed(file.filename):
            return jsonify({'success': False, 'message': '不支持的文件类型'})

        ext      = file.filename.rsplit('.', 1)[1].lower()
        ts       = datetime.now().strftime('%Y%m%d%H%M%S')
        filename = f"{username}_{ts}_{uuid.uuid4().hex[:8]}.{ext}"
        folder   = os.path.join('static', 'uploads', 'avatars')
        os.makedirs(folder, exist_ok=True)
        file.save(os.path.join(folder, filename))

        custom_sql, _ = sql_map[role]
        db.execute_query(custom_sql, (filename, True, username))

        return jsonify({
            'success': True, 'message': '头像上传成功',
            'avatar_url': url_for('static', filename=f'uploads/avatars/{filename}'),
            'avatar_file': filename,
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'message': f'更新头像失败: {e}'})


# 重置默认头像
@profile_bp.route('/reset_avatar', methods=['POST'])
def reset_avatar():
    if 'username' not in session or session.get('user_type') not in ALLOWED_ROLES:
        return jsonify({'success': False, 'message': '未登录'})

    username = session['username']
    role     = session['user_type']
    avatar   = f"{random.randint(1, 50)}.jpg"

    sql_map = {
        '普通用户': "UPDATE users   SET user_avatar=%s,   is_custom_avatar=FALSE WHERE user_name=%s",
        'experts':  "UPDATE experts SET export_avatar=%s, is_custom_avatar=FALSE WHERE export_name=%s",
        'admin':    "UPDATE admin   SET admin_avatar=%s,  is_custom_avatar=FALSE WHERE admin_name=%s",
    }
    try:
        db.execute_query(sql_map[role], (avatar, username))
        return jsonify({
            'success': True, 'message': '已重置为默认头像',
            'avatar_url': url_for('static', filename=f'touxiang_image/{avatar}'),
            'avatar_file': avatar,
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'重置头像失败: {e}'})


# 获取当前用户头像 URL
@profile_bp.route('/get_user_avatar')
def get_user_avatar():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})

    default = url_for('static', filename='touxiang_image/1.jpg')
    try:
        username  = session['username']
        user_type = session['user_type']

        sql_map = {
            '普通用户': "SELECT user_avatar   as avatar, is_custom_avatar FROM users   WHERE user_name   = %s",
            'experts':  "SELECT export_avatar as avatar, is_custom_avatar FROM experts WHERE export_name = %s",
            'admin':    "SELECT admin_avatar  as avatar, is_custom_avatar FROM admin   WHERE admin_name  = %s",
        }
        r = db.fetch_one(sql_map.get(user_type, sql_map['普通用户']), (username,))
        if r and r['avatar']:
            avatar_url = _build_avatar_url(r['avatar'], r['is_custom_avatar'])
        else:
            avatar_url = default
        return jsonify({'success': True, 'avatar_url': avatar_url})
    except Exception:
        return jsonify({'success': False, 'avatar_url': default})
