#!/usr/bin/env python3
"""验证600519 L1财报分析 8个新增fin指标的趋势数据覆盖情况（使用akshare直连获取数据）"""
import sys, os, warnings
sys.path.insert(0, '/Users/sunrui/WorkBuddy/2026-05-23-task-13')
os.chdir('/Users/sunrui/WorkBuddy/2026-05-23-task-13')
warnings.filterwarnings('ignore')

import akshare as ak
from tradingagents.l1.analyzer_l1_enhanced_complete import L1FinancialAnalyzerEnhanced as L1

# 直接用akshare获取所有数据
print("正在通过akshare直接获取600519数据...")
code_ak = 'SH600519'

profile = {}
try:
    profile['analysis_indicator'] = ak.stock_financial_analysis_indicator(symbol='600519', start_year='2021')
    print(f"  analysis_indicator: {profile['analysis_indicator'].shape}")
except Exception as e:
    print(f"  analysis_indicator 失败: {e}")
    profile['analysis_indicator'] = __import__('pandas').DataFrame()

try:
    profile['balance_sheet'] = ak.stock_balance_sheet_by_report_em(symbol=code_ak)
    print(f"  balance_sheet: {profile['balance_sheet'].shape}")
except Exception as e:
    print(f"  balance_sheet 失败: {e}")
    profile['balance_sheet'] = __import__('pandas').DataFrame()

try:
    profile['profit_sheet'] = ak.stock_profit_sheet_by_report_em(symbol=code_ak)
    print(f"  profit_sheet: {profile['profit_sheet'].shape}")
except Exception as e:
    print(f"  profit_sheet 失败: {e}")
    profile['profit_sheet'] = __import__('pandas').DataFrame()

try:
    profile['cashflow'] = ak.stock_cash_flow_sheet_by_report_em(symbol=code_ak)
    print(f"  cashflow: {profile['cashflow'].shape}")
except Exception as e:
    print(f"  cashflow 失败: {e}")
    profile['cashflow'] = __import__('pandas').DataFrame()

profile['fin_indicator'] = __import__('pandas').DataFrame()  # abstract可能为空

analyzer = L1()
print("\n开始 analyze_dual...")
result = analyzer.analyze_dual('600519.sh', profile=profile)

# ===== 年报部分 =====
annual = result.annual
new_indicators = [
    ('revenue_growth_trend', '营业收入增长率(%)'),
    ('profit_growth_trend', '净利润增长率(%)'),
    ('asset_turnover_trend', '总资产周转率(次)'),
    ('current_ratio_trend', '流动比率'),
    ('quick_ratio_trend', '速动比率'),
    ('ocf_trend', '经营现金流净额(亿元)'),
    ('capex_trend', '资本支出(亿元)'),
    ('fcf_trend', '自由现金流(亿元)'),
]

output = []
output.append("=" * 80)
output.append("【年报】8个新增 analysis_indicator 指标趋势覆盖验证")
output.append("=" * 80)
annual_none_count = 0
annual_total_vals = 0
for attr_name, label in new_indicators:
    vals = getattr(annual, attr_name, None)
    years = getattr(annual, f'{attr_name}_years', None)
    if vals is None:
        output.append(f"  X {label:25s} ({attr_name:30s}) = None (属性不存在)")
        annual_none_count += 1
    elif len(vals) == 0 or all(v is None for v in vals):
        non_none = sum(1 for v in vals if v is not None)
        output.append(f"  ! {label:25s} = 空列表/全None (非空{non_none}个) years={years}")
        annual_none_count += 1
    else:
        non_none = sum(1 for v in vals if v is not None)
        annual_total_vals += non_none
        val_str = ', '.join(f"{v:.2f}" if v is not None else "None" for v in vals[:6])
        year_str = ', '.join(str(y) for y in (years or [])[:6])
        output.append(f"  OK {label:25s} = [{val_str}]  years=[{year_str}]  ({non_none}/{len(vals)})")

# ===== 季报部分 =====
quarter = result.quarter
output.append("")
output.append("=" * 80)
output.append("【季报】8个新增 analysis_indicator 指标趋势覆盖验证")
output.append("=" * 80)
q_none_count = 0
q_total_vals = 0
for attr_name, label in new_indicators:
    vals = getattr(quarter, attr_name, None)
    years = getattr(quarter, f'{attr_name}_years', None)
    if vals is None:
        output.append(f"  X {label:25s} = None (属性不存在)")
        q_none_count += 1
    elif len(vals) == 0 or all(v is None for v in vals):
        non_none = sum(1 for v in vals if v is not None)
        output.append(f"  ! {label:25s} = 空列表/全None (非空{non_none}个)")
        q_none_count += 1
    else:
        non_none = sum(1 for v in vals if v is not None)
        q_total_vals += non_none
        val_str = ', '.join(f"{v:.2f}" if v is not None else "None" for v in vals)
        year_str = ', '.join(str(y) for y in (years or []))
        output.append(f"  OK {label:25s} = [{val_str}]  years=[{year_str}]  ({non_none}/{len(vals)})")

# ===== 原有字段健康检查 =====
output.append("")
output.append("=" * 80)
output.append("原有16+4=20字段年报趋势健康检查")
output.append("=" * 80)
original_attrs = [
    'roe_trend', 'net_profit_margin_parent_trend', 'ocf_to_netprofit_trend',
    'debt_ratio_trend', 'equity_trend', 'interest_debt_trend',
    'cash_coverage_trend', 'cash_ratio_trend', 'production_asset_trend',
    'r_d_expense_trend', 'dividend_ocf_ratio_trend', 'finance_cost_ratio_trend',
    'interest_coverage_trend', 'receivables_to_payables_trend',
    'revenue_trend', 'net_profit_trend'
]
orig_ok = 0
orig_none = 0
for attr in original_attrs:
    vals = getattr(annual, attr, None)
    if vals is None:
        orig_none += 1
        output.append(f"  X {attr} = None")
    elif len(vals) > 0 and any(v is not None for v in vals):
        n = sum(1 for v in vals if v is not None)
        orig_ok += 1
        output.append(f"  OK {attr}: 有效{n}/{len(vals)}")
    else:
        orig_none += 1
        output.append(f"  ! {attr}: 空/全None")

# ===== 汇总 =====
output.append("")
output.append("=" * 80)
output.append("汇总统计")
output.append("=" * 80)
total_annual_ok = orig_ok + (8 - annual_none_count)
output.append(f"  年报原有20字段: 正常={orig_ok}, 异常={orig_none}")
output.append(f"  年报新增8字段:   有数据={8-annual_none_count}, None={annual_none_count}, 总有效值={annual_total_vals}")
output.append(f"  季报新增8字段:   有数据={8-q_none_count}, None={q_none_count}, 总有效值={q_total_vals}")
output.append(f"\n  年报总计: {total_annual_ok}/34 个指标有趋势数据")

result_text = '\n'.join(output)
print(result_text)

# 写入文件供查看
with open('/tmp/verify_8new_result.txt', 'w', encoding='utf-8') as f:
    f.write(result_text)
