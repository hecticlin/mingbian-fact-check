#!/usr/bin/env python3
# run.py
# ============================================================
# 命令行运行脚本
# 用法：
#   单条：python run.py --image xxx.jpg --text "描述文字"
#   批量：python run.py --batch input.json --output results.json
# ============================================================

import argparse
import json
import sys
import os

# 将项目根目录加入 sys.path（确保子目录导入正确）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from detector import RumorDetector


def parse_args():
    parser = argparse.ArgumentParser(
        description="中文图文谣言零样本检测系统",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--image",
        type=str,
        help="图片文件路径（单条检测）",
    )
    mode.add_argument(
        "--batch",
        type=str,
        help=(
            "批量检测 JSON 文件路径\n"
            "格式：[\n"
            '  {"id":"001","image_path":"a.jpg","text":"文字..."}\n'
            "]"
        ),
    )

    parser.add_argument(
        "--text",
        type=str,
        help="配套文字描述（单条检测时必填）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="批量检测结果输出路径（默认不保存）",
    )
    parser.add_argument(
        "--strategy",
        type=str,
        default="both",
        choices=["both", "bocha_only", "baidu_only"],
        help="检索策略（默认: both）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出单条检测结果",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    # 初始化检测器
    detector = RumorDetector(retrieval_strategy=args.strategy)

    # ── 单条检测 ──────────────────────────────────────────────
    if args.image:
        if not args.text:
            print("❌ 单条检测模式下必须提供 --text 参数")
            sys.exit(1)

        print(f"\n🔍 开始检测...")
        print(f"   图片: {args.image}")
        print(f"   文本: {args.text[:80]}{'...' if len(args.text) > 80 else ''}\n")

        try:
            result = detector.detect(image_path=args.image, text=args.text)

            if args.json:
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(result)

        except Exception as e:
            print(f"❌ 检测失败: {e}")
            sys.exit(1)

    # ── 批量检测 ──────────────────────────────────────────────
    elif args.batch:
        try:
            with open(args.batch, "r", encoding="utf-8") as f:
                items = json.load(f)
        except FileNotFoundError:
            print(f"❌ 批量输入文件不存在: {args.batch}")
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"❌ JSON 格式错误: {e}")
            sys.exit(1)

        if not isinstance(items, list):
            print("❌ 批量输入文件应为 JSON 数组")
            sys.exit(1)

        print(f"\n🔍 批量检测：{len(items)} 条数据\n")

        try:
            detector.detect_batch(items=items, output_path=args.output)
        except Exception as e:
            print(f"❌ 批量检测失败: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
