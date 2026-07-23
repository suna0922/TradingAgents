"""Agent orchestration service for the roundtable discussion.

Manages the multi-agent debate pipeline — using real LLM calls via the
existing TradingAgents infrastructure.
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

# Add project root
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from web_app.backend.models import (
    AgentRole,
    ChatMessage,
    Master,
    Seat,
    SessionInfo,
    SessionStatus,
    Signal,
    get_default_seats,
)


def _get_llm_client(provider: str = "deepseek", model: str = "deepseek-chat"):
    """Get an LLM client instance."""
    from tradingagents.llm_clients.factory import create_llm_client
    from tradingagents.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = provider
    config["deep_think_llm"] = model
    config["quick_think_llm"] = model

    client = create_llm_client(provider, model, temperature=0)
    return client.get_llm()


def _build_agent_prompt(
    role: AgentRole,
    ticker: str,
    stock_name: str,
    fundamentals_md: str,
    technicals_md: str,
    master: Master | None,
    debate_context: str = "",
) -> str:
    """Build the system + user prompt for a specific agent role."""

    master_context = ""
    if master:
        master_context = f"""
你正在以 **{master.name}（{master.title}）** 的身份和投资理念进行分析。
投资风格: {master.style.value}
核心理念: {master.methodology}
请严格以这位投资大师的思维方式和语言风格来分析这只股票。
"""

    role_instructions = {
        AgentRole.FUNDAMENTALS: """你是一名资深基本面分析师。请分析以下公司的财务数据，评估其盈利能力、成长性、估值水平和财务健康状况。
输出格式：
1. **盈利能力分析**（毛利率、净利率、ROE趋势）
2. **成长性分析**（营收/利润增长率）
3. **估值分析**（PE/PB/PS与行业对比）
4. **财务健康**（资产负债率、现金流状况）
5. **总体评价**（满分10分，给出评分和理由）""",

        AgentRole.MARKET: """你是一名资深技术分析师。请分析以下技术指标和价格数据，评估当前的技术面状况。
输出格式：
1. **趋势分析**（均线排列、MACD信号）
2. **动能分析**（RSI、KDJ状态）
3. **波动率分析**（布林带、ATR）
4. **成交量分析**（量价配合）
5. **总体评价**（满分10分，给出评分和理由）""",

        AgentRole.BULL: """你是一名看多分析师（多头）。你的任务是找出所有支撑这只股票上涨的理由和证据。
请基于基本面和技术面数据，从乐观角度分析：
1. **核心看多理由**（至少3条）
2. **增长催化剂**（有哪些潜在利好）
3. **估值上行空间**（目标估值和价格）
4. **反驳看空观点**（对风险的回应）
5. **看多评级**（强烈看多/看多/谨慎看多）""",

        AgentRole.BEAR: """你是一名看空分析师（空头）。你的任务是找出所有可能导致这只股票下跌的风险和问题。
请基于基本面和技术面数据，从悲观角度分析：
1. **核心看空理由**（至少3条）
2. **潜在风险**（经营风险、估值风险、宏观风险）
3. **估值下行空间**（底部估值和价格）
4. **反驳看多观点**（对乐观预期的质疑）
5. **看空评级**（强烈看空/看空/谨慎看空）""",

        AgentRole.RISK_AGGRESSIVE: """你是一名激进风险管理师。你的任务是评估在预期看多情景下，承担风险能带来的回报。
请分析：
1. **上行风险回报比**（潜在收益 vs 潜在损失）
2. **最优仓位建议**（激进型投资者的合理仓位）
3. **关键止损位**（技术面和基本面止损）
4. **杠杆考量**（是否适合加杠杆）
5. **风险评级**（高/中/低风险）""",

        AgentRole.RISK_CONSERVATIVE: """你是一名保守风险管理师。你的任务是评估最坏情况下的风险，保护本金安全。
请分析：
1. **下行风险量化**（最大可能亏损幅度）
2. **黑天鹅情景**（极端不利事件的影响）
3. **安全边际评估**（当前价格的安全边际）
4. **保守止损策略**（最小化损失的具体方案）
5. **风险评级**（高/中/低风险）""",

        AgentRole.RISK_NEUTRAL: """你是一名中立风险管理师。你的任务是平衡风险与回报，给出客观的风险评估。
请分析：
1. **风险收益平衡点**（在什么条件下风险与回报平衡）
2. **概率加权预期收益**（考虑各种情景的概率和结果）
3. **资金管理建议**（合理的仓位和止盈/止损）
4. **时间维度风险**（短期/中期/长期分别的风险）
5. **最终风险评级**（高/中/低风险）""",

        AgentRole.MANAGER: """你是一名资深投资组合经理。你的任务是综合所有分析师的意见，做出最终投资决策。
