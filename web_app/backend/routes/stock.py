"""Stock data API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from web_app.backend.models import (
    StockBasicInfo,
    TechnicalIndicators,
    FundamentalsData,
)
from web_app.backend.services.stock_service import (
    get_basic_info,
    get_technical_data,
    get_fundamentals_data,
    get_fundamentals_history,
)

router = APIRouter(prefix="/api/stock", tags=["stock"])


@router.get("/{ticker}/info", response_model=StockBasicInfo)
async def stock_info(ticker: str):
    """Get basic stock information."""
    try:
        return await get_basic_info(ticker)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取股票信息失败: {e}")


@router.get("/{ticker}/technicals", response_model=TechnicalIndicators)
async def stock_technicals(ticker: str, date: str | None = None):
    """Get technical indicators for a stock."""
    try:
        return await get_technical_data(ticker, date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取技术面数据失败: {e}")


@router.get("/{ticker}/fundamentals")
async def stock_fundamentals(ticker: str, date: str | None = None):
    """Get fundamental analysis data for a stock."""
    from fastapi.responses import JSONResponse
    try:
        result = await get_fundamentals_data(ticker, date)
        return JSONResponse(
            content=result.model_dump(mode="json"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取基本面数据失败: {e}")


@router.get("/{ticker}/fundamentals/history")
async def stock_fundamentals_history(ticker: str, type: str = "annual"):
    """Get historical fundamentals for charting.
    
    type: "annual" (近5年年报) or "quarterly" (近4个季度季报)
    """
    if type not in ("annual", "quarterly"):
        raise HTTPException(status_code=400, detail="type 必须是 annual 或 quarterly")
    try:
        return await get_fundamentals_history(ticker, type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取历史基本面数据失败: {e}")
