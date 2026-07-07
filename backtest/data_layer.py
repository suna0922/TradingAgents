"""回测系统数据获取层。

封装 baostock OHLCV 数据获取和 stockstats 技术指标计算。
与上游 akshare_data.py 保持一致的数据源策略：baostock 主 + akshare fallback。

注意：
- baostock 不是线程安全的，必须使用全局锁保护 login→query→logout
- 返回 DataFrame（非 CSV 字符串），供 L2 执行层 pandas 规则计算使用
"""

import threading
import logging
from typing import Optional, Dict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── baostock 全局锁（与上游 akshare_data.py 共用同一策略）─────────
_BS_LOCK = threading.Lock()

# baostock 登录状态（避免重复 login）
_bs_logged_in = False


def _ensure_login():
    """确保 baostock 已登录。"""
    global _bs_logged_in
    if not _bs_logged_in:
        import baostock as bs
        lg = bs.login()
        _bs_logged_in = (lg.error_code == "0")
        if not _bs_logged_in:
            logger.warning(f"baostock login failed: {lg.error_msg}")
    return _bs_logged_in


class DataLayer:
    """回测系统的数据获取层。

    不同于 TradingAgents 的 CSV-string 接口（给 LLM Agent 用），
    这里提供纯 DataFrame 接口，供 L2 执行层的 pandas/stockstats 规则计算。

    主要数据源：baostock（免费稳定、无反爬）
    """

    def __init__(self, symbol: str, start_date: str, end_date: str):
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self._ohlcv_cache: Optional[pd.DataFrame] = None
        self._inds_cache: Optional[pd.DataFrame] = None   # 带技术指标的 DataFrame
        self._stock_name: Optional[str] = None

    # ── 日线 OHLCV ──────────────────────────────────────────────

    def fetch_ohlcv(self) -> pd.DataFrame:
        """获取完整回测区间的日线前复权数据。

        Returns:
            DataFrame with columns:
                date(DatetimeIndex), open, high, low, close, volume,
                amount, pct_chg, turn(turnover)

            baostock 返回的 adjustflag=2（前复权），避免回测时除权除息数据跳变。
            数据只请求一次并内存缓存。
        """
        if self._ohlcv_cache is not None:
            return self._ohlcv_cache

        import baostock as bs

        code = self._resolve_bs_code(self.symbol)
        bs_start = self._fmt_date(self.start_date)
        bs_end = self._fmt_date(self.end_date)

        with _BS_LOCK:
            try:
                if not _ensure_login():
                    raise RuntimeError("baostock login failed")

                rs = bs.query_history_k_data_plus(
                    code,
                    "date,open,high,low,close,volume,amount,pctChg,turn",
                    start_date=bs_start,
                    end_date=bs_end,
                    frequency="d",
                    adjustflag="2",       # 前复权
                )
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())

            except Exception as e:
                logger.error(f"baostock query error for {code}: {e}")
                # 尝试 fallback
                try:
                    bs.logout()
                except Exception:
                    pass
                return self._fallback_fetch()
            finally:
                try:
                    bs.logout()
                except Exception:
                    pass

        if not rows:
            logger.warning(f"No data returned for {code} [{bs_start} ~ {bs_end}]")
            # 尝试 fallback
            return self._fallback_fetch()

        df = pd.DataFrame(rows, columns=[
            "date", "open", "high", "low", "close",
            "volume", "amount", "pct_chg", "turn",
        ])

        # 类型转换
        for col in ["open", "high", "low", "close", "volume", "pct_chg", "turn"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["date"] = pd.to_datetime(df["date"])

        # 清洗：删除无收盘价的行
        df = df.dropna(subset=["close"]).reset_index(drop=True)

        # 设置日期索引
        df = df.set_index("date").sort_index()

        self._ohlcv_cache = df
        logger.info(f"DataLayer: fetched {len(df)} bars for {self.symbol}"
                     f" [{df.index[0].date()} ~ {df.index[-1].date()}]")
        return df

    def _fallback_fetch(self) -> pd.DataFrame:
        """东方财富 fallback（akshare）。"""
        try:
            import akshare as ak
            logger.info(f"Trying akshare fallback for {self.symbol}")

            df = ak.stock_zh_a_hist(
                symbol=self.symbol,
                period="daily",
                start_date=self.start_date.replace("-", ""),
                end_date=self.end_date.replace("-", ""),
                adjust="qfq",          # 前复权
            )

            if df is not None and len(df) > 0:
                # 统一列名
                col_map = {
                    "日期": "date", "开盘": "open", "收盘": "close",
                    "最高": "high", "最低": "low", "成交量": "volume",
                    "成交额": "amount", "涨跌幅": "pct_chg", "换手率": "turn",
                }
                df = df.rename(columns=col_map)

                numeric_cols = ["open", "high", "low", "close",
                                "volume", "amount", "pct_chg", "turn"]
                for col in numeric_cols:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")

                df["date"] = pd.to_datetime(df["date"])
                df = df.dropna(subset=["close"]).set_index("date").sort_index()

                self._ohlcv_cache = df
                logger.info(f"Fallback OK: {len(df)} bars from akshare")
                return df

        except Exception as e:
            logger.error(f"akshare fallback also failed: {e}")

        # 返回空 DataFrame 而非崩溃
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "volume",
                      "amount", "pct_chg", "turn"]
        )

    # ── 技术指标计算 ────────────────────────────────────────────

    def compute_indicators(self) -> pd.DataFrame:
        """计算常用技术指标并附加到 ohlcv DataFrame 上。

        使用 stockstats 库（项目已有依赖），惰性计算模式。
        预先触发所有需要的指标列以确保后续访问无需重复计算。

        新增列：
            rsi(14), macd, macds, macdh,
            boll, boll_ub, boll_lb,
            kdjk, kdjd, kdjj,
            atr(14),
            close_5_sma, close_20_sma, close_50_sma,

        Returns:
            stockstats.StockDataFrame (DataFrame 子类)，以 date 为 index
        """
        if self._inds_cache is not None:
            return self._inds_cache

        from stockstats import wrap

        df = self.fetch_ohlcv()
        if df.empty:
            return df

        # stockstats wrap 要求 DataFrame 有 open/high/low/close/volume 列
        wrapped = wrap(df.copy())

        # 预先触发所有需要的技术指标计算
        indicator_cols = [
            "rsi",
            "macd", "macds", "macdh",
            "boll", "boll_ub", "boll_lb",
            "kdjk", "kdjd", "kdjj",
            "atr",
            "close_5_sma", "close_20_sma", "close_50_sma",
        ]

        for col in indicator_cols:
            try:
                _ = wrapped[col]
            except (KeyError, Exception):
                logger.debug(f"Indicator {col} not available, skipping")

        # 计算量比（当日成交量 / 20日均线成交量）
        try:
            vol_sma = wrapped["volume"].rolling(window=20).mean()
            wrapped["_volume_ratio"] = wrapped["volume"] / vol_sma.replace(0, np.nan)
        except Exception:
            wrapped["_volume_ratio"] = pd.Series(np.nan, index=wrapped.index)

        self._inds_cache = wrapped
        return wrapped

    # ── 价格查询 ────────────────────────────────────────────────

    def get_price_on(self, dt: str) -> Optional[float]:
        """获取指定日期的收盘价。"""
        df = self.fetch_ohlcv()
        if df.empty:
            return None
        target = pd.Timestamp(dt)
        if target in df.index:
            return float(df.loc[target, "close"])
        # 精确匹配失败，尝试最近的前一个交易日
        mask = df.index <= target
        if mask.any():
            return float(df.loc[mask, "close"].iloc[-1])
        return None

    def get_row_on(self, dt: str, with_indicators: bool = True):
        """获取指定日期的数据行（含技术指标）。

        Returns:
            pd.Series 或 None
        """
        if with_indicators:
            df = self.compute_indicators()
        else:
            df = self.fetch_ohlcv()

        if df.empty:
            return None

        target = pd.Timestamp(dt)
        if target in df.index:
            return df.loc[target]

        mask = df.index <= target
        if mask.any:
            return df.loc[mask].iloc[-1]
        return None

    # ── 股票名称 ────────────────────────────────────────────────

    def get_stock_name(self) -> str:
        """获取股票中文名称。"""
        if self._stock_name is None:
            from tradingagents.dataflows.akshare_data import get_stock_name
            self._stock_name = get_stock_name(self.symbol)
        return self._stock_name

    # ── A股特殊状态检测 ─────────────────────────────────────────

    @staticmethod
    def is_suspended(row: pd.Series) -> bool:
        """检测停牌日（成交量为0 或接近0）。"""
        vol = row.get("volume", 0)
        try:
            vol_val = float(vol)
        except (TypeError, ValueError):
            vol_val = 0
        return vol_val == 0 or (pd.isna(vol_val))

    @staticmethod
    def is_limit_up(row: pd.Series) -> bool:
        """检测涨停日（涨幅 ≥ +9.9%，无法买入）。"""
        pct_chg = row.get("pct_chg", 0)
        try:
            return float(pct_chg) >= 9.9
        except (TypeError, ValueError):
            return False

    @staticmethod
    def is_limit_down(row: pd.Series) -> bool:
        """检测跌停日（跌幅 ≤ -9.9%，无法卖出）。"""
        pct_chg = row.get("pct_chg", 0)
        try:
            return float(pct_chg) <= -9.9
        except (TypeError, ValueError):
            return False

    # ── 辅助方法 ────────────────────────────────────────────────

    def get_latest_report_date(self, as_of_date: Optional[str] = None) -> Optional[str]:
        """获取该股票在指定日期前已发布的最新季度报告发布日。

        Args:
            as_of_date: 截止日期 (YYYY-MM-DD)。只返回 pubDate <= as_of_date 的报告。
                       如果为 None，返回实际最新报告（含未发布的，可能造成 look-ahead）。

        通过 baostock query_profit_data 查询，结果缓存 60 秒。
        """
        import time
        now = time.time()
        # 缓存 key 包含 as_of_date
        cache_key = as_of_date or "latest"
        if not hasattr(self, '_report_cache'):
            self._report_cache: Dict[str, tuple] = {}
        if cache_key in self._report_cache:
            cached_time, cached_result = self._report_cache[cache_key]
            if now - cached_time < 60:
                return cached_result

        import baostock as bs
        from datetime import datetime

        code = self._resolve_bs_code(self.symbol)

        # 查询当前和上一年的报告（覆盖跨年窗口）
        current_year = datetime.now().year
        years = [current_year, current_year - 1]
        latest_pub_date: Optional[str] = None

        with _BS_LOCK:
            try:
                if not _ensure_login():
                    return None
                for year in years:
                    for q in range(1, 5):
                        rs = bs.query_profit_data(
                            code=code, year=year, quarter=q
                        )
                        if rs.error_code == "0":
                            while rs.next():
                                row = rs.get_row_data()
                                # rs.fields: ['code','pubDate','statDate','roeAvg',...]
                                pub_date = row[1] if len(row) > 1 else None
                                if pub_date and pub_date.strip():
                                    # 防 look-ahead: 只返回截止日期前发布的
                                    if as_of_date and pub_date > as_of_date:
                                        continue
                                    if latest_pub_date is None or pub_date > latest_pub_date:
                                        latest_pub_date = pub_date
            except Exception as e:
                logger.debug(f"get_latest_report_date failed: {e}")

        self._report_cache[cache_key] = (now, latest_pub_date)
        return latest_pub_date

    def preload_report_dates(self) -> Dict[str, str]:
        """预加载该股票所有季度报告的发布日期。

        一次性查询所有年份 Q1-Q4 的 pubDate，返回 {pubDate: statDate} 映射。
        用于回测中每日 O(1) 查找，避免逐日 baostock 查询超时。

        Returns:
            dict: key=pubDate (YYYY-MM-DD), value=statDate (YYYY-MM-DD, 会计期截止日)
        """
        import time
        now = time.time()
        if hasattr(self, '_report_dates_cache') and now - self._report_dates_cache_time < 86400:
            return self._report_dates_cache

        import baostock as bs
        from datetime import datetime
        code = self._resolve_bs_code(self.symbol)
        result: Dict[str, str] = {}
        current_year = datetime.now().year

        with _BS_LOCK:
            try:
                if not _ensure_login():
                    return result
                for year in range(current_year - 2, current_year + 1):
                    for q in range(1, 5):
                        rs = bs.query_profit_data(code=code, year=year, quarter=q)
                        if rs.error_code == "0":
                            while rs.next():
                                row = rs.get_row_data()
                                pub = row[1] if len(row) > 1 else None
                                stat = row[2] if len(row) > 2 else None
                                if pub and pub.strip():
                                    result[pub.strip()] = stat.strip() if stat else ""
            except Exception as e:
                logger.warning(f"preload_report_dates failed: {e}")

        self._report_dates_cache = result
        self._report_dates_cache_time = now
        return result

    @staticmethod
    def _resolve_bs_code(symbol: str) -> str:
        """将股票代码转为 baostock 格式。"""
        if symbol.startswith("6"):
            return f"sh.{symbol}"
        else:
            return f"sz.{symbol}"

    @staticmethod
    def _fmt_date(d: str) -> str:
        """统一日期格式为 YYYY-MM-DD。"""
        if len(d) == 8 and d.isdigit():
            return f"{d[:4]}-{d[4:6]}-{d[6:]}"
        return d