请基于以下内容给出决策：
1. **决策摘要**（一句话总结）
2. **多空力量对比**（看多理由 vs 看空理由的权重对比）
3. **关键变量**（决定最终判断的核心因素）
4. **投资建议**（买入/增持/持有/减持/卖出）
5. **仓位建议**（占总资产百分比）
6. **目标价格和止损价格**
7. **主要风险提示**""",
    }

    instructions = role_instructions.get(
        role,
        "请基于提供的数据给出你的专业分析和建议。",
    )

    # Pre-compute to avoid backslash in f-string (Python 3.10 compat)
    debate_line = f"辩论历史:\n{debate_context}" if debate_context else ""

    return f"""{master_context}

{instructions}

{debate_line}

---

## 标的信息
- 股票代码: {ticker}
- 股票名称: {stock_name}

## 基本面数据
{fundamentals_md[:3000]}

## 技术面数据
{technicals_md[:2000]}

请用中文输出你的分析。

**严格规则：你只能使用上面提供的「基本面数据」和「技术面数据」进行分析。禁止引用你训练数据中关于这只股票的任何先验信息（包括但不限于估值倍数、PE/PB、历史表现、行业排名）。如果某项分析所需的数据未提供，直接写"数据不足，无法判断"，不得编造数值。**
"""


async def _call_llm(prompt: str, provider: str = "deepseek", model: str = "deepseek-chat") -> str:
    """Call LLM and return response text."""
    try:
        llm = _get_llm_client(provider, model)
        from langchain_core.messages import HumanMessage

        response = await llm.ainvoke([HumanMessage(content=prompt)])
        return response.content
    except Exception as e:
        return f"[LLM调用失败: {e}]"


class RoundtableSession:
    """Manages a single roundtable analysis session."""

    def __init__(
        self,
        ticker: str,
        stock_name: str,
        seats: list[Seat],
        provider: str = "deepseek",
        model: str = "deepseek-chat",
    ):
        self.session_id = str(uuid.uuid4())[:8]
        self.ticker = ticker
        self.stock_name = stock_name
        self.seats = seats
        self.provider = provider
        self.model = model
        self.status = SessionStatus.CREATED
        self.messages: list[ChatMessage] = []
        self._msg_counter = 0
        self._fundamentals_md = ""
        self._technicals_md = ""

        # Result storage
        self.fundamentals_report = ""
        self.market_report = ""
        self.bull_report = ""
        self.bear_report = ""
        self.risk_report = ""
        self.trading_report = ""
        self.decision_report = ""
        self.signal: Signal | None = None
        self.trading_rules: list = []  # P0: structured rules from PM decision

    def _next_msg_id(self) -> str:
        self._msg_counter += 1
        return f"{self.session_id}-msg-{self._msg_counter}"

    def _add_message(self, role: AgentRole, content: str, master: Master | None = None):
        msg = ChatMessage(
            id=self._next_msg_id(),
            session_id=self.session_id,
            role=role,
            master_name=master.name if master else role.value,
            master_avatar=master.avatar_url if master else "🤖",
            content=content,
            timestamp=datetime.now(),
            is_complete=True,
        )
        self.messages.append(msg)
        return msg

    def set_data(self, fundamentals_md: str, technicals_md: str):
        self._fundamentals_md = fundamentals_md
        self._technicals_md = technicals_md

    async def run(self) -> AsyncGenerator[ChatMessage, None]:
        """Run the full roundtable analysis pipeline, yielding messages as they arrive."""

        self.status = SessionStatus.ANALYZING

        # Helper: get master for a seat
        def _master_for(role: AgentRole) -> Master | None:
            for seat in self.seats:
                if seat.role == role:
                    return seat.master
            return None

        # Phase 1: Analyst Reports (并行)
        self.status = SessionStatus.ANALYZING

        analyst_roles = [AgentRole.FUNDAMENTALS, AgentRole.MARKET]
        analyst_tasks = []
        for role in analyst_roles:
            master = _master_for(role)
            prompt = _build_agent_prompt(
                role, self.ticker, self.stock_name,
                self._fundamentals_md, self._technicals_md, master,
            )
            analyst_tasks.append(_call_llm(prompt, self.provider, self.model))

        # Wait for both analysts
        results = await asyncio.gather(*analyst_tasks, return_exceptions=True)

        for role, result in zip(analyst_roles, results):
            content = str(result) if not isinstance(result, Exception) else f"[错误: {result}]"
            master = _master_for(role)
            msg = self._add_message(role, content, master)
            yield msg

        # Store analyst reports
        self.fundamentals_report = str(results[0]) if not isinstance(results[0], Exception) else ""
        self.market_report = str(results[1]) if not isinstance(results[1], Exception) else ""

        # Include analyst reports as context for debate
        analyst_context = f"""
## 基本面分析师报告:
{self.fundamentals_report[:2000]}

