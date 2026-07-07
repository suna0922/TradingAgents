#!/usr/bin/env python3
"""诊断脚本：分别检查 annual 和 quarter 两个 result 对象的趋势字段"""
import sys, os, json
sys.path.insert(0, '/Users/sunrui/WorkBuddy/2026-05-23-task-13')
os.chdir('/Users/sunrui/WorkBuddy/2026-05-23-task-13')

from tradingagents.l1.analyzer_l1_enhanced_complete import L1FinancialAnalyzerEnhanced
from tradingagents.l1.data_loader_fixed import get_stock_profile_safe

CODE = '600519'
NAME = '贵州茅台'

print("=" * 70)
print(f"诊断: {CODE} {NAME} — 季报趋势字段排查")
print("=" * 70)

# 加载数据
import pandas as pd
profile = get_stock_profile_safe(CODE)
bal = profile.get('balance_sheet', pd.DataFrame())
prof = profile.get('profit_sheet', pd.DataFrame())
cash = profile.get('cashflow', pd.DataFrame())
fin = profile.get('fin_indicator', pd.DataFrame())

print(f"\n[数据概览]")
print(f"  BS rows: {len(bal)}, PROF rows: {len(prof)}, CASH rows: {len(cash)}, FIN rows: {len(fin)}")

# 创建分析器并运行
analyzer = L1FinancialAnalyzerEnhanced(debug=True)
dual_result = analyzer.analyze_dual(CODE, NAME, profile)

# ===== 分别检查 annual 和 quarter 的趋势字段 =====
annual = dual_result.annual
quarter = dual_result.quarter

print(f"\n{'='*70}")
print(f"ANNUAL result (data_period={getattr(annual, 'data_period', 'N/A')})")
print(f"{'='*70}")

# 所有已知的趋势字段名
all_trend_fields = [
    'roe_trend', 'net_profit_margin_parent_trend', 'ocf_to_netprofit_trend',
    'debt_ratio_trend', 'equity_trend', 'interest_debt_trend',
    'cash_coverage_trend', 'cash_ratio_trend',
    'production_asset_trend', 'r_d_expense_trend', 'dividend_payout_trend',
    'gross_margin_trend', 'inventory_ratio_trend', 'receivables_ratio_trend',
    'goodwill_ratio_trend', 'non_core_asset_ratio_trend', 'other_receivable_ratio_trend',
    'production_asset_roe_trend', 'short_long_ratio_trend', 'payables_ratio_trend',
    'dividend_ocf_ratio_trend', 'finance_cost_ratio_trend',
    'interest_coverage_trend', 'receivables_to_payables_trend',
]

ann_stats = []
for f in all_trend_fields:
    val = getattr(annual, f, None)
    years = getattr(annual, f'{f}_years', None) if not f.endswith('_years') else None
    status = "有数据" if val is not None and isinstance(val, (list, tuple)) and len(val) > 0 else "NULL"
    detail = str(val)[:80] if val is not None else "None"
    ann_stats.append((f, status, detail))
    print(f"  annual.{f:45s} | {status:6s} | {detail}")

print(f"\n{'='*70}")
print(f"QUARTER result (data_period={getattr(quarter, 'data_period', 'N/A')})")
print(f"{'='*70}")

qtr_stats = []
for f in all_trend_fields:
    val = getattr(quarter, f, None)
    status = "有数据" if val is not None and isinstance(val, (list, tuple)) and len(val) > 0 else "NULL"
    detail = str(val)[:80] if val is not None else "None"
    qtr_stats.append((f, status, detail))
    print(f"  quarter.{f:45s} | {status:6s} | {detail}")

# ===== 深入诊断：为什么季报为null？=====
print(f"\n{'='*70}")
print(f"深入诊断：_compute_4quarter_trends 入口条件")
print(f"{'='*70}")

# 模拟 _get_quarter_rows_with_period 的逻辑
def diag_get_quarter_rows(df, label):
    if df is None or df.empty:
        print(f"  {label}: EMPTY or None!")
        return []
    date_col = None
    for c in df.columns:
        if 'REPORT_DATE' in c.upper() or '报告期' in c:
            date_col = c
            break
    if not date_col:
        print(f"  {label}: 未找到REPORT_DATE列! columns={list(df.columns)[:5]}")
        return []
    date_strs = df[date_col].astype(str).str.replace(' 00:00:00', '')
    quarter_mask = ~date_strs.str[:10].str.endswith('12-31')
    quarter_df = df[quarter_mask]
    print(f"  {label}: 总行数={len(df)}, 过滤掉年报后={len(quarter_df)}")
    if len(quarter_df) > 0:
        print(f"    季报日期样例: {list(quarter_df[date_col].astype(str).str[:10].tail(8))}")
    return list(quarter_df.sort_values(by=date_col)[date_col].astype(str).str[:10].values[-4:])

bs_q_dates = diag_get_quarter_rows(bal, "BS(资产负债表)")
prof_q_dates = diag_get_quarter_rows(prof, "PROF(利润表)")
cash_q_dates = diag_get_quarter_rows(cash, "CASH(现金流量表)")

# 检查 _compute_4quarter_trends 是否被调用（通过debug输出推断）
print(f"\n--- 关键判断 ---")
if not bs_q_dates:
    print("  根因：资产负债表无季报数据 → _compute_4quarter_trends 直接return!")
elif len(bs_q_dates) < 2:
    print(f"  警告：只有{len(bs_q_dates)}个季度，趋势计算可能受限")
else:
    print(f"  BS有{len(bs_q_dates)}个季度，应该能进入计算逻辑...")
    print("  需要进一步检查内部是否有其他early return")

# 输出汇总
summary = {
    'code': CODE,
    'name': NAME,
    'annual_trends': {f: {'status': s, 'detail': d} for f, s, d in ann_stats},
    'quarter_trends': {f: {'status': s, 'detail': d} for f, s, d in qtr_stats},
    'diagnosis': {
        'bs_quarter_count': len(bs_q_dates),
        'prof_quarter_count': len(prof_q_dates),
        'cash_quarter_count': len(cash_q_dates),
        'bs_quarter_dates': bs_q_dates,
        'prof_quarter_dates': prof_q_dates,
        'cash_quarter_dates': cash_q_dates,
    }
}

out_path = '/Users/sunrui/WorkBuddy/2026-05-23-task-13/qtr_trend_diagnose.json'
with open(out_path, 'w', encoding='utf-8') as fp:
    json.dump(summary, fp, ensure_ascii=False, indent=2, default=str)
print(f"\n完整诊断结果已写入: {out_path}")
