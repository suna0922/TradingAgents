#!/usr/bin/env python3
"""批量 Market + Fundamental 分析脚本。

对 6 支标的分别运行 L0 FA + L1 Market 决策链，
保留 PM 原始输出并解析到 decision，生成 Markdown 检查文档。

Usage:
    python run_batch_analysis.py [--force] [--date 2026-05-30]
"""

import argparse
import logging
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest.models import BacktestConfig
from backtest.cache_manager import CacheManager
from backtest.decision_engine import DecisionEngine

# ── 配置 ──────────────────────────────────────────────────────────

SYMBOLS = [
    ("000423", "东阿阿胶"),
    ("000333", "美的集团"),
    ("000568", "泸州老窖"),
    ("000887", "伊利股份"),
    ("601225", "陕西煤业"),
    ("601318", "中国平安"),
]

OUTPUT_DIR = "batch_results"


def setup_logging():
    fmt = "[%(asctime)s] %(levelname)-7s %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def run_one_symbol(
    symbol: str, name: str, date_str: str, force: bool,
    decision_engine: DecisionEngine, cache: CacheManager,
) -> dict:
    """对单支股票运行 FA + Decision 分析。

    Returns:
        dict with keys: symbol, name, fa_ok, fa_report, decision_ok, pm_raw, parsed
    """
    logger = logging.getLogger(f"batch.{symbol}")
    result = {"symbol": symbol, "name": name, "date": date_str}

    # ── Step 1: FA 分析 ──
    logger.info(f"=== {name}({symbol}) L0 FA ===")
    report_period = cache.get_latest_fa_period(date_str)

    if force:
        # 强制重跑：删除缓存
        fa_cache_file = cache.fa_dir / f"{symbol}_{report_period}.json"
        if fa_cache_file.exists():
            fa_cache_file.unlink()
            logger.info(f"  Deleted FA cache: {fa_cache_file}")

    fa_result = decision_engine.run_fundamentals_analysis(symbol, date_str)
    if fa_result:
        result["fa_ok"] = True
        result["fa_report"] = fa_result.get("fundamentals_report", "")
        result["fa_signal"] = fa_result.get("signal", "")
        result["fa_period"] = fa_result.get("report_period", "")
        logger.info(f"  FA done, signal={result['fa_signal']}, report_len={len(result['fa_report'])}")
    else:
        result["fa_ok"] = False
        result["fa_report"] = "[FA分析失败]"
        logger.error(f"  FA failed!")
        return result

    # ── Step 2: Decision 分析 ──
    logger.info(f"=== {name}({symbol}) L1 Decision ===")
    if force:
        # 强制重跑：删除决策缓存
        decision_cache_file = cache.decisions_dir / f"{symbol}_{date_str}.json"
        if decision_cache_file.exists():
            decision_cache_file.unlink()
            logger.info(f"  Deleted decision cache: {decision_cache_file}")

    decision = decision_engine.run_decision_chain(
        symbol=symbol,
        date_str=date_str,
        last_decision_price=0.0,
        current_price=0.0,
        days_since_last_decision=999,
    )

    result["decision_ok"] = decision.parsed_ok
    result["direction"] = decision.direction.value
    result["position_pct"] = decision.position_pct
    result["pm_rating"] = decision.pm_rating
    result["signal_raw"] = decision.signal_raw
    result["pm_raw"] = decision.pm_raw_output or ""
    result["stop_loss"] = decision.price_cond.stop_loss
    result["take_profit"] = decision.price_cond.take_profit
    result["buy_range"] = decision.price_cond.buy_range
    result["parsed_ok"] = decision.parsed_ok

    # 交易规则
    trading_rules = []
    for r in decision.trading_rules:
        trading_rules.append({
            "rule_type": r.rule_type,
            "action": r.action.value if hasattr(r.action, 'value') else str(r.action),
            "trigger_condition": r.trigger_condition,
            "price_threshold": r.price_threshold,
            "technical_reference": r.technical_reference,
            "action_detail": r.action_detail,
            "priority": r.priority,
        })
    result["trading_rules"] = trading_rules
    result["rules_count"] = len(trading_rules)
    result["rules_parsed_ok"] = decision.rules_parsed_ok

    # 推理链
    if decision.reasoning_chain:
        result["reasoning_chain"] = decision.reasoning_chain

    logger.info(f"  Decision: {decision.direction.value} | rating={decision.pm_rating} | rules={len(trading_rules)}")
    return result


