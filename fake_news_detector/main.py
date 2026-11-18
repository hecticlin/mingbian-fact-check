"""
main.py
虚假新闻检测系统主入口。

用法：
  单条检测：python main.py --news "新闻文本内容"
  文件批量：python main.py --file news_list.jsonl --output results.jsonl
  交互模式：python main.py
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from detector import detect, DetectionResult
from config import LOG_LEVEL

# ============================================================
# 日志配置
# ============================================================
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================
# 结果格式化输出
# ============================================================

def format_result_readable(result: DetectionResult) -> str:
    """将检测结果格式化为可读文本报告"""
    lines = []
    sep = "=" * 60

    lines.append(sep)
    lines.append("📋 虚假新闻检测报告")
    lines.append(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(sep)

    # 新闻摘要
    news_preview = result.news_text[:100].replace("\n", " ")
    lines.append(f"📰 新闻内容（前100字）: {news_preview}...")
    lines.append("")

    # 最终判决
    verdict_icon = {
        "真实": "",
        "虚假": "❌",
        "无法判断": "❓",
    }.get(result.final_verdict, "❓")

    lines.append(f"{verdict_icon} 最终判定: 【{result.final_verdict}】")
    lines.append("")

    # 短路说明
    if result.short_circuited:
        lines.append("⚡ 检测方式: 权威媒体/辟谣平台直接核查（短路）")
        if result.short_circuit_source:
            lines.append(f"   来源: {result.short_circuit_source}")
        lines.append("")

    # 判断理由
    lines.append("📌 判断理由:")
    lines.append(result.final_reason)
    lines.append("")

    # 各分支详情（非短路时）
    if not result.short_circuited and result.branch_results:
        lines.append(f" 分支检测详情 (共{result.total_branches}个分支):")
        lines.append("-" * 40)
        for i, branch in enumerate(result.branch_results, 1):
            verdict_icon_b = {"真实": "", "虚假": "❌", "无法判断": "❓", "分歧未解决": ""}.get(branch.verdict, "❓")
            lines.append(f"\n{i}. [{branch.branch_label}] {verdict_icon_b} {branch.verdict}")
            lines.append(f"   事件: {branch.event_summary[:80]}...")

            if branch.agreed:
                lines.append(f"   理由: {branch.reason[:200]}")
            else:
                lines.append(f"    两阶段分歧未解决（复核{branch.review_rounds}轮）")
                lines.append(f"   [初判] {branch.deepseek_verdict} - {branch.deepseek_reason[:100]}")
                lines.append(f"   [复核] {branch.review_verdict} - {branch.review_reason[:100]}")

    # 事件抽取概览
    if result.extraction and not result.short_circuited:
        lines.append("")
        lines.append("📊 事件抽取概览:")
        lines.append(f"   主干事件: {result.extraction.backbone.summary[:80]}")
        lines.append(f"   次要事件: {len(result.extraction.secondary_events)} 个")
        lines.append(f"   隐含信息: {len(result.extraction.implicit_infos)} 类")

    lines.append("")
    lines.append(sep)
    return "\n".join(lines)


def result_to_dict(result: DetectionResult) -> dict:
    """将检测结果序列化为字典（用于JSON输出）"""
    branches = []
    for b in result.branch_results:
        entry = {
            "branch_type": b.branch_type,
            "branch_label": b.branch_label,
            "event_summary": b.event_summary,
            "verdict": b.verdict,
            "agreed": b.agreed,
            "reason": b.reason,
            "review_rounds": b.review_rounds,
        }
        if not b.agreed:
            entry["deepseek"] = {"verdict": b.deepseek_verdict, "reason": b.deepseek_reason}
            entry["review"] = {"verdict": b.review_verdict, "reason": b.review_reason}
        branches.append(entry)

    out = {
        "news_preview": result.news_text[:200],
        "final_verdict": result.final_verdict,
        "final_reason": result.final_reason,
        "short_circuited": result.short_circuited,
        "short_circuit_source": result.short_circuit_source,
        "total_branches": result.total_branches,
        "fake_branches": result.fake_branches,
        "branch_results": branches,
        "timestamp": datetime.now().isoformat(),
    }
    return out


# ============================================================
# 批量检测
# ============================================================

def batch_detect(input_file: str, output_file: str):
    """
    批量检测JSONL文件中的新闻。
    输入格式每行: {"id": "xxx", "text": "新闻正文"}
    输出格式每行: 检测结果JSON
    """
    in_path = Path(input_file)
    out_path = Path(output_file)

    if not in_path.exists():
        logger.error(f"输入文件不存在: {input_file}")
        sys.exit(1)

    total = 0
    success = 0
    fake_count = 0

    with open(in_path, "r", encoding="utf-8") as fin, \
         open(out_path, "w", encoding="utf-8") as fout:

        for line_num, line in enumerate(fin, 1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                news_id = item.get("id", str(line_num))
                news_text = item.get("text", "")

                if not news_text:
                    logger.warning(f"第{line_num}行: 新闻文本为空，跳过")
                    continue

                logger.info(f"处理第{line_num}条新闻 (id={news_id})")
                result = detect(news_text)
                out_dict = result_to_dict(result)
                out_dict["id"] = news_id

                fout.write(json.dumps(out_dict, ensure_ascii=False) + "\n")
                fout.flush()

                total += 1
                success += 1
                if result.final_verdict == "虚假":
                    fake_count += 1

            except Exception as e:
                logger.error(f"第{line_num}条处理失败: {e}")
                total += 1

    logger.info(f"\n批量检测完成: 共{total}条，成功{success}条，虚假{fake_count}条")
    logger.info(f"结果已保存至: {output_file}")


# ============================================================
# 交互模式
# ============================================================

def interactive_mode():
    print("\n 虚假新闻检测系统（交互模式）")
    print("输入新闻文本，按两次回车开始检测，输入 'quit' 退出\n")

    while True:
        print("-" * 40)
        print("请输入新闻文本（两次回车确认）：")
        lines = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line == "quit":
                print("退出系统")
                return
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)

        news_text = "\n".join(lines).strip()
        if not news_text:
            print("新闻文本为空，请重新输入")
            continue

        print("\n⏳ 检测中，请稍候...\n")
        try:
            result = detect(news_text)
            print(format_result_readable(result))
        except Exception as e:
            logger.error(f"检测失败: {e}", exc_info=True)
            print(f"❌ 检测出错: {e}")


# ============================================================
# CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="基于大语言模型隐含语义增强的细粒度虚假新闻检测系统（改进版）",
    )
    parser.add_argument("--news", type=str, help="单条新闻文本")
    parser.add_argument("--file", type=str, help="批量输入文件（JSONL格式）")
    parser.add_argument("--output", type=str, default="results.jsonl", help="批量输出文件")
    parser.add_argument("--json", action="store_true", help="以JSON格式输出单条结果")

    args = parser.parse_args()

    if args.file:
        batch_detect(args.file, args.output)

    elif args.news:
        logger.info("开始单条新闻检测...")
        result = detect(args.news)
        if args.json:
            print(json.dumps(result_to_dict(result), ensure_ascii=False, indent=2))
        else:
            print(format_result_readable(result))

    else:
        interactive_mode()


if __name__ == "__main__":
    main()