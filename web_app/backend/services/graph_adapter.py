"""将主程序 TradingAgentsGraph.propagate_stream() 包装为 Web SSE 流。

替代原先手工实现的 RoundtableSession.run()，确保 Web 与主程序分析逻辑完全一致。
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from datetime import datetime
from typing import Any, AsyncGenerator, Dict

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

# ── SSE event helpers ──

def _sse_event(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _status_event(message: str, **kwargs) -> str:
    d = {"type": "status", "message": message}
    d.update(kwargs)
    return _sse_event(d)


def _chat_event(msg: dict) -> str:
    return _sse_event({"type": "chat", "message": msg})


def _extract_signal_from_state(decision_text: str) -> str | None:
    d = decision_text.lower()
    for keyword, sig in [
        ("强烈买入", "buy"), ("买入", "buy"), ("增持", "overweight"),
        ("持有", "hold"), ("减持", "underweight"), ("强烈卖出", "sell"), ("卖出", "sell"),
    ]:
        if keyword in d:
            return sig
    return None


# ── State → Chat message extraction ──

def _extract_chat_messages(
    state: dict, prev_state: dict, ticker: str, msg_counter: list
) -> list[dict]:
    """Compare current state with previous, produce chat messages for new content."""
    messages = []
    sid = ticker[:6]

    # Reports that map to chat "roles"
    report_roles = [
        ("fundamentals_report", "fundamentals", "📊"),
        ("market_report", "market", "📈"),
        ("sentiment_report", "sentiment", "😊"),
        ("news_report", "news", "📰"),
    ]
    for key, role, icon in report_roles:
        new_val = state.get(key, "")
        old_val = prev_state.get(key, "")
        if new_val and new_val != old_val and len(new_val) > 10:
            msg_counter[0] += 1
            messages.append({
                "id": f"{sid}-msg-{msg_counter[0]}",
                "session_id": sid,
                "role": role,
                "master_name": {"fundamentals": "基本面分析师", "market": "技术面分析师",
                                "sentiment": "情绪面分析师", "news": "新闻研究员"}.get(role, role),
                "master_avatar": icon,
                "content": new_val,
                "timestamp": datetime.now().isoformat(),
                "is_complete": True,
            })

    # Debate: bull/bear rounds
    debate = state.get("investment_debate_state", {})
    prev_debate = prev_state.get("investment_debate_state", {})
    if isinstance(debate, dict):
        # bull_history
        new_bull = debate.get("bull_history", "")
        old_bull = prev_debate.get("bull_history", "") if isinstance(prev_debate, dict) else ""
        if new_bull and new_bull != old_bull:
            # Only keep the latest addition
            added = new_bull[len(old_bull):].strip() if old_bull and old_bull in new_bull else new_bull
            if added and len(added) > 20:
                msg_counter[0] += 1
                messages.append({
                    "id": f"{sid}-msg-{msg_counter[0]}",
                    "session_id": sid,
                    "role": "bull",
                    "master_name": "看多分析师",
                    "master_avatar": "🐂",
                    "content": added[:3000],
                    "timestamp": datetime.now().isoformat(),
                    "is_complete": True,
                })
        # bear_history
        new_bear = debate.get("bear_history", "")
        old_bear = prev_debate.get("bear_history", "") if isinstance(prev_debate, dict) else ""
        if new_bear and new_bear != old_bear:
            added = new_bear[len(old_bear):].strip() if old_bear and old_bear in new_bear else new_bear
            if added and len(added) > 20:
                msg_counter[0] += 1
                messages.append({
                    "id": f"{sid}-msg-{msg_counter[0]}",
                    "session_id": sid,
                    "role": "bear",
                    "master_name": "看空分析师",
                    "master_avatar": "🐻",
                    "content": added[:3000],
                    "timestamp": datetime.now().isoformat(),
                    "is_complete": True,
                })

    # Risk debate
    risk = state.get("risk_debate_state", {})
    prev_risk = prev_state.get("risk_debate_state", {})
    if isinstance(risk, dict):
        for rkey, role, icon in [
            ("aggressive_history", "risk_aggressive", "🔥"),
            ("conservative_history", "risk_conservative", "🛡️"),
            ("neutral_history", "risk_neutral", "⚖️"),
        ]:
            new_r = risk.get(rkey, "")
            old_r = prev_risk.get(rkey, "") if isinstance(prev_risk, dict) else ""
            if new_r and new_r != old_r:
                added = new_r[len(old_r):].strip() if old_r and old_r in new_r else new_r
                if added and len(added) > 20:
                    msg_counter[0] += 1
                    messages.append({
                        "id": f"{sid}-msg-{msg_counter[0]}",
                        "session_id": sid,
                        "role": role,
                        "master_name": {"risk_aggressive": "激进风控师", "risk_conservative": "保守风控师", "risk_neutral": "中立风控师"}.get(role, role),
                        "master_avatar": icon,
                        "content": added[:3000],
                        "timestamp": datetime.now().isoformat(),
                        "is_complete": True,
                    })

    # Trader plan
    trader = state.get("trader_investment_plan", "")
    prev_trader = prev_state.get("trader_investment_plan", "")
    if trader and trader != prev_trader:
        msg_counter[0] += 1
        messages.append({
            "id": f"{sid}-msg-{msg_counter[0]}",
            "session_id": sid,
            "role": "market",
            "master_name": "交易策略师",
            "master_avatar": "💼",
            "content": trader[:3000],
            "timestamp": datetime.now().isoformat(),
            "is_complete": True,
        })

    # Final decision
    decision = state.get("final_trade_decision", "")
    prev_decision = prev_state.get("final_trade_decision", "")
    if decision and decision != prev_decision:
        msg_counter[0] += 1
        messages.append({
            "id": f"{sid}-msg-{msg_counter[0]}",
            "session_id": sid,
            "role": "manager",
            "master_name": "投资组合经理",
            "master_avatar": "👑",
            "content": decision,
            "timestamp": datetime.now().isoformat(),
            "is_complete": True,
        })

    return messages


def _get_debate_field(state: dict, key: str, risk: bool = False) -> str:
    """Extract debate field from state dict."""
    source = state.get("risk_debate_state", {}) if risk else state.get("investment_debate_state", {})
    if isinstance(source, dict):
        return source.get(key, "")
    return ""


# ── Main streaming function ──

async def stream_graph_analysis(
    ticker: str,
    trade_date: str,
    stock_name: str = "",
    on_state_capture: callable = None,
    master_config: Dict[str, str] | None = None,
    custom_theory_config: Dict[str, str] | None = None,
    data_session=None,
) -> AsyncGenerator[str, None]:
    """Run the main program propagate_stream() in a thread, yield SSE events.

    on_state_capture(final_state, signal, reports_map) is called
    after the graph completes if provided, for session storage.

    data_session: optional DataSession holding pre-fetched OHLCV +
    fundamentals.  When provided, stage 0 skips redundant API calls
    and reads directly from it, guaranteeing panel/LLM data identity.

    master_config / custom_theory_config: per-role theory injection built
    from the roundtable seats (see models.seats_to_engine_config).  These
    feed the core "角色定义 + {自定义理论}" prompt contract — custom theory
    text wins over master methodology for the same role.
    """
    # ── 阶段 0: 读取 DataSession（一次取数，全链路共享）──
    yield _status_event("正在获取股票数据…")

    loop = asyncio.get_event_loop()

    # ★ 优先从 DataSession 读取（与面板共享同一份数据，零 API 调用）
    if data_session and data_session.panel_ready:
        ohlcv = data_session.ohlcv
        fund_struct = data_session.fundamentals_structured
    else:
        # fallback: 直接 API 调用（兼容未预取场景）
        from tradingagents.dataflows.akshare_data import (
            _load_ohlcv_akshare, get_fundamentals, get_fundamentals_structured,
        )
        ohlcv = await loop.run_in_executor(None, _load_ohlcv_akshare, ticker, trade_date)
        if ohlcv is None or ohlcv.empty:
            yield _sse_event({"type": "error", "message": f"无法获取 {ticker} OHLCV 数据"})
            yield _sse_event({"type": "done"})
            return
        await loop.run_in_executor(None, get_fundamentals, ticker, trade_date)
        fund_struct = get_fundamentals_structured(ticker)

    if ohlcv is None or ohlcv.empty:
        yield _sse_event({"type": "error", "message": f"无法获取 {ticker} OHLCV 数据"})
        yield _sse_event({"type": "done"})
        return

    price = float(ohlcv["Close"].iloc[-1])
    prev_close = float(ohlcv["Close"].iloc[-2]) if len(ohlcv) > 1 else price
    change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0
    kdj_k, kdj_d, kdj_j = _calc_kdj(ohlcv)
    boll_u, boll_m, boll_l = _calc_boll(ohlcv)
    macd_dif, macd_dea = _calc_macd(ohlcv["Close"])
    tech_data = {
        "latest_price": price, "change_pct": change_pct, "analysis_date": trade_date,
        "sma_5": _calc_sma(ohlcv, 5), "sma_10": _calc_sma(ohlcv, 10),
        "sma_20": _calc_sma(ohlcv, 20), "sma_60": _calc_sma(ohlcv, 60),
        "macd": macd_dif,
        "macd_signal": macd_dea,
        "rsi_6": _calc_rsi(ohlcv["Close"], 6),
        "rsi_14": _calc_rsi(ohlcv["Close"], 14),
        "boll_upper": boll_u, "boll_mid": boll_m, "boll_lower": boll_l,
        "atr_14": _calc_atr(ohlcv, 14),
        "kdj_k": kdj_k, "kdj_d": kdj_d, "kdj_j": kdj_j,
        "volume_ratio": _calc_volume_ratio(ohlcv), "turn_over": 0,
    }
    yield _sse_event({"type": "data_technicals", "data": tech_data})

    # 基本面数据 — 从 DataSession 读取（与面板 REST 共享同一份数据）
    if fund_struct:
        fund_sections = [{
            "title": "核心财务指标 (来源: 同花顺·DataSession，与LLM分析同源)",
            "metrics": [
                {"name": "净利润", "value": fund_struct.get("net_income"), "unit": "亿"},
                {"name": "净利润同比增长率", "value": fund_struct.get("net_income_growth_yoy"), "unit": "%"},
                {"name": "营业总收入", "value": fund_struct.get("total_revenue"), "unit": "亿"},
                {"name": "营业总收入同比增长率", "value": fund_struct.get("revenue_growth_yoy"), "unit": "%"},
                {"name": "基本每股收益", "value": fund_struct.get("eps"), "unit": ""},
                {"name": "每股净资产", "value": fund_struct.get("book_value_per_share"), "unit": ""},
                {"name": "净资产收益率(ROE)", "value": fund_struct.get("roe"), "unit": "%"},
                {"name": "销售毛利率", "value": fund_struct.get("gross_margin"), "unit": "%"},
                {"name": "销售净利率", "value": fund_struct.get("net_margin"), "unit": "%"},
                {"name": "资产负债率", "value": fund_struct.get("debt_to_asset_ratio"), "unit": "%"},
                {"name": "市盈率(静态)", "value": fund_struct.get("pe_static"), "unit": "倍"},
                {"name": "市净率(PB)", "value": fund_struct.get("pb"), "unit": "倍"},
                {"name": "PEG", "value": fund_struct.get("peg"), "unit": ""},
                {"name": "总市值", "value": fund_struct.get("market_cap_亿"), "unit": "亿"},
                {"name": "市销率(PS)", "value": fund_struct.get("ps"), "unit": "倍"},
                {"name": "股息率", "value": fund_struct.get("dividend_yield"), "unit": "%"},
            ],
        }]
        fund_sections = [s for s in fund_sections if any(m["value"] is not None for m in s["metrics"])]
        yield _sse_event({"type": "data_fundamentals", "data": {"sections": fund_sections, "stock_name": stock_name or ticker}})
    else:
        yield _sse_event({"type": "data_fundamentals", "data": {"sections": [], "stock_name": stock_name or ticker}})

    # ── 阶段 1-5: 运行主程序图 ──
    yield _status_event("分析师正在讨论…")
    # Use a thread-safe queue to bridge sync→async
    q: queue.Queue = queue.Queue()
    msg_counter = [0]
    prev_state = {}
    graph_done = threading.Event()

    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "deepseek"
    config["deep_think_llm"] = "deepseek-chat"
    config["quick_think_llm"] = "deepseek-chat"
    config["output_language"] = "Chinese"
    config["debug"] = False
    # ── 座位理论注入：大师方法论 + 用户自定义理论 ──
    # merge 而非整体覆盖，未配置的角色保持 DEFAULT_CONFIG 中的 "default"/""
    if master_config:
        merged_masters = dict(DEFAULT_CONFIG.get("master_config", {}))
        merged_masters.update(master_config)
        config["master_config"] = merged_masters
    if custom_theory_config:
        merged_theories = dict(DEFAULT_CONFIG.get("custom_theory_config", {}))
        merged_theories.update(custom_theory_config)
        config["custom_theory_config"] = merged_theories
    # TradingAgentsGraph 构造涉及 LLM 客户端初始化等同步 IO，放进 executor
    graph = await loop.run_in_executor(
        None,
        lambda: TradingAgentsGraph(
            selected_analysts=["market", "fundamentals"],
            debug=False,
            config=config,
        ),
    )

    def _on_chunk(state: dict, node_name: str):
        """Called from propagate_stream's thread for each graph node output."""
        q.put(("chunk", dict(state), node_name))

    def _run_in_thread():
        try:
            final_state, signal = graph.propagate_stream(
                ticker, trade_date, on_chunk=_on_chunk,
            )
            q.put(("done", final_state, signal))
        except Exception as e:
            q.put(("error", str(e)))
        finally:
            graph_done.set()

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()

    yield _status_event("正在初始化分析引擎…")

    while True:
        try:
            # ★ q.get 是同步阻塞调用，必须放进 executor 线程执行，
            #   否则会卡死整个 uvicorn 事件循环（LLM 分析期间面板/
            #   其他 HTTP 请求全部无法响应）
            item = await loop.run_in_executor(
                None, lambda: q.get(timeout=0.3)
            )
        except queue.Empty:
            if graph_done.is_set():
                break
            await asyncio.sleep(0)  # 让出事件循环
            continue

        kind = item[0]
        if kind == "chunk":
            state, node_name = item[1], item[2]
            # Convert state changes to chat messages
            msgs = _extract_chat_messages(state, prev_state, ticker, msg_counter)
            for m in msgs:
                yield _chat_event(m)
            prev_state = state

        elif kind == "done":
            final_state, signal = item[1], item[2]
            # Emit any remaining messages
            msgs = _extract_chat_messages(final_state, prev_state, ticker, msg_counter)
            for m in msgs:
                yield _chat_event(m)

            sig_text = _extract_signal_from_state(final_state.get("final_trade_decision", ""))
            yield _status_event("分析完成！", signal=sig_text)

            # Build reports dict for session storage
            reports = {
                "fundamentals_report": final_state.get("fundamentals_report", ""),
                "market_report": final_state.get("market_report", ""),
                "bull_report": _get_debate_field(final_state, "bull_history"),
                "bear_report": _get_debate_field(final_state, "bear_history"),
                "risk_report": "\n\n".join([
                    _get_debate_field(final_state, "aggressive_history", risk=True),
                    _get_debate_field(final_state, "conservative_history", risk=True),
                    _get_debate_field(final_state, "neutral_history", risk=True),
                ]),
                "trading_report": final_state.get("trader_investment_plan", ""),
                "decision_report": final_state.get("final_trade_decision", ""),
            }
            if on_state_capture:
                on_state_capture(reports, sig_text)

            yield _sse_event({"type": "reports_ready", "reports": [
                "fundamentals", "technical", "bull", "bear", "trading", "risk", "decision"
            ]})
            yield _sse_event({"type": "done"})
            break

        elif kind == "error":
            yield _sse_event({"type": "error", "message": item[1]})
            yield _sse_event({"type": "done"})
            break

    # thread.join 同样是阻塞调用，放进 executor 避免冻结事件循环
    await loop.run_in_executor(None, lambda: thread.join(timeout=5))


