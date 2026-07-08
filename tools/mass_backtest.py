#!/usr/bin/env python3
"""50 只股票 × 10 行业 × 行业大师 大规模回测。
支持断点续传（B模式）：每30天自动保存 checkpoint，网络断后自动恢复。
用法: .venv/bin/python tools/mass_backtest.py
"""
import subprocess, json, os, time, sys
from pathlib import Path

BASE = "backtest_results/mass_test"
CONFIG_FILE = "backtest_results/mass_test_stocks.json"
# 30 天保存一次 checkpoint
CHECKPOINT_INTERVAL = 30

MASTER_ENVS = {
    "consumer":       {"BULL":"buffett","BEAR":"graham","AGGRESSIVE":"lynch","CONSERVATIVE":"duan_yongping","NEUTRAL":"munger","PM":"buffett","FUNDAMENTALS_ANALYST":"buffett","MARKET_ANALYST":"lynch"},
    "cyclical":       {"BULL":"ptj","BEAR":"marks","AGGRESSIVE":"druckenmiller","CONSERVATIVE":"klarman","NEUTRAL":"aqr","PM":"ptj","FUNDAMENTALS_ANALYST":"dalio","MARKET_ANALYST":"livermore"},
    "finance":        {"BULL":"buffett","BEAR":"bury","AGGRESSIVE":"soros","CONSERVATIVE":"graham","NEUTRAL":"swensen","PM":"dalio","FUNDAMENTALS_ANALYST":"graham","MARKET_ANALYST":"dalio"},
    "pharma":         {"BULL":"fisher","BEAR":"taleb","AGGRESSIVE":"wood","CONSERVATIVE":"klarman","NEUTRAL":"aqr","PM":"qiu_guolu","FUNDAMENTALS_ANALYST":"fisher","MARKET_ANALYST":"marks"},
    "tech_innovation": {"BULL":"fisher","BEAR":"marks","AGGRESSIVE":"wood","CONSERVATIVE":"klarman","NEUTRAL":"aqr","PM":"lynch","FUNDAMENTALS_ANALYST":"fisher","MARKET_ANALYST":"raschke"},
    "new_energy":     {"BULL":"zhang_lei","BEAR":"bury","AGGRESSIVE":"soros","CONSERVATIVE":"marks","NEUTRAL":"munger","PM":"ptj","FUNDAMENTALS_ANALYST":"lynch","MARKET_ANALYST":"livermore"},
    "manufacturing":  {"BULL":"fisher","BEAR":"graham","AGGRESSIVE":"druckenmiller","CONSERVATIVE":"schloss","NEUTRAL":"munger","PM":"zhang_lei","FUNDAMENTALS_ANALYST":"lynch","MARKET_ANALYST":"ptj"},
    "real_estate":    {"BULL":"graham","BEAR":"bury","AGGRESSIVE":"soros","CONSERVATIVE":"klarman","NEUTRAL":"marks","PM":"marks","FUNDAMENTALS_ANALYST":"graham","MARKET_ANALYST":"marks"},
    "utility":        {"BULL":"buffett","BEAR":"graham","AGGRESSIVE":"lynch","CONSERVATIVE":"schloss","NEUTRAL":"bogle","PM":"swensen","FUNDAMENTALS_ANALYST":"buffett","MARKET_ANALYST":"dalio"},
    "agriculture":    {"BULL":"ptj","BEAR":"bury","AGGRESSIVE":"soros","CONSERVATIVE":"marks","NEUTRAL":"aqr","PM":"ptj","FUNDAMENTALS_ANALYST":"dalio","MARKET_ANALYST":"livermore"},
}
BATCH_SIZE = 6

def is_done(symbol, output_dir):
    """检查股票是否已成功完成回测。"""
    result_file = Path(output_dir) / symbol / symbol / "result_20260101_20260701.json"
    if not result_file.exists():
        return False
    try:
        with open(result_file) as f:
            data = json.load(f)
        summary = data.get("summary", {})
        if "error" in summary and len(summary) == 1:
            return False
        return len(data.get("daily_states", [])) > 0
    except Exception:
        return False

