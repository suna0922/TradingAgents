#!/usr/bin/env python3
"""
使用已有的真实PM报告跑回测链路验证。
从 reports/logs/000423/ 下的历史报告提取PM决策，然后执行15天回测。

同时测试：
1. 旧版 IF 格式规则 → _structured_extract 解析
2. 新版 WHEN sql 格式规则 → _sql_extract 解析
3. 两种格式在 ExecutionEngine 中的兼容性
"""

import os
import sys
import json
import logging
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TICKER = "000423"
STOCK_NAME = "东阿阿胶"
BACKTEST_DAYS = 15
INITIAL_CASH = 1_000_000
OUTPUT_DIR = Path("test_results/pm_backtest_from_cache_000423")


def load_pm_report_from_cache(ticker: str, date_str: str) -> str:
    """从缓存目录加载PM报告。"""
    # 尝试直接匹配日期
    report_path = Path(f"reports/logs/{ticker}/{date_str}/reports/final_trade_decision.md")
    if report_path.exists():
        return report_path.read_text(encoding="utf-8")

    # 尝试找最近的日期
    ticker_dir = Path(f"reports/logs/{ticker}")
    if not ticker_dir.exists():
        return ""

    dates = [d for d in ticker_dir.iterdir() if d.is_dir() and len(d.name) == 10 and d.name[4] == '-']
    if not dates:
        return ""

    # 找最接近的日期
    target = pd.Timestamp(date_str)
    closest = min(dates, key=lambda d: abs(pd.Timestamp(d.name) - target))
    report_path = closest / "reports" / "final_trade_decision.md"
    if report_path.exists():
        logger.info(f"[Cache] Using closest date {closest.name} for {date_str}")
        return report_path.read_text(encoding="utf-8")

    return ""


def ensure_data(ticker: str, start_date: str, end_date: str):
    """获取OHLCV数据。"""
    import akshare as ak
    df = ak.stock_zh_a_hist(symbol=ticker, period="daily",
                            start_date=start_date.replace("-", ""),
                            end_date=end_date.replace("-", ""), adjust="qfq")
    df = df.rename(columns={
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "涨跌幅": "pct_chg", "换手率": "turn",
    })
    df["date"] = pd.to_datetime(df["date"])
    return df


def parse_pm_report(pm_report: str) -> tuple:
    """解析PM报告，提取信号和交易规则。"""
    from backtest.trading_rules import RuleParser, RuleAction
    from backtest.models import TradeDirection

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

    # 确定方向
    direction = TradeDirection.HOLD
    if signal in ("Buy", "Overweight"):
        direction = TradeDirection.BUY
    elif signal in ("Sell", "Underweight"):
        direction = TradeDirection.SELL

    # 解析交易规则
    parser = RuleParser()
    rules = parser.parse(pm_report)
    logger.info(f"[Parse] Extracted {len(rules)} rules")
    for i, r in enumerate(rules):
        logger.info(f"  [{i}] {r.description}")

    return direction, rules, signal


def run_backtest(ticker: str, pm_date_str: str, direction, trading_rules, df: pd.DataFrame) -> list:
    """从PM日期起连续执行15天回测。"""
    from backtest.models import PortfolioState, RuleAction

    pm_date = pd.Timestamp(pm_date_str)
    df_bt = df[df["date"] >= pm_date].head(BACKTEST_DAYS).copy()
    if len(df_bt) == 0:
        return []

    logger.info(f"[Backtest] {len(df_bt)} days from {df_bt['date'].iloc[0].date()} to {df_bt['date'].iloc[-1].date()}")

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

        for rule in trading_rules:
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

    return daily_results


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "pm_reports").mkdir(exist_ok=True)

    # 加载数据
    df = ensure_data(TICKER, "2024-12-01", "2026-05-30")
    logger.info(f"[Data] {len(df)} rows loaded")

    # 使用已有的真实PM报告日期
    test_dates = [
        ("2025-02-01", "2025-02-01"),  # 已有缓存
        ("2026-06-01", "2026-06-01"),  # 已有缓存
    ]

    # 同时测试4个目标日期（如果没有缓存就用模拟）
    target_dates = ["2025-01-01", "2025-05-01", "2025-10-01", "2026-04-01"]

    all_results = []

    for target_date in target_dates:
        logger.info(f"\n{'='*60}")
        logger.info(f"[Test] {STOCK_NAME}({TICKER}) @ {target_date}")
        logger.info(f"{'='*60}")

        # 尝试加载缓存报告
        pm_report = load_pm_report_from_cache(TICKER, target_date)

        if not pm_report:
            logger.warning(f"[Cache] No report found for {target_date}, using synthetic")
            # 用模拟数据
            pm_report = f"""
# Portfolio Manager Decision - {STOCK_NAME}

**Rating:** Buy
**Position:** 80%

## Trading Rules
- [RULE1] [stop_loss] WHEN close < {df[df['date'] >= pd.Timestamp(target_date)]['close'].iloc[0] * 0.9:.2f} THEN stop_loss — 清仓
- [RULE2] [take_profit] WHEN close > {df[df['date'] >= pd.Timestamp(target_date)]['close'].iloc[0] * 1.1:.2f} THEN take_profit — 止盈
"""

        # 保存报告
        report_path = OUTPUT_DIR / "pm_reports" / f"{TICKER}_PM_{target_date}.md"
        report_path.write_text(pm_report, encoding="utf-8")

        # 解析
        direction, rules, signal = parse_pm_report(pm_report)

        # 回测
        daily_results = run_backtest(TICKER, target_date, direction, rules, df)

        if daily_results:
            df_daily = pd.DataFrame(daily_results)
            csv_path = OUTPUT_DIR / f"{TICKER}_{target_date}_daily.csv"
            df_daily.to_csv(csv_path, index=False, encoding="utf-8-sig")

            final = daily_results[-1]
            summary = {
                "pm_date": target_date,
                "signal": signal,
                "start_date": daily_results[0]["date"],
                "end_date": daily_results[-1]["date"],
                "days": len(daily_results),
                "trades": sum(1 for d in daily_results if d["action"] != "HOLD"),
                "final_value": final["total_value"],
                "pnl": final["pnl"],
                "pnl_pct": final["pnl_pct"],
                "csv_path": str(csv_path),
            }
            all_results.append(summary)
            logger.info(f"[Result] PnL={final['pnl']:.2f} ({final['pnl_pct']:.2f}%) Trades={summary['trades']}")

    # 保存汇总
    if all_results:
        df_summary = pd.DataFrame(all_results)
        df_summary.to_csv(OUTPUT_DIR / f"{TICKER}_summary.csv", index=False, encoding="utf-8-sig")
        logger.info(f"\n[Save] Summary saved")

    logger.info(f"\n{'='*60}")
    logger.info(f"Done! Output: {OUTPUT_DIR.absolute()}")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    main()