# ── 技术指标辅助函数（与主程序同源OHLCV数据）──

import pandas as _pd

def _calc_sma(series, window: int) -> float:
    try:
        s = series["Close"] if isinstance(series, _pd.DataFrame) else series
        return round(float(s.tail(window).mean()), 2)
    except Exception: return 0


def _calc_rsi(close_series, window: int = 14) -> float:
    try:
        delta = close_series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(span=window, adjust=False).mean().iloc[-1]
        avg_loss = loss.ewm(span=window, adjust=False).mean().iloc[-1]
        if avg_loss == 0: return 100.0
        rs = avg_gain / avg_loss
        return round(float(100 - 100 / (1 + rs)), 2)
    except Exception: return 0


def _calc_atr(df, window: int = 14) -> float:
    try:
        high, low, close = df["High"], df["Low"], df["Close"]
        tr = _pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
        return round(float(tr.tail(window).mean()), 2)
    except Exception: return 0


def _calc_macd(close_series, fast: int = 12, slow: int = 26, signal: int = 9):
    """标准 MACD（EMA 体系）：返回 (DIF, DEA)。

    DIF = EMA(12) - EMA(26)；DEA = DIF 的 9 日 EMA。
    之前误用 SMA 差值冒充 MACD，已修正。
    """
    try:
        ema_fast = close_series.ewm(span=fast, adjust=False).mean()
        ema_slow = close_series.ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=signal, adjust=False).mean()
        return round(float(dif.iloc[-1]), 4), round(float(dea.iloc[-1]), 4)
    except Exception:
        return 0, 0