def generate_report(results: list, output_path: str) -> str:
    """生成 Markdown 检查文档。"""
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"# 批量 Market + Fundamental 分析报告")
    lines.append(f"")
    lines.append(f"**生成时间**: {now}")
    lines.append(f"**分析日期**: {results[0]['date'] if results else 'N/A'}")
    lines.append(f"**标的数量**: {len(results)}")
    lines.append(f"")
    lines.append("---")
    lines.append("")

    # 汇总表
    lines.append("## 📊 汇总")
    lines.append("")
    lines.append("| # | 标的 | 代码 | FA | Decision | 方向 | 评级 | 止损 | 止盈 | 规则数 |")
    lines.append("|---|------|------|-----|----------|------|------|------|------|--------|")
    for i, r in enumerate(results, 1):
        fa_icon = "✅" if r.get("fa_ok") else "❌"
        dec_icon = "✅" if r.get("parsed_ok") else "❌"
        sl = f"{r.get('stop_loss', 0):.2f}" if r.get('stop_loss') else "-"
        tp = f"{r.get('take_profit', 0):.2f}" if r.get('take_profit') else "-"
        lines.append(
            f"| {i} | {r['name']} | {r['symbol']} | {fa_icon} | {dec_icon} "
            f"| {r.get('direction', '-')} | {r.get('pm_rating', '-')} "
            f"| {sl} | {tp} | {r.get('rules_count', 0)} |"
        )
    lines.append("")

    # 逐标的详细报告
    for i, r in enumerate(results, 1):
        lines.append(f"---")
        lines.append(f"")
        lines.append(f"## {i}. {r['name']} ({r['symbol']})")
        lines.append("")

        # 基本面摘要
        lines.append(f"### L0 基本面分析")
        lines.append(f"")
        if r.get("fa_ok"):
            lines.append(f"- **信号**: {r.get('fa_signal', '-')}")
            lines.append(f"- **报告期**: {r.get('fa_period', '-')}")
            lines.append(f"")
            # FA 报告截取前 3000 字符
            fa_text = r.get("fa_report", "")
            if fa_text:
                lines.append(f"<details>")
                lines.append(f"<summary>FA 报告 (前 3000 字符，点击展开)</summary>")
                lines.append(f"")
                lines.append(f"```")
                lines.append(fa_text[:3000])
                if len(fa_text) > 3000:
                    lines.append(f"... (截断，完整报告 {len(fa_text)} 字符)")
                lines.append(f"```")
                lines.append(f"")
                lines.append(f"</details>")
                lines.append(f"")
        else:
            lines.append(f"❌ **FA 分析失败**")
            lines.append(f"")

        # PM 决策
        lines.append(f"### L1 PM 决策")
        lines.append(f"")
        lines.append(f"| 字段 | 值 |")
        lines.append(f"|------|-----|")
        lines.append(f"| 方向 | {r.get('direction', '-')} |")
        lines.append(f"| 仓位比例 | {r.get('position_pct', '-'):.0%}" if isinstance(r.get('position_pct'), (int, float)) and r.get('position_pct') >= 0 else f"| 仓位比例 | 保持现状 |")
        lines.append(f"| 评级 | {r.get('pm_rating', '-')} |")
        lines.append(f"| 原始信号 | {r.get('signal_raw', '-')} |")
        lines.append(f"| 止损价 | {r.get('stop_loss', 0):.2f}" if r.get('stop_loss') else "| 止损价 | 未设置 |")
        lines.append(f"| 止盈价 | {r.get('take_profit', 0):.2f}" if r.get('take_profit') else "| 止盈价 | 未设置 |")
        br = r.get('buy_range')
        if br:
            lines.append(f"| 买入区间 | {br[0]:.2f} ~ {br[1]:.2f} |")
        else:
            lines.append(f"| 买入区间 | 未设置 |")
        lines.append(f"| 解析成功 | {'✅' if r.get('parsed_ok') else '❌'} |")
        lines.append(f"| 规则解析 | {'✅' if r.get('rules_parsed_ok') else '❌'} |")
        lines.append(f"")

        # 交易规则
        rules = r.get("trading_rules", [])
        if rules:
            lines.append(f"#### 交易规则 ({len(rules)} 条)")
            lines.append(f"")
            lines.append(f"| # | 类型 | 操作 | 触发条件 | 价格阈值 | 技术参考 | 优先级 |")
            lines.append(f"|---|------|------|----------|----------|----------|--------|")
            for j, rule in enumerate(rules, 1):
                rt = rule.get("rule_type", "-")
                action = rule.get("action", "-")
                cond = rule.get("trigger_condition", "-")[:60]
                price = f"{rule.get('price_threshold'):.2f}" if rule.get('price_threshold') else "-"
                tech = rule.get("technical_reference", "-") or "-"
                pri = rule.get("priority", "-")
                lines.append(f"| {j} | {rt} | {action} | {cond} | {price} | {tech} | {pri} |")
            lines.append(f"")
        else:
            lines.append(f"⚠️ **未解析到交易规则**")
            lines.append(f"")

        # PM 原始输出
        pm_raw = r.get("pm_raw", "")
        if pm_raw:
            lines.append(f"#### PM 原始输出")
            lines.append(f"")
            lines.append(f"<details>")
            lines.append(f"<summary>点击展开 PM 完整输出 ({len(pm_raw)} 字符)</summary>")
            lines.append(f"")
            lines.append(f"```")
            lines.append(pm_raw[:5000])
            if len(pm_raw) > 5000:
                lines.append(f"... (截断)")
            lines.append(f"```")
            lines.append(f"")
            lines.append(f"</details>")
            lines.append(f"")

        # 推理链（如有）
        rc = r.get("reasoning_chain")
        if rc:
            lines.append(f"#### 推理链")
            lines.append(f"")
            lines.append(f"<details>")
            lines.append(f"<summary>点击展开推理链</summary>")
            lines.append(f"")
            for k, v in rc.items():
                if isinstance(v, str) and v:
                    lines.append(f"**{k}**:")
                    lines.append(f"```")
                    lines.append(v[:1500])
                    lines.append(f"```")
                    lines.append(f"")
                elif isinstance(v, list):
                    lines.append(f"**{k}**: {v}")
                    lines.append(f"")
            lines.append(f"</details>")
            lines.append(f"")

    # 页脚
    lines.append(f"---")
    lines.append(f"")
    lines.append(f"*报告由 run_batch_analysis.py 自动生成*")

    report = "\n".join(lines)
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
    return report


