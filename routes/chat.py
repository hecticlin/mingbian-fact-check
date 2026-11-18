"""
routes/chat.py - 聊天室 Blueprint

"""

from datetime import datetime

from flask import (
    Blueprint, request, jsonify, render_template,
    session, redirect, url_for
)

from utils.db      import db
from utils.helpers import get_user_info

chat_bp = Blueprint('chat', __name__)


# 聊天室主页面
@chat_bp.route('/my_messages')
def my_messages():
    if 'username' not in session:
        return redirect(url_for('auth.login_page'))
    try:
        username = session['username']

        sql = """
            SELECT DISTINCT
                CASE WHEN sender=%s THEN receiver ELSE sender END as chat_user,
                MAX(timestamp) as last_message_time
            FROM messages
            WHERE sender=%s OR receiver=%s
            GROUP BY chat_user ORDER BY last_message_time DESC
        """
        chat_users = db.fetch_all(sql, (username, username, username))

        for chat in chat_users:
            info = get_user_info(chat['chat_user'])
            if info:
                chat['nickname']   = info['nickname']
                chat['avatar_url'] = info['avatar_url']
            else:
                chat['nickname']   = chat['chat_user']
                chat['avatar_url'] = url_for('static', filename='touxiang_image/1.jpg')

            unread = db.fetch_one(
                "SELECT COUNT(*) as c FROM messages WHERE sender=%s AND receiver=%s AND is_read=0",
                (chat['chat_user'], username)
            )
            chat['unread_count'] = (unread or {}).get('c', 0)

        target_user = request.args.get('user', '')
        return render_template('chat_room.html',
                               chat_users=chat_users,
                               current_user=username,
                               target_user=target_user)
    except Exception as e:
        print(f"获取聊天列表失败: {e}")
        return redirect(url_for('news.news'))


# 获取聊天历史记录
@chat_bp.route('/get_messages')
def get_messages():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    chat_with = request.args.get('user')
    if not chat_with:
        return jsonify({'success': False, 'message': '缺少用户参数'})
    try:
        username = session['username']
        messages = db.fetch_all(
            "SELECT * FROM messages "
            "WHERE (sender=%s AND receiver=%s) OR (sender=%s AND receiver=%s) "
            "ORDER BY timestamp ASC",
            (username, chat_with, chat_with, username)
        )
        for m in messages:
            ts = m.get('timestamp')
            m['timestamp'] = ts.strftime('%Y-%m-%d %H:%M:%S') if hasattr(ts, 'strftime') else str(ts or '')
            m['is_self']   = (m['sender'] == username)

        db.execute_query(
            "UPDATE messages SET is_read=1 WHERE sender=%s AND receiver=%s AND is_read=0",
            (chat_with, username)
        )
        return jsonify({'success': True, 'messages': messages})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# 发送消息
@chat_bp.route('/send_message', methods=['POST'])
def send_message():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    try:
        data     = request.get_json()
        receiver = data.get('receiver')
        content  = data.get('content')
        if not receiver or not content:
            return jsonify({'success': False, 'message': '参数不完整'})
        if len(content) > 1000:
            return jsonify({'success': False, 'message': '消息内容过长'})

        from utils.helpers import check_user_exists
        if not check_user_exists(receiver):
            return jsonify({'success': False, 'message': '用户不存在'})

        now = datetime.now()
        db.execute_query(
            "INSERT INTO messages (sender,receiver,content,timestamp,is_read) VALUES (%s,%s,%s,%s,0)",
            (session['username'], receiver, content, now)
        )
        db.execute_query(
            "INSERT INTO user_status (username,last_active,is_online) VALUES (%s,%s,1) "
            "ON DUPLICATE KEY UPDATE last_active=%s, is_online=1",
            (session['username'], now, now)
        )
        return jsonify({'success': True, 'message': '发送成功',
                        'timestamp': now.strftime('%Y-%m-%d %H:%M:%S')})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# 撤回消息
