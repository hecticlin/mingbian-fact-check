"""
detector.py
虚假新闻检测主流程编排。

完整流程：
Step 0: 权威媒体直接核查（时效性优先短路）
        + 可信官方信源佐证注入（新增）
Step 1: 三层事件抽取（主干 + 次要 + 隐含）
Step 2: 各事件检索外部知识
Step 3: 层级递进式DeepSeek两阶段判断
  3a: 主干事件判断
  3b: 次要事件逐一判断（任一为假则终止）
  3c: 隐含信息判断
Step 4: 汇总最终结论
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from authority_check import authority_check, AuthorityCheckResult
from event_extractor import extract_all, ExtractionResult, format_event_for_retrieval, Event5W1H, ImplicitInfo
from retrieval import retrieve_for_event, format_retrieved_info
from dual_model import judge, format_result
from implicit_types import JUDGMENT_PROMPT

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class BranchResult:
    """单个分支（主干/次要事件/隐含信息）的判断结果"""
    branch_type: str                        # "backbone" / "secondary" / "implicit"
    branch_label: str                       # 可读标签
    event_summary: str                      # 被判断的内容描述
    verdict: str                            # "真实" / "虚假" / "无法判断" / "分歧未解决"
    reason: str                             # 判断理由
    agreed: bool = True                     # 双模型是否达成一致
    deepseek_verdict: str = ""
    deepseek_reason: str = ""
    review_verdict: str = ""
    review_reason: str = ""
    debate_rounds: int = 0


@dataclass
class DetectionResult:
    """完整检测结果"""
    news_text: str
    final_verdict: str = "无法判断"          # "真实" / "虚假" / "无法判断"
    final_reason: str = ""
    short_circuited: bool = False            # 是否第一级权威短路
    short_circuit_source: str = ""

    # 新增：第二级可信官方信源佐证
    trusted_source_injected: bool = False    # 是否注入了官方信源佐证
    trusted_source_name: str = ""           # 信源名称（如：北京大学官网）
    trusted_source_url: str = ""            # 信源链接

    branch_results: list[BranchResult] = field(default_factory=list)
    extraction: Optional[ExtractionResult] = None

    # 统计
    total_branches: int = 0
    fake_branches: int = 0
    undecided_branches: int = 0


# ============================================================
# 单个事件的判断（含检索 + 双模型协商）
# ============================================================

def _judge_event(
    event_summary: str,
    news_text: str,
    verified_facts: str,
    branch_type: str,
    branch_label: str,
) -> BranchResult:
    """
    对单个事件/隐含信息进行检索 + DeepSeek两阶段判断。
    """
    logger.info(f"判断分支: {branch_label} | 内容: {event_summary[:50]}...")

    # 检索外部知识
    pkg = retrieve_for_event(query=event_summary, news_text=news_text)
    retrieved_text = format_retrieved_info(pkg)

    # 构建判断提示词
    judgment_prompt = JUDGMENT_PROMPT.format(
        event_description=event_summary,
        retrieved_info=retrieved_text,
        verified_facts=verified_facts if verified_facts else "（暂无已核实事实）",
    )

    # 双模型协商
    dual_result = judge(
        judgment_prompt=judgment_prompt,
        event_description=event_summary,
        retrieved_info=retrieved_text,
    )
    formatted = format_result(dual_result)

    branch = BranchResult(
        branch_type=branch_type,
        branch_label=branch_label,
        event_summary=event_summary,
        verdict=formatted.get("verdict", "无法判断"),
        reason=formatted.get("reason", ""),
        agreed=formatted.get("agreed", False),
        debate_rounds=formatted.get("debate_rounds", 0),
    )

    if not formatted.get("agreed"):
        initial_info = formatted.get("initial", {})
        review_info  = formatted.get("review", {})
        branch.deepseek_verdict = initial_info.get("verdict", "")
        branch.deepseek_reason  = initial_info.get("reason", "")
        branch.review_verdict   = review_info.get("verdict", "")
        branch.review_reason    = review_info.get("reason", "")

    return branch


# ============================================================
# 主检测流程
# ============================================================

def detect(news_text: str) -> DetectionResult:
    """
    对一条新闻进行完整的虚假检测。

    Args:
        news_text: 新闻正文文本

    Returns:
        DetectionResult
    """
    result = DetectionResult(news_text=news_text)

    # ================================================================
    # Step 0: 权威核查 + 可信官方信源佐证
    # ================================================================
    logger.info("=" * 60)
    logger.info("Step 0: 权威媒体直接核查 + 可信官方信源检测")
    auth_result: AuthorityCheckResult = authority_check(news_text)

    # 第一级：短路返回
    if auth_result.short_circuit:
        result.short_circuited     = True
        result.final_verdict       = auth_result.verdict
        result.final_reason        = auth_result.reason
        result.short_circuit_source = auth_result.source_name
        logger.info(f"第一级短路触发！最终判定: {result.final_verdict}")
        return result

    # 第二级：可信官方信源佐证注入
    # 将官方信源原文以"已核实背景"的形式注入 verified_facts_list，
    # 使后续每个分支的判断都能感知到"官方已有相同内容发布"这一有利证据。
    # 注意：这不等于免检——三层分析仍会独立核查所有事实细节。
    verified_facts_list: list[str] = []

    if auth_result.trusted_source_found:
        result.trusted_source_injected = True
        result.trusted_source_name     = auth_result.trusted_source_name
        result.trusted_source_url      = auth_result.trusted_source_url

        inject_note = (
            f"【可信官方信源已找到原文】\n"
            f"  来源：{auth_result.trusted_source_name}（{auth_result.trusted_source_url}）\n"
            f"  摘要：{auth_result.trusted_source_content}\n"
            f"  说明：上述官方机构已发布与待检测新闻一致的内容，"
            f"可作为真实性的有利佐证，但仍需核实具体细节。"
        )
        verified_facts_list.append(inject_note)
        logger.info(
            f"第二级佐证已注入 verified_facts: {auth_result.trusted_source_name}"
        )

    # ================================================================
    # Step 1: 三层事件抽取
    # ================================================================
    logger.info("Step 1: 三层事件抽取（双模型）")
    extraction: ExtractionResult = extract_all(news_text)
    result.extraction = extraction

    # ================================================================
    # Step 2 & 3: 层级递进式判断
    # ================================================================

    # 3a: 主干事件判断
    logger.info("Step 2a: 主干事件判断")
    backbone_summary = extraction.backbone.summary
    backbone_branch = _judge_event(
        event_summary=backbone_summary,
        news_text=news_text,
        verified_facts="\n".join(verified_facts_list),
        branch_type="backbone",
        branch_label="主干事件",
    )
    result.branch_results.append(backbone_branch)
    result.total_branches += 1

    if backbone_branch.verdict == "虚假":
        result.fake_branches += 1
        logger.info("主干事件判定为虚假，直接终止")
        result.final_verdict = "虚假"
        result.final_reason  = f"主干事件存在虚假信息：{backbone_branch.reason}"
        return result
    elif backbone_branch.verdict == "真实":
        verified_facts_list.append(f"主干事件（已核实为真）：{backbone_summary}")

    # 3b: 次要事件逐一判断
    logger.info(f"Step 2b: 次要事件判断（共{len(extraction.secondary_events)}个）")
    for i, sec_event in enumerate(extraction.secondary_events):
        if not sec_event.summary:
            continue
        branch = _judge_event(
            event_summary=sec_event.summary,
            news_text=news_text,
            verified_facts="\n".join(verified_facts_list),
            branch_type="secondary",
            branch_label=f"次要事件{i+1}",
        )
        result.branch_results.append(branch)
        result.total_branches += 1

        if branch.verdict == "虚假":
            result.fake_branches += 1
            logger.info(f"次要事件{i+1}判定为虚假，终止")
            result.final_verdict = "虚假"
            result.final_reason  = f"次要事件{i+1}存在虚假信息：{branch.reason}"
            return result
        elif branch.verdict == "真实":
            verified_facts_list.append(f"次要事件{i+1}（已核实为真）：{sec_event.summary}")
        else:
            result.undecided_branches += 1

    # 3c: 隐含信息判断
    logger.info(f"Step 2c: 隐含信息判断（共{len(extraction.implicit_infos)}类）")
    for impl in extraction.implicit_infos:
        if not impl.summary or "失败" in impl.summary:
            continue
        branch = _judge_event(
            event_summary=f"[{impl.type_label}] {impl.summary}",
            news_text=news_text,
            verified_facts="\n".join(verified_facts_list),
            branch_type="implicit",
            branch_label=f"隐含信息-{impl.type_label}",
        )
        result.branch_results.append(branch)
        result.total_branches += 1

        if branch.verdict == "虚假":
            result.fake_branches += 1
            logger.info(f"隐含信息[{impl.type_label}]判定为虚假")
        elif branch.verdict == "真实":
            verified_facts_list.append(
                f"隐含信息[{impl.type_label}]（已核实为真）：{impl.summary}"
            )
        else:
            result.undecided_branches += 1

    # 最终汇总
    if result.fake_branches > 0:
        result.final_verdict = "虚假"
        fake_branches_desc = [
            f"  - [{b.branch_label}] {b.reason}"
            for b in result.branch_results
            if b.verdict == "虚假"
        ]
        result.final_reason = (
            f"新闻中有{result.fake_branches}个分支被判定为虚假：\n"
            + "\n".join(fake_branches_desc)
        )
    else:
        result.final_verdict = "真实"
        trusted_note = ""
        if result.trusted_source_injected:
            trusted_note = f"（{result.trusted_source_name}已发布相同内容，佐证真实性）"
        result.final_reason = (
            f"所有{result.total_branches}个分支均未发现明显虚假信息，"
            f"新闻判定为真实。{trusted_note}"
        )

    logger.info(f"检测完成，最终判定: {result.final_verdict}")
    return result