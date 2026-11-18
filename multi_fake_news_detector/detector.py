# detector.py
# ============================================================
# 主检测器（对外统一入口）
# 编排：输入校验 → 任务分解 → 子任务执行 → 融合判决 → 输出
# ============================================================

import json
import time
from pathlib import Path
from typing import Optional, List, Dict

from core.lvlm_client       import LVLMClient
from core.task_decomposer    import TaskDecomposer
from core.verifier           import Verifier
from core.fusion             import FusionEngine, DetectionResult
from retrieval.retrieval_manager import RetrievalManager
from utils.image_utils       import validate_image
from utils.logger            import get_logger

logger = get_logger("detector")


class RumorDetector:
    """
    中文图文谣言零样本检测器。

    使用方法：
        detector = RumorDetector()
        result = detector.detect(image_path="xxx.jpg", text="描述文字")
        print(result)
    """

    def __init__(
        self,
        dashscope_api_key: Optional[str] = None,
        bocha_api_key:     Optional[str] = None,
        baidu_api_key:     Optional[str] = None,
        baidu_secret_key:  Optional[str] = None,
        retrieval_strategy: str = "both",
    ):
        # 初始化各模块
        self.lvlm       = LVLMClient(api_key=dashscope_api_key)
        self.retrieval  = RetrievalManager(strategy=retrieval_strategy)
        self.verifier   = Verifier(
            lvlm_client=self.lvlm,
            retrieval_manager=self.retrieval,
        )
        self.fusion     = FusionEngine()
        self.decomposer = TaskDecomposer()

        logger.info("RumorDetector 初始化完成")

    # ── 核心检测接口 ──────────────────────────────────────────

    def detect(self, image_path: str, text: str) -> DetectionResult:
        """
        对单条图文内容执行谣言检测。

        Args:
            image_path: 图片本地路径（.jpg / .png 等）
            text:       配套文字描述

        Returns:
            DetectionResult 包含判决、置信度、推理依据、证据
        """
        start_time = time.time()
        logger.info("=" * 60)
        logger.info(f"开始检测 | 图片: {image_path} | 文本: {text[:50]}...")

        # ── 1. 输入校验 ────────────────────────────────────────
        if not text or not text.strip():
            raise ValueError("文本内容不能为空")

        if not validate_image(image_path):
            raise ValueError(
                f"图片无效或不存在: {image_path}。"
                "请检查路径和格式（支持 jpg/png/webp/gif/bmp）"
            )

        # ── 2. 执行三子任务 ─────────────────────────────────────
        sub_results = self.verifier.run_all(
            image_path=image_path,
            text=text.strip(),
        )

        # ── 3. 多视角融合判决 ────────────────────────────────────
        final_result = self.fusion.fuse(
            sub_results       = sub_results,
            input_text        = text,
            input_image_path  = image_path,
        )

        elapsed = time.time() - start_time
        logger.info(f"检测完成，耗时 {elapsed:.1f}s | 结果: {final_result.verdict}")

        return final_result

    # ── 批量检测 ──────────────────────────────────────────────

    def detect_batch(
        self,
        items: List[Dict],
        output_path: Optional[str] = None,
    ) -> List[Dict]:
        """
        批量检测。

        Args:
            items: 列表，每项包含 {"id": "...", "image_path": "...", "text": "..."}
            output_path: 若提供，将结果写入该 JSON 文件

        Returns:
            检测结果列表（每项为 dict）
        """
        results = []
        total   = len(items)

        logger.info(f"批量检测开始，共 {total} 条")

        for idx, item in enumerate(items, 1):
            item_id     = item.get("id", str(idx))
            image_path  = item.get("image_path", "")
            text        = item.get("text", "")

            logger.info(f"[{idx}/{total}] 处理 id={item_id}")

            try:
                result = self.detect(image_path=image_path, text=text)
                row = {"id": item_id, "status": "success", **result.to_dict()}
            except Exception as e:
                logger.error(f"[{idx}/{total}] 检测失败 id={item_id}: {e}")
                row = {
                    "id":      item_id,
                    "status":  "error",
                    "error":   str(e),
                    "verdict": "ERROR",
                    "label":   "ERROR",
                }

            results.append(row)

            # 简单打印进度
            if "verdict" in row:
                print(f"  [{idx}/{total}] id={item_id} → {row.get('verdict','ERROR')} "
                      f"(置信度: {row.get('confidence', '-')})")

        # 写入文件
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            logger.info(f"批量结果已保存至: {output_path}")

        # 统计摘要
        self._print_summary(results)
        return results

    @staticmethod
    def _print_summary(results: List[Dict]) -> None:
        total      = len(results)
        fake_count = sum(1 for r in results if r.get("label") == "FAKE")
        real_count = sum(1 for r in results if r.get("label") == "REAL")
        unver      = sum(1 for r in results if r.get("label") == "UNVERIFIED")
        error      = sum(1 for r in results if r.get("status") == "error")

        print("\n" + "=" * 50)
        print(f"  批量检测统计")
        print(f"  总计: {total}  谣言: {fake_count}  真实: {real_count}"
              f"  待核实: {unver}  错误: {error}")
        print("=" * 50)
