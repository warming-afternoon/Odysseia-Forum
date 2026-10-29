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
