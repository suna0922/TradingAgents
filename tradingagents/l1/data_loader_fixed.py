#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复版数据加载器 - 解决AkShare API调用失败问题
"""

import os
import warnings
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

warnings.filterwarnings('ignore')


def _filter_df_by_date(df: pd.DataFrame, cutoff_date: Optional[str]) -> pd.DataFrame:
    """
    过滤东方财富报表 DataFrame 中 cutoff_date 之后的行。
    
    东方财富报表 DataFrame 的日期列名通常包含 '日期' 或 'DATE'，
    值为报告期日期字符串（如 "2025-12-31"）。
    
    Args:
        df: 东方财富报表 DataFrame
        cutoff_date: 截止日期 YYYY-MM-DD，为 None 时不过滤
    Returns:
        过滤后的 DataFrame
    """
    if cutoff_date is None or df is None or df.empty:
        return df
    
    try:
        cutoff = pd.Timestamp(cutoff_date)
        date_cols = [c for c in df.columns if '日期' in str(c) or 'DATE' in str(c).upper()]
        if not date_cols:
            return df  # 没有日期列，不进行过滤
        
        date_col = date_cols[0]
        # 解析日期并过滤
        mask = pd.to_datetime(df[date_col], errors='coerce') <= cutoff
        filtered = df[mask]
        removed = len(df) - len(filtered)
        if removed > 0:
            print(f"    🔒 日期过滤({cutoff_date}): 移除 {removed} 行未来数据")
        return filtered
    except Exception:
        return df  # 过滤失败时不裁剪，保留全部数据

# 代理修复
def _fix_proxy():
    """清除所有代理环境变量"""
    for var in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'all_proxy']:
        os.environ[var] = ''
        os.environ[var.upper()] = ''

_fix_proxy()

# 导入akshare
try:
    import akshare as ak
except ImportError:
    print("❌ 请先安装akshare: pip install akshare")
    raise

def _to_ak_code(code: str) -> str:
    """将股票代码转换为akshare需要的格式"""
    code = code.strip().upper()
    if code.startswith(('SH', 'SZ')):
        return code
    if code.startswith('6'):
        return f'SH{code}'
    elif code.startswith(('0', '3')):
        return f'SZ{code}'
    return code


def get_balance_sheet_safe(code: str) -> pd.DataFrame:
    """
    安全获取资产负债表（带错误处理）
    """
    try:
        symbol = _to_ak_code(code)
        print(f"  正在获取 {symbol} 的资产负债表...")
        
        # 尝试多个函数
        try:
            df = ak.stock_balance_sheet_by_report_em(symbol=symbol)
        except Exception as e1:
            print(f"    ⚠️ 主函数失败: {e1}")
            try:
                df = ak.stock_balance_sheet_by_yearly_em(symbol=symbol)
            except Exception as e2:
                print(f"    ⚠️ 备用函数也失败: {e2}")
                return pd.DataFrame()
        
        if len(df) > 0:
            # 查找日期列
            date_cols = [c for c in df.columns if '日期' in str(c) or 'DATE' in str(c).upper()]
            if date_cols:
                df = df.sort_values(date_cols[0])
            print(f"    ✅ 成功获取 {df.shape[0]} 行 × {df.shape[1]} 列")
            return df
        return pd.DataFrame()
    except Exception as e:
        print(f"  ❌ 获取资产负债表失败: {e}")
        return pd.DataFrame()


def get_profit_sheet_safe(code: str) -> pd.DataFrame:
    """安全获取利润表"""
    try:
        symbol = _to_ak_code(code)
        print(f"  正在获取 {symbol} 的利润表...")
        df = ak.stock_profit_sheet_by_report_em(symbol=symbol)
        
        if len(df) > 0:
            date_cols = [c for c in df.columns if '日期' in str(c) or 'DATE' in str(c).upper()]
            if date_cols:
                df = df.sort_values(date_cols[0])
            print(f"    ✅ 成功获取 {df.shape[0]} 行 × {df.shape[1]} 列")
            return df
        return pd.DataFrame()
    except Exception as e:
        print(f"  ❌ 获取利润表失败: {e}")
        return pd.DataFrame()


def get_cashflow_sheet_safe(code: str) -> pd.DataFrame:
    """安全获取现金流量表"""
    try:
        symbol = _to_ak_code(code)
        print(f"  正在获取 {symbol} 的现金流量表...")
        df = ak.stock_cash_flow_sheet_by_report_em(symbol=symbol)
        
        if len(df) > 0:
            date_cols = [c for c in df.columns if '日期' in str(c) or 'DATE' in str(c).upper()]
            if date_cols:
                df = df.sort_values(date_cols[0])
            print(f"    ✅ 成功获取 {df.shape[0]} 行 × {df.shape[1]} 列")
            return df
        return pd.DataFrame()
    except Exception as e:
        print(f"  ❌ 获取现金流量表失败: {e}")
        return pd.DataFrame()


# 导入配置
try:
    from .l1_config import DataConfig
except ImportError:
    from l1_config import DataConfig


def _est_pub_date(period_ts: pd.Timestamp) -> pd.Timestamp:
    """D2 修复：用保守估计的发布日代替会计期截止日。
    
    - 年报 (12-31): 次年 4/30  - Q1 (03-31): 当年 4/30
    - 中报 (06-30): 当年 8/31  - Q3 (09-30): 当年 10/31
    """
    m = period_ts.month
    y = period_ts.year
    if m == 12:    return pd.Timestamp(year=y + 1, month=4, day=30)
    elif m == 3:   return pd.Timestamp(year=y, month=4, day=30)
    elif m == 6:   return pd.Timestamp(year=y, month=8, day=31)
    elif m == 9:   return pd.Timestamp(year=y, month=10, day=31)
    else:          return period_ts + pd.DateOffset(months=2)


def _filter_abstract_periods(
    df: pd.DataFrame,
    max_annual: int = None,
    max_quarter: int = None,
    cutoff_date: str = None,
    symbol: str = "",
) -> pd.DataFrame:
    """
    裁剪stock_financial_abstract数据，只保留最近N年年报和M个季度报。
    abstract格式：列0='选项', 列1='指标', 其余列为报告期(YYYYMMDD)。
    默认值从 DataConfig 读取（ANNUAL_YEARS=5, QUARTER_COUNT=4）。

    当 cutoff_date 提供时，额外排除该日期之后的报告期列（防止 look-ahead bias）。
    D2 v2: 支持 symbol 参数以使用 baostock 真实 pubDate。
    """
    if df is None or df.empty:
        return df

    # 从配置读取默认值
    if max_annual is None:
        max_annual = getattr(DataConfig, 'ANNUAL_YEARS', 5)
    if max_quarter is None:
        max_quarter = getattr(DataConfig, 'QUARTER_COUNT', 4)

    # 识别报告期列（排除'选项'和'指标'）
    period_cols = [c for c in df.columns if c not in ('选项', '指标')]
    # 按时间降序排列（最新在前）
    period_cols_sorted = sorted(period_cols, key=lambda x: str(x), reverse=True)

    # ---- 按 cutoff_date 排除未来报告期（look-ahead bias 防护）----
    # D2 v2: 真实 pubDate 优先 → 保守估计回退
    if cutoff_date:
        try:
            # 尝试导入真实 pubDate 查询
            try:
                from tradingagents.dataflows.akshare_data import _lookup_pub_date
            except ImportError:
                _lookup_pub_date = None
            cutoff = pd.Timestamp(cutoff_date)
            valid_cols = []
            for c in period_cols_sorted:
                try:
                    ts = pd.Timestamp(str(c))
                    # D2 v2: 真实 pubDate 优先
                    if symbol and _lookup_pub_date:
                        pub = _lookup_pub_date(ts, symbol)
                        if pub is not None:
                            if pub <= cutoff:
                                valid_cols.append(c)
                            continue
                    # 回退：保守估计
                    if _est_pub_date(ts) <= cutoff:
                        valid_cols.append(c)
                except Exception:
                    valid_cols.append(c)  # 无法解析的列保留
            period_cols_sorted = valid_cols
        except Exception:
            pass  # 解析失败时不裁剪
            cutoff = pd.Timestamp(cutoff_date)
            valid_cols = []
            for c in period_cols_sorted:
                try:
                    ts = pd.Timestamp(str(c))
                    # 比较估计发布日而非报告期
                    if _est_pub_date(ts) <= cutoff:
                        valid_cols.append(c)
                except Exception:
                    valid_cols.append(c)  # 无法解析的列保留
            period_cols_sorted = valid_cols
        except Exception:
            pass  # 解析失败时不裁剪

    # 分离年报(1231结尾)和季报
    annual_cols = [c for c in period_cols_sorted if str(c).endswith('1231')][:max_annual]
    quarter_cols = [c for c in period_cols_sorted if not str(c).endswith('1231')][:max_quarter]

    # 合并并按时间降序排列
    keep_cols = ['选项', '指标'] + sorted(annual_cols + quarter_cols, key=lambda x: str(x), reverse=True)

    # 只保留存在的列
    keep_cols = [c for c in keep_cols if c in df.columns]

    result = df[keep_cols].copy()
    print(f"    📊 数据裁剪: 保留最近{len(annual_cols)}年年报 + {len(quarter_cols)}个季度报, "
          f"共{result.shape[1]-2}个报告期列 (原{df.shape[1]-2}个)"
          f"{f', cutoff<={cutoff_date}' if cutoff_date else ''}")
    return result


def get_financial_indicator_safe(code: str, years: int = 3, analysis_date: str = None) -> pd.DataFrame:
    """安全获取财务指标 - 增强版：优先使用stock_financial_abstract，并裁剪数据范围。

    Args:
        code: 股票代码
        years: 用于 fallback API 的起始年份偏移（仅当 abstract 不可用时使用）
        analysis_date: 分析日期 YYYY-MM-DD。提供时，会排除该日期之后的报告期数据。
    """
    print(f"  正在获取 {code} 的财务指标...")

    # 方法1: 尝试stock_financial_abstract (Python 3.8可用)
    try:
        df = ak.stock_financial_abstract(symbol=code)
        if df is not None and len(df) > 0:
            df = _filter_abstract_periods(df, cutoff_date=analysis_date, symbol=code)
            print(f"    ✅ stock_financial_abstract成功: {df.shape[0]}行×{df.shape[1]}列")
            return df
    except Exception as e:
        print(f"    ⚠️ stock_financial_abstract失败: {e}")

    # 方法2: 尝试stock_financial_analysis_indicator
    try:
        if analysis_date:
            ref_year = pd.Timestamp(analysis_date).year
        else:
            ref_year = datetime.now().year
        start_year = str(ref_year - years)
        df = ak.stock_financial_analysis_indicator(symbol=code, start_year=start_year)
        if df is not None and len(df) > 0:
            print(f"    ✅ stock_financial_analysis_indicator成功: {df.shape[0]}行×{df.shape[1]}列")
            return df
    except Exception as e:
        print(f"    ⚠️ stock_financial_analysis_indicator失败: {e}")

    print(f"    ❌ 所有财务指标接口均失败")
    return pd.DataFrame()


def get_financial_analysis_indicator_safe(code: str, years: int = 5, analysis_date: str = None) -> pd.DataFrame:
    """
    安全获取财务分析指标(86列) — 增长率、周转率、股息率等
    数据源: akshare stock_financial_analysis_indicator

    Args:
        code: 股票代码
        years: 回溯年数
        analysis_date: 分析日期 YYYY-MM-DD。提供时，以此日期计算起始年份（而非 datetime.now()）。
    """
    try:
        if analysis_date:
            ref_year = pd.Timestamp(analysis_date).year
        else:
            ref_year = datetime.now().year
        start_year = str(ref_year - years)
        df = ak.stock_financial_analysis_indicator(symbol=code, start_year=start_year)
        if df is not None and len(df) > 0:
            print(f"    ✅ financial_analysis_indicator成功: {df.shape[0]}行×{df.shape[1]}列")
            return df
    except Exception as e:
        print(f"    ⚠️ financial_analysis_indicator失败: {e}")
    return pd.DataFrame()


def get_stock_profile_safe(code: str, analysis_date: str = None) -> Dict[str, pd.DataFrame]:
    """
    安全获取股票完整画像数据
    返回: dict[report_type, DataFrame]

    Args:
        code: 股票代码
        analysis_date: 分析日期 YYYY-MM-DD。提供时会过滤掉该日期之后的报告期数据。
    """
    print(f"\n{'='*60}")
    print(f"获取股票 {code} 的财务数据")
    if analysis_date:
        print(f"分析日期: {analysis_date}")
    print(f"{'='*60}\n")

    profile = {}

    # 获取各类报表（透传 analysis_date）
    profile['fin_indicator'] = get_financial_indicator_safe(code, analysis_date=analysis_date)
    profile['balance_sheet'] = _filter_df_by_date(get_balance_sheet_safe(code), analysis_date)
    profile['profit_sheet'] = _filter_df_by_date(get_profit_sheet_safe(code), analysis_date)
    profile['cashflow'] = _filter_df_by_date(get_cashflow_sheet_safe(code), analysis_date)
    profile['analysis_indicator'] = get_financial_analysis_indicator_safe(code, analysis_date=analysis_date)

    # 统计成功获取的数据
    success_count = sum(1 for v in profile.values() if not v.empty)
    print(f"\n✅ 成功获取 {success_count}/5 类数据")

    return profile


if __name__ == '__main__':
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else '000001'
    profile = get_stock_profile_safe(code)
    
    print("\n数据概览:")
    for key, df in profile.items():
        if not df.empty:
            print(f"  {key}: {df.shape}")
            print(f"    列名: {list(df.columns)[:5]}...")
