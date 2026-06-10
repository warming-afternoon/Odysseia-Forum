# Banner 系统说明

Banner 系统是一个**「申请 → 审核 → 轮播展示」**的完整工作流，服务于 Discord 论坛。它允许用户为自己的帖子申请展示 Banner，审核员审批后，Banner 会进入轮播队列，按时间自动轮替。

---

## 1. 整体架构

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  FastAPI 接口  │────▶│  BannerService │────▶│   MySQL 数据库  │
│  (REST API)   │     │  (业务逻辑)     │     │  (3 张表)      │
└──────────────┘     └──────────────┘     └──────────────┘
                            ▲
┌──────────────┐           │
│  Discord Bot  │──────────┘
│  (用户交互)    │
└──────────────┘
```

系统由三层组成：
- **FastAPI** — 提供 REST API，处理外部请求（Banner 申请提交、获取活跃 Banner）
- **Discord Bot** — 提供用户交互界面（申请按钮、表单、审核按钮）
- **BannerService** — 核心业务逻辑层，封装所有数据库操作

---

## 2. 数据库模型（3 张表）

### `banner_application` — 申请表

记录每一次 Banner 申请及其审核状态。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int (PK) | 自增主键 |
| `thread_id` | BigInteger | 帖子 Discord ID |
| `channel_id` | BigInteger | 帖子所在频道 |
| `applicant_id` | BigInteger | 申请人 Discord ID |
| `cover_image_url` | str | 封面图片 URL |
| `target_scope` | str | `"global"`（全频道）或特定频道 ID |
| `status` | str | `pending` / `approved` / `rejected` |
| `applied_at` | datetime | 申请时间 |
| `reviewed_at` | datetime (可空) | 审核时间 |
| `reviewer_id` | BigInteger (可空) | 审核人 ID |
| `reject_reason` | str (可空) | 拒绝理由 |
| `review_message_id` | BigInteger (可空) | Discord 审核消息 ID |
| `review_thread_id` | BigInteger (可空) | Discord 审核线程 ID |

**文件：** [src/models/banner_application.py](../src/models/banner_application.py)

### `banner_carousel` — 轮播表

记录当前正在展示的 Banner（已批准且未过期）。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int (PK) | 自增主键 |
| `thread_id` | BigInteger | 帖子 ID |
| `channel_id` | BigInteger (可空) | 所属频道（NULL = 全局） |
| `cover_image_url` | str | 封面图片 URL |
| `title` | str | 帖子标题 |
| `start_time` | datetime | 展示开始时间 |
| `end_time` | datetime | 展示结束时间（**3 天后**） |
| `position` | int | 展示顺序 |

**文件：** [src/models/banner_carousel.py](../src/models/banner_carousel.py)

### `banner_waitlist` — 等待队列表

当轮播已满时，新批准的 Banner 会排入此队列，等待轮播有空位后被自动晋升。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int (PK) | 自增主键 |
| `thread_id` | BigInteger | 帖子 ID |
| `channel_id` | BigInteger (可空) | 所属频道 |
| `cover_image_url` | str | 封面图片 URL |
| `title` | str | 帖子标题 |
| `queued_at` | datetime | 入队时间 |
| `position` | int | 队列位置 |

**文件：** [src/models/banner_waitlist.py](../src/models/banner_waitlist.py)

---

## 3. API 端点

REST API 位于 [src/api/v1/routers/banner.py](../src/api/v1/routers/banner.py)，前缀 `/v1/banner`，全部需要认证。

### `POST /v1/banner/apply` — 提交 Banner 申请

**请求体：**
```json
{
  "thread_id": "1234567890123456789",
  "cover_image_url": "https://example.com/cover.png",
  "target_scope": "global"
}
```

- `thread_id`：纯数字字符串，长度 17-20
- `cover_image_url`：封面图片 URL
- `target_scope`：`"global"` 表示全频道，或填写具体频道 ID

**处理流程：**
1. 调用 `BannerService.validate_and_create_application()` 验证并写入数据库
2. 将申请 ID 推送到 Redis 队列 `"banner:review:queue"`
3. Bot 进程消费队列，发送审核消息到 Discord 审核频道

**响应：**
```json
{
  "success": true,
  "message": "申请已提交",
  "application_id": 42
}
```

### `GET /v1/banner/active` — 获取活跃 Banner

**查询参数：** `channel_id`（可选）

当指定 `channel_id` 时，返回：
- 该频道的专属 Banner（最多 **5 个**）
- + 全局 Banner（最多 **3 个**）

**响应示例：**
```json
[
  {
    "thread_id": "1234567890123456789",
    "title": "帖子标题",
    "cover_image_url": "https://example.com/cover.png",
    "channel_id": "1374474903981527082",
    "guild_id": "9876543210987654321"
  }
]
```

> **注意：** 所有 Discord ID（thread_id、channel_id、guild_id）序列化为**字符串**，避免 JavaScript 大整数精度丢失。

### 搜索接口集成

搜索接口 [src/api/v1/routers/search.py](../src/api/v1/routers/search.py)（第 747-803 行）会**并发**调用 `BannerService.get_active_banners()`，将 Banner 数据注入 `SearchResponse.banner_carousel` 字段，失败时优雅降级返回空列表。

---

## 4. Discord Bot 组件

### Cog：`BannerManagement`

位于 [src/banner/cog.py](../src/banner/cog.py)，提供：

| 功能 | 说明 |
|------|------|
| `/banner 创建申请通道` | 向当前频道发送申请入口按钮（管理员/机器人管理员权限） |
| `/banner 查看状态` | 查看当前轮播统计信息（管理员/机器人管理员权限） |
| `cleanup_expired_banners()` | 定时任务，**每小时**自动清理过期 Banner 并从等待队列晋升 |

在 `cog_load()` 时注册持久化视图（`BannerApplicationButtonView`、`ReviewView`），确保 Bot 重启后按钮仍然可用。

### 用户交互流程（Discord Views）

```
用户点击 "申请Banner展示" 按钮
  └─ BannerApplicationButtonView  ── 检查用户角色权限
       └─ ApplicationFormModal     ── 填写 帖子ID + 封面URL
            └─ ChannelSelectionView ── 选择展示范围（全局/某频道）
                 └─ BannerService.validate_and_create_application()
                      └─ send_review_message() 推送审核消息到审核频道
