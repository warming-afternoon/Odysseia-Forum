# 🌐 API 模块

## 📖 简介
`api` 模块提供基于 FastAPI 的 RESTful HTTP API 服务，是 Discord Bot 数据对外暴露的统一接口层。前端索引页 (`webpage`) 和第三方集成通过此模块访问帖子搜索、收藏、书单、发现页等功能。

API 与 Bot 共享同一个数据库连接池，通过 uvicorn 在 Bot 进程内或独立进程中运行。

---

## 🏗️ 架构概览

```text
api/
├── main.py                      # FastAPI 应用实例、中间件配置、路由注册。
└── v1/
    ├── routers/                 # 🚀 路由层 (按业务领域拆分)
    │   ├── auth.py              # Discord OAuth2 登录与 JWT Token 管理。
    │   ├── authors.py           # 作者信息查询。
    │   ├── banner.py            # Banner 轮播数据接口。
    │   ├── booklists.py         # 书单 CRUD（创建、编辑、添加/移除帖子等）。
    │   ├── collections.py       # 用户收藏接口。
    │   ├── discovery.py         # 发现页多轨道数据接口。
    │   ├── fetch_images.py      # 图片代理/预取接口。
    │   ├── follows.py           # 帖子关注与未读更新接口。
    │   ├── meta.py              # 系统元数据（频道列表、标签列表等）。
    │   ├── preferences.py       # 用户搜索偏好接口。
    │   ├── search.py            # 帖子搜索接口（FTS5 + 多条件）。
    │   └── tags.py              # 标签统计接口。
    │
    ├── schemas/                 # 📦 Pydantic 请求/响应模型
    │   ├── base.py              # 通用基类。
    │   ├── banner/              # Banner 相关模型。
    │   ├── booklist/            # 书单相关模型。
    │   ├── discovery.py         # 发现页模型。
    │   ├── preferences/         # 偏好模型。
    │   ├── search/              # 搜索请求/响应模型。
    │   └── tags/                # 标签统计模型。
    │
    ├── dependencies/            # 🔌 FastAPI 依赖注入
    │   └── security.py          # JWT Token 验证依赖 (Bearer Auth)。
    │
    └── utils/                   # 🛠️ 工具函数
        ├── jwt_utils.py         # JWT 签发与验证逻辑。
        └── thread_detail_builder.py # 帖子详情数据组装器。
```

---

## ⚙️ 核心配置

### 中间件
- **CORS**: 从 `config.json` 的 `api.cors_origins` 和 `auth.frontend_url` 读取允许的源。
- **GZip**: 响应体超过 1000 字节时自动压缩。
- **ORJSONResponse**: 使用 orjson 加速 JSON 序列化。

### 认证机制
1. 用户通过 `/v1/auth` 使用 Discord OAuth2 登录。
2. 后端签发 JWT Token，包含 `user_id` 等声明。
3. 需要身份验证的接口通过 `dependencies/security.py` 校验 Bearer Token。

### 文档
- 可通过 `config.json` 的 `api.enable_docs` 控制是否暴露 `/docs` (Swagger) 和 `/redoc`。
- 生产环境建议关闭。

---

## 💡 开发指南

### 添加新路由
1. 在 `v1/routers/` 下新建文件，使用 `APIRouter` 定义端点。
2. 在 `v1/routers/__init__.py` 中导出。
3. 在 `main.py` 中 `include_router` 注册。

### 请求/响应模型
- 所有 API 的入参和出参都应在 `v1/schemas/` 中定义 Pydantic 模型。
- 避免直接返回 SQLModel 实体，防止泄露内部字段。
