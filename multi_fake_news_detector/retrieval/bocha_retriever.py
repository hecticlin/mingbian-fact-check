# retrieval/bocha_retriever.py
# ============================================================
# 博查搜索 API 封装（专为国内 AI 应用设计）
# ============================================================

import json
import http.client
import urllib.parse
from typing import List, Dict, Optional

from config.settings import (
    BOCHA_API_KEY,
    BOCHA_API_URL,
    BOCHA_MAX_RESULTS,
    REQUEST_TIMEOUT,
)
from utils.logger import get_logger

logger = get_logger("bocha_retriever")


class BochaRetriever:
    """
    博查 AI 搜索 API 封装。
    适合检索：微博/头条/抖音等国内平台内容，谣言辟谣信息。
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or BOCHA_API_KEY
        if self.api_key == "YOUR_BOCHA_API_KEY":
            logger.warning("  博查 API Key 未配置，请在 config/settings.py 中填写")

    def search(self, query: str, max_results: int = BOCHA_MAX_RESULTS) -> List[Dict]:
        """
        执行搜索，返回结果列表。
        每条结果包含：title, url, snippet, source。
        """
        logger.info(f"[博查] 搜索: {query}")

        parsed = urllib.parse.urlparse(BOCHA_API_URL)
        host = parsed.netloc
        path = parsed.path

        payload = json.dumps(
            {
                "query": query,
                "freshness": "noLimit",
                "summary": True,
                "count": max_results,
            },
            ensure_ascii=False,
        ).encode("utf-8")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            conn = http.client.HTTPSConnection(host, timeout=REQUEST_TIMEOUT)
            conn.request("POST", path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
            conn.close()
        except Exception as e:
            logger.error(f"博查请求失败: {e}")
            return []

        if resp.status != 200:
            logger.error(f"博查返回错误 [{resp.status}]: {raw[:300]}")
            return []

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.error(f"博查响应解析失败: {raw[:300]}")
            return []

        return self._parse_results(data)

    def search_for_verification(self, claim: str) -> List[Dict]:
        """
        针对事实核查场景的搜索：自动拼接辟谣关键词。
        """
        queries = [
            f"{claim} 辟谣",
            f"{claim} 真相",
            f"{claim} 事实核查",
        ]
        all_results = []
        for q in queries[:2]:   # 控制 API 调用次数
            results = self.search(q, max_results=3)
            all_results.extend(results)
        return self._deduplicate(all_results)

    # 内部工具

    def _parse_results(self, data: dict) -> List[Dict]:
        results = []
        try:
            web_pages = data.get("data", {}).get("webPages", {}).get("value", [])
            for item in web_pages:
                results.append(
                    {
                        "title":   item.get("name", ""),
                        "url":     item.get("url", ""),
                        "snippet": item.get("snippet", ""),
                        "source":  "bocha",
                    }
                )
        except Exception as e:
            logger.warning(f"博查结果解析异常: {e}")
        logger.info(f"[博查] 获得 {len(results)} 条结果")
        return results

    def _deduplicate(self, results: List[Dict]) -> List[Dict]:
        seen_urls = set()
        unique = []
        for r in results:
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique.append(r)
        return unique
