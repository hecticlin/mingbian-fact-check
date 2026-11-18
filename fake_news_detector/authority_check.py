"""
authority_check.py
权威媒体与辟谣平台直接核查模块（时效性优先，基于 DeepSeek 联网搜索）。

核心逻辑：
1. 对原始新闻进行 DeepSeek 联网专项核查；
2. 若找到与原新闻完全一致的辟谣平台记录 → 直接短路输出结论；
3. 若找到权威媒体（第一级）完全匹配的官方报道 → 同样短路；
4. 若找到可信官方信源（第二级，如 edu.cn / gov.cn）的原文 →
       不短路，但将原文信息作为"可信佐证"返回，由 detector.py 注入
       verified_facts，在后续三层分析中提升真实判断的置信度；
5. 时效性新闻额外限制检索时间窗口，避免用旧信息误判新事件。

信源可信度分级：
  第一级 AUTHORITATIVE_MEDIA + FACT_CHECK_PLATFORMS → 短路
  第二级 TRUSTED_OFFICIAL_SOURCES (edu.cn/gov.cn等)  → 佐证注入
  其他                                                → 仅作参考
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from llm_client import call_deepseek_search_json
from config import (
    AUTHORITATIVE_MEDIA, FACT_CHECK_PLATFORMS,
    TRUSTED_OFFICIAL_SOURCES,
    STRICT_RECENCY_DAYS, RECENCY_DAYS,
)

logger = logging.getLogger(__name__)


@dataclass
class AuthorityCheckResult:
    """权威核查结果"""
    # 第一级：直接短路
    short_circuit: bool = False
    verdict: Optional[str] = None      # "真实" / "虚假"
    reason: str = ""
    source_url: str = ""
    source_name: str = ""

    # 第二级：可信佐证（不短路，注入 verified_facts）
    trusted_source_found: bool = False
    trusted_source_name: str = ""
    trusted_source_url: str = ""
    trusted_source_content: str = ""   # 原文摘要，供注入提示词

    # 通用
    is_time_sensitive: bool = False


# ============================================================
# 时效性新闻关键词检测
# ============================================================

_TIME_KEYWORDS = [
    "今日", "今天", "昨日", "昨天", "本周", "上周",
    "刚刚", "最新", "突发", "紧急", "刚才",
    "小时前", "分钟前", "今晨", "今晚",
]

def _is_time_sensitive(news_text: str) -> bool:
    return any(kw in news_text for kw in _TIME_KEYWORDS)


def _is_trusted_official(domain: str) -> bool:
    """判断域名是否属于第二级可信官方信源。"""
    if not domain:
        return False
    return any(domain.endswith(t) or t in domain for t in TRUSTED_OFFICIAL_SOURCES)


# ============================================================
# DeepSeek 联网核查提示词（合并两级信源搜索）
# ============================================================

_AUTHORITY_DOMAINS    = "、".join(AUTHORITATIVE_MEDIA[:6])
_FACT_CHECK_DOMAINS   = "、".join(FACT_CHECK_PLATFORMS[:4])
_TRUSTED_DOMAINS      = "、".join(
    [s for s in TRUSTED_OFFICIAL_SOURCES if not s.startswith("#")][:6]
)

AUTHORITY_SEARCH_PROMPT = """
你是一位专业的中国新闻事实核查员。请联网搜索并核查以下新闻的真实性。

【待核查新闻】（前150字）：
{news_head}

搜索策略（按优先级）：
1. 辟谣平台（最高优先级）：{fact_check_domains}
2. 权威媒体：{authority_domains}
3. 可信官方信源（高校/政府机构等）：{trusted_domains}
4. 时间范围：优先最近 {recency_days} 天内的结果{time_sensitive_note}

判断标准（非常重要）：
- 必须是与上述新闻【完全一致】的同一事件才能作为判断依据；
- "完全一致"需满足：核心人物相同、核心事件相同、时间地点大致相符；
- 仅话题相似但细节不同的报道，不能作为同一条新闻的依据；

短路判断标准（can_short_circuit=true 的条件）：
- 辟谣平台明确标注为谣言/虚假/不实 → 判定【虚假】，可以短路；
- 权威媒体（第1/2类来源）发布了完全一致的官方报道 → 判定【真实】，可以短路；

佐证判断标准（trusted_source_found=true 的条件，不短路）：
- 高校官网(.edu.cn)、政府部门(.gov.cn)、科研机构等可信官方信源，
  发布了与该新闻内容完全一致的原文 → 填写 trusted_source 相关字段；
- 注意：此类信源仅作佐证，不能直接短路，系统会继续进行完整核查。

