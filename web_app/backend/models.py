"""Pydantic models for the Stock Roundtable Web API."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────

class Signal(str, Enum):
    BUY = "buy"
    OVERWEIGHT = "overweight"
    HOLD = "hold"
    UNDERWEIGHT = "underweight"
    SELL = "sell"


class AgentRole(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    FUNDAMENTALS = "fundamentals"
    MARKET = "market"
    RISK_AGGRESSIVE = "risk_aggressive"
    RISK_CONSERVATIVE = "risk_conservative"
    RISK_NEUTRAL = "risk_neutral"
    MANAGER = "manager"


class MasterStyle(str, Enum):
    VALUE = "value"
    GROWTH = "growth"
    MOMENTUM = "momentum"
    QUANT = "quant"
    MACRO = "macro"
    CONTRARIAN = "contrarian"


class SessionStatus(str, Enum):
    CREATED = "created"
    FETCHING_DATA = "fetching_data"
    ANALYZING = "analyzing"
    DEBATING = "debating"
    DECIDING = "deciding"
    COMPLETED = "completed"
    ERROR = "error"


# ── Stock Data ────────────────────────────────────────────────────────────────

class StockBasicInfo(BaseModel):
    ticker: str
    name: str
    market: str = "A"  # A / HK / US
    industry: str = ""
    area: str = ""
    list_date: str = ""


class OHLCVPoint(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class TechnicalIndicators(BaseModel):
    ticker: str
    stock_name: str
    analysis_date: str
    latest_price: float
    change_pct: float = 0
    # Moving averages
    sma_5: float = 0
    sma_10: float = 0
    sma_20: float = 0
    sma_60: float = 0
    ema_12: float = 0
    ema_26: float = 0
    # MACD
    macd: float = 0
    macd_signal: float = 0
    macd_hist: float = 0
    # RSI
    rsi_6: float = 0
    rsi_14: float = 0
    rsi_24: float = 0
    # Bollinger
    boll_upper: float = 0
    boll_mid: float = 0
    boll_lower: float = 0
    # Other
    atr_14: float = 0
    vwma_14: float = 0
    kdj_k: float = 0
    kdj_d: float = 0
    kdj_j: float = 0
    # Volume
    volume_ratio: float = 0
    turn_over: float = 0
    # Valuation (moved from fundamentals panel)
    pe_static: float = 0
    pb: float = 0
    peg: float = 0
    market_cap: float = 0
    ps: float = 0
    dividend_yield: float = 0
    # OHLCV history (last 60 days)
    ohlcv_history: list[OHLCVPoint] = Field(default_factory=list)


class FundamentalMetric(BaseModel):
    """A single fundamental metric with value, unit, and optional YoY/QoQ change."""
    name: str
    value: float
    unit: str = ""
    yoy: Optional[float] = None  # year-over-year change %
    qoq: Optional[float] = None  # quarter-over-quarter change %


class FundamentalSection(BaseModel):
    """A section of fundamental data (e.g. profitability, growth, etc.)."""
    title: str
    metrics: list[FundamentalMetric] = Field(default_factory=list)


class FundamentalsData(BaseModel):
    ticker: str
    stock_name: str
    report_date: str
    sections: list[FundamentalSection] = Field(default_factory=list)
    raw_report_md: str = ""  # Full L1 analysis markdown


# ── Master / Seat / Roundtable ───────────────────────────────────────────────

class Master(BaseModel):
    id: str
    name: str
    title: str = ""  # e.g. "价值投资之父"
    avatar_url: str = ""  # emoji or URL
    style: MasterStyle = MasterStyle.VALUE
    methodology: str = ""  # detailed description
    best_for: list[AgentRole] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)  # suggested industry tags


class Seat(BaseModel):
    id: str
    role: AgentRole
    label: str  # Chinese display name
    description: str = ""
    master: Optional[Master] = None
    # 用户自写的投资理论（自由文本）。非空时优先于 master 注入该角色 prompt，
    # 与核心库 custom_theory_config 的 "角色定义 + {自定义理论}" 契约一致。
    custom_theory: str = ""


# Web AgentRole → 核心引擎角色键 的映射。
# 用于把圆桌座位配置转换成 tradingagents 的 master_config / custom_theory_config。
WEB_ROLE_TO_ENGINE_ROLES: dict[AgentRole, list[str]] = {
    AgentRole.BULL:              ["bull_researcher"],
    AgentRole.BEAR:              ["bear_researcher"],
    AgentRole.FUNDAMENTALS:      ["fundamentals_analyst"],
    AgentRole.MARKET:            ["market_analyst"],
    AgentRole.RISK_AGGRESSIVE:   ["aggressive_debator"],
    AgentRole.RISK_CONSERVATIVE: ["conservative_debator"],
    AgentRole.RISK_NEUTRAL:      ["neutral_debator"],
    # 圆桌上的"投资组合经理"座位同时驱动 研究主管 与 PM 两个决策角色
    AgentRole.MANAGER:           ["research_manager", "portfolio_manager"],
}


def seats_to_engine_config(seats: list["Seat"]) -> tuple[dict, dict]:
    """把座位列表转换成 (master_config, custom_theory_config) 两个引擎配置字典。

    规则：custom_theory 非空 → 写入 custom_theory_config（引擎侧优先级更高）；
         同时若拖入了大师 → 写入 master_config（当 custom_theory 为空时生效）。
    """
    master_config: dict[str, str] = {}
    custom_theory_config: dict[str, str] = {}
    for seat in seats:
        # seat_trader 在圆桌上 role 标为 market，但应驱动 trader 引擎角色
        if "trader" in seat.id:
            engine_roles = ["trader"]
        else:
            engine_roles = WEB_ROLE_TO_ENGINE_ROLES.get(seat.role, [])
        for engine_role in engine_roles:
            if seat.master and seat.master.id:
                master_config[engine_role] = seat.master.id
            if seat.custom_theory and seat.custom_theory.strip():
                custom_theory_config[engine_role] = seat.custom_theory.strip()
    return master_config, custom_theory_config


# ── Chat / Session ───────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    id: str
    session_id: str
    role: AgentRole
    master_name: str = ""
    master_avatar: str = ""
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    is_complete: bool = True  # whether the message is still streaming


class ReportType(str, Enum):
    FUNDAMENTALS = "fundamentals"
    TECHNICAL = "technical"
    BULL = "bull"
    BEAR = "bear"
    RISK = "risk"
    TRADING = "trading"
    DECISION = "decision"


class SessionInfo(BaseModel):
    session_id: str
    ticker: str
    stock_name: str = ""
    status: SessionStatus = SessionStatus.CREATED
    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    signal: Optional[Signal] = None
    seats: list[Seat] = Field(default_factory=list)


class CreateSessionRequest(BaseModel):
    ticker: str
    analysis_date: str | None = None  # YYYY-MM-DD, default: today


class CreateSessionResponse(BaseModel):
    session_id: str
    stock_name: str
    status: SessionStatus


# ── Available Masters ────────────────────────────────────────────────────────

def get_default_masters() -> list[Master]:
    """Return the default set of investment masters available for drag-and-drop."""
    return [
        Master(
            id="buffett",
            name="沃伦·巴菲特",
            title="价值投资之父",
            avatar_url="👴",
            style=MasterStyle.VALUE,
            methodology="寻找具有持久竞争优势、优秀管理层和合理估值的优质企业，长期持有。关注ROE、自由现金流、护城河。",
            best_for=[AgentRole.BULL, AgentRole.FUNDAMENTALS, AgentRole.MANAGER],
        ),
        Master(
            id="graham",
            name="本杰明·格雷厄姆",
            title="证券分析之父",
            avatar_url="📚",
            style=MasterStyle.VALUE,
            methodology="寻找市场价格低于内在价值的股票，强调安全边际。关注PE、PB、股息率，偏好低估值蓝筹。",
            best_for=[AgentRole.BEAR, AgentRole.FUNDAMENTALS],
        ),
        Master(
            id="lynch",
            name="彼得·林奇",
            title="成长股猎手",
            avatar_url="🔍",
            style=MasterStyle.GROWTH,
            methodology="投资你了解的公司，寻找PEG<1的成长股。关注盈利增长率、市场份额扩张、行业拐点。",
            best_for=[AgentRole.BULL, AgentRole.MARKET],
        ),
        Master(
            id="soros",
            name="乔治·索罗斯",
            title="宏观对冲大师",
            avatar_url="🌐",
            style=MasterStyle.MACRO,
            methodology="利用反身性理论，识别市场极端情绪和趋势拐点。关注宏观经济、货币政策、地缘政治。",
            best_for=[AgentRole.RISK_AGGRESSIVE, AgentRole.MANAGER],
        ),
        Master(
            id="dalio",
            name="瑞·达利欧",
            title="全天候策略创始人",
            avatar_url="🔄",
            style=MasterStyle.MACRO,
            methodology="理解经济机器运作原理，通过多元化配置穿越周期。关注债务周期、央行政策、生产率增长。",
            best_for=[AgentRole.RISK_CONSERVATIVE, AgentRole.MANAGER],
        ),
        Master(
            id="marks",
            name="霍华德·马克斯",
            title="周期与风险大师",
            avatar_url="📉",
            style=MasterStyle.CONTRARIAN,
            methodology="理解市场周期，在恐惧时贪婪、贪婪时恐惧。关注信用利差、投资者情绪、估值极端。",
            best_for=[AgentRole.BEAR, AgentRole.RISK_CONSERVATIVE],
        ),
        Master(
            id="taleb",
            name="纳西姆·塔勒布",
            title="黑天鹅作者",
            avatar_url="🦢",
            style=MasterStyle.CONTRARIAN,
            methodology="关注尾部风险和脆弱性，做多波动率。寻找被低估的风险，反对过度杠杆和模型崇拜。",
            best_for=[AgentRole.BEAR, AgentRole.RISK_NEUTRAL],
        ),
        Master(
            id="simons",
            name="詹姆斯·西蒙斯",
            title="量化之王",
            avatar_url="🔢",
            style=MasterStyle.QUANT,
            methodology="纯数据驱动，寻找统计套利和短期价格模式。关注价量关系、动量因子、波动率模式。",
            best_for=[AgentRole.MARKET, AgentRole.RISK_AGGRESSIVE],
        ),
        Master(
            id="fisher",
            name="菲利普·费雪",
            title="成长股投资之父",
            avatar_url="🌱",
            style=MasterStyle.GROWTH,
            methodology="寻找具有卓越管理层、强大研发能力和高利润率的成长型公司。重视'闲聊法'调研。",
            best_for=[AgentRole.BULL, AgentRole.FUNDAMENTALS],
        ),
        Master(
            id="burry",
            name="迈克尔·伯里",
            title="大空头原型",
            avatar_url="🔮",
            style=MasterStyle.CONTRARIAN,
            methodology="深入挖掘财务报表中的异常，寻找市场定价错误的重大风险或机会。极度重视数据验证。",
            best_for=[AgentRole.BEAR, AgentRole.FUNDAMENTALS],
        ),
    ]


def get_default_seats() -> list[Seat]:
    """Return the default roundtable seats."""
    return [
        Seat(
            id="seat_bull",
            role=AgentRole.BULL,
            label="看多分析师",
            description="从乐观角度分析，寻找买入理由和增长潜力",
        ),
        Seat(
            id="seat_bear",
            role=AgentRole.BEAR,
            label="看空分析师",
            description="从悲观角度分析，寻找风险点和下跌理由",
        ),
        Seat(
            id="seat_fundamentals",
            role=AgentRole.FUNDAMENTALS,
            label="基本面分析师",
            description="深入分析财务报表、估值、盈利能力",
        ),
        Seat(
            id="seat_market",
            role=AgentRole.MARKET,
            label="技术面分析师",
            description="分析价格走势、技术指标、市场情绪",
        ),
        Seat(
            id="seat_risk",
            role=AgentRole.RISK_NEUTRAL,
            label="风险管理师",
            description="评估投资风险，制定风控策略",
        ),
        Seat(
            id="seat_manager",
            role=AgentRole.MANAGER,
            label="投资组合经理",
            description="综合各方意见，做出最终买入/卖出决策",
        ),
    ]
