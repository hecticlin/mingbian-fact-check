"""
routes/admin.py - 管理员 Blueprint

"""
from __future__ import annotations

import logging
import sys

from flask import (
    Blueprint, request, jsonify, render_template,
    session, redirect, url_for
)

from utils.db import db

admin_bp = Blueprint('admin', __name__)

# 允许查询的表白名单
_ALLOWED_TABLES = {'news_detection', 'news', 'users', 'experts', 'admin',
                   'comment', 'expert_reexamine', 'messages'}


def _admin_required():
    return 'username' in session and session.get('user_type') == 'admin'


# 管理员首页
@admin_bp.route('/admin')
@admin_bp.route('/admin/index')
def admin_index():
    if not _admin_required():
        return redirect(url_for('auth.login_page'))
    try:
        r     = db.fetch_one("SELECT COUNT(*) as c FROM experts WHERE approval_status='待审核'")
        count = r['c'] if r else 0
        return render_template('admin_index.html', pending_experts_count=count)
    except Exception as e:
        logging.error(f"获取待审核专家数量失败: {e}")
        return render_template('admin_index.html', pending_experts_count=0)


@admin_bp.route('/admin/pending_experts_count')
def pending_experts_count():
    if not _admin_required():
        return jsonify({'success': False, 'message': '未授权访问'}), 401
    try:
        r     = db.fetch_one("SELECT COUNT(*) as c FROM experts WHERE approval_status='待审核'")
        count = r['c'] if r else 0
        return jsonify({'success': True, 'count': count})
    except Exception as e:
        logging.error(f"获取待审核专家数量失败: {e}")
        return jsonify({'success': False, 'message': '获取失败'}), 500


# 专家管理
@admin_bp.route('/admin/experts')
def admin_experts():
    if not _admin_required():
        return redirect(url_for('auth.login_page'))
    try:
        pending    = db.fetch_all("SELECT * FROM experts WHERE approval_status='待审核'")
        certified  = db.fetch_all(
            "SELECT * FROM experts WHERE certification_status='已认证' AND admin_name=%s",
            (session.get('username'),)
        )
        return render_template('admin_experts.html',
                               pending_experts=pending, certified_experts=certified)
    except Exception:
        return render_template('admin_experts.html',
                               pending_experts=[], certified_experts=[])


@admin_bp.route('/admin/experts/<int:expertId>/approve', methods=['POST'])
def approve_expert(expertId):
    if not _admin_required():
        return jsonify({'success': False, 'message': '未授权'}), 401
    data            = request.get_json()
    approval_status = data.get('approval_status')
    if approval_status not in ['通过', '未通过']:
        return jsonify({'success': False, 'message': '无效的审批状态'})
    try:
        db.execute_query(
            "UPDATE experts SET approval_status=%s,certification_status='已认证',admin_name=%s "
            "WHERE export_id=%s",
            (approval_status, session.get('username'), expertId)
        )
        return jsonify({'success': True, 'message': f'已更新为：{approval_status}'})
    except Exception:
        return jsonify({'success': False, 'message': '更新失败'})


# 数据查询
@admin_bp.route('/admin/query')
def admin_query():
    if not _admin_required():
        return redirect(url_for('auth.login_page'))
    try:
        queries = db.fetch_all("SELECT * FROM news_detection ORDER BY check_time DESC")
        return render_template('admin_query.html', queries=queries)
    except Exception as e:
        logging.error(f"获取查询记录失败: {e}")
        return redirect(url_for('admin.admin_index'))


@admin_bp.route('/admin/query/data')
def admin_query_data():
    if not _admin_required():
        return jsonify({'error': '未授权访问'}), 401

    table     = request.args.get('table', 'news_detection')
    page      = int(request.args.get('page', 1))
    search    = request.args.get('search', '')
    page_size = 15

    # 白名单校验，防止 SQL 注入
    if table not in _ALLOWED_TABLES:
        return jsonify({'error': f'不允许查询表 {table}'}), 403

    try:
        if not db.check_table_exists(table):
            return jsonify({'error': f'表 {table} 不存在'}), 404

        columns_result = db.fetch_all(f"DESCRIBE `{table}`")
        if not columns_result:
            return jsonify({'error': f'表 {table} 为空'}), 404
        columns = [r['Field'] for r in columns_result]

        query  = f"SELECT * FROM `{table}` WHERE 1=1"
        params = []
        if search:
            conds  = " OR ".join([f"`{c}` LIKE %s" for c in columns])
            query += f" AND ({conds})"
            params = [f"%{search}%"] * len(columns)

        count_q = f"SELECT COUNT(*) as total FROM `{table}`"
        if search:
            count_q += f" WHERE ({' OR '.join([f'`{c}` LIKE %s' for c in columns])})"
        total   = (db.fetch_one(count_q, params) or {}).get('total', 0)

        query  += f" LIMIT %s OFFSET %s"
        params += [page_size, (page - 1) * page_size]
        rows    = db.fetch_all(query, params)
        return jsonify({'rows': rows, 'total': total})
    except Exception as e:
        logging.error(f"查询数据失败: {e}")
        return jsonify({'error': '查询数据失败'}), 500


