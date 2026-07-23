"""akshare data vendor adapter for TradingAgents.

Provides all required data interfaces using domestic Chinese financial data sources
(东方财富/同花盛) via the akshare library. Falls back to yfinance for non-A-share
tickers (US stocks, etc.) that akshare cannot serve reliably.
"""

from __future__ import annotations

import time
import threading
import logging
import re
from datetime import datetime, timedelta
from typing import Annotated, Optional, Dict

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ── Global throttle for 东方财富 anti-scraping ──────────────────
# All akshare calls that hit 东方财富 servers must respect this.
_ak_last_call_time: float = 0.0
_ak_lock = threading.Lock()
_AK_MIN_INTERVAL: float = 3.0  # seconds between requests (conservative)

# ── Baostock session lock ────────────────────────────────────────
# baostock is NOT thread-safe; concurrent logins corrupt the TCP
# connection and produce garbled (GBK) responses.  All callers must
# hold _bs_lock for the entire login → query → logout cycle.
_bs_lock = threading.Lock()

# ── Response dedup cache ───────────────────────────────────────────
# Prevents redundant HTTP calls within the same analysis run.
# e.g. get_stock_data AND get_indicators both fetch OHLCV for the
# same symbol — only one actual request to 东方财富, second returns cache.
_ak_cache: dict = {}          # {cache_key: (result, timestamp)}
_ak_cache_lock = threading.Lock()
# 15 min — 必须长于一次完整 LLM 分析（数分钟），否则分析中期缓存过期
# 会导致 agent 工具重新取数并吃 3s 节流。财报/日K 数据 15 分钟内不会变化。
_AK_CACHE_TTL: float = 900.0


def _make_cache_key(func_name: str, *args, **kwargs) -> str:
    """Build a stable cache key from function name and arguments."""
    parts = [func_name]
    for a in args:
        parts.append(repr(a))
    for k in sorted(kwargs):
        parts.append(f"{k}={repr(kwargs[k])}")
    return "|".join(parts)


def _ak_throttle() -> None:
    """Sleep if needed to enforce minimum interval between akshare calls."""
    global _ak_last_call_time
    with _ak_lock:
        now = time.monotonic()
        elapsed = now - _ak_last_call_time
        if elapsed < _AK_MIN_INTERVAL:
            wait = _AK_MIN_INTERVAL - elapsed
            logger.debug(f"akshare throttling: sleeping {wait:.1f}s")
            time.sleep(wait)
        _ak_last_call_time = time.monotonic()

# ── Ticker classification ────────────────────────────────────────
# A-share tickers: 6 digits (000xxx Shanghai, 000xxx Shenzhen,
# 300xxx ChiNext, 688xxx STAR, 60xxxx Shanghai main)
_ASHARE_RE = re.compile(r"^\d{6}$")
# Hong Kong: 5 digits starting with 0-9
_HK_RE = re.compile(r"^\d{4,5}$")
# US / other: letters
_US_RE = re.compile(r"^[A-Za-z]{1,5}$")


def _is_ashare(symbol: str) -> bool:
    """Return True if symbol looks like an A-share ticker."""
    return bool(_ASHARE_RE.match(symbol))


def _is_hk(symbol: str) -> bool:
    """Return True if symbol looks like a Hong Kong stock."""
    return bool(_HK_RE.match(symbol)) and not _is_ashare(symbol)


def _is_us_stock(symbol: str) -> bool:
    """Return True if symbol looks like a US stock ticker."""
    return bool(_US_RE.match(symbol))


# ── Lazy imports ─────────────────────────────────────────────────

_ak = None  # cached akshare module


def _get_ak():
    global _ak
    if _ak is None:
        import akshare as ak_mod
        _ak = ak_mod
    return _ak


def _safe_call(func, *args, retries=2, base_delay=1, _use_cache=True, **kwargs):
    """Call an akshare function with cache + throttle + retry.

    1. Cache hit → return immediately (zero HTTP call).
    2. Cache miss → throttle → HTTP request → store in cache.
    3. Network error → exponential backoff retry (2→4→8→16→32s).

    This eliminates duplicate requests within the same analysis run
    (e.g. get_stock_data AND get_indicators both fetching OHLCV
    for the same symbol only hits 东方财富 once).
    """
    global _ak_cache

    # ── Step 1: Check cache first ──────────────────────────────
    if _use_cache:
        cache_key = _make_cache_key(func.__name__, *args, **kwargs)
        with _ak_cache_lock:
            if cache_key in _ak_cache:
                cached_result, cached_ts = _ak_cache[cache_key]
                if time.monotonic() - cached_ts < _AK_CACHE_TTL:
                    logger.debug(f"Cache HIT for {func.__name__} (key={cache_key[:60]}...)")
                    return cached_result
                else:
                    # Expired — remove
                    del _ak_cache[cache_key]

    # ── Step 2: Throttle then call ─────────────────────────────
    _ak_throttle()
    last_err = None
    for i in range(retries + 1):
        try:
            result = func(*args, **kwargs)
            # Store successful result in cache
            if _use_cache:
                with _ak_cache_lock:
                    _ak_cache[cache_key] = (result, time.monotonic())
            return result
        except Exception as e:
            last_err = e
            emsg = str(e).lower()
            is_network = any(
                kw in emsg for kw in [
                    "connection", "timeout", "remote disconnected",
                    "reset", "abort", "too many requests",
                    "new connection error", "read error",
                ]
            )
            if is_network and i < retries:
                delay = base_delay * (2 ** i)
                logger.warning(
                    f"akshare call failed ({type(e).__name__}: {str(e)[:120]}), "
                    f"retrying in {delay:.0f}s ... ({i+1}/{retries})"
                )
                time.sleep(delay)
                _ak_throttle()  # re-throttle before retry
            else:
                raise
    raise last_err  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════
