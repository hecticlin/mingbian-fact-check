"""
routes/news.py - 新闻资讯 Blueprint

"""
from __future__ import annotations

from datetime import datetime

from flask import (
    Blueprint, request, jsonify, render_template,
    session, redirect, url_for
)

from utils.db      import db
from utils.helpers import build_comment_tree, check_user_exists

news_bp = Blueprint('news', __name__)


def _avatar_url(avatar_file, is_custom):
    if is_custom:
        return url_for('static', filename=f'uploads/avatars/{avatar_file}')
    return url_for('static', filename=f'touxiang_image/{avatar_file}')


def _safe_strftime(dt, fmt='%Y-%m-%d %H:%M:%S'):
    """安全的日期格式化，None 时返回空字符串"""
    if dt and hasattr(dt, 'strftime'):
        return dt.strftime(fmt)
    return str(dt or '')


# 新闻列表
@news_bp.route('/news')
def news():
    if 'username' not in session:
        return redirect(url_for('auth.login_page'))

    try:
        page         = request.args.get('page', 1, type=int)
        per_page     = 5
        show_my_news = request.args.get('my_news') == '1'
        news_type    = request.args.get('type', 'all')
        sort_by      = request.args.get('sort', 'time')

        # 各类型数量（使用参数化查询构建条件）
        def _count(type_filter=None):
            conditions, params = [], []
            if type_filter:
                conditions.append("type=%s")
                params.append(type_filter)
            if show_my_news:
                conditions.append("username=%s")
                params.append(session['username'])
            where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
            r = db.fetch_one(f"SELECT COUNT(*) as total FROM news{where}", tuple(params))
            return r['total'] if r else 0

        total_all  = _count()
        total_real = _count('real')
        total_fake = _count('fake')

        # 构建 WHERE 条件
        conditions, params = [], []
        if show_my_news:
            conditions.append("n.username = %s")
            params.append(session['username'])
        if news_type != 'all':
            conditions.append("n.type = %s")
            params.append(news_type)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        total_news  = (db.fetch_one(f"SELECT COUNT(*) as total FROM news n{where}", tuple(params)) or {}).get('total', 0)
        total_pages = (total_news + per_page - 1) // per_page or 1
        page        = max(1, min(page, total_pages))

        order_map = {
            'likes':     "like_count DESC, n.time DESC",
            'favorites': "favorite_count DESC, n.time DESC",
            'comments':  "comment_count DESC, n.time DESC",
        }
        order_by = order_map.get(sort_by, "n.time DESC")

        base_sql = """
            SELECT n.*,
                   (SELECT COUNT(*) FROM news_likes     WHERE news_id=n.id) as like_count,
                   (SELECT COUNT(*) FROM news_favorites WHERE news_id=n.id) as favorite_count,
                   (SELECT COUNT(*) FROM comment        WHERE news_id=n.id) as comment_count,
                   CASE WHEN u.user_name   IS NOT NULL THEN u.user_nickname
                        WHEN e.export_name IS NOT NULL THEN e.export_nickname
                        ELSE a.admin_nickname END as nickname,
                   CASE WHEN u.user_name   IS NOT NULL THEN u.user_avatar
                        WHEN e.export_name IS NOT NULL THEN e.export_avatar
                        ELSE a.admin_avatar END as avatar_file,
                   CASE WHEN u.user_name   IS NOT NULL THEN u.is_custom_avatar
                        WHEN e.export_name IS NOT NULL THEN e.is_custom_avatar
                        ELSE a.is_custom_avatar END as is_custom_avatar,
                   (SELECT COUNT(*) FROM news_likes     WHERE news_id=n.id AND username=%s)>0 as user_liked,
                   (SELECT COUNT(*) FROM news_favorites WHERE news_id=n.id AND username=%s)>0 as user_favorited
            FROM news n
            LEFT JOIN users   u ON n.username=u.user_name
            LEFT JOIN experts e ON n.username=e.export_name
            LEFT JOIN admin   a ON n.username=a.admin_name
        """
        offset    = (page - 1) * per_page
        news_sql  = f"{base_sql}{where} ORDER BY {order_by} LIMIT %s OFFSET %s"
        q_params  = [session['username'], session['username']] + params + [per_page, offset]
        news_list = db.fetch_all(news_sql, tuple(q_params)) if total_news > 0 else []

        comment_sql = """
            SELECT c.*,
                   (SELECT COUNT(*) FROM comment_likes WHERE comment_id=c.id) as like_count,
                   (SELECT COUNT(*) FROM comment_likes WHERE comment_id=c.id AND username=%s)>0 as user_liked,
                   CASE WHEN u.user_name   IS NOT NULL THEN u.user_nickname
                        WHEN e.export_name IS NOT NULL THEN e.export_nickname
                        ELSE a.admin_nickname END as nickname,
                   CASE WHEN u.user_name   IS NOT NULL THEN u.user_avatar
                        WHEN e.export_name IS NOT NULL THEN e.export_avatar
                        ELSE a.admin_avatar END as avatar_file,
                   CASE WHEN u.user_name   IS NOT NULL THEN u.is_custom_avatar
                        WHEN e.export_name IS NOT NULL THEN e.is_custom_avatar
                        ELSE a.is_custom_avatar END as is_custom_avatar
            FROM comment c
            LEFT JOIN users   u ON c.username=u.user_name
            LEFT JOIN experts e ON c.username=e.export_name
            LEFT JOIN admin   a ON c.username=a.admin_name
            WHERE c.news_id=%s ORDER BY c.time DESC
        """

        for item in news_list:
            item['author_avatar_url'] = _avatar_url(item.get('avatar_file', '1.jpg'),
                                                     item.get('is_custom_avatar', False))
            comments = db.fetch_all(comment_sql, (session['username'], item['id']))
            for c in comments:
                c['author_avatar_url'] = _avatar_url(c.get('avatar_file', '1.jpg'),
                                                      c.get('is_custom_avatar', False))
            item['comments']  = build_comment_tree(comments)
            item['is_author'] = (item['username'] == session['username'])

        return render_template('news.html',
                               news_list=news_list,
                               is_reviewer=(session['user_type'] == 'experts'),
                               is_admin=(session['user_type'] == 'admin'),
                               current_page=page, total_pages=total_pages,
                               total_news=total_news, show_my_news=show_my_news,
                               current_type=news_type, current_sort=sort_by,
                               total_all=total_all, total_real=total_real, total_fake=total_fake)
    except Exception as e:
        import traceback; traceback.print_exc()
        return redirect(url_for('auth.login_page'))