## 技术面分析师报告:
{self.market_report[:2000]}
"""

        # Phase 2: Bull vs Bear Debate
        self.status = SessionStatus.DEBATING

        # Bull first
        bull_master = _master_for(AgentRole.BULL)
        bull_prompt = _build_agent_prompt(
            AgentRole.BULL, self.ticker, self.stock_name,
            self._fundamentals_md, self._technicals_md, bull_master,
            analyst_context,
        )
        bull_content = await _call_llm(bull_prompt, self.provider, self.model)
        bull_msg = self._add_message(AgentRole.BULL, bull_content, bull_master)
        self.bull_report = bull_content
        yield bull_msg

        # Bear (with bull context)
        bear_master = _master_for(AgentRole.BEAR)
        debate_context = analyst_context + f"\n## 看多分析师的观点:\n{bull_content[:2000]}"
        bear_prompt = _build_agent_prompt(
            AgentRole.BEAR, self.ticker, self.stock_name,
            self._fundamentals_md, self._technicals_md, bear_master,
            debate_context,
        )
        bear_content = await _call_llm(bear_prompt, self.provider, self.model)
        bear_msg = self._add_message(AgentRole.BEAR, bear_content, bear_master)
        self.bear_report = bear_content
        yield bear_msg

        # Phase 3: Risk Analysis
        risk_context = analyst_context + f"""
## 多空辩论:

### 看多:
{bull_content[:1500]}

### 看空:
{bear_content[:1500]}
"""

        for risk_role in [AgentRole.RISK_AGGRESSIVE, AgentRole.RISK_CONSERVATIVE, AgentRole.RISK_NEUTRAL]:
            risk_master = _master_for(risk_role)
            prompt = _build_agent_prompt(
                risk_role, self.ticker, self.stock_name,
                self._fundamentals_md, self._technicals_md, risk_master,
                risk_context,
            )
            content = await _call_llm(prompt, self.provider, self.model)
            msg = self._add_message(risk_role, content, risk_master)
            yield msg

        # Build risk report
        risk_messages = [
            m.content for m in self.messages
            if m.role in (
                AgentRole.RISK_AGGRESSIVE,
                AgentRole.RISK_CONSERVATIVE,
                AgentRole.RISK_NEUTRAL,
            )
        ]
        self.risk_report = "\n\n---\n\n".join(risk_messages)

        # Phase 4: Trading analysis (Trader)
        # Use market analyst for trading suggestions
        trading_prompt = _build_agent_prompt(
            AgentRole.MARKET, self.ticker, self.stock_name,
            self._fundamentals_md, self._technicals_md, _master_for(AgentRole.MARKET),
            f"""你现在的角色是交易策略师。请基于以下分析制定具体交易方案：

### 多空分析:
看多: {bull_content[:1000]}
看空: {bear_content[:1000]}

### 风险分析:
{risk_context[:1000]}

请输出：
1. **交易方向**（做多/做空/观望）
2. **进场价格区间**
3. **目标价格**（第一目标、第二目标）
4. **止损价格**
5. **建议仓位比例**
6. **持仓周期**（短线/中线/长线）
7. **关键观察点**（什么情况下需要调整策略）
""",
        )
        trading_content = await _call_llm(trading_prompt, self.provider, self.model)
        trading_msg = self._add_message(AgentRole.MARKET, trading_content, _master_for(AgentRole.MARKET))
        self.trading_report = trading_content
        yield trading_msg

        # Phase 5: Final Decision (Portfolio Manager)
        self.status = SessionStatus.DECIDING

        all_analysis = f"""
## 分析师报告:
{analyst_context}

## 多空辩论:
看多: {bull_content[:1500]}
看空: {bear_content[:1500]}

## 风险分析:
{risk_context[:1500]}

