import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from api.v1.routers import (
    auth,
    authors,
    banner,
    booklists,
    collections,
    fetch_images,
    follows,
    meta,
    preferences,
    search,
    tags,
    discovery,
)

# 读取配置
try:
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
    
    api_config = config.get("api", {})
    auth_config = config.get("auth", {})
    
    enable_docs = api_config.get("enable_docs", True)
    
    # 优先读取 api.cors_origins
    cors_origins = api_config.get("cors_origins", [])
    if isinstance(cors_origins, str): # 防止有人填错成字符串
        cors_origins = [cors_origins]
        
    # 如果没配 cors_origins，尝试读取 auth.frontend_url
    frontend_url = auth_config.get("frontend_url")
    
    # 汇总允许的源
    allowed_origins = []
    if cors_origins:
        allowed_origins.extend(cors_origins)
    if frontend_url and frontend_url not in allowed_origins:
        allowed_origins.append(frontend_url)
        
    # 如果汇总后依然为空，则允许所有源
    if not allowed_origins:
        allowed_origins = ["*"]

except (FileNotFoundError, KeyError, json.JSONDecodeError):
    enable_docs = True
    allowed_origins = ["*"]

# 根据配置决定是否启用文档
docs_url = "/docs" if enable_docs else None
redoc_url = "/redoc" if enable_docs else None

app = FastAPI(
    title="Odysseia Forum Bot API",
    description="Odysseia 论坛机器人 API 服务",
    version="1.0.0",
    docs_url=docs_url,
    redoc_url=redoc_url,
)

# 配置 CORS
# 支持在config.json中配置多个允许的源
if "*" in allowed_origins:
    # 允许所有源（不推荐用于生产环境）
    actual_allowed_origins = ["*"]
    allow_credentials = False  # "*" 不能与 credentials 同时使用
else:
    # 使用配置的源列表
    actual_allowed_origins = allowed_origins
    allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=actual_allowed_origins,
    allow_credentials=allow_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],  # 允许所有headers
    expose_headers=["*"],  # 暴露所有headers
    max_age=3600,  # 预检请求缓存1小时
)

# 启用 GZip 压缩
app.add_middleware(GZipMiddleware, minimum_size=1000)

# 包含路由
app.include_router(auth.router, prefix="/v1")
app.include_router(authors.router, prefix="/v1")
app.include_router(preferences.router, prefix="/v1")
app.include_router(search.router, prefix="/v1")
app.include_router(follows.router, prefix="/v1")
app.include_router(meta.router, prefix="/v1")
app.include_router(fetch_images.router, prefix="/v1")
app.include_router(banner.router, prefix="/v1")
app.include_router(collections.router, prefix="/v1")
app.include_router(booklists.router, prefix="/v1")
app.include_router(tags.router, prefix="/v1")
app.include_router(discovery.router, prefix="/v1")


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
            "auth": "/v1/auth",
            "authors": "/v1/authors",
            "preferences": "/v1/preferences",
            "search": "/v1/search",
            "follows": "/v1/follows",
            "meta": "/v1/meta",
            "fetch-images": "/v1/fetch-images",
            "banner": "/v1/banner",
            "collections": "/v1/collections",
            "booklists": "/v1/booklists",
            "tags": "/v1/tags",
        },
    }