# 发布新闻
@news_bp.route('/publish_news', methods=['POST'])
def publish_news():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    data       = request.get_json()
    title      = data.get('title')
    content    = data.get('content')
    news_type  = data.get('type', 'real')
    if not title or not content:
        return jsonify({'success': False, 'message': '标题和内容不能为空'})
    try:
        cur = db.execute_query(
            "INSERT INTO news (username,title,content,time,type) VALUES (%s,%s,%s,%s,%s)",
            (session['username'], title, content,
             datetime.now().strftime('%Y-%m-%d %H:%M:%S'), news_type)
        )
        if cur and cur.rowcount > 0:
            return jsonify({'success': True, 'message': '新闻发布成功'})
        return jsonify({'success': False, 'message': '新闻发布失败'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'发布失败: {e}'})


# 删除新闻
@news_bp.route('/delete_news', methods=['POST'])
def delete_news():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    data    = request.get_json()
    news_id = data.get('news_id')
    try:
        if session['user_type'] != 'admin':
            r = db.fetch_one("SELECT username FROM news WHERE id=%s", (news_id,))
            if not r or r['username'] != session['username']:
                return jsonify({'success': False, 'message': '无权删除此新闻'})
        db.execute_query("DELETE FROM comment WHERE news_id=%s", (news_id,))
        db.execute_query("DELETE FROM news WHERE id=%s", (news_id,))
        return jsonify({'success': True, 'message': '新闻删除成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': '删除失败'})


