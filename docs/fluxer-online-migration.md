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

生产切换必须在完整用户导入、持续旧写入期间的最终审计归零、部署双写功能版本后单独操作。不可自动删除旧身份列或回退迁移。绑定变化需要重新同步权威投影并审计，本工具不会把冲突绑定自动重分配。


## 功能版本与认证部署

完整权威导入并持续回填审计归零后，显式运行 `alembic upgrade fluxer_identity_runtime`。该迁移允许原生用户没有 Discord ID，增加独立 Fluxer 唯一索引，并把已在准备阶段分批填好的投票代理键提升为主键。功能迁移仍保留 Discord 原字段。只有准备分支时不要运行 runtime 迁移或启用新模式。

最后显式配置 `FORUM_AUTH_MODE=fluxer` 与 `FLUXER_API_URL`，通过 `INTEGRATION_NATS_URL/USER/PASSWORD` 连接带 ACL 的内部 NATS。Forum 订阅 `events.auth.v1.session_invalidated`、`events.auth.v1.user_invalidated`、`events.identity.v1.binding_changed`、`rpc.forum.v1.service_info` 和 `_INBOX.forum.>`，发布认证与身份 RPC 及对端 `_INBOX` 回复。启动时必须完成失效订阅与代次轮换，断线期间认证返回 503；重连轮换全部缓存代次。Redis 必须可用，令牌仅以 SHA256 作为缓存键，缓存不续期，正结果从请求开始最多一小时，明确无效结果最多五秒，其他错误不缓存。撤销与在途回填通过 Redis Lua 代次比较互斥。原始令牌不写入日志或键名。

Fluxer 模式只接受 `Authorization: Bearer flx_...`，不接受自动附带的 session Cookie，因而没有 Cookie 写请求 CSRF 隐患。Discord OAuth 入口保留成员和身份组资格校验，增加一次性 state 和浏览器绑定 Cookie。回调仅把 60 秒一次性 `login_code` 放入前端 `/login#login_code=...`。前端向 `POST /v1/auth/session/claim` 提交 `{login_code}` 并携带绑定 Cookie；后端校验精确前端 Origin、浏览器绑定并原子消费后返回 `{token,user}`。可选的 `password_setup_proof,proof_expires_in` 仅随该一次性响应返回，用于立即设置初始密码，服务不保留客户端全局 proof 状态。所有登录交付响应均 `Cache-Control:no-store`。

`GET /v1/auth/config` 返回 `{mode,fluxer_api_url}`。`GET /v1/auth/checkauth` 使用相同权威解析与限频主体，不续期 token，`user.id` 始终为 Fluxer 字符串，`discord_user_id` 来自权威会话结果，可为 NULL。`POST /v1/auth/logout` 撤销当前 Bearer 会话；Fluxer 模式拒绝 GET 退出和开发 JWT 登录。Discord 专属权限只使用已确认绑定，原生用户可普通访问和创建私人/公开书单。

验证命令仅使用隔离环境：`FLUXER_MIGRATION_TEST_URL` 显式指向临时 PostgreSQL，测试为每例创建随机 schema；`FLUXER_LIVE_NATS_CONFIG` 显式指向临时 NATS 测试元数据。测试不会自动访问生产数据库、生产 Redis 或生产 NATS。

实际部署 ACL 配置和 `provision-content.mjs` 已在隔离 Windows NATS 上验证：匿名拒绝、Forum 禁止 ensure、Bot 禁止 mint、越权订阅拒绝、服务动态回复权限、管理账号创建流/消费者、Forum 拉取并 ACK 均通过。内容流仍无生产者或消费者运行，本测试只发送隔离内容。
