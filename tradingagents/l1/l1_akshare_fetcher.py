#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L1 分析器 — TradingAgents 集成入口

直接复用已调通的 data_loader_fixed（akshare 数据加载）+ analyzer_l1_enhanced_complete（分析引擎）。
不重新实现数据获取逻辑。
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def run_l1_analysis(symbol: str, name: str = "", output_dir: Optional[str] = None, analysis_date: Optional[str] = None) -> str:
    """
    一键执行 L1 双维度分析（年报 + 季报），返回完整报告文本。

    调用链：
      data_loader_fixed.get_stock_profile_safe(symbol, analysis_date=analysis_date)  →  profile dict
      L1FinancialAnalyzerEnhanced.analyze_dual(symbol, name, profile)  →  L1DualPeriodResult
      → dual_report (str)

    Args:
        symbol: A股6位代码，如 '600519'、'000001'
        name:   股票名称，可选
        output_dir: 若指定，将原始 L1 报告保存到该目录下的 0_l1_raw/l1_structured_report.md
        analysis_date: 分析日期 YYYY-MM-DD，用于过滤该日期之后的报告期（防 look-ahead bias）
                   用于审计大模型幻觉问题

    Returns:
        L1DualPeriodResult.dual_report（年报+季报双维度报告）
    """
    from .data_loader_fixed import get_stock_profile_safe
    from .analyzer_l1_enhanced_complete import L1FinancialAnalyzerEnhanced

    # 1. 加载数据（使用已调通的 akshare 数据加载器）
    logger.info(f"[L1] Loading profile for {symbol}...")
    profile = get_stock_profile_safe(symbol, analysis_date=analysis_date)

    if not profile or all(
        v is None or (hasattr(v, 'empty') and v.empty)
        for v in profile.values()
    ):
        return f"L1分析失败：无法获取 {symbol} 的财报数据，请检查网络连接或股票代码是否正确。"

    # 2. 运行分析
    logger.info(f"[L1] Running analyze_dual for {symbol}...")
    analyzer = L1FinancialAnalyzerEnhanced(debug=False)
    result = analyzer.analyze_dual(symbol, name=name or symbol, profile=profile)

    report = result.dual_report
    if not report or not report.strip():
        return "L1分析未完成，请检查数据完整性。"

    # 3. 保存原始 L1 报告到磁盘（用于审计 LLM 幻觉）
    if output_dir:
        try:
            l1_dir = Path(output_dir) / "0_l1_raw"
            l1_dir.mkdir(parents=True, exist_ok=True)
            l1_file = l1_dir / "l1_structured_report.md"
            l1_file.write_text(report, encoding="utf-8")
            logger.info(f"[L1] Raw report saved to {l1_file}")
        except Exception as e:
            logger.warning(f"[L1] Failed to save raw report to {output_dir}: {e}")

    return report
