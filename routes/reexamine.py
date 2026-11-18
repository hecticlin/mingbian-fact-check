"""
routes/reexamine.py - 专家复审 Blueprint

"""
from __future__ import annotations

from datetime import datetime

from flask import (
    Blueprint, request, jsonify, render_template,
    session, redirect, url_for
)

from utils.db import db

reexamine_bp = Blueprint('reexamine', __name__)


def _parse_reasoning(raw_reasoning):
    if not raw_reasoning:
        return {}
    try:
        import json
        r = json.loads(raw_reasoning)
        return r if isinstance(r, dict) else {}
    except Exception:
        return {}


def _clean_image_urls(image_path_str):
    if not image_path_str:
        return []
    urls = []
    for p in image_path_str.split(','):
        p = p.strip()
        if not p:
            continue
        if p.startswith('/static/'):
            p = p[8:]
        elif p.startswith('static/'):
            p = p[7:]
        urls.append(url_for('static', filename=p))
    return urls


def _safe_strftime(dt, fmt='%Y-%m-%d %H:%M:%S'):
    if dt and hasattr(dt, 'strftime'):
        return dt.strftime(fmt)
    return str(dt or '')


# 复审入口
@reexamine_bp.route('/reexamine')
def reexamine():
    if 'username' not in session:
        return redirect(url_for('auth.login_page'))
    if session['user_type'] == 'experts':
        return redirect(url_for('reexamine.expert_reexamine'))
    return redirect(url_for('reexamine.user_reexamine'))


@reexamine_bp.route('/user_reexamine')
def user_reexamine():
    return render_template('user_reexamine.html')


@reexamine_bp.route('/expert_reexamine')
def expert_reexamine():
    return render_template('expert_reexamine.html')


# 用户提交复审申请
@reexamine_bp.route('/user/submit_reexamine', methods=['POST'])
def submit_reexamine():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})

    data       = request.get_json()
    detect_ids = data.get('detect_ids', [])
    if not detect_ids:
        return jsonify({'success': False, 'message': '请选择要复审的新闻'})

    try:
        check = db.fetch_one(
            "SELECT COUNT(*) as count FROM news_detection "
            "WHERE detect_id IN %s AND user_name=%s "
            "AND detect_id NOT IN (SELECT news_id FROM expert_reexamine)",
            (tuple(detect_ids), session['username'])
        )
        if not check or check['count'] != len(detect_ids):
            return jsonify({'success': False, 'message': '部分新闻不存在或已被复审'})

        expert = db.fetch_one(
            "SELECT export_name FROM experts "
            "WHERE certification_status='已认证' AND approval_status='通过' "
            "ORDER BY RAND() LIMIT 1"
        )
        if not expert:
            return jsonify({'success': False, 'message': '没有可用的认证专家'})
        export_name = expert['export_name']

        for did in detect_ids:
            news_data = db.fetch_one(
                "SELECT user_name,detect_data FROM news_detection WHERE detect_id=%s", (did,)
            )
            if news_data:
                db.execute_query(
                    "INSERT INTO expert_reexamine "
                    "(user_name,export_name,reexamine_data,news_id,summit_time) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (news_data['user_name'], export_name, news_data['detect_data'],
                     did, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                )

        return jsonify({'success': True, 'message': '提交复审成功'})

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'message': '提交复审失败'})


# 用户查看待复审列表
@reexamine_bp.route('/user/get_pending_reexamine', methods=['GET'])
def get_pending_reexamine():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    try:
        rows = db.fetch_all(
            "SELECT r.reexamine_id,r.reexamine_data,r.export_name,r.summit_time,"
            "n.image_path,n.reasoning FROM expert_reexamine r "
            "LEFT JOIN news_detection n ON r.news_id=n.detect_id "
            "WHERE r.user_name=%s AND r.reexamine_detect_code='未检测' "
            "ORDER BY r.summit_time DESC",
            (session['username'],)
        )
        news = []
        for item in rows:
            parts   = item['reexamine_data'].split('|')
            title   = parts[0] if parts else '无标题'
            content = parts[1] if len(parts) > 1 else '无内容'
            urls    = _clean_image_urls(item.get('image_path'))
            news.append({
                'reexamine_id': item['reexamine_id'],
                'title': title, 'content': content,
                'export_name':  item['export_name'],
                'summit_time':  _safe_strftime(item.get('summit_time')),
                'image_urls':   urls, 'has_image': bool(urls),
                'reasoning':    _parse_reasoning(item.get('reasoning')),
            })
        return jsonify({'success': True, 'news': news})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'message': '获取失败'})


