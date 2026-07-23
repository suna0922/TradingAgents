"""Stock data service — routes panel endpoints through the shared DataSession.

历史包袱说明：
- 旧实现每次请求 spawn 一个 data_fetcher.py 子进程（冷启动 5-15s），且
  /technicals 与 /fundamentals 各 spawn 一个做完全相同的工作，超时后
  静默返回全 0 数据。已废弃（data_fetcher.py 保留仅作应急脚本）。
- 现在两个端点共享同一个 DataSession（进程内缓存，keyed by ticker+date），
  与 SSE 分析流用的是同一份数据 —— 面板与 LLM 分析数值天然同源。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from web_app.backend.models import (
    TechnicalIndicators,
    FundamentalsData,
    FundamentalSection,
    FundamentalMetric,
    StockBasicInfo,
)

logger = logging.getLogger(__name__)


_name_load_started = False
_name_load_lock = __import__("threading").Lock()


def preload_name_table() -> None:
    """后台线程预载全市场代码→名称表（幂等，服务启动时调用）。"""
    global _name_load_started
    import threading
    with _name_load_lock:
        if _name_load_started:
            return
        _name_load_started = True

    def _load():
        try:
            from tradingagents.dataflows.akshare_data import _load_name_table
            _load_name_table()
            logger.info("A-share name table preloaded")
        except Exception as e:
            logger.warning("Name table preload failed: %s", e)

    threading.Thread(target=_load, daemon=True).start()


def get_stock_name(ticker: str) -> str:
    """非阻塞名称查询：表已加载→真实名称；未加载→立即返回 ticker 并触发后台加载。

    绝不在请求路径上同步下载全市场代码表（首次下载 ~10-25s，
    会阻塞面板端点 —— 这是旧实现面板 25s 才返回的根因之一）。
    """
    try:
        from tradingagents.dataflows import akshare_data as akd
        if akd._name_cache_loaded:
            return akd.get_stock_name(ticker)
        preload_name_table()   # 触发后台加载，本次先返回 ticker
        return ticker
    except Exception as e:
        logger.warning("get_stock_name failed for %s: %s", ticker, e)
        return ticker


async def get_basic_info(ticker: str) -> StockBasicInfo:
    name = await asyncio.to_thread(get_stock_name, ticker)
    return StockBasicInfo(ticker=ticker, name=name)


# ★ 共享预取：避免两个 REST 端点同时调用 prefetch_panel 导致重复取数
_panel_prefetch_tasks: dict[str, asyncio.Task] = {}
_panel_prefetch_lock = asyncio.Lock()

async def _get_panel(ticker: str, analysis_date: str | None = None) -> dict:
    """获取 DataSession 面板数据 — 共享预取，避免并行调用时的重复取数。"""
    from web_app.backend.services.data_session import DataSession
    ds = DataSession.get_or_create(ticker, analysis_date)
    key = f"{ticker}:{analysis_date}"
    
    # 已有缓存 → 直接返回
    if ds.panel_ready:
        return ds._build_panel_dict()
    
    # 检查是否有正在进行的预取
    async with _panel_prefetch_lock:
        existing = _panel_prefetch_tasks.get(key)
        if existing is None or existing.done():
            # 发起新的预取任务
            task = asyncio.create_task(ds.prefetch_panel())
            _panel_prefetch_tasks[key] = task
        else:
            task = existing
    
    return await task


async def get_technical_data(
    ticker: str, analysis_date: str | None = None
) -> TechnicalIndicators:
    curr_date = analysis_date or date.today().strftime("%Y-%m-%d")
    tech = TechnicalIndicators(
        ticker=ticker, stock_name=get_stock_name(ticker),
        analysis_date=curr_date, latest_price=0,
    )
    try:
        panel = await _get_panel(ticker, curr_date)
    except Exception as e:
        logger.error("Technical panel fetch failed for %s: %s", ticker, e)
        return tech

    t = panel.get("technicals", {})
    if not t or not t.get("latest_price"):
        return tech

    tech.latest_price = t.get("latest_price", 0)
    tech.change_pct = t.get("change_pct", 0)
    tech.sma_5 = t.get("sma_5", 0)
    tech.sma_10 = t.get("sma_10", 0)
    tech.sma_20 = t.get("sma_20", 0)
    tech.sma_60 = t.get("sma_60", 0)
    tech.macd = t.get("macd", 0)
    tech.macd_signal = t.get("macd_signal", 0)
    tech.rsi_6 = t.get("rsi_6", 0)
    tech.rsi_14 = t.get("rsi_14", 0)
    tech.boll_upper = t.get("boll_upper", 0)
    tech.boll_mid = t.get("boll_mid", 0)
    tech.boll_lower = t.get("boll_lower", 0)
    tech.atr_14 = t.get("atr_14", 0)
    tech.kdj_k = t.get("kdj_k", 0)
    tech.kdj_d = t.get("kdj_d", 0)
    tech.kdj_j = t.get("kdj_j", 0)
    tech.volume_ratio = t.get("volume_ratio", 0)

    # ★ 估值指标（从 DataSession 直接读，与 LLM prompt 同源）
    from web_app.backend.services.data_session import DataSession
    ds = DataSession.get_or_create(ticker, curr_date)
    if ds.valuation:
        tech.pe_static = float(ds.valuation.get("市盈率(静态)", [0])[0] if isinstance(ds.valuation.get("市盈率(静态)"), (list,tuple)) else ds.valuation.get("市盈率(静态)", 0) or 0)
        tech.pb = float(ds.valuation.get("市净率(PB)", [0])[0] if isinstance(ds.valuation.get("市净率(PB)"), (list,tuple)) else ds.valuation.get("市净率(PB)", 0) or 0)
        tech.peg = float(ds.valuation.get("PEG", [0])[0] if isinstance(ds.valuation.get("PEG"), (list,tuple)) else ds.valuation.get("PEG", 0) or 0)
        tech.market_cap = float(ds.valuation.get("总市值", [0])[0] if isinstance(ds.valuation.get("总市值"), (list,tuple)) else ds.valuation.get("总市值", 0) or 0)
        tech.ps = float(ds.valuation.get("市销率(PS)", [0])[0] if isinstance(ds.valuation.get("市销率(PS)"), (list,tuple)) else ds.valuation.get("市销率(PS)", 0) or 0)
        tech.dividend_yield = float(ds.valuation.get("股息率", [0])[0] if isinstance(ds.valuation.get("股息率"), (list,tuple)) else ds.valuation.get("股息率", 0) or 0)
    return tech


async def get_fundamentals_data(
    ticker: str, analysis_date: str | None = None
) -> FundamentalsData:
    curr_date = analysis_date or date.today().strftime("%Y-%m-%d")
    fund = FundamentalsData(
        ticker=ticker, stock_name=get_stock_name(ticker), report_date=curr_date,
    )
    try:
        panel = await _get_panel(ticker, curr_date)
    except Exception as e:
        logger.error("Fundamentals panel fetch failed for %s: %s", ticker, e)
        return fund

    sections = panel.get("fundamentals", {}).get("sections", [])
    fund.sections = [
        FundamentalSection(
            title=s.get("title", ""),
            metrics=[
                FundamentalMetric(
                    name=m["name"], value=float(m["value"]), unit=m.get("unit", "")
                )
                for m in s.get("metrics", [])
                if isinstance(m.get("value"), (int, float))
            ],
        )
        for s in sections
    ]
    fund.raw_report_md = "\n".join(
        f"- {m.name}: {m.value}{m.unit}"
        for s in fund.sections for m in s.metrics
    )
    return fund


# ── 历史财务缓存：切换年报/季报时秒回（不受全局 akshare 节流影响）──
_history_cache: dict = {}          # {(ticker, type): (result, monotonic_ts)}
_HISTORY_CACHE_TTL = 900.0         # 15 min，与 akshare 层缓存一致


async def get_fundamentals_history(ticker: str, period_type: str = "annual") -> dict:
    """财务历史趋势：年报最近5年 / 季报最近4季度，18项指定财务指标。

    自动过滤未来报告期（防 look-ahead）。
    """
    import time as _time
    from datetime import date as _date
    cache_key = (ticker, period_type)
    cached = _history_cache.get(cache_key)
    if cached and _time.monotonic() - cached[1] < _HISTORY_CACHE_TTL:
        return cached[0]

    result = {
        "ticker": ticker, "stock_name": get_stock_name(ticker),
        "type": period_type, "periods": [], "metrics": {},
    }
    try:
        def _fetch():
            from tradingagents.dataflows.akshare_data import _safe_call, _get_ak
            return _safe_call(
                _get_ak().stock_financial_abstract_ths,
                symbol=ticker, indicator="按报告期",
            )
        df = await asyncio.to_thread(_fetch)
        if df is None or df.empty:
            return result

        date_col = df.columns[0]
        # 防 look-ahead：过滤掉今天之后的报告期
        today_str = _date.today().strftime("%Y-%m-%d")
        df = df[df[date_col].astype(str) <= today_str]
        if df.empty:
            return result

        if period_type == "annual":
            df = df[df[date_col].astype(str).str.endswith("12-31")].tail(5)
        else:
            df = df[~df[date_col].astype(str).str.endswith("12-31")].tail(4)
        result["periods"] = [str(v) for v in df[date_col]]

        # 18 项指定财务指标（用户选定，不包含估值指标）
        wanted = [
            "营业总收入", "净利润", "扣非净利润",
            "基本每股收益", "每股净资产", "每股资本公积金", "每股未分配利润", "每股经营现金流",
            "净资产收益率", "净资产收益率-摊薄",
            "销售毛利率", "销售净利率",
            "资产负债率", "流动比率", "速动比率", "产权比率",
            "存货周转率", "应收账款周转天数",
        ]
        for cn in wanted:
            matches = [c for c in df.columns if cn in c]
            if not matches:
                continue
            col_name = matches[0].replace("(亿元)", "").replace("(万元)", "").replace("(元)", "").replace("(万)", "").replace("(%)", "").replace("(倍)", "")
            vals = []
            for v in df[matches[0]]:
                s = str(v).replace("亿", "").replace("万", "").replace("%", "").replace(",", "").strip()
                try:
                    vals.append(float(s))
                except ValueError:
                    vals.append(None)
            if any(v is not None for v in vals):
                result["metrics"][col_name] = vals
        _history_cache[cache_key] = (result, _time.monotonic())
    except Exception as e:
        logger.warning("Fundamentals history failed for %s: %s", ticker, e)
    return result
