"""
implicit_types.py
隐含信息类型的精细建模：定义各类型的提示词模板
"""

from config import IMPLICIT_INFO_TYPES

# ============================================================
# 每种隐含信息类型对应的专门提示词
# ============================================================

IMPLICIT_PROMPTS = {

    "presupposition": """
你是一位专业的新闻逻辑分析师。请分析以下新闻文本中的【预设信息】。

预设信息定义：新闻在叙述时默认成立、未经证明的前提假设。
例如："某政策带来的好处" 预设了 "该政策已经实施且产生效果"。

新闻文本：
{news_text}

主干事件参考：
{backbone_event}

请完成以下任务：
1. 找出新闻中2-4个关键预设假设，每条预设以"该新闻预设：[具体内容]"的格式输出；
2. 判断每个预设是否符合已知事实或常识；
3. 将所有预设整合为一段简洁的隐含信息描述。

输出格式（严格JSON）：
{{
  "type": "presupposition",
  "items": [
    {{"presupposition": "预设内容", "is_valid": true/false, "reason": "判断依据"}}
  ],
  "summary": "隐含信息综合描述"
}}
""",

    "implicature": """
你是一位语用学专家。请分析以下新闻文本中的【会话含义】。

会话含义定义：说话者通过字面意思之外传递的隐含信息，读者能从上下文推断出来。
例如："专家表示暂未发现危害" 实际含义可能是 "存在潜在风险但证据不足"。

新闻文本：
{news_text}

主干事件参考：
{backbone_event}

请完成以下任务：
1. 找出2-3个明显的会话含义，格式：字面表达 → 实际传递的含义；
2. 判断这些会话含义是否构成信息误导；
3. 将分析整合为一段简洁描述。

输出格式（严格JSON）：
{{
  "type": "implicature",
  "items": [
    {{"literal": "字面表达", "implied": "实际含义", "is_misleading": true/false}}
  ],
  "summary": "隐含信息综合描述"
}}
""",

    "framing_bias": """
你是一位媒体研究专家。请分析以下新闻文本中的【框架偏向】。

框架偏向定义：新闻通过选择性叙事、措辞选择、议程设置来引导读者得出特定结论，而非客观呈现事实。
例如：同一事件，用"示威者"vs"暴徒"描述，传递截然不同的立场。

新闻文本：
{news_text}

主干事件参考：
{backbone_event}

请完成以下任务：
1. 识别新闻的整体叙事立场；
2. 找出2-4处有明显倾向性的表述；
3. 判断该框架是否可能扭曲事实认知；
4. 整合描述。

输出格式（严格JSON）：
{{
  "type": "framing_bias",
  "stance": "支持/反对/中立/煽动",
  "items": [
    {{"expression": "原文表述", "bias": "倾向分析"}}
  ],
  "distorts_fact": true/false,
  "summary": "隐含信息综合描述"
}}
""",

    "factual_gap": """
你是一位资深新闻编辑。请分析以下新闻文本中的【事实缺口】。

事实缺口定义：新闻刻意省略、回避或模糊了对判断真相至关重要的信息。
例如：报道某药品"有效率提升30%"，但未说明基准值和样本规模。

新闻文本：
{news_text}

主干事件参考：
{backbone_event}

请完成以下任务：
1. 列出2-4个新闻中缺失的关键信息点；
2. 判断这些缺口是否影响新闻真实性的判断；
3. 整合描述。

输出格式（严格JSON）：
{{
  "type": "factual_gap",
  "items": [
    {{"missing_info": "缺失信息描述", "impact": "high/medium/low", "reason": "为何重要"}}
  ],
  "summary": "隐含信息综合描述"
}}
""",

    "emotion_manipulation": """
你是一位心理学与传播学专家。请分析以下新闻文本中的【情绪操控】。

情绪操控定义：新闻通过特定词汇、叙述节奏或情境构建，刻意激发读者恐惧、愤怒、同情等情绪，使其绕过理性判断。
例如：使用"骇人"、"令人发指"等词汇夸大事件影响。

新闻文本：
{news_text}

请完成以下任务：
1. 找出2-3处情绪化表达；
2. 判断情绪操控的目的（恐惧/愤怒/同情/其他）；
3. 分析这种操控是否服务于虚假信息传播；
4. 整合描述。

输出格式（严格JSON）：
{{
  "type": "emotion_manipulation",
  "dominant_emotion": "主导情绪类型",
  "items": [
    {{"expression": "情绪化表达", "intended_emotion": "目标情绪", "serves_misinformation": true/false}}
  ],
  "summary": "隐含信息综合描述"
}}
""",

    "causal_fallacy": """
你是一位逻辑学专家。请分析以下新闻文本中的【因果谬误】。

因果谬误定义：新闻建立了错误或缺乏依据的因果关系。
常见类型：后此谬误（A后发生B就认为A导致B）、相关性混淆（相关≠因果）、单一原因谬误（忽视其他因素）。

新闻文本：
{news_text}

主干事件参考：
{backbone_event}

请完成以下任务：
1. 找出新闻中1-3个存疑的因果关系；
2. 指出谬误类型；
3. 说明正确的因果推断应如何表述；
4. 整合描述。

输出格式（严格JSON）：
{{
  "type": "causal_fallacy",
  "items": [
    {{"claimed_cause": "声称的原因", "claimed_effect": "声称的结果", "fallacy_type": "谬误类型", "correct_interpretation": "正确解读"}}
  ],
  "summary": "隐含信息综合描述"
}}
""",
}