# 获取评论
@news_bp.route('/get_comments', methods=['POST'])
def get_comments():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    data    = request.get_json()
    news_id = data.get('news_id')
    if not news_id:
        return jsonify({'success': False, 'message': '参数错误'})
    try:
        sql = """
            SELECT c.*,
                   (SELECT COUNT(*) FROM comment_likes WHERE comment_id=c.id) as like_count,
                   (SELECT COUNT(*) FROM comment_likes WHERE comment_id=c.id AND username=%s)>0 as user_liked,
                   CASE WHEN u.user_name   IS NOT NULL THEN u.user_nickname
                        WHEN e.export_name IS NOT NULL THEN e.export_nickname
                        ELSE a.admin_nickname END as nickname,
                   CASE WHEN u.user_name   IS NOT NULL THEN u.user_avatar
                        WHEN e.export_name IS NOT NULL THEN e.export_avatar
                        ELSE a.admin_avatar END as avatar_file,
                   CASE WHEN u.user_name   IS NOT NULL THEN u.is_custom_avatar
                        WHEN e.export_name IS NOT NULL THEN e.is_custom_avatar
                        ELSE a.is_custom_avatar END as is_custom_avatar
            FROM comment c
            LEFT JOIN users   u ON c.username=u.user_name
            LEFT JOIN experts e ON c.username=e.export_name
            LEFT JOIN admin   a ON c.username=a.admin_name
            WHERE c.news_id=%s ORDER BY c.time DESC
        """
        comments = db.fetch_all(sql, (session['username'], news_id))
        for c in comments:
            c['author_avatar_url'] = _avatar_url(c.get('avatar_file', '1.jpg'),
                                                  c.get('is_custom_avatar', False))
            c['time'] = _safe_strftime(c.get('time'))
        return jsonify({'success': True, 'comments': build_comment_tree(comments)})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)})


# 发表评论
@news_bp.route('/add_comment', methods=['POST'])
def add_comment():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    data    = request.get_json()
    news_id = data.get('news_id')
    content = data.get('content')
    pid     = data.get('pid', 0)
    if not news_id or not content:
        return jsonify({'success': False, 'message': '新闻ID和评论内容不能为空'})
    try:
        if not check_user_exists(session['username']):
            return jsonify({'success': False, 'message': '用户不存在'})
        if not db.fetch_one("SELECT id FROM news WHERE id=%s", (news_id,)):
            return jsonify({'success': False, 'message': '新闻不存在'})
        if pid != 0 and not db.fetch_one("SELECT id FROM comment WHERE id=%s AND news_id=%s", (pid, news_id)):
            return jsonify({'success': False, 'message': '父级评论不存在'})

        cur = db.execute_query(
            "INSERT INTO comment (username,news_id,content,pid,time) VALUES (%s,%s,%s,%s,%s)",
            (session['username'], news_id, content, pid,
             datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        if not (cur and cur.rowcount > 0):
            return jsonify({'success': False, 'message': '评论添加失败'})

        comment = db.fetch_one("""
            SELECT c.*,
                   CASE WHEN u.user_name   IS NOT NULL THEN u.user_nickname
                        WHEN e.export_name IS NOT NULL THEN e.export_nickname
                        ELSE COALESCE(a.admin_nickname, c.username) END AS nickname,
                   CASE WHEN u.user_name   IS NOT NULL THEN u.user_avatar
                        WHEN e.export_name IS NOT NULL THEN e.export_avatar
                        ELSE a.admin_avatar END AS avatar_file,
                   CASE WHEN u.user_name   IS NOT NULL THEN u.is_custom_avatar
                        WHEN e.export_name IS NOT NULL THEN e.is_custom_avatar
                        ELSE a.is_custom_avatar END AS is_custom_avatar
            FROM comment c
            LEFT JOIN users   u ON c.username=u.user_name
            LEFT JOIN experts e ON c.username=e.export_name
            LEFT JOIN admin   a ON c.username=a.admin_name
            WHERE c.id=%s
        """, (cur.lastrowid,))
        comment['replies'] = []
        comment['author_avatar_url'] = _avatar_url(
            comment.get('avatar_file', '1.jpg'),
            comment.get('is_custom_avatar', False)
        )
        html = render_template('comment.html', comment=comment)
        return jsonify({'success': True, 'message': '评论添加成功',
                        'html': html, 'comment_id': cur.lastrowid})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'message': f'评论失败: {e}'})