# 1. Core Stock Data (OHLCV)
# ══════════════════════════════════════════════════════════════════

def get_stock_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:

    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    # Convert date formats for akshare: yyyy-mm-dd → YYYYMMDD or YYYY-MM-DD
    ak_start = start_date.replace("-", "")
    ak_end = end_date.replace("-", "")

    if _is_ashare(symbol):
        df = _fetch_ashare_ohlcv(symbol, ak_start, ak_end)
    elif _is_hk(symbol):
        df = _fetch_hk_ohlcv(symbol, ak_start, ak_end)
    else:
        # Fallback to yfinance for US stocks
        return _yfinance_fallback_stock(symbol, start_date, end_date)

    if df.empty:
        return f"No data found for symbol '{symbol}' between {start_date} and {end_date}"

    csv_string = _df_to_csv(df, f"Stock data for {symbol}", start_date, end_date)
    return csv_string


def _fetch_ashare_ohlcv(symbol: str, start_str: str, end_str: str) -> pd.DataFrame:
    """Fetch A-share OHLCV — baostock primary, 东方财富 fallback.

    Strategy: baostock is free, stable, and immune to anti-scraping.
    Falls back to 东方财富 (akshare) only if baostock fails.
    """
    # ── Primary: baostock ────────────────────────────────────────
    df = _fetch_via_baostock(symbol, start_str, end_str)
    if not df.empty:
        return _normalize_ashare_df(df)

    # ── Fallback: 东方财富 akshare ──────────────────────────────
    logger.warning("baostock returned empty for %s, falling back to 东方财富", symbol)
    try:
        ak = _get_ak()
        df = _safe_call(
            ak.stock_zh_a_hist,
            symbol=symbol,
            period="daily",
            start_date=start_str,
            end_date=end_str,
            adjust="qfq",
        )
        if not df.empty:
            return _normalize_ashare_df(df)
    except Exception as e:
        logger.error("东方财富 fallback also failed for %s: %s", symbol, e)

    return pd.DataFrame()


def _fetch_via_baostock(symbol: str, start_str: str, end_str: str) -> pd.DataFrame:
    """Fetch A-share OHLCV data via baostock (stable, no anti-scraping).

    baostock requires YYYY-MM-DD date format (with dashes).
    Returns DataFrame with columns: [date, open, high, low, close, volume]
    or empty DataFrame on failure.

    Thread-safety: holds _bs_lock for the entire login→query→logout cycle
    to prevent concurrent sessions from corrupting each other's TCP stream.
    """
    import baostock as bs

    # Convert date format: 20250601 → 2025-06-01 (baostock requires dashes)
    def fmt(d):
        if len(d) == 8 and d.isdigit():
            return f"{d[:4]}-{d[4:6]}-{d[6:]}"
        return d

    bs_start = fmt(start_str)
    bs_end = fmt(end_str)

    # Determine market prefix: 6xxxx → sh., others → sz.
    if symbol.startswith(("6",)):
        code = f"sh.{symbol}"
    else:
        code = f"sz.{symbol}"

    # ── Global lock: only one baostock session at a time ────────
    # Without this lock, 4 concurrent get_indicators calls corrupt
    # the shared TCP connection, producing UTF-8 decode errors and
    # Bad File Descriptor on logout.
    if not _bs_lock.acquire(timeout=30):
        logger.error("baostock lock timeout for %s", symbol)
        return pd.DataFrame()

    try:
        # ── 持久化连接：login 一次，不每次 logout ──
        lg = bs.login()
        if lg.error_code != '0':
            logger.error("baostock login failed: %s", lg.error_msg)
            return pd.DataFrame()

        rs = bs.query_history_k_data_plus(
            code,
            "date,open,high,low,close,volume,amount",
            start_date=bs_start, end_date=bs_end,
            frequency="d", adjustflag="2",  # 2=前复权
        )
        rows = []
        while rs and rs.error_code == '0' and rs.next():
            try:
                row = rs.get_row_data()
                rows.append(row)
            except (UnicodeDecodeError, UnicodeError):
                # baostock sometimes returns garbled GBK bytes; skip bad row
                logger.debug("Skipping malformed baostock row for %s", symbol)
                continue
            except Exception as e2:
                logger.debug("Unexpected error reading baostock row: %s", e2)
                break

        # ⚠️ 不再 logout — 保持连接存活供后续查询复用
        # 仅在连接断开等异常时清理

        if not rows:
            logger.warning("baostock returned 0 rows for %s (%s ~ %s)", symbol, bs_start, bs_end)
            return pd.DataFrame()

        columns = ["Date", "Open", "High", "Low", "Close", "Volume", "Amount"]
        df = pd.DataFrame(rows, columns=columns)

        # baostock returns strings; parse numerics
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Filter out invalid rows
        df = df.dropna(subset=["Close"])
        df = df[df["Close"] > 0]

        return df

    except Exception as e:
        logger.error("baostock fetch error for %s: %s", symbol, e)
        # 连接异常时登出，下次调用自动重登
        err_msg = str(e)
        if any(p in err_msg for p in ("Bad file descriptor", "Remote end closed",
                                       "Connection aborted", "Connection reset",
                                       "Broken pipe", "recv data error",
                                       "接收数据异常")):
            logger.info("baostock connection broken, will re-login on next call")
            try:
                bs.logout()
            except Exception:
                pass
        return pd.DataFrame()

    finally:
        _bs_lock.release()