def _calc_kdj(df, n: int = 9, m1: int = 3, m2: int = 3):
    """Return (K, D, J)."""
    try:
        low, high, close = df["Low"], df["High"], df["Close"]
        llv = low.rolling(n).min()
        hhv = high.rolling(n).max()
        rsv = (close - llv) / (hhv - llv + 1e-10) * 100
        k = rsv.ewm(span=m1, adjust=False).mean()
        d = k.ewm(span=m2, adjust=False).mean()
        j = 3 * k - 2 * d
        return round(float(k.iloc[-1]), 2), round(float(d.iloc[-1]), 2), round(float(j.iloc[-1]), 2)
    except Exception: return 0, 0, 0


def _calc_boll(df, window: int = 20):
    """Return (upper, mid, lower)."""
    try:
        mid = df["Close"].rolling(window).mean()
        std = df["Close"].rolling(window).std()
        upper = mid + 2 * std
        lower = mid - 2 * std
        return round(float(upper.iloc[-1]), 2), round(float(mid.iloc[-1]), 2), round(float(lower.iloc[-1]), 2)
    except Exception: return 0, 0, 0


def _calc_volume_ratio(df, short: int = 5, long: int = 20) -> float:
    try:
        short_avg = df["Volume"].tail(short).mean()
        long_avg = df["Volume"].tail(long).mean()
        return round(float(short_avg / long_avg), 2) if long_avg > 0 else 1.0
    except Exception: return 1.0
