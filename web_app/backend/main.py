"""Stock Roundtable Web App — FastAPI Backend + Frontend."""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root for imports
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from web_app.backend.routes.stock import router as stock_router
from web_app.backend.routes.analyze import router as analyze_router

app = FastAPI(
    title="选股圆桌会议",
    description="一群投资大师围坐圆桌，为你讨论分析股票",
    version="1.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stock_router)
app.include_router(analyze_router)


@app.on_event("startup")
async def _startup_preload():
    """服务启动即后台预载 A 股代码→名称表，避免首个请求付出 10-25s 下载成本。"""
    from web_app.backend.services.stock_service import preload_name_table
    preload_name_table()

# Frontend static file path
_FRONTEND_DIR = _project_root / "web_app" / "frontend"


@app.get("/")
async def root():
    """Serve the main frontend HTML."""
    index_path = _FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path, media_type="text/html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return {"service": "选股圆桌会议 API", "version": "1.0.0"}


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/test")
async def test_page():
    return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>连通性测试</title></head>
<body style="background:#1a1;color:#fff;padding:80px;font-size:48px;text-align:center;font-family:sans-serif">
✅ 服务器正常！
<p style="font-size:18px;margin-top:30px">如果你能看到这个，说明 HTTP 服务完全没问题。<br>
请回 <a href="/" style="color:#ff0">主页</a> 按 <b>Ctrl+Shift+R</b> 硬刷新。</p>
</body></html>""")


# Serve local vendor assets (React / ReactDOM / Babel / Tailwind) without external CDN
_vendor_dir = _FRONTEND_DIR / "vendor"
if _vendor_dir.exists():
    app.mount("/vendor", StaticFiles(directory=str(_vendor_dir)), name="vendor")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