def main():
    parser = argparse.ArgumentParser(description="批量 Market + Fundamental 分析")
    parser.add_argument("--date", default="2026-05-30", help="分析日期 YYYY-MM-DD")
    parser.add_argument("--force", action="store_true", help="强制重跑（清除缓存）")
    parser.add_argument("--output", default=None, help="输出 Markdown 文件路径")
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("batch")

    output_path = args.output or f"{OUTPUT_DIR}/batch_analysis_{args.date}.md"

    logger.info(f"批量分析启动: {len(SYMBOLS)} 支股票, date={args.date}")
    logger.info(f"输出: {output_path}")

    # 构建配置
    config = BacktestConfig(
        symbol="000423",  # 占位，每个 symbol 单独用
        start_date="2024-01-02",
        end_date="2026-05-30",
        initial_cash=1_000_000,
        llm_provider="deepseek",
        deep_think_llm="deepseek-v4-pro",
        quick_think_llm="deepseek-v4-flash",
        output_dir=OUTPUT_DIR,
    )

    cache = CacheManager(OUTPUT_DIR)
    decision_engine = DecisionEngine(config, cache)

    results = []
    for i, (symbol, name) in enumerate(SYMBOLS, 1):
        logger.info(f"\n{'='*60}")
        logger.info(f"[{i}/{len(SYMBOLS)}] {name} ({symbol})")
        logger.info(f"{'='*60}")
        try:
            r = run_one_symbol(symbol, name, args.date, args.force, decision_engine, cache)
            results.append(r)
        except Exception as e:
            logger.error(f"  CRASH: {e}", exc_info=True)
            results.append({
                "symbol": symbol, "name": name, "date": args.date,
                "fa_ok": False, "decision_ok": False,
                "error": str(e),
            })

    # 生成报告
    logger.info(f"\n生成报告...")
    report = generate_report(results, output_path)
    logger.info(f"报告已保存: {output_path}")
    logger.info(f"报告长度: {len(report)} 字符")

    # 打印快速汇总
    print("\n" + "=" * 60)
    print("📊 快速汇总")
    print("=" * 60)
    for r in results:
        fa = "✅" if r.get("fa_ok") else "❌"
        dec = "✅" if r.get("parsed_ok") else "❌"
        dir_ = r.get("direction", "-")
        rules = r.get("rules_count", 0)
        print(f"  {fa} {dec} {r['name']:<8} {r['symbol']:<8} {dir_:<6} rules={rules}")
    print("=" * 60)
    print(f"\n📄 完整报告: {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
