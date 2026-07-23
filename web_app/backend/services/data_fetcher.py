#!/usr/bin/env python3
"""独立子进程数据取数脚本 — 隔离 baostock C 扩展崩溃，保护 uvicorn 主进程。

用法:
    .venv/bin/python web_app/backend/services/data_fetcher.py 600519 2026-07-17
    # → 输出 JSON: {"ohlcv_rows": 1210, "fundamentals": {...}, "error": null}
"""

from __future__ import annotations
import json
import sys
import traceback
from datetime import date as _date_obj


def _describe_ohlcv(df):
    """OHLCV 摘要（不传全量 DataFrame，JSON 太大）。"""
    if df is None or df.empty:
        return {"rows": 0, "latest_price": 0, "change_pct": 0, "sma_5": 0, "sma_10": 0,
                "sma_20": 0, "sma_60": 0, "rsi_14": 0, "atr_14": 0,
                "high_60": 0, "low_60": 0, "vol_ratio": 0}
    close = df["Close"]
    p = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) > 1 else p
    chg = round((p - prev) / prev * 100, 2) if prev else 0

    def sma(w):
        return round(float(close.tail(w).mean()), 2) if len(close) >= w else 0

    def rsi(w=14):
        try:
            d = close.diff()
            g = d.where(d > 0, 0.0)
            l = -d.where(d < 0, 0.0)
            ag = g.ewm(span=w, adjust=False).mean().iloc[-1]
            al = l.ewm(span=w, adjust=False).mean().iloc[-1]
            if al == 0: return 100.0
            return round(float(100 - 100 / (1 + ag / al)), 2)
        except: return 0

    def atr(w=14):
        try:
            import pandas as pd
            h, lo, c = df["High"], df["Low"], df["Close"]
            tr = pd.concat([h - lo, abs(h - c.shift()), abs(lo - c.shift())], axis=1).max(axis=1)
            return round(float(tr.tail(w).mean()), 2)
        except: return 0

    return {
        "rows": len(df),
        "latest_price": p, "change_pct": chg,
        "sma_5": sma(5), "sma_10": sma(10), "sma_20": sma(20), "sma_60": sma(60),
        "rsi_14": rsi(14), "atr_14": atr(14),
        "high_60": round(float(close.tail(60).max()), 2),
        "low_60": round(float(close.tail(60).min()), 2),
        "vol_ratio": round((df["Volume"].tail(5).mean() / df["Volume"].tail(20).mean()), 2) if len(df) >= 20 else 0,
    }


def fetch_all(ticker: str, date_str: str) -> dict:
    result = {"ticker": ticker, "date": date_str, "error": None}

    # ── OHLCV ──
    try:
        from tradingagents.dataflows.akshare_data import _load_ohlcv_akshare
        ohlcv = _load_ohlcv_akshare(ticker, date_str)
        result["technicals"] = _describe_ohlcv(ohlcv)
    except Exception as e:
        result["technicals"] = {"rows": 0, "error": str(e)}

    # ── 基本面 ──
    try:
        from tradingagents.dataflows.akshare_data import get_fundamentals, get_fundamentals_structured
        get_fundamentals(ticker, date_str)
        struct = get_fundamentals_structured(ticker)
        # 清理 non-serializable 值
        clean = {}
        for k, v in struct.items():
            if v is not None and not isinstance(v, (str, int, float, bool, list, dict)):
                clean[k] = str(v)
            else:
                clean[k] = v
        result["fundamentals"] = clean
    except Exception as e:
        result["fundamentals"] = {"error": str(e)}

    # ── 估值 ──
    try:
        from tradingagents.dataflows.akshare_data import get_valuation_metrics
        vm = get_valuation_metrics(ticker, date_str)
        result["valuation"] = {k: list(v) for k, v in vm.items()}
    except Exception as e:
        result["valuation"] = {"error": str(e)}

    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: data_fetcher.py TICKER DATE"}))
        sys.exit(1)
    ticker = sys.argv[1]
    date_str = sys.argv[2]
    try:
        data = fetch_all(ticker, date_str)
        print(json.dumps(data, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"error": str(e), "traceback": traceback.format_exc()}))
        sys.exit(1)