```

### 审核交互流程

```
审核员点击 "批准"
  └─ BannerService.approve_application()
       ├─ 轮播未满 → 写入 banner_carousel（start=now, end=now+3天）
       └─ 轮播已满 → 写入 banner_waitlist（排队等待）
  └─ DM 通知申请人 "你的 Banner 已通过审核"
  └─ 归档到 archive_thread

审核员点击 "拒绝"
  └─ RejectReasonModal ── 填写拒绝理由
  └─ BannerService.reject_application()
  └─ DM 通知申请人
  └─ 归档到 archive_thread
```

**相关文件：**
| 文件 | 功能 |
|------|------|
| [src/banner/views/banner_application_button_view.py](../src/banner/views/banner_application_button_view.py) | 申请入口按钮 |
| [src/banner/views/application_form_modal.py](../src/banner/views/application_form_modal.py) | 申请表单模态框 |
| [src/banner/views/channel_selection_view.py](../src/banner/views/channel_selection_view.py) | 频道选择下拉菜单 |
| [src/banner/views/review_view.py](../src/banner/views/review_view.py) | 审核批准/拒绝按钮 + 拒绝理由模态框 |

---

## 5. 业务逻辑服务

核心类 `BannerService` 位于 [src/banner/banner_service.py](../src/banner/banner_service.py)。

### 关键常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `GLOBAL_MAX_BANNERS` | 3 | 全局 Banner 最多同时展示数 |
| `CHANNEL_MAX_BANNERS` | 5 | 每个频道 Banner 最多同时展示数 |
| `BANNER_DURATION_DAYS` | 3 | 每个 Banner 展示天数 |

### 核心方法

| 方法 | 功能 |
|------|------|
| `validate_application_request()` | 验证申请：帖子存在性、作者匹配、URL 格式、scope 合法性 |
| `validate_and_create_application()` | 验证 + 创建 PENDING 状态的申请记录 |
| `create_application()` | 插入 `banner_application` 行 |
| `approve_application()` | 批准申请：轮播未满加入 `banner_carousel`，已满加入 `banner_waitlist` |
| `reject_application()` | 拒绝申请，记录审核人和理由 |
| `get_active_banners()` | 查询 `end_time > now` 的轮播 Banner，支持按频道筛选 + 全局合并 |
| `cleanup_expired_banners()` | 删除过期轮播项，从等待队列按 FIFO 晋升 |
| `update_review_message_info()` | 更新申请记录的审核消息 ID（用于按钮回调关联） |
| `get_application_by_review_message()` | 通过审核消息 ID 查找申请 |

### 辅助模块函数

`send_review_message()` — 由 Bot 端调用，负责：
1. 获取配置的 `review_thread_id`
2. 在 Discord 中构建嵌入式审核消息（申请人、范围、帖子链接、封面图片）
3. 发送消息并附加 `ReviewView` 按钮
4. 更新申请记录中的审核消息 ID

### 辅助数据结构

```python
@dataclass
class ApplicationResult:
    success: bool
    message: str
    application: Optional[BannerApplication] = None
    thread: Optional[ThreadDTO] = None
