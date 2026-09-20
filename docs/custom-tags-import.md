# 批量导入自定义 TAG

适用已部署本版本且完成 Alembic 迁移的 PostgreSQL 正式服。导入脚本复用 `CustomTagService`，不会修改帖子绑定、投票或历史治理状态。脚本需要服务器访问权限，`--actor-id` 是审计归属及配置白名单检查，不是独立的登录认证。

## 准备 JSON

格式见 [示例](custom-tags-import.example.json)。示例不是征集表正式数据。脚本只接收明确的 UTF-8 JSON，不猜测 Excel 中的别名、备注或上级含义。

- `version`：固定 `1`。
- `batch`：批次名，记录在新增标签的 `tag.pool.import` 审计中。同批次可重复执行，但不是凭批次名跳过；每次都核对数据库实际状态。
- `tags`：每项必须有唯一 `key`、标准 `name`、整数 `category`（1～7）。名称做 NFC 与首尾去空白规范化，同分类同名不能重复。
- `aliases`：可省略，表示保留已有别名、新标签不加别名。显式传列表则要求已有标签别名集合完全一致，否则整批阻止；不会覆盖或增补已有别名。`[]` 也参与一致性检查。
- `existing_id`：可选正 BIGINT 字符串，用于明确引用正式服已有标签（包括 DC 标签）。名称及分类必须与数据库一致，且启用、未删除。未分类 DC 标签需先通过管理功能分类再引用。所有关系端点都必须列入 `tags`。
- `description`：可选标签含义说明，纯文本、最多 2000 字，清理首尾空白并保留内部换行。新建时写入 `tag.description`。未传或为空不修改已有描述；已有描述为空则补入，相同则跳过，非空且不同则整批报告冲突。显式 `null` 不合法。
- `origin`、`notes`：可选原始资料。`origin` 出现在报告及新增/补描述的导入审计中；`notes` 仅保留在 JSON，不写入标签模型。
- `is_abyss` 不作为清单输入字段：`origin` 清理首尾空白后以 `深渊向TAG!` 开头即推导为深渊向，其他来源均为正常向。预检和执行报告会显示推导结果。
- `relations`：`source`、`target` 引用本文件的 `key`；`kind` 为 `implies`（子→父）或 `excludes`。重复边跳过，自环、含数据库已有边的包含循环都阻止导入。

默认按“自定义来源＋分类＋标准名”查找已有标签。软删除、停用、别名不一致都报告冲突。同名有效 DC 标签必须使用 `existing_id` 明确引用，不自动新建或合并。复用 DC 标签不会改变其来源，也不会使其成为帖子可手动添加的自定义标签。

复用实体（包括显式 `existing_id`）的 `is_abyss` 必须与 origin 推导结果一致；不一致会阻止整批导入，不会自动切换已有标签方向。新标签通过管理服务写入推导方向，因此例如 `深渊向TAG!A45:E45` 的“饲养”会导入为深渊向。

## Linux / Docker Compose 操作

以下指令在正式服项目根目录执行。以仓库 `docker-compose.yml` 的服务名、数据库名和挂载为准；自定义部署请调整。把已审阅的 JSON 上传为 `data/custom-tags-v1.json`。把命令中的 `你的DiscordID` 替换为 `config.json` 中 `bot_admin_user_ids` 的真实 ID。

1. 部署包含本脚本的新镜像。Dockerfile 已复制 `scripts` 和 `src`；单独上传宿主机脚本不会自动更新运行中的容器。若需要结构迁移，先按 [部署文档](custom-tags.md) 完成维护窗口、备份和迁移，再进行本次数据导入。导入器要求数据库 revision 与镜像 Alembic heads 完全一致。
2. 备份完整数据库。下面命令会生成时间戳文件；确认退出码为 0 且文件非空，再继续：

```bash
docker compose exec -T odysseia-postgres pg_dump -U odysseia -d odysseia -Fc > "data/pre-custom-tags-$(date +%Y%m%d-%H%M%S).dump"
```

3. 只读预检并保存报告：

```bash
docker compose exec -T odysseia-forum-api uv run python scripts/import_custom_tags.py --file /app/data/custom-tags-v1.json --actor-id 你的DiscordID --dry-run > data/custom-tags-preview.json
```

查看 `status`、`errors`、每个标签的 `is_abyss`、`create_count`、`reuse_count`、`relations_to_create`。冲突报告退出码为 2，输入、权限、连接等异常为 1，成功为 0。省略 `--dry-run` 和 `--apply` 时同样只预检。预检是数据库 READ ONLY 事务，不创建标签、不写审计、不消耗 ID 序列。

4. 修正冲突、重新预检通过后正式执行：

```bash
docker compose exec -T odysseia-forum-api uv run python scripts/import_custom_tags.py --file /app/data/custom-tags-v1.json --actor-id 你的DiscordID --apply > data/custom-tags-result.json
```

确认退出码为 0、`status` 为 `applied`。结果包含全部 key 对应的正式服 ID、条目操作及输入文件 SHA-256。务必保存输入文件和结果文件。导入器先持有与业务及 DC 同步共用的事务锁，再重新预检，全部标签和关系在一个事务中提交；数据冲突不写入，执行异常回滚。成功提交之前不会输出 `applied`。

该操作通常不必停 BOT/API，但整批写入持有独占 TAG 锁，会暂时阻塞其他标签操作，建议低峰执行。等待锁超过 15 秒或单条 SQL 超过 120 秒会失败，应检查服务负载后重试。

5. 再运行预检。相同文件应显示 `create_count: 0`、全部标签复用且 `relations_to_create: []`。也可通过标签池接口核对。重复执行 `--apply` 不新增重复实体、关系或无变化操作日志。

如果连接在提交阶段中断，客户端可能无法判断是否已提交：先对同一文件重新预检，核对结果后再决定是否重跑。不要根据终端断连直接认定回滚。事务回滚可能消耗序列号，ID 不连续属于正常现象。

## 回退

预检报告的 `description_updates` 展示待补描述条目的 key、ID、原表位置及前后值；`description_update_count` 为数量，`reuse_count` 包括这些复用实体。正式执行通过管理服务补写并记录批次审计，和标签、关系创建共用事务；重跑无变化时不重复写日志。成功导入后再预检，描述补写数量也应为 0。

使用本版导入器前需升级到 `add_abyss_tag_flag` 迁移。降级会依次丢失深渊向标识和描述内容，不可依靠重新升级恢复。

脚本不提供批次硬删除：上线后标签可能已被帖子/书单引用。执行失败由事务回滚；提交后若需要撤销，先核对结果中的新增 ID，通过管理接口停用/软删除，或在维护窗口使用完整备份恢复（完整恢复也会丢失备份之后其他业务变更）。复用的已有标签不可按本批次新增标签处理。
