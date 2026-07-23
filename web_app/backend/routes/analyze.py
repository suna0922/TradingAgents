"""Analysis / Roundtable API routes with SSE streaming."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from web_app.backend.services.data_session import DataSession

from web_app.backend.models import (
    CreateSessionRequest,
    CreateSessionResponse,
    Master,
    ReportType,
    Seat,
    SessionInfo,
    SessionStatus,
    get_default_masters,
    get_default_seats,
)
from web_app.backend.services.agent_service import (
    RoundtableSession,
    create_session,
    get_session,
    _save_session,
    list_sessions,
)
from web_app.backend.services.stock_service import (
    get_basic_info,
    get_stock_name,
)
from web_app.backend.services.master_loader import load_all_masters

router = APIRouter(prefix="/api/analyze", tags=["analyze"])


# ── 后台数据预取辅助 ──

async def _prefetch_and_warm(ticker: str, trade_date: str, ds: DataSession) -> None:
    """后台预热 DataSession + L2/L3 分析数据。

    放在 analyze session 创建后 fire-and-forget 执行。
    面板 REST 端点通过 DataSession.get_or_create() 获取同一实例并等待 prefetch_panel() 完成。
    """
    import logging
    try:
        await ds.prefetch_panel()
        logging.getLogger(__name__).info("DataSession panel prefetch done: %s", ticker)
        # L2+L3 继续后台跑，不阻塞
        asyncio.create_task(_prefetch_analysis(ticker, ds))
    except Exception as e:
        logging.getLogger(__name__).error("DataSession prefetch failed: %s %s", ticker, e)
        # 失败不抛——面板 REST 和 SSE 会走 fallback 直接调 API


async def _prefetch_analysis(ticker: str, ds: DataSession) -> None:
    """L2 新闻 + L3 深度分析 后台预取。"""
    import logging
    try:
        await ds.prefetch_analysis_background()
        logging.getLogger(__name__).info("DataSession analysis prefetch done: %s", ticker)
    except Exception as e:
        logging.getLogger(__name__).warning("DataSession analysis prefetch failed: %s %s", ticker, e)


@router.get("/masters", response_model=list[Master])
async def list_masters():
    """Get the list of available investment masters (loaded from YAML)."""
    return load_all_masters()


@router.get("/seats", response_model=list[Seat])
async def list_seats():
    """Get the default roundtable seat configuration."""
    return get_default_seats()


@router.post("/session", response_model=CreateSessionResponse)
async def create_analysis_session(req: CreateSessionRequest):
    """Create a new analysis session and start fetching stock data."""
    import asyncio, traceback, logging
    ticker = req.ticker.strip()

    # 股票名：真实名称查询（进程内缓存），失败兜底 ticker
    try:
        name = await asyncio.to_thread(get_stock_name, ticker)
    except Exception:
        name = ticker

    session = create_session(ticker=ticker, stock_name=name)

    # ★ 会话创建后立即后台预热数据（fire-and-forget）。
    # 面板 REST 端点和 SSE 流都通过 DataSession.get_or_create() 共享
    # 同一实例：预热完成后两者都是缓存命中，亚秒级返回。
    # （数据获取通过 run_in_executor 在线程池执行，与 SSE 路径的执行
    # 方式完全相同，不存在额外的进程崩溃风险。）
    from datetime import date as _date
    trade_date = _date.today().strftime("%Y-%m-%d")
    ds = DataSession.get_or_create(ticker, trade_date)
    asyncio.create_task(_prefetch_and_warm(ticker, trade_date, ds))

    return CreateSessionResponse(
        session_id=session.session_id,
        stock_name=name,
        status=SessionStatus.CREATED,
    )


@router.get("/session/{session_id}", response_model=SessionInfo)
async def get_session_info(session_id: str):
    """Get session status and info."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session.to_info()


