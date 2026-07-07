"""
用 DeepSeek API 在指定日期为指定股票生成 PM 报告，
然后连续 15 天执行 execution，保存 PM 报告和每日执行结果。

用法:
    .venv/bin/python run_deepseek_backtest.py <TICKER> <YYYY-MM-DD>

示例:
    .venv/bin/python run_deepseek_backtest.py 000423 2025-01-01
"""
import sys
import os
sys.path.insert(0, '.')

# 加载 .env（tradingagents 包会自动做，但我们需要在此之前设置 env）
from dotenv import find_dotenv, load_dotenv
load_dotenv(find_dotenv(usecwd=True))

# 覆盖：禁用 benchmark（避免 Yahoo Finance rate limit）
os.environ["TRADINGAGENTS_BENCHMARK_TICKER"] = ""

# Monkey-patch yfinance: 直接返回空，避免 rate limit 拖慢速度
import types
_ymod = types.ModuleType("yfinance")
class _FakeTicker:
    def __init__(self, symbol): pass
    def history(self, **kw): return None
_ymod.Ticker = _FakeTicker
# 伪造 exceptions 子模块，满足 stockstats_utils.py 的 import
_exmod = types.ModuleType("yfinance.exceptions")
class YFRateLimitError(Exception): pass
_exmod.YFRateLimitError = YFRateLimitError
_ymod.exceptions = _exmod
sys.modules["yfinance"] = _ymod
sys.modules["yfinance.exceptions"] = _exmod

import json
import csv
import logging
import time
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from backtest.trading_rules import RuleParser
from backtest.models import TradeDirection, PortfolioState, RuleAction

# ── 配置 ──────────────────────────────────────────────────────────────
TICKER = sys.argv[1] if len(sys.argv) > 1 else "000423"
PM_DATE = sys.argv[2] if len(sys.argv) > 2 else "2025-01-01"
HOLDING_DAYS = 15
INITIAL_CASH = 1_000_000

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# 输出目录
OUT_DIR = Path(f"test_results/deepseek_backtest_{TICKER}_{PM_DATE.replace('-', '')}")
OUT_DIR.mkdir(parents=True, exist_ok=True)

print(f"[CONFIG] TICKER={TICKER}  PM_DATE={PM_DATE}  HOLDING={HOLDING_DAYS}d")
print(f"[CONFIG] llm_provider={DEFAULT_CONFIG['llm_provider']}  deep={DEFAULT_CONFIG['deep_think_llm']}  quick={DEFAULT_CONFIG['quick_think_llm']}")
print(f"[CONFIG] output_dir={OUT_DIR}")

# ── 1. 生成 PM 报告 ────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"[STEP 1] Running propagate for {TICKER} @ {PM_DATE} ...")
print(f"{'='*60}")

config = DEFAULT_CONFIG.copy()
config["benchmark_ticker"] = None

ta = TradingAgentsGraph(debug=True, config=config)
final_state, decision = ta.propagate(TICKER, PM_DATE)

# decision 是 SignalProcessor 简化后的信号（如 "Hold"）
# final_state["final_trade_decision"] 才是完整的 PM 报告 markdown
pm_report = final_state.get("final_trade_decision", str(decision))
print(f"\n[PM Signal] {decision}")
print(f"\n[PM Report]\n{pm_report[:2000]}...")

# 保存 PM 报告
pm_report_path = OUT_DIR / f"{TICKER}_PM_{PM_DATE}.txt"
with open(pm_report_path, "w", encoding="utf-8") as f:
    f.write(f"# PM Report for {TICKER} @ {PM_DATE}\n\n")
    f.write(pm_report)
print(f"[SAVED] PM report: {pm_report_path}")

# ── 2. 解析 trading rules ──────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"[STEP 2] Parsing trading rules from PM report ...")
print(f"{'='*60}")

# 提取信号
signal = "Hold"
if "Buy" in pm_report or "buy" in pm_report.lower():
    signal = "Buy"
elif "Sell" in pm_report or "sell" in pm_report.lower():
    signal = "Sell"
