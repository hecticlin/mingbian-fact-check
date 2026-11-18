"""
routes/llm_detect.py - 大语言模型深度文本检测 Blueprint

"""
from __future__ import annotations

import sys
import os
import json
import time
import threading
import queue
from datetime import datetime

from flask import Blueprint, request, jsonify, session, Response, stream_with_context

from utils.db import db

llm_detect_bp = Blueprint('llm_detect', __name__)
ALLOWED_ROLES = ['普通用户', 'experts', 'admin']

# 自动定位 fake_news_detector 目录
def _find_and_add_llm_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        'fake_news_detector',
        'text_fake_news_detector',
        'FakeNewsDetector',
    ]
    for name in candidates:
        path = os.path.join(root, name)
        if os.path.isdir(path) and os.path.exists(os.path.join(path, 'detector.py')):
            return True, path
    if os.path.exists(os.path.join(root, 'detector.py')):
        return True, root
    return False, None

_LLM_FOUND, _LLM_PATH = _find_and_add_llm_path()


def _import_from_path(module_name, file_path):
    """按绝对文件路径导入模块，完全绕过 sys.path，避免 config 冲突"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    # 先把同名模块从缓存中临时注入，让内部 from config import ... 能找到正确的 config
    old = sys.modules.get(module_name)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        # 回滚
        if old is not None:
            sys.modules[module_name] = old
        else:
            sys.modules.pop(module_name, None)
        raise
    return mod


def _load_llm_modules():
    """一次性按文件路径加载 fake_news_detector 下的所有模块"""
    if not _LLM_FOUND or not _LLM_PATH:
        return None

    p = _LLM_PATH

    # 必须先加载 config，因为其他模块依赖它
    config_mod = _import_from_path('config', os.path.join(p, 'config.py'))

    # 再依次加载其他模块（它们内部 from config import ... 现在能找到正确的 config）
    _import_from_path('implicit_types', os.path.join(p, 'implicit_types.py'))
    _import_from_path('llm_client',     os.path.join(p, 'llm_client.py'))
    _import_from_path('retrieval',      os.path.join(p, 'retrieval.py'))
    _import_from_path('event_extractor',os.path.join(p, 'event_extractor.py'))
    _import_from_path('dual_model',     os.path.join(p, 'dual_model.py'))
    _import_from_path('authority_check',os.path.join(p, 'authority_check.py'))
    detector_mod = _import_from_path('detector', os.path.join(p, 'detector.py'))

    return detector_mod


# 标签映射
def _map_label(label: str) -> str:
    return {
        '真实': '真实新闻', '虚假': '虚假新闻',
        '无法判断': '无法判断', '两阶段分歧未解决': '无法判断',
    }.get(label, label)


def _estimate_confidence(result) -> float:
    if result.short_circuited:
        return 95.0
    if result.final_verdict == '无法判断':
        return 50.0
    agreed = sum(1 for b in result.branch_results if b.agreed)
    total  = max(len(result.branch_results), 1)
    return round(70 + (agreed / total) * 15, 2)


# 写库
def _save_llm_detection(username, role, title, content, label, confidence, reasoning):
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
            "detect_resultrate,detect_data,reasoning) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (role, datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
             username, label, count + 1,
             round(confidence, 2), detect_data, reasoning)
        )
        return cur.lastrowid if cur else None
    except Exception as e:
        print(f"LLM检测写库失败: {e}")
    return None


# # SSE 流式检测接口
# ─

def _sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _run_detection_with_progress(news_text: str, msg_queue: queue.Queue):
    def push(event_type, **kwargs):
        msg_queue.put((event_type, kwargs))

    try:
        if not _LLM_FOUND:
            push('error', message='找不到 fake_news_detector 目录，请确认目录名为 '
                 'fake_news_detector 或 text_fake_news_detector 并与 app.py 同级')
            return

        push('progress', step=0, total=6, label='正在加载检测模块...')
        try:
            detector_mod = _load_llm_modules()
            if detector_mod is None:
                push('error', message='找不到 fake_news_detector 目录')
                return
            authority_mod       = sys.modules.get('authority_check')
            event_extractor_mod = sys.modules.get('event_extractor')
            detect_fn           = detector_mod.detect
            DetectionResult     = detector_mod.DetectionResult
        except Exception as e:
            import traceback; traceback.print_exc()
            push('error', message=f'模块导入失败: {e}，请确认 fake_news_detector 目录下有 detector.py 和 config.py')
            return

        # Step 0：权威核查
        push('progress', step=1, total=6,
             label=' Step 0：正在检索权威媒体与辟谣平台...',
             detail='联网搜索权威媒体（人民网、新华网等）和辟谣平台（piyao.org.cn等）')

        auth_result = authority_mod.authority_check(news_text)

        if auth_result.short_circuit:
            label_display = _map_label(auth_result.verdict)
            confidence    = 95.0
            push('short_circuit',
                 verdict=label_display,
                 source=auth_result.source_name,
                 reason=auth_result.reason)
            push('done',
                 label=label_display,
                 confidence=confidence,
                 short_circuited=True,
                 short_circuit_source=auth_result.source_name,
                 final_reason=auth_result.reason,
                 branch_results=[],
                 extraction_summary={},
                 total_branches=0,
                 fake_branches=0)
            return

        push('progress', step=1, total=6,
             label=' Step 0：未找到直接核查记录，继续深度分析',
             detail='权威媒体与辟谣平台未发现完全一致的核查结论')

        # Step 1：事件抽取
        push('progress', step=2, total=6,
             label='📝 Step 1：提取主干事件（5W1H）...',
             detail='分析新闻的核心事件：何人、何事、何时、何地、为何、如何')

        from event_extractor import extract_backbone_event, extract_secondary_events, extract_implicit_info
        backbone = extract_backbone_event(news_text)

        push('progress', step=2, total=6,
             label=f'📝 Step 1：主干事件提取完成',
             detail=f'主干事件：{backbone.summary[:80]}')

        push('progress', step=2, total=6,
             label='📝 Step 1：提取次要事件...',
             detail='逐一提取新闻中的细粒度次要事件')
        secondary = extract_secondary_events(news_text, backbone.summary)

        push('progress', step=2, total=6,
             label=f'📝 Step 1：提取隐含信息（6类）...',
             detail='分析预设信息、会话含义、框架偏向、事实缺口、情绪操控、因果谬误')
        implicit  = extract_implicit_info(news_text, backbone.summary)

        push('progress', step=2, total=6,
             label=f' Step 1：事件抽取完成（次要事件{len(secondary)}个，隐含信息{len(implicit)}类）',
             detail='')

        from event_extractor import ExtractionResult
        extraction = ExtractionResult(
            backbone=backbone,
            secondary_events=secondary,
            implicit_infos=implicit
        )

        # Step 2：主干事件判断
        push('progress', step=3, total=6,
             label='🤖 Step 2：判断主干事件真实性...',
             detail=f'检索外部知识 + 两阶段验证（初判→批判复核）')

        from retrieval     import retrieve_for_event, format_retrieved_info
        from dual_model    import judge, format_result
        from implicit_types import JUDGMENT_PROMPT

        branch_results     = []
        verified_facts_lst = []
        total_branches     = 0
        fake_branches      = 0

        def judge_event(summary, branch_type, branch_label):
            pkg = retrieve_for_event(query=summary, news_text=news_text)
            retrieved_text = format_retrieved_info(pkg)
            jp  = JUDGMENT_PROMPT.format(
                event_description=summary,
                retrieved_info=retrieved_text,
                verified_facts='\n'.join(verified_facts_lst) or '（暂无已核实事实）',
            )
            dr  = judge(judgment_prompt=jp, event_description=summary, retrieved_info=retrieved_text)
            fmt = format_result(dr)
            from detector import BranchResult
            b = BranchResult(
                branch_type=branch_type, branch_label=branch_label,
                event_summary=summary,
                verdict=fmt.get('verdict', '无法判断'),
                reason=fmt.get('reason', ''),
                agreed=fmt.get('agreed', False),
                debate_rounds=fmt.get('review_rounds', 0),
            )
            if not fmt.get('agreed'):
                b.deepseek_verdict = fmt.get('initial', {}).get('verdict', '')
                b.deepseek_reason  = fmt.get('initial', {}).get('reason', '')
                b.review_verdict   = fmt.get('review',  {}).get('verdict', '')
                b.review_reason    = fmt.get('review',  {}).get('reason', '')
            return b

        # 主干判断
        b = judge_event(backbone.summary, 'backbone', '主干事件')
        branch_results.append(b)
        total_branches += 1
        verdict_display = _map_label(b.verdict)

        push('branch_result',
             branch_label=b.branch_label,
             verdict=verdict_display,
             agreed=b.agreed,
             reason=b.reason,
             event_summary=b.event_summary[:100])

        if b.verdict == '虚假':
            fake_branches += 1
            final_verdict = '虚假新闻'
            final_reason  = f'主干事件存在虚假信息：{b.reason}'
            _finish(push, final_verdict, final_reason, branch_results,
                    extraction, total_branches, fake_branches, news_text)
            return
        elif b.verdict == '真实':
            verified_facts_lst.append(f'主干事件（已核实为真）：{backbone.summary}')

        # Step 3：次要事件判断
        push('progress', step=4, total=6,
             label=f'🤖 Step 3：逐一判断次要事件（共{len(secondary)}个）...',
             detail='')

        for i, se in enumerate(secondary):
            if not se.summary:
                continue
            push('progress', step=4, total=6,
                 label=f'🤖 Step 3：判断次要事件 {i+1}/{len(secondary)}...',
                 detail=se.summary[:80])
            b2 = judge_event(se.summary, 'secondary', f'次要事件{i+1}')
            branch_results.append(b2)
            total_branches += 1
            push('branch_result',
                 branch_label=b2.branch_label,
                 verdict=_map_label(b2.verdict),
                 agreed=b2.agreed,
                 reason=b2.reason,
                 event_summary=b2.event_summary[:100])
            if b2.verdict == '虚假':
                fake_branches += 1
                final_verdict = '虚假新闻'
                final_reason  = f'次要事件{i+1}存在虚假信息：{b2.reason}'
                _finish(push, final_verdict, final_reason, branch_results,
                        extraction, total_branches, fake_branches, news_text)
                return
            elif b2.verdict == '真实':
                verified_facts_lst.append(f'次要事件{i+1}（已核实为真）：{se.summary}')

        # Step 4：隐含信息判断
        push('progress', step=5, total=6,
             label=f'🤖 Step 4：判断隐含信息（共{len(implicit)}类）...',
             detail='')

        for impl in implicit:
            if not impl.summary or '失败' in impl.summary:
                continue
            summary = f'[{impl.type_label}] {impl.summary}'
            push('progress', step=5, total=6,
                 label=f'🤖 Step 4：判断隐含信息 [{impl.type_label}]...',
                 detail=impl.summary[:80])
            b3 = judge_event(summary, 'implicit', f'隐含信息-{impl.type_label}')
            branch_results.append(b3)
            total_branches += 1
            push('branch_result',
                 branch_label=b3.branch_label,
                 verdict=_map_label(b3.verdict),
                 agreed=b3.agreed,
                 reason=b3.reason,
                 event_summary=b3.event_summary[:100])
            if b3.verdict == '虚假':
                fake_branches += 1
            elif b3.verdict == '真实':
                verified_facts_lst.append(f'隐含信息[{impl.type_label}]（已核实为真）：{impl.summary}')

        # Step 5：汇总
        push('progress', step=6, total=6, label='📊 Step 5：汇总最终结论...', detail='')

        if fake_branches > 0:
            final_verdict = '虚假新闻'
            descs = [f'  - [{b.branch_label}] {b.reason}'
                     for b in branch_results if b.verdict == '虚假']
            final_reason = f'新闻中有{fake_branches}个分支被判定为虚假：\n' + '\n'.join(descs)
        else:
            final_verdict = '真实新闻'
            final_reason  = f'所有{total_branches}个分支均未发现明显虚假信息，新闻判定为真实。'

        _finish(push, final_verdict, final_reason, branch_results,
                extraction, total_branches, fake_branches, news_text)

    except Exception as e:
        import traceback
        push('error', message=f'检测过程出错: {str(e)}', traceback=traceback.format_exc())


def _finish(push, final_verdict, final_reason, branch_results,
            extraction, total_branches, fake_branches, news_text):
    confidence = 95.0 if final_verdict == '虚假新闻' and fake_branches > 0 else \
                 (70.0 + min(fake_branches, 3) * 5 if fake_branches > 0 else
                  70.0 + (total_branches / max(total_branches, 1)) * 15)
    confidence = round(min(confidence, 99.0), 2)

    branches_data = []
    for b in branch_results:
        entry = {
            'branch_label':  b.branch_label,
            'branch_type':   b.branch_type,
            'event_summary': b.event_summary,
            'verdict':       _map_label(b.verdict),
            'agreed':        b.agreed,
            'reason':        b.reason,
        }
        if not b.agreed:
            entry['initial_verdict'] = _map_label(b.deepseek_verdict)
            entry['initial_reason']  = b.deepseek_reason
            entry['review_verdict']  = _map_label(b.review_verdict)
            entry['review_reason']   = b.review_reason
        branches_data.append(entry)

    extraction_summary = {}
    if extraction:
        extraction_summary = {
            'backbone':        extraction.backbone.summary if extraction.backbone else '',
            'secondary_count': len(extraction.secondary_events),
            'implicit_count':  len(extraction.implicit_infos),
            'implicit_types':  [
                {'type': i.type_label, 'summary': i.summary}
                for i in extraction.implicit_infos
                if i.summary and '失败' not in i.summary
            ],
        }

    push('done',
         label=final_verdict,
         confidence=confidence,
         short_circuited=False,
         short_circuit_source='',
         final_reason=final_reason,
         branch_results=branches_data,
         extraction_summary=extraction_summary,
         total_branches=total_branches,
         fake_branches=fake_branches)


# ─
# SSE 路由
# ─

@llm_detect_bp.route('/llm_text_predict_stream', methods=['GET'])
def llm_text_predict_stream():
    if 'username' not in session or session.get('user_type') not in ALLOWED_ROLES:
        def _err():
            yield _sse_event('error', {'message': '请先登录'})
        return Response(stream_with_context(_err()), mimetype='text/event-stream')

    title   = request.args.get('title', '').strip()
    content = request.args.get('content', '').strip()

    if not content:
        def _err2():
            yield _sse_event('error', {'message': '新闻内容不能为空'})
        return Response(stream_with_context(_err2()), mimetype='text/event-stream')

    full_text = f'标题：{title}\n\n正文：{content}' if title else content

    msg_q = queue.Queue()

    thread = threading.Thread(
        target=_run_detection_with_progress,
        args=(full_text, msg_q),
        daemon=True
    )
    thread.start()

    username = session['username']
    role     = session['user_type']

    def generate():
        yield _sse_event('start', {'message': '开始深度检测...'})
        done_data = None

        while True:
            try:
                event_type, data = msg_q.get(timeout=120)
            except queue.Empty:
                yield _sse_event('error', {'message': '检测超时（120秒），请重试'})
                break

            yield _sse_event(event_type, data)

            if event_type == 'done':
                done_data = data
                break
            if event_type == 'error':
                break

        # 写库
        if done_data:
            reasoning_json = json.dumps({
                'short_circuited':      done_data.get('short_circuited', False),
                'short_circuit_source': done_data.get('short_circuit_source', ''),
                'final_reason':         done_data.get('final_reason', ''),
                'branch_results':       done_data.get('branch_results', []),
                'extraction_summary':   done_data.get('extraction_summary', {}),
            }, ensure_ascii=False)

            detect_id = _save_llm_detection(
                username   = username,
                role       = role,
                title      = title,
                content    = content,
                label      = done_data.get('label', '无法判断'),
                confidence = done_data.get('confidence', 50.0),
                reasoning  = reasoning_json,
            )
            yield _sse_event('saved', {'detect_id': detect_id})

    resp = Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control':    'no-cache',
            'X-Accel-Buffering':'no',
            'Connection':       'keep-alive',
        }
    )
    return resp


# 同步接口（兼容保留）
@llm_detect_bp.route('/llm_text_predict', methods=['POST'])
def llm_text_predict():
    if 'username' not in session or session.get('user_type') not in ALLOWED_ROLES:
        return jsonify({'success': False, 'error': '请先登录'})

    data    = request.get_json() or {}
    title   = data.get('title', '').strip()
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'success': False, 'error': '新闻内容不能为空'})

    if not _LLM_FOUND:
        return jsonify({'success': False,
                        'error': f'大语言模型检测模块加载失败，请确认目录名为 '
                                 f'fake_news_detector 或 text_fake_news_detector，'
                                 f'并与 app.py 同级。当前搜索路径: '
                                 f'{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}'})

    full_text = f'标题：{title}\n\n正文：{content}' if title else content

    try:
        detector_mod = _load_llm_modules()
        if detector_mod is None:
            return jsonify({'success': False, 'error': '找不到 fake_news_detector 目录'})
        result = detector_mod.detect(full_text)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'error': f'检测出错: {e}'})

    label      = _map_label(result.final_verdict)
    confidence = _estimate_confidence(result)

    branch_results = []
    for b in result.branch_results:
        entry = {
            'branch_label':  b.branch_label,
            'branch_type':   b.branch_type,
            'event_summary': b.event_summary,
            'verdict':       _map_label(b.verdict),
            'agreed':        b.agreed,
            'reason':        b.reason,
        }
        if not b.agreed:
            entry['initial_verdict'] = _map_label(b.deepseek_verdict)
            entry['initial_reason']  = b.deepseek_reason
            entry['review_verdict']  = _map_label(b.review_verdict)
            entry['review_reason']   = b.review_reason
        branch_results.append(entry)

    extraction_summary = {}
    if result.extraction:
        extraction_summary = {
            'backbone':        result.extraction.backbone.summary,
            'secondary_count': len(result.extraction.secondary_events),
            'implicit_count':  len(result.extraction.implicit_infos),
            'implicit_types':  [
                {'type': i.type_label, 'summary': i.summary}
                for i in result.extraction.implicit_infos
                if i.summary and '失败' not in i.summary
            ],
        }

    reasoning_json = json.dumps({
        'short_circuited':      result.short_circuited,
        'short_circuit_source': result.short_circuit_source,
        'final_reason':         result.final_reason,
        'branch_results':       branch_results,
        'extraction_summary':   extraction_summary,
    }, ensure_ascii=False)

    detect_id = _save_llm_detection(
        session['username'], session['user_type'],
        title, content, label, confidence, reasoning_json
    )

    return jsonify({
        'success':            True,
        'label':              label,
        'confidence':         confidence,
        'prob':               confidence,
        'result_rate':        confidence,
        'short_circuited':    result.short_circuited,
        'short_circuit_source': result.short_circuit_source,
        'final_reason':       result.final_reason,
        'branch_results':     branch_results,
        'extraction_summary': extraction_summary,
        'detect_id':          detect_id,
        'total_branches':     result.total_branches,
        'fake_branches':      result.fake_branches,
        'fusion_method':      'LLM 深度文本检测（三层事件抽取 + 两阶段验证）',
    })


# 检测详情接口
@llm_detect_bp.route('/llm_detect_detail/<int:detect_id>')
def llm_detect_detail(detect_id):
    if 'username' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    try:
        row = db.fetch_one(
            "SELECT detect_id,check_time,detect_result,detect_resultrate,"
            "detect_data,reasoning,image_path "
            "FROM news_detection WHERE detect_id=%s AND user_name=%s",
            (detect_id, session['username'])
        )
        if not row:
            return jsonify({'success': False, 'message': '记录不存在'})
        try:
            title, content = row['detect_data'].split('|', 1)
        except Exception:
            title, content = '无标题', row.get('detect_data', '')

        reasoning = {}
        if row.get('reasoning'):
            try:
                reasoning = json.loads(row['reasoning'])
            except Exception:
                reasoning = {'raw': row['reasoning']}

        ct = row.get('check_time')
        return jsonify({'success': True, 'detail': {
            'detect_id':         row['detect_id'],
            'check_time':        ct.strftime('%Y-%m-%d %H:%M:%S') if hasattr(ct, 'strftime') else str(ct or ''),
            'detect_result':     row['detect_result'],
            'detect_resultrate': float(row['detect_resultrate'] or 0),
            'title':             title,
            'content':           content,
            'reasoning':         reasoning,
            'has_reasoning':     bool(reasoning),
        }})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
