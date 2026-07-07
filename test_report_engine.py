#!/usr/bin/env python3
"""测试 ReportEngine HTML 生成 — 使用 mock 数据"""
import sys, json
sys.path.insert(0, ".")

from backtest.report_engine import ReportEngine

# 构造模拟回测结果（基于 dryrun 的真实数据）
result = {
    "config": {"symbol": "000960", "start_date": "2024-01-02", "end_date": "2026-05-20", "initial_cash": 1_000_000},
    "summary": {
        "total_return": 0.0950,
        "annual_return": 0.0467,
        "max_drawdown": 0.0759,
        "sharpe_ratio": 0.71,
        "win_rate": 0.25,
        "alpha": -1.5458,
        "benchmark_total_return": 1.6408,
        "total_trades": 36,
        "profit_factor": 2.89,
        "final_value": 1094962,
    },
    # 模拟每日状态（前10天 + 最后1天，用于验证格式）
    "daily_states": [
        {"date": "2024-01-02", "close": 13.80, "cash": 1000000, "shares": 0, 
         "position_value": 0, "total_value": 1000000, "position_pct": 0, "action": "HOLD"},
        {"date": "2024-01-03", "close": 13.75, "cash": 1000000, "shares": 0,
         "position_value": 0, "total_value": 1000000, "position_pct": 0, "action": "HOLD"},
        {"date": "2024-02-21", "close": 12.47, "cash": 399503.28, "shares": 48100,
         "position_value": 599807, "total_value": 999310, "position_pct": 0.60, "action": "BUY", "action_price": 12.47},
        {"date": "2024-03-06", "close": 12.70, "cash": 409098.28, "shares": 0,
         "position_value": 0, "total_value": 409098, "position_pct": 0, "action": "SELL", "action_price": 12.70},
        {"date": "2026-05-20", "close": 16.50, "cash": 418500, "shares": 40900,
         "position_value": 674850, "total_value": 1093350, "position_pct": 0.617, "action": "HOLD"},
    ],
    "benchmark_daily": [
        {"date": "2024-01-02", "total_value": 1000000},
        {"date": "2024-01-03", "total_value": 996377},
        {"date": "2026-05-20", "total_value": 2640775},
    ],
    "trade_history": [
        {"direction": "BUY", "entry_date": "2024-02-21", "exit_date": None, 
         "entry_price": 12.47, "exit_price": None, "shares": 48100, "pnl": 0, "pnl_pct": 0, "exit_reason": ""},
        {"direction": "SELL", "entry_date": "2024-02-21", "exit_date": "2024-03-06",
         "entry_price": 12.47, "exit_price": 12.70, "shares": 48100, "pnl": 9633.0, "pnl_pct": 0.0184, "exit_reason": "trailing_stop"},
        {"direction": "BUY", "entry_date": "2024-03-11", "exit_date": None,
         "entry_price": 12.97, "exit_price": None, "shares": 46600, "pnl": 0, "pnl_pct": 0, "exit_reason": ""},
        {"direction": "SELL", "entry_date": "2024-03-11", "exit_date": "2024-04-15",
         "entry_price": 12.97, "exit_price": 13.96, "shares": 46600, "pnl": 44396.85, "pnl_pct": 0.0759, "exit_reason": "trailing_stop"},
    ],
    "decisions": [
        {"date": "2024-02-20", "type": "L1", "direction": "BUY", "position_pct": "60%",
         "signal_raw": "Buy", "pm_rating": "Buy", "trigger": "price_change > 10%"},
    ],
}

engine = ReportEngine()
html = engine.generate(result)
output_path = "/Users/sunrui/WorkBuddy/2026-05-23-task-13/reports/backtest_report_test.html"

with open(output_path, "w", encoding="utf-8") as f:
    f.write(html)

size = len(html)
print(f"HTML 报告生成成功!")
print(f"  文件大小: {size:,} 字节 ({size/1024:.1f} KB)")
print(f"  路径: {output_path}")
print(f"  包含 Chart.js CDN: {'chart.js' in html}")
print(f"  包含 KPI 卡片: {'kpi-card' in html}")
print(f"  包含净值曲线: {'navChart' in html}")
print(f"  包含回撤曲线: {'ddChart' in html}")
print(f"  包含仓位图:   {'posChart' in html}")
print(f"  包含交易表:   {'交易记录' in html}")
print(f"  包含决策表:   {'L1 决策' in html}")
print(f"  DOCTYPE 声明: {'<!DOCTYPE html>' in html}")
print(f"  </html> 收尾:  {html.rstrip().endswith('</html>')}")
