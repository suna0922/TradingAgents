"""回测系统数据结构定义。"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from enum import Enum
from datetime import date

# 复合规则类型（定义在 trading_rules.py 中，这里做 re-export 方便使用）
from backtest.trading_rules import (
    RuleAction,
    TradingRule,
    RuleParser,
)


class TradeDirection(str, Enum):
    """交易方向。"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class PriceCondition:
    """PM 决策中的价格约束。"""
    stop_loss: float = 0.0                    # 止损价（元）
    take_profit: float = 0.0                  # 止盈价（元）
    buy_range: Optional[Tuple[float, float]] = None  # 买入区间 (low, high)
    trailing_stop_pct: float = 0.12           # 移动止损百分比（如 0.12 = 12%）


@dataclass
class TechnicalTriggers:
    """技术指标触发阈值。"""
    atr_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    ma_fast: int = 5
    ma_slow: int = 20
    macd_signal: bool = True
    boll_period: int = 20
    kdj_k: float = 80.0
    kdj_d: float = 20.0
    volume_ratio_threshold: float = 1.5


@dataclass
class FundamentalGuards:
    """基本面红线阈值。"""
    ocf_to_net_profit_min: float = 0.5       # OCF/净利润 ≥ 0.5
    gross_margin_min: float = 20.0            # 毛利率 ≥ 20%
    net_margin_min: float = 5.0               # 净利率 ≥ 5%
    debt_ratio_max: float = 70.0              # 资产负债率 ≤ 70%
    current_ratio_min: float = 1.0            # 流动比率 ≥ 1.0
    cash_coverage_ratio_min: float = 30.0     # 现金覆盖率 ≥ 30%
    interest_coverage_min: float = 1.5        # 利息保障倍数 ≥ 1.5


@dataclass
class MarketSnapshot:
    """决策当天的市场快照——量价、技术面、基本面状态。

    这是做加仓/减仓判断时的完整决策依据。
    每次决策时由 BacktestEngine 从 row 数据 + FA 结果中注入。
    """
    # ── 量价 ──
    close: float = 0.0              # 收盘价
    volume: float = 0.0             # 成交量（手）
    pct_chg: float = 0.0            # 涨跌幅
    turnover: float = 0.0           # 换手率%

    # ── 技术指标 ──
    rsi: float = 0.0                # RSI(14)
    macd: float = 0.0               # MACD 柱
    ma5: float = 0.0                # 5日均线
    ma20: float = 0.0               # 20日均线
    ma50: float = 0.0               # 50日均线
    boll_upper: float = 0.0         # 布林上轨
    boll_lower: float = 0.0         # 布林下轨
    kdj_k: float = 0.0              # KDJ K值
    kdj_d: float = 0.0              # KDJ D值
    atr: float = 0.0                # ATR(14) 波动率
    volume_ratio: float = 0.0       # 量比（当日量/20日均量）

    # ── 基本面（来自最近一次 L0 FA 更新） ──
    fa_period: str = ""             # 最新财报期 (如 "2025Q3")
    fa_signal: str = ""             # FA 评级信号 (Buy/Hold/Sell)


@dataclass
class WeeklyDecision:
    """L1 决策层产出的结构化指令，供 L2 执行层使用。"""
    direction: TradeDirection
    position_pct: float                             # 目标仓位比例 0.0~1.0 (-1表示不改变)
    price_cond: PriceCondition
    technical_triggers: TechnicalTriggers
    fundamental_guards: FundamentalGuards
    decision_date: str                              # YYYY-MM-DD
    signal_raw: str                                 # Buy/Overweight/Hold/Underweight/Sell
    pm_rating: str                                  # PM 原始 Rating
    pm_raw_output: str                              # PM 原始 markdown 输出（审计用）
    parsed_ok: bool = True                          # 解析是否成功
    reasoning_chain: Optional[Dict] = None          # 上游 agent 推理链（审计用）
    market: Optional[MarketSnapshot] = None         # 决策当天市场快照（量价+技术面+基本面）
    trading_rules: List[TradingRule] = field(default_factory=list)  # 复合交易规则（核心新增）
    rules_parsed_ok: bool = False                   # 规则解析是否成功提取到有效规则


@dataclass
class TradeRecord:
    """单笔交易记录。"""
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    direction: str                                  # BUY/SELL
    pnl: float
    pnl_pct: float
    exit_reason: str                                # take_profit / stop_loss / trailing_stop / decision_change


@dataclass
class DailyState:
    """每日组合快照。"""
    date: str
    close: float
    cash: float
    shares: int
    position_value: float                           # 持仓市值 = shares * close
    total_value: float                              # 总资产 = cash + position_value
    position_pct: float                             # 实际持仓比例
    action: str                                     # BUY / SELL / HOLD / NONE
    action_price: float = 0.0
    action_shares: int = 0
    triggered_rules: List[str] = field(default_factory=list)  # 当天触发的规则名列表
    alert_triggered: bool = False                   # 是否触发了 ALERT_ONLY 规则


@dataclass
class PortfolioState:
    """组合状态（被 BacktestEngine 维护）。"""
    cash: float = 1_000_000.0
    shares: int = 0
    current_date: str = ""                          # YYYY-MM-DD
    active_decision: Optional[WeeklyDecision] = None  # 当前生效的决策
    last_decision_executed_date: str = ""           # 最后一次执行决策的日期（防止重复执行）
    state_history: List[DailyState] = field(default_factory=list)
    trade_history: List[TradeRecord] = field(default_factory=list)


@dataclass
class BacktestConfig:
    """回测配置参数。"""
    symbol: str = "000960"                         # 股票代码
    start_date: str = "2024-01-02"                  # 回测开始日
    end_date: str = "2026-05-20"                    # 回测结束日
    initial_cash: float = 1_000_000.0               # 初始资金

    # LLM 配置
    llm_provider: str = "deepseek"
    deep_think_llm: str = "deepseek-v4-pro"         # FA 用（高质量模型）
    quick_think_llm: str = "deepseek-v4-flash"      # 决策链用（快速模型）
    max_debate_rounds: int = 1                      # 辩论轮次（默认1轮=一来一回）
    max_risk_discuss_rounds: int = 1                # 风险讨论轮次（默认1轮=三方一轮）

    # L1 触发条件
    price_change_threshold: float = 0.10            # 价格波动 ≥10% 触发重决策
    decision_stale_days: int = 15                   # 超过 15 天保底刷新决策

    # FA 频率控制
    fa_quarterly: bool = True                       # 仅每季度跑 FA

    # A股交易成本
    slippage_pct: float = 0.001                     # 滑点 0.1%
    commission_rate: float = 0.0003                 # 佣金 万三
    stamp_duty_rate: float = 0.001                  # 印花税 千一（仅卖出）
    transfer_fee_rate: float = 0.00002             # 过户费 万0.2（上海）
    min_commission: float = 5.0                     # 最低佣金 5 元

    # 输出
    output_dir: str = "backtest_results"

    @property
    def is_sh_market(self) -> bool:
        """判断是否为上海市场（用于过户费计算）。"""
        return self.symbol.startswith("6")
