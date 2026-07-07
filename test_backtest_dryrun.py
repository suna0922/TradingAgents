#!/usr/bin/env python3
"""回测系统端到端 dry-run 测试 (mock decision, 无 LLM 调用)"""

import sys
import logging
import numpy as np

sys.path.insert(0, ".")
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s"
)

from backtest.models import (
    BacktestConfig, PortfolioState, WeeklyDecision, TradeDirection,
    PriceCondition, TechnicalTriggers, FundamentalGuards, DailyState,
)
from backtest.cache_manager import CacheManager
from backtest.data_layer import DataLayer
from backtest.execution_engine import ExecutionEngine


def main():
    config = BacktestConfig(
        symbol="000960",
        start_date="2024-01-02",
        end_date="2026-05-20",
        initial_cash=1_000_000,
    )

    cache = CacheManager(config.output_dir)
    dl = DataLayer(config.symbol, config.start_date, config.end_date)
    exec_engine = ExecutionEngine(config, dl)

    # === Phase 1: 数据加载 ===
    print("=" * 60)
    print("Phase 1: 数据加载")
    print("=" * 60)
    df = dl.fetch_ohlcv()
    stats = dl.compute_indicators()
    df = stats.reset_index()
    total_days = len(df)
    print(f"总交易日: {total_days}")
    print(f"范围: {df.iloc[0]['date']} ~ {df.iloc[-1]['date']}")
    print(f"价格范围: {df['close'].min():.2f} ~ {df['close'].max():.2f}")

    # === Phase 2: 回测循环 ===
    print()
    print("=" * 60)
    print("Phase 2: 回测循环 (Mock BUY @ Day30)")
    print("=" * 60)

    portfolio = PortfolioState(cash=config.initial_cash)

    day30_idx = min(30, total_days - 1)
    day30_close = float(df.iloc[day30_idx]["close"])
    day30_date = str(df.iloc[day30_idx]["date"])[:10]
    print(f"Day[{day30_idx}] ({day30_date}) close={day30_close:.2f}")

    mock_decision = WeeklyDecision(
        direction=TradeDirection.BUY,
        position_pct=0.6,
        price_cond=PriceCondition(
            stop_loss=day30_close * 0.85,
            take_profit=day30_close * 1.3,
            buy_range=(day30_close * 0.95, day30_close * 1.05),
        ),
        technical_triggers=TechnicalTriggers(),
        fundamental_guards=FundamentalGuards(),
        decision_date=day30_date,
        signal_raw="Buy",
        pm_rating="Buy",
        pm_raw_output="",
    )

    active_decision = None

    for idx in range(total_days):
        row = df.iloc[idx]

        # Mock L1: Day30 激活决策
        if idx == day30_idx:
            active_decision = mock_decision
            print(f"  [L1] Decision activated: BUY @{day30_date}")

        # L2 执行 (纯规则) — execute() 内部已 append 到 state_history
        state = exec_engine.execute(portfolio, active_decision, row, idx, df)

    # === Phase 3: 基准对比 ===
    print()
    print("=" * 60)
    print("Phase 3: 基准对比 (买入持有)")
    print("=" * 60)

    first_close = float(df.iloc[0]["close"])
    bm_shares = int(config.initial_cash / first_close / 100) * 100
    bm_cash = config.initial_cash - bm_shares * first_close
    bm_final = bm_cash + bm_shares * float(df.iloc[-1]["close"])
    bm_return = (bm_final / config.initial_cash - 1) * 100
    print(f"基准买入: {bm_shares} 股 @ {first_close:.2f} | 剩余现金: ¥{bm_cash:,.0f}")
    print(f"基准收益: {bm_return:+.2f}%")

    # === Phase 4: 统计汇总 ===
    print()
    print("=" * 60)
    print("Phase 4: 统计汇总")
    print("=" * 60)

    final_total = portfolio.cash + portfolio.shares * float(df.iloc[-1]["close"])
    total_return = (final_total / config.initial_cash - 1) * 100

    # 最大回撤
    peak = config.initial_cash
    max_dd = 0.0
    for s in portfolio.state_history:
        if s.total_value > peak:
            peak = s.total_value
        dd = (peak - s.total_value) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    # 夏普比率
    daily_returns = []
    prev_val = config.initial_cash
    for s in portfolio.state_history:
        if prev_val > 0:
            daily_returns.append(s.total_value / prev_val - 1)
        prev_val = s.total_value

    if len(daily_returns) > 5:
        sharpe = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252)
    else:
        sharpe = 0.0

    # 胜率 & 盈亏比
    trades = portfolio.trade_history
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    win_rate = len(wins) / len(trades) if trades else 0
    total_pnl_w = sum(t.pnl for t in wins)
    total_pnl_l = abs(sum(t.pnl for t in losses)) if losses else 1
    profit_factor = total_pnl_w / total_pnl_l if total_pnl_l > 0 else float("inf")

    alpha = total_return - bm_return

    print(f"+{'─'*46}+")
    print(f"|  回测结果: {config.symbol} 锡业股份                      |")
    print(f"|  周期: {config.start_date} ~ {config.end_date} |")
    print(f"+{'─'*46}+")
    print(f"|  总收益率:     {total_return:>10.2f}%                    |")
    print(f"|  基准收益:     {bm_return:>10.2f}%                    |")
    print(f"|  超额收益(α):  {alpha:>10.2f}%                    |")
    print(f"|  最大回撤:     {max_dd*100:>10.2f}%                    |")
    print(f"|  夏普比率:     {sharpe:>10.2f}                    |")
    print(f"|  交易胜率:     {win_rate*100:>9.1f}%                     |")
    print(f"|  交易次数:     {len(trades):>10d}                    |")
    print(f"|  盈亏比:       {profit_factor:>10.2f}                    |")
    print(f"|  最终资产:     ¥{final_total:>12,.0f}          |")
    print(f"+{'─'*46}+")

    # 交易明细
    if trades:
        print()
        print(f"交易明细 ({len(trades)} 笔):")
        for i, t in enumerate(trades):
            sign = "+" if t.pnl > 0 else ""
            print(f"  {i+1}. {t.direction:4s} {t.shares:d}股 "
                  f"@{t.entry_price:.2f}→{t.exit_price:.2f} "
                  f"{sign}{t.pnl:,.0f} ({t.pnl_pct*100:+.2f}%) "
                  f"[{t.exit_reason}]")

    # 验证数据完整性
    assert len(portfolio.state_history) == total_days, \
        f"状态数({len(portfolio.state_history)}) != 交易日数({total_days})"
    print()
    print(f"✅ 数据完整性验证通过: {len(portfolio.state_history)} 日快照")


if __name__ == "__main__":
    main()
