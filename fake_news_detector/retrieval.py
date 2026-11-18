"""
retrieval.py
基于 DeepSeek 联网搜索的国内新闻检索模块。

不依赖任何第三方搜索 API，直接通过 DeepSeek 的联网能力检索：
  - 优先命中辟谣平台（piyao.org.cn、腾讯较真等）
  - 其次命中权威媒体（人民网、新华网、央视等）
  - 通过提示词约束 DeepSeek 返回结构化的检索结果 + 分析

完全匹配原新闻时触发短路返回。
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from llm_client import call_deepseek_search_json
from config import (
    AUTHORITATIVE_MEDIA,
    FACT_CHECK_PLATFORMS,
    RECENCY_DAYS,
    MAX_SEARCH_RESULTS,
)

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class SearchResult:
    """单条检索结果（由 DeepSeek 联网搜索返回并解析）"""
    title: str = ""
    url: str = ""
    snippet: str = ""
    source_domain: str = ""
    publish_date: str = ""          # 字符串形式，如 "2024-03-15"
    is_authoritative: bool = False
    is_fact_check: bool = False
    relevance_score: float = 0.0


@dataclass
class RetrievalPackage:
    """一次检索任务的完整结果包"""
    query: str = ""
    results: list = field(default_factory=list)   # list[SearchResult]
    raw_analysis: str = ""
    authority_match: Optional[SearchResult] = None
    fact_check_match: Optional[SearchResult] = None
    direct_verdict: Optional[str] = None           # "真实"/"虚假"
    direct_reason: str = ""


# ============================================================
# 域名列表（注入提示词用）
# ============================================================
_AUTHORITY_DOMAINS = "、".join(AUTHORITATIVE_MEDIA[:6])
_FACT_CHECK_DOMAINS = "、".join(FACT_CHECK_PLATFORMS[:4])


# ============================================================
# 提示词模板
# ============================================================

RETRIEVAL_SEARCH_PROMPT = """
你是一位专业的中国新闻事实核查员。请联网搜索以下查询内容，重点在以下来源中寻找相关信息：

【辟谣平台（优先）】：{fact_check_domains}
【权威媒体（次之）】：{authority_domains}

查询内容：
{query}

搜索要求：
1. 优先搜索上述辟谣平台和权威媒体；
2. 重点关注最近 {recency_days} 天内的内容；
3. 严格区分"完全一致的同一事件"和"仅相似的不同事件"；
4. 若辟谣平台找到完全一致的核查结果，请在 exact_match_verdict 中填写；
5. 若权威媒体找到完全一致的官方报道，同上。

请严格按以下 JSON 格式输出，不要输出其他任何内容：
{{
  "results": [
    {{
      "title": "文章标题",
      "url": "文章链接",
      "snippet": "相关摘要（100字以内）",
      "source_domain": "来源域名",
      "publish_date": "发布日期如2024-03-15，不确定则填空字符串",
      "is_authoritative": true或false,
      "is_fact_check": true或false,
      "relevance_score": 0.0到1.0之间的数字
    }}
  ],
  "exact_match_found": true或false,
  "exact_match_index": null或results中完全匹配条目的索引数字,
  "exact_match_verdict": null或"真实"或"虚假",
  "exact_match_reason": "若找到完全匹配则说明依据，否则填null"
}}
"""


# ============================================================
# 主检索函数
# ============================================================

def retrieve_for_event(query: str, news_text: str = "") -> RetrievalPackage:
    """
    通过 DeepSeek 联网搜索检索与事件相关的权威信息。

    Args:
        query: 检索查询（通常是事件摘要）
        news_text: 原始新闻文本（用于辅助完全匹配检测）

    Returns:
        RetrievalPackage
    """
    package = RetrievalPackage(query=query)
    logger.info(f"DeepSeek联网检索: {query[:60]}...")

    prompt = RETRIEVAL_SEARCH_PROMPT.format(
        fact_check_domains=_FACT_CHECK_DOMAINS,
        authority_domains=_AUTHORITY_DOMAINS,
        query=query,
        recency_days=RECENCY_DAYS,
    )

    try:
        result_dict, citations = call_deepseek_search_json(prompt)
    except Exception as e:
        logger.error(f"DeepSeek联网检索失败: {e}")
        return package

    if "error" in result_dict:
        logger.warning(f"检索结果解析失败: {result_dict.get('raw', '')[:100]}")
        # 降级：把 citations 原始结果整理进去
        for c in (citations or [])[:MAX_SEARCH_RESULTS]:
            url = c.get("url", "")
            domain = url.split("/")[2].replace("www.", "") if "://" in url else ""
            package.results.append(SearchResult(
                title=c.get("title", ""),
                url=url,
                snippet=c.get("snippet", ""),
                source_domain=domain,
                is_authoritative=any(a in domain for a in AUTHORITATIVE_MEDIA),
                is_fact_check=any(f in domain for f in FACT_CHECK_PLATFORMS),
            ))
        return package

    # 解析 results 列表
    for item in (result_dict.get("results") or [])[:MAX_SEARCH_RESULTS]:
        sr = SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=item.get("snippet", ""),
            source_domain=item.get("source_domain", ""),
            publish_date=item.get("publish_date", ""),
            is_authoritative=bool(item.get("is_authoritative", False)),
            is_fact_check=bool(item.get("is_fact_check", False)),
            relevance_score=float(item.get("relevance_score", 0.0)),
        )
        package.results.append(sr)

    # 若 DeepSeek 没有返回 results 但有 citations，降级填充
    if not package.results and citations:
        for c in citations[:MAX_SEARCH_RESULTS]:
            url = c.get("url", "")
            domain = url.split("/")[2].replace("www.", "") if "://" in url else ""
            package.results.append(SearchResult(
                title=c.get("title", ""),
                url=url,
                snippet=c.get("snippet", ""),
                source_domain=domain,
                is_authoritative=any(a in domain for a in AUTHORITATIVE_MEDIA),
                is_fact_check=any(f in domain for f in FACT_CHECK_PLATFORMS),
            ))

    # 处理短路逻辑
    exact_match_found = result_dict.get("exact_match_found", False)
    exact_verdict = result_dict.get("exact_match_verdict")
    exact_reason = result_dict.get("exact_match_reason") or ""
    exact_index = result_dict.get("exact_match_index")

    if exact_match_found and exact_verdict in ("真实", "虚假"):
        package.direct_verdict = exact_verdict
        package.direct_reason = exact_reason
        if isinstance(exact_index, int) and 0 <= exact_index < len(package.results):
            matched = package.results[exact_index]
            if matched.is_fact_check:
                package.fact_check_match = matched
            else:
                package.authority_match = matched
        logger.info(f"检索短路命中，判定: {exact_verdict}")

    return package


def format_retrieved_info(package: RetrievalPackage) -> str:
    """将检索结果格式化为可注入提示词的文本。"""
    if not package.results:
        return "未找到相关检索信息。"

    lines = [f"检索查询：{package.query}\n"]
    for i, r in enumerate(package.results, 1):
        src_tag = "【辟谣平台】" if r.is_fact_check else ("【权威媒体】" if r.is_authoritative else "")
        date_str = r.publish_date if r.publish_date else "日期未知"
        lines.append(
            f"{i}. {src_tag}{r.title}（{r.source_domain}，{date_str}）\n"
            f"   {r.snippet}"
        )
    return "\n".join(lines)