elif "Underweight" in pm_report:
    signal = "Sell"
elif "Overweight" in pm_report:
    signal = "Buy"

direction = TradeDirection.HOLD
if signal in ("Buy", "Overweight"):
    direction = TradeDirection.BUY
elif signal in ("Sell", "Underweight"):
    direction = TradeDirection.SELL

parser = RuleParser()
rules = parser.parse(pm_report, use_llm=False)
print(f"Parsed {len(rules)} trading rules:")
for i, r in enumerate(rules):
    print(f"  [{i}] {r.description}")

if not rules:
    print("[WARN] No trading rules found! Cannot run execution.")
    sys.exit(1)

# 保存 rules
rules_path = OUT_DIR / f"{TICKER}_rules_{PM_DATE}.json"
with open(rules_path, "w", encoding="utf-8") as f:
    json.dump([r.to_dict() for r in rules], f, indent=2, ensure_ascii=False)
print(f"[SAVED] Rules: {rules_path}")

# ── 3. 加载 OHLCV 数据 ────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"[STEP 3] Loading OHLCV data ...")
print(f"{'='*60}")

import baostock as bs

start_dt = datetime.strptime(PM_DATE, "%Y-%m-%d")
end_dt = start_dt + timedelta(days=HOLDING_DAYS + 30)

lg = bs.login()
rs = bs.query_history_k_data_plus(
    f"sz.{TICKER}",
    "date,open,high,low,close,volume",
    start_date=start_dt.strftime("%Y-%m-%d"),
    end_date=end_dt.strftime("%Y-%m-%d"),
    frequency="d", adjustflag="2"
)
data_list = []
while (rs.error_code == "0") & rs.next():
    data_list.append(rs.get_row_data())
bs.logout()

df = pd.DataFrame(data_list, columns=rs.fields)
df["date"] = pd.to_datetime(df["date"])
df["open"] = pd.to_numeric(df["open"], errors="coerce")
df["high"] = pd.to_numeric(df["high"], errors="coerce")
df["low"] = pd.to_numeric(df["low"], errors="coerce")
df["close"] = pd.to_numeric(df["close"], errors="coerce")
df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

print(f"OHLCV data: {len(df)} rows from {df['date'].iloc[0].date()} to {df['date'].iloc[-1].date()}")

# ── 4. 连续 15 天 execution ───────────────────────────────────────────
print(f"\n{'='*60}")
print(f"[STEP 4] Running execution for {HOLDING_DAYS} days ...")
print(f"{'='*60}")

pm_date_ts = pd.Timestamp(PM_DATE)
df_bt = df[df["date"] >= pm_date_ts].head(HOLDING_DAYS).copy()
if len(df_bt) == 0:
    print(f"[ERROR] No trading days from {PM_DATE}")
    sys.exit(1)

print(f"Backtest period: {len(df_bt)} days from {df_bt['date'].iloc[0].date()} to {df_bt['date'].iloc[-1].date()}")

portfolio = PortfolioState(cash=INITIAL_CASH)
daily_results = []

