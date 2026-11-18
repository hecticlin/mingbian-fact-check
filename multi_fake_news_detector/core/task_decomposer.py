# core/task_decomposer.py
# ============================================================
# 层级任务分解器
# 将图文谣言检测分解为三个子任务，生成各子任务所需的 Prompt
# 参考：MMD-Agent (ICLR 2025) 范式
# ============================================================

from dataclasses import dataclass
from typing import Optional
from utils.logger import get_logger

logger = get_logger("task_decomposer")


@dataclass
class SubTaskPrompts:
    """三个子任务的 Prompt 集合。"""
    text_check_prompt:        str
    image_check_prompt:       str
    consistency_check_prompt: str
    retrieval_queries:        list  # 建议的检索关键词列表


class TaskDecomposer:
    """
    将输入的图文内容分解为三个标准化子任务：
      1. 文本真实性核查（关键声明提取 + 事实分析）
      2. 图像真实性检测（PS / AI生成 / 断章取义）
      3. 跨模态一致性推理（图文内容是否匹配）
    """

    # 子任务 1：文本真实性核查

    TEXT_CHECK_SYSTEM = """你是一位专业的中文谣言鉴别专家。
你的任务是分析给定的文本内容，判断其可信度。
请从以下角度进行系统分析：
1. 提取文本中的核心声明（人物、事件、地点、数据）
2. 识别夸大、误导、情绪煽动的表达方式
3. 判断声明是否符合常识和已知事实
4. 结合提供的检索证据进行核实

输出格式要求：
- 用中文作答
- 给出 0.0~1.0 的虚假可能性评分（1.0=极可能虚假）
- 简要说明判断依据"""

    @staticmethod
    def build_text_check_prompt(text: str, retrieval_context: str) -> str:
        return f"""请对以下文本内容进行事实核查分析：

【待核查文本】
{text}

【检索到的参考信息】
{retrieval_context}

请完成以下分析：
1. 核心声明提取：列出文本中最关键的1-3个可验证声明
2. 可信度分析：结合参考信息，逐条评估声明的可信度
3. 语言特征分析：是否存在夸大、情绪化、缺乏来源等特征
4. 综合判断：

请用以下JSON格式输出（只输出JSON，不要额外解释）：
{{
  "key_claims": ["声明1", "声明2"],
  "credibility_analysis": "详细分析...",
  "language_features": "语言特征描述...",
  "fake_score": 0.0,
  "reasoning": "综合判断理由..."
}}"""

    # 子任务 2：图像真实性检测

    IMAGE_CHECK_SYSTEM = """你是一位专业的图片真实性鉴别专家。
请仔细分析图片内容，识别以下问题：
1. 图片是否存在明显的 PS 痕迹（光影不一致、边缘异常、接缝痕迹）
2. 图片是否为 AI 生成（手指异常、文字模糊、细节不自然）
3. 图片内容与上下文是否相符（是否为断章取义的旧图）
4. 图片中是否存在误导性信息（篡改的文字、数据、标志）"""

    @staticmethod
    def build_image_check_prompt(
        text: str, retrieval_context: str
    ) -> str:
        return f"""请仔细分析这张图片，结合配套文字和参考信息进行真实性鉴别。

【配套文字描述】
{text}

【图片相关检索信息】
{retrieval_context}

请从以下维度分析：
1. 图片技术真实性：是否有 PS / AI 生成的痕迹？
2. 图片内容真实性：图片所展示的场景是否真实发生？
3. 图片与文字匹配性：图片是否真的描述了文字所说的内容？
4. 图片来源分析：结合检索信息，图片是否来自其他场景/时间？

请用以下JSON格式输出（只输出JSON，不要额外解释）：
{{
  "technical_issues": "技术层面的真实性问题描述...",
  "content_authenticity": "内容真实性分析...",
  "text_image_match": "图片是否与文字描述一致...",
  "source_analysis": "来源分析...",
  "fake_score": 0.0,
  "reasoning": "综合判断理由..."
}}"""

    # 子任务 3：图文一致性推理

    CONSISTENCY_CHECK_SYSTEM = """你是一位专业的多模态内容核查专家。
你需要分析图片和文字是否构成误导性组合：
1. 图文内容是否在时间、地点、事件上相互匹配
2. 图片是否被断章取义（原本是 A 事件的图片，却用于描述 B 事件）
3. 文字描述是否夸大或歪曲了图片所展示的内容
4. 两者组合是否会对读者产生误导"""

    @staticmethod
    def build_consistency_check_prompt(
        text: str, retrieval_context: str
    ) -> str:
        return f"""请综合分析图片与文字之间的一致性，判断是否存在图文不符的误导情况。

【文字内容】
{text}

【参考信息】
{retrieval_context}

请分析：
1. 图文时间一致性：图片拍摄时间与文字描述的事件时间是否一致？
2. 图文地点一致性：图片场景与文字描述的地点是否一致？
3. 图文事件一致性：图片内容与文字描述的事件是否一致？
4. 是否存在断章取义：图片是否被用于描述它实际无关的事件？
5. 整体误导性评估：这种图文组合是否会误导读者？

请用以下JSON格式输出（只输出JSON，不要额外解释）：
{{
  "time_consistency": "时间一致性分析...",
  "location_consistency": "地点一致性分析...",
  "event_consistency": "事件一致性分析...",
  "out_of_context": true,
  "misleading_assessment": "误导性评估...",
  "fake_score": 0.0,
  "reasoning": "综合判断理由..."
}}"""

    # 检索词生成

    @staticmethod
    def generate_retrieval_queries(text: str) -> list:
        """
        根据文本生成检索关键词列表（零样本，无需模型调用）。
        实际使用时可调用 LVLM 生成更精准的检索词。
        """
        queries = []

        # 截取核心片段
        core = text[:50].strip()
        queries.append(core)
        queries.append(f"{core} 辟谣")
        queries.append(f"{core} 真相")

        return queries[:3]

    # 主入口

    def decompose(self, text: str) -> SubTaskPrompts:
        """
        根据输入文本生成所有子任务 Prompt 和检索词。
        （图片在各 verifier 调用时传入，不在这里处理）
        """
        logger.info("开始任务分解...")
        queries = self.generate_retrieval_queries(text)

        # Prompt 此时用空的检索上下文占位；
        # verifier 会在获取检索结果后重新填充。
        placeholder = "（检索结果将在执行时填充）"

        return SubTaskPrompts(
            text_check_prompt        = self.build_text_check_prompt(text, placeholder),
            image_check_prompt       = self.build_image_check_prompt(text, placeholder),
            consistency_check_prompt = self.build_consistency_check_prompt(text, placeholder),
            retrieval_queries        = queries,
        )
