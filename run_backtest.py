#!/usr/bin/env python3
"""A 股回测 CLI 入口。

Usage::

    # 使用默认配置回测锡业股份(000960)
    python run_backtest.py

    # 指定参数
    python run_backtest.py --symbol 600519 --start 2024-01-02 --end 2026-05-20 --cash 1000000

    # 仅运行不生成报告（快速验证）
    python run_backtest.py --no-report
"""

import argparse
import json
import logging
import sys

# 加载 .env 文件（必须在其他导入之前）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 确保项目根目录在 sys.path 中
sys.path.insert(0, __file__.rsplit("/", 1)[0] or ".")


def setup_logging(verbose: bool = False):
    """配置日志。"""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "[%(asctime)s] %(levelname)-7s %(name)s: %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("backtest.log", encoding="utf-8"),
        ],
    )


def main():
    parser = argparse.ArgumentParser(
        description="TradingAgents A股回测系统",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # 股票与时间
    parser.add_argument("--symbol", default="000960", help="A 股代码 (6位数字)")
    parser.add_argument("--start", default="2024-01-02", help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", default="2026-05-20", help="结束日期 YYYY-MM-DD")

    # 资金
    parser.add_argument("--cash", type=float, default=1_000_000, help="初始资金 (元)")

    # LLM 配置
    parser.add_argument("--provider", default="deepseek", help="LLM 提供商")
    parser.add_argument("--pro-model", default="deepseek-v4-pro", help="FA 用模型 (高质量)")
    parser.add_argument("--flash-model", default="deepseek-v4-flash", help="决策链用模型 (快速)")

    # 触发条件
    parser.add_argument("--price-threshold", type=float, default=0.10,
                        help="价格波动触发阈值 (如 0.10 = 10%%)")
    parser.add_argument("--stale-days", type=int, default=15,
                        help="决策保底刷新天数")

    # 输出控制
    parser.add_argument("--output-dir", default="backtest_results", help="输出目录")
    parser.add_argument("--no-report", action="store_true", help="跳过 HTML 报告生成")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")

    args = parser.parse_args()

    setup_logging(args.verbose)

    from backtest.models import BacktestConfig
    from backtest.backtest_engine import BacktestEngine
    from backtest.report_engine import ReportEngine

    # 构建配置
    config = BacktestConfig(
        symbol=args.symbol,
        start_date=args.start,
        end_date=args.end,
        initial_cash=args.cash,
        llm_provider=args.provider,
        deep_think_llm=args.pro_model,
        quick_think_llm=args.flash_model,
        price_change_threshold=args.price_threshold,
        decision_stale_days=args.stale_days,
        output_dir=args.output_dir,
    )

    print("=" * 60)
    print(f"🚀 TradingAgents A 股回测系统")
    print(f"   标的: {config.symbol} | 周期: {args.start} ~ {args.end}")
    print(f"   初始资金: ¥{args.cash:,.0f}")
    print(f"   FA 模型: {args.pro_model} | 决策模型: {args.flash_model}")
    print("=" * 60)

    # 运行回测
    engine = BacktestEngine(config)
    try:
        result = engine.run()
    except Exception as e:
        import traceback
        print(f"\n❌ 回测引擎崩溃: {e}")
        traceback.print_exc()
        sys.exit(1)

    if "error" in result:
        print(f"\n❌ 回测失败: {result['error']}")
        sys.exit(1)

    # 打印摘要
    summary = result["summary"]
    print("\n" + "─" * 50)
    print("📊 回测结果摘要")
    print("─" * 50)
    print(f"  总收益率:     {summary['total_return']*100:+.2f}%")
    print(f"  年化收益:     {summary['annual_return']*100:+.2f}%")
    print(f"  最大回撤:     {summary['max_drawdown']*100:.2f}%")
    print(f"  夏普比率:     {summary['sharpe_ratio']:.2f}")
    print(f"  交易胜率:     {summary['win_rate']*100:.1f}%")
    print(f"  交易次数:     {summary['total_trades']}")
    print(f"  盈亏比:       {summary['profit_factor']:.2f}")
    print()
    print(f"  基准收益:     {summary['benchmark_total_return']*100:+.2f}%")
    print(f"  超额收益(α):  {summary['alpha']*100:+.2f}%")
    print(f"  最终资产:     ¥{summary['final_value']:,.2f}")
    print("─" * 50)

    if not args.no_report:
        # 生成报告
        report_path = f"{args.output_dir}/report_{config.symbol}.html"
        report_engine = ReportEngine()
        report_engine.save(result, report_path)
        print(f"\n📄 HTML 报告已保存: {report_path}")

    # ── 量化决策表（L2 执行层直接使用的字段） ──
    decisions = result.get("decisions", [])
    decision_only = [d for d in decisions if d.get("type") == "Decision"]
    fa_records = [d for d in decisions if d.get("type") == "FA"]

    if decision_only:
        print(f"\n{'='*100}")
        print(f"📋 量化决策表 ({len(decision_only)} 条决策 | {len(fa_records)} 次基本面更新)")
        print(f"{'='*100}")
        # 表头
        header = (f"{'日期':^12} {'方向':^6} {'仓位%':^8} {'止损价':^10} "
                  f"{'止盈价':^10} {'买入区间':^18} {'移动止损%':^10} {'触发原因':^16}")
        print(header)
        print("-" * 100)
        for d in decision_only:
            direction = d.get("direction", "-")
            pos_pct = d.get("position_pct", 0)
            sl = d.get("stop_loss", 0)
            tp = d.get("take_profit", 0)
            br_low = d.get("buy_range_low", 0)
            br_high = d.get("buy_range_high", 0)
            trail = d.get("trailing_stop_pct", 0)
            trigger = d.get("trigger", "-")
            buy_range_str = f"{br_low:.2f}~{br_high:.2f}" if br_low or br_high else "-"
            parsed = "✅" if d.get("parsed_ok", True) else "❌"
            row = (f"{d['date']:^12} {direction:^6} {pos_pct:^8.1%} {sl:>10.2f} "
                   f"{tp:>10.2f} {buy_range_str:^18} {trail:^10.1%} {trigger:<16}{parsed}")
            print(row)

        print("-" * 100)
        # 一行摘要统计
        buys = sum(1 for d in decision_only if d.get("direction") == "BUY")
        sells = sum(1 for d in decision_only if d.get("direction") == "SELL")
        holds = sum(1 for d in decision_only if d.get("direction") == "HOLD")
        print(f"  摘要: BUY={buys} | SELL={sells} | HOLD={holds} | FA更新={len(fa_records)}")

        # 保存完整数据（含 _detail）到独立 JSON
        decision_log_path = f"{args.output_dir}/decisions_{config.symbol}.json"
        try:
            with open(decision_log_path, "w", encoding="utf-8") as f:
                json.dump(decisions, f, ensure_ascii=False, indent=2, default=str)
            print(f"\n💾 完整决策数据(含推理链): {decision_log_path}")
        except IOError as e:
            print(f"\n⚠️ 决策日志保存失败: {e}")

    # 打印交易记录
    trades = result.get("trade_history", [])
    if trades:
        print("\n📋 交易记录:")
        print(f"{'#':>3} {'买入日':<12} {'卖出日':<12} {'方向':<6} {'买入价':>8} "
              f"{'卖出价':>8} {'盈亏':>12} {'收益率':>8} {'原因':<16}")
        for i, t in enumerate(trades):
            pnl_sign = "+" if t["pnl"] > 0 else ""
            print(f"{i+1:>3} {t['entry_date']:<12} {(t['exit_date'] or '-'):>12} {t['direction']:<6} "
                  f"{t['entry_price']:>8.2f} {(t['exit_price'] or 0):>8.2f} "
                  f"{pnl_sign}{t['pnl']:>11,.2f} {t['pnl_pct']*100:>7.2f}% "
                  f"{t.get('exit_reason', '-'):<16}")

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