请严格按以下 JSON 格式输出，不要输出其他任何内容：
{{
  "can_short_circuit": true或false,
  "verdict": "真实"或"虚假"或null,
  "confidence": "高"或"中"或"低"或null,
  "matched_title": "匹配到的文章标题，找不到则填null",
  "matched_url": "匹配到的文章链接，找不到则填null",
  "matched_source": "来源名称，找不到则填null",
  "is_fact_check_source": true或false,
  "is_exact_same_news": true或false,
  "reason": "为何认定是同一条新闻，以及判断真假的具体依据",
  "trusted_source_found": true或false,
  "trusted_source_name": "可信官方信源名称（如：北京大学官网），找不到则填null",
  "trusted_source_url": "可信官方信源链接，找不到则填null",
  "trusted_source_content": "原文关键内容摘要（100字内），找不到则填null"
}}
"""


# ============================================================
# 主函数
# ============================================================

def authority_check(news_text: str) -> AuthorityCheckResult:
    """
    对原始新闻进行权威媒体 + 辟谣平台 + 可信官方信源快速核查。

    Returns:
        AuthorityCheckResult
          - short_circuit=True  → 直接使用结论，跳过后续三层分析（第一级命中）
          - trusted_source_found=True, short_circuit=False
                                → 继续三层分析，但 detector.py 会将
                                  trusted_source_content 注入 verified_facts
    """
    result = AuthorityCheckResult()
    result.is_time_sensitive = _is_time_sensitive(news_text)

    time_sensitive_note = ""
    recency_days = RECENCY_DAYS
    if result.is_time_sensitive:
        recency_days = STRICT_RECENCY_DAYS
        time_sensitive_note = f"（该新闻含时效性关键词，请严格限制在 {STRICT_RECENCY_DAYS} 天内）"
        logger.info("检测到时效性新闻，启用严格时效过滤")

    news_head = news_text[:150].strip()
    logger.info("Step 0: DeepSeek联网权威核查（含可信官方信源检测）...")

    prompt = AUTHORITY_SEARCH_PROMPT.format(
        news_head=news_head,
        fact_check_domains=_FACT_CHECK_DOMAINS,
        authority_domains=_AUTHORITY_DOMAINS,
        trusted_domains=_TRUSTED_DOMAINS,
        recency_days=recency_days,
        time_sensitive_note=time_sensitive_note,
    )

    try:
        check_result, citations = call_deepseek_search_json(prompt)
    except Exception as e:
        logger.error(f"权威核查失败: {e}")
        return result

    if "error" in check_result:
        logger.warning(f"权威核查结果解析失败，跳过: {check_result.get('raw', '')[:100]}")
        return result

    # 第一级：短路判断
    can_short_circuit = check_result.get("can_short_circuit", False)
    verdict    = check_result.get("verdict")
    is_exact   = check_result.get("is_exact_same_news", False)
    confidence = check_result.get("confidence", "低")

    if can_short_circuit and is_exact and verdict in ("真实", "虚假") and confidence in ("高", "中"):
        result.short_circuit = True
        result.verdict     = verdict
        result.source_url  = check_result.get("matched_url") or ""
        result.source_name = check_result.get("matched_source") or ""
        result.reason = (
            f"权威核查直接命中（{result.source_name}）\n"
            f"匹配文章：{check_result.get('matched_title', '未知')}\n"
            f"链接：{result.source_url}\n"
            f"判断依据：{check_result.get('reason', '')}"
        )
        logger.info(f"第一级短路命中，判定: {verdict}（置信度: {confidence}）")
        return result   # 第一级命中直接返回，无需检查第二级

    # 第一级未命中，记录原因
    if can_short_circuit and not is_exact:
        logger.info("找到相关报道但非同一条新闻，不触发短路，继续三层分析")
    elif not can_short_circuit:
        logger.info("权威核查未找到完全一致来源，继续三层分析")
    else:
        logger.info(f"权威核查置信度过低（{confidence}），继续三层分析")

    # 第二级：可信官方信源佐证
    trusted_found   = check_result.get("trusted_source_found", False)
    trusted_name    = check_result.get("trusted_source_name") or ""
    trusted_url     = check_result.get("trusted_source_url") or ""
    trusted_content = check_result.get("trusted_source_content") or ""

    # 兜底：若 DeepSeek 未识别但 citations 中有 edu.cn/gov.cn 链接，自动补充
    if not trusted_found and citations:
        for c in citations:
            url = c.get("url", "")
            domain = url.split("/")[2].replace("www.", "") if "://" in url else ""
            if _is_trusted_official(domain):
                trusted_found   = True
                trusted_name    = trusted_name or domain
                trusted_url     = trusted_url  or url
                trusted_content = trusted_content or c.get("snippet", "")
                logger.info(f"从 citations 兜底识别到可信官方信源: {domain}")
                break

    if trusted_found and trusted_content:
        result.trusted_source_found   = True
        result.trusted_source_name    = trusted_name
        result.trusted_source_url     = trusted_url
        result.trusted_source_content = trusted_content
        logger.info(
            f"第二级：找到可信官方信源原文 ({trusted_name})，"
            "将作为佐证注入后续分析，不短路"
        )
    else:
        logger.info("未找到可信官方信源原文，继续常规三层分析")

    return result