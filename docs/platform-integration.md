# Forum 平台测试网关

测试网关以 Forum 的 `work`、`booklist` 和 `booklist_item` 为正本，DD 的数据库保存频道、消息和已读状态。平台用户 ID 存入 `user.id`。为兼容 Forum 现有查询，名字以 `fluxer_` 开头的归属列在本测试库保存平台 ID，原 Discord 数字 ID 留在来源列中。

## 启动

测试环境可以直接使用 `compose.platform-test.yml`。在环境文件中设置 `FORUM_TEST_DATABASE_URL` 指向专用空库，`FORUM_TEST_NETWORK` 填 Core、DD 和 JetStream 所在的已有内部网络。四个 NATS 密码使用网关部署的 `NATS_<模块>_PASSWORD` 变量。将 Core 的 `FORUM_WORKS_URL` 配成 `http://forum:8000` 后执行下列命令。这个 Compose 会先运行迁移，HTTP 和消费者分别使用各自的凭据。

```sh
docker compose --env-file .env.platform-test -f compose.platform-test.yml up -d --build
docker compose --env-file .env.platform-test -f compose.platform-test.yml exec forum-worker .venv/bin/python -m platform_bus.reconcile
```

在独立的空 PostgreSQL 测试库运行 `uv run alembic upgrade head`，再导入 Discord 形态的测试数据。已有原始帖子的测试数据可以用 `uv run python scripts/backfill_works.py` 关联到作品。此流程针对测试库，正式服 V1 的数据库升级须单独规划。

合表后的迁移版本是 `platform_integration`。如果旧测试库的 `alembic_version` 是 `platform_discord_events`，请保留该库，另建测试库验证新版。两版结构不同，直接用 `stamp` 更改版本号会跳过实际建列。

为 Forum 的 HTTP 进程及后台进程配置 `DATABASE_URL` 和 `PYTHONPATH=src`。使用项目依赖，以两个独立进程运行。Core 中的 `FORUM_WORKS_URL` 指向这个 HTTP 服务的根地址。

```sh
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
uv run python -m platform_bus.worker
```

| 配置 | 使用位置及用途 |
| --- | --- |
| `FORUM_PLATFORM_ENABLED=true` | HTTP 开放平台接口 |
| `FORUM_PRINCIPAL_SECRET` | Core 向 Forum 转发作品、搜索、书单及 Discord 来源请求时使用的共享身份签名密钥 |
| `FORUM_INBOUND_INTERNAL_TOKEN` | 其他服务调用 Forum 内部接口时携带的入站令牌；目前用于 Core 回查作品策略，与身份签名密钥分别生成 |
| `FORUM_CORE_URL` | Forum 访问 Core 的内部根地址 |
| `FORUM_CORE_PROFILE_TOKEN` | HTTP 服务批量读取账号资料；worker 通过同一凭据调用 Core 的 `GET /internal/users/:id/follow-settings` 查询自动关注偏好 |
| `FORUM_CORE_CHAT_SYNC_TOKEN` | Core 允许调用者 `forum-work-chat-sync` 执行 `POST /internal/chat/sync-work` |
| `FORUM_INTERNAL_TOKEN` | 专用绑定查询令牌，Core 和 Forum 中配置相同值 |
| `FORUM_DC_LOOKUP_TOKEN` | 初次导入及漏收修复时使用，调用者需获准访问 `GET /internal/users/by-discord/:discordId` |
| `FORUM_NATS_URL` | 网关部署的 JetStream 监听地址 |
| `FORUM_NATS_WORKS_PASSWORD`、`FORUM_NATS_SEARCH_PASSWORD`、`FORUM_NATS_COLLECTIONS_PASSWORD`、`FORUM_NATS_FORUM_PASSWORD` | 分别对应 broker 的 works、search、collections、forum 用户 |

先用网关的 `nats-init` 建立流和 durable consumer。切换到 Forum 时，将原来拉取同名模块 consumer 的旧进程停用。多个客户端共用同一个 durable 时，JetStream 会在它们之间分配消息。

## 测试网关开放的 Forum 接口

下表的路径共用 `/api/v1` 前缀。平台原生作品先创建草稿，再调用发布接口。Discord 来源作品的详情提供原帖链接。

| 方法 | 路径 |
| --- | --- |
| GET、POST | `/works` |
| GET、PATCH、DELETE | `/works/{id}` |
| POST | `/works/{id}/publish`、`/works/{id}/restore` |
| POST、DELETE | `/works/{id}/like`、`/works/{id}/favorite`、`/works/{id}/follow` |
| GET、POST | `/works/{id}/announcements` |
| PATCH、DELETE | `/works/{id}/announcements/{announcementId}` |
| GET、PUT | `/works/{id}/rules` |
| POST | `/works/{id}/ack`、`/me/follows/mark-viewed` |
| GET | `/me/follows` |
| PATCH | `/works/{id}/chat/settings` |
| GET | `/works/by-legacy/discord/{threadId}` |
| GET | `/admin/trash/works` |
| POST | `/admin/trash/works/{itemId}/restore` |
| DELETE | `/admin/trash/works/{itemId}` |
| GET | `/search`、`/search/suggest`、`/me/collections` |
| GET、POST | `/collections`、`/collections/{id}/items` |
| GET、PATCH、DELETE | `/collections/{id}` |
| PATCH、DELETE | `/collections/{id}/items/{workId}` |

聊天消息和已读状态由 DD 处理。版本、随机作品和书单批量同步等未开放的协议能力由网关返回 HTTP 501，错误码为 `not_implemented`。

测试网关的日、周、月榜沿用 Forum 现有的 1、7、30 天点赞净增长统计，默认展示周榜。上升榜与新人榜返回 501。Discord 来源作品保留原帖链接；旧首楼图片只有来源 URL，首期未转换成平台附件。

## 作品互动部署

平台互动引入的列已合入测试库迁移 `platform_integration`。已运行同名旧版迁移的测试库应另建空库或核对后应用本次增量。自动关注任务使用本地 outbox，先更新 worker，再开放产生任务的新接口。每次新增点赞或收到新的交流区发言都可以触发判断，曾主动取消关注时跳过。规则确认与本地通知标已读共用事务，当前阶段不向 Core 投递通知。

## 初次导入及漏收恢复

```sh
uv run python -m platform_bus.reconcile
```

先刷新本地已经记录的平台账号，再按本地作品作者、书单与互动来源的 Discord ID 向 Core 反查。命令可以重复运行。无绑定的来源资料维持原状。一个平台账号有多个原默认书单时，保留已经存在的平台默认书单，尚无默认书单则选最早编号。其他原默认书单保留为私密普通书单，平台默认书单收录各来源收藏的并集。

## 检查交付状态

作品发布和交流区同步分别确认。在 Forum 的事务提交后，作品可以被搜索和加入书单。观察 `platform_outbox` 中该作品的 `chat_sync` 行，`sent_at` 有值表示 Core 已回复 204。修改权限后应查看最新任务。总线事件行的 `sent_at` 表示已收到 JetStream 的发布确认。消费记录 `platform_inbox` 与各消费模块的业务更新在同一事务提交。

```sql
SELECT id, work_id, kind, attempts, available_at, sent_at
FROM platform_outbox
WHERE sent_at IS NULL
ORDER BY available_at, id;
```

DD 或网关暂时不可用时，后台自动退避并继续处理其他作品。工作进程重启后从数据库和 durable consumer 继续。超过总线保留窗口的绑定变更可以运行上述修复命令。已投递次数耗尽的消息可在网关的 JetStream 死信通知中排查。

