#!/usr/bin/env python3
"""
PM报告 + 连续15天执行回测测试脚本

功能：
1. 在指定日期调用 TradingAgentsGraph 生成 PM 结论报告
2. 从该日期起连续执行15天回测（使用 ExecutionEngine）
3. 保存所有 PM 报告和每日执行结果

用法：
    .venv/bin/python run_pm_backtest_test.py [TICKER]

默认测试4个日期：2025-01-01, 2025-05-01, 2025-10-01, 2026-04-01
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

import pandas as pd

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("PM_Backtest_Test")

# ── 配置 ─────────────────────────────────────────────────────────

TEST_DATES = [
    "2025-01-01",
    "2025-05-01",
    "2025-10-01",
    "2026-04-01",
]

# 回测参数
INITIAL_CASH = 100_0000  # 100万
COMMISSION_RATE = 0.0003  # 万三
MIN_COMMISSION = 5.0
STAMP_DUTY_RATE = 0.001  # 千一（卖出）
TRANSFER_FEE_RATE = 0.00002  # 万0.2（沪股）

# 输出目录
OUTPUT_DIR = Path("test_results/pm_backtest")

# ── 主函数 ───────────────────────────────────────────────────────


def run_pm_and_backtest(ticker: str, pm_date: str) -> Dict[str, Any]:
    """在指定日期生成PM报告，然后连续执行15天回测。

    Returns:
        {
            "pm_date": str,
            "pm_report": str,           # PM 原始输出
            "pm_signal": str,           # PM 信号
            "trading_rules": List,      # 提取的交易规则
            "daily_results": List[Dict], # 15天每日结果
            "summary": Dict,            # 汇总统计
        }
    """
    logger.info(f"\n{'='*60}")
    logger.info(f"[TEST] PM Date: {pm_date} | Ticker: {ticker}")
    logger.info(f"{'='*60}")

    # ── Step 1: 生成 PM 报告 ──────────────────────────────────
    logger.info(f"[Step 1] Generating PM report for {ticker} @ {pm_date}...")

    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()
    # 使用轻量级配置（仅 market analyst，减少 LLM 调用）
    config["data_cache_dir"] = str(OUTPUT_DIR / "graph_cache")
    config["results_dir"] = str(OUTPUT_DIR / "graph_results")
    config["checkpoint_enabled"] = False

    ta = TradingAgentsGraph(
        selected_analysts=["market"],  # 仅市场分析，减少调用
        debug=False,
        config=config,
    )

    try:
        state_dict, signal = ta.propagate(ticker, pm_date)
        pm_report = state_dict.get("final_trade_decision", "")
        pm_raw = state_dict.get("portfolio_manager_output", "") or pm_report

        # 提取结构化 trading rules
        from backtest.trading_rules import RuleParser
        parser = RuleParser()
        trading_rules = parser._sql_extract(pm_report) if hasattr(parser, '_sql_extract') else []
        if not trading_rules:
            # 回退到旧版解析
            trading_rules = parser.parse(pm_report, None, None, use_llm=False)

        logger.info(f"[PM] Signal: {signal}")
        logger.info(f"[PM] Trading rules extracted: {len(trading_rules)}")
        for r in trading_rules:
            logger.info(f"  - {r.description}")

    except Exception as e:
        logger.error(f"[PM] Failed to generate report: {e}", exc_info=True)
        return {
            "pm_date": pm_date,
            "pm_report": f"ERROR: {e}",
            "pm_signal": "ERROR",
            "trading_rules": [],
            "daily_results": [],
            "summary": {"error": str(e)},
        }

    # ── Step 2: 准备15天回测数据 ──────────────────────────────
    logger.info(f"[Step 2] Preparing 15-day backtest data...")

    # 计算15个交易日范围（考虑周末和节假日）
    start_date = datetime.strptime(pm_date, "%Y-%m-%d")
    # 向后取约20个自然日以确保覆盖15个交易日
    end_date = start_date + timedelta(days=25)

    from tradingagents.dataflows import akshare_data
    import akshare as ak

    # 获取历史数据
    try:
        df = akshare_data._safe_call(
            ak.stock_zh_a_hist,
            symbol=ticker, period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="qfq",
        )
        if df is None or df.empty:
            logger.error(f"[Data] No data available for {ticker} from {pm_date}")
            return {
                "pm_date": pm_date,
                "pm_report": pm_report,
                "pm_signal": signal,
                "trading_rules": [r.to_dict() for r in trading_rules],
                "daily_results": [],
                "summary": {"error": "No price data"},
            }

        # 标准化列名
        col_map = {
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "换手率": "turn", "涨跌幅": "pct_chg",
        }
        df = df.rename(columns=col_map)
        for col in ["open", "close", "high", "low", "volume", "turn", "pct_chg"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # 找到 pm_date 或之后的第一个交易日
        df["date_str"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        pm_dt = datetime.strptime(pm_date, "%Y-%m-%d")

        # 找到起始索引（pm_date 当天或之后的第一个交易日）
        start_idx = None
        for i, row in df.iterrows():
            row_date = pd.to_datetime(row["date"])
            if row_date >= pm_dt:
                start_idx = i
                break

        if start_idx is None:
            logger.error(f"[Data] No trading day on or after {pm_date}")
            return {
                "pm_date": pm_date,
                "pm_report": pm_report,
                "pm_signal": signal,
                "trading_rules": [r.to_dict() for r in trading_rules],
                "daily_results": [],
                "summary": {"error": f"No trading day on or after {pm_date}"},
            }

        # 取15个交易日
        end_idx = min(start_idx + 15, len(df))
        test_df = df.iloc[start_idx:end_idx].copy().reset_index(drop=True)

        logger.info(f"[Data] Test period: {test_df.iloc[0]['date_str']} ~ {test_df.iloc[-1]['date_str']}")
        logger.info(f"[Data] Total trading days: {len(test_df)}")

    except Exception as e:
        logger.error(f"[Data] Failed to load data: {e}", exc_info=True)
        return {
            "pm_date": pm_date,
            "pm_report": pm_report,
            "pm_signal": signal,
            "trading_rules": [r.to_dict() for r in trading_rules],
            "daily_results": [],
            "summary": {"error": f"Data load failed: {e}"},
        }

    # ── Step 3: 执行15天回测 ──────────────────────────────────
    logger.info(f"[Step 3] Running 15-day execution backtest...")

    # 构建 WeeklyDecision（从 PM 报告解析）
    from backtest.models import (
        WeeklyDecision, TradeDirection, PriceCondition,
        PortfolioState, DailyState, TradeRecord, RuleAction,
    )

    # 解析方向
    direction = TradeDirection.HOLD
    if "Buy" in signal or "Overweight" in signal:
        direction = TradeDirection.BUY
    elif "Sell" in signal:
        direction = TradeDirection.SELL

    # 从 trading_rules 中提取价格条件
    stop_loss = 0.0
    take_profit = 0.0
    for rule in trading_rules:
        if rule.action == RuleAction.STOP_LOSS:
            # 尝试从 expression 或 conditions 提取价格
            if rule.expression is not None:
                # 简单提取：找 AtomicExpr with close < value
                try:
                    sql = rule.expression.to_sql()
                    import re
                    m = re.search(r"close\s*<\s*([\d.]+)", sql, re.I)
                    if m:
                        stop_loss = float(m.group(1))
                except Exception:
                    pass
            elif rule.conditions:
                for c in rule.conditions:
                    if c.field == "close" and c.value > 0:
                        stop_loss = c.value
                        break
        elif rule.action == RuleAction.TAKE_PROFIT:
            if rule.expression is not None:
                try:
                    sql = rule.expression.to_sql()
                    import re
                    m = re.search(r"close\s*>\s*([\d.]+)", sql, re.I)
                    if m:
                        take_profit = float(m.group(1))
                except Exception:
                    pass
            elif rule.conditions:
                for c in rule.conditions:
                    if c.field == "close" and c.value > 0:
                        take_profit = c.value
                        break

    decision = WeeklyDecision(
        direction=direction,
        position_pct=0.5 if direction == TradeDirection.BUY else (-1.0 if direction == TradeDirection.HOLD else 0.0),
        price_cond=PriceCondition(stop_loss=stop_loss, take_profit=take_profit),
        trading_rules=trading_rules,
        decision_date=pm_date,
        signal_raw=signal,
        pm_rating=signal,
        pm_raw_output=pm_report,
    )

    # 初始化组合
    portfolio = PortfolioState(cash=INITIAL_CASH)
    daily_results = []
    trade_history = []

    for idx in range(len(test_df)):
        row = test_df.iloc[idx]
        date_str = str(row["date_str"])
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        open_price = float(row["open"])
        volume = float(row.get("volume", 0))
        pct_chg = float(row.get("pct_chg", 0))
        turn = float(row.get("turn", 0))

        # 停牌检测
        if volume <= 0:
            daily_results.append({
                "date": date_str,
                "close": close,
                "action": "SUSPENDED",
                "shares": portfolio.shares,
                "cash": round(portfolio.cash, 2),
                "position_value": round(portfolio.shares * close, 2),
                "total_value": round(portfolio.cash + portfolio.shares * close, 2),
                "notes": "停牌",
            })
            continue

        # 涨跌停检测
        at_limit_up = pct_chg >= 9.9
        at_limit_down = pct_chg <= -9.9

        # 构建 row_dict 供规则评估
        row_dict = {
            "date": date_str,
            "open": open_price,
            "close": close,
            "high": high,
            "low": low,
            "volume": volume,
            "turn": turn,
            "pct_chg": pct_chg,
            "_close": close,
            "_high": high,
            "_low": low,
        }

        # 注入预计算指标（如果有）
        for col in test_df.columns:
            if col not in row_dict and col not in ["date", "date_str"]:
                row_dict[col] = row.get(col)

        # 注入历史数据引用（供 MA/RSI 等函数）
        row_dict["_df"] = test_df
        row_dict["_idx"] = idx

        # ── 检查交易规则 ──────────────────────────────────────
        action = "HOLD"
        action_price = close
        action_shares = 0
        exit_reason = ""
        triggered_rule = None

        has_position = portfolio.shares > 0

        # 优先检查复合交易规则
        if decision.trading_rules:
            for rule in decision.trading_rules:
                if not rule.enabled:
                    continue
                try:
                    if rule.evaluate_all(row_dict):
                        triggered_rule = rule
                        logger.info(f"[RULE] Triggered @ {date_str}: {rule.description}")

                        # 执行规则动作
                        if rule.action == RuleAction.STOP_LOSS:
                            action = "SELL"
                            action_shares = portfolio.shares
                            exit_reason = "stop_loss"
                        elif rule.action == RuleAction.TAKE_PROFIT:
                            action = "SELL"
                            action_shares = portfolio.shares
                            exit_reason = "take_profit"
                        elif rule.action == RuleAction.SELL_HALF:
                            action = "SELL"
                            action_shares = (portfolio.shares // 2 // 100) * 100
                            exit_reason = "reduce_position"
                        elif rule.action == RuleAction.SELL_ALL:
                            action = "SELL"
                            action_shares = portfolio.shares
                            exit_reason = "sell_all"
                        elif rule.action == RuleAction.BUY_ADD:
                            if not at_limit_up:
                                action = "BUY"
                                # 买入50%仓位
                                max_shares = int((portfolio.cash * 0.5) / close // 100) * 100
                                action_shares = max_shares
                        break
                except Exception as e:
                    logger.warning(f"[RULE] Evaluate failed for {rule.name}: {e}")

        # ── 如果规则未触发，走简单方向逻辑 ──────────────────────
        if action == "HOLD":
            if has_position:
                # 持仓中：检查止损/止盈
                if stop_loss > 0 and low <= stop_loss:
                    action = "SELL"
                    action_shares = portfolio.shares
                    action_price = stop_loss
                    exit_reason = "stop_loss_simple"
                elif take_profit > 0 and high >= take_profit:
                    action = "SELL"
                    action_shares = portfolio.shares
                    action_price = take_profit
                    exit_reason = "take_profit_simple"
            else:
                # 空仓中：根据方向决定
                if direction == TradeDirection.BUY and not at_limit_up:
                    action = "BUY"
                    # 买入50%仓位
                    max_shares = int((portfolio.cash * 0.5) / close // 100) * 100
                    action_shares = max_shares

        # ── 执行交易 ──────────────────────────────────────────
        executed = False
        if action == "BUY" and action_shares > 0:
            cost = action_shares * action_price
            commission = max(cost * COMMISSION_RATE, MIN_COMMISSION)
            total_cost = cost + commission

            if total_cost <= portfolio.cash:
                portfolio.cash -= total_cost
                portfolio.shares += action_shares
                executed = True

                trade_history.append({
                    "date": date_str,
                    "action": "BUY",
                    "price": action_price,
                    "shares": action_shares,
                    "cost": round(total_cost, 2),
                    "commission": round(commission, 2),
                })
                logger.info(f"[EXEC] BUY {action_shares} @ {action_price:.2f} "
                           f"cost={total_cost:.2f} @ {date_str}")
            else:
                action = "HOLD"
                action_shares = 0

        elif action == "SELL" and action_shares > 0:
            proceeds = action_shares * action_price
            commission = max(proceeds * COMMISSION_RATE, MIN_COMMISSION)
            stamp_duty = proceeds * STAMP_DUTY_RATE
            transfer_fee = proceeds * TRANSFER_FEE_RATE
            total_cost = commission + stamp_duty + transfer_fee
            net_proceeds = proceeds - total_cost

            portfolio.cash += net_proceeds
            portfolio.shares -= action_shares
            executed = True

            trade_history.append({
                "date": date_str,
                "action": "SELL",
                "price": action_price,
                "shares": action_shares,
                "proceeds": round(net_proceeds, 2),
                "commission": round(commission, 2),
                "stamp_duty": round(stamp_duty, 2),
                "reason": exit_reason,
            })
            logger.info(f"[EXEC] SELL {action_shares} @ {action_price:.2f} "
                       f"net={net_proceeds:.2f} reason={exit_reason} @ {date_str}")

        # ── 记录每日状态 ──────────────────────────────────────
        pos_value = portfolio.shares * close
        total_value = portfolio.cash + pos_value

        daily_results.append({
            "date": date_str,
            "close": close,
            "open": open_price,
            "high": high,
            "low": low,
            "pct_chg": pct_chg,
            "action": action if executed else "HOLD",
            "action_shares": action_shares if executed else 0,
            "action_price": round(action_price, 2) if executed else None,
            "shares": portfolio.shares,
            "cash": round(portfolio.cash, 2),
            "position_value": round(pos_value, 2),
            "total_value": round(total_value, 2),
            "position_pct": round(pos_value / total_value, 4) if total_value > 0 else 0,
            "triggered_rule": triggered_rule.name if triggered_rule else None,
            "exit_reason": exit_reason if executed else None,
        })

    # ── Step 4: 汇总统计 ──────────────────────────────────────
    final_value = daily_results[-1]["total_value"] if daily_results else INITIAL_CASH
    total_return = (final_value - INITIAL_CASH) / INITIAL_CASH

    buy_trades = [t for t in trade_history if t["action"] == "BUY"]
    sell_trades = [t for t in trade_history if t["action"] == "SELL"]

    summary = {
        "pm_date": pm_date,
        "test_period": f"{test_df.iloc[0]['date_str']} ~ {test_df.iloc[-1]['date_str']}",
        "trading_days": len(test_df),
        "initial_cash": INITIAL_CASH,
        "final_value": round(final_value, 2),
        "total_return_pct": round(total_return * 100, 2),
        "total_trades": len(trade_history),
        "buy_trades": len(buy_trades),
        "sell_trades": len(sell_trades),
        "final_shares": portfolio.shares,
        "final_cash": round(portfolio.cash, 2),
        "pm_signal": signal,
        "rules_extracted": len(trading_rules),
    }

    logger.info(f"[Summary] Return: {total_return*100:.2f}% | "
               f"Trades: {len(trade_history)} | Final: ¥{final_value:,.2f}")

    return {
        "pm_date": pm_date,
        "pm_report": pm_report,
        "pm_signal": signal,
        "trading_rules": [r.to_dict() for r in trading_rules],
        "daily_results": daily_results,
        "trade_history": trade_history,
        "summary": summary,
    }


def save_results(ticker: str, all_results: List[Dict]):
    """保存所有结果到文件。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 保存完整结果（JSON）
    output_file = OUTPUT_DIR / f"{ticker}_pm_backtest_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"[Save] Results saved to {output_file}")

    # 保存每日结果 CSV（合并所有周期）
    all_daily = []
    for result in all_results:
        for day in result["daily_results"]:
            day["pm_date"] = result["pm_date"]
            day["pm_signal"] = result["pm_signal"]
            all_daily.append(day)

    if all_daily:
        df = pd.DataFrame(all_daily)
        csv_file = OUTPUT_DIR / f"{ticker}_daily_results.csv"
        df.to_csv(csv_file, index=False, encoding="utf-8-sig")
        logger.info(f"[Save] Daily results CSV: {csv_file}")

    # 保存汇总表
    summaries = [r["summary"] for r in all_results]
    summary_df = pd.DataFrame(summaries)
    summary_csv = OUTPUT_DIR / f"{ticker}_summary.csv"
    summary_df.to_csv(summary_csv, index=False, encoding="utf-8-sig")
    logger.info(f"[Save] Summary CSV: {summary_csv}")

    # 保存 PM 报告（文本）
    pm_dir = OUTPUT_DIR / "pm_reports"
    pm_dir.mkdir(exist_ok=True)
    for result in all_results:
        pm_file = pm_dir / f"{ticker}_PM_{result['pm_date']}.md"
        with open(pm_file, "w", encoding="utf-8") as f:
            f.write(f"# PM Report: {ticker} @ {result['pm_date']}\n\n")
            f.write(f"**Signal:** {result['pm_signal']}\n\n")
            f.write(f"**Trading Rules:** {len(result['trading_rules'])}\n\n")
            for rule in result['trading_rules']:
                f.write(f"- {rule.get('name', 'N/A')}: {rule.get('action', 'N/A')}\n")
            f.write(f"\n---\n\n")
            f.write(result["pm_report"])
        logger.info(f"[Save] PM report: {pm_file}")


def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else "000960"
    logger.info(f"Starting PM + Backtest Test for {ticker}")
    logger.info(f"Test dates: {TEST_DATES}")

    all_results = []

    for pm_date in TEST_DATES:
        result = run_pm_and_backtest(ticker, pm_date)
        all_results.append(result)

    # 保存结果
    save_results(ticker, all_results)

    # 打印最终汇总
    logger.info(f"\n{'='*60}")
    logger.info("FINAL SUMMARY")
    logger.info(f"{'='*60}")
    for r in all_results:
        s = r["summary"]
        logger.info(
            f"{s['pm_date']}: Signal={s['pm_signal']:12s} | "
            f"Return={s['total_return_pct']:7.2f}% | "
            f"Trades={s['total_trades']:2d} | "
            f"Final=¥{s['final_value']:>12,.2f}"
        )

    logger.info(f"\nAll results saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
