#!/usr/bin/env python3
"""测试 BacktestEngine 核心功能 (Phase 1 数据加载 + Phase 3 基准 + Phase 4 统计)
不触发 LLM 调用，仅验证非 LLM 路径"""
import sys, logging
sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from backtest.models import BacktestConfig, PortfolioState
from backtest.backtest_engine import BacktestEngine

config = BacktestConfig(
    symbol="000960",
    start_date="2024-01-02",
    end_date="2026-05-20",
    initial_cash=1_000_000,
)

engine = BacktestEngine(config)

# Phase 1: 数据加载
print("=" * 60)
print("Test: Phase 1 Data Loading")
print("=" * 60)
engine._load_data()
assert engine.df is not None, "df is None after _load_data"
assert len(engine.df) == 573, f"Expected 573 rows, got {len(engine.df)}"
print(f"  ✅ Loaded {len(engine.df)} bars")
print(f"     Range: {engine.df.iloc[0]['date']} ~ {engine.df.iloc[-1]['date']}")
print(f"     Columns: {list(engine.df.columns)}")

# Phase 3: 基准
print()
print("=" * 60)
print("Test: Phase 3 Benchmark (Buy & Hold)")
print("=" * 60)
bm_states = engine._run_benchmark()
assert len(bm_states) == 573, f"Expected 573 benchmark states, got {len(bm_states)}"
bm_first = bm_states[0]
bm_last = bm_states[-1]
bm_return = (bm_last.total_value / config.initial_cash - 1) * 100
print(f"  ✅ Benchmark: {len(bm_states)} states")
print(f"     First day: close={bm_first.close:.2f}, shares={bm_first.shares}, action={bm_first.action}")
print(f"     Last day:  close={bm_last.close:.2f}, total_value=¥{bm_last.total_value:,.0f}")
print(f"     Return: {bm_return:+.2f}%")

# Phase 4: 统计（用空交易历史）
print()
print("=" * 60)
print("Test: Phase 4 Summary Stats (no trades)")
print("=" * 60)
summary = engine._compute_summary(bm_states)
print(f"  ✅ Summary computed:")
for k, v in summary.items():
    if isinstance(v, float):
        print(f"     {k}: {v}")
    else:
        print(f"     {k}: {v}")

# 验证序列化方法
print()
print("=" * 60)
print("Test: Serialization helpers")
print("=" * 60)
d = engine._daily_state_to_dict(bm_states[0])
assert "date" in d and "total_value" in d
print(f"  ✅ daily_state_to_dict: {list(d.keys())}")

t = engine.portfolio.trade_history[0] if engine.portfolio.trade_history else None
if t:
    td = engine._trade_to_dict(t)
    print(f"  ✅ trade_to_dict: {list(td.keys())}")
else:
    print(f"  ⚠ No trades to serialize (expected)")

cd = engine._config_to_dict()
assert "symbol" in cd and "llm_provider" in cd
assert "api_key" not in cd  # 脱敏检查
print(f"  ✅ config_to_dict: {list(cd.keys())} (sanitized, no secrets)")

# Portfolio snapshot
snap = engine._portfolio_snapshot(engine.portfolio)
assert "cash" in snap and "shares" in snap
print(f"  ✅ portfolio_snapshot: {snap}")

print()
print("=" * 60)
print("🎉 All BacktestEngine core tests PASSED!")
print("=" * 60)