def run_batch(batch, env_map):
    """运行一批股票回测。全部后台启动。"""
    procs = []
    for symbol, industry in batch:
        output_dir = f"{BASE}/{symbol}"
        os.makedirs(f"{output_dir}/l1_analysis", exist_ok=True)

        env = os.environ.copy()
        env.update({f"TRADINGAGENTS_MASTER_{k}": v for k, v in env_map[industry].items()})

        log = open(f"{output_dir}/backtest.log", "w")
        cmd = [
            ".venv/bin/python", "backtest_hybrid.py",
            "--symbol", symbol,
            "--start", "2026-01-01",
            "--end", "2026-07-01",
            "--stale-days", "15",
            "--output-dir", output_dir,
            "--initial-cash", "100000",
        ]
        p = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
        procs.append((symbol, industry, p, log))

        time.sleep(3)  # 错开启动，减少数据源争抢

    return procs

def wait_batch(procs, timeout_per_stock=3600):
    """等待一批完成，返回未完成的列表。"""
    results = []
    for symbol, industry, p, log_handle in procs:
        try:
            p.wait(timeout=timeout_per_stock)
            log_handle.close()
            done = is_done(symbol, f"{BASE}/{symbol}")
            results.append((symbol, industry, True, done))
        except subprocess.TimeoutExpired:
            p.kill()
            log_handle.close()
            p.wait()
            results.append((symbol, industry, False, False))
    return results


def main():
    with open(CONFIG_FILE) as f:
        stock_map = json.load(f)

    # 构建所有股票→行业映射
    all_stocks = []
    for industry, symbols in stock_map.items():
        for s in symbols:
            all_stocks.append((s, industry))

    # 过滤已完成的和正在运行的
    remaining = []
    done_count = 0
    for sym, ind in all_stocks:
        if is_done(sym, f"{BASE}/{sym}"):
            done_count += 1
        else:
            remaining.append((sym, ind))

    print(f"{'='*60}")
    print(f"Mass Backtest: {len(all_stocks)} stocks × 10 industries")
    print(f"  Done: {done_count} | Remaining: {len(remaining)}")
    print(f"  Initial Cash: ¥100,000 | Checkpoint: every {CHECKPOINT_INTERVAL} days")
    print(f"{'='*60}\n")

    if not remaining:
        print("All done!")
        return

    # 分批运行
    total_batches = (len(remaining) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(remaining), BATCH_SIZE):
        batch = remaining[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"\n{'─'*50}")
        print(f"Batch {batch_num}/{total_batches}:")
        for sym, ind in batch:
            print(f"  {sym} ({ind})")
        print(f"{'─'*50}")

        procs = run_batch(batch, MASTER_ENVS)
        print(f"  Running {len(procs)} backtests...")

        results = wait_batch(procs, timeout_per_stock=3600)

        succeeded = sum(1 for _, _, ok, d in results if ok and d)
        failed = sum(1 for _, _, ok, _ in results if not ok)
        corrupt = sum(1 for _, _, ok, d in results if ok and not d)

        print(f"  Batch {batch_num} done: {succeeded} ok, {failed} timeout, {corrupt} corrupt")
        for sym, ind, ok, done in results:
            if not ok:
                print(f"    ⚠ {sym} ({ind}): TIMEOUT")
            elif not done:
                print(f"    ⚠ {sym} ({ind}): result corrupt, needs re-run")

        # 如果还有未完成的，提示可以手动重跑
        if failed + corrupt > 0 and batch_num < total_batches:
            print(f"\n  🛑 {failed + corrupt} stocks need attention. Run again to resume them.")
            print(f"  (checkpoint files will be used for restart)")
            break

    # 最终统计
    final_done = sum(1 for sym, _ in all_stocks if is_done(sym, f"{BASE}/{sym}"))
    print(f"\n{'='*60}")
    print(f"FINAL: {final_done}/{len(all_stocks)} completed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
