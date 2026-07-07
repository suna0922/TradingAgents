#!/usr/bin/env python3
"""
PM报告 + 连续15天执行回测测试脚本（模拟模式，不调用LLM）

功能：
1. 使用预定义的模拟PM报告（包含类SQL trading rules）
2. 从指定日期起连续执行15天回测
3. 验证 RuleExpression 解析和 evaluate 全链路
4. 保存所有 PM 报告和每日执行结果

用法：
    .venv/bin/python run_pm_backtest_test_mock.py [TICKER]

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
logger = logging.getLogger("PM_Backtest_Test_Mock")

# ── 配置 ─────────────────────────────────────────────────────────

TEST_DATES = [
    "2025-01-01",
    "2025-05-01",
    "2025-10-01",
    "2026-04-01",
]

# 模拟PM报告模板（每个日期一个，包含类SQL trading rules）
MOCK_PM_REPORTS = {
    "2025-01-01": {
        "signal": "Buy",
        "report": """
# Portfolio Manager Decision

**Rating:** Buy
**Position:** 80%
**Confidence:** High

## Analysis
锡业股份当前处于上升通道，基本面稳健。建议重仓持有。

## Trading Rules
- [RULE1] [stop_loss] WHEN close < 12.00 THEN stop_loss — 无条件清仓 (@ 12.00元)
- [RULE2] [take_profit] WHEN close > 16.00 THEN take_profit — 止盈减仓 (@ 16.00元)
- [RULE3] [reduce_position] WHEN close < 13.00 AND volume > 50000 THEN reduce_position — 减仓50% (@ N/A)
- [RULE4] [observation_anchor] WHEN RSI(14) > 70 THEN alert_only — 超买预警 (@ N/A)
""",
        "rules": [
            {"type": "stop_loss", "sql": "close < 12.00", "action": "stop_loss", "priority": 90},
            {"type": "take_profit", "sql": "close > 16.00", "action": "take_profit", "priority": 85},
            {"type": "reduce_position", "sql": "close < 13.00 AND volume > 50000", "action": "reduce_position", "priority": 75},
            {"type": "observation_anchor", "sql": "RSI(14) > 70", "action": "alert_only", "priority": 60},
        ]
    },
    "2025-05-01": {
        "signal": "Hold",
        "report": """
# Portfolio Manager Decision

**Rating:** Hold
**Position:** 维持现状
**Confidence:** Medium

## Analysis
市场震荡，建议观望。设置止损保护。

## Trading Rules
- [RULE1] [stop_loss] WHEN close < 13.80 THEN stop_loss — 清仓 (@ 13.80元)
- [RULE2] [observation_anchor] WHEN MA(close, 20) < close AND volume_ratio > 1.5 THEN alert_only — 放量突破关注 (@ N/A)
- [RULE3] [entry_zone] WHEN RSI(14) < 30 AND MACD() > 0 THEN add_position — 超跌反弹加仓 (@ N/A)
""",
        "rules": [
            {"type": "stop_loss", "sql": "close < 13.80", "action": "stop_loss", "priority": 90},
            {"type": "observation_anchor", "sql": "MA(close, 20) < close AND volume_ratio > 1.5", "action": "alert_only", "priority": 60},
            {"type": "entry_zone", "sql": "RSI(14) < 30 AND MACD() > 0", "action": "add_position", "priority": 40},
        ]
    },
    "2025-10-01": {
        "signal": "Sell",
        "report": """
# Portfolio Manager Decision

**Rating:** Sell
**Position:** 0%
**Confidence:** High

## Analysis
趋势转弱，建议清仓观望。

## Trading Rules
- [RULE1] [stop_loss] WHEN close < 12.00 THEN stop_loss — 止损清仓 (@ 12.00元)
- [RULE2] [reduce_position] WHEN close < 13.00 OR volume < 10000 THEN reduce_position — 减仓 (@ N/A)
- [RULE3] [observation_anchor] WHEN annual_debt_ratio > 60 THEN alert_only — 负债率预警 (@ N/A)
""",
        "rules": [
            {"type": "stop_loss", "sql": "close < 12.00", "action": "stop_loss", "priority": 90},
            {"type": "reduce_position", "sql": "close < 13.00 OR volume < 10000", "action": "reduce_position", "priority": 75},
            {"type": "observation_anchor", "sql": "annual_debt_ratio > 60", "action": "alert_only", "priority": 60},
        ]
    },
    "2026-04-01": {
        "signal": "Buy",
        "report": """
