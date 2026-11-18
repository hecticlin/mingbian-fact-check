# core/verifier.py
# ============================================================
# 子任务执行器
# 依次执行：文本核查 → 图像核查 → 图文一致性核查
# 每个子任务都结合检索结果和 LVLM 推理
# ============================================================

import json
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict

from core.lvlm_client import LVLMClient
from core.task_decomposer import TaskDecomposer
from retrieval.retrieval_manager import RetrievalManager
from utils.logger import get_logger

logger = get_logger("verifier")


@dataclass
class SubTaskResult:
    """单个子任务的检测结果。"""
    task_name:    str
    fake_score:   float          # 0.0 ~ 1.0，越高越可能是谣言
    reasoning:    str
    raw_output:   str            # LVLM 原始输出
    evidence:     List[str] = field(default_factory=list)
    detail:       Dict       = field(default_factory=dict)


class Verifier:
    """
    三子任务执行器。
    每个子任务流程：检索 → 构造 Prompt → 调用 LVLM → 解析结果
    """

    def __init__(
        self,
        lvlm_client:       Optional[LVLMClient]      = None,
        retrieval_manager: Optional[RetrievalManager] = None,
    ):
        self.lvlm     = lvlm_client       or LVLMClient()
        self.retrieval = retrieval_manager or RetrievalManager()
        self.decomposer = TaskDecomposer()

    # 子任务 1：文本真实性核查

    def verify_text(self, text: str) -> SubTaskResult:
        """对文本内容进行事实核查。"""
        logger.info("▶ 子任务1: 文本真实性核查")

        # 1. 检索
        retrieval_results = self.retrieval.retrieve_for_text_check(text)
        context = self.retrieval.format_results_as_context(retrieval_results)

        # 2. 构建 Prompt
        prompt = TaskDecomposer.build_text_check_prompt(text, context)

        # 3. 调用 LVLM（仅文本，不含图片）
        raw_output = self.lvlm.chat_text_only(prompt)

        # 4. 解析结果
        parsed = self._parse_json_output(raw_output)
        fake_score = float(parsed.get("fake_score", 0.5))
        reasoning  = parsed.get("reasoning", raw_output[:200])

        evidence = [
            r.get("snippet", "") for r in retrieval_results if r.get("snippet")
        ]

        logger.info(f"  文本核查评分: {fake_score:.2f}")
        return SubTaskResult(
            task_name  = "text_check",
            fake_score = fake_score,
            reasoning  = reasoning,
            raw_output = raw_output,
            evidence   = evidence[:3],
            detail     = parsed,
        )

    # 子任务 2：图像真实性检测

    def verify_image(self, image_path: str, text: str) -> SubTaskResult:
        """对图片进行真实性检测（PS / AI生成 / 断章取义）。"""
        logger.info("▶ 子任务2: 图像真实性检测")

        # 1. 用图片描述文本检索（图片无法直接检索，用文本代替）
        image_query = f"{text[:40]} 图片 来源 真实"
        retrieval_results = self.retrieval.retrieve_for_image_check(image_query)
        context = self.retrieval.format_results_as_context(retrieval_results)

        # 2. 构建 Prompt
        prompt = TaskDecomposer.build_image_check_prompt(text, context)

        # 3. 调用 LVLM（图文混合）
        raw_output = self.lvlm.chat_with_image(image_path, prompt)

        # 4. 解析结果
        parsed = self._parse_json_output(raw_output)
        fake_score = float(parsed.get("fake_score", 0.5))
        reasoning  = parsed.get("reasoning", raw_output[:200])

        evidence = [
            r.get("snippet", "") for r in retrieval_results if r.get("snippet")
        ]

        logger.info(f"  图像核查评分: {fake_score:.2f}")
        return SubTaskResult(
            task_name  = "image_check",
            fake_score = fake_score,
            reasoning  = reasoning,
            raw_output = raw_output,
            evidence   = evidence[:3],
            detail     = parsed,
        )

    # 子任务 3：图文一致性推理

    def verify_consistency(self, image_path: str, text: str) -> SubTaskResult:
        """检测图片和文字之间是否存在不一致或误导性组合。"""
        logger.info("▶ 子任务3: 图文一致性核查")

        # 1. 综合检索
        retrieval_results = self.retrieval.retrieve_for_consistency_check(
            text, text[:30]
        )
        context = self.retrieval.format_results_as_context(retrieval_results)

        # 2. 构建 Prompt
        prompt = TaskDecomposer.build_consistency_check_prompt(text, context)

        # 3. 调用 LVLM（图文混合）
        raw_output = self.lvlm.chat_with_image(image_path, prompt)

        # 4. 解析结果
        parsed = self._parse_json_output(raw_output)
        fake_score = float(parsed.get("fake_score", 0.5))
        reasoning  = parsed.get("reasoning", raw_output[:200])

        evidence = [
            r.get("snippet", "") for r in retrieval_results if r.get("snippet")
        ]

        logger.info(f"  一致性核查评分: {fake_score:.2f}")
        return SubTaskResult(
            task_name  = "consistency_check",
            fake_score = fake_score,
            reasoning  = reasoning,
            raw_output = raw_output,
            evidence   = evidence[:3],
            detail     = parsed,
        )

    # 权威媒体 / 辟谣平台短路核查

    # 辟谣平台关键词
    _FACT_CHECK_KEYWORDS = [
        "piyao.org.cn", "较真", "辟谣", "谣言", "不实",
        "vp.fact.qq.com", "jiaozhen", "rumor",
    ]
    # 权威媒体域名
    _AUTHORITY_DOMAINS = [
        "people.com.cn", "xinhuanet.com", "cctv.com", "chinanews.com",
        "thepaper.cn", "bjnews.com.cn", "caixin.com", "gov.cn",
    ]

    def _authority_pre_check(self, text: str) -> Optional[SubTaskResult]:
        """
        在三子任务之前，先用检索引擎查询是否有权威媒体/辟谣平台命中。
        命中则直接短路返回，跳过后续三个子任务。

        Returns:
            SubTaskResult (fake_score=0.95/0.05, 含短路标记) 或 None
        """
        logger.info("▶ 短路预检：权威媒体 / 辟谣平台检索")
        try:
            results = self.retrieval.retrieve_for_text_check(text)
        except Exception as e:
            logger.warning(f"短路预检检索失败: {e}")
            return None

        if not results:
            logger.info("  短路预检：无检索结果，继续完整分析")
            return None

        for r in results:
            url     = (r.get("url") or "").lower()
            title   = (r.get("title") or "").lower()
            snippet = (r.get("snippet") or "").lower()
            combined = url + " " + title + " " + snippet

            # 检查是否命中辟谣平台
            is_fact_check = any(kw in combined for kw in self._FACT_CHECK_KEYWORDS)
            if is_fact_check:
                source = r.get("url", "辟谣平台")
                reason = (
                    f"辟谣平台直接命中，短路判定为虚假新闻。\n"
                    f"来源: {r.get('title', '')}\n"
                    f"链接: {source}\n"
                    f"摘要: {r.get('snippet', '')[:200]}"
                )
                logger.info(f"   辟谣平台命中: {source}")
                return SubTaskResult(
                    task_name  = "authority_short_circuit",
                    fake_score = 0.95,
                    reasoning  = reason,
                    raw_output = reason,
                    evidence   = [r.get("snippet", "")],
                    detail     = {
                        "short_circuited": True,
                        "short_circuit_source": r.get("title", "辟谣平台"),
                        "short_circuit_url": source,
                        "short_circuit_verdict": "虚假",
                    },
                )

            # 检查是否命中权威媒体
            is_authority = any(d in url for d in self._AUTHORITY_DOMAINS)
            if is_authority:
                source = r.get("url", "权威媒体")
                reason = (
                    f"权威媒体报道命中，短路判定为真实新闻。\n"
                    f"来源: {r.get('title', '')}\n"
                    f"链接: {source}\n"
                    f"摘要: {r.get('snippet', '')[:200]}"
                )
                logger.info(f"   权威媒体命中: {source}")
                return SubTaskResult(
                    task_name  = "authority_short_circuit",
                    fake_score = 0.05,
                    reasoning  = reason,
                    raw_output = reason,
                    evidence   = [r.get("snippet", "")],
                    detail     = {
                        "short_circuited": True,
                        "short_circuit_source": r.get("title", "权威媒体"),
                        "short_circuit_url": source,
                        "short_circuit_verdict": "真实",
                    },
                )

        logger.info("  短路预检：未命中权威源，继续完整分析")
        return None

    # 批量执行三子任务

    def run_all(
        self, image_path: str, text: str
    ) -> Dict[str, SubTaskResult]:
        """
        依次执行全部三个子任务，返回结果字典。
        键：text_check / image_check / consistency_check
        若权威预检命中则短路，仅返回 authority_short_circuit。
        """
        # Step 0: 权威媒体/辟谣平台短路预检
        sc = self._authority_pre_check(text)
        if sc is not None:
            logger.info("权威短路命中，跳过三子任务")
            return {"authority_short_circuit": sc}

        results = {}

        try:
            results["text_check"] = self.verify_text(text)
        except Exception as e:
            logger.error(f"文本核查失败: {e}")
            results["text_check"] = self._error_result("text_check", str(e))

        try:
            results["image_check"] = self.verify_image(image_path, text)
        except Exception as e:
            logger.error(f"图像核查失败: {e}")
            results["image_check"] = self._error_result("image_check", str(e))

        try:
            results["consistency_check"] = self.verify_consistency(image_path, text)
        except Exception as e:
            logger.error(f"一致性核查失败: {e}")
            results["consistency_check"] = self._error_result("consistency_check", str(e))

        return results

    # 工具方法

    @staticmethod
    def _parse_json_output(raw: str) -> dict:
        """
        从 LVLM 输出中提取 JSON。
        处理常见情况：Markdown 代码块、前缀文字、不规范引号。
        """
        # 去除 Markdown 代码块标记
        text = re.sub(r"```(?:json)?\s*", "", raw)
        text = text.replace("```", "").strip()

        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试提取 { ... } 片段
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # 兜底：返回带原始输出的空结构
        logger.warning(f"JSON 解析失败，原始输出片段: {raw[:200]}")
        return {
            "fake_score": 0.5,
            "reasoning":  raw[:300],
            "_parse_error": True,
        }

    @staticmethod
    def _error_result(task_name: str, error_msg: str) -> SubTaskResult:
        return SubTaskResult(
            task_name  = task_name,
            fake_score = 0.5,
            reasoning  = f"子任务执行失败: {error_msg}",
            raw_output = "",
            evidence   = [],
        )
