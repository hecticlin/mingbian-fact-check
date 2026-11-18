# config/settings.py
# ============================================================
# 全局配置：API 密钥、模型参数、检测阈值
# ============================================================

import os

# DashScope（Qwen2.5-VL）
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")

# 主模型（优先使用）
LVLM_MODEL = "qwen-vl-max-2025-08-13"

# 自动切换备用模型列表
# 当前模型额度耗尽或触发限速时，按顺序自动切换下一个模型
# 对应阿里云 DashScope 控制台中已开通的 qwen-vl-max 系列模型
LVLM_FALLBACK_MODELS = [
    "qwen-vl-max-2025-08-13",   # 主选，已开启
    "qwen-vl-max-latest",
    "qwen-vl-max-2025-04-02",
    "qwen-vl-max-1119",
    "qwen-vl-max-2025-04-08",
    "qwen-vl-max-1230",
    "qwen-vl-max",
    "qwen-vl-max-2025-01-25",
]

# 触发切换的错误码（DashScope 额度耗尽 / 限速相关）
LVLM_QUOTA_ERROR_CODES = {
    "Arrearage",          # 账户欠费
    "QuotaExceeded",      # 免费额度耗尽
    "RateLimitExceeded",  # 请求速率超限
    "PaymentRequired",    # 需要付费
    "insufficient_quota", # 余额不足
    "DataInspectionFailed",  # 偶发触发时可能也需要切换
}

LVLM_MAX_TOKENS  = 2048
LVLM_TEMPERATURE = 0.1   # 检测任务要低温，提高稳定性

# 博查搜索 API
BOCHA_API_KEY = os.getenv("BOCHA_API_KEY", "")
BOCHA_API_URL    = "https://api.bochaai.com/v1/web-search"
BOCHA_MAX_RESULTS = 5

# 检索策略
# 仅使用博查搜索（已移除百度搜索）
RETRIEVAL_STRATEGY = "bocha_only"

# 判决阈值
FAKE_THRESHOLD = 0.60
REAL_THRESHOLD = 0.35

# 各子任务权重（三者之和应为 1.0）
WEIGHT_TEXT        = 0.35
WEIGHT_IMAGE       = 0.30
WEIGHT_CONSISTENCY = 0.35

# 请求超时（秒）
REQUEST_TIMEOUT = 30

# 日志
LOG_LEVEL = "INFO"
LOG_FILE  = "rumor_detector.log"