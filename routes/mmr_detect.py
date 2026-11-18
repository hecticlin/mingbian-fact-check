# routes/mmr_detect.py - v5
# ============================================================
# 修复：
#   1. _extract_db_params 直接读取 DB_CONFIG，不再从 _ConnectionProxy 取属性
#   2. 移除对 db.connect()/db.close() 的依赖
# ============================================================

from __future__ import annotations

import importlib.util
import sys
import types
import uuid
import traceback
from pathlib import Path

from flask import Blueprint, request, jsonify, session

import json
import datetime
try:
    import pymysql as _mysql_driver
except ImportError:
    try:
        import MySQLdb as _mysql_driver
    except ImportError:
        _mysql_driver = None


mmr_detect_bp = Blueprint("mmr_detect", __name__)

_ROUTES_DIR   = Path(__file__).resolve().parent
_PROJECT_ROOT = _ROUTES_DIR.parent
_MMD_DIR      = _PROJECT_ROOT / "multi_fake_news_detector"

# ─
# 工具：创建 mmd 专属包 stub
# ─
_MMD_PKG_PREFIX = "__mmd__."


def _make_pkg_stub(pkg_name: str) -> types.ModuleType:
    internal_key = _MMD_PKG_PREFIX + pkg_name
    if internal_key in sys.modules:
        return sys.modules[internal_key]
    stub = types.ModuleType(pkg_name)
    stub.__path__    = [str(_MMD_DIR / pkg_name)]
    stub.__package__ = pkg_name
    stub.__spec__    = None
    sys.modules[internal_key] = stub
    return stub


_cfg_stub  = _make_pkg_stub("config")
_utl_stub  = _make_pkg_stub("utils")
_ret_stub  = _make_pkg_stub("retrieval")
_core_stub = _make_pkg_stub("core")


def _load(rel_path: str, mmd_name: str, pkg_name: str, attr_name: str):
    if mmd_name in sys.modules:
        return sys.modules[mmd_name]

    dot_path = f"{pkg_name}.{attr_name}"

    stub_map = {
        "config":    _cfg_stub,
        "utils":     _utl_stub,
        "retrieval": _ret_stub,
        "core":      _core_stub,
    }
    stub = stub_map.get(pkg_name)

    placeholder = types.ModuleType(mmd_name)
    sys.modules[mmd_name]  = placeholder
    sys.modules[dot_path]  = placeholder
    if stub is not None:
        setattr(stub, attr_name, placeholder)

    old_pkg = sys.modules.get(pkg_name)
    if stub is not None:
        sys.modules[pkg_name] = stub

    spec = importlib.util.spec_from_file_location(
        mmd_name,
        str(_MMD_DIR / rel_path),
        submodule_search_locations=[]
    )
    mod = importlib.util.module_from_spec(spec)

    sys.modules[mmd_name] = mod
    sys.modules[dot_path] = mod
    if stub is not None:
        setattr(stub, attr_name, mod)

    spec.loader.exec_module(mod)

    if old_pkg is not None:
        sys.modules[pkg_name] = old_pkg
    elif pkg_name in sys.modules and sys.modules[pkg_name] is stub:
        pass

    return mod


def _bootstrap_mmd():
    sentinel = "__mmd__.__bootstrapped__"
    if sentinel in sys.modules:
        return

    _load("config/settings.py",    "mmd_settings",          "config",    "settings")
    _load("utils/logger.py",       "mmd_logger",            "utils",     "logger")
    _load("utils/image_utils.py",  "mmd_image_utils",       "utils",     "image_utils")
    _load("retrieval/bocha_retriever.py",   "mmd_bocha_retriever",   "retrieval", "bocha_retriever")

    if "mmd_baidu_retriever" not in sys.modules:
        _baidu_stub = types.ModuleType("mmd_baidu_retriever")
        exec(
            "class BaiduRetriever:\n"
            "    def __init__(self,*a,**k): pass\n"
            "    def search(self,*a,**k): return []\n"
            "    def search_baike(self,*a,**k): return []\n",
            _baidu_stub.__dict__
        )
        sys.modules["mmd_baidu_retriever"]       = _baidu_stub
        sys.modules["retrieval.baidu_retriever"] = _baidu_stub
        setattr(_ret_stub, "baidu_retriever", _baidu_stub)

    _load("retrieval/retrieval_manager.py", "mmd_retrieval_manager", "retrieval", "retrieval_manager")
    _load("core/lvlm_client.py",     "mmd_lvlm_client",     "core", "lvlm_client")
    _load("core/task_decomposer.py", "mmd_task_decomposer", "core", "task_decomposer")
    _load("core/verifier.py",        "mmd_verifier",        "core", "verifier")
    _load("core/fusion.py",          "mmd_fusion",          "core", "fusion")

    if "mmd_detector" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "mmd_detector", str(_MMD_DIR / "detector.py"),
            submodule_search_locations=[]
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["mmd_detector"] = mod
        old = {}
        for pkg, stub in [("config", _cfg_stub), ("utils", _utl_stub),
                          ("retrieval", _ret_stub), ("core", _core_stub)]:
            old[pkg] = sys.modules.get(pkg)
            sys.modules[pkg] = stub
        spec.loader.exec_module(mod)
        for pkg, orig in old.items():
            if orig is not None:
                sys.modules[pkg] = orig

    sys.modules[sentinel] = types.ModuleType(sentinel)


