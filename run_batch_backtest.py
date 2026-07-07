"""
批量回测：为指定股票在多个日期生成PM报告，然后各连续15天执行。

用法:
    .venv/bin/python run_batch_backtest.py <TICKER> <DATE1> <DATE2> ...

示例:
    .venv/bin/python run_batch_backtest.py 000423 2025-01-01 2025-05-01 2025-10-01 2026-04-01
"""
import sys
import os
sys.path.insert(0, '.')

from dotenv import find_dotenv, load_dotenv
load_dotenv(find_dotenv(usecwd=True))

# 禁用 benchmark（避免 Yahoo Finance）
os.environ["TRADINGAGENTS_BENCHMARK_TICKER"] = ""

# Monkey-patch yfinance
import types
_ymod = types.ModuleType("yfinance")
class _FakeTicker:
    def __init__(self, symbol): pass
    def history(self, **kw): return None
_ymod.Ticker = _FakeTicker
_exmod = types.ModuleType("yfinance.exceptions")
class YFRateLimitError(Exception): pass
_exmod.YFRateLimitError = YFRateLimitError
_ymod.exceptions = _exmod
sys.modules["yfinance"] = _ymod
sys.modules["yfinance.exceptions"] = _exmod

import json
import csv
import logging
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.dataflows.stockstats_utils import load_ohlcv, wrap
from backtest.trading_rules import RuleParser
from backtest.models import TradeDirection, PortfolioState

# ── 配置 ──────────────────────────────────────────────────────────────
TICKER = sys.argv[1] if len(sys.argv) > 1 else "000423"
DATES = sys.argv[2:] if len(sys.argv) > 2 else ["2025-01-01"]
HOLDING_DAYS = 30
INITIAL_CASH = 1_000_000

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

print(f"[CONFIG] TICKER={TICKER}")
print(f"[CONFIG] DATES={DATES}")
print(f"[CONFIG] HOLDING={HOLDING_DAYS}d")
print(f"[CONFIG] llm_provider={DEFAULT_CONFIG['llm_provider']}")