@router.post("/session/{session_id}/seats")
async def update_seats(session_id: str, seats: list[Seat]):
    """Update the seat assignments (drag-and-drop)."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    session.seats = seats
    return {"ok": True, "count": len(seats)}


@router.get("/session/{session_id}/stream")
async def stream_analysis(session_id: str):
    """Run the roundtable analysis and stream agent messages via SSE."""

    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    async def event_generator():
        import traceback
        try:
            # Use the main program's graph for analysis (same logic as CLI)
            from web_app.backend.services.graph_adapter import stream_graph_analysis
            from datetime import date

            trade_date = date.today().strftime("%Y-%m-%d")

            # ★ 获取/创建 DataSession（与面板 REST 共享同一实例）
            data_session = DataSession.get_or_create(session.ticker, trade_date)
            # 如果还没预取（session 创建时已异步触发），等待面板数据就绪
            if not data_session.panel_ready:
                try:
                    await asyncio.wait_for(data_session.prefetch_panel(), timeout=20.0)
                except asyncio.TimeoutError:
                    pass  # 超时继续走 fallback

            def _store_reports(reports: dict, signal: str | None):
                """Callback: store reports into session after graph completes."""
                session.fundamentals_report = reports.get("fundamentals_report", "")
                session.market_report = reports.get("market_report", "")
                session.bull_report = reports.get("bull_report", "")
                session.bear_report = reports.get("bear_report", "")
                session.risk_report = reports.get("risk_report", "")
                session.trading_report = reports.get("trading_report", "")
                session.decision_report = reports.get("decision_report", "")
                if signal:
                    from web_app.backend.models import Signal
                    try:
                        session.signal = Signal(signal)
                    except ValueError:
                        session.signal = None

            # 把圆桌座位配置（大师 + 自定义理论）转换成引擎的角色理论注入配置
            from web_app.backend.models import seats_to_engine_config
            master_config, custom_theory_config = seats_to_engine_config(session.seats or [])

            async for sse_line in stream_graph_analysis(
                session.ticker, trade_date, session.stock_name,
                on_state_capture=_store_reports,
                master_config=master_config,
                custom_theory_config=custom_theory_config,
                data_session=data_session,
            ):
                yield sse_line
                session.status = SessionStatus.ANALYZING

            session.status = SessionStatus.COMPLETED
            # P1: persist session results
            try:
                _save_session(session)
            except Exception:
                import logging
                logging.getLogger(__name__).warning("Failed to persist session %s", session.session_id)

        except Exception as e:
            session.status = SessionStatus.ERROR
            # traceback 只记服务器日志，不下发给客户端（避免泄露内部路径/代码结构）
            import logging
            logging.getLogger(__name__).error(
                "SSE analysis failed for %s: %s\n%s",
                session.ticker, e, traceback.format_exc(),
            )
            yield f"data: {json.dumps({'type': 'error', 'message': '分析过程出现异常，请稍后重试'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/session/{session_id}/report/{report_type}")
async def get_report(session_id: str, report_type: ReportType):
    """Get a specific analysis report."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    report_map = {
        ReportType.FUNDAMENTALS: session.fundamentals_report,
        ReportType.TECHNICAL: session.market_report,
        ReportType.BULL: session.bull_report,
        ReportType.BEAR: session.bear_report,
        ReportType.RISK: session.risk_report,
        ReportType.TRADING: session.trading_report,
        ReportType.DECISION: session.decision_report,
    }

    content = report_map.get(report_type, "")
    if not content:
        raise HTTPException(status_code=404, detail="报告尚未生成")

    return {
        "session_id": session_id,
        "type": report_type.value,
        "content": content,
    }


@router.get("/session/{session_id}/messages")
async def get_messages(session_id: str):
    """Get all chat messages for a session."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "session_id": session_id,
        "messages": [m.model_dump(mode="json") for m in session.messages],
    }


# ── P0: Structured Trading Rules ──

@router.get("/session/{session_id}/rules")
async def get_rules(session_id: str):
    """Get structured trading rules from PM decision."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    rules = []
    for r in session.trading_rules:
        rules.append({
            "name": r.name,
            "action": r.action.value if hasattr(r.action, 'value') else str(r.action),
            "condition_str": getattr(r, 'condition_str', ''),
            "priority": getattr(r, 'priority', 50),
            "pct": getattr(r, 'pct', 0.0),
            "description": getattr(r, 'description', ''),
        })
    return {
        "session_id": session_id,
        "ticker": session.ticker,
        "stock_name": session.stock_name,
        "signal": session.signal.value if session.signal else None,
        "rules": rules,
    }


# ── P1: Session History ──

@router.get("/sessions")
async def get_sessions():
    """List all persisted analysis sessions."""
    sessions = list_sessions()
    return {"sessions": sessions}