# 删除评论
@news_bp.route('/delete_comment', methods=['POST'])
def delete_comment():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    data       = request.get_json()
    comment_id = data.get('comment_id')
    try:
        r = db.fetch_one("SELECT username FROM comment WHERE id=%s", (comment_id,))
        if not r:
            return jsonify({'success': False, 'message': '评论不存在'})
        if session['user_type'] not in ('admin',) and r['username'] != session['username']:
            return jsonify({'success': False, 'message': '无权删除此评论'})
        db.execute_query("DELETE FROM comment WHERE id=%s", (comment_id,))
        return jsonify({'success': True, 'message': '评论删除成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': '删除失败'})


# 新闻点赞
@news_bp.route('/like_news', methods=['POST'])
def like_news():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    data     = request.get_json()
    news_id  = data.get('news_id')
    action   = data.get('action')
    username = session['username']
    try:
        if action == 'like':
            if not db.fetch_one("SELECT id FROM news_likes WHERE news_id=%s AND username=%s", (news_id, username)):
                db.execute_query("INSERT INTO news_likes (news_id,username,created_at) VALUES (%s,%s,%s)",
                                 (news_id, username, datetime.now()))
        else:
            db.execute_query("DELETE FROM news_likes WHERE news_id=%s AND username=%s", (news_id, username))
        count = (db.fetch_one("SELECT COUNT(*) as c FROM news_likes WHERE news_id=%s", (news_id,)) or {}).get('c', 0)
        return jsonify({'success': True, 'like_count': count})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# 新闻收藏
@news_bp.route('/favorite_news', methods=['POST'])
def favorite_news():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    data     = request.get_json()
    news_id  = data.get('news_id')
    action   = data.get('action')
    username = session['username']
    try:
        if action == 'favorite':
            if not db.fetch_one("SELECT id FROM news_favorites WHERE news_id=%s AND username=%s", (news_id, username)):
                db.execute_query("INSERT INTO news_favorites (news_id,username,created_at) VALUES (%s,%s,%s)",
                                 (news_id, username, datetime.now()))
        else:
            db.execute_query("DELETE FROM news_favorites WHERE news_id=%s AND username=%s", (news_id, username))
        count = (db.fetch_one("SELECT COUNT(*) as c FROM news_favorites WHERE news_id=%s", (news_id,)) or {}).get('c', 0)
        return jsonify({'success': True, 'favorite_count': count})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# 评论点赞
@news_bp.route('/like_comment', methods=['POST'])
def like_comment():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    data       = request.get_json()
    comment_id = data.get('comment_id')
    action     = data.get('action')
    username   = session['username']
    try:
        if action == 'like':
            if not db.fetch_one("SELECT id FROM comment_likes WHERE comment_id=%s AND username=%s", (comment_id, username)):
                db.execute_query("INSERT INTO comment_likes (comment_id,username,created_at) VALUES (%s,%s,%s)",
                                 (comment_id, username, datetime.now()))
        else:
            db.execute_query("DELETE FROM comment_likes WHERE comment_id=%s AND username=%s", (comment_id, username))
        count = (db.fetch_one("SELECT COUNT(*) as c FROM comment_likes WHERE comment_id=%s", (comment_id,)) or {}).get('c', 0)
        return jsonify({'success': True, 'like_count': count})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# 批量查询新闻点赞/收藏状态
