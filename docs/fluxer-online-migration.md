# Fluxer 在线身份准备

默认 `FORUM_AUTH_MODE=legacy`，准备分支只增加投影，不切换应用身份。原 `author` 表和所有 Discord 身份列保留，`user.id` 才是统一账户和作者 ID。`discord_identity` 是 Fluxer Cassandra 绑定的本地投影，不负责分配 ID。

本次交付不运行生产迁移或预建用户。先在 Fluxer 预建正式成员，取得权威 JSONL，每行包含 `discord_user_id,user_id,version,username,display_name,avatar_url`；不要把 Discord ID 复制成 Fluxer ID。对全部历史作者、书单所有者及正式成员导出映射。源 0 和 NULL 保持系统与未知含义。

显式部署 `alembic upgrade fluxer_identity_prepare` 后，旧服务继续运行。短 DDL 锁设置 5 秒超时，锁竞争会失败并可重试；不会锁住长事务等待。准备迁移删除现存全部实体外键，新模型同样不声明外键。触发器捕获旧进程后续插入与身份修改；映射缺失留下 NULL，审计阻止切换。

使用同一 Windows checkout 的 uv 环境显式执行：

```
uv run python scripts/fluxer_identity_migration.py import --file authoritative-members.jsonl --batch-size 1000
uv run python scripts/fluxer_identity_migration.py backfill --batch-size 1000 --max-batches 100
uv run python scripts/fluxer_identity_migration.py audit
```

命令使用 `DATABASE_URL`；import 每批独立事务、重复输入幂等，碰撞或重绑拒绝，不静默转移所有权。backfill 每批行锁加源值条件更新，允许旧写入继续发生，重启会继续处理差异而非依赖不可靠游标。JSON 作者列表保留顺序与重复项，全部映射齐全才更新。持续重复 backfill/audit，退出码 1 表示仍有缺失/失配/孤立身份，绝不可切换。所有原身份字段保持审计原值。

在线准备阶段允许旧系统继续写入并反复回填。最终切换必须安排停写窗口：停止全部旧写入者，包括旧 API、所有 Bot、worker 和后台脚本；在停写期间完成最后一轮 backfill 并确认 audit=0；再执行 runtime 迁移、启动功能版本并显式启用 Fluxer 模式。任何旧写入者都不能在 runtime 迁移后恢复运行。不可自动删除旧身份列或回退迁移。绑定变化需要重新同步权威投影并审计，本工具不会把冲突绑定自动重分配。


## 功能版本与认证部署

完整权威导入后，先停止全部旧 API/Bot/worker/脚本写入，在停写窗口执行最后 backfill/audit=0，再显式运行 `alembic upgrade fluxer_identity_runtime`。该迁移允许原生用户没有 Discord ID，增加独立 Fluxer 唯一索引，并把已在准备阶段分批填好的投票代理键提升为主键。功能迁移仍保留 Discord 原字段。只有准备分支时不要运行 runtime 迁移或启用新模式。

最后显式配置 `FORUM_AUTH_MODE=fluxer` 与 `FLUXER_API_URL`，通过 `INTEGRATION_NATS_URL/USER/PASSWORD` 连接带 ACL 的内部 NATS。Forum 订阅 `events.auth.v1.session_invalidated`、`events.auth.v1.user_invalidated`、`events.identity.v1.binding_changed`、`rpc.forum.v1.service_info` 和 `_INBOX.forum.>`，发布认证与身份 RPC 及对端 `_INBOX` 回复。启动时必须完成失效订阅与代次轮换，断线期间认证返回 503；重连轮换全部缓存代次。Redis 必须可用，令牌仅以 SHA256 作为缓存键，缓存不续期，正结果从请求开始最多一小时，明确无效结果最多五秒，其他错误不缓存。会话事件只推进 session epoch，用户/绑定事件只推进对应 user epoch，不影响其他用户。缓存读写使用真实 Lua 原子核对目标 epoch；RPC 前尚不知道 user_id 时记录事件序号，并只比较返回目标的最后事件序号，防止在途撤销回填。epoch 保留 3700 秒，覆盖一小时缓存和最长有界 RPC。失效处理失败会关闭旧连接，下次请求重建并轮换全局代次；失败绑定事件保留重试，权威 User 资料与本地绑定同事务更新。正常绑定投影不暂停其他用户鉴权。原始令牌不写入日志或键名。