def run_single_backtest(pm_date: str) -> dict:
    """为单个日期跑完整回测。"""
    out_dir = Path(f"test_results/deepseek_backtest_{TICKER}_{pm_date.replace('-', '')}")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*70}")
    print(f"[BACKTEST] {TICKER} @ {pm_date}")
    print(f"{'='*70}")

    # ── 1. 获取 PM 报告（优先用缓存，避免 akshare 网络问题）─────────────────
    cached_pm_dir = Path("test_results/pm_backtest_from_cache_000423/pm_reports")
    cached_pm_path = cached_pm_dir / f"{TICKER}_PM_{pm_date}.md"

    if cached_pm_path.exists():
        print(f"\n[STEP 1] Loading cached PM report from {cached_pm_path}")
        with open(cached_pm_path, "r", encoding="utf-8") as f:
            pm_report = f.read()
    else:
        print(f"\n[STEP 1] Generating PM report ...")
        config = DEFAULT_CONFIG.copy()
        config["benchmark_ticker"] = None
        ta = TradingAgentsGraph(debug=True, config=config)
        final_state, decision = ta.propagate(TICKER, pm_date)
        pm_report = final_state.get("final_trade_decision", str(decision))

    # 保存 PM 报告到输出目录
    pm_path = out_dir / f"{TICKER}_PM_{pm_date}.txt"
    with open(pm_path, "w", encoding="utf-8") as f:
        f.write(f"# PM Report for {TICKER} @ {pm_date}\n\n")
        f.write(pm_report)
    print(f"[SAVED] PM report: {pm_path}")

    # ── 2. 解析 trading rules ─────────────────────────────────────
    print(f"\n[STEP 2] Parsing trading rules ...")
    parser = RuleParser()
    rules = parser.parse(pm_report, use_llm=False)
    print(f"Parsed {len(rules)} trading rules:")
    for i, r in enumerate(rules):
        print(f"  [{i}] {r.description}")
        print(f"        condition_str: {r.condition_str}")

    if not rules:
        print("[WARN] No trading rules found!")
        return {"date": pm_date, "error": "no_rules"}

    # 保存 rules
    rules_path = out_dir / f"{TICKER}_rules_{pm_date}.json"
    with open(rules_path, "w", encoding="utf-8") as f:
        json.dump([r.to_dict() for r in rules], f, indent=2, ensure_ascii=False)
    print(f"[SAVED] Rules: {rules_path}")

    # ── 3. 加载 OHLCV + 指标 ──────────────────────────────────────
    print(f"\n[STEP 3] Loading OHLCV with indicators ...")

    # 加载到回测结束日期后60天（确保有足够数据算指标）
    end_dt = datetime.strptime(pm_date, "%Y-%m-%d") + timedelta(days=HOLDING_DAYS + 60)
    end_str = end_dt.strftime("%Y-%m-%d")

    # 优先用baostock（更稳定），fallback到akshare
    try:
        import baostock as bs
        lg = bs.login()
        market = "sh" if TICKER.startswith("6") else "sz"
        rs = bs.query_history_k_data_plus(
            f"{market}.{TICKER}",
            "date,open,high,low,close,volume",
            start_date="2020-01-01",
            end_date=end_str,
            frequency="d", adjustflag="2"
        )
        data_list = []
        while (rs.error_code == "0") & rs.next():
            data_list.append(rs.get_row_data())
        bs.logout()
        df_raw = pd.DataFrame(data_list, columns=rs.fields)
        df_raw["date"] = pd.to_datetime(df_raw["date"])
        df_raw = df_raw.rename(columns={
            "date": "Date", "open": "Open", "high": "High",
            "low": "Low", "close": "Close", "volume": "Volume"
        })
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce")
        print(f"[DATA] Loaded from baostock: {len(df_raw)} rows")
    except Exception as e:
        print(f"[WARN] baostock failed: {e}, fallback to akshare")
        df_raw = load_ohlcv(TICKER, end_str)

    df = wrap(df_raw)

    # 触发所有可能用到的指标计算
    indicator_cols = [
        'rsi_14', 'macd', 'macds', 'macdh', 'boll', 'boll_ub', 'boll_lb',
        'close_5_sma', 'close_10_sma', 'close_20_sma', 'close_50_sma',
        'close_60_sma', 'close_120_sma',
    ]
    for col in indicator_cols:
        try:
            _ = df[col]
        except:
            pass

    print(f"OHLCV: {len(df)} rows, columns: {list(df.columns)}")

    # ── 4. 连续 HOLDING_DAYS 天 execution ─────────────────────────
    print(f"\n[STEP 4] Running execution for {HOLDING_DAYS} days ...")

    pm_date_ts = pd.Timestamp(pm_date)
    df_bt = df[df["Date"] >= pm_date_ts].head(HOLDING_DAYS).copy()
    if len(df_bt) == 0:
        print(f"[ERROR] No trading days from {pm_date}")
        return {"date": pm_date, "error": "no_data"}

    print(f"Backtest period: {len(df_bt)} days from {df_bt['Date'].iloc[0]} to {df_bt['Date'].iloc[-1]}")

    portfolio = PortfolioState(cash=INITIAL_CASH)
    daily_results = []
    trades = []

    for pos, (idx, row) in enumerate(df_bt.iterrows()):
        date_str = str(row["Date"])[:10]
        close = float(row["close"])
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        volume = float(row.get("volume", 0))
        pct_chg = float(row.get("pct_chg", 0)) if "pct_chg" in row else 0.0

        # 涨跌停检测
        prev_close = float(df_bt.iloc[pos - 1]["close"]) if pos > 0 else close
        limit_up = round(prev_close * 1.1, 2)
        limit_down = round(prev_close * 0.9, 2)
        at_limit_up = abs(high - limit_up) < 0.01
        at_limit_down = abs(low - limit_down) < 0.01

        # 构建 row_dict（包含所有字段）
        row_dict = row.to_dict()
        row_dict["_close"] = close
        row_dict["_high"] = high
        row_dict["_low"] = low
        row_dict["_df"] = df
        row_dict["_idx"] = idx

        # 检查规则
        action = "HOLD"
        action_shares = 0
        exit_reason = ""
        triggered_rule = None
        has_position = portfolio.shares > 0

        for rule in rules:
            if not rule.enabled:
                continue
            try:
                if rule.evaluate_all(row_dict):
                    triggered_rule = rule
                    logger.info(f"[RULE] Triggered @ {date_str}: {rule.description}")

                    if rule.action.value in ("stop_loss", "take_profit", "sell_all", "sell_half", "sell_pct", "downgrade"):
                        if has_position:
                            action = "SELL"
                            if rule.action.value == "sell_half":
                                action_shares = (portfolio.shares // 2 // 100) * 100
                                exit_reason = "reduce_position"
                            else:
                                action_shares = portfolio.shares
                                exit_reason = rule.action.value
                            break
                        else:
                            continue
                    elif rule.action.value == "buy_add":
                        if not at_limit_up and not has_position:
                            action = "BUY"
                            max_shares = int((portfolio.cash * 0.5) / close // 100) * 100
                            action_shares = max_shares
                            break
                        else:
                            continue
                    elif rule.action.value == "hold":
                        action = "HOLD"
                        break
            except Exception as e:
                logger.warning(f"[RULE] Eval error @ {date_str}: {e}")
                continue

        # 执行交易
        if action == "BUY" and action_shares > 0:
            cost = action_shares * close
            portfolio.cash -= cost
            portfolio.shares += action_shares
            trades.append({
                "date": date_str, "action": "BUY", "shares": action_shares,
                "price": close, "cost": cost
            })
        elif action == "SELL" and action_shares > 0:
            revenue = action_shares * close
            portfolio.cash += revenue
            portfolio.shares -= action_shares
            trades.append({
                "date": date_str, "action": "SELL", "shares": action_shares,
                "price": close, "revenue": revenue, "reason": exit_reason
            })

        position_value = portfolio.shares * close
        total_value = portfolio.cash + position_value

        daily_results.append({
            "date": date_str,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "action": action,
            "shares": action_shares,
            "cash": portfolio.cash,
            "shares_held": portfolio.shares,
            "market_value": position_value,
            "total_value": total_value,
            "pnl": total_value - INITIAL_CASH,
            "pnl_pct": (total_value - INITIAL_CASH) / INITIAL_CASH,
            "triggered_rule": triggered_rule.description if triggered_rule else "",
            "exit_reason": exit_reason,
        })

    # ── 5. 保存结果 ───────────────────────────────────────────────
    # CSV
    csv_path = out_dir / f"{TICKER}_{pm_date}_daily.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=daily_results[0].keys())
        writer.writeheader()
        writer.writerows(daily_results)
    print(f"[SAVED] Daily CSV: {csv_path}")

    # Summary
    final_value = daily_results[-1]["total_value"]
    summary = {
        "ticker": TICKER,
        "pm_date": pm_date,
        "holding_days": len(daily_results),
        "initial_cash": INITIAL_CASH,
        "final_value": final_value,
        "pnl": final_value - INITIAL_CASH,
        "pnl_pct": (final_value - INITIAL_CASH) / INITIAL_CASH,
        "trades": trades,
        "rules_count": len(rules),
    }
    summary_path = out_dir / f"{TICKER}_{pm_date}_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"[SAVED] Summary: {summary_path}")

    print(f"\n[RESULT] {pm_date}: Final={final_value:.2f} PnL={summary['pnl']:.2f} ({summary['pnl_pct']*100:+.2f}%)")

    return summary


# ── 主循环 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    all_results = []
    for date in DATES:
        try:
            result = run_single_backtest(date)
            all_results.append(result)
        except Exception as e:
            logger.error(f"Backtest failed for {date}: {e}", exc_info=True)
            all_results.append({"date": date, "error": str(e)})

    # 总汇总
    print(f"\n{'='*70}")
    print("[ALL RESULTS]")
    print(f"{'='*70}")
    for r in all_results:
        if "error" in r:
            print(f"  {r['date']}: ERROR - {r['error']}")
        else:
            print(f"  {r['pm_date']}: {r['final_value']:.2f} ({r['pnl_pct']*100:+.2f}%)")
