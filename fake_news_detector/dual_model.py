"""
dual_model.py
DeepSeek 两阶段交叉验证判断机制。

原来的"双模型协商"由两个不同模型完成；现已改为 DeepSeek 单模型两阶段验证。
现在改为 DeepSeek 两次独立调用，通过角色切换实现同等效果：

  第一阶段（初判）：
    DeepSeek 扮演"正向推理员"，基于事件描述与检索信息给出初步判断。

  第二阶段（批判复核）：
    DeepSeek 扮演"挑剔的核查员"，对第一阶段结论进行批判性审查：
    - 主动寻找初判的逻辑漏洞；
    - 质疑是否有被忽视的矛盾点；
    - 给出是否维持或推翻初判的决定。

  结论一致 → 直接输出。
  结论不一致 → 最多进行 MAX_REVIEW_ROUNDS 轮追加复核。
  仍不一致 → 并列输出两阶段各自的判断与理由。
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from llm_client import call_llm_json
from config import MAX_REVIEW_ROUNDS

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class StageJudgment:
    """单阶段判断结果"""
    stage: str           # "initial"（初判）或 "review"（复核）
    verdict: str         # "真实" / "虚假" / "无法判断"
    confidence: str      # "高" / "中" / "低"
    contradictions: list[dict] = field(default_factory=list)
    reason: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class JudgmentResult:
    """两阶段判断最终结果"""
    agreed: bool                               # 两阶段是否达成一致
    final_verdict: Optional[str] = None        # 一致时的最终判断
    final_reason: str = ""                     # 一致时的综合理由
    initial_judgment: Optional[StageJudgment] = None
    review_judgment: Optional[StageJudgment] = None
    review_rounds: int = 0                     # 实际追加复核轮次
    review_log: list[dict] = field(default_factory=list)


# ============================================================
# 提示词
# ============================================================

_INITIAL_JUDGMENT_SYSTEM = (
    "你是一位专业的新闻事实核查员，负责对新闻片段进行正向推理分析，"
    "基于已有信息和外部知识给出客观判断。"
    "请严格按照要求的 JSON 格式输出，不要添加任何额外说明。"
)

_REVIEW_SYSTEM = (
    "你是一位极其挑剔的资深新闻核查主编，专门负责审查初级核查员的判断是否存在漏洞。"
    "你的职责是：主动寻找初判中被忽视的矛盾、逻辑漏洞和证据不足之处。"
    "请严格按照要求的 JSON 格式输出，不要添加任何额外说明。"
)

_REVIEW_PROMPT_TEMPLATE = """
你是一位极其挑剔的资深新闻核查主编。以下是一位核查员对某条新闻片段的初步判断，请进行批判性审查。

=== 待核查的新闻事件 ===
{event_description}

=== 相关外部检索信息 ===
{retrieved_info}

=== 初判结论 ===
判断：{initial_verdict}（置信度：{initial_confidence}）
理由：{initial_reason}

=== 你的审查任务 ===
请以批判视角重新审视上述判断：
1. 初判理由是否存在逻辑漏洞或推断过度？
2. 检索信息中是否有被初判忽视的关键矛盾点？
3. 初判结论是否有"确认偏误"（只选取支持自己结论的证据）？
4. 给出你的最终审查结论，并明确说明是维持还是推翻初判。

请严格按以下 JSON 格式输出：
{{
  "found_issues": true或false,
  "issues": ["具体问题1", "具体问题2"],
  "verdict": "真实"或"虚假"或"无法判断",
  "confidence": "高"或"中"或"低",
  "upheld_initial": true或false,
  "reason": "完整的审查结论与判断理由"
}}
"""

_REBUTTAL_PROMPT_TEMPLATE = """
你是一位专业的新闻事实核查员，你的初步判断正在接受资深主编的质疑，请进行反驳或修正。

=== 待核查的新闻事件 ===
{event_description}

=== 相关外部检索信息 ===
{retrieved_info}