@news_bp.route('/get_news_status', methods=['POST'])
def get_news_status():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    data     = request.get_json()
    news_ids = data.get('news_ids', [])
    if not news_ids:
        return jsonify({'success': True, 'data': {}})
    try:
        username = session['username']
        ph       = ','.join(['%s'] * len(news_ids))
        liked    = {r['news_id'] for r in db.fetch_all(
            f"SELECT news_id FROM news_likes WHERE news_id IN ({ph}) AND username=%s",
            tuple(news_ids) + (username,))}
        faved    = {r['news_id'] for r in db.fetch_all(
            f"SELECT news_id FROM news_favorites WHERE news_id IN ({ph}) AND username=%s",
            tuple(news_ids) + (username,))}
        lc = {r['news_id']: r['count'] for r in db.fetch_all(
            f"SELECT news_id,COUNT(*) as count FROM news_likes WHERE news_id IN ({ph}) GROUP BY news_id",
            tuple(news_ids))}
        fc = {r['news_id']: r['count'] for r in db.fetch_all(
            f"SELECT news_id,COUNT(*) as count FROM news_favorites WHERE news_id IN ({ph}) GROUP BY news_id",
            tuple(news_ids))}
        return jsonify({'success': True, 'data': {
            str(nid): {'liked': nid in liked, 'favorited': nid in faved,
                       'like_count': lc.get(nid, 0), 'favorite_count': fc.get(nid, 0)}
            for nid in news_ids
        }})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)})


# 批量查询评论点赞状态
@news_bp.route('/get_comments_status', methods=['POST'])
def get_comments_status():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    data        = request.get_json()
    comment_ids = data.get('comment_ids', [])
    if not comment_ids:
        return jsonify({'success': True, 'data': {}})
    try:
        username = session['username']
        ph       = ','.join(['%s'] * len(comment_ids))
        liked    = {r['comment_id'] for r in db.fetch_all(
            f"SELECT comment_id FROM comment_likes WHERE comment_id IN ({ph}) AND username=%s",
            tuple(comment_ids) + (username,))}
        lc = {r['comment_id']: r['count'] for r in db.fetch_all(
            f"SELECT comment_id,COUNT(*) as count FROM comment_likes WHERE comment_id IN ({ph}) GROUP BY comment_id",
            tuple(comment_ids))}
        return jsonify({'success': True, 'data': {
            str(cid): {'liked': cid in liked, 'like_count': lc.get(cid, 0)}
            for cid in comment_ids
        }})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# 我的收藏
@news_bp.route('/my_favorites')
def my_favorites():
    if 'username' not in session:
        return redirect(url_for('auth.login_page'))
    try:
        username = session['username']
        sql = """
            SELECT n.*,
                   CASE WHEN u.user_name   IS NOT NULL THEN u.user_nickname
                        WHEN e.export_name IS NOT NULL THEN e.export_nickname
                        ELSE a.admin_nickname END as nickname,
                   CASE WHEN u.user_name   IS NOT NULL THEN u.user_avatar
                        WHEN e.export_name IS NOT NULL THEN e.export_avatar
                        ELSE a.admin_avatar END as avatar_file,
                   CASE WHEN u.user_name   IS NOT NULL THEN u.is_custom_avatar
                        WHEN e.export_name IS NOT NULL THEN e.is_custom_avatar
                        ELSE a.is_custom_avatar END as is_custom_avatar,
                   (SELECT COUNT(*) FROM news_likes     WHERE news_id=n.id) as like_count,
                   (SELECT COUNT(*) FROM news_favorites WHERE news_id=n.id) as favorite_count
            FROM news n
            LEFT JOIN users   u ON n.username=u.user_name
            LEFT JOIN experts e ON n.username=e.export_name
            LEFT JOIN admin   a ON n.username=a.admin_name
            WHERE n.id IN (SELECT news_id FROM news_favorites WHERE username=%s)
            ORDER BY n.time DESC
        """
        news_list = db.fetch_all(sql, (username,))
        for item in news_list:
            item['author_avatar_url'] = _avatar_url(item.get('avatar_file', '1.jpg'),
                                                     item.get('is_custom_avatar', False))
            r = db.fetch_one("SELECT id FROM news_likes WHERE news_id=%s AND username=%s",
                             (item['id'], username))
            item['user_liked'] = r is not None
        return render_template('my_favorites.html', news_list=news_list)
    except Exception as e:
        return redirect(url_for('news.news'))


# 介绍页
@news_bp.route('/introduce')
def introduce():
    if 'username' not in session:
        return redirect(url_for('auth.login_page'))
    return render_template('introduce.html')
