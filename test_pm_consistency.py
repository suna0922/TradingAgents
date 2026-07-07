#!/usr/bin/env python3
"""
PM Output Consistency Test — Task #65
=====================================
Run the full TradingAgents pipeline 3× for the same ticker (000960) on the
same date, using a fixed LLM (deepseek-v4), and compare the Portfolio Manager
output across runs to measure rating / thesis drift.

Usage (MUST use .venv Python):
    .venv/bin/python test_pm_consistency.py

Output:
    - Console: side-by-side comparison table
    - File:    reports/consistency_test_000960_<timestamp>.json
"""

import copy
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Force deepseek-v4 BEFORE any tradingagents imports ──────────────
# default_config.py reads env vars at import time via _apply_env_overrides(),
# so these must be set before importing the package.
os.environ["TRADINGAGENTS_LLM_PROVIDER"] = "deepseek"
os.environ["TRADINGAGENTS_DEEP_THINK_LLM"] = "deepseek-v4-pro"
os.environ["TRADINGAGENTS_QUICK_THINK_LLM"] = "deepseek-v4-flash"
os.environ["TRADINGAGENTS_OUTPUT_LANGUAGE"] = "Chinese"

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

# ── Configuration ────────────────────────────────────────────────────
TICKER = "000960"
ANALYSIS_DATE = "2026-05-23"  # Friday, most recent trading day
NUM_RUNS = 3

# Build a fresh config with our overrides applied
test_config = DEFAULT_CONFIG.copy()
print("=" * 70)
print("PM 一致性测试配置")
print(f"  股票代码 : {TICKER}")
print(f"  分析日期 : {ANALYSIS_DATE}")
print(f"  运行次数 : {NUM_RUNS}")
print(f"  LLM 提供商: {test_config['llm_provider']}")
print(f"  深度模型 : {test_config['deep_think_llm']}")
print(f"  快速模型 : {test_config['quick_think_llm']}")
print(f"  输出语言 : {test_config.get('output_language', 'N/A')}")
print("=" * 70)


def extract_pm_fields(final_state: dict) -> dict:
    """Extract the key PM output fields from a pipeline final state."""
    decision_text = final_state.get("final_trade_decision", "")

    return {
        "rating": _extract_field(decision_text, "Rating"),
        "executive_summary": _extract_field(decision_text, "Executive Summary"),
        "investment_thesis": _extract_field(decision_text, "Investment Thesis"),
        "price_target": _extract_field(decision_text, "Price Target"),
        "time_horizon": _extract_field(decision_text, "Time Horizon"),
        "raw_decision": decision_text,
    }