=== 你的初判 ===
判断：{initial_verdict}（置信度：{initial_confidence}）
理由：{initial_reason}

=== 主编的审查意见 ===
判断：{review_verdict}（置信度：{review_confidence}）
发现的问题：{review_issues}
审查理由：{review_reason}

=== 追加复核任务（第{round_num}轮） ===
请重新审视双方的论点：
1. 主编的质疑是否有道理？哪些点你接受，哪些点你反驳？
2. 综合双方意见后，给出你的最终结论。

请严格按以下 JSON 格式输出：
{{
  "accepted_issues": ["接受的质疑点"],
  "rebutted_issues": ["反驳的质疑点，及反驳理由"],
  "final_verdict": "真实"或"虚假"或"无法判断",
  "final_confidence": "高"或"中"或"低",
  "final_reason": "综合双方意见后的完整判断理由"
}}
"""


# ============================================================
# 内部工具
# ============================================================

def _extract_stage_judgment(raw: dict, stage: str) -> StageJudgment:
    """从模型 JSON 响应中提取结构化判断。"""
    if "error" in raw:
        logger.warning(f"[{stage}] 解析失败，使用默认值")
        return StageJudgment(
            stage=stage,
            verdict="无法判断",
            confidence="低",
            reason=raw.get("raw", "模型响应解析失败"),
            raw=raw,
        )
    # 兼容初判字段名与复核字段名
    verdict = raw.get("verdict") or raw.get("final_verdict", "无法判断")
    confidence = raw.get("confidence") or raw.get("final_confidence", "低")
    reason = raw.get("reason") or raw.get("final_reason", "")
    return StageJudgment(
        stage=stage,
        verdict=verdict,
        confidence=confidence,
        contradictions=raw.get("contradictions", []),
        reason=reason,
        raw=raw,
    )


def _verdicts_agree(j1: StageJudgment, j2: StageJudgment) -> bool:
    return j1.verdict.strip() == j2.verdict.strip()


def _merge_reasons(initial: StageJudgment, review: StageJudgment) -> str:
    """两阶段一致时合并理由。"""
    if initial.reason == review.reason:
        return initial.reason
    return (
        f"【初判理由】{initial.reason}\n\n"
        f"【复核确认】{review.reason}\n\n"
        f"（两阶段均判定为：{initial.verdict}）"
    )


# ============================================================
# 核心入口
# ============================================================

def judge(
    judgment_prompt: str,
    event_description: str,
    retrieved_info: str,
) -> JudgmentResult:
    """
    DeepSeek 两阶段交叉验证判断。

    Args:
        judgment_prompt:   初判提示词（JUDGMENT_PROMPT 格式）
        event_description: 事件描述（用于复核上下文）
        retrieved_info:    外部检索信息文本

    Returns:
        JudgmentResult
    """
    result = JudgmentResult(agreed=False)

    # 第一阶段：初判（正向推理）
    logger.info("  阶段1: 初判（正向推理）...")
    initial_raw = call_llm_json(judgment_prompt, system_prompt=_INITIAL_JUDGMENT_SYSTEM)
    initial = _extract_stage_judgment(initial_raw, "initial")
    result.initial_judgment = initial
    logger.info(f"  初判结果: {initial.verdict}（置信度: {initial.confidence}）")

    # 第二阶段：批判复核
    logger.info("  阶段2: 批判复核...")
    review_prompt = _REVIEW_PROMPT_TEMPLATE.format(
        event_description=event_description,
        retrieved_info=retrieved_info,
        initial_verdict=initial.verdict,
        initial_confidence=initial.confidence,
        initial_reason=initial.reason,
    )
    review_raw = call_llm_json(review_prompt, system_prompt=_REVIEW_SYSTEM)
    review = _extract_stage_judgment(review_raw, "review")
    result.review_judgment = review
    logger.info(f"  复核结果: {review.verdict}（维持初判: {review_raw.get('upheld_initial', '?')}）")

    # 检查是否一致
    if _verdicts_agree(initial, review):
        result.agreed = True
        result.final_verdict = initial.verdict
        result.final_reason = _merge_reasons(initial, review)
        logger.info(f"  两阶段一致，判断: {result.final_verdict}")
        return result

    # 追加复核循环
    logger.info(
        f"  初判与复核不一致（{initial.verdict} vs {review.verdict}），"
        f"开始追加复核（最多{MAX_REVIEW_ROUNDS}轮）"
    )

    current_initial = initial
    current_review  = review

    for rnd in range(1, MAX_REVIEW_ROUNDS + 1):
        result.review_rounds = rnd
        logger.info(f"  追加复核第{rnd}轮...")

        issues_text = "\n".join(
            current_review.raw.get("issues", [])
        ) or current_review.reason[:200]

        rebuttal_prompt = _REBUTTAL_PROMPT_TEMPLATE.format(
            event_description=event_description,
            retrieved_info=retrieved_info,
            initial_verdict=current_initial.verdict,
            initial_confidence=current_initial.confidence,
            initial_reason=current_initial.reason,
            review_verdict=current_review.verdict,
            review_confidence=current_review.confidence,
            review_issues=issues_text,
            review_reason=current_review.reason,
            round_num=rnd,
        )

        rebuttal_raw = call_llm_json(rebuttal_prompt, system_prompt=_INITIAL_JUDGMENT_SYSTEM)
        rebuttal = _extract_stage_judgment(rebuttal_raw, f"rebuttal_round{rnd}")

        result.review_log.append({
            "round":          rnd,
            "rebuttal_verdict": rebuttal.verdict,
            "rebuttal_reason":  rebuttal.reason,
            "accepted_issues":  rebuttal_raw.get("accepted_issues", []),
            "rebutted_issues":  rebuttal_raw.get("rebutted_issues", []),
        })

        logger.info(f"  追加复核第{rnd}轮结果: {rebuttal.verdict}")

        # 若追加复核与复核阶段一致，则两者共同推翻初判
        if _verdicts_agree(rebuttal, current_review):
            result.agreed = True
            result.final_verdict = rebuttal.verdict
            result.final_reason = (
                f"经{rnd}轮追加复核后，复核意见得到确认：\n{rebuttal.reason}"
            )
            result.review_judgment = rebuttal
            logger.info(f"  追加复核第{rnd}轮达成一致: {result.final_verdict}")
            return result

        # 若追加复核与初判一致，则维持初判
        if _verdicts_agree(rebuttal, current_initial):
            result.agreed = True
            result.final_verdict = rebuttal.verdict
            result.final_reason = (
                f"经{rnd}轮追加复核后，初判得到确认：\n{rebuttal.reason}"
            )
            result.initial_judgment = rebuttal
            logger.info(f"  追加复核第{rnd}轮初判得到确认: {result.final_verdict}")
            return result

        # 仍不一致，更新当前状态继续
        current_initial = rebuttal
        current_review  = current_review

    # 超出上限仍未一致
    logger.info(
        f"  追加复核达到上限（{MAX_REVIEW_ROUNDS}轮），仍未达成一致，并列输出"
    )
    result.agreed = False
    result.final_verdict = None
    return result


def format_result(result: JudgmentResult) -> dict:
    """将判断结果格式化为统一输出字典。"""
    if result.agreed:
        return {
            "agreed":        True,
            "verdict":       result.final_verdict,
            "reason":        result.final_reason,
            "review_rounds": result.review_rounds,
        }
    else:
        init = result.initial_judgment
        rev  = result.review_judgment
        return {
            "agreed":  False,
            "verdict": "两阶段分歧未解决",
            "initial": {
                "verdict":    init.verdict    if init else "N/A",
                "confidence": init.confidence if init else "",
                "reason":     init.reason     if init else "",
            },
            "review": {
                "verdict":    rev.verdict    if rev else "N/A",
                "confidence": rev.confidence if rev else "",
                "reason":     rev.reason     if rev else "",
            },
            "review_rounds": result.review_rounds,
            "review_log":    result.review_log,
        }