def get_implicit_prompt(implicit_type: str, news_text: str, backbone_event: str = "") -> str:
    """
    根据隐含信息类型获取对应的格式化提示词。

    Args:
        implicit_type: 隐含信息类型键名（见 config.IMPLICIT_INFO_TYPES）
        news_text: 原始新闻文本
        backbone_event: 主干事件描述（可选，用于上下文参考）

    Returns:
        格式化后的提示词字符串
    """
    if implicit_type not in IMPLICIT_PROMPTS:
        raise ValueError(f"未知的隐含信息类型: {implicit_type}，支持的类型: {list(IMPLICIT_PROMPTS.keys())}")

    return IMPLICIT_PROMPTS[implicit_type].format(
        news_text=news_text,
        backbone_event=backbone_event or "暂未提取",
    )


def get_all_implicit_prompts(news_text: str, backbone_event: str = "") -> dict:
    """
    获取所有隐含信息类型的提示词字典。
    """
    return {
        itype: get_implicit_prompt(itype, news_text, backbone_event)
        for itype in IMPLICIT_PROMPTS
    }


# ============================================================
# 事件抽取提示词（5W1H）
# ============================================================

BACKBONE_EXTRACT_PROMPT = """
你是一位资深新闻记者。请从以下新闻文本中提取【主干事件】的六要素（5W1H）。

新闻文本：
{news_text}

提取规则：
- 主干事件是新闻最核心、最主要的事件，是整篇报道的骨架；
- 按以下六个维度分别提取，若文本未提及则填写"文本未说明"；
- 提取内容要忠实原文，不要添加推测。

维度定义：
- Who（何人）：事件的主要参与者/当事人
- What（何事）：发生了什么核心事件
- When（何时）：事件发生的时间
- Where（何地）：事件发生的地点
- Why（为何）：事件发生的原因
- How（如何）：事件如何发展/经过

输出格式（严格JSON）：
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

SECONDARY_EXTRACT_PROMPT = """
你是一位资深新闻记者。请从以下新闻文本中提取【次要事件】。

新闻文本：
{news_text}

已知主干事件（请排除此内容，不要重复）：
{backbone_event}

已提取的次要事件（请排除此内容，不要重复）：
{previous_secondary}

提取规则：
- 次要事件是对主干事件的补充、背景、细节说明；
- 每个次要事件同样按5W1H提取；
- 若此时已无新的次要事件可提取，请在 has_more 中返回 false。

输出格式（严格JSON）：
{{
  "has_more": true/false,
  "event": {{
    "who": "",
    "what": "",
    "when": "",
    "where": "",
    "why": "",
    "how": "",
    "summary": "次要事件一句话描述"
  }}
}}
"""

JUDGMENT_PROMPT = """
你是一位专业的新闻真实性核查专家。请根据以下信息判断新闻片段的真实性。

待判断的新闻片段：
{event_description}

相关外部检索信息：
{retrieved_info}

已经核查为真的事实基础（前序判断结果）：
{verified_facts}

判断要求：
1. 对比新闻片段与外部检索信息，寻找内部矛盾（逻辑不一致）和外部矛盾（与已知事实冲突）；
2. 给出明确的真/假判断；
3. 提供详细的判断理由，指出具体矛盾点；
4. 若信息不足以判断，标注为"无法判断"并说明原因。

输出格式（严格JSON）：
{{
  "verdict": "真实/虚假/无法判断",
  "confidence": "高/中/低",
  "contradictions": [
    {{"type": "内部矛盾/外部矛盾", "description": "矛盾描述"}}
  ],
  "reason": "详细判断理由"
}}
"""