@admin_bp.route('/admin/query/delete_users', methods=['POST'])
def delete_users():
    if not _admin_required():
        return jsonify({'error': '未授权访问'}), 401
    data     = request.get_json()
    user_ids = data.get('userIds', [])
    if not user_ids:
        return jsonify({'success': False, 'message': '未选择用户'})
    try:
        ph = ','.join(['%s'] * len(user_ids))
        db.execute(f"DELETE FROM users WHERE user_id IN ({ph})", user_ids)
        return jsonify({'success': True, 'message': '用户删除成功'})
    except Exception:
        return jsonify({'success': False, 'message': '删除失败'}), 500


@admin_bp.route('/admin/query/delete_experts', methods=['POST'])
def delete_experts():
    if not _admin_required():
        return jsonify({'error': '未授权访问'}), 401
    data       = request.get_json()
    expert_ids = data.get('expertIds', [])
    if not expert_ids:
        return jsonify({'success': False, 'message': '未选择专家'})
    try:
        ph = ','.join(['%s'] * len(expert_ids))
        db.execute(f"DELETE FROM experts WHERE export_id IN ({ph})", expert_ids)
        return jsonify({'success': True, 'message': '专家删除成功'})
    except Exception:
        return jsonify({'success': False, 'message': '删除失败'}), 500


@admin_bp.route('/admin/query/delete', methods=['POST'])
def delete_records():
    if not _admin_required():
        return jsonify({'success': False, 'message': '权限不足'})
    data       = request.get_json()
    table_name = data.get('table')
    ids        = data.get('ids', [])
    if not table_name or not ids:
        return jsonify({'success': False, 'message': '参数错误'})

    # 白名单校验
    if table_name not in _ALLOWED_TABLES:
        return jsonify({'success': False, 'message': f'不允许操作表 {table_name}'}), 403

    try:
        id_column = 'id' if table_name == 'news' else 'detect_id'
        ph = ','.join(['%s'] * len(ids))

        if table_name == 'news':
            db.execute_query(f"DELETE FROM comment WHERE news_id IN ({ph})", tuple(ids))

        cur = db.execute_query(f"DELETE FROM `{table_name}` WHERE `{id_column}` IN ({ph})", tuple(ids))
        if cur and cur.rowcount > 0:
            return jsonify({'success': True, 'message': f'成功删除 {cur.rowcount} 条记录'})
        return jsonify({'success': False, 'message': '没有记录被删除'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'删除失败: {e}'})


# 新闻与报告
@admin_bp.route('/admin/news_and_reports')
def admin_news_and_reports():
    if not _admin_required():
        return redirect(url_for('auth.login_page'))
    return render_template('admin_news_and_reports.html')


# 评论管理
@admin_bp.route('/admin/comments')
def admin_comments():
    if not _admin_required():
        return redirect(url_for('auth.login_page'))
    return render_template('admin_comments.html')


@admin_bp.route('/admin/comments/data')
def get_comments_data():
    if not _admin_required():
        return jsonify({'error': '未授权访问'}), 401
    try:
        page     = request.args.get('page', 1, type=int)
        search   = request.args.get('search', '')
        per_page = 15

        query  = "SELECT * FROM comment WHERE 1=1"
        params = []
        if search:
            query  += " AND (content LIKE %s OR username LIKE %s)"
            params += [f"%{search}%", f"%{search}%"]

        total_r = db.fetch_one(f"SELECT COUNT(*) as c FROM ({query}) t", params)
        total   = total_r['c'] if total_r else 0

        query  += " ORDER BY time DESC LIMIT %s OFFSET %s"
        params += [per_page, (page - 1) * per_page]
        rows    = db.fetch_all(query, params)
        return jsonify({'success': True, 'rows': rows, 'total': total,
                        'page': page, 'per_page': per_page})
    except Exception as e:
        logging.error(f"获取评论数据失败: {e}")
        return jsonify({'success': False, 'message': '获取评论数据失败'}), 500