Fluxer 模式只接受 `Authorization: Bearer flx_...`，不接受自动附带的 session Cookie，因而没有 Cookie 写请求 CSRF 隐患。Discord OAuth 入口保留成员和身份组资格校验，增加一次性 state 和浏览器绑定 Cookie。回调仅把 60 秒一次性 `login_code` 放入前端 `/login#login_code=...`。前端向 `POST /v1/auth/session/claim` 提交 `{login_code}` 并携带绑定 Cookie；后端校验精确前端 Origin、浏览器绑定并原子消费后返回 `{token,user}`。可选的 `password_setup_proof,proof_expires_in` 仅随该一次性响应返回，用于立即设置初始密码，服务不保留客户端全局 proof 状态。所有登录交付响应均 `Cache-Control:no-store`。

`GET /v1/auth/config` 返回 `{mode,fluxer_api_url}`。`GET /v1/auth/checkauth` 使用相同权威解析与限频主体，不续期 token，`user.id` 始终为 Fluxer 字符串，`discord_user_id` 来自权威会话结果，可为 NULL。`POST /v1/auth/logout` 撤销当前 Bearer 会话并只失效该会话；Fluxer 模式拒绝 GET 退出和开发 JWT 登录。Discord 专属权限只使用已确认绑定，原生用户可普通访问和创建私人/公开书单。

验证命令仅使用隔离环境：`FLUXER_MIGRATION_TEST_URL` 显式指向临时 PostgreSQL，测试为每例创建随机 schema；`FLUXER_LIVE_NATS_CONFIG` 显式指向临时 NATS 测试元数据。测试不会自动访问生产数据库、生产 Redis 或生产 NATS。

实际部署 ACL 配置和 `provision-content.mjs` 已在隔离 Windows NATS 上验证：匿名拒绝、Forum 禁止 ensure、Bot 禁止 mint、越权订阅拒绝、服务动态回复权限、管理账号创建流/消费者、Forum 拉取并 ACK 均通过。内容流仍无生产者或消费者运行，本测试只发送隔离内容。


## 正式成员离线预建与权威导出

未找到等价批量工具，因此新增 `scripts/fluxer_prebuild_members.py`。本次只实现和测试工具，未执行真实建号。它不抓取 Discord，也不从浏览器接收账户 ID；输入必须由可信同步 Bot 导出、已经验证正式资格的 UTF-8 JSONL。每行格式如下，ID 为无前导零的十进制字符串：

```json
{"discord_user_id":"123456789012345678","formal_member":true,"verified_at":"2026-10-29T01:00:00Z","profile":{"username":"discord_name","display_name":"显示名","avatar_url":null}}
```

默认要求验证时间不超过 24 小时，可通过显式 `--max-age-hours` 调整到已批准的快照窗口。`formal_member` 必须严格为 true，头像只能来自该 Discord 用户自己的 CDN 路径。任何坏行、重复主体或资格失败都会在第一次 RPC 前阻断整个输入快照。

Windows PowerShell 示例：

```powershell
uv run python scripts/fluxer_prebuild_members.py --input verified-formal-members.jsonl --output authoritative-members.jsonl
$env:INTEGRATION_NATS_URL = "nats://integration-broker:4222"
$env:INTEGRATION_BOT_PASSWORD = "独立 discord-sync 密码"
uv run python scripts/fluxer_prebuild_members.py --input verified-formal-members.jsonl --output authoritative-members.jsonl --state member-prebuild.state.sqlite3 --concurrency 8 --batch-size 100 --execute
```

不带 `--execute` 时仅 dry-run 校验，不连接 NATS、不建号、不生成日志或输出。执行模式强制使用 `discord-sync` 账号和 `_INBOX.bot`，只调用 ensure_discord、批量 lookup_discord 与 profiles，不能铸造 token。每个 Discord 主体的 operation_id 由固定命名空间和主体生成，与文件路径、行顺序、重试次数无关。ensure 采用 25 秒有界超时，可重试错误最多重试三次；默认并发八个，最大三十二个，批量最多一百条。

每条 ensure 成功立即以 SQLite FULL 同步事务记录，Windows 操作系统锁阻止并发运行同一日志，崩溃会自动释放锁。资料查询失败后重跑会先批量查现有权威绑定，避免重复 ensure；版本必须来自权威 lookup，用户名/显示名/头像来自 Fluxer 原生 profiles，绝不把 Discord ID 当 Fluxer ID。输出只含现有 import 工具需要的六个字段，通过临时文件 fsync 后原子替换；文件被占用时旧快照保持完整，成功日志仍可恢复。

只有退出码 0 且 `ready:true` 才可把输出交给 `fluxer_identity_migration.py import`；失败返回非零，输出仅含本轮完整成功记录，不代表正式成员已全量建号。缺失的历史作者或书单所有者映射仍会阻止后续审计/切换，不能通过伪造正式资格或复制 ID 绕过。保留输入、SQLite 状态日志与权威输出以备审计，日志和输出均不包含登录 token。
