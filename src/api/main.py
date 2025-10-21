import json
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from .v1.routers import preferences, search
from .v1.main import app as app_v1

# 读取配置来决定是否启用文档
try:
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
    enable_docs = config.get("api", {}).get("enable_docs", True)
except (FileNotFoundError, KeyError):
    enable_docs = True

# 根据配置决定是否启用文档
docs_url = "/docs" if enable_docs else None
redoc_url = "/redoc" if enable_docs else None

app = FastAPI(
    title="Odysseia Forum Bot API",
    description="Odysseia 论坛机器人 API 服务",
    version="1.0.0",
    docs_url=docs_url,
    redoc_url=redoc_url
)

# 启用 GZip 压缩
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(preferences.router, prefix="/v1")
app.include_router(search.router, prefix="/v1")

# 包含 v1 的健康检查端点
@app.get("/v1/health", summary="健康检查", tags=["系统"])
async def health_check():
    """API 服务健康检查端点"""
    return {"status": "ok"}

@app.get("/", summary="API 根路径", tags=["系统"])
async def root():
    """API 服务根路径"""
    return {
        "message": "Odysseia Forum Bot API",
        "version": "1.0.0",
        "v1_api": "/v1",
        "endpoints": {
            "health": "/v1/health",
            "preferences": "/v1/preferences",
            "search": "/v1/search"
        }
    }