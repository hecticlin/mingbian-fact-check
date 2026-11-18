# core/fusion.py
# ============================================================
# 多视角推理融合器
# 汇总三子任务结果 → 加权融合 → 最终判决 + 可解释输出
# ============================================================

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config.settings import (
    WEIGHT_TEXT,
    WEIGHT_IMAGE,
    WEIGHT_CONSISTENCY,
    FAKE_THRESHOLD,
    REAL_THRESHOLD,
)
from core.verifier import SubTaskResult
from utils.logger import get_logger

logger = get_logger("fusion")


@dataclass
class DetectionResult:
    """最终检测结果（对外输出的完整报告）。"""
    verdict:     str    # "谣言" / "真实" / "待核实"
    label:       str    # "FAKE" / "REAL" / "UNVERIFIED"
    confidence:  float  # 0.0 ~ 1.0（谣言可能性）

    reasoning: Dict[str, str] = field(default_factory=dict)
    evidence:  List[str]      = field(default_factory=list)

    # 子任务分数
    text_score:        float = 0.0
    image_score:       float = 0.0
    consistency_score: float = 0.0

    # 输入内容摘要
    input_text:        str = ""
    input_image_path:  str = ""

    def to_dict(self) -> dict:
        return {
            "verdict":    self.verdict,
            "label":      self.label,
            "confidence": round(self.confidence, 4),
            "reasoning":  self.reasoning,
            "evidence":   self.evidence,
            "sub_scores": {
                "text_check":        round(self.text_score, 4),
                "image_check":       round(self.image_score, 4),
                "consistency_check": round(self.consistency_score, 4),
            },
            "input": {
                "text":        self.input_text[:100] + "..." if len(self.input_text) > 100 else self.input_text,
                "image_path":  self.input_image_path,
            },
        }

    def __str__(self) -> str:
        bar = "─" * 50
        lines = [
            bar,
            f"  检测结果：【{self.verdict}】  置信度：{self.confidence:.0%}",
            bar,
            f"  文本核查分  : {self.text_score:.2f}（权重 {WEIGHT_TEXT}）",
            f"  图像核查分  : {self.image_score:.2f}（权重 {WEIGHT_IMAGE}）",
            f"  图文一致性分: {self.consistency_score:.2f}（权重 {WEIGHT_CONSISTENCY}）",
            bar,
        ]
        if self.reasoning:
            lines.append("  推理依据：")
            for k, v in self.reasoning.items():
                lines.append(f"    [{k}] {v[:120]}")
        if self.evidence:
            lines.append("  核心证据：")
            for e in self.evidence[:3]:
                lines.append(f"    • {e[:100]}")
        lines.append(bar)
        return "\n".join(lines)


class FusionEngine:
    """
    多视角推理融合引擎。
    策略：加权平均 + 置信度阈值 → 最终标签。
    可扩展为自适应权重（根据各子任务可信度动态调整）。
    """

    def __init__(
        self,
        weight_text:        float = WEIGHT_TEXT,
        weight_image:       float = WEIGHT_IMAGE,
        weight_consistency: float = WEIGHT_CONSISTENCY,
        fake_threshold:     float = FAKE_THRESHOLD,
        real_threshold:     float = REAL_THRESHOLD,
    ):
        total = weight_text + weight_image + weight_consistency
        self.w_text   = weight_text        / total
        self.w_image  = weight_image       / total
        self.w_consist = weight_consistency / total

        self.fake_threshold = fake_threshold
        self.real_threshold = real_threshold

    def fuse(
        self,
        sub_results: Dict[str, SubTaskResult],
        input_text:       str = "",
        input_image_path: str = "",
    ) -> DetectionResult:
        """
        融合三子任务结果，输出最终 DetectionResult。
        若 sub_results 中存在 authority_short_circuit 键则直接短路输出。
        """

        # 权威短路处理
        sc = sub_results.get("authority_short_circuit")
        if sc is not None:
            detail = sc.detail or {}
            sc_verdict = detail.get("short_circuit_verdict", "")
            if sc_verdict == "虚假":
                verdict, label, confidence = "谣言", "FAKE", sc.fake_score
            else:
                verdict, label, confidence = "真实", "REAL", 1.0 - sc.fake_score

            logger.info(f"权威短路融合: {verdict} (confidence={confidence:.2f})")
            return DetectionResult(
                verdict           = verdict,
                label             = label,
                confidence        = round(confidence, 4),
                reasoning         = {
                    "authority_short_circuit": sc.reasoning,
                    "short_circuited": True,
                    "short_circuit_source": detail.get("short_circuit_source", ""),
                },
                evidence          = sc.evidence[:5],
                text_score        = sc.fake_score,
                image_score       = 0.0,
                consistency_score = 0.0,
                input_text        = input_text,
                input_image_path  = input_image_path,
            )

        text_result    = sub_results.get("text_check")
        image_result   = sub_results.get("image_check")
        consist_result = sub_results.get("consistency_check")

        # 分数提取（缺失时默认 0.5 中性）
        t_score = text_result.fake_score    if text_result    else 0.5
        i_score = image_result.fake_score   if image_result   else 0.5
        c_score = consist_result.fake_score if consist_result else 0.5

        # 加权融合
        weighted_score = (
            self.w_text    * t_score +
            self.w_image   * i_score +
            self.w_consist * c_score
        )

        # 极值调整：任意一项极高则拉高综合分
        max_single = max(t_score, i_score, c_score)
        if max_single >= 0.85:
            weighted_score = max(weighted_score, 0.65)

        # 最终标签
        if weighted_score >= self.fake_threshold:
            verdict, label = "谣言", "FAKE"
        elif weighted_score <= self.real_threshold:
            verdict, label = "真实", "REAL"
        else:
            verdict, label = "待核实", "UNVERIFIED"

        logger.info(
            f"融合结果: 文本={t_score:.2f}, 图像={i_score:.2f}, "
            f"一致性={c_score:.2f} → 综合={weighted_score:.2f} → {verdict}"
        )

        # 汇总推理和证据
        reasoning = {}
        if text_result:
            reasoning["text_check"]        = text_result.reasoning
        if image_result:
            reasoning["image_check"]       = image_result.reasoning
        if consist_result:
            reasoning["consistency_check"] = consist_result.reasoning

        all_evidence: List[str] = []
        for r in [text_result, image_result, consist_result]:
            if r:
                all_evidence.extend(r.evidence)
        unique_evidence = list(dict.fromkeys(e for e in all_evidence if e))

        return DetectionResult(
            verdict           = verdict,
            label             = label,
            confidence        = round(weighted_score, 4),
            reasoning         = reasoning,
            evidence          = unique_evidence[:6],
            text_score        = t_score,
            image_score       = i_score,
            consistency_score = c_score,
            input_text        = input_text,
            input_image_path  = input_image_path,
        )