for idx, row in df_bt.iterrows():
    date_str = row["date"].strftime("%Y-%m-%d")
    close = float(row["close"])
    open_price = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    volume = float(row.get("volume", 0))

    # 涨跌停
    pos = df_bt.index.get_loc(idx)
    prev_close = float(df_bt.iloc[pos - 1]["close"]) if pos > 0 else close
    limit_up = round(prev_close * 1.1, 2)
    limit_down = round(prev_close * 0.9, 2)
    at_limit_up = abs(high - limit_up) < 0.01
    at_limit_down = abs(low - limit_down) < 0.01

    row_dict = dict(row)
    row_dict["_close"] = close
    row_dict["_high"] = high
    row_dict["_low"] = low

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
                    logger.info(f"[RULE] Alert: {rule.name}")
                    continue
        except Exception as e:
            logger.warning(f"[RULE] Evaluate failed: {e}")

    # 信号兜底
    if action == "HOLD":
        if direction.value == "buy" and not has_position and not at_limit_up:
            action = "BUY"
            max_shares = int((portfolio.cash * 0.5) / close // 100) * 100
            action_shares = max_shares
        elif direction.value == "sell" and has_position:
            action = "SELL"
            action_shares = portfolio.shares
            exit_reason = "signal_sell"

    # 执行
    trade_value = 0.0
    commission = 0.0
    if action == "BUY" and action_shares > 0:
        trade_value = action_shares * close
        commission = max(trade_value * 0.0003, 5.0)
        if portfolio.cash >= trade_value + commission:
            portfolio.cash -= trade_value + commission
            portfolio.shares += action_shares
            portfolio.avg_cost = close
            logger.info(f"[EXEC] BUY {action_shares} @ {close:.2f} on {date_str}")
        else:
            action = "HOLD"
            action_shares = 0
    elif action == "SELL" and action_shares > 0:
        trade_value = action_shares * close
        commission = max(trade_value * 0.0003, 5.0)
        stamp_tax = trade_value * 0.001
        transfer_fee = trade_value * 0.00002
        total_cost = commission + stamp_tax + transfer_fee
        portfolio.cash += trade_value - total_cost
        portfolio.shares -= action_shares
        logger.info(f"[EXEC] SELL {action_shares} @ {close:.2f} on {date_str} ({exit_reason})")

    market_value = portfolio.shares * close
    total_value = portfolio.cash + market_value
    pnl = total_value - INITIAL_CASH
    pnl_pct = (pnl / INITIAL_CASH) * 100

    daily_results.append({
        "date": date_str, "open": open_price, "high": high, "low": low,
        "close": close, "volume": volume, "action": action,
        "shares": action_shares, "cash": round(portfolio.cash, 2),
        "shares_held": portfolio.shares, "market_value": round(market_value, 2),
        "total_value": round(total_value, 2), "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "triggered_rule": triggered_rule.name if triggered_rule else "",
        "exit_reason": exit_reason,
    })

# ── 5. 保存结果 ────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"[STEP 5] Saving results ...")
print(f"{'='*60}")

daily_csv_path = OUT_DIR / f"{TICKER}_{PM_DATE}_daily.csv"
with open(daily_csv_path, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.writer(f)
    writer.writerow(["date", "open", "high", "low", "close", "volume", "action",
                     "shares", "cash", "shares_held", "market_value", "total_value",
                     "pnl", "pnl_pct", "triggered_rule", "exit_reason"])
    for r in daily_results:
        writer.writerow([
            r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"],
            r["action"], r["shares"], r["cash"], r["shares_held"], r["market_value"],
            r["total_value"], r["pnl"], r["pnl_pct"], r["triggered_rule"], r["exit_reason"],
        ])
print(f"[SAVED] Daily results: {daily_csv_path}")

summary_path = OUT_DIR / f"{TICKER}_{PM_DATE}_summary.json"
with open(summary_path, "w", encoding="utf-8") as f:
    json.dump({
        "ticker": TICKER,
        "pm_date": PM_DATE,
        "signal": signal,
        "direction": direction.value,
        "total_trades": sum(1 for d in daily_results if d["action"] != "HOLD"),
        "final_return_pct": daily_results[-1]["pnl_pct"] if daily_results else 0,
        "final_capital": daily_results[-1]["total_value"] if daily_results else INITIAL_CASH,
        "holding_days": len(daily_results),
        "start_date": daily_results[0]["date"] if daily_results else "",
        "end_date": daily_results[-1]["date"] if daily_results else "",
    }, f, indent=2, ensure_ascii=False)
print(f"[SAVED] Summary: {summary_path}")

print(f"\n{'='*60}")
print(f"[DONE] All results saved to {OUT_DIR}")
print(f"{'='*60}")
print(f"\nSummary:")
print(f"  Signal: {signal}")
print(f"  Direction: {direction.value}")
print(f"  Trades: {sum(1 for d in daily_results if d['action'] != 'HOLD')}")
print(f"  Return: {daily_results[-1]['pnl_pct']:.2f}%")
print(f"  Final Capital: ¥{daily_results[-1]['total_value']:,.0f}")