@admin_bp.route('/admin/comments/delete', methods=['POST'])
def delete_comments():
    if not _admin_required():
        return jsonify({'error': '未授权访问'}), 401
    data        = request.get_json()
    comment_ids = data.get('commentIds', [])
    if not comment_ids:
        return jsonify({'success': False, 'message': '未选择评论'})
    try:
        ph = ','.join(['%s'] * len(comment_ids))
        db.execute(f"DELETE FROM comment WHERE id IN ({ph})", comment_ids)
        return jsonify({'success': True, 'message': '评论删除成功'})
    except Exception:
        return jsonify({'success': False, 'message': '删除失败'}), 500


# 数据报表
@admin_bp.route('/data_excel')
def data_excel():
    if 'username' not in session:
        return redirect(url_for('auth.login_page'))
    if session.get('user_type') not in ['admin', 'experts', '普通用户']:
        return redirect(url_for('auth.login_page'))
    return render_template('data_excel.html')


@admin_bp.route('/api/data_excel')
def get_data_excel():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    try:
        username = session['username']
        role     = session.get('user_type')

        base_where = "" if role == 'admin' else "WHERE user_name = %s"
        params     = () if role == 'admin' else (username,)
        and_or_where = " AND " if base_where else " WHERE "

        def _q(sql):
            return db.fetch_one(sql, params) or {}

        total      = _q(f"SELECT COUNT(*) as total FROM news_detection {base_where}").get('total', 0)
        real_count = _q(f"SELECT COUNT(*) as total FROM news_detection {base_where}"
                        f"{and_or_where}detect_result='真实新闻'").get('total', 0)
        fake_count = _q(f"SELECT COUNT(*) as total FROM news_detection {base_where}"
                        f"{and_or_where}detect_result='虚假新闻'").get('total', 0)
        avg_acc    = round(float(_q(f"SELECT AVG(detect_resultrate) as avg FROM news_detection {base_where}").get('avg') or 0), 2)

        trend_sql = (
            "SELECT DATE(check_time) as date,"
            "SUM(CASE WHEN detect_result='真实新闻' THEN 1 ELSE 0 END) as real_count,"
            "SUM(CASE WHEN detect_result='虚假新闻' THEN 1 ELSE 0 END) as fake_count,"
            "AVG(detect_resultrate) as avg_accuracy "
            f"FROM news_detection {base_where} GROUP BY DATE(check_time) ORDER BY date ASC LIMIT 30"
        )
        trend_data     = db.fetch_all(trend_sql, params) or []
        dates          = [d['date'].strftime('%Y-%m-%d') if hasattr(d.get('date'), 'strftime') else str(d.get('date', '')) for d in trend_data]
        real_counts    = [d['real_count'] or 0 for d in trend_data]
        fake_counts    = [d['fake_count'] or 0 for d in trend_data]
        accuracy_rates = [round(float(d['avg_accuracy'] or 0), 2) for d in trend_data]

        news_sql = (
            "SELECT detect_id,user_name,detect_data,check_time,detect_result,detect_resultrate,reasoning "
            f"FROM news_detection {base_where} ORDER BY check_time DESC LIMIT 100"
        )
        news_rows = db.fetch_all(news_sql, params) or []
        news_list = []
        for item in news_rows:
            parts   = (item['detect_data'] or '').split('|', 1)
            title   = parts[0].strip() if parts else '无标题'
            content = parts[1].strip() if len(parts) > 1 else '无内容'
            _r = {}
            if item.get('reasoning'):
                try:
                    _r = json.loads(item['reasoning'])
                except Exception:
                    pass
            ct = item.get('check_time')
            news_list.append({
                'detect_id':         item['detect_id'],
                'user_name':         item['user_name'] or '未知',
                'title':             title,
                'content':           content,
                'detect_time':       ct.strftime('%Y-%m-%d %H:%M:%S') if hasattr(ct, 'strftime') else str(ct or '未知'),
                'detect_result':     item['detect_result'] or '未知',
                'detect_resultrate': float(item['detect_resultrate']) if item.get('detect_resultrate') else 0.0,
                'reasoning':         _r,
            })

        return jsonify({
            'success':          True,
            'total_detections': total,
            'real_news_count':  real_count,
            'fake_news_count':  fake_count,
            'avg_accuracy':     avg_acc,
            'daily_trend': {
                'dates': dates, 'real_counts': real_counts,
                'fake_counts': fake_counts, 'accuracy_rates': accuracy_rates,
            },
            'news_list': news_list,
        })

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'message': f'获取数据失败: {e}'})


# 顶部需要 import json
import json
