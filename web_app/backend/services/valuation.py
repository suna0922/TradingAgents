"""估值指标计算模块：总市值、PS、股息率、Beta。

供 web_app 和 tradingagents 主程序共用。
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np

_log = logging.getLogger(__name__)


def calc_valuation_metrics(ticker: str, ohlcv_df, fundamentals_sections, fhps_df=None):
    """从已有数据计算估值指标，返回 (额外指标列表, 追加的 raw_report_md 行)。

    ohlcv_df: OHLCV DataFrame (含 Date/Close 列)
    fundamentals_sections: list[FundamentalSection] (web) 或 list[dict]
    fhps_df: stock_fhps_detail_ths 返回的分红 DataFrame（可选）
    """
    extra_metrics = []
    extra_lines = []

    if ohlcv_df is None or ohlcv_df.empty:
        return extra_metrics, extra_lines

    price = float(ohlcv_df["Close"].iloc[-1])
    if price <= 0:
        return extra_metrics, extra_lines

    # ---- 1. 总股本 → 总市值 ----
    total_shares = _calc_total_shares(ticker, fundamentals_sections)
    market_cap = None
    if total_shares and total_shares > 0:
        market_cap = round(price * total_shares / 1e8, 2)  # 亿
        extra_metrics.append(_metric("总市值", market_cap, "亿"))
        extra_lines.append(f"- 总市值: {market_cap}亿")

    # ---- 2. PS 市销率 = 总市值 / 营收 ----
    revenue = _find_metric_val(fundamentals_sections, "营业总收入")
    if market_cap and revenue and revenue > 0:
        ps = round(market_cap / revenue, 2)
        extra_metrics.append(_metric("市销率(PS)", ps, "倍"))
        extra_lines.append(f"- 市销率(PS): {ps}倍")

    # ---- 3. 股息率 ----
    if fhps_df is not None and not fhps_df.empty and "税前分红率" in fhps_df.columns:
        # 倒序查找最近一条有效分红方案
        for _, row in fhps_df[::-1].iterrows():
            div_rate = str(row.get("税前分红率", "")).strip()
            if div_rate and div_rate not in ("--", "nan", "None", ""):
                try:
                    div_val = float(div_rate.replace("%", ""))
                    extra_metrics.append(_metric("股息率", div_val, "%"))
                    extra_lines.append(f"- 股息率: {div_val}%")
                except (ValueError, TypeError):
                    pass
                break

    # ---- 4. Beta (指数基准可能不可用，静默跳过) ----
    beta_val = _calc_beta(ohlcv_df)
    if beta_val is not None:
        extra_metrics.append(_metric("Beta系数", beta_val, ""))
        extra_lines.append(f"- Beta系数: {beta_val}")

    return extra_metrics, extra_lines


def _metric(name, value, unit):
    """创建 FundamentalMetric (web) 或简单 dict (主程序)"""
    try:
        from web_app.backend.models import FundamentalMetric
        return FundamentalMetric(name=name, value=value, unit=unit)
    except ImportError:
        return {"name": name, "value": value, "unit": unit}


def _find_metric_val(sections, name_contains):
    """从 sections 中查找指标值 (兼容 Pydantic model 和 dict)"""
    for sec in sections:
        if isinstance(sec, dict):
            metrics = sec.get("metrics", [])
        else:
            metrics = getattr(sec, "metrics", [])
        for m in metrics:
            if isinstance(m, dict):
                name = m.get("name", "")
                val = m.get("value")
            else:
                name = getattr(m, "name", "")
                val = getattr(m, "value", None)
            if name_contains in str(name):
                return val
    return None


def _calc_total_shares(ticker: str, sections) -> float | None:
    """从总资产和资产负债率推算净资产→总股本"""
    try:
        import akshare as ak
        fa_df = ak.stock_financial_analysis_indicator(symbol=ticker, start_year=str(date.today().year - 2))
        if fa_df is None or fa_df.empty:
            return None
        latest = fa_df.iloc[-1]
        total_assets = _safe_float(latest.get("总资产(元)"))
        debt_ratio = _safe_float(latest.get("资产负债率(%)"))
        bvps = _find_metric_val(sections, "每股净资产")
        if total_assets and debt_ratio is not None and bvps:
            net_assets = total_assets * (1 - debt_ratio / 100)  # 元
            shares = net_assets / bvps  # 股
            return shares
    except Exception:
        _log.debug("Total shares calc failed for %s", ticker, exc_info=True)
    return None


def _safe_float(val):
    if val is None or str(val) in ("", "nan", "None"):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _calc_beta(ohlcv_df, window: int = 60) -> float | None:
    """用最近 window 个交易日计算个股 Beta vs 沪深300。

    若市场基准不可用（baostock不支持指数、东方财富被反爬），返回 None。
    """
    try:
        from tradingagents.dataflows.akshare_data import _load_ohlcv_akshare

        bench = _load_ohlcv_akshare("000300", date.today().strftime("%Y-%m-%d"))
        if bench is None or bench.empty:
            _log.debug("Beta: no benchmark data available")
            return None

        common = ohlcv_df.merge(bench, on="Date", suffixes=("_s", "_b")).tail(window)
        if len(common) < 20:
            return None

        stock_ret = common["Close_s"].pct_change().dropna()
        bench_ret = common["Close_b"].pct_change().dropna()
        if len(stock_ret) < 10:
            return None

        cov = np.cov(stock_ret, bench_ret)[0][1]
        var = np.var(bench_ret)
        if var == 0:
            return None
        return round(float(cov / var), 2)
    except Exception:
        _log.debug("Beta calc skipped (benchmark unavailable)", exc_info=True)
        return None
