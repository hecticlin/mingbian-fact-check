# retrieval/retrieval_manager.py
# ============================================================
# 检索管理器：仅使用博查 API（已移除百度搜索）
# ============================================================

from typing import List, Dict, Optional

from retrieval.bocha_retriever import BochaRetriever
from utils.logger import get_logger

logger = get_logger("retrieval_manager")


class RetrievalManager:
    """
    统一检索入口，使用博查 API 进行检索。
    提供面向谣言检测场景的语义化接口。
    """

    def __init__(self, strategy: Optional[str] = None):
        # strategy 参数保留以兼容旧调用，实际只用博查
        self.bocha = BochaRetriever()

    # 场景化接口

    def retrieve_for_text_check(self, claim: str) -> List[Dict]:
        """文本事实核查检索：搜索核心声明是否有辟谣/核实信息。"""
        logger.info(f"[检索-文本核查] claim: {claim[:60]}...")
        results  = self.bocha.search_for_verification(claim)
        return self._merge_and_rank(results, max_keep=6)

    def retrieve_for_image_check(self, image_description: str) -> List[Dict]:
        """图像来源核查检索：搜索图片描述，寻找原始来源/断章取义线索。"""
        logger.info(f"[检索-图像核查] desc: {image_description[:60]}...")
        results = []
        for q in [image_description, f"{image_description} 原图 来源"]:
            results += self.bocha.search(q, max_results=3)
        return self._merge_and_rank(results, max_keep=6)

    def retrieve_for_consistency_check(
        self, text: str, image_description: str
    ) -> List[Dict]:
        """图文一致性核查检索：综合文本和图片描述搜索相关背景。"""
        logger.info("[检索-图文一致性]")
        query   = f"{text[:40]} {image_description[:40]}"
        results = self.bocha.search(query, max_results=4)
        return self._merge_and_rank(results, max_keep=6)

    def retrieve_general(self, query: str, max_results: int = 5) -> List[Dict]:
        """通用检索入口。"""
        results = self.bocha.search(query, max_results=max_results)
        return self._merge_and_rank(results, max_keep=max_results)

    # 内部工具

    def _merge_and_rank(self, results: List[Dict], max_keep: int) -> List[Dict]:
        """去重 + 截取前 N 条。"""
        seen_urls: set = set()
        unique = []
        for r in results:
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique.append(r)
            elif not url:
                unique.append(r)
        return unique[:max_keep]

    def format_results_as_context(self, results: List[Dict]) -> str:
        """将检索结果格式化为供 LVLM 阅读的纯文本上下文。"""
        if not results:
            return "（未检索到相关参考信息）"
        lines = []
        for i, r in enumerate(results, 1):
            title   = r.get("title",   "无标题")
            snippet = r.get("snippet", "无摘要")
            source  = r.get("source",  "unknown")
            url     = r.get("url",     "")
            lines.append(
                f"[参考{i}]（来源:{source}）{title}\n  摘要: {snippet}\n  链接: {url}"
            )
        return "\n\n".join(lines)