## 交易方案:
{trading_content[:1000]}
"""

        pm_master = _master_for(AgentRole.MANAGER)
        decision_prompt = _build_agent_prompt(
            AgentRole.MANAGER, self.ticker, self.stock_name,
            self._fundamentals_md, self._technicals_md, pm_master,
            all_analysis,
        )
        decision_content = await _call_llm(decision_prompt, self.provider, self.model)
        decision_msg = self._add_message(AgentRole.MANAGER, decision_content, pm_master)
        self.decision_report = decision_content
        yield decision_msg

        # Extract signal
        self.signal = _extract_signal(decision_content)

        # P0: Extract structured trading rules from PM decision
        try:
            from backtest.trading_rules import RuleParser
            parser = RuleParser()
            self.trading_rules = parser.parse(decision_content)
            # Also try extracting from trading_report
            if not self.trading_rules and self.trading_report:
                self.trading_rules = parser.parse(self.trading_report)
        except Exception:
            self.trading_rules = []

        self.status = SessionStatus.COMPLETED

    def to_info(self) -> SessionInfo:
        return SessionInfo(
            session_id=self.session_id,
            ticker=self.ticker,
            stock_name=self.stock_name,
            status=self.status,
            signal=self.signal,
            seats=self.seats,
        )


def _extract_signal(text: str) -> Signal | None:
    """Extract trading signal from the PM's final decision text.
    
    Strategy: find the "投资建议" section first (PM's own conclusion),
    then extract the signal from just that section. Falls back to the
    last portion of text if no explicit section found.
    """
    import re
    
    # 1. Try to find "投资建议" section
    section_patterns = [
        r'(?:#{1,3}\s*)?(?:\d+[.、]\s*)?(?:投资建议|最终建议|决策建议)[：:]\s*(.+?)(?:\n\n|\n(?:#{1,3}|\d+[.、])|$)',
        r'(?:#{1,3}\s*)?(?:\d+[.、]\s*)?投资建议\s*\n(.+?)(?:\n\n|\n(?:#{1,3}|\d+[.、])|$)',
    ]
    
    search_text = text
    for pattern in section_patterns:
        m = re.search(pattern, search_text, re.DOTALL)
        if m:
            search_text = m.group(1)
            break
    
    # 2. If no section found, search the last 600 chars
    if search_text == text and len(text) > 600:
        search_text = text[-600:]
    
    # 3. Match signal — order matters: check stronger signals first
    text_lower = search_text.lower()
    
    # Strong buy: 强烈买入, 强力买入, 强烈推荐买入
    if re.search(r'(?:强烈|强力|strong).*(?:买入|buy)', text_lower):
        return Signal.BUY
    if re.search(r'(?:买入|buy).*(?:强烈|强力|strong)', text_lower):
        return Signal.BUY
    
    # Strong sell: 强烈卖出, 强力卖出, 强烈推荐卖出
    if re.search(r'(?:强烈|强力|strong).*(?:卖出|减持|sell)', text_lower):
        return Signal.SELL
    if re.search(r'(?:卖出|减持|sell).*(?:强烈|强力|strong)', text_lower):
        return Signal.SELL
    
    # Moderate signals
    if re.search(r'(?:买入|推荐买入|buy|增持|overweight)', text_lower):
        return Signal.OVERWEIGHT
    if re.search(r'(?:卖出|推荐卖出|减持|sell|underweight)', text_lower):
        return Signal.UNDERWEIGHT
    
    # Check for explicit "持有" / "观望"
    if re.search(r'(?:持有|hold|观望|neutral)', text_lower):
        return Signal.HOLD
    
    return Signal.HOLD


# Session manager
_sessions: dict[str, RoundtableSession] = {}


def create_session(
    ticker: str,
    stock_name: str,
    seats: list[Seat] | None = None,
) -> RoundtableSession:
    session = RoundtableSession(
        ticker=ticker,
        stock_name=stock_name,
        seats=seats or get_default_seats(),
    )
    _sessions[session.session_id] = session
    return session


def get_session(session_id: str) -> RoundtableSession | None:
    return _sessions.get(session_id)


# ── P1: Session Persistence ───────────────────────────────────────────

_SESSIONS_DIR = _project_root / "sessions"


def _save_session(session: RoundtableSession):
    """Persist session results to JSON file."""
    import os
    os.makedirs(_SESSIONS_DIR / session.session_id, exist_ok=True)

    data = {
        "session_id": session.session_id,
        "ticker": session.ticker,
        "stock_name": session.stock_name,
        "status": session.status.value,
        "created_at": datetime.now().isoformat(),
        "signal": session.signal.value if session.signal else None,
        "trading_rules": [
            {
                "name": r.name,
                "action": r.action.value if hasattr(r.action, 'value') else str(r.action),
                "condition_str": getattr(r, 'condition_str', ''),
                "priority": getattr(r, 'priority', 50),
                "pct": getattr(r, 'pct', 0.0),
            }
            for r in session.trading_rules
        ],
    }
    with open(_SESSIONS_DIR / session.session_id / "result.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Save report texts
    reports = {
        "fundamentals.md": session.fundamentals_report,
        "market.md": session.market_report,
        "bull.md": session.bull_report,
        "bear.md": session.bear_report,
        "risk.md": session.risk_report,
        "trading.md": session.trading_report,
        "final_decision.md": session.decision_report,
    }
    for filename, content in reports.items():
        if content:
            path = _SESSIONS_DIR / session.session_id / filename
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)


def list_sessions() -> list[dict]:
    """List all persisted sessions."""
    import os
    result = []
    if not _SESSIONS_DIR.exists():
        return result
    for entry in sorted(os.listdir(_SESSIONS_DIR), reverse=True):
        result_file = _SESSIONS_DIR / entry / "result.json"
        if result_file.exists():
            try:
                with open(result_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                result.append(data)
            except Exception:
                pass
    return result
