# utils/image_utils.py
# ============================================================
# 图片工具：读取 / Base64 编码 / 基础预处理
# ============================================================

import base64
import os
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

logger = get_logger("image_utils")

SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def load_image_as_base64(image_path: str) -> str:
    """
    读取本地图片，返回 base64 编码字符串。
    Qwen2.5-VL 的 DashScope 接口需要此格式。
    """
    path = Path(image_path)

    if not path.exists():
        raise FileNotFoundError(f"图片文件不存在: {image_path}")

    if path.suffix.lower() not in SUPPORTED_FORMATS:
        raise ValueError(
            f"不支持的图片格式: {path.suffix}，"
            f"支持格式: {', '.join(SUPPORTED_FORMATS)}"
        )

    with open(path, "rb") as f:
        data = f.read()

    encoded = base64.b64encode(data).decode("utf-8")
    logger.debug(f"图片已编码: {path.name}，大小: {len(data) / 1024:.1f} KB")
    return encoded


def get_image_media_type(image_path: str) -> str:
    """返回图片的 MIME 类型。"""
    ext = Path(image_path).suffix.lower()
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }
    return mapping.get(ext, "image/jpeg")


def build_image_url_for_dashscope(image_path: str) -> dict:
    """
    构建 DashScope 接口所需的图片消息块。
    返回格式：{"image": "data:image/jpeg;base64,..."} 或 URL 格式。
    """
    if image_path.startswith(("http://", "https://")):
        return {"image": image_path}

    b64 = load_image_as_base64(image_path)
    media_type = get_image_media_type(image_path)
    return {"image": f"data:{media_type};base64,{b64}"}


def validate_image(image_path: str) -> bool:
    """基础校验图片文件是否可用。"""
    try:
        path = Path(image_path)
        if not path.exists():
            return False
        if path.stat().st_size == 0:
            logger.warning(f"图片文件为空: {image_path}")
            return False
        if path.suffix.lower() not in SUPPORTED_FORMATS:
            return False
        return True
    except Exception as e:
        logger.warning(f"图片校验失败: {e}")
        return False
