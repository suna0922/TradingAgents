#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L1 财报分析器包

提供完整的6维64指标基本面分析能力。
用于替换 TradingAgents 原有基本面数据工具。

核心链路：
  run_l1_analysis(code) → data_loader_fixed.get_stock_profile_safe()
                      → L1FinancialAnalyzerEnhanced.analyze_dual()
                      → dual_report (Markdown str)
"""

from .l1_result import L1AnalysisResult, L1DualPeriodResult
from .l1_akshare_fetcher import run_l1_analysis
from .data_loader_fixed import get_stock_profile_safe

# 导出分析器类（供高级用法）
try:
    from .analyzer_l1_enhanced_complete import L1FinancialAnalyzerEnhanced
except Exception:
    L1FinancialAnalyzerEnhanced = None

__all__ = [
    "L1AnalysisResult",
    "L1DualPeriodResult",
    "L1FinancialAnalyzerEnhanced",
    "run_l1_analysis",
    "get_stock_profile_safe",
]