```

---

## 6. 完整数据流

```
┌──────────────────────────────────────────────────────────────┐
│                        申请阶段                               │
├──────────────────────────────────────────────────────────────┤
│  用户 → 点击按钮 → 填写表单 → 选择频道                        │
│       → BannerService.validate_and_create_application()       │
│       → 写入 banner_application (status=pending)              │
│       → Redis 队列 → Bot 发送审核消息到审核频道                 │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│                        审核阶段                               │
├──────────────────────────────────────────────────────────────┤
│  审核员 → 点击 批准/拒绝 → ReviewView                         │
│                                                              │
│  【批准】 approve_application()                               │
│     ├─ 轮播未满 → 写入 banner_carousel (start=now, end=+3天)  │
│     └─ 轮播已满 → 写入 banner_waitlist (排队等待)              │
│                                                              │
│  【拒绝】 reject_application()                                │
│     └─ 记录理由 + 审核人                                      │
│                                                              │
│  → DM 通知申请人 → 归档到 archive_thread                     │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│                        展示阶段                               │
├──────────────────────────────────────────────────────────────┤
│  GET /v1/banner/active → BannerService.get_active_banners()  │
│     → 查询 banner_carousel WHERE end_time > now               │
│     → 按频道聚合（专属最多 5 + 全局最多 3）                    │
│                                                              │
│  搜索接口并发获取 → 注入 SearchResponse.banner_carousel        │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│                   维护阶段（每小时自动）                        │
├──────────────────────────────────────────────────────────────┤
│  BannerService.cleanup_expired_banners()                      │
│     → 删除过期的 banner_carousel 行                           │
│     → 从 banner_waitlist 按 FIFO 晋升到 banner_carousel       │
└──────────────────────────────────────────────────────────────┘
```

---

## 7. 配置

在 `config.json` 中的 `banner` 段：

```json
{
  "banner": {
    "enabled": true,
    "applicant_role_ids": "role_id_1,role_id_2",
    "reviewer_role_ids": "role_id_1,role_id_2,role_id_3",
    "review_thread_id": 1234567890123456789,
    "archive_thread_id": 9876543210987654321,
    "available_channels": {
      "1374474903981527082": "频道A",
      "1374481342825238821": "频道B"
    }
  }
}
```

| 配置项 | 说明 |
|------|------|
| `enabled` | 是否启用 Banner 系统 |
| `applicant_role_ids` | 允许申请 Banner 的 Discord 角色 ID（逗号分隔） |
| `reviewer_role_ids` | 允许审核 Banner 的 Discord 角色 ID（逗号分隔） |
| `review_thread_id` | 审核消息发送到哪个 Discord 帖子 |
| `archive_thread_id` | 审核记录归档到哪个 Discord 帖子 |
| `available_channels` | 可供用户选择的展示频道（key 为频道 ID，value 为显示名称） |

配置模板见 [config.example.json](../config.example.json)。

---

## 8. 设计要点

### 容量控制
- **全局最多 3 个** Banner 同时展示
- **每个频道最多 5 个** Banner 同时展示
- 超额申请进入 `banner_waitlist` 等待队列

### 时间管理
- 每个 Banner 展示 **3 天** 后自动过期
- 每小时定时任务清理过期项并晋升等待队列中的下一个

### 数据隔离
- Banner 分为**全局**（`target_scope = "global"`，`channel_id = NULL`）和**频道专属**两种
- 获取时合并两类 Banner（专属 + 全局），分属不同容量上限

### 持久化视图
- 所有 Discord UI 组件使用持久化视图（`custom_id`），Bot 重启后按钮仍可响应

### 优雅降级
- 搜索接口获取 Banner 失败时返回空列表 `[]`，不影响搜索结果

---

## 9. 文件索引

| 文件 | 角色 |
|------|------|
| [src/api/v1/routers/banner.py](../src/api/v1/routers/banner.py) | REST API 端点（申请提交、获取活跃 Banner） |
| [src/banner/banner_service.py](../src/banner/banner_service.py) | 核心业务逻辑 + `send_review_message()` |
| [src/models/banner_application.py](../src/models/banner_application.py) | 申请表模型 `banner_application` |
| [src/models/banner_carousel.py](../src/models/banner_carousel.py) | 轮播表模型 `banner_carousel` |
| [src/models/banner_waitlist.py](../src/models/banner_waitlist.py) | 等待队列表模型 `banner_waitlist` |
| [src/banner/cog.py](../src/banner/cog.py) | Discord Bot 命令与定时清理任务 |
| [src/banner/views/banner_application_button_view.py](../src/banner/views/banner_application_button_view.py) | 申请入口按钮视图 |
| [src/banner/views/application_form_modal.py](../src/banner/views/application_form_modal.py) | 申请表单模态框 |
| [src/banner/views/channel_selection_view.py](../src/banner/views/channel_selection_view.py) | 频道选择下拉菜单 |
| [src/banner/views/review_view.py](../src/banner/views/review_view.py) | 审核批准/拒绝按钮 + 拒绝理由模态框 |
| [src/api/v1/schemas/banner/banner_item.py](../src/api/v1/schemas/banner/banner_item.py) | API 响应 Schema `BannerItem` |
| [src/api/v1/schemas/banner/__init__.py](../src/api/v1/schemas/banner/__init__.py) | Schema 包导出 |
| [src/api/v1/schemas/search/search_response.py](../src/api/v1/schemas/search/search_response.py) | 搜索响应中的 `banner_carousel` 字段 |
| [src/shared/enum/application_status.py](../src/shared/enum/application_status.py) | `ApplicationStatus` 枚举（pending/approved/rejected） |
| [src/models/__init__.py](../src/models/__init__.py) | 模型导出 |
