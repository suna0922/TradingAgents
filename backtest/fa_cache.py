"""L1 基本面指标缓存与展平工具。

职责：
- 将 L1AnalysisResult 中所有标量字段（float/int/bool）展平为 fa_ 前缀的键值对
- 年报字段加 `annual_` 前缀，季报字段加 `quarter_` 前缀
- 趋势字段取最新值（list[-1]），命名 `{prefix}_{field_name}_trend`
- 排除 List/Dict/str/None 类型字段（不可比较）

使用方式：
    from backtest.fa_cache import flatten_l1_result, FA_CACHE_SCALAR_FIELDS

    metrics = flatten_l1_result(dual_result.annual, prefix="annual")
    # → {"annual_roe": 22.5, "annual_debt_ratio": 35.2, ...}
"""

import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


# ── L1AnalysisResult 中所有标量字段名（float/int/bool 类型） ──────
# 这些字段会被展平注入到 ExecutionEngine 的 row_dict 中

ANNUAL_SCALAR_FIELDS: List[str] = [
    # ── 基本信息（标量部分） ──
    "data_period", "report_date",
    
    # ── 资本结构 ──
    "debt_ratio", "interest_debt", "cash_coverage",
    "short_term_debt_ratio", "long_term_debt_ratio",
    "interest_bearing_debt",
    "operating_debt_ratio", "financing_debt_ratio",
    "short_long_ratio", "finance_cost_ratio", "interest_coverage",
    
    # ── 资产质量 ──
    "production_asset_ratio", "production_asset_roe",
    "receivables_ratio", "goodwill_ratio", "inventory_ratio",
    "r_d_capitalize_ratio", "non_core_asset_ratio",
    "payables_ratio", "receivables_to_payables",
    "other_rece_change", "real_debt_ratio_ex_goodwill",
    
    # ── 盈利与现金流 ──
    "roe", "net_margin", "gross_margin",
    "ocf_to_netprofit", "dividend_payout", "fin_assets_ratio",
    "dividend_ocf_ratio", "net_profit_margin_parent",
    
    # ── 风险信号 ──
    "cash_excess_signal", "high_cash_high_debt",
    "other_monetary", "operating_cycle_days",
    "inefficient_cash", "cash_ratio", "cash", "cash_assets",
    
    # ── 综合评分 ──
    "overall_score", "pass_l1",
]

# 趋势字段：取 list[-1] 作为最新值注入
TREND_SCALAR_FIELDS: List[str] = [
    "roe_trend", "net_profit_margin_parent_trend", "ocf_to_netprofit_trend",
    "debt_ratio_trend", "equity_trend", "interest_debt_trend",
    "cash_coverage_trend", "cash_ratio_trend",
    "r_d_expense_trend", "gross_margin_trend",
    "inventory_ratio_trend", "receivables_ratio_trend",
    "goodwill_ratio_trend", "non_core_asset_ratio_trend",
    "other_receivable_ratio_trend", "production_asset_roe_trend",
    "short_long_ratio_trend", "payables_ratio_trend",
    "dividend_payout_trend", "dividend_ocf_ratio_trend",
    "finance_cost_ratio_trend", "interest_coverage_trend",
    "receivables_to_payables_trend",
    "revenue_trend", "net_profit_trend",
    "revenue_growth_trend", "profit_growth_trend",
    "asset_turnover_trend", "current_ratio_trend", "quick_ratio_trend",
    "ocf_trend", "capex_trend", "fcf_trend",
]


def flatten_l1_result(
    result: Any,  # L1AnalysisResult
    prefix: str,
) -> Dict[str, Any]:
    """将 L1AnalysisResult 中所有标量字段展平为 {prefix}_{field} 键值对。

    Args:
        result: L1AnalysisResult 实例
        prefix: 键前缀，如 "annual" 或 "quarter"

    Returns:
        展平后的字典，如 {"annual_roe": 22.5, "annual_debt_ratio": 35.2}
        空 result 返回空字典。
    """
    if result is None:
        return {}
    
    metrics: Dict[str, Any] = {}
    
    # ── 标量字段 ──
    for field_name in ANNUAL_SCALAR_FIELDS:
        val = _safe_getattr(result, field_name)
        if val is None:
            continue
        # bool → float
        if isinstance(val, bool):
            val = 1.0 if val else 0.0
        # str → 跳过（不可比较）
        if isinstance(val, str):
            continue
        # float/int
        try:
            metrics[f"{prefix}_{field_name}"] = float(val)
        except (TypeError, ValueError):
            continue
    
    # ── 趋势字段（取最新值） ──
    for field_name in TREND_SCALAR_FIELDS:
        trend_list = _safe_getattr(result, field_name)
        if trend_list is None:
            continue
        if not isinstance(trend_list, list) or len(trend_list) == 0:
            continue
        # 取最后一个值（最新一期）
        latest_val = trend_list[-1]
        if latest_val is None:
            continue
        try:
            metrics[f"{prefix}_{field_name}"] = float(latest_val)
        except (TypeError, ValueError):
            continue
    
    return metrics


def flatten_dual_period(
    dual_result: Any,  # L1DualPeriodResult
) -> Dict[str, Any]:
    """将 L1DualPeriodResult 的年度和季度指标合并展平。

    Args:
        dual_result: L1DualPeriodResult 实例

    Returns:
        合并后的字典，同时包含 annual_* 和 quarter_* 前缀的指标。
    """
    if dual_result is None:
        return {}
    
    metrics = {}
    
    # 年报
    if hasattr(dual_result, 'annual') and dual_result.annual is not None:
        metrics.update(flatten_l1_result(dual_result.annual, prefix="annual"))
    
    # 季报
    if hasattr(dual_result, 'quarter') and dual_result.quarter is not None:
        metrics.update(flatten_l1_result(dual_result.quarter, prefix="quarter"))
    
    return metrics


def _safe_getattr(obj: Any, name: str) -> Any:
    """安全获取属性，不存在或异常时返回 None。"""
    try:
        return getattr(obj, name, None)
    except Exception:
        return None
