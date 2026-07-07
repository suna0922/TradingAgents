#!/usr/bin/env python3
"""
真实LLM版本：在指定日期生成PM报告，然后连续15天执行回测。
股票：000423 东阿阿胶
日期：2025-01-01, 2025-05-01, 2025-10-01, 2026-04-01

用法:
    DASHSCOPE_CN_API_KEY=sk-xxx .venv/bin/python run_pm_backtest_real.py
"""

import os
import sys
import json
import logging
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── 配置 ─────────────────────────────────────────────────────────
TICKER = "000423"
STOCK_NAME = "东阿阿胶"
PM_DATES = ["2025-01-01", "2025-05-01", "2025-10-01", "2026-04-01"]
BACKTEST_DAYS = 15
INITIAL_CASH = 1_000_000
OUTPUT_DIR = Path("test_results/pm_backtest_real_000423")

# LLM 配置
LLM_PROVIDER = "qwen-cn"
DEEP_THINK_LLM = "qwen3.6-plus"      # PM 用强模型
QUICK_THINK_LLM = "qwen3.6-flash"    # 其他用快模型


def ensure_data(ticker: str, start_date: str, end_date: str):
    """确保有OHLCV数据。"""
    import akshare as ak
    logger.info(f"[Data] Fetching {ticker} from {start_date} to {end_date}")
    df = ak.stock_zh_a_hist(symbol=ticker, period="daily",
                            start_date=start_date.replace("-", ""),
                            end_date=end_date.replace("-", ""), adjust="qfq")
    if df is None or df.empty:
        raise ValueError(f"No data for {ticker}")
    # 统一列名
    df = df.rename(columns={
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "振幅": "amplitude", "涨跌幅": "pct_chg",
        "涨跌额": "change", "换手率": "turn",
    })
    df["date"] = pd.to_datetime(df["date"])
    logger.info(f"[Data] Got {len(df)} rows, {df['date'].min().date()} ~ {df['date'].max().date()}")
    return df


def run_pm_decision(ticker: str, pm_date: str, stock_name: str) -> dict:
    """在指定日期运行完整 pipeline 生成 PM 报告。"""
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG

    config = dict(DEFAULT_CONFIG)
    config["llm_provider"] = LLM_PROVIDER
    config["deep_think_llm"] = DEEP_THINK_LLM
    config["quick_think_llm"] = QUICK_THINK_LLM
    config["output_language"] = "Chinese"
    config["analyst_concurrency_limit"] = 1
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1

    graph = TradingAgentsGraph(config=config)

    logger.info(f"\n{'='*60}")
    logger.info(f"[PM] Running pipeline for {stock_name}({ticker}) @ {pm_date}")
    logger.info(f"{'='*60}")

    try:
        result = graph.propagate(ticker, pm_date, asset_type="stock")
        pm_report = result.get("pm_report", "")
        pm_decision = result.get("pm_decision", {})

        # 提取信号
        signal = "Unknown"
        if pm_decision:
            signal = pm_decision.get("signal", "Unknown")

        logger.info(f"[PM] Signal: {signal}")
        logger.info(f"[PM] Report length: {len(pm_report)} chars")

        return {
            "pm_date": pm_date,
            "ticker": ticker,
            "stock_name": stock_name,
            "signal": signal,
            "pm_report": pm_report,
            "pm_decision": pm_decision,
            "success": True,
        }
    except Exception as e:
        logger.error(f"[PM] Pipeline failed: {e}", exc_info=True)
        return {
            "pm_date": pm_date,
            "ticker": ticker,
            "stock_name": stock_name,
            "signal": "ERROR",
            "pm_report": f"ERROR: {e}",
            "pm_decision": {},
            "success": False,
            "error": str(e),
        }


def parse_trading_rules_from_pm(pm_report: str) -> list:
    """从PM报告中提取交易规则。"""
    from backtest.trading_rules import RuleParser
    parser = RuleParser()
    rules = parser.parse(pm_report)
    logger.info(f"[Rules] Extracted {len(rules)} rules from PM report")
    for i, r in enumerate(rules):
        logger.info(f"  [{i}] {r.description}")
    return rules