# Portfolio Manager Decision

**Rating:** Buy
**Position:** 60%
**Confidence:** High

## Analysis
底部确认，建议分批建仓。

## Trading Rules
- [RULE1] [stop_loss] WHEN close < 28.00 THEN stop_loss — 止损 (@ 28.00元)
- [RULE2] [entry_zone] WHEN close < 30.00 AND RSI(14) < 35 THEN add_position — 分批建仓 (@ N/A)
- [RULE3] [take_profit] WHEN close > 40.00 THEN take_profit — 止盈 (@ 40.00元)
- [RULE4] [reduce_position] WHEN BOLL_LOWER(20) > close AND volume_ratio > 2.0 THEN reduce_position — 异常放量减仓 (@ N/A)
""",
        "rules": [
            {"type": "stop_loss", "sql": "close < 28.00", "action": "stop_loss", "priority": 90},
            {"type": "entry_zone", "sql": "close < 30.00 AND RSI(14) < 35", "action": "add_position", "priority": 40},
            {"type": "take_profit", "sql": "close > 40.00", "action": "take_profit", "priority": 85},
            {"type": "reduce_position", "sql": "BOLL_LOWER(20) > close AND volume_ratio > 2.0", "action": "reduce_position", "priority": 75},
        ]
    },
}

# 回测参数
INITIAL_CASH = 100_0000  # 100万
COMMISSION_RATE = 0.0003  # 万三
MIN_COMMISSION = 5.0
STAMP_DUTY_RATE = 0.001  # 千一（卖出）
TRANSFER_FEE_RATE = 0.00002  # 万0.2（沪股）

# 输出目录
OUTPUT_DIR = Path("test_results/pm_backtest_mock")

# ── 主函数 ───────────────────────────────────────────────────────


def run_mock_pm_and_backtest(ticker: str, pm_date: str) -> Dict[str, Any]:
    """使用模拟PM报告，执行15天回测。"""
    logger.info(f"\n{'='*60}")
    logger.info(f"[TEST] PM Date: {pm_date} | Ticker: {ticker}")
    logger.info(f"{'='*60}")

    mock = MOCK_PM_REPORTS.get(pm_date)
    if not mock:
        logger.error(f"[Mock] No mock data for {pm_date}")
        return {"pm_date": pm_date, "error": "No mock data"}

    signal = mock["signal"]
    pm_report = mock["report"]

    # ── Step 1: 解析 Trading Rules ────────────────────────────
    logger.info(f"[Step 1] Parsing trading rules from mock PM report...")

    from backtest.trading_rules import RuleParser
    from backtest.rule_expression import parse_sql

    parser = RuleParser()

    # 尝试新版 SQL 解析
    trading_rules = []
    if hasattr(parser, '_sql_extract'):
        trading_rules = parser._sql_extract(pm_report)

    if not trading_rules:
        # 回退到通用解析
        trading_rules = parser.parse(pm_report, None, None, use_llm=False)

    logger.info(f"[PM] Signal: {signal}")
    logger.info(f"[PM] Trading rules extracted: {len(trading_rules)}")
    for r in trading_rules:
        logger.info(f"  - {r.description}")

    # ── Step 2: 准备15天回测数据 ──────────────────────────────
    logger.info(f"[Step 2] Loading 15-day price data...")

    start_date = datetime.strptime(pm_date, "%Y-%m-%d")
    end_date = start_date + timedelta(days=25)

    from tradingagents.dataflows import akshare_data
    import akshare as ak

    try:
        df = akshare_data._safe_call(
            ak.stock_zh_a_hist,
            symbol=ticker, period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="qfq",
        )
        if df is None or df.empty:
            logger.error(f"[Data] No data for {ticker} from {pm_date}")
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

        df["date_str"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        pm_dt = datetime.strptime(pm_date, "%Y-%m-%d")

        # 找到起始索引
        start_idx = None
        for i, row in df.iterrows():
            if pd.to_datetime(row["date"]) >= pm_dt:
                start_idx = i
                break

        if start_idx is None:
            return {
                "pm_date": pm_date,
                "pm_report": pm_report,
                "pm_signal": signal,
                "trading_rules": [r.to_dict() for r in trading_rules],
                "daily_results": [],
                "summary": {"error": f"No trading day on or after {pm_date}"},
            }

        end_idx = min(start_idx + 15, len(df))
        test_df = df.iloc[start_idx:end_idx].copy().reset_index(drop=True)

        logger.info(f"[Data] Period: {test_df.iloc[0]['date_str']} ~ {test_df.iloc[-1]['date_str']}")
        logger.info(f"[Data] Trading days: {len(test_df)}")

    except Exception as e:
        logger.error(f"[Data] Failed: {e}", exc_info=True)
        return {
            "pm_date": pm_date,
            "pm_report": pm_report,
            "pm_signal": signal,
            "trading_rules": [r.to_dict() for r in trading_rules],
            "daily_results": [],
            "summary": {"error": f"Data load failed: {e}"},
        }

    # ── Step 3: 执行15天回测 ──────────────────────────────────
    logger.info(f"[Step 3] Running 15-day backtest...")

    from backtest.models import (
        WeeklyDecision, TradeDirection, PriceCondition,
        TechnicalTriggers, FundamentalGuards,
        PortfolioState, RuleAction,
    )

    # 解析方向
    direction_map = {
        "Buy": TradeDirection.BUY,
        "Overweight": TradeDirection.BUY,
        "Hold": TradeDirection.HOLD,
        "Underweight": TradeDirection.SELL,
        "Sell": TradeDirection.SELL,
    }
    direction = direction_map.get(signal, TradeDirection.HOLD)

    # 提取价格条件
    stop_loss = 0.0
    take_profit = 0.0
    for rule in trading_rules:
        if rule.action == RuleAction.STOP_LOSS and rule.expression is not None:
            try:
                sql = rule.expression.to_sql()
                import re
                m = re.search(r"close\s*<\s*([\d.]+)", sql, re.I)
                if m:
                    stop_loss = float(m.group(1))
            except Exception:
                pass
        elif rule.action == RuleAction.TAKE_PROFIT and rule.expression is not None:
            try:
                sql = rule.expression.to_sql()
                m = re.search(r"close\s*>\s*([\d.]+)", sql, re.I)
                if m:
                    take_profit = float(m.group(1))
            except Exception:
                pass

    decision = WeeklyDecision(
        direction=direction,
        position_pct=0.5 if direction == TradeDirection.BUY else (-1.0 if direction == TradeDirection.HOLD else 0.0),
        price_cond=PriceCondition(stop_loss=stop_loss, take_profit=take_profit),
        technical_triggers=TechnicalTriggers(),
        fundamental_guards=FundamentalGuards(),
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
                "date": date_str, "close": close, "action": "SUSPENDED",
                "shares": portfolio.shares, "cash": round(portfolio.cash, 2),
                "position_value": round(portfolio.shares * close, 2),
                "total_value": round(portfolio.cash + portfolio.shares * close, 2),
                "notes": "停牌",
            })
            continue

        at_limit_up = pct_chg >= 9.9
        at_limit_down = pct_chg <= -9.9

        # 构建 row_dict
        row_dict = {
            "date": date_str, "open": open_price, "close": close,
            "high": high, "low": low, "volume": volume,
            "turn": turn, "pct_chg": pct_chg,
            "_close": close, "_high": high, "_low": low,
        }
        for col in test_df.columns:
            if col not in row_dict and col not in ["date", "date_str"]:
                row_dict[col] = row.get(col)
        row_dict["_df"] = test_df
        row_dict["_idx"] = idx

        # ── 检查交易规则 ──────────────────────────────────────
        action = "HOLD"
        action_price = close
        action_shares = 0
        exit_reason = ""
        triggered_rule = None
        has_position = portfolio.shares > 0

        if decision.trading_rules:
            for rule in decision.trading_rules:
                if not rule.enabled:
                    continue
                try:
                    if rule.evaluate_all(row_dict):
                        triggered_rule = rule
                        logger.info(f"[RULE] Triggered @ {date_str}: {rule.description}")

                        # 卖出类规则：只在有持仓时执行
                        if rule.action in (RuleAction.STOP_LOSS, RuleAction.TAKE_PROFIT,
                                           RuleAction.SELL_HALF, RuleAction.SELL_ALL,
                                           RuleAction.SELL_PCT):
                            if has_position:
                                action = "SELL"
                                if rule.action == RuleAction.STOP_LOSS:
                                    action_shares = portfolio.shares
                                    exit_reason = "stop_loss"
                                elif rule.action == RuleAction.TAKE_PROFIT:
                                    action_shares = portfolio.shares
                                    exit_reason = "take_profit"
                                elif rule.action == RuleAction.SELL_HALF:
                                    action_shares = (portfolio.shares // 2 // 100) * 100
                                    exit_reason = "reduce_position"
                                elif rule.action == RuleAction.SELL_ALL:
                                    action_shares = portfolio.shares
                                    exit_reason = "sell_all"
                                break
                            else:
                                # 空仓时触发卖出规则 → 忽略（不阻塞买入）
                                logger.debug(f"[RULE] Sell rule triggered but no position, ignoring")
                                continue

                        # 买入类规则
                        elif rule.action == RuleAction.BUY_ADD:
                            if not at_limit_up and not has_position:
                                action = "BUY"
                                max_shares = int((portfolio.cash * 0.5) / close // 100) * 100
                                action_shares = max_shares
                                break
                            else:
                                continue

                        # 观察类规则：只记录不交易
                        elif rule.action == RuleAction.HOLD:
                            logger.info(f"[RULE] Alert-only rule triggered: {rule.name}")
                            continue
                except Exception as e:
                    logger.warning(f"[RULE] Evaluate failed: {e}")

        # ── 规则未触发时的默认逻辑 ────────────────────────────
        if action == "HOLD":
            if has_position:
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
                if direction == TradeDirection.BUY and not at_limit_up:
                    action = "BUY"
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
                    "date": date_str, "action": "BUY", "price": action_price,
                    "shares": action_shares, "cost": round(total_cost, 2),
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
                "date": date_str, "action": "SELL", "price": action_price,
                "shares": action_shares, "proceeds": round(net_proceeds, 2),
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
            "date": date_str, "close": close, "open": open_price,
            "high": high, "low": low, "pct_chg": pct_chg,
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

    # ── 汇总 ──────────────────────────────────────────────────
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
    """保存所有结果。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # JSON
    output_file = OUTPUT_DIR / f"{ticker}_pm_backtest_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"[Save] JSON: {output_file}")

    # 合并每日结果 CSV
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
        logger.info(f"[Save] Daily CSV: {csv_file}")

    # 汇总 CSV
    summaries = [r["summary"] for r in all_results]
    summary_df = pd.DataFrame(summaries)
    summary_csv = OUTPUT_DIR / f"{ticker}_summary.csv"
    summary_df.to_csv(summary_csv, index=False, encoding="utf-8-sig")
    logger.info(f"[Save] Summary CSV: {summary_csv}")

    # PM 报告
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
    logger.info(f"Starting Mock PM + Backtest Test for {ticker}")
    logger.info(f"Test dates: {TEST_DATES}")

    all_results = []
    for pm_date in TEST_DATES:
        result = run_mock_pm_and_backtest(ticker, pm_date)
        all_results.append(result)

    save_results(ticker, all_results)

    # 打印最终汇总
    logger.info(f"\n{'='*60}")
    logger.info("FINAL SUMMARY")
    logger.info(f"{'='*60}")
    for r in all_results:
        s = r["summary"]
        if "error" in s:
            logger.info(f"{s['pm_date']}: ERROR - {s['error']}")
        else:
            logger.info(
                f"{s['pm_date']}: Signal={s['pm_signal']:12s} | "
                f"Return={s['total_return_pct']:7.2f}% | "
                f"Trades={s['total_trades']:2d} | "
                f"Final=¥{s['final_value']:>12,.2f}"
            )

    logger.info(f"\nAll results saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