@chat_bp.route('/withdraw_message', methods=['POST'])
def withdraw_message():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    try:
        data       = request.get_json()
        message_id = data.get('message_id')
        if not message_id:
            return jsonify({'success': False, 'message': '参数错误'})

        msg = db.fetch_one("SELECT sender,timestamp FROM messages WHERE id=%s", (message_id,))
        if not msg:
            return jsonify({'success': False, 'message': '消息不存在'})
        if msg['sender'] != session['username']:
            return jsonify({'success': False, 'message': '无权撤回此消息'})

        diff = (datetime.now() - msg['timestamp']).total_seconds()
        if diff > 120:
            return jsonify({'success': False, 'message': '消息发送超过2分钟，不能撤回'})

        db.execute_query(
            "UPDATE messages SET is_withdrawn=1,content='消息已撤回' WHERE id=%s", (message_id,)
        )
        return jsonify({'success': True, 'message': '撤回成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# 未读消息数量
@chat_bp.route('/get_unread_count')
def get_unread_count():
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    try:
        r = db.fetch_one("SELECT COUNT(*) as c FROM messages WHERE receiver=%s AND is_read=0",
                         (session['username'],))
        return jsonify({'success': True, 'count': (r or {}).get('c', 0)})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@chat_bp.route('/api/unread_message_count')
def get_unread_message_count():
    if 'username' not in session:
        return jsonify({'success': False, 'count': 0})
    try:
        if not db.check_table_exists('messages'):
            return jsonify({'success': True, 'count': 0})
        r     = db.fetch_one("SELECT COUNT(*) as c FROM messages WHERE receiver=%s AND is_read=0",
                             (session['username'],))
        count = (r or {}).get('c', 0)
        return jsonify({'success': True, 'count': count})
    except Exception:
        return jsonify({'success': False, 'count': 0})


# 更新用户在线状态
@chat_bp.route('/update_status', methods=['POST'])
def update_user_status():
    if 'username' not in session:
        return jsonify({'success': False})
    try:
        username = session['username']
        now      = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if not db.check_table_exists('user_status'):
            db.execute_query(
                "CREATE TABLE IF NOT EXISTS user_status ("
                "username VARCHAR(50) PRIMARY KEY,"
                "last_active DATETIME NOT NULL,"
                "is_online TINYINT(1) DEFAULT 0,"
                "INDEX idx_last_active (last_active)"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
            )

        db.execute_query(
            "INSERT INTO user_status (username,last_active,is_online) VALUES (%s,%s,1) "
            "ON DUPLICATE KEY UPDATE last_active=%s, is_online=1",
            (username, now, now)
        )
        return jsonify({'success': True})
    except Exception:
        return jsonify({'success': False})


# 获取用户在线状态
@chat_bp.route('/get_user_status')
def get_user_status():
    if 'username' not in session:
        return jsonify({'success': False})
    users = request.args.get('users', '')
    if not users:
        return jsonify({'success': False})
    user_list = users.split(',')
    try:
        offline = {u: {'is_online': False, 'last_active': None} for u in user_list}

        if not db.check_table_exists('user_status'):
            return jsonify({'success': True, 'status': offline})

        ph      = ','.join(['%s'] * len(user_list))
        results = db.fetch_all(
            f"SELECT username,is_online,last_active FROM user_status WHERE username IN ({ph})",
            tuple(user_list)
        )

        now         = datetime.now()
        status_dict = dict(offline)
        for r in (results or []):
            diff      = (now - r['last_active']).total_seconds() if r['last_active'] else 999
            is_online = bool(r['is_online']) and diff < 300
            status_dict[r['username']] = {
                'is_online':   is_online,
                'last_active': r['last_active'].strftime('%Y-%m-%d %H:%M:%S') if r['last_active'] else None,
            }

        return jsonify({'success': True, 'status': status_dict})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False})
