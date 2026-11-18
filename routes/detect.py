"""
routes/detect.py - 新闻检测 Blueprint

"""
from __future__ import annotations

import os
import uuid
import json
from datetime import datetime

import requests as http_requests
from flask import Blueprint, request, jsonify, render_template, session, url_for

from utils.db import db

detect_bp = Blueprint('detect', __name__)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = 'https://api.deepseek.com/v1/chat/completions'
ALLOWED_ROLES    = ['普通用户', 'experts', 'admin']
ALLOWED_IMAGE_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def _allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXT


# 检测页面
@detect_bp.route('/defect_predict')
def defect_predict():
    if 'username' not in session:
        from flask import redirect
        return redirect(url_for('auth.login_page'))
    return render_template('defect_predict.html')


# 多模态检测（文本 + 图片，仅使用 DeepSeek）
@detect_bp.route('/multimodal_predict', methods=['POST'])
def multimodal_predict():
    if 'username' not in session or session.get('user_type') not in ALLOWED_ROLES:
        return jsonify({'success': False, 'error': '请先登录'})

    username = session['username']
    role     = session['user_type']

    try:
        title   = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        if not content:
            return jsonify({'success': False, 'error': '新闻内容不能为空'})

        # 处理上传图片
        uploaded_files = request.files.getlist('images')
        if len(uploaded_files) > 5:
            return jsonify({'success': False, 'error': '最多上传5张图片'})

        image_paths = []
        for file in uploaded_files:
            if not (file and file.filename):
                continue
            if not _allowed_file(file.filename):
                return jsonify({'success': False, 'error': f'不支持的文件类型: {file.filename}'})
            file.seek(0, os.SEEK_END)
            if file.tell() > 10 * 1024 * 1024:
                return jsonify({'success': False, 'error': '图片超过10MB'})
            file.seek(0)

            ext      = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'jpg'
            ts       = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = f"news_{username}_{ts}_{uuid.uuid4().hex[:8]}.{ext}"
            folder   = os.path.join('static', 'uploads', 'news_images')
            os.makedirs(folder, exist_ok=True)
            file.save(os.path.join(folder, filename))
            image_paths.append(os.path.join('uploads', 'news_images', filename))

        full_text = f"{title} {content}".strip()

        # DeepSeek 文本分析
        deepseek_result = _analyze_text_with_deepseek(full_text)

        if not deepseek_result.get('success'):
            return jsonify({'success': False, 'error': 'DeepSeek 检测失败，请稍后重试'})

        p = deepseek_result['prob_fake']
        final = {
            'label':            deepseek_result['label'],
            'confidence':       deepseek_result['confidence'],
            'fake_probability': p,
            'real_probability': 100 - p,
        }
        method = "DeepSeek AI 文本分析"

        # 写库
        detect_id = _save_detection(username, role, title, content, final, image_paths)

        return jsonify({
            'success':          True,
            'label':            final['label'],
            'prob':             round(final['confidence'], 2),
            'result_rate':      round(final['confidence'], 2),
            'real_probability': round(final.get('real_probability', 0), 2),
            'fake_probability': round(final.get('fake_probability', 0), 2),
            'fusion_method':    method,
            'detect_id':        detect_id,
            'has_images':       bool(image_paths),
            'image_paths':      image_paths,
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': f'预测失败: {e}'})


# 纯文本 DeepSeek 快速检测
@detect_bp.route('/submit_and_predict', methods=['POST'])
def submit_and_predict():
    if 'username' not in session or session.get('user_type') not in ALLOWED_ROLES:
        return jsonify({'success': False, 'error': '请先登录'})

    data    = request.get_json()
    title   = data.get('title', '').strip()
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'success': False, 'error': '新闻内容不能为空'})

    prompt = (
        f"请分析以下新闻的真实性。\n标题：{title}\n内容：{content}\n\n"
        "以JSON格式返回：label(真实新闻/虚假新闻)、confidence(0-100)、"
        "reasoning(简要理由)、need_web_search(true/false)"
    )

    try:
        resp = http_requests.post(
            DEEPSEEK_API_URL,
            headers={'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
                     'Content-Type': 'application/json'},
            json={
                'model':           'deepseek-chat',
                'messages':        [
                    {'role': 'system', 'content': '你是专业的新闻真实性分析专家。'},
                    {'role': 'user',   'content': prompt},
                ],
                'temperature':     0.3,
                'max_tokens':      500,
                'response_format': {'type': 'json_object'},
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return jsonify({'success': False, 'error': 'DeepSeek API 调用失败'})

        ds = json.loads(resp.json()['choices'][0]['message']['content'])
        label      = ds.get('label', '')
        confidence = float(ds.get('confidence', 50))
        prob_fake  = confidence if label == '虚假新闻' else 100 - confidence

        _save_detection(session['username'], session['user_type'],
                        title, content,
                        {'label': label, 'confidence': confidence},
                        [])

        return jsonify({
            'success':          True,
            'label':            label,
            'prob':             round(confidence, 2),
            'result_rate':      round(confidence, 2),
            'prob_fake':        round(prob_fake, 2),
            'real_probability': round(confidence if label == '真实新闻' else 100 - confidence, 2),
            'fake_probability': round(prob_fake, 2),
            'fusion_method':    'DeepSeek AI 单独预测',
            'ai_prediction': {
                'label':      label,
                'confidence': confidence,
                'reasoning':  ds.get('reasoning', ''),
                'need_web_search': ds.get('need_web_search', False),
            },
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': f'预测失败: {e}'})


# 内部工具

def _save_detection(username, role, title, content, result, image_paths):
    """写入 news_detection 表，返回 detect_id 或 None"""
    try:
        count = (db.fetch_one(
            "SELECT COUNT(*) as c FROM news_detection WHERE user_name=%s AND user_role=%s",
            (username, role)
        ) or {}).get('c', 0)

        detect_data = f"{title}|{content}" if title else f"无标题|{content}"
        cur = db.execute_query(
            "INSERT INTO news_detection "
            "(user_role,check_time,user_name,detect_result,detect_counts,"
            "detect_resultrate,detect_data,image_path) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (role, datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
             username, result['label'], count + 1,
             round(result['confidence'], 2), detect_data,
             ','.join(image_paths) if image_paths else None)
        )
        return cur.lastrowid if cur else None
    except Exception as e:
        print(f"写库失败: {e}")
    return None


def _analyze_text_with_deepseek(text: str) -> dict:
    """DeepSeek 文本分析"""
    try:
        payload = {
            'model': 'deepseek-chat',
            'messages': [
                {'role': 'system', 'content': '你是一个专业的新闻真实性分析专家。'},
                {'role': 'user',   'content': (
                    f"请分析以下新闻的真实性，以JSON格式返回："
                    f"label(真实新闻/虚假新闻)、confidence(0-100)、fake_probability(0-100)。\n\n{text}"
                )},
            ],
            'temperature': 0.3,
            'max_tokens':  500,
            'response_format': {'type': 'json_object'},
        }
        resp = http_requests.post(
            DEEPSEEK_API_URL,
            headers={'Authorization': f'Bearer {DEEPSEEK_API_KEY}', 'Content-Type': 'application/json'},
            json=payload, timeout=30,
        )
        if resp.status_code == 200:
            r = json.loads(resp.json()['choices'][0]['message']['content'])
            return {
                'success':    True,
                'label':      r.get('label', '未知'),
                'confidence': float(r.get('confidence', 50)),
                'prob_fake':  float(r.get('fake_probability', 50)),
            }
    except Exception as e:
        print(f"DeepSeek 分析失败: {e}")
    return {'success': False, 'error': '调用失败'}


def _build_image_urls(image_path_str):
    """将数据库中存储的图片路径转换为可访问 URL"""
    if not image_path_str:
        return []
    urls = []
    for p in image_path_str.split(','):
        p = p.strip().replace('\\', '/')
        if not p:
            continue
        if 'static/' in p:
            idx = p.rfind('static/')
            p   = p[idx + 7:]
        elif p.startswith('/'):
            p = p.lstrip('/')
        if p.startswith('static/'):
            p = p[7:]
        if not p:
            continue
        try:
            urls.append(url_for('static', filename=p))
        except Exception:
            urls.append('/static/' + p)
    return urls
