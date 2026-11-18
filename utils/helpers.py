"""
utils/helpers.py - 各模块共用的工具函数

避免全局 db 连接断开后 context_processor 仍用旧连接查询。
"""
from __future__ import annotations

import os
import uuid
import random
from datetime import datetime

from flask import url_for
from werkzeug.utils import secure_filename

from utils.db import db, DB_CONFIG, _make_connection

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


# 文件工具

def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_uploaded_image(file):
    """保存新闻图片，返回 (相对路径, URL路径)"""
    filename = secure_filename(file.filename)
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'jpg'
    new_filename = f"{uuid.uuid4().hex}.{ext}"

    upload_folder = os.path.join('static', 'uploads', 'news_images')
    os.makedirs(upload_folder, exist_ok=True)

    filepath = os.path.join(upload_folder, new_filename)
    file.save(filepath)

    rel_path = os.path.join('uploads', 'news_images', new_filename).replace('\\', '/')
    url_path = url_for('static', filename=rel_path)
    return rel_path, url_path


def random_avatar() -> str:
    return f"{random.randint(1, 50)}.jpg"


# 用户信息

def _build_avatar_url(avatar_file: str, is_custom: bool) -> str:
    if is_custom:
        return url_for('static', filename=f'uploads/avatars/{avatar_file}')
    return url_for('static', filename=f'touxiang_image/{avatar_file}')


def get_user_info(username: str):
    """
    查找用户信息，复用调用者已建立的 db 连接。
    返回 {nickname, avatar_url} 或 None。
    """
    queries = [
        "SELECT user_nickname as nickname, user_avatar as avatar, is_custom_avatar FROM users WHERE user_name = %s",
        "SELECT export_nickname as nickname, export_avatar as avatar, is_custom_avatar FROM experts WHERE export_name = %s",
        "SELECT admin_nickname as nickname, admin_avatar as avatar, is_custom_avatar FROM admin WHERE admin_name = %s",
    ]
    for sql in queries:
        result = db.fetch_one(sql, (username,))
        if result:
            return {
                'nickname':   result['nickname'],
                'avatar_url': _build_avatar_url(
                    result['avatar'] or '1.jpg',
                    bool(result['is_custom_avatar'])
                ),
            }
    return None


def get_user_info_helper(username: str):
    """
    context_processor 专用版：每次独立新建连接，
    避免全局 db 连接断开后模板渲染失败。
    """
    conn = None
    try:
        conn = _make_connection()
        queries = [
            ("SELECT user_nickname as nickname, user_avatar as avatar, is_custom_avatar FROM users WHERE user_name = %s", username),
            ("SELECT export_nickname as nickname, export_avatar as avatar, is_custom_avatar FROM experts WHERE export_name = %s", username),
            ("SELECT admin_nickname as nickname, admin_avatar as avatar, is_custom_avatar FROM admin WHERE admin_name = %s", username),
        ]
        for sql, param in queries:
            with conn.cursor() as cur:
                cur.execute(sql, (param,))
                result = cur.fetchone()
            if result:
                return {
                    'nickname':   result['nickname'],
                    'avatar_url': _build_avatar_url(
                        result['avatar'] or '1.jpg',
                        bool(result['is_custom_avatar'])
                    ),
                }
        return None
    except Exception as e:
        print(f"获取用户信息失败: {e}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def get_user_avatar_url(username: str, user_type: str = None) -> str:
    """根据用户名+类型获取头像 URL"""
    default = url_for('static', filename='touxiang_image/1.jpg')
    try:
        db.connect()
        sql_map = {
            'experts': "SELECT export_avatar as avatar, is_custom_avatar FROM experts WHERE export_name = %s",
            'admin':   "SELECT admin_avatar  as avatar, is_custom_avatar FROM admin   WHERE admin_name  = %s",
        }
        sql = sql_map.get(user_type,
              "SELECT user_avatar as avatar, is_custom_avatar FROM users WHERE user_name = %s")
        result = db.fetch_one(sql, (username,))
        db.close()
        if result:
            return _build_avatar_url(result['avatar'] or '1.jpg', bool(result['is_custom_avatar']))
    except Exception:
        pass
    return default


def check_user_exists(username: str) -> bool:
    """检查用户名在任意用户表中是否存在（复用当前 db 连接）"""
    for sql in [
        "SELECT user_name   FROM users   WHERE user_name   = %s",
        "SELECT export_name FROM experts WHERE export_name = %s",
        "SELECT admin_name  FROM admin   WHERE admin_name  = %s",
    ]:
        if db.fetch_one(sql, (username,)):
            return True
    return False


# 评论树

def get_comment_depth(comment: dict, comment_dict: dict, depth: int = 0) -> int:
    if depth > 10:
        return depth
    if comment['pid'] == 0:
        return depth
    parent = comment_dict.get(comment['pid'])
    if parent:
        return get_comment_depth(parent, comment_dict, depth + 1)
    return depth


def build_comment_tree(comments: list, max_depth: int = 10) -> list:
    comment_dict = {}
    root_comments = []

    for c in comments:
        c['replies'] = []
        comment_dict[c['id']] = c

    for c in comments:
        if c['pid'] == 0:
            root_comments.append(c)
        else:
            parent = comment_dict.get(c['pid'])
            if parent and get_comment_depth(parent, comment_dict) < max_depth:
                parent['replies'].append(c)
            else:
                root_comments.append(c)

    for c in comment_dict.values():
        if c['replies']:
            c['replies'].sort(key=lambda x: x['time'], reverse=True)

    return root_comments