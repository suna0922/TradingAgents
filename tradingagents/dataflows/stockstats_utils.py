import time
import logging
import re
import threading

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError
from stockstats import wrap
from typing import Annotated
import os
from .config import get_config
from .utils import safe_ticker_component

logger = logging.getLogger(__name__)

# A-share detection: 6-digit numeric codes are Chinese A-shares
_ASHARE_RE = re.compile(r"^\d{6}$")

# ── Global request throttle for yfinance ──────────────────────────
# Prevents rapid sequential calls from triggering Yahoo's rate limit.
# All yf_retry() calls wait at least this long after the previous one.
_yf_last_call_time: float = 0.0
_yf_lock = threading.Lock()
_YF_MIN_INTERVAL: float = 1.5  # seconds between requests


def _yf_throttle() -> None:
    """Sleep if needed to enforce minimum interval between yfinance calls."""
    global _yf_last_call_time
    with _yf_lock:
        now = time.monotonic()
        elapsed = now - _yf_last_call_time
        if elapsed < _YF_MIN_INTERVAL:
            wait = _YF_MIN_INTERVAL - elapsed
            logger.debug(f"yfinance throttle: waiting {wait:.1f}s")
            time.sleep(wait)
        _yf_last_call_time = time.monotonic()


def yf_retry(func, max_retries=5, base_delay=5.0):
    """Execute a yfinance call with exponential backoff on rate limits.

    yfinance raises YFRateLimitError on HTTP 429 responses but does not
    retry them internally. This wrapper adds retry logic specifically
    for rate limits. Other exceptions propagate immediately.

    Tuned for the multi-agent tools_market node which fires many
    sequential data-fetching calls (price, indicators, fundamentals,
    financials, insider trades).
    """
    _yf_throttle()
    for attempt in range(max_retries + 1):
        try:
            return func()
        except YFRateLimitError:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"Yahoo Finance rate limited, retrying in {delay:.0f}s "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
                # Re-acquire throttle after sleeping so we don't
                # fire immediately into another rate limit.
                _yf_throttle()
            else:
                logger.error(
                    f"Yahoo Finance rate limit exceeded after {max_retries} retries. "
                    f"Total wait: {base_delay * (2 ** max_retries - 1):.0f}s"
                )
                raise


def _clean_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize a stock DataFrame for stockstats: parse dates, drop invalid rows, fill price gaps."""
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data.dropna(subset=["Date"])

    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
    data = data.dropna(subset=["Close"])
    data[price_cols] = data[price_cols].ffill().bfill()

    return data


def load_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    """Fetch OHLCV data with caching, filtered to prevent look-ahead bias.

    For Chinese A-shares (6-digit codes), uses akshare via 东方财富.
    For other tickers, falls back to yfinance.
    Downloads ~5 years of data up to today and caches per symbol.
    Rows after curr_date are filtered out so backtests never see future prices.
    """
    # Reject ticker values that would escape the cache directory when
    # interpolated into the cache filename (e.g. ``../../tmp/x``).
    safe_symbol = safe_ticker_component(symbol)

    config = get_config()
    curr_date_dt = pd.to_datetime(curr_date)

    # Cache uses a fixed window (~5y to today) so one file per symbol
    today_date = pd.Timestamp.today()
    start_date = today_date - pd.DateOffset(years=5)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = today_date.strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    vendor_label = "akshare" if _ASHARE_RE.match(symbol) else "yfinance"
    data_file = os.path.join(
        config["data_cache_dir"],
        f"{safe_symbol}-{vendor_label}-data-{start_str}-{end_str}.csv",
    )

    if os.path.exists(data_file):
        data = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")
    else:
        # ── A-share path: baostock primary, 东方财富 fallback ───
        if _ASHARE_RE.match(symbol):
            # Use akshare_data's _fetch_ashare_ohlcv which uses baostock as primary
            from . import akshare_data
            ak_start = start_str.replace("-", "")
            ak_end = end_str.replace("-", "")
            df = akshare_data._fetch_ashare_ohlcv(symbol, ak_start, ak_end)

            if not df.empty:
                # _fetch_ashare_ohlcv already returns normalized columns
                data = df.reset_index(drop=True)
            else:
                data = pd.DataFrame()
        # ── Fallback: yfinance for US/international stocks ───────
        else:
            data = yf_retry(lambda: yf.download(
                symbol,
                start=start_str,
                end=end_str,
                multi_level_index=False,
                progress=False,
                auto_adjust=True,
            ))
            data = data.reset_index()

        if not data.empty:
            data.to_csv(data_file, index=False, encoding="utf-8")

    if data.empty:
        # Return empty DataFrame with expected columns so downstream
        # code (stockstats, indicators) degrades gracefully.
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    data = _clean_dataframe(data)

    # Filter to curr_date to prevent look-ahead bias in backtesting
    data = data[data["Date"] <= curr_date_dt]

    return data


def filter_financials_by_date(data: pd.DataFrame, curr_date: str) -> pd.DataFrame:
    """Drop financial statement columns (fiscal period timestamps) after curr_date.

    yfinance financial statements use fiscal period end dates as columns.
    Columns after curr_date represent future data and are removed to
    prevent look-ahead bias.
    """
    if not curr_date or data.empty:
        return data
    cutoff = pd.Timestamp(curr_date)
    mask = pd.to_datetime(data.columns, errors="coerce") <= cutoff
    return data.loc[:, mask]


class StockstatsUtils:
    @staticmethod
    def get_stock_stats(
        symbol: Annotated[str, "ticker symbol for the company"],
        indicator: Annotated[
            str, "quantitative indicators based off of the stock data for the company"
        ],
        curr_date: Annotated[
            str, "curr date for retrieving stock price data, YYYY-mm-dd"
        ],
    ):
        data = load_ohlcv(symbol, curr_date)
        df = wrap(data)
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
        curr_date_str = pd.to_datetime(curr_date).strftime("%Y-%m-%d")

        df[indicator]  # trigger stockstats to calculate the indicator
        matching_rows = df[df["Date"].str.startswith(curr_date_str)]

        if not matching_rows.empty:
            indicator_value = matching_rows[indicator].values[0]
            return indicator_value
        else:
            return "N/A: Not a trading day (weekend or holiday)"