def run_backtest(ticker: str, pm_result: dict, df: pd.DataFrame) -> list:
    """从PM日期起连续执行15天回测。"""
    from backtest.models import PortfolioState, TradeDirection, PriceCondition, TechnicalTriggers, FundamentalGuards

    pm_date = pd.Timestamp(pm_result["pm_date"])
    signal = pm_result["signal"]
    pm_report = pm_result["pm_report"]

    # 提取规则
    trading_rules = parse_trading_rules_from_pm(pm_report)

    # 确定方向
    direction = TradeDirection.HOLD
    if "buy" in signal.lower() or "add" in signal.lower():
        direction = TradeDirection.BUY
    elif "sell" in signal.lower():
        direction = TradeDirection.SELL

    # 从报告中提取价格
    import re
    stop_loss = None
    take_profit = None
    m_sl = re.search(r"止损.*?([\d.]+)\s*元", pm_report)
    m_tp = re.search(r"止盈.*?([\d.]+)\s*元", pm_report)
    if m_sl:
        stop_loss = float(m_sl.group(1))
    if m_tp:
        take_profit = float(m_tp.group(1))

    # 过滤回测区间
    df_bt = df[df["date"] >= pm_date].head(BACKTEST_DAYS).copy()
    if len(df_bt) == 0:
        logger.warning(f"[Backtest] No data after {pm_date}")
        return []

    logger.info(f"[Backtest] Running {len(df_bt)} days from {df_bt['date'].iloc[0].date()} to {df_bt['date'].iloc[-1].date()}")

    portfolio = PortfolioState(cash=INITIAL_CASH)
    daily_results = []

    for idx, row in df_bt.iterrows():
        date_str = row["date"].strftime("%Y-%m-%d")
        close = float(row["close"])
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        volume = float(row.get("volume", 0))

        # 涨跌停判断（A股10%）
        prev_close = float(df_bt.iloc[df_bt.index.get_loc(idx) - 1]["close"]) if df_bt.index.get_loc(idx) > 0 else close
        limit_up = round(prev_close * 1.1, 2)
        limit_down = round(prev_close * 0.9, 2)
        at_limit_up = abs(high - limit_up) < 0.01
        at_limit_down = abs(low - limit_down) < 0.01

        # 构建 row_dict
        row_dict = dict(row)
        row_dict["_close"] = close
        row_dict["_high"] = high
        row_dict["_low"] = low

        # 检查交易规则
        action = "HOLD"
        action_price = close
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
                        logger.info(f"[RULE] Alert-only: {rule.name}")
                        continue
            except Exception as e:
                logger.warning(f"[RULE] Evaluate failed: {e}")

        # 如果规则未触发，按信号执行
        if action == "HOLD":
            if direction == TradeDirection.BUY and not has_position and not at_limit_up:
                action = "BUY"
                max_shares = int((portfolio.cash * 0.5) / close // 100) * 100
                action_shares = max_shares
            elif direction == TradeDirection.SELL and has_position:
                action = "SELL"
                action_shares = portfolio.shares
                exit_reason = "signal_sell"

        # 执行交易
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
            logger.info(f"[EXEC] SELL {action_shares} @ {close:.2f} on {date_str} (reason={exit_reason})")

        # 计算市值
        market_value = portfolio.shares * close
        total_value = portfolio.cash + market_value
        pnl = total_value - INITIAL_CASH
        pnl_pct = (pnl / INITIAL_CASH) * 100

        daily_results.append({
            "date": date_str,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "action": action,
            "shares": action_shares,
            "cash": round(portfolio.cash, 2),
            "shares_held": portfolio.shares,
            "market_value": round(market_value, 2),
            "total_value": round(total_value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "triggered_rule": triggered_rule.name if triggered_rule else "",
            "exit_reason": exit_reason,
        })

    return daily_results


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "pm_reports").mkdir(exist_ok=True)

    # 确保数据足够
    start_data = "2024-12-01"
    end_data = "2026-05-30"
    df = ensure_data(TICKER, start_data, end_data)

    all_results = []
    pm_reports_saved = []

    for pm_date_str in PM_DATES:
        pm_date = pd.Timestamp(pm_date_str)

        # 检查该日期是否有数据
        if pm_date > df["date"].max():
            logger.warning(f"[Skip] {pm_date_str} is after last data date {df['date'].max().date()}")
            continue

        # 1. 生成 PM 报告
        pm_result = run_pm_decision(TICKER, pm_date_str, STOCK_NAME)

        # 保存 PM 报告
        report_path = OUTPUT_DIR / "pm_reports" / f"{TICKER}_PM_{pm_date_str}.md"
        report_path.write_text(pm_result["pm_report"], encoding="utf-8")
        pm_reports_saved.append({
            "date": pm_date_str,
            "signal": pm_result["signal"],
            "path": str(report_path),
            "success": pm_result["success"],
        })

        if not pm_result["success"]:
            logger.error(f"[Skip] PM failed for {pm_date_str}")
            continue

        # 2. 执行回测
        daily_results = run_backtest(TICKER, pm_result, df)

        # 3. 保存结果
        if daily_results:
            df_daily = pd.DataFrame(daily_results)
            csv_path = OUTPUT_DIR / f"{TICKER}_{pm_date_str}_daily.csv"
            df_daily.to_csv(csv_path, index=False, encoding="utf-8-sig")
            logger.info(f"[Save] Daily results -> {csv_path}")

            # 汇总
            final = daily_results[-1]
            summary = {
                "pm_date": pm_date_str,
                "signal": pm_result["signal"],
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
            logger.info(f"[Summary] {pm_date_str}: PnL={final['pnl']:.2f} ({final['pnl_pct']:.2f}%) Trades={summary['trades']}")

    # 保存汇总
    if all_results:
        df_summary = pd.DataFrame(all_results)
        summary_path = OUTPUT_DIR / f"{TICKER}_summary.csv"
        df_summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
        logger.info(f"[Save] Summary -> {summary_path}")

    # 保存完整JSON
    full_results = {
        "ticker": TICKER,
        "stock_name": STOCK_NAME,
        "pm_dates": PM_DATES,
        "pm_reports": pm_reports_saved,
        "backtest_summaries": all_results,
    }
    json_path = OUTPUT_DIR / f"{TICKER}_full_results.json"
    json_path.write_text(json.dumps(full_results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[Save] Full results -> {json_path}")

    logger.info(f"\n{'='*60}")
    logger.info("All done!")
    logger.info(f"Output directory: {OUTPUT_DIR.absolute()}")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    main()
