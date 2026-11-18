# 中文图文谣言零样本检测系统

基于 MMD-Agent 范式，使用 Qwen2.5-VL + 博查/百度检索 实现零样本中文图文谣言检测。

## 项目结构

```
chinese_rumor_detector/
├── config/
│   └── settings.py          # API密钥与全局配置
├── core/
│   ├── lvlm_client.py       # Qwen2.5-VL 调用封装
│   ├── task_decomposer.py   # 层级任务分解（MMD-Agent核心）
│   ├── verifier.py          # 三子任务执行（文本/图像/图文）
│   └── fusion.py            # 多视角推理融合 + 最终判决
├── retrieval/
│   ├── bocha_retriever.py   # 博查搜索API
│   ├── baidu_retriever.py   # 百度AI搜索API
│   └── retrieval_manager.py # 检索管理器（统一入口）
├── utils/
│   ├── image_utils.py       # 图片读取/编码/预处理
│   └── logger.py            # 日志工具
├── data/
│   └── samples/             # 放置测试图片和文本
├── detector.py              # 主检测器（对外统一入口）
├── run.py                   # 命令行运行脚本
└── requirements.txt
```

## 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置 API 密钥
编辑 `config/settings.py`，填入你的密钥：
- `DASHSCOPE_API_KEY`：阿里云 DashScope
- `BOCHA_API_KEY`：博查搜索
- `BAIDU_API_KEY` / `BAIDU_SECRET_KEY`：百度 AI 搜索

### 3. 运行检测

**单条检测（交互式）：**
```bash
python run.py --image data/samples/test.jpg --text "这张图片显示某地发生了大规模事故"
```

**批量检测（JSON文件）：**
```bash
python run.py --batch data/samples/batch_input.json --output results.json
```

## 输入格式

**命令行单条：**
```
--image  图片文件路径
--text   配套文字描述
```

**批量 JSON（batch_input.json）：**
```json
[
  {
    "id": "001",
    "image_path": "data/samples/img1.jpg",
    "text": "配套文字描述..."
  }
]
```

## 输出格式

```json
{
  "verdict": "谣言",
  "confidence": 0.87,
  "label": "FAKE",
  "reasoning": {
    "text_check": "文本声称...",
    "image_check": "图片经检索发现...",
    "consistency_check": "图文存在明显矛盾..."
  },
  "evidence": ["检索到的证据1", "检索到的证据2"],
  "retrieval_queries": ["搜索词1", "搜索词2"]
}
```
