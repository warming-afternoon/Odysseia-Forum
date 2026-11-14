# 作品公告与通知兼容

`GET /v2/works/{id}/announcements?offset=0&limit=20` 返回 `{items,total,offset,limit}`，limit 为 1–100。历史包含旧正式更新投影；`legacy_update_id` 唯一关联旧 ThreadUpdate，不补发历史通知。

手动发布公告在同一 PostgreSQL 事务中创建公告并用单条 INSERT SELECT 快照活跃关注者，排除发布者和发布后才关注的人。不会创建空通知任务。受众写入失败则公告一并回滚；每次发布独立通知，编辑不再通知，撤回保留公告和原通知的 read_at。旧正式更新已映射公告时，旧撤回操作也保留通知并显示已撤回。

`GET /v2/me/notifications` 返回共享 Notification 行，兼容 thread_update、author_new_thread、tag_review、work_announcement，保留 Page 字段并提供 unread_count。旧更新通过 legacy_update_id 映射公告，已有旧通知时不重复展示镜像通知。work_id/announcement_id 对不关联作品的书单审核可为空，target_type/target_id/proposal_id 用于审核目标。作品删除、来源隐藏、NSFW 和深渊过滤同步作用于列表、total 和 unread_count。标记已读更新原通知行，V1/V2 不复制已读事实。

V1 只展示真实 Discord 帖子或书单可表达的通知。作品公告适配成原 thread_update wire，不将 Fluxer thread_id 伪装成 Discord ID；过滤条件同样用于 total/unread-count。event_source_platform 将同雪花的 Discord/Fluxer 来源隔离，旧平台删除或按线程已读不会误伤原生记录。

`NotificationFanoutService.fanout_work_created(work_id, thread_id, author_id, platform, created_at)` 复用调用方 session，不提交事务。事件 snapshot.created_at 来自原生 ThreadState 创建时间（缺状态时取 Snowflake 时间），不是首次索引时间。只给 followed_at <= 实际创建时间的作者关注者写入通知，排作者并按平台来源幂等；重新索引历史作品不会给后来关注者补发通知。

## V2 通知接口和实时提示

- `GET /v2/me/notifications` 可选 `unread_only`、`author_id`、`offset`、`limit` 和 `cursor`；`author_id` 是作者的 Fluxer 统一用户 ID，不将同值 Discord ID 当作同一身份。`items` 包含已通过可见性过滤的 `work` 批量投影，书单审核可以没有作品。新客户端使用返回的 `next_cursor` 翻页，避免前方插入导致重复；旧 `offset` 调用仍兼容。
- `GET /v2/me/notifications/unread-count` 与列表使用同一可见性条件。`POST /v2/me/notifications/{id}/read`、`POST /v2/me/notifications/read-all` 均只影响当前用户可见、操作语句快照中已存在的未读通知；重复调用安全。
- `POST /v2/me/notifications/socket-ticket` 通过现有 V2 认证后签发 30 秒有效的一次性票据；票据不应缓存、记录或放进 URL。网关首帧为 `{"type":"authenticate","ticket":"…"}`，确认订阅后发送 `ready`；`notification.changed` 只是刷新提示，不承载通知正文。客户端在 ready、重连和窗口恢复时从 HTTP 校准。总线断线或消费者溢出时连接失效，不能认为每条提示必达。
- 新通知及 V2 已读操作的持久 outbox 记录与业务写入同事务；重试可能重复，接收方必须把提示视为幂等失效信号。已成功投递的 outbox 元数据可清理，**通知本身不自动过期**。
- 实时发布器由 `FORUM_NOTIFICATION_REALTIME_ENABLED=true` 启用；启用前须配置 NATS ACL，允许 Forum 发布、网关独立订阅 `events.forum.v1.notification.changed` 和认证失效主题。网关使用 `FORUM_NOTIFICATION_NATS_URL`、`FORUM_NOTIFICATION_REDIS_URL`、精确 `FORUM_NOTIFICATION_ORIGIN` 和连接数上限，默认只监听本机。不得把 Redis/NATS 凭据放入日志或客户端。

## 公告写入

公告作者及其 protect 直接授权的协作者可发布、编辑和撤回；当前管理员能力保留。协作者判定使用双方当前 Discord 绑定和 protect 实时授权，不可用或身份无法验证时拒绝，且不扩大其他作品管理权限。protect 可选只读服务默认关闭，仅监听本机；跨主机访问必须经受信的加密通道或 TLS 代理。两端分别配置同一个至少 32 字节的随机 `FORUM_COLLABORATOR_AUTH_SECRET`，Forum 的 `FORUM_COLLABORATOR_AUTH_URL` 必须为 HTTPS 或本机 HTTP，禁止把密钥暴露给浏览器。

`POST /v2/works/{id}/announcements` 可带 `Idempotency-Key`（1–128 位字母、数字、点、下划线、冒号或连字符）；同一用户、作品和键的相同请求重试返回同一公告，内容不同返回 409。编辑和撤回可带 `If-Match: "<revision>"`，版本不匹配返回 409；为兼容已有客户端，未传版本时仍可写入，但无法防止基于旧内容的覆盖，新客户端应始终传版本。重复撤回不新建通知或改变版本。返回的公告包含 `revision` 和最新编辑／撤回操作者信息。

票据入口在路由的强制权威认证前用 Redis 进行跨进程全局准入（现有认证中间件可能先执行缓存查询或 RPC，仍需独立限流和压测），默认每秒 256 次（`FORUM_NOTIFICATION_TICKETS_PER_SECOND`，允许 10–5000），超限返回 429 并要求客户端退避；单用户另有限速。40,000 个连接同时重建时仅按默认全局限额计算至少约 157 秒，实际还受退避和认证速度影响。它保护服务器，并非已承诺的恢复时间。

为保护小规格数据库，发布器默认每批最多 128 个接收者、批次间隔 1 秒（`FORUM_NOTIFICATION_BATCH_INTERVAL`，允许 0.1–10 秒）。这是**每进程**上限，并非全局限速：多个进程的速率会相加。单进程、40,000 个接收者的集中扇出仅按该参数估算约需 313 秒，具体延迟还取决于队列、网络和处理时间；普通零星通知不必等待整个大批次。上线前须结合进程数量、HTTP/数据库容量和可接受延迟调整，不应把它当成已验证的服务等级。

以上实时通道、迁移和授权接口必须先通过独立数据库、认证失效、断网恢复及目标规模压力测试，不能因为启用开关存在就认为部署或容量已验证。

迁移前须先对目标数据库只读检查 `(fluxer_user_id, event_type, event_source_id, event_source_platform)` 是否已有重复。新唯一索引遇到冲突会中止迁移，**不会自动删除或合并历史通知**；如有冲突先分析并经授权处理。索引创建、作品可见性触发器和大规模公告扇出均需在目标 PostgreSQL 版本和预估数据量下评估锁、写放大、计划与延迟，不能把空库测试当容量验证。
