#!/usr/bin/env python3
"""批量分析 6 只 A 股，验证 RuleParser 输出"""
import sys, os, time, logging, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s %(name)s - %(message)s',
    datefmt='%H:%M:%S'
)

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

# 6 只 A 股目标
STOCKS = [
    ("000423", "东阿阿胶", "2025-12-01"),
    ("000333", "美的集团", "2025-12-01"),
    ("000568", "泸州老窖", "2025-12-01"),
    ("000887", "伊利股份", "2025-12-01"),
    ("601225", "陕西煤业", "2025-12-01"),
    ("601318", "中国平安", "2025-12-01"),
]

config = DEFAULT_CONFIG.copy()
config['llm_provider'] = 'deepseek'
config['deep_think_llm'] = 'deepseek-v4-pro'
config['quick_think_llm'] = 'deepseek-v4-flash'
config['timeout'] = 300  # 5-minute HTTP/invoke timeout to prevent API hangs

print("=" * 80)
print(f"批量分析 {len(STOCKS)} 只 A 股 | 模型: v4-pro(深度)+v4-flash(快速)")
print("=" * 80, flush=True)

ta = TradingAgentsGraph(debug=False, config=config)
results = {}

for i, (ticker, name, date) in enumerate(STOCKS):
    print(f"\n[{i+1}/{len(STOCKS)}] 分析 {ticker} {name} @ {date}", flush=True)
    print("-" * 60, flush=True)
    
    start = time.time()
    try:
        state_dict, decision = ta.propagate(ticker, date)
        elapsed = time.time() - start
        
        pm_output = state_dict.get('final_trade_decision', '') or state_dict.get('pm_raw_output', '')
        pm_len = len(pm_output) if pm_output else 0
        
        print(f"  ✅ 决策: {decision} | 耗时: {elapsed:.0f}s | PM输出: {pm_len} chars", flush=True)
        
        results[ticker] = {
            "name": name,
            "status": "OK",
            "decision": decision,
            "elapsed_s": round(elapsed),
            "pm_output_len": pm_len,
        }
        
        # 保存 PM 原始输出
        if pm_output:
            out_dir = os.path.join("reports", "batch_analysis")
            os.makedirs(out_dir, exist_ok=True)
            out_file = os.path.join(out_dir, f"{ticker}_pm.md")
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(f"# {ticker} {name} - Portfolio Manager Output\n\n")
                f.write(f"**决策**: {decision}\n")
                f.write(f"**耗时**: {elapsed:.0f}s\n\n---\n\n")
                f.write(pm_output)
            print(f"  📄 PM报告: {out_file}", flush=True)
            
    except Exception as e:
        elapsed = time.time() - start
        err_str = str(e)[:200]
        print(f"  ❌ 失败: {err_str} | 耗时: {elapsed:.0f}s", flush=True)
        results[ticker] = {
            "name": name,
            "status": "FAIL",
            "error": err_str,
            "elapsed_s": round(elapsed),
        }

# 汇总
print("\n" + "=" * 80)
print("批量分析汇总")
print("=" * 80)
ok_count = sum(1 for r in results.values() if r["status"] == "OK")
fail_count = sum(1 for r in results.values() if r["status"] == "FAIL")
print(f"成功: {ok_count}/{len(STOCKS)} | 失败: {fail_count}/{len(STOCKS)}", flush=True)

for ticker, name, date in STOCKS:
    r = results.get(ticker, {})
    status_icon = "✅" if r.get("status") == "OK" else "❌"
    decision = r.get("decision", r.get("error", "?"))
    elapsed = r.get("elapsed_s", "?")
    print(f"  {status_icon} {ticker} {name}: {decision} ({elapsed}s)", flush=True)

# 保存汇总 JSON
summary_file = os.path.join("reports", "batch_analysis", "_summary.json")
with open(summary_file, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\n📊 汇总已保存: {summary_file}", flush=True)
