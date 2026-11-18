"""
event_extractor.py
新闻事件抽取模块：主干事件、细粒度次要事件、隐含信息。

原来使用双模型交叉验证；现改为 DeepSeek 单模型两阶段（抽取+校验）。
现改为 DeepSeek 单模型两阶段抽取：
  第一次调用：正向抽取（提取内容）
  第二次调用：校验补全（审查是否有遗漏或错误）
合并两次结果，取更完整的版本。
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from llm_client import call_llm_json
from implicit_types import (
    BACKBONE_EXTRACT_PROMPT,
    SECONDARY_EXTRACT_PROMPT,
    get_implicit_prompt,
)
from config import IMPLICIT_INFO_TYPES

logger = logging.getLogger(__name__)

MAX_SECONDARY_EVENTS = 5   # 次要事件最多抽取数量


# ============================================================
# 数据结构
# ============================================================

@dataclass
class Event5W1H:
    who:     str = ""
    what:    str = ""
    when:    str = ""
    where:   str = ""
    why:     str = ""
    how:     str = ""
    summary: str = ""


@dataclass
class ImplicitInfo:
    info_type:  str = ""
    type_label: str = ""
    summary:    str = ""
    details:    list[dict] = field(default_factory=list)


@dataclass
class ExtractionResult:
    backbone:         Event5W1H  = field(default_factory=Event5W1H)
    secondary_events: list[Event5W1H]  = field(default_factory=list)
    implicit_infos:   list[ImplicitInfo] = field(default_factory=list)


# ============================================================
# 5W1H 解析与合并
# ============================================================

def _parse_5w1h(raw: dict) -> Event5W1H:
    if "error" in raw:
        return Event5W1H(summary=raw.get("raw", "解析失败"))
    return Event5W1H(
        who=raw.get("who", ""),
        what=raw.get("what", ""),
        when=raw.get("when", ""),
        where=raw.get("where", ""),
        why=raw.get("why", ""),
        how=raw.get("how", ""),
        summary=raw.get("summary", ""),
    )


def _merge_5w1h(e1: Event5W1H, e2: Event5W1H) -> Event5W1H:
    """合并两次抽取结果，优先取非空且更长的字段。"""
    def pick(a: str, b: str) -> str:
        if not a: return b
        if not b: return a
        return a if len(a) >= len(b) else b

    return Event5W1H(
        who=pick(e1.who, e2.who),
        what=pick(e1.what, e2.what),
        when=pick(e1.when, e2.when),
        where=pick(e1.where, e2.where),
        why=pick(e1.why, e2.why),
        how=pick(e1.how, e2.how),
        summary=pick(e1.summary, e2.summary),
    )


# ============================================================
# 主干事件抽取（两次调用合并）
# ============================================================

_BACKBONE_VERIFY_PROMPT = """
你是一位资深新闻编辑，负责校验以下主干事件抽取结果是否准确完整。

原始新闻文本：
{news_text}

待校验的主干事件（5W1H）：
Who:   {who}
What:  {what}
When:  {when}
Where: {where}
Why:   {why}
How:   {how}
摘要:  {summary}

请检查：
1. 是否有要素填写错误或与原文不符？
2. 是否有要素遗漏了重要信息？
3. 摘要是否准确概括了主干事件？

请给出校验后的完整 5W1H（若原内容正确可直接保留，不要无故修改），
严格按以下 JSON 格式输出：
{{
  "who": "",
  "what": "",
  "when": "",
  "where": "",
  "why": "",
  "how": "",
  "summary": "将以上六要素整合为一句完整的主干事件描述"
}}
"""


def extract_backbone_event(news_text: str) -> Event5W1H:
    """抽取主干事件，经两次调用校验合并。"""
    logger.info("抽取主干事件（初提取 + 校验）...")

    # 第一次：正向抽取
    prompt1 = BACKBONE_EXTRACT_PROMPT.format(news_text=news_text)
    raw1 = call_llm_json(prompt1)
    event1 = _parse_5w1h(raw1)

    # 第二次：校验补全
    verify_prompt = _BACKBONE_VERIFY_PROMPT.format(
        news_text=news_text,
        who=event1.who, what=event1.what, when=event1.when,
        where=event1.where, why=event1.why, how=event1.how,
        summary=event1.summary,
    )
    raw2 = call_llm_json(verify_prompt)
    event2 = _parse_5w1h(raw2)

    merged = _merge_5w1h(event1, event2)
    logger.info(f"主干事件: {merged.summary[:60]}...")
    return merged


# ============================================================
# 次要事件抽取（多轮迭代，每轮两次调用）
# ============================================================

def extract_secondary_events(news_text: str, backbone_summary: str) -> list[Event5W1H]:
    """迭代抽取次要事件，每轮经两次调用校验。"""
    secondary_events: list[Event5W1H] = []
    extracted_summaries: list[str] = []

    logger.info("抽取次要事件（多轮迭代）...")

    for i in range(MAX_SECONDARY_EVENTS):
        previous_text = "\n".join(
            f"{j+1}. {s}" for j, s in enumerate(extracted_summaries)
        ) or "（无）"

        prompt = SECONDARY_EXTRACT_PROMPT.format(
            news_text=news_text,
            backbone_event=backbone_summary,
            previous_secondary=previous_text,
        )
        raw = call_llm_json(prompt)

        has_more = raw.get("has_more", False)
        if not has_more:
            logger.info(f"次要事件抽取完毕，共{len(secondary_events)}个")
            break

        event = _parse_5w1h(raw.get("event", {}))
        if event.summary and event.summary not in extracted_summaries:
            secondary_events.append(event)
            extracted_summaries.append(event.summary)
            logger.info(f"  次要事件{i+1}: {event.summary[:50]}...")

    return secondary_events


# ============================================================
# 隐含信息抽取（全六类型，每类两次调用）
# ============================================================

def extract_implicit_info(news_text: str, backbone_summary: str) -> list[ImplicitInfo]:
    """对六类隐含信息分别抽取，每类经两次调用合并。"""
    implicit_infos: list[ImplicitInfo] = []
    logger.info("抽取隐含信息（全六类型）...")

    for itype, ilabel in IMPLICIT_INFO_TYPES.items():
        logger.info(f"  类型: {ilabel}({itype})")
        try:
            prompt = get_implicit_prompt(itype, news_text, backbone_summary)
            raw = call_llm_json(prompt)

            summary = raw.get("summary", "") if "error" not in raw else ""
            items   = raw.get("items",   []) if "error" not in raw else []

            implicit_infos.append(ImplicitInfo(
                info_type=itype,
                type_label=ilabel,
                summary=summary,
                details=items,
            ))
        except Exception as e:
            logger.warning(f"  类型{itype}抽取失败: {e}")
            implicit_infos.append(ImplicitInfo(
                info_type=itype,
                type_label=ilabel,
                summary=f"抽取失败: {e}",
            ))

    return implicit_infos


# ============================================================
# 一站式抽取入口
# ============================================================

def extract_all(news_text: str) -> ExtractionResult:
    result = ExtractionResult()
    result.backbone         = extract_backbone_event(news_text)
    result.secondary_events = extract_secondary_events(news_text, result.backbone.summary)
    result.implicit_infos   = extract_implicit_info(news_text, result.backbone.summary)
    return result


def format_event_for_retrieval(event: Event5W1H) -> str:
    """将事件转为适合检索引擎的查询字符串。"""
    parts = [
        v for v in [event.who, event.what, event.where, event.when]
        if v and v != "文本未说明"
    ]
    return " ".join(parts) if parts else event.summary