# 用户删除复审申请
@reexamine_bp.route('/user/delete_reexamine', methods=['POST'])
def delete_reexamine():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    data          = request.get_json()
    reexamine_ids = data.get('reexamine_ids', [])
    if not reexamine_ids:
        return jsonify({'success': False, 'message': '请选择要删除的复审请求'})
    try:
        check = db.fetch_one(
            "SELECT COUNT(*) as count FROM expert_reexamine "
            "WHERE reexamine_id IN %s AND user_name=%s AND reexamine_detect_code='未检测'",
            (tuple(reexamine_ids), session['username'])
        )
        if not check or check['count'] != len(reexamine_ids):
            return jsonify({'success': False, 'message': '部分复审请求不存在或已被处理'})
        db.execute_query("DELETE FROM expert_reexamine WHERE reexamine_id IN %s",
                         (tuple(reexamine_ids),))
        return jsonify({'success': True, 'message': '删除复审请求成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': '删除失败'})


# 用户查看已复审结果
@reexamine_bp.route('/user/get_reviewed_reexamine', methods=['GET'])
def get_reviewed_reexamine():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    try:
        rows = db.fetch_all(
            "SELECT r.reexamine_id,r.reexamine_time,r.reexamine_data,"
            "r.reexamine_result,r.reexamine_reason,"
            "e.export_name as expert_name,u.user_name,n.image_path,n.reasoning "
            "FROM expert_reexamine r "
            "JOIN users u ON r.user_name=u.user_name "
            "LEFT JOIN experts e ON r.export_name=e.export_name "
            "LEFT JOIN news_detection n ON r.news_id=n.detect_id "
            "WHERE r.user_name=%s AND r.reexamine_detect_code='已检测' "
            "ORDER BY r.reexamine_time DESC",
            (session['username'],)
        )
        news = []
        for item in rows:
            parts   = item['reexamine_data'].split('|')
            title   = parts[0] if parts else '无标题'
            content = parts[1] if len(parts) > 1 else '无内容'
            urls    = _clean_image_urls(item.get('image_path'))
            news.append({
                'reexamine_id':     item['reexamine_id'],
                'reexamine_time':   _safe_strftime(item.get('reexamine_time')),
                'reexamine_result': item['reexamine_result'],
                'reexamine_reason': item['reexamine_reason'],
                'expert_name':      item['expert_name'],
                'user_name':        item['user_name'],
                'title': title, 'content': content,
                'image_urls': urls, 'has_image': bool(urls),
                'reasoning':  _parse_reasoning(item.get('reasoning')),
            })
        return jsonify({'success': True, 'news': news})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'message': '获取失败'})


# 专家获取待复审列表
@reexamine_bp.route('/expert/get_pending_reexamine', methods=['GET'])
def expert_get_pending_reexamine():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    try:
        if not db.fetch_one(
            "SELECT export_name FROM experts WHERE export_name=%s AND certification_status='已认证'",
            (session['username'],)
        ):
            return jsonify({'success': False, 'message': '您不是认证专家'})

        # 修复：只查询分配给当前专家的待复审任务
        rows = db.fetch_all(
            "SELECT r.reexamine_id,r.user_name,r.reexamine_data,r.summit_time,"
            "n.detect_result,n.detect_resultrate,n.image_path,n.reasoning "
            "FROM expert_reexamine r "
            "JOIN news_detection n ON r.news_id=n.detect_id "
            "WHERE r.reexamine_detect_code='未检测' AND r.export_name=%s "
            "ORDER BY r.summit_time ASC",
            (session['username'],)
        )
        news = []
        for item in rows:
            parts   = item['reexamine_data'].split('|')
            title   = parts[0] if parts else '无标题'
            content = parts[1] if len(parts) > 1 else '无内容'
            urls    = _clean_image_urls(item.get('image_path'))
            news.append({
                'reexamine_id':      item['reexamine_id'],
                'user_name':         item['user_name'],
                'title': title, 'content': content,
                'summit_time':       _safe_strftime(item.get('summit_time')),
                'detect_result':     item['detect_result'],
                'detect_resultrate': float(item['detect_resultrate'] or 0),
                'image_urls': urls, 'has_image': bool(urls),
                'reasoning':  _parse_reasoning(item.get('reasoning')),
            })
        return jsonify({'success': True, 'news': news})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'message': '获取失败'})


