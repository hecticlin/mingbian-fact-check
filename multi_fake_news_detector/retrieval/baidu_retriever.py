# retrieval/baidu_retriever.py
# ============================================================
# 百度 AI 搜索 API 封装（千帆 AppBuilder）
# ============================================================

import json
import http.client
import urllib.parse
from typing import List, Dict, Optional

from config.settings import (
    BAIDU_API_KEY,
    BAIDU_SECRET_KEY,
    BAIDU_MAX_RESULTS,
    REQUEST_TIMEOUT,
)
from utils.logger import get_logger

logger = get_logger("baidu_retriever")

# 百度鉴权 + 搜索端点
TOKEN_URL    = "aip.baidubce.com"
TOKEN_PATH   = "/oauth/2.0/token"
SEARCH_HOST  = "aip.baidubce.com"
SEARCH_PATH  = "/rpc/2.0/nlp/v1/search"   # 百度 AI 搜索接口


class BaiduRetriever:
    """
    百度 AI 搜索 API 封装。
    适合检索：百科、权威新闻、政府公告等高可信内容。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
    ):
        self.api_key    = api_key    or BAIDU_API_KEY
        self.secret_key = secret_key or BAIDU_SECRET_KEY
        self._access_token: Optional[str] = None

        if self.api_key == "YOUR_BAIDU_API_KEY":
            logger.warning("  百度 API Key 未配置，请在 config/settings.py 中填写")

    # 公开接口

    def search(self, query: str, max_results: int = BAIDU_MAX_RESULTS) -> List[Dict]:
        """执行百度 AI 搜索，返回结构化结果列表。"""
        logger.info(f"[百度] 搜索: {query}")

        token = self._get_access_token()
        if not token:
            logger.error("百度 Access Token 获取失败，跳过百度搜索")
            return []

        # 百度搜索 API（使用文心 ERNIE + 联网搜索）
        payload = json.dumps(
            {"query": query, "scope": 3},
            ensure_ascii=False,
        ).encode("utf-8")

        path_with_token = f"{SEARCH_PATH}?access_token={token}"
        headers = {"Content-Type": "application/json"}

        try:
            conn = http.client.HTTPSConnection(SEARCH_HOST, timeout=REQUEST_TIMEOUT)
            conn.request("POST", path_with_token, body=payload, headers=headers)
            resp = conn.getresponse()
            raw  = resp.read().decode("utf-8")
            conn.close()
        except Exception as e:
            logger.error(f"百度搜索请求失败: {e}")
            return []

        if resp.status != 200:
            logger.error(f"百度搜索返回错误 [{resp.status}]: {raw[:300]}")
            return []

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.error(f"百度搜索响应解析失败: {raw[:300]}")
            return []

        return self._parse_results(data, max_results)

    def search_baike(self, keyword: str) -> List[Dict]:
        """专门搜索百度百科，用于实体事实核查。"""
        return self.search(f"site:baike.baidu.com {keyword}", max_results=3)

    # 鉴权

    def _get_access_token(self) -> Optional[str]:
        """获取百度 OAuth Access Token（带缓存）。"""
        if self._access_token:
            return self._access_token

        params = urllib.parse.urlencode(
            {
                "grant_type":    "client_credentials",
                "client_id":     self.api_key,
                "client_secret": self.secret_key,
            }
        )
        try:
            conn = http.client.HTTPSConnection(TOKEN_URL, timeout=REQUEST_TIMEOUT)
            conn.request(
                "POST",
                f"{TOKEN_PATH}?{params}",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp = conn.getresponse()
            raw  = resp.read().decode("utf-8")
            conn.close()
            data = json.loads(raw)
            self._access_token = data.get("access_token")
            if self._access_token:
                logger.debug("百度 Access Token 获取成功")
            else:
                logger.error(f"百度 Token 响应异常: {raw[:200]}")
        except Exception as e:
            logger.error(f"百度 Token 请求失败: {e}")

        return self._access_token

    # 结果解析

    def _parse_results(self, data: dict, max_results: int) -> List[Dict]:
        results = []
        try:
            items = data.get("results", [])[:max_results]
            for item in items:
                results.append(
                    {
                        "title":   item.get("title", ""),
                        "url":     item.get("url", ""),
                        "snippet": item.get("content", "") or item.get("summary", ""),
                        "source":  "baidu",
                    }
                )
        except Exception as e:
            logger.warning(f"百度结果解析异常: {e}")
        logger.info(f"[百度] 获得 {len(results)} 条结果")
        return results
