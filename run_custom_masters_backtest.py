#!/usr/bin/env python3
"""
Run backtest with custom master methodology configuration.

Usage:
  .venv/bin/python run_custom_masters_backtest.py --symbol 000423 --start 2026-01-01 --end 2026-07-06
"""

import sys, os

_PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROJECT)

# ── Load .env ─────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("[ENV] .env loaded")
except ImportError:
    env_file = os.path.join(_PROJECT, ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        print("[ENV] .env loaded (manual)")

# ── Apply custom master config ─────────────────────────────────────
from tradingagents.default_config import DEFAULT_CONFIG

# 用户指定的大师配置
CUSTOM_MASTER_CONFIG = {
    "bull_researcher": "buffett",
    "bear_researcher": "graham",
    "aggressive_debator": "lynch",
    "conservative_debator": "duan_yongping",
    "neutral_debator": "munger",
    "portfolio_manager": "buffett",
    "fundamentals_analyst": "buffett",
    "market_analyst": "lynch",
}

DEFAULT_CONFIG["master_config"] = CUSTOM_MASTER_CONFIG

print("[CONFIG] Custom master methodology applied:")
for role, master in sorted(CUSTOM_MASTER_CONFIG.items()):
    print(f"  {role:25s} → {master}")

# ── Backup & clear memory log (prevents past_context bloat) ──────
import pandas as pd  # needed early for timestamp
_memory_log_path = DEFAULT_CONFIG.get("memory_log_path", "")
if _memory_log_path and os.path.exists(_memory_log_path):
    _sz = os.path.getsize(_memory_log_path)
    _bak = _memory_log_path + f".bak_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}"
    os.rename(_memory_log_path, _bak)
    print(f"[MEMORY] Backed up memory log ({_sz:,} bytes) → {_bak}")
    print(f"[MEMORY] Cleared memory log for fresh backtest run")
else:
    print(f"[MEMORY] No existing memory log at {_memory_log_path}")

# ── Parse arguments ────────────────────────────────────────────────
import argparse
parser = argparse.ArgumentParser(description="Run custom masters backtest")
parser.add_argument("--symbol", default="000423", help="Stock symbol (default: 000423)")
parser.add_argument("--start", default="2026-01-01", help="Start date (YYYY-MM-DD)")
parser.add_argument("--end", default="2026-07-06", help="End date (YYYY-MM-DD)")
parser.add_argument("--initial-cash", type=float, default=1_000_000.0, help="Initial cash")
parser.add_argument("--price-change-threshold", type=float, default=0.10, help="Price change threshold")
parser.add_argument("--stale-days", type=int, default=15, help="Stale days before re-analysis")
args = parser.parse_args()

# Adjust start date if it's a weekend/holiday (first trading day)
import pandas as pd
start_dt = pd.Timestamp(args.start)
if start_dt.dayofweek >= 5:  # Saturday=5, Sunday=6
    # Move to next Monday
    start_dt = start_dt + pd.offsets.Day(7 - start_dt.dayofweek)
    print(f"[DATE] Adjusted start date to {start_dt.strftime('%Y-%m-%d')} (skip weekend)")

start_date_str = start_dt.strftime("%Y-%m-%d")

# ── Clear relevant L1 cache ────────────────────────────────────────
import json
cache_dir = os.path.join(_PROJECT, "backtest_results", "hybrid_custom", "l1_cache")
if os.path.exists(cache_dir):
    symbol = args.symbol
    for fname in os.listdir(cache_dir):
        if fname.startswith(f"q_{symbol}_") and fname.endswith(".json"):
            path = os.path.join(cache_dir, fname)
            os.remove(path)
            print(f"[CACHE] Cleared stale cache: {fname}")

# ── Run backtest ──────────────────────────────────────────────────
from backtest_hybrid import HybridBacktestEngine

output_dir = f"backtest_results/hybrid_custom_{args.symbol}"

engine = HybridBacktestEngine(
    symbol=args.symbol,
    start_date=start_date_str,
    end_date=args.end,
    initial_cash=args.initial_cash,
    price_change_threshold=args.price_change_threshold,
    stale_days=args.stale_days,
    output_dir=output_dir,
)

print(f"\n{'='*60}")
print(f"  Starting hybrid backtest: {args.symbol} (custom masters)")
print(f"  Masters: bull=buffett, bear=graham, agg=lynch, cons=duan_yongping, neu=munger, pm=buffett, fa=buffett, ma=lynch")
print(f"  Period: {start_date_str} → {args.end}")
print(f"{'='*60}\n")

result = engine.run()

# ── Save & print results ──────────────────────────────────────────
out_dir = os.path.join(_PROJECT, output_dir, args.symbol)
os.makedirs(out_dir, exist_ok=True)

ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")

# Full result
result_file = os.path.join(out_dir, f"result_{ts}.json")
with open(result_file, "w", encoding="utf-8") as f:
    json.dump(result.to_dict(), f, indent=2, ensure_ascii=False, default=str)

# Summary
summary_file = os.path.join(out_dir, f"summary_{ts}.json")
with open(summary_file, "w", encoding="utf-8") as f:
    json.dump(result.summary, f, indent=2, ensure_ascii=False)

# Print summary
s = result.summary
print(f"\n{'='*60}")
print(f"  Hybrid Backtest Result: {args.symbol} (custom masters)")
print(f"{'='*60}")
print(f"  Period:         {s.get('period', 'N/A')}")
print(f"  Initial Cash:   ¥{s['initial_cash']:,.0f}")
print(f"  Final Value:    ¥{s['final_value']:,.0f}")
print(f"  Total Return:   {s['total_return_pct']:+.2f}%")
print(f"  Max Drawdown:   {s['max_drawdown_pct']:.2f}%")
print(f"  Sharpe Ratio:   {s['sharpe_ratio']:.2f}")
print(f"  Benchmark:      {s['benchmark_return_pct']:+.2f}%")
print(f"  Total Trades:   {s['total_trades']}")
print(f"\n  Results saved to:")
print(f"    {result_file}")
print(f"    {summary_file}")
print(f"{'='*60}")