def _normalize_ashare_df(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize A-share OHLCV column names and types."""
    if df.empty:
        return df

    # Rename Chinese columns (东方财富) to English
    col_map = {
        "\u65e5\u671f": "Date",   # 日期
        "\u5f00\u76d8": "Open",   # 开盘
        "\u6536\u76d8": "Close",   # 收盘
        "\u6700\u9ad8": "High",   # 最高
        "\u6700\u4f4e": "Low",    # 最低
        "\u6210\u4ea4\u91cf": "Volume",   # 成交量
        "\u6210\u4ea4\u989d": "Amount",   # 成交额
        "\u632f\u5e45": "Amplitude",      # 振幅
        "\u6da8\u8dcc\u5e45": "PctChange", # 涨跌幅
        "\u6da8\u8dcc\u989d": "Change",     # 涨跌额
        "\u6362\u624b\u7387": "Turnover",   # 换手率
    }
    df = df.rename(columns={c: col_map.get(c, c) for c in df.columns})

    # Ensure standard columns exist
    for c in ["Date", "Open", "High", "Low", "Close", "Volume"]:
        if c not in df.columns:
            raise ValueError(f"A-share data missing required column '{c}'")

    numeric_cols = ["Open", "High", "Low", "Close", "Volume"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(2)

    return df


def _fetch_hk_ohlcv(symbol: str, start_str: str, end_str: str) -> pd.DataFrame:
    """Fetch HK stock OHLCV via akshare."""
    ak = _get_ak()
    try:
        df = _safe_call(
            ak.stock_hk_hist,
            symbol=symbol,
            period="daily",
            start_date=start_str,
            end_date=end_str,
            adjust="qfq",
        )
        # Normalize HK column names
        col_map = {
            "date": "Date", "open": "Open", "close": "Close",
            "high": "High", "low": "Low", "volume": "Volume",
        }
        df = df.rename(columns={c: col_map.get(c, c) for c in df.columns})
        return df
    except Exception as e:
        logger.warning(f"HK stock fetch failed for {symbol}: {e}, falling back to yfinance")
        return pd.DataFrame()  # signal to caller to use fallback


def _yfinance_fallback_stock(symbol: str, start_date: str, end_date: str) -> str:
    """Fallback to yfinance for non-A-share symbols."""
    import yfinance as yf
    from tradingagents.dataflows.stockstats_utils import yf_retry

    try:
        ticker = yf.Ticker(symbol.upper())
        data = yf_retry(lambda: ticker.history(start=start_date, end=end_date))
        if data.empty:
            return f"No data found for symbol '{symbol}' between {start_date} and {end_date}"
        if data.index.tz is not None:
            data.index = data.index.tz_localize(None)
        numeric_cols = [c for c in ["Open", "High", "Low", "Close", "Adj Close"] if c in data.columns]
        for c in numeric_cols:
            data[c] = data[c].round(2)
        data = data.reset_index()
        data.rename(columns={"index": "Date"} if "index" in data.columns else {}, inplace=True)
        return _df_to_csv(data, f"Stock data for {symbol}", start_date, end_date)
    except Exception as e:
        return f"Error fetching data for {symbol}: {str(e)}"


# ══════════════════════════════════════════════════════════════════
# 2. Technical Indicators
# ══════════════════════════════════════════════════════════════════
# Reuse the same stockstats-based computation pipeline that yfinance uses,
# but feed it with akshare-fetched OHLCV data instead.

from stockstats import wrap  # noqa: E402
from .stockstats_utils import load_ohlcv as yf_load_ohlcv  # noqa: E402


def _load_ohlcv_akshare(symbol: str, curr_date: str) -> pd.DataFrame:
    """Load OHLCV data using akshare (mirrors load_ohlcv interface)."""
    safe_symbol = symbol.replace("/", "_").replace("\\", "_")[:32]

    config = __import__("tradingagents.dataflows.config", fromlist=["get_config"]).get_config()
    curr_date_dt = pd.to_datetime(curr_date)
    today_date = pd.Timestamp.today()
    start_date = today_date - pd.DateOffset(years=5)
    start_str = start_date.strftime("%Y%m%d")
    end_str = today_date.strftime("%Y%m%d")

    import os
    cache_dir = os.path.join(config["data_cache_dir"], "akshare")
    os.makedirs(cache_dir, exist_ok=True)
    data_file = os.path.join(cache_dir, f"{safe_symbol}-data-{start_str}-{end_str}.csv")

    if os.path.exists(data_file):
        data = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")
    else:
        if _is_ashare(symbol):
            # Use baostock primary, 东方财富 fallback (same as _fetch_ashare_ohlcv)
            data = _fetch_via_baostock(symbol, start_str, end_str)
            if data.empty:
                logger.warning("baostock empty for %s in load_ohlcv, fallback", symbol)
                try:
                    ak = _get_ak()
                    data = _safe_call(
                        ak.stock_zh_a_hist,
                        symbol=symbol,
                        period="daily",
                        start_date=start_str,
                        end_date=end_str,
                        adjust="qfq",
                    )
                except Exception as e:
                    logger.error("东方财富 fallback failed for %s: %s", symbol, e)
                    data = pd.DataFrame()
            if not data.empty:
                data = _normalize_ashare_df(data)
        else:
            # For non-A-shares, fall back to yfinance's loader
            return yf_load_ohlcv(symbol, curr_date)

        if not data.empty and "Date" in data.columns:
            data.to_csv(data_file, index=False, encoding="utf-8")

    if data.empty:
        return data

    # Clean up
    if "Date" in data.columns:
        data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
        data = data.dropna(subset=["Date"])
    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]
    if price_cols:
        data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
        data = data.dropna(subset=["Close"])
        data[price_cols] = data[price_cols].ffill().bfill()

    # Filter to prevent look-ahead bias
    if "Date" in data.columns:
        data = data[data["Date"] <= curr_date_dt]
    return data


def get_indicators(
    symbol: Annotated[str, "ticker symbol"],
    indicator: Annotated[str, "technical indicator name"],
    curr_date: Annotated[str, "current date YYYY-mm-dd"],
    look_back_days: int = 120,
) -> str:

    best_ind_params = {
        "close_50_sma": ("50 SMA: Medium-term trend indicator."),
        "close_200_sma": ("200 SMA: Long-term trend benchmark."),
        "close_10_ema": ("10 EMA: Short-term responsive average."),
        "macd": ("MACD: Momentum via EMA differences."),
        "macds": ("MACD Signal: EMA smoothing of MACD line."),
        "macdh": ("MACD Histogram: Gap between MACD and Signal."),
        "rsi": ("RSI: Momentum / overbought-oversold detector."),
        "boll": ("Bollinger Middle: 20-SMA basis for Bands."),
        "boll_ub": ("Bollinger Upper: +2σ above middle band."),
        "boll_lb": ("Bollinger Lower: -2σ below middle band."),
        "atr": ("ATR: Average True Range volatility measure."),
        "vwma": ("VWMA: Volume-weighted moving average."),
        "mfi": ("MFI: Money Flow Index (volume + price)."),
    }

    if indicator not in best_ind_params:
        raise ValueError(f"Indicator {indicator} not supported. Choose from: {list(best_ind_params.keys())}")

    end_date = curr_date
    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_date_dt - timedelta(days=look_back_days)

    try:
        # Use akshare data loader for A-shares, yfinance for others
        if _is_ashare(symbol):
            data = _load_ohlcv_akshare(symbol, curr_date)
        else:
            data = yf_load_ohlcv(symbol, curr_date)

        if data.empty:
            return f"No data available for {symbol}"

        df = wrap(data)
        if "Date" in df.columns:
            df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

        # Trigger calculation
        _ = df[indicator]

        result_lines = []
        current_dt = curr_date_dt
        while current_dt >= before:
            date_str = current_dt.strftime("%Y-%m-%d")
            if "Date" in df.columns:
                matching = df[df["Date"].str.startswith(date_str)]
                if not matching.empty:
                    val = matching[indicator].values[0]
                    result_lines.append(f"{date_str}: {'N/A' if pd.isna(val) else val}")
                else:
                    result_lines.append(f"{date_str}: N/A: Not a trading day")
            else:
                result_lines.append(f"{date_str}: N/A")
            current_dt -= timedelta(days=1)

        desc = best_ind_params.get(indicator, "No description available.")
        header = f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {end_date}:\n\n"
        return header + "\n".join(result_lines) + "\n\n" + desc + "\n"

    except Exception as e:
        logger.error(f"Error computing indicators for {symbol}: {e}")
        return f"Error calculating indicator {indicator} for {symbol}: {str(e)}"


# ══════════════════════════════════════════════════════════════════
# 3. Fundamentals
# ══════════════════════════════════════════════════════════════════

# Module-level cache for structured fundamentals data (consumed by numeric_guard)
# ── 并发安全：按 ticker 键控存储，避免多会话竞态拿错标的数据 ──
_last_fundamentals_structured: dict = {}                  # legacy: 最近一次（单会话兼容）
_fundamentals_structured_by_ticker: dict = {}             # {ticker: structured_dict}
_fundamentals_structured_lock = threading.Lock()

# D2 v2: 报告真实发布日缓存 {symbol: {statDate(str): pubDate(str)}}
# 由回测引擎启动时预加载，数据层过滤时优先用真实发布日，回退到保守估计
_report_pub_dates_cache: Dict[str, Dict[str, str]] = {}
_report_pub_dates_lock = threading.Lock()


def set_report_pub_dates(symbol: str, pub_to_stat: Dict[str, str]) -> None:
    """D2 v2: 注册某只股票的真实报告发布日期映射。
    
    Args:
        symbol: 股票代码
        pub_to_stat: {pubDate(YYYY-MM-DD): statDate(YYYY-MM-DD)} 映射
    """
    with _report_pub_dates_lock:
        # 构建反向索引：statDate → pubDate
        rev: Dict[str, str] = {}
        for pub, stat in pub_to_stat.items():
            if stat and pub:
                rev[stat] = pub
        _report_pub_dates_cache[symbol.upper()] = rev


def _lookup_pub_date(stat_date: "pd.Timestamp", symbol: str = "") -> "pd.Timestamp | None":
    """D2 v2: 查找报告的真实发布日。
    
    优先查询预加载的 baostock pubDate 缓存；未命中返回 None。
    调用方应在 None 时回退到 _estimate_publish_date。
    """
    if not symbol:
        return None
    import pandas as pd
    with _report_pub_dates_lock:
        rev = _report_pub_dates_cache.get(symbol.upper(), {})
    for fmt in (stat_date.strftime("%Y-%m-%d"), stat_date.strftime("%Y%m%d"),
                stat_date.strftime("%Y-%m-%d 00:00:00")):
        if fmt in rev:
            try:
                return pd.Timestamp(rev[fmt])
            except Exception:
                pass
    return None


def _estimate_publish_date(period_date: "pd.Timestamp") -> "pd.Timestamp":
    """估算财报实际发布日期（保守估计，基于监管截止日）。
    
    仅在没有 baostock 真实 pubDate 时作为回退方案使用。
    真实发布日通常早于这些截止日（如年报很多在 3 月就发布了）。
    
    - 年报 (12-31): 次年 4 月 30 日
    - Q1 (03-31):    当年 4 月 30 日
    - 中报 (06-30):  当年 8 月 31 日
    - Q3 (09-30):    当年 10 月 31 日
    """
    m = period_date.month
    y = period_date.year
    if m == 12:
        return pd.Timestamp(year=y + 1, month=4, day=30)
    elif m == 3:
        return pd.Timestamp(year=y, month=4, day=30)
    elif m == 6:
        return pd.Timestamp(year=y, month=8, day=31)
    elif m == 9:
        return pd.Timestamp(year=y, month=10, day=31)
    else:
        return period_date + pd.DateOffset(months=2)


def _filter_by_report_date(df: "pd.DataFrame", curr_date: str,
                           symbol: str = "") -> "pd.DataFrame":
    """Filter DataFrame rows whose publish date is <= curr_date.
    
    D2 v2 修复：优先用 baostock 真实 pubDate 过滤（由 set_report_pub_dates 预加载），
    未命中时回退到 _estimate_publish_date 保守估计。
    消除"年报 1 月即可用"的前视偏差。
    """
    if not curr_date:
        return df.copy()

    date_col_candidates = (
        [df.columns[0]]
        + [c for c in df.columns[1:] if "期" in c or "date" in c.lower()]
    )
    cutoff = pd.Timestamp(curr_date)
    for col in date_col_candidates:
        try:
            dates = pd.to_datetime(df[col], errors="coerce")
            if dates.notna().sum() < 3:
                continue
            # D2 v2: 真实 pubDate 优先 → 保守估计回退
            if symbol:
                pub_dates = dates.apply(
                    lambda d: _lookup_pub_date(d, symbol) or _estimate_publish_date(d)
                )
            else:
                pub_dates = dates.apply(_estimate_publish_date)
            return df[pub_dates <= cutoff].copy()
        except Exception:
            continue
    return df.copy()


def _safe_float(val):
    """Convert val to float, returning None on failure."""
    if val is None or str(val) in ("", "nan", "None"):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _append_valuation_metrics(
    ticker: str, curr_date: str | None, structured: dict, lines: list
) -> None:
    """Append PE / PB / PEG / MarketCap / PS / Dividend / Beta to fundamentals output."""
    try:
        from datetime import date as _date_obj
        cdate = curr_date or _date_obj.today().strftime("%Y-%m-%d")
        raw_df = _load_ohlcv_akshare(ticker, cdate)
        if raw_df is None or raw_df.empty:
            return
        price = float(raw_df["Close"].iloc[-1])
        if price <= 0:
            return

        # ── 财务分析指标（新浪，最慢接口）：只调一次，走 _safe_call
        #    （缓存 + 节流 + 重试），PE 和 总资产 复用同一份结果 ──
        fa_df = None
        try:
            _ak_mod = _get_ak()
            fa_df = _safe_call(
                _ak_mod.stock_financial_analysis_indicator,
                symbol=ticker,
                start_year=str(_date_obj.today().year - 2),
            )
        except Exception:
            fa_df = None

        # ── PE (静态) ──
        # D2 v2 修复: 优先用真实 pubDate，回退到保守估计
        try:
            if fa_df is not None and not fa_df.empty and "摊薄每股收益(元)" in fa_df.columns \
               and "日期" in fa_df.columns:
                eps_annual = None
                cutoff = pd.Timestamp(curr_date) if curr_date else pd.Timestamp.now()
                for _, row in fa_df[::-1].iterrows():
                    row_date = str(row["日期"])
                    if row_date.endswith("12-31"):
                        period_date = pd.Timestamp(row_date)
                        # D2 v2: 真实 pubDate 优先 → 保守估计回退
                        pub_date = (_lookup_pub_date(period_date, ticker)
                                    or _estimate_publish_date(period_date))
                        if pub_date > cutoff:
                            continue
                        eps_annual = float(row["摊薄每股收益(元)"])
                        break
                if eps_annual and eps_annual > 0:
                    pe = round(price / eps_annual, 2)
                    lines.append(f"PE (Static): {pe}")
                    structured["pe_static"] = pe
        except Exception:
            pass
        
        # ── PB ──
        bvps = structured.get("book_value_per_share")
        if bvps and bvps > 0:
            pb = round(price / bvps, 2)
            lines.append(f"PB: {pb}")
            structured["pb"] = pb
        
        # ── PEG ──
        pe_val = structured.get("pe_static")
        growth = structured.get("net_income_growth_yoy")
        if pe_val and growth and growth != 0:
            peg = round(pe_val / abs(growth), 2)
            lines.append(f"PEG: {peg}")
            structured["peg"] = peg
        
        # ── 总市值 / PS / 股息率 ──
        try:
            # 总市值: price × total_shares / 1e8（复用上面同一份 fa_df，不再重复请求）
            total_assets = None
            debt_ratio = None
            try:
                if fa_df is not None and not fa_df.empty:
                    latest = fa_df.iloc[-1]
                    total_assets = _safe_float(latest.get("总资产(元)"))
                    debt_ratio = _safe_float(latest.get("资产负债率(%)"))
            except Exception:
                pass
            if total_assets and debt_ratio is not None and bvps and bvps > 0:
                net_assets = total_assets * (1 - debt_ratio / 100)
                total_shares = net_assets / bvps
                market_cap = round(price * total_shares / 1e8, 2)
                lines.append(f"Market Cap (亿): {market_cap}")
                structured["market_cap_亿"] = market_cap
                # PS = 总市值 / 营收
                revenue = structured.get("total_revenue")
                if revenue and revenue > 0:
                    ps = round(market_cap / revenue, 2)
                    lines.append(f"PS: {ps}")
                    structured["ps"] = ps
            # 股息率（走 _safe_call：缓存 + 节流 + 重试）
            try:
                fhps_df = _safe_call(_get_ak().stock_fhps_detail_ths, symbol=ticker)
                if fhps_df is not None and not fhps_df.empty and "税前分红率" in fhps_df.columns:
                    for _, row in fhps_df[::-1].iterrows():
                        div_rate = str(row.get("税前分红率", "")).strip()
                        if div_rate and div_rate not in ("--", "nan", "None", ""):
                            div_val = float(div_rate.replace("%", ""))
                            lines.append(f"- 股息率: {div_val}%")
                            structured["dividend_yield"] = div_val
                            break
            except Exception:
                pass
        except Exception:
            pass
            
    except Exception:
        pass  # 估值指标失败不影响主流程


def get_fundamentals_structured(ticker: str = None) -> dict:
    """Return structured fundamentals data populated by get_fundamentals().

    Args:
        ticker: 指定标的代码时，返回该标的的结构化数据（并发安全，推荐）。
            为 None 时返回"最近一次"调用的数据（legacy 行为，仅单会话安全）。

    Returns empty dict if get_fundamentals() has not been called yet
    (or has not been called for the given ticker).
    """
    with _fundamentals_structured_lock:
        if ticker:
            return dict(_fundamentals_structured_by_ticker.get(ticker.upper(), {}))
        return dict(_last_fundamentals_structured)


def _store_fundamentals_structured(ticker: str, structured: dict) -> None:
    """线程安全地写入 keyed + legacy 两份结构化数据。"""
    global _last_fundamentals_structured
    with _fundamentals_structured_lock:
        _fundamentals_structured_by_ticker[ticker.upper()] = dict(structured)
        _last_fundamentals_structured = dict(structured)


def get_fundamentals(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date - rows after this date are excluded"] = None,
) -> str:
    """Get company fundamentals via akshare (同花顺财务摘要 for A-shares).

    When ``curr_date`` is provided, report periods after that date are
    filtered out to prevent look-ahead bias.

    Side effect: populates ``_last_fundamentals_structured`` with a
    parsed dict that agents can use for post-hoc numeric validation.
    """
    if not _is_ashare(ticker):
        return _yfinance_fallback("fundamentals", ticker)

    ak = _get_ak()
    try:
        df = _safe_call(
            ak.stock_financial_abstract_ths,
            symbol=ticker,
            indicator="按报告期",
        )
        if df.empty:
            _store_fundamentals_structured(ticker, {})
            return f"No fundamentals data found for {ticker}"

        # ---- date filtering (prevent look-ahead bias) ----
        if curr_date:
            date_col = df.columns[0]  # 第一列是"报告期"
            try:
                dates = pd.to_datetime(df[date_col], errors="coerce")
                cutoff = pd.Timestamp(curr_date)
                df = df[dates <= cutoff].copy()
                if df.empty:
                    _store_fundamentals_structured(ticker, {})
                    return f"No fundamentals data on or before {curr_date} for {ticker}"
            except Exception:
                pass  # fall through to unfiltered if date parsing fails

        lines = []
        lines.append(f"# Company Fundamentals for {ticker.upper()} (via 同花顺)")
        lines.append(f"# Data retrieved: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        if curr_date:
            lines.append(f"# Filtered to report periods <= {curr_date}\n")
        else:
            lines.append("")
        # Get latest row (after filtering)
        latest = df.iloc[-1]
        # Map known Chinese columns — ALL 24 metrics from 同花顺 financial abstract
        cn_map = {
            "报告期": "Report Date",
            "营业总收入": "Total Revenue",
            "营业总收入同比增长": "Revenue Growth YoY",
            "净利润": "Net Income",
            "净利润同比增长": "Net Income Growth YoY",
            "扣非净利润": "Net Income (Recurring)",
            "扣非净利润同比增长": "Net Income Growth YoY (Recurring)",
            "每股收益": "EPS",
            "每股净资产": "Book Value Per Share",
            "每股资本公积金": "Capital Reserve Per Share",
            "每股未分配利润": "Undivided Profit Per Share",
            "每股经营现金流": "Operating CF Per Share",
            "销售毛利润": "Gross Margin",   # 部分历史版本用 "销售毛利润" 非 "销售毛利率"
            "销售毛利率": "Gross Margin",
            "销售净利率": "Net Margin",
            "净资产收益率": "ROE",
            "净资产收益率-摊薄": "ROE (Diluted)",
            "营业周期": "Operating Cycle",
            "存货周转率": "Inventory Turnover",
            "存货周转天数": "Inventory Turnover Days",
            "应收账款周转天数": "Receivables Turnover Days",
            "流动比率": "Current Ratio",
            "速动比率": "Quick Ratio",
            "保守速动比率": "Conservative Quick Ratio",
            "产权比率": "Equity Ratio",
            "资产负债率": "Debt-to-Asset Ratio",
        }
        # Also populate structured dict for numeric_guard
        structured = {
            'ticker': ticker.upper(),
            'source': '同花顺',
        }
        for cn_col, en_name in cn_map.items():
            matches = [c for c in df.columns if cn_col in c]
            if matches:
                col = matches[0]
                val = latest.get(col)
                if val is not None and str(val) != "" and str(val) != "nan":
                    lines.append(f"{en_name}: {val}")
                    # Store in structured dict with normalized key
                    s_key = en_name.lower().replace(' ', '_').replace('-', '_')
                    # Clean numeric value
                    clean = str(val).replace('亿', '').replace('%', '').replace(',', '').strip()
                    try:
                        structured[s_key] = float(clean)
                    except ValueError:
                        structured[s_key] = val

        _store_fundamentals_structured(ticker, structured)
        
        # ── 追加估值指标：PE/PB/PEG ──
        _append_valuation_metrics(ticker, curr_date, structured, lines)
        # 估值字段写入 structured 后需再存一次（含 pe/pb/peg 等）
        _store_fundamentals_structured(ticker, structured)
        
        return "\n".join(lines) + "\n"
    except Exception as e:
        logger.error(f"Fundamentals fetch failed for {ticker}: {e}")
        _store_fundamentals_structured(ticker, {})
        return f"Error retrieving fundamentals for {ticker}: {str(e)}"


# ══════════════════════════════════════════════════════════════════
# 估值快照 — 单一权威源（Web 面板与所有 Agent prompt 共用）
# ══════════════════════════════════════════════════════════════════

_valuation_cache: dict = {}

# (中文名, structured键, 单位) — 面板与 prompt 注入共用同一映射
_VALUATION_FIELDS = [
    ("市盈率(静态)", "pe_static", "倍"),
    ("市净率(PB)",   "pb",        "倍"),
    ("PEG",          "peg",       ""),
    ("总市值",       "market_cap_亿", "亿"),
    ("市销率(PS)",   "ps",        "倍"),
    ("股息率",       "dividend_yield", "%"),
]


def get_valuation_metrics(ticker: str, curr_date: str = None) -> dict:
    """Canonical price-based valuation metrics — the single source of truth.

    Returns {"市盈率(静态)": (18.36, "倍"), ...} computed by the SAME
    ``_append_valuation_metrics`` pipeline that ``get_fundamentals`` uses,
    so the web panel, the fundamentals tool output, and the prompt
    injection block are guaranteed numerically identical.

    Cached per (ticker, date). Returns {} for non-A-share or on failure.
    """
    from datetime import date as _date_obj
    key = (ticker, curr_date or _date_obj.today().strftime("%Y-%m-%d"))
    if key in _valuation_cache:
        return dict(_valuation_cache[key])

    result: dict = {}
    try:
        if _is_ashare(ticker):
            # 优先复用已有的结构化数据（含估值字段），避免重跑整个
            # get_fundamentals → _append_valuation_metrics 管线
            structured = get_fundamentals_structured(ticker)
            has_valuation = any(
                structured.get(s_key) is not None for _, s_key, _ in _VALUATION_FIELDS
            )
            if not has_valuation:
                get_fundamentals(ticker, curr_date)  # populates structured incl. valuation
                structured = get_fundamentals_structured(ticker)
            for cn_name, s_key, unit in _VALUATION_FIELDS:
                val = structured.get(s_key)
                if val is not None:
                    result[cn_name] = (val, unit)
    except Exception as e:
        logger.warning(f"Valuation metrics failed for {ticker}: {e}")

    _valuation_cache[key] = result
    return dict(result)


def get_valuation_snapshot(ticker: str, curr_date: str = None) -> str:
    """Formatted valuation block for injection into EVERY agent's prompt.

    Same numbers as the web panel (both read get_valuation_metrics).
    Returns "" when no data is available (non-A-share / fetch failure),
    so callers can concatenate unconditionally.
    """
    metrics = get_valuation_metrics(ticker, curr_date)
    if not metrics:
        return ""
    from datetime import date as _date_obj
    as_of = curr_date or _date_obj.today().strftime("%Y-%m-%d")
    lines = [f"\n【当前估值快照（截至 {as_of}，基于最新收盘价实时计算，非财报申报值）】"]
    for cn_name, s_key, unit in _VALUATION_FIELDS:
        if cn_name in metrics:
            val, u = metrics[cn_name]
            lines.append(f"- {cn_name}: {val}{u}")
    lines.append("（以上估值指标全体分析角色可见，与前端数据面板同源同值）\n")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# 4. Financial Statements
# ══════════════════════════════════════════════════════════════════

def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "frequency: annual or quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date - rows after this date are excluded"] = None,
) -> str:
    if not _is_ashare(ticker):
        return _yfinance_fallback("balance_sheet", ticker)
    ak = _get_ak()
    try:
        func = ak.stock_balance_sheet_by_report_em if freq == "quarterly" else ak.stock_balance_sheet_by_yearly_em
        df = _safe_call(func, symbol=ticker)
        if df.empty:
            return f"No balance sheet data for {ticker}"

        # ---- date filtering (prevent look-ahead bias) ----
        if curr_date:
            df = _filter_by_report_date(df, curr_date, symbol=ticker)
            if df.empty:
                return f"No balance sheet data on or before {curr_date} for {ticker}"

        return _df_to_csv(df, f"Balance Sheet for {ticker} ({freq})", extra=f"Freq: {freq}")
    except Exception as e:
        return f"Error retrieving balance sheet for {ticker}: {str(e)}"


def get_cashflow(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "frequency: annual or quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date - rows after this date are excluded"] = None,
) -> str:
    if not _is_ashare(ticker):
        return _yfinance_fallback("cashflow", ticker)
    ak = _get_ak()
    try:
        func = ak.stock_cash_flow_sheet_by_report_em if freq == "quarterly" else ak.stock_cash_flow_sheet_by_yearly_em
        df = _safe_call(func, symbol=ticker)
        if df.empty:
            return f"No cash flow data for {ticker}"

        # ---- date filtering (prevent look-ahead bias) ----
        if curr_date:
            df = _filter_by_report_date(df, curr_date)
            if df.empty:
                return f"No cash flow data on or before {curr_date} for {ticker}"

        return _df_to_csv(df, f"Cash Flow for {ticker} ({freq})", extra=f"Freq: {freq}")
    except Exception as e:
        return f"Error retrieving cash flow for {ticker}: {str(e)}"


def get_income_statement(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "frequency: annual or quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date - rows after this date are excluded"] = None,
) -> str:
    if not _is_ashare(ticker):
        return _yfinance_fallback("income_statement", ticker)
    # akshare doesn't have a direct income statement API, use financial abstract
    return get_fundamentals(ticker, curr_date)


# ══════════════════════════════════════════════════════════════════
# 5. News & Insider Data
# ══════════════════════════════════════════════════════════════════

def get_news(
    symbol: Annotated[str, "ticker symbol"],
    start_date: Annotated[str, "start date yyyy-mm-dd"] = "",
    end_date: Annotated[str, "end date yyyy-mm-dd"] = "",
) -> str:
    if not _is_ashare(symbol):
        return _yfinance_fallback("news", symbol)
    ak = _get_ak()
    try:
        df = _safe_call(ak.stock_news_em, symbol=symbol)
        if df.empty:
            return f"No news found for {symbol}"

        # ---- date filtering (prevent look-ahead bias) ----
        if end_date:
            # 新闻 DataFrame 通常第 5 列是发布日期
            date_col_candidates = [c for c in df.columns if "日" in c or "date" in c.lower() or "time" in c.lower()]
            date_col = date_col_candidates[0] if date_col_candidates else df.columns[4]
            try:
                dates = pd.to_datetime(df[date_col], errors="coerce")
                cutoff = pd.Timestamp(end_date)
                df = df[dates <= cutoff].copy()
            except Exception:
                pass  # fall through to unfiltered if date parsing fails

        lines = [f"# News for {symbol.upper()} (via 东方财富)\n"]
        for _, row in df.head(20).iterrows():
            title = str(row.iloc[2]) if len(row) > 2 else str(row.iloc[1])
            pub = str(row.iloc[4]) if len(row) > 4 else ""
            source = str(row.iloc[3]) if len(row) > 3 else ""
            content = str(row.iloc[1]) if len(row) > 1 else ""
            content_preview = content[:300] if len(content) > 300 else content
            lines.append(f"### {title}")
            lines.append(f"- Source: {source} | Date: {pub}")
            lines.append(f"- {content_preview}")
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"News fetch failed for {symbol}: {e}")
        return f"Error fetching news for {symbol}: {str(e)}"


def get_global_news(
    start_date: Annotated[str, "start date"] = "",
    end_date: Annotated[str, "end date"] = "",
) -> str:
    """Global macro news — akshare has limited support, provide stub."""
    try:
        ak = _get_ak()
        df = _safe_call(ak.stock_news_main_cx)
        if not df.empty:
            lines = ["# Global/Macro Headlines (财联社电报)\n"]
            for _, row in df.head(15).iterrows():
                title = str(row.iloc[1]) if len(row) > 1 else str(row.iloc[0])
                dt = str(row.iloc[2]) if len(row) > 2 else ""
                lines.append(f"- [{dt}] {title}")
            return "\n".join(lines) + "\n"
    except Exception:
        pass
    return "# Global/Macro News\n\n(Not available from akshare; consider enabling yfinance as news fallback)"


def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
) -> str:
    """Insider transactions — limited in akshare for A-shares."""
    return (
        f"# Insider Transactions for {ticker.upper()}\n\n"
        "(Insider transaction data is not readily available for Chinese A-shares. "
        "This feature is mainly applicable to US markets where SEC filings are public.)"
    )


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

def _df_to_csv(
    df: pd.DataFrame,
    title: str,
    start_date: str = "",
    end_date: str = "",
    extra: str = "",
) -> str:
    """Format DataFrame as CSV string with header."""
    header = f"# {title}\n"
    header += f"# Total records: {len(df)}\n"
    if start_date:
        header += f"# Period: {start_date} ~ {end_date}\n"
    header += f"# Retrieved: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    if extra:
        header += f"# {extra}\n"
    return header + "\n" + df.to_csv() + "\n"


def _yfinance_fallback(data_type: str, symbol: str, *args, **kwargs) -> str:
    """Generic fallback to yfinance for unsupported data types/symbols."""
    import yfinance as yf
    from tradingagents.dataflows.stockstats_utils import yf_retry

    fallback_map = {
        "fundamentals": lambda s: yf_retry(lambda: yf.Ticker(s).info),
        "balance_sheet": lambda s: yf_retry(lambda: yf.Ticker(s).quarterly_balance_sheet),
        "cashflow": lambda s: yf_retry(lambda: yf.Ticker(s).quarterly_cashflow),
        "income_statement": lambda s: yf_retry(lambda: yf.Ticker(s).quarterly_income_stmt),
        "news": lambda s: yf_retry(lambda: yf.Ticker(s).get_news(count=10)),
    }
    try:
        fetcher = fallback_map.get(data_type)
        if fetcher:
            result = fetcher(symbol)
            if isinstance(result, dict):
                lines = [f"# {data_type.title()} for {symbol} (yfinance fallback)\n"]
                for k, v in result.items():
                    if v is not None:
                        lines.append(f"{k}: {v}")
                return "\n".join(lines) + "\n"
            elif hasattr(result, "to_csv"):
                return _df_to_csv(result, f"{data_type.title()} for {symbol}")
            elif isinstance(result, list):
                return f"# {data_type} for {symbol}: {len(result)} items returned"
        return f"(Fallback not implemented for {data_type})"
    except Exception as e:
        return f"Error fetching {data_type} for {symbol} (yfinance fallback): {str(e)}"


# ── Stock name lookup (A-share code → 简称) ───────────────────
# Resolves ticker codes to real company short names so that LLM
# reports use the correct company name instead of hallucinating one.

_name_cache: dict = {}           # {code: name}
_name_cache_loaded: bool = False


def _load_name_table() -> None:
    """Load the full A-share code→name mapping table (once, lazy)."""
    global _name_cache, _name_cache_loaded
    if _name_cache_loaded:
        return
    try:
        ak = _get_ak()
        df = ak.stock_info_a_code_name()
        _name_cache = dict(zip(df["code"], df["name"]))
        _name_cache_loaded = True
        logger.info(f"Loaded A-share name table: {len(_name_cache)} entries")
    except Exception as e:
        logger.warning(f"Failed to load A-share name table: {e}")
        _name_cache = {}
        _name_cache_loaded = True  # don't retry every call


def get_stock_name(ticker: str) -> str:
    """Return the real A-share stock short name for a ticker code.

    For non-A-share tickers (US/HK), returns the original ticker string.

    Args:
        ticker: Stock code like "000960", "600519"

    Returns:
        Chinese short name like "锡业股份", "贵州茅台".
        Falls back to the original ticker if lookup fails.
    """
    if not _is_ashare(ticker):
        return ticker

    _load_name_table()

    # Strip any exchange suffix that might have been attached
    code = ticker.strip().upper()
    name = _name_cache.get(code)
    if name:
        return name

    logger.warning(f"No name found for A-share code: {ticker}")
    return ticker
