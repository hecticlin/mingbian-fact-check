"""
llm_client.py
DeepSeek 模型统一调用封装。

职责：
  - 普通文本调用（call_llm）
  - JSON 解析调用（call_llm_json）
  - 联网搜索调用（call_deepseek_search / call_deepseek_search_json）

所有调用均使用 DeepSeek，不再依赖任何其他模型。
"""

import json
import logging
import re
import time
from typing import Optional
from openai import OpenAI

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    MAX_TOKENS,
    TEMPERATURE,
)

logger = logging.getLogger(__name__)

# ============================================================
# 客户端单例
# ============================================================

_client: Optional[OpenAI] = None

def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
    return _client


# ============================================================
# 内部：JSON 清洗与解析
# ============================================================

def _parse_json(raw: str) -> dict:
    """从模型输出中提取并解析 JSON，自动剥离 markdown 代码块。"""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw)
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON解析失败: {e} | 原始输出前200字: {raw[:200]}")
        return {"error": str(e), "raw": raw}


# ============================================================
# 普通调用（无联网）
# ============================================================

def call_llm(
    prompt: str,
    system_prompt: str = "你是一位专业的新闻真实性核查专家，擅长逻辑推理和事实核查。",
    temperature: float = TEMPERATURE,
    max_tokens: int = MAX_TOKENS,
    retry: int = 2,
) -> str:
    """
    调用 DeepSeek，返回文本响应。

    Args:
        prompt:        用户提示词
        system_prompt: 系统提示词
        temperature:   温度参数
        max_tokens:    最大输出 token 数
        retry:         失败重试次数（指数退避）
    """
    client = _get_client()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": prompt},
    ]
    for attempt in range(retry + 1):
        try:
            resp = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = resp.choices[0].message.content or ""
            logger.debug(f"[DeepSeek] 响应长度: {len(content)} chars")
            return content.strip()
        except Exception as e:
            logger.warning(f"[DeepSeek] 第{attempt + 1}次调用失败: {e}")
            if attempt < retry:
                time.sleep(2 ** attempt)
            else:
                raise RuntimeError(f"[DeepSeek] 调用失败，已重试{retry}次: {e}") from e
    return ""


def call_llm_json(
    prompt: str,
    system_prompt: str = (
        "你是一位专业的新闻真实性核查专家。"
        "请严格按照要求的 JSON 格式输出，不要添加任何额外说明。"
    ),
    **kwargs,
) -> dict:
    """调用 DeepSeek 并解析 JSON 响应。"""
    raw = call_llm(prompt, system_prompt, **kwargs)
    return _parse_json(raw)


# ============================================================
# 联网搜索调用（DeepSeek search_type="pro"）
# ============================================================

def call_deepseek_search(
    prompt: str,
    system_prompt: str = (
        "你是一位专业的中国新闻事实核查员，擅长利用网络信息核查新闻真实性。"
    ),
    max_tokens: int = MAX_TOKENS,
    retry: int = 2,
) -> tuple[str, list[dict]]:
    """
    调用 DeepSeek 联网深度搜索（extra_body={"search_type": "pro"}）。
    DeepSeek 会自动联网检索并将结果整合进回答。

    Returns:
        (回答文本, 引用来源列表)
        引用来源列表：[{"title": ..., "url": ..., "snippet": ...}]
    """
    client = _get_client()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": prompt},
    ]
    for attempt in range(retry + 1):
        try:
            resp = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=messages,
                max_tokens=max_tokens,
                temperature=TEMPERATURE,
                extra_body={"search_type": "pro"},  # 开启联网深度搜索
            )
            content = resp.choices[0].message.content or ""

            # 提取引用来源
            citations: list[dict] = []
            raw_resp = resp.model_dump()
            search_results = (
                raw_resp.get("search_results")
                or raw_resp.get("choices", [{}])[0].get("search_results")
                or []
            )
            for sr in search_results:
                citations.append({
                    "title":   sr.get("title", ""),
                    "url":     sr.get("url", ""),
                    "snippet": sr.get("content", sr.get("snippet", "")),
                })

            logger.debug(
                f"[DeepSeek-Search] 回答长度: {len(content)} chars，"
                f"引用来源: {len(citations)} 条"
            )
            return content.strip(), citations

        except Exception as e:
            logger.warning(f"[DeepSeek-Search] 第{attempt + 1}次调用失败: {e}")
            if attempt < retry:
                time.sleep(2 ** attempt)
            else:
                raise RuntimeError(
                    f"[DeepSeek-Search] 调用失败，已重试{retry}次: {e}"
                ) from e
    return "", []


def call_deepseek_search_json(
    prompt: str,
    system_prompt: str = (
        "你是一位专业的新闻事实核查员。"
        "请严格按照要求的 JSON 格式输出，不要输出其他任何内容。"
    ),
    **kwargs,
) -> tuple[dict, list[dict]]:
    """
    联网搜索 + JSON 解析一体化接口。

    Returns:
        (解析后的 dict, 引用来源列表)
    """
    raw_text, citations = call_deepseek_search(prompt, system_prompt, **kwargs)
    return _parse_json(raw_text), citations