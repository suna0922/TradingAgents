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
from typing import Annotated, Optional

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
_AK_CACHE_TTL: float = 120.0  # seconds — longer than any single analysis run


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

        try:
            bs.logout()
        except Exception:
            pass  # connection may already be torn down after encoding errors

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
_last_fundamentals_structured: dict = {}


def _filter_by_report_date(df: "pd.DataFrame", curr_date: str) -> "pd.DataFrame":
    """Filter DataFrame rows whose report-period (first column) is <= curr_date.

    Tries the first column as the date column; falls back to any column whose
    name contains "期" or "date" (case-insensitive).  Returns a **copy** so
    callers can safely mutate the result.

    When ``curr_date`` is None/empty, returns ``df.copy()`` unchanged.
    """
    if not curr_date:
        return df.copy()

    # 尝试常见的报告期列名
    date_col_candidates = (
        [df.columns[0]]
        + [c for c in df.columns[1:] if "期" in c or "date" in c.lower()]
    )
    cutoff = pd.Timestamp(curr_date)
    for col in date_col_candidates:
        try:
            dates = pd.to_datetime(df[col], errors="coerce")
            if dates.notna().sum() < 3:  # 太少，跳过
                continue
            return df[dates <= cutoff].copy()
        except Exception:
            continue
    # 如果所有尝试都失败，返回原 df（不过滤）
    return df.copy()


def get_fundamentals_structured() -> dict:
    """Return the most recent fundamentals data as a structured dict.

    This is populated as a side effect of get_fundamentals() and
    consumed by downstream agents for programmatic numeric cross-checks.
    Returns empty dict if get_fundamentals() has not been called yet.
    """
    return dict(_last_fundamentals_structured)


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
    global _last_fundamentals_structured

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
            _last_fundamentals_structured = {}
            return f"No fundamentals data found for {ticker}"

        # ---- date filtering (prevent look-ahead bias) ----
        if curr_date:
            date_col = df.columns[0]  # 第一列是"报告期"
            try:
                dates = pd.to_datetime(df[date_col], errors="coerce")
                cutoff = pd.Timestamp(curr_date)
                df = df[dates <= cutoff].copy()
                if df.empty:
                    _last_fundamentals_structured = {}
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
        # Map known Chinese columns
        cn_map = {
            "报告期": "Report Date",
            "营业总收入": "Total Revenue",
            "营业总收入同比增长": "Revenue Growth YoY",
            "净利润": "Net Income",
            "净利润同比增长": "Net Income Growth YoY",
            "每股收益": "EPS",
            "净资产收益率": "ROE",
            "毛利率": "Gross Margin",
            "净利率": "Net Margin",
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

        _last_fundamentals_structured = structured
        return "\n".join(lines) + "\n"
    except Exception as e:
        logger.error(f"Fundamentals fetch failed for {ticker}: {e}")
        _last_fundamentals_structured = {}
        return f"Error retrieving fundamentals for {ticker}: {str(e)}"


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
            df = _filter_by_report_date(df, curr_date)
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
