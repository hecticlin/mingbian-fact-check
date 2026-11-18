# core/lvlm_client.py
# ============================================================
# Qwen2.5-VL 客户端封装（DashScope API）
# 支持：纯文本 / 图文混合 / 多轮对话
# ============================================================

import json
from typing import Optional, Union
import http.client
import urllib.parse

from config.settings import (
    DASHSCOPE_API_KEY,
    LVLM_MODEL,
    LVLM_MAX_TOKENS,
    LVLM_TEMPERATURE,
    REQUEST_TIMEOUT,
)
from utils.image_utils import build_image_url_for_dashscope
from utils.logger import get_logger

logger = get_logger("lvlm_client")


class LVLMClient:
    """
    Qwen2.5-VL DashScope API 客户端。
    主要方法：
      - chat_with_image(image_path, text_prompt) → str
      - chat_text_only(prompt) → str
    """

    BASE_URL = "dashscope.aliyuncs.com"
    ENDPOINT = "/api/v1/services/aigc/multimodal-generation/generation"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or DASHSCOPE_API_KEY
        self.model = model or LVLM_MODEL

        if self.api_key == "YOUR_DASHSCOPE_API_KEY":
            logger.warning("  DashScope API Key 未配置，请在 config/settings.py 中填写")

    # 核心调用

    def chat_with_image(self, image_path: str, text_prompt: str) -> str:
        """
        图文混合推理。
        image_path 可以是本地路径或 HTTP URL。
        """
        image_block = build_image_url_for_dashscope(image_path)
        messages = [
            {
                "role": "user",
                "content": [
                    image_block,
                    {"text": text_prompt},
                ],
            }
        ]
        return self._call_api(messages)

    def chat_text_only(self, prompt: str) -> str:
        """纯文本推理（不含图片）。"""
        messages = [
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ]
        return self._call_api(messages)

    def chat_with_image_and_history(
        self,
        image_path: str,
        text_prompt: str,
        history: Optional[list] = None,
    ) -> str:
        """带历史上下文的图文推理（用于多轮检测）。"""
        messages = history or []
        image_block = build_image_url_for_dashscope(image_path)
        messages.append(
            {
                "role": "user",
                "content": [image_block, {"text": text_prompt}],
            }
        )
        return self._call_api(messages)

    # 内部 HTTP 调用

    def _call_api(self, messages: list) -> str:
        """
        发送请求到 DashScope，返回模型文本输出。
        使用标准库 http.client，避免外部 HTTP 依赖。
        """
        payload = {
            "model": self.model,
            "input": {"messages": messages},
            "parameters": {
                "max_tokens": LVLM_MAX_TOKENS,
                "temperature": LVLM_TEMPERATURE,
            },
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        try:
            conn = http.client.HTTPSConnection(
                self.BASE_URL, timeout=REQUEST_TIMEOUT
            )
            conn.request("POST", self.ENDPOINT, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
            conn.close()
        except Exception as e:
            logger.error(f"DashScope 请求失败: {e}")
            raise RuntimeError(f"LVLM API 调用失败: {e}") from e

        # 解析响应
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.error(f"响应解析失败，原始内容: {raw[:500]}")
            raise RuntimeError("DashScope 返回了非 JSON 响应")

        if resp.status != 200:
            code = data.get("code", resp.status)
            msg  = data.get("message", "未知错误")
            logger.error(f"DashScope 错误 [{code}]: {msg}")
            raise RuntimeError(f"DashScope API 错误 [{code}]: {msg}")

        # 标准输出路径
        try:
            text = (
                data["output"]["choices"][0]["message"]["content"][0]["text"]
            )
            logger.debug(f"模型输出（前100字）: {text[:100]}")
            return text.strip()
        except (KeyError, IndexError, TypeError):
            # 兼容不同版本的响应结构
            try:
                text = data["output"]["text"]
                return text.strip()
            except Exception:
                logger.error(f"无法解析模型输出，完整响应: {raw[:1000]}")
                raise RuntimeError("无法从 DashScope 响应中提取文本")