# 懒加载单例检测器
_detector = None


def _get_detector():
    global _detector
    if _detector is not None:
        return _detector

    _bootstrap_mmd()

    RumorDetector = sys.modules["mmd_detector"].RumorDetector
    if not hasattr(RumorDetector, "detect_text_only"):
        _attach_text_only(RumorDetector)

    _detector = RumorDetector(retrieval_strategy="bocha_only")
    return _detector


def _attach_text_only(cls):
    SubTaskResult = sys.modules["mmd_verifier"].SubTaskResult

    def detect_text_only(self, text: str):
        sub = {}
        try:
            sub["text_check"] = self.verifier.verify_text(text)
        except Exception as e:
            sub["text_check"] = SubTaskResult(
                task_name="text_check", fake_score=0.5,
                reasoning=f"文本核查失败: {e}", raw_output="")
        sub["image_check"] = SubTaskResult(
            task_name="image_check", fake_score=0.5,
            reasoning="未上传图片，跳过图像核查", raw_output="")
        sub["consistency_check"] = SubTaskResult(
            task_name="consistency_check", fake_score=0.5,
            reasoning="未上传图片，跳过图文一致性核查", raw_output="")
        return self.fusion.fuse(
            sub_results=sub, input_text=text, input_image_path="")

    cls.detect_text_only = detect_text_only


# ─
# 路由  POST /mmr_predict
# ─
@mmr_detect_bp.route("/mmr_predict", methods=["POST"])
def mmr_predict():
    if "username" not in session:
        return jsonify({"success": False, "error": "请先登录"}), 401

    title   = request.form.get("title",   "").strip()
    content = request.form.get("content", "").strip()
    if not content:
        return jsonify({"success": False, "error": "请输入新闻正文"}), 400

    upload_dir = _PROJECT_ROOT / "static" / "uploads" / "mmr_tmp"
    upload_dir.mkdir(parents=True, exist_ok=True)

    image_paths: list[str] = []
    for f in request.files.getlist("images")[:5]:
        if f and f.filename:
            ext = Path(f.filename).suffix.lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
                continue
            fpath = upload_dir / f"{uuid.uuid4().hex}{ext}"
            f.save(str(fpath))
            image_paths.append(str(fpath))

    full_text = f"{title}\n\n{content}" if title else content

    try:
        detector = _get_detector()
        if image_paths:
            if len(image_paths) > 1:
                full_text += f"\n\n（共上传 {len(image_paths)} 张图片）"
            result = detector.detect(image_path=image_paths[0], text=full_text)
        else:
            result = detector.detect_text_only(text=full_text)
    except Exception:
        traceback.print_exc()
        _cleanup(image_paths)
        return jsonify({"success": False, "error": "检测过程出错，请查看后台日志"}), 500

    # 检测成功：把图片从 mmr_tmp 移到永久目录，转为 URL 路径
    saved_urls = []
    perm_dir = _PROJECT_ROOT / "static" / "uploads" / "news_images"
    perm_dir.mkdir(parents=True, exist_ok=True)
    for p in image_paths:
        try:
            src = Path(p)
            if src.exists():
                dst = perm_dir / src.name
                src.rename(dst)
                saved_urls.append(f"/static/uploads/news_images/{src.name}")
            else:
                saved_urls.append("")
        except Exception:
            saved_urls.append("")

    label_map = {"谣言": "虚假新闻", "真实": "真实新闻", "待核实": "待核实"}
    label = label_map.get(result.verdict, result.verdict)

    detect_id = _save_mmr_result(session, title, content, saved_urls, result, label)

    resp = {
        "success":    True,
        "label":      label,
        "prob":       round(result.confidence * 100, 2),
        "verdict":    result.verdict,
        "confidence": result.confidence,
        "reasoning":  result.reasoning,
        "sub_scores": {
            "text_check":        round(result.text_score,        4),
            "image_check":       round(result.image_score,       4),
            "consistency_check": round(result.consistency_score, 4),
        },
        "evidence":   result.evidence[:5],
        "has_image":  bool(saved_urls and any(saved_urls)),
        "image_urls": [u for u in saved_urls if u],
        "detect_id":  detect_id,
    }

    # 若为权威短路，补充标记
    if isinstance(result.reasoning, dict) and result.reasoning.get("short_circuited"):
        resp["short_circuited"] = True
        resp["short_circuit_source"] = result.reasoning.get("short_circuit_source", "")

    return jsonify(resp)


