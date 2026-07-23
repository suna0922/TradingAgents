"""统一数据会话 — 一次取数，全链路共享。

消除 Web 面板 REST + SSE graph_adapter + LLM 工具的重复 API 调用。

使用方式::

    session = DataSession("600519", "2026-07-17")
    panel_data = await session.prefetch_panel()      # L1: ~12s, 面板先出
    await session.prefetch_analysis_background()     # L2+L3: 后台, ~35s
    # SSE stream 和 LLM 工具后续读取 session.ohlcv / session.fundamentals_* / ...
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import date as _date_obj
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 全局会话注册表（keyed by ticker+date，同一标的同一天共享）
_sessions: dict[str, "DataSession"] = {}
_sessions_lock = threading.Lock()


@dataclass
class DataSession:
    """一次取数，全链路共享的数据容器。

    三层结构:
        L1 面板层 (~12s): OHLCV + 30项财务指标 + 估值快照
        L2 新闻层 (~3s):  东方财富新闻
        L3 深度层 (~30s): L1 深度分析（新浪+东财四大报表）
    """

    ticker: str
    date: str

    # ── L1 面板层 ──
    ohlcv: Any = None                       # pd.DataFrame
    fundamentals_md: str = ""               # get_fundamentals() 返回文本
    fundamentals_structured: dict = field(default_factory=dict)
    valuation: dict = field(default_factory=dict)  # {"市盈率(静态)": (18.48, "倍"), ...}

    # ── L2 新闻层 ──
    news_md: str = ""                       # get_news() 返回文本

    # ── L3 深度层 ──
    l1_report: str = ""                     # run_l1_analysis() 返回文本

    # ── 状态 ──
    panel_ready: bool = False
    analysis_ready: bool = False
    _prefetch_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    def get_or_create(cls, ticker: str, date_str: str | None = None) -> "DataSession":
        """获取或创建会话（同一天同一标的全局复用）。"""
        date_str = date_str or _date_obj.today().strftime("%Y-%m-%d")
        key = f"{ticker}:{date_str}"
        with _sessions_lock:
            if key not in _sessions:
                _sessions[key] = cls(ticker=ticker, date=date_str)
            return _sessions[key]

    # ── L1: 面板数据（Web 面板需要的所有数据）──

    async def prefetch_panel(self) -> dict:
        """L1 预取：OHLCV + 财务指标并行取数，估值快照最后合并。

        调用时机: analyze API 收到请求后第一时间执行。
        单个数据源超时不超过 15s，避免 baostock 卡死阻塞全部面板。
        """
        async with self._prefetch_lock:
            if self.panel_ready:
                return self._build_panel_dict()

            loop = asyncio.get_event_loop()

            # ★ OHLCV 和 同花顺 完全独立，并行取数
            async def _fetch_ohlcv_async():
                if self.ohlcv is None:
                    try:
                        def _fn():
                            from tradingagents.dataflows.akshare_data import _load_ohlcv_akshare
                            return _load_ohlcv_akshare(self.ticker, self.date)
                        self.ohlcv = await asyncio.wait_for(
                            loop.run_in_executor(None, _fn), timeout=20.0
                        )
                    except (asyncio.TimeoutError, Exception) as e:
                        logger.warning("OHLCV fetch failed/timed out for %s: %s", self.ticker, e)

            async def _fetch_fund_async():
                if not self.fundamentals_structured:
                    try:
                        def _fn():
                            from tradingagents.dataflows.akshare_data import (
                                get_fundamentals, get_fundamentals_structured,
                            )
                            get_fundamentals(self.ticker, self.date)
                            return get_fundamentals_structured(self.ticker)
                        self.fundamentals_structured = await asyncio.wait_for(
                            loop.run_in_executor(None, _fn), timeout=20.0
                        )
                    except (asyncio.TimeoutError, Exception) as e:
                        logger.warning("Fundamentals fetch failed/timed out for %s: %s", self.ticker, e)

            await asyncio.gather(_fetch_ohlcv_async(), _fetch_fund_async())

            if self.ohlcv is None or self.ohlcv.empty:
                logger.warning("No OHLCV data for %s, panel will be partial", self.ticker)

            # 估值快照
            if not self.valuation:
                try:
                    def _fetch_val():
                        from tradingagents.dataflows.akshare_data import get_valuation_metrics
                        return get_valuation_metrics(self.ticker, self.date)
                    self.valuation = await asyncio.wait_for(
                        loop.run_in_executor(None, _fetch_val), timeout=15.0
                    )
                except Exception as e:
                    logger.warning("Valuation fetch failed for %s: %s", self.ticker, e)

            self.panel_ready = True
            logger.info("DataSession panel prefetch done: %s %s", self.ticker, self.date)
            return self._build_panel_dict()

    # ── L2+L3: 分析数据（LLM 工具需要的深层数据，后台完成）──

    async def prefetch_analysis_background(self) -> None:
        """L2+L3 后台预取: 新闻 + L1 深度分析。

        调用时机: 面板数据推送给前端后，在 SSE 分析流启动前执行。
        耗时: 新闻 ~3s + L1 ~30s（两个独立，可并行但 L1 太重单独跑）
        """
        if self.analysis_ready:
            return

        loop = asyncio.get_event_loop()

        # L2: 新闻（轻量，~3s）
        async def _fetch_news():
            try:
                def _fn():
                    from tradingagents.agents.utils.news_data_tools import get_news
                    from datetime import timedelta
                    start = (_date_obj.today() - timedelta(days=7)).strftime("%Y-%m-%d")
                    return get_news.func(self.ticker, start, self.date)
                self.news_md = await loop.run_in_executor(None, _fn)
            except Exception as e:
                logger.warning("News fetch failed for %s: %s", self.ticker, e)

        # L3: L1 深度分析（重量级，~30s）
        async def _fetch_l1():
            try:
                def _fn():
                    from tradingagents.l1 import run_l1_analysis
                    return run_l1_analysis(
                        self.ticker, name=self.ticker,
                        analysis_date=self.date,
                    )
                self.l1_report = await loop.run_in_executor(None, _fn)
            except Exception as e:
                logger.warning("L1 analysis failed for %s: %s", self.ticker, e)

        # 新闻和 L1 并行获取
        await asyncio.gather(_fetch_news(), _fetch_l1())
        self.analysis_ready = True
        logger.info("DataSession analysis prefetch done: %s %s", self.ticker, self.date)

    # ── 数据格式化 ──

    def _build_panel_dict(self) -> dict:
        """构建 Web 面板需要的数据结构（兼容现有 SSE event 格式）。"""
        from tradingagents.dataflows.akshare_data import (
            _VALUATION_FIELDS,
        )
        import pandas as pd

        price = float(self.ohlcv["Close"].iloc[-1]) if self.ohlcv is not None else 0
        prev_close = (
            float(self.ohlcv["Close"].iloc[-2])
            if self.ohlcv is not None and len(self.ohlcv) > 1
            else price
        )
        change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0

        macd_dif, macd_dea = self._calc_macd()
        boll_u, boll_m, boll_l = self._calc_boll()
        kdj_k, kdj_d, kdj_j = self._calc_kdj()
        tech_data = {
            "latest_price": price,
            "change_pct": change_pct,
            "analysis_date": self.date,
            "sma_5": self._calc_sma(5),
            "sma_10": self._calc_sma(10),
            "sma_20": self._calc_sma(20),
            "sma_60": self._calc_sma(60),
            "macd": macd_dif,
            "macd_signal": macd_dea,
            "rsi_6": self._calc_rsi(6),
            "rsi_14": self._calc_rsi(14),
            "boll_upper": boll_u, "boll_mid": boll_m, "boll_lower": boll_l,
            "atr_14": self._calc_atr(14),
            "kdj_k": kdj_k, "kdj_d": kdj_d, "kdj_j": kdj_j,
            "volume_ratio": self._calc_volume_ratio(), "turn_over": 0,
        }

        fund_sections = []
        if self.fundamentals_structured:
            metrics = []
            # 核心财务指标（前 15 个）
            key_fields = [
                ("total_revenue", "营业总收入", "亿"),
                ("revenue_growth_yoy", "营收同比增长", "%"),
                ("net_income", "净利润", "亿"),
                ("net_income_growth_yoy", "净利润同比增长", "%"),
                ("net_income_(recurring)", "扣非净利润", "亿"),
                ("net_income_growth_yoy_(recurring)", "扣非净利润同比增长", "%"),
                ("eps", "基本每股收益", "元"),
                ("book_value_per_share", "每股净资产", "元"),
                ("capital_reserve_per_share", "每股资本公积金", "元"),
                ("undivided_profit_per_share", "每股未分配利润", "元"),
                ("operating_cf_per_share", "每股经营现金流", "元"),
                ("roe", "净资产收益率(ROE)", "%"),
                ("roe_(diluted)", "ROE(摊薄)", "%"),
                ("gross_margin", "销售毛利率", "%"),
                ("net_margin", "销售净利率", "%"),
                ("debt_to_asset_ratio", "资产负债率", "%"),
                ("current_ratio", "流动比率", ""),
                ("quick_ratio", "速动比率", ""),
                ("equity_ratio", "产权比率", ""),
                ("inventory_turnover", "存货周转率", ""),
                ("receivables_turnover_days", "应收周转天数", "天"),
            ]
            for s_key, cn_name, unit in key_fields:
                val = self.fundamentals_structured.get(s_key)
                if val is not None:
                    metrics.append({"name": cn_name, "value": val, "unit": unit})

            # 估值指标
            for cn_name, (val, unit) in self.valuation.items():
                metrics.append({"name": cn_name, "value": val, "unit": unit})

            # 标明报告期 + 类型
            rpt_date = self.fundamentals_structured.get("report_date", "")
            rpt_label = ""
            if rpt_date:
                if str(rpt_date).endswith("12-31"):
                    rpt_label = f" ({rpt_date} 年报)"
                elif "-03-31" in str(rpt_date):
                    rpt_label = f" ({rpt_date} 一季报)"
                elif "-06-30" in str(rpt_date):
                    rpt_label = f" ({rpt_date} 半年报)"
                elif "-09-30" in str(rpt_date):
                    rpt_label = f" ({rpt_date} 三季报)"
                else:
                    rpt_label = f" ({rpt_date})"
            fund_sections = [{"title": f"核心财务指标{rpt_label}", "metrics": metrics}]

        return {
            "technicals": tech_data,
            "fundamentals": {
                "sections": fund_sections,
                "stock_name": "",  # caller fills
            },
        }

    # ── 技术指标辅助计算 ──

    def _close_series(self):
        return self.ohlcv["Close"] if self.ohlcv is not None else None

    def _calc_sma(self, window: int) -> float:
        s = self._close_series()
        if s is None or len(s) < window:
            return 0
        return round(float(s.tail(window).mean()), 2)

    def _calc_rsi(self, window: int = 14) -> float:
        s = self._close_series()
        if s is None or len(s) < window:
            return 0
        try:
            delta = s.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            avg_gain = gain.ewm(span=window, adjust=False).mean().iloc[-1]
            avg_loss = loss.ewm(span=window, adjust=False).mean().iloc[-1]
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return round(float(100 - 100 / (1 + rs)), 2)
        except Exception:
            return 0

    def _calc_atr(self, window: int = 14) -> float:
        df = self.ohlcv
        if df is None or len(df) < window:
            return 0
        try:
            import pandas as pd
            high, low, close = df["High"], df["Low"], df["Close"]
            tr = pd.concat(
                [high - low, abs(high - close.shift()), abs(low - close.shift())],
                axis=1,
            ).max(axis=1)
            return round(float(tr.tail(window).mean()), 2)
        except Exception:
            return 0

    def _calc_macd(self, fast: int = 12, slow: int = 26, signal: int = 9):
        """标准 MACD（EMA 体系）：返回 (DIF, DEA)。"""
        s = self._close_series()
        if s is None or len(s) < slow:
            return 0, 0
        try:
            ema_fast = s.ewm(span=fast, adjust=False).mean()
            ema_slow = s.ewm(span=slow, adjust=False).mean()
            dif = ema_fast - ema_slow
            dea = dif.ewm(span=signal, adjust=False).mean()
            return round(float(dif.iloc[-1]), 4), round(float(dea.iloc[-1]), 4)
        except Exception:
            return 0, 0

    def _calc_boll(self, window: int = 20):
        """布林带：返回 (upper, mid, lower)。"""
        s = self._close_series()
        if s is None or len(s) < window:
            return 0, 0, 0
        try:
            mid = s.rolling(window).mean()
            std = s.rolling(window).std()
            return (
                round(float((mid + 2 * std).iloc[-1]), 2),
                round(float(mid.iloc[-1]), 2),
                round(float((mid - 2 * std).iloc[-1]), 2),
            )
        except Exception:
            return 0, 0, 0

    def _calc_kdj(self, n: int = 9, m1: int = 3, m2: int = 3):
        """KDJ：返回 (K, D, J)。"""
        df = self.ohlcv
        if df is None or len(df) < n:
            return 0, 0, 0
        try:
            low, high, close = df["Low"], df["High"], df["Close"]
            llv = low.rolling(n).min()
            hhv = high.rolling(n).max()
            rsv = (close - llv) / (hhv - llv + 1e-10) * 100
            k = rsv.ewm(span=m1, adjust=False).mean()
            d = k.ewm(span=m2, adjust=False).mean()
            j = 3 * k - 2 * d
            return (
                round(float(k.iloc[-1]), 2),
                round(float(d.iloc[-1]), 2),
                round(float(j.iloc[-1]), 2),
            )
        except Exception:
            return 0, 0, 0

    def _calc_volume_ratio(self, short: int = 5, long: int = 20) -> float:
        df = self.ohlcv
        if df is None or "Volume" not in df.columns or len(df) < long:
            return 1.0
        try:
            short_avg = df["Volume"].tail(short).mean()
            long_avg = df["Volume"].tail(long).mean()
            return round(float(short_avg / long_avg), 2) if long_avg > 0 else 1.0
        except Exception:
            return 1.0
