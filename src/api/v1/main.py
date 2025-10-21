from fastapi import FastAPI
from .routers import preferences, search

app = FastAPI(
    title="Odysseia Forum API v1",
    description="Odysseia 论坛机器人 API 第一版",
    version="1.0.0"
)

app.include_router(preferences.router)
app.include_router(search.router)

@app.get("/health", summary="健康检查", tags=["系统"])
async def health_check():
    """API 服务健康检查端点"""
    return {"status": "ok"}