# ============================================================
# 独立数据库连接
# ============================================================

def _to_urls(ipath_str):
    """将数据库中的 image_path 字段（逗号分隔）转为前端可用的 URL 列表"""
    if not ipath_str:
        return []
    urls = []
    for p in str(ipath_str).split(","):
        p = p.strip().replace("\\", "/")
        if not p:
            continue
        if p.startswith("/static/"):
            urls.append(p)  # 已经是 URL 格式
        elif "static/" in p:
            urls.append("/static/" + p[p.rfind("static/") + 7:])
        elif not p.startswith("/"):
            urls.append("/static/uploads/news_images/" + p.split("/")[-1])
        else:
            urls.append(p)
    return urls


def _new_conn():
    """
    创建一个全新的独立 pymysql 连接。
    直接使用 DB_CONFIG 中的参数，不再依赖 _ConnectionProxy。
    """
    if _mysql_driver is None:
        return None
    try:
        from utils.db import DB_CONFIG
        conn = pymysql.connect(
            host=DB_CONFIG['host'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            port=DB_CONFIG['port'],
            database=DB_CONFIG['database'],
            charset=DB_CONFIG.get('charset', 'utf8mb4'),
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )
        return conn
    except Exception:
        traceback.print_exc()
        return None


import pymysql
import pymysql.cursors


# ============================================================
# 检测结果写入 news_detection 表
# ============================================================

def _save_mmr_result(session_obj: dict, title: str, content: str,
                     image_paths: list, result, label: str) -> int | None:
    conn = _new_conn()
    if conn is None:
        return None
    try:
        reasoning_dict = {
            "text_check":        (result.reasoning or {}).get("text_check", ""),
            "image_check":       (result.reasoning or {}).get("image_check", ""),
            "consistency_check": (result.reasoning or {}).get("consistency_check", ""),
            "sub_scores": {
                "text_check":        round(getattr(result, "text_score",        0), 4),
                "image_check":       round(getattr(result, "image_score",       0), 4),
                "consistency_check": round(getattr(result, "consistency_score", 0), 4),
            },
            "evidence":    (result.evidence or [])[:5],
            "detect_type": "mmr",
        }
        image_path_str = ",".join(image_paths) if image_paths else ""
        detect_data    = f"{title}|{content}" if title else f"|{content}"

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO news_detection "
                "(user_name, user_role, check_time, detect_result, detect_counts, "
                "detect_resultrate, reasoning, detect_data, image_path) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    session_obj.get("username", ""),
                    session_obj.get("user_type", "user"),
                    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    label,
                    1,
                    round(result.confidence * 100, 2),
                    json.dumps(reasoning_dict, ensure_ascii=False),
                    detect_data,
                    image_path_str,
                ),
            )
            conn.commit()
            return cur.lastrowid
    except Exception:
        traceback.print_exc()
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


