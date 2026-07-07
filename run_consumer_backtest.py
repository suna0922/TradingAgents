#!/usr/bin/env python3
"""
Run backtest with consumer industry master preset.

Usage:
  .venv/bin/python run_consumer_backtest.py --symbol 000423 --start 2025-01-01 --end 2025-01-07
"""

import sys, os

_PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROJECT)

# ── Load .env (DeepSeek API key etc.) ───────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("[ENV] .env loaded")
except ImportError:
    # fallback: manually source .env
    env_file = os.path.join(_PROJECT, ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        print("[ENV] .env loaded (manual)")

# ── Apply consumer preset to DEFAULT_CONFIG ───────────────────────────────────────
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.masters.industry_presets import industry_preset, apply_industry_preset

# Apply preset: modifies DEFAULT_CONFIG["master_config"] in-place
apply_industry_preset("consumer")

print("[CONFIG] Consumer industry preset applied:")
for role, master in sorted(DEFAULT_CONFIG["master_config"].items()):
    print(f"  {role:25s} → {master}")

# ── Clear L1 cache for 000423 Q1 2025 (force fresh LLM with consumer preset) ──
cache_dir = os.path.join(_PROJECT, "backtest_results", "hybrid", "l1_cache")
if os.path.exists(cache_dir):
    for fname in os.listdir(cache_dir):
        if fname.startswith("q_000423_2025Q1") and fname.endswith(".json"):
            path = os.path.join(cache_dir, fname)
            os.remove(path)
            print(f"[CACHE] Cleared stale cache: {fname}")

# ── Run backtest ──────────────────────────────────────────────────────────────────
from backtest_hybrid import HybridBacktestEngine

engine = HybridBacktestEngine(
    symbol="000423",
    start_date="2025-01-02",   # Jan 1 is a holiday; first trading day
    end_date="2025-01-07",
    initial_cash=1_000_000.0,
    price_change_threshold=0.10,
    stale_days=15,
    output_dir="backtest_results/hybrid_consumer",
)

print(f"\n{'='*60}")
print(f"  Starting hybrid backtest: 000423 东阿阿胶 (consumer preset)")
print(f"  Period: 2025-01-02 → 2025-01-07")
print(f"{'='*60}\n")

result = engine.run()

# ── Save & print results ─────────────────────────────────────────────────────────
out_dir = os.path.join(_PROJECT, "backtest_results", "hybrid_consumer", "000423")
os.makedirs(out_dir, exist_ok=True)

from datetime import datetime
ts = datetime.now().strftime("%Y%m%d_%H%M%S")

# Full result
result_file = os.path.join(out_dir, f"result_{ts}.json")
with open(result_file, "w", encoding="utf-8") as f:
    import json
    json.dump(result.to_dict(), f, indent=2, ensure_ascii=False, default=str)

# Summary
summary_file = os.path.join(out_dir, f"summary_{ts}.json")
with open(summary_file, "w", encoding="utf-8") as f:
    json.dump(result.summary, f, indent=2, ensure_ascii=False)

# Print summary
s = result.summary
print(f"\n{'='*60}")
print(f"  Hybrid Backtest Result: 000423 (consumer preset)")
print(f"{'='*60}")
print(f"  Period:         {s.get('period', 'N/A')}")
print(f"  Initial Cash:   ¥{s['initial_cash']:,.0f}")
print(f"  Final Value:    ¥{s['final_value']:,.0f}")
print(f"  Total Return:   {s['total_return_pct']:+.2f}%")
print(f"  Max Drawdown:   {s['max_drawdown_pct']:.2f}%")
print(f"  Sharpe Ratio:   {s['sharpe_ratio']:.2f}")
print(f"  Benchmark:      {s['benchmark_return_pct']:+.2f}%")
print(f"  Trades:         {s['total_trades']} ({s['win_trades']} wins, {s['win_rate_pct']:.1f}%)")
print(f"  Total PnL:      ¥{s['total_pnl']:+,.2f}")
print(f"  L1 Analyses:    {s['l1_analyses_total']} (full={s['l1_analyses_full']}, quick={s['l1_analyses_quick']})")
print(f"  Trading Days:   {s['trading_days']}")
print(f"\n  Results saved to: {out_dir}")
print(f"  Full result:      {result_file}")
print(f"  Summary:          {summary_file}")
print(f"{'='*60}\n")

# Also save the master config used
config_file = os.path.join(out_dir, f"master_config_{ts}.json")
with open(config_file, "w", encoding="utf-8") as f:
    json.dump(DEFAULT_CONFIG.get("master_config", {}), f, indent=2, ensure_ascii=False)
print(f"  Master config:    {config_file}")