def _extract_field(text: str, field_name: str) -> str:
    """Extract a **Field**: value block from PM markdown output."""
    pattern = rf"\*\*{field_name}\*\*:\s*(.*?)(?=\n\*\*|\Z)"
    import re
    m = re.search(pattern, text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return "(未找到)"


def run_single_pass(config: dict, run_id: int, log_file=None) -> dict:
    """Run one full pipeline pass and extract PM output."""
    import logging

    print(f"\n{'━' * 60}")
    print(f"  第 {run_id}/{NUM_RUNS} 次运行 — 开始")
    print(f"{'━' * 60}")

    # Redirect pipeline stdout/stderr to log file to avoid output truncation
    if log_file:
        orig_stdout = sys.stdout
        orig_stderr = sys.stderr
        fh = open(log_file, "a", encoding="utf-8")
        sys.stdout = fh
        sys.stderr = fh

        # Also suppress LangGraph / httpx noise — only show WARNING+
        logging.basicConfig(level=logging.WARNING, force=True)

    start = time.time()
    try:
        ta = TradingAgentsGraph(debug=False, config=config)  # debug=False to reduce noise
        final_state, signal = ta.propagate(TICKER, ANALYSIS_DATE)
    finally:
        if log_file:
            sys.stdout = orig_stdout
            sys.stderr = orig_stderr
            fh.close()
            logging.basicConfig(level=logging.INFO, force=True)  # restore

    elapsed = time.time() - start

    pm = extract_pm_fields(final_state)
    pm["elapsed_seconds"] = round(elapsed, 1)
    pm["signal"] = signal

    print(f"\n  ✅ 第 {run_id} 次完成 ({elapsed:.1f}s)")
    print(f"     Rating: {pm['rating']}")
    print(f"     Price Target: {pm['price_target']}")
    print(f"     Time Horizon: {pm['time_horizon']}")

    return pm


def main():
    results = []

    # Set up log file for full pipeline output
    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = out_dir / f"consistency_test_{TICKER}_{ts}.log"
    print(f"📝 完整日志: {log_path}")

    for i in range(1, NUM_RUNS + 1):
        pm_data = run_single_pass(test_config, i, log_file=str(log_path))
        results.append(pm_data)

        if i < NUM_RUNS:
            wait = 3
            print(f"\n  ⏳ 等待 {wait}s 后开始下一次运行...")
            time.sleep(wait)

    # ── Comparison Report ──────────────────────────────────────────
    print("\n\n")
    print("╔" + "═" * 78 + "╗")
    print("║" + "  PM 输出一致性测试报告".center(76) + "║")
    print("╠" + "═" * 78 + "╣")

    # Header row
    header = f"{'字段':<22}"
    for i, r in enumerate(results, 1):
        header += f"│ 第{i}次运行".center(18)
    print(f"║{header:^78}║")
    print("╠" + "─" * 22 + "┼" + ("─" * 19 + "┼") * 2 + "─" * 19 + "╣")

    fields_to_compare = [
        ("rating", "评级"),
        ("price_target", "目标价"),
        ("time_horizon", "时间跨度"),
    ]

    all_same_rating = True
    first_rating = results[0]["rating"]

    for key, label in fields_to_compare:
        row = f"{label:<20}"
        values = []
        for r in results:
            val = r.get(key, "(空)")
            values.append(val[:17] if len(str(val)) > 17 else val)
        row += " │ ".join(f"{v:^17}" for v in values)
        print(f"║{row}║")

        if key == "rating":
            for v in values:
                if v != first_rating:
                    all_same_rating = False

    # Verdict
    print("╠" + "═" * 78 + "╣")
    if all_same_rating:
        verdict = f"✅ 一致性通过: {NUM_RUNS}次运行评级均为 [{first_rating}]"
    else:
        ratings = [r["rating"] for r in results]
        verdict = f"⚠️ 一致性警告: {NUM_RUNS}次评级不一致 → {ratings}"

    print(f"║{verdict:^78}║")
    print("╚" + "═" * 78 + "╝")

    # Executive Summary comparison (truncated for readability)
    print("\n📋 Executive Summary 对比:")
    print("-" * 70)
    for i, r in enumerate(results, 1):
        summary = r.get("executive_summary", "(空)")
        if len(summary) > 200:
            summary = summary[:200] + "..."
        print(f"  [第{i}次] {summary}")
        print()

    # Investment Thesis comparison (first 300 chars each)
    print("📋 Investment Thesis 对比 (前300字):")
    print("-" * 70)
    for i, r in enumerate(results, 1):
        thesis = r.get("investment_thesis", "(空)")
        if len(thesis) > 300:
            thesis = thesis[:300] + "..."
        print(f"  [第{i}次] {thesis}")
        print()

    # ── Save raw results to JSON ───────────────────────────────────
    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"consistency_test_{TICKER}_{ts}.json"

    save_data = {
        "config": {
            "ticker": TICKER,
            "analysis_date": ANALYSIS_DATE,
            "llm_provider": test_config["llm_provider"],
            "deep_think_llm": test_config["deep_think_llm"],
            "quick_think_llm": test_config["quick_think_llm"],
            "num_runs": NUM_RUNS,
        },
        "verdict": {
            "all_same_rating": all_same_rating,
            "ratings": [r["rating"] for r in results],
        },
        "runs": [
            {
                "run_id": i + 1,
                "rating": r["rating"],
                "executive_summary": r["executive_summary"],
                "investment_thesis": r["investment_thesis"],
                "price_target": r["price_target"],
                "time_horizon": r["time_horizon"],
                "signal": r.get("signal"),
                "elapsed_seconds": r["elapsed_seconds"],
                "raw_decision": r["raw_decision"],
            }
            for i, r in enumerate(results)
        ],
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

    print(f"\n💾 完整结果已保存至: {out_path}")

    return all_same_rating, results


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⛔ 用户中断测试")
        sys.exit(130)