# 历史记录路由
@mmr_detect_bp.route("/api/mmr/detection_history", methods=["GET"])
def get_detection_history():
    if "username" not in session:
        return jsonify({"success": False, "message": "请先登录"}), 401

    page     = max(1, int(request.args.get("page",     1)))
    per_page = max(1, int(request.args.get("per_page", 5)))
    username = session.get("username", "")

    conn = _new_conn()
    if conn is None:
        return jsonify({"success": False, "message": "数据库连接失败"}), 500

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM news_detection WHERE user_name = %s",
                (username,)
            )
            row   = cur.fetchone()
            total = int((row.get("cnt") or 0) if isinstance(row, dict) else (row[0] if row else 0))

        total_pages = max(1, (total + per_page - 1) // per_page)
        offset      = (page - 1) * per_page

        with conn.cursor() as cur:
            cur.execute(
                "SELECT detect_id, check_time, detect_result, detect_resultrate, "
                "detect_data, image_path, reasoning "
                "FROM news_detection "
                "WHERE user_name = %s "
                "ORDER BY check_time DESC "
                "LIMIT %s OFFSET %s",
                (username, per_page, offset)
            )
            rows = cur.fetchall() or []

        records = []
        for r in rows:
            if isinstance(r, dict):
                did   = r.get("detect_id")
                ct    = r.get("check_time", "")
                dres  = r.get("detect_result", "")
                drate = r.get("detect_resultrate", 0)
                ddata = r.get("detect_data") or ""
                ipath = r.get("image_path")  or ""
                rraw  = r.get("reasoning")   or ""
            else:
                did, ct, dres, drate, ddata, ipath, rraw = (
                    r[0], r[1], r[2], r[3], r[4] or "", r[5] or "", r[6] or ""
                )

            parts   = str(ddata).split("|", 1)
            title   = parts[0].strip() if len(parts) > 1 else ""
            content = parts[1].strip() if len(parts) > 1 else parts[0].strip()

            reasoning_obj = {}
            if rraw:
                try:
                    reasoning_obj = json.loads(rraw)
                except Exception:
                    reasoning_obj = {"raw": str(rraw)}

            records.append({
                "detect_id":         did,
                "check_time":        ct.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ct, "strftime") else str(ct),
                "detect_result":     dres,
                "detect_resultrate": float(drate or 0),
                "title":             title,
                "content":           content,
                "has_image":         bool(ipath),
                "image_urls":        _to_urls(ipath),
                "reasoning":         reasoning_obj,
            })

        return jsonify({
            "success":     True,
            "records":     records,
            "total":       total,
            "total_pages": total_pages,
            "page":        page,
            "user_role":   session.get("user_type", ""),
        })
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "查询失败，请查看后台日志"}), 500
    finally:
        try:
            conn.close()
        except Exception:
            pass


# 详情路由
@mmr_detect_bp.route("/api/mmr/detection_detail/<int:detect_id>", methods=["GET"])
def get_detection_detail(detect_id):
    if "username" not in session:
        return jsonify({"success": False, "message": "请先登录"}), 401

    conn = _new_conn()
    if conn is None:
        return jsonify({"success": False, "message": "数据库连接失败"}), 500

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT detect_id, check_time, detect_result, detect_resultrate, "
                "detect_data, image_path, reasoning "
                "FROM news_detection "
                "WHERE detect_id = %s AND user_name = %s",
                (detect_id, session.get("username", ""))
            )
            row = cur.fetchone()

        if not row:
            return jsonify({"success": False, "message": "记录不存在或无权访问"})

        if isinstance(row, dict):
            did   = row.get("detect_id")
            ct    = row.get("check_time", "")
            dres  = row.get("detect_result", "")
            drate = row.get("detect_resultrate", 0)
            ddata = row.get("detect_data") or ""
            ipath = row.get("image_path")  or ""
            rraw  = row.get("reasoning")   or ""
        else:
            did, ct, dres, drate, ddata, ipath, rraw = (
                row[0], row[1], row[2], row[3], row[4] or "", row[5] or "", row[6] or ""
            )

        parts   = str(ddata).split("|", 1)
        title   = parts[0].strip() if len(parts) > 1 else ""
        content = parts[1].strip() if len(parts) > 1 else parts[0].strip()

        reasoning_obj = {}
        if rraw:
            try:
                reasoning_obj = json.loads(rraw)
            except Exception:
                reasoning_obj = {"raw": str(rraw)}

        image_urls = _to_urls(ipath)

        return jsonify({
            "success": True,
            "detail": {
                "detect_id":         did,
                "check_time":        ct.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ct, "strftime") else str(ct),
                "detect_result":     dres,
                "detect_resultrate": float(drate or 0),
                "title":             title,
                "content":           content,
                "has_image":         bool(ipath),
                "image_urls":        image_urls,
                "reasoning":         reasoning_obj,
            }
        })
    except Exception:
        traceback.print_exc()
        return jsonify({"success": False, "message": "查询失败，请查看后台日志"}), 500
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _cleanup(paths: list[str]) -> None:
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass
