#!/usr/bin/env python3
"""诊断运行脚本：捕获 DeepSeek API 调用详情以定位 400 input length too long 错误"""
import sys, os, time, logging, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 配置日志 - 显示所有 WARNING 以上级别
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s %(name)s - %(message)s',
    datefmt='%H:%M:%S'
)

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config['llm_provider'] = 'deepseek'
config['deep_think_llm'] = 'deepseek-v4-pro'
config['quick_think_llm'] = 'deepseek-v4-flash'

print('=== 启动 000423 分析（带诊断日志） ===', flush=True)
ta = TradingAgentsGraph(debug=False, config=config)

start = time.time()
try:
    state_dict, decision = ta.propagate('000423', '2025-12-01')
    elapsed = time.time() - start
    print(f'\n=== 成功! decision={decision}, 耗时={elapsed:.1f}s ===', flush=True)
    pm = state_dict.get('pm_raw_output', '')
    print(f'PM output 长度: {len(pm)} chars', flush=True)
except Exception as e:
    elapsed = time.time() - start
    print(f'\n=== 失败: {e} ===', flush=True)
    print(f'耗时: {elapsed:.1f}s', flush=True)
    import traceback
    traceback.print_exc()