# 专家获取已复审列表
@reexamine_bp.route('/expert/get_reviewed_reexamine', methods=['GET'])
def expert_get_reviewed_reexamine():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    try:
        if not db.fetch_one(
            "SELECT export_name FROM experts WHERE export_name=%s AND certification_status='已认证'",
            (session['username'],)
        ):
            return jsonify({'success': False, 'message': '您不是认证专家'})

        rows = db.fetch_all(
            "SELECT r.reexamine_id,r.reexamine_time,r.summit_time,"
            "r.reexamine_data,r.reexamine_result,r.reexamine_reason,"
            "u.user_name,n.image_path,n.reasoning "
            "FROM expert_reexamine r "
            "JOIN users u ON r.user_name=u.user_name "
            "LEFT JOIN news_detection n ON r.news_id=n.detect_id "
            "WHERE r.reexamine_detect_code='已检测' AND r.export_name=%s "
            "ORDER BY r.reexamine_time DESC",
            (session['username'],)
        )
        news = []
        for item in rows:
            parts   = item['reexamine_data'].split('|')
            title   = parts[0] if parts else '无标题'
            content = parts[1] if len(parts) > 1 else '无内容'
            urls    = _clean_image_urls(item.get('image_path'))
            news.append({
                'reexamine_id':     item['reexamine_id'],
                'reexamine_time':   _safe_strftime(item.get('reexamine_time')),
                'summit_time':      _safe_strftime(item.get('summit_time')),
                'reexamine_result': item['reexamine_result'],
                'reexamine_reason': item['reexamine_reason'],
                'user_name':        item['user_name'],
                'title': title, 'content': content,
                'image_urls': urls, 'has_image': bool(urls),
                'reasoning':  _parse_reasoning(item.get('reasoning')),
            })
        return jsonify({'success': True, 'news': news})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'message': '获取失败'})


# 专家提交复审结果
@reexamine_bp.route('/expert/submit_reexamine', methods=['POST'])
def expert_submit_reexamine():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    data         = request.get_json()
    reexamine_id = data.get('reexamine_id')
    result       = data.get('result')
    reason       = data.get('reason')

    if not all([reexamine_id, result, reason]):
        return jsonify({'success': False, 'message': '参数不完整'})
    if result not in ['真实新闻', '虚假新闻']:
        return jsonify({'success': False, 'message': '复审结果无效'})
    if not isinstance(reexamine_id, int) or reexamine_id <= 0:
        return jsonify({'success': False, 'message': '复审ID无效'})
    if len(reason) > 1000:
        return jsonify({'success': False, 'message': '复审理由过长'})

    try:
        if not db.fetch_one(
            "SELECT export_name FROM experts WHERE export_name=%s AND certification_status='已认证'",
            (session['username'],)
        ):
            return jsonify({'success': False, 'message': '您不是认证专家'})

        # 修复：校验任务是否分配给当前专家
        record = db.fetch_one(
            "SELECT reexamine_id, export_name FROM expert_reexamine WHERE reexamine_id=%s",
            (reexamine_id,)
        )
        if not record:
            return jsonify({'success': False, 'message': '复审任务不存在'})
        if record['export_name'] != session['username']:
            return jsonify({'success': False, 'message': '该复审任务未分配给您'})

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db.execute_query(
            "UPDATE expert_reexamine SET reexamine_result=%s,reexamine_reason=%s,"
            "export_name=%s,reexamine_time=%s,reexamine_detect_code='已检测' "
            "WHERE reexamine_id=%s",
            (result, reason, session['username'], now, reexamine_id)
        )
        return jsonify({
            'success': True, 'message': '复审提交成功',
            'data': {'reexamine_id': reexamine_id, 'result': result,
                     'reason': reason, 'review_time': now,
                     'expert_name': session['username']},
        })
    except Exception as e:
        return jsonify({'success': False, 'message': '复审提交失败'})
