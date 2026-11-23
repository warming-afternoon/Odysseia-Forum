# Forum 作品互动、公告与规则：PostgreSQL 表变更草案

供审阅。基线是 Forum `dev-new` 的 `bf046f7`，已含 `platform_integration`。下列升级 SQL 是相对于该版本的增量。审定后并入测试库的同一份 Alembic 迁移。已打过同名版本的测试库需要重建或显式运行增量；单纯执行 `upgrade head` 不会重跑旧版本。本稿不用于正式服 V1 迁移。

## 先看数据归属

| 业务 | 表设计 | 选择原因 |
| --- | --- | --- |
| 关注与主动取关 | 扩展 `thread_follow` | 继续使用已有的作品关注唯一键、关注时间和最后查看时间。显式取关保留记录，交流区每次新发言都可以触发判断。 |
| 自动关注失败重试 | 复用 `platform_outbox` 记录本地任务 | 它已有唯一事件 ID、重试次数和下次执行时间。增加本地处理分支，此任务不能投递到事件总线。 |
| 点赞 | 新增 `work_like`；扩展 `work_statistics` | 明细回答“我点过吗”，统计服务作品列表。Discord 同步只改来源反应数。 |
| 快捷收藏 | 沿用 `booklist`、`booklist_item`、`work_statistics.collection_count` | 快捷操作就是添加或移除默认书单条目。与 Bot、书单页共用同一份数据。 |
| 作品公告 | 扩展 `work_announcement`；锁作品行后查询最大序号 | 同一部作品每发布一条公告就分配一个序号。已有 `revision` 继续表示单条公告被编辑的版本。 |
| 作品规则 | 扩展 `work` | 每部作品保存一份当前规则、规则版本和更新时间。本期不保存每次修改的全文。 |
| 读者确认 | 新增 `work_ack` | 每个用户、作品、内容类型只存最新确认序号。未关注作品的读者也能确认。 |
| 规则通知 | 扩展 `notification`；沿用 `work_notification_outbox` | 一次规则更新对应一个全局来源编号，另存作品内规则版本以精确标已读。 |

共新增两张表。新表的 `user_id` 是平台用户 ID（对应 `user.id`），`work_id` 对应 `work.id`，仅逻辑关联。旧表中的 `fluxer_user_id` 沿用当前平台 ID 的物理列名；旧 `thread_follow.user_id` 和 `notification.user_id` 均表示 Discord ID。以下新增关系均不建立实体外键。

## 升级 SQL

适用于独立测试库。整个 DDL 块在一个事务内执行，回填期间会阻塞相关表的并发写入。时间列沿用 Forum 业务表的 UTC 无时区时间；本节包含给后续 SQLModel 同步的数据库默认值。

```sql
BEGIN;

ALTER TABLE thread_follow
    ADD COLUMN manual_unfollowed_at timestamp without time zone;
COMMENT ON COLUMN thread_follow.manual_unfollowed_at IS '最近一次主动取消关注的 UTC 时间；手动重新关注后保留，自动关注据此跳过';

CREATE TABLE work_like (
    work_id bigint NOT NULL,
    user_id varchar NOT NULL,
    created_at timestamp without time zone NOT NULL DEFAULT (now() AT TIME ZONE 'UTC'),
    PRIMARY KEY (work_id, user_id)
);
COMMENT ON TABLE work_like IS '用户当前对作品的平台点赞；取消点赞时移除明细';
ALTER TABLE work_statistics
    ADD COLUMN platform_like_count bigint NOT NULL DEFAULT 0,
    ADD CONSTRAINT ck_work_statistics_platform_likes CHECK (platform_like_count >= 0);

ALTER TABLE work
    ADD COLUMN rules_body text NOT NULL DEFAULT '',
    ADD COLUMN rules_version bigint NOT NULL DEFAULT 0,
    ADD COLUMN rules_updated_at timestamp without time zone,
    ADD CONSTRAINT ck_work_rules_state CHECK (
        (rules_version = 0 AND rules_body = '' AND rules_updated_at IS NULL)
        OR (rules_version > 0 AND rules_updated_at IS NOT NULL)
    );
COMMENT ON COLUMN work.rules_version IS '0 表示从未保存；之后每次成功保存作品规则递增一次';

ALTER TABLE work_announcement ADD COLUMN version bigint;
-- 历史旧帖投影、已撤回公告也参与编号；仅更新元数据，不产生通知。
WITH numbered AS (
    SELECT id, row_number() OVER (PARTITION BY work_id ORDER BY created_at, id) AS seq
    FROM work_announcement
)
UPDATE work_announcement AS a SET version = n.seq
FROM numbered AS n WHERE a.id = n.id;

ALTER TABLE work_announcement
    ALTER COLUMN version SET NOT NULL,
    ADD CONSTRAINT ck_work_announcement_version CHECK (version > 0),
    ADD CONSTRAINT uq_work_announcement_version UNIQUE (work_id, version);

-- 分配放在 INSERT 触发器，兼容旧公告服务、历史投影和新平台写入。
CREATE FUNCTION assign_work_announcement_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM id FROM work WHERE id = NEW.work_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION '作品 % 不存在，无法分配公告序号', NEW.work_id;
    END IF;
    SELECT coalesce(max(a.version), 0) + 1 INTO NEW.version
    FROM work_announcement AS a WHERE a.work_id = NEW.work_id;
    RETURN NEW;
END;
$$;
CREATE TRIGGER assign_work_announcement_version
    BEFORE INSERT ON work_announcement
    FOR EACH ROW EXECUTE FUNCTION assign_work_announcement_version();

CREATE TABLE work_ack (
    user_id varchar NOT NULL,
    work_id bigint NOT NULL,
    kind varchar NOT NULL CHECK (kind IN ('announcement', 'rules')),
    confirmed_version bigint NOT NULL CHECK (confirmed_version > 0),
    PRIMARY KEY (work_id, user_id, kind)
);
COMMENT ON COLUMN work_ack.confirmed_version IS '用户已确认的公告序号或规则版本；写入只能推进';

-- 通知类型在应用写入入口统一校验。实施时同步移除 ORM 中同名 CHECK。
ALTER TABLE notification DROP CONSTRAINT ck_notification_event_type;
ALTER TABLE notification ADD COLUMN rules_version bigint;
-- 通知写入服务统一校验：规则提醒须有 work_id 和正整数 rules_version；
-- 其他类型的 rules_version 应为空。
CREATE SEQUENCE work_rules_notice_source_seq AS bigint;
COMMENT ON SEQUENCE work_rules_notice_source_seq IS '每次规则保存取一个通知来源 ID，所有接收者共用；序号间隙无业务含义';
CREATE INDEX ix_notification_unread_work_rules
    ON notification (fluxer_user_id, work_id, rules_version)
    WHERE event_type = 'work_rules' AND read_at IS NULL;

COMMIT;
```

## 写入约定与 SQL 例子

以下为应用事务片段，`:参数` 由调用方绑定。

**关注与重试。** 每次收到新的 `chat.message_created` 都允许触发自动关注。用户之前关闭开关发过言，打开后再次发言即可自动关注。已关注、明确主动取关、作者本人或作品不可用时跳过。沿用 `UNIQUE (fluxer_user_id, work_id)` 保证关系唯一；事件消费按事件 ID 去重。消息恢复不触发。无需保存用户发言次数或时间。

手动取关可以先插入非活跃行，显式设置 `active_flag=false` 并满足旧表必填的 `followed_at`；实际关注时重新写入关注时间。历史非活跃行无法证明是用户主动取消，取关标记保留 NULL。账号合并保留任一来源的主动取关记录。符合候选条件的新消息与其自动关注任务在消费事务内记录，新增点赞与其任务在点赞事务内记录。worker 对账号偏好查询失败退避重试，写入前对用户和作品重新检查关注和主动取关状态。先部署识别本地任务的 worker，再启用生产任务的入口，避免把任务按普通事件发布到 NATS。

**点赞与收藏。** `INSERT INTO work_like ... ON CONFLICT DO NOTHING RETURNING work_id` 或 `DELETE ... RETURNING work_id` 实际返回一行时，才在同事务给 `platform_like_count` 加一或减一；缺统计行时使用 upsert。同步写入已有每日增长指标时使用实际增减量。无历史平台点赞明细，初始值为零；不从默认书单或 Discord 汇总数猜点赞人。作品对外赞数为平台点赞数加 Discord 来源作品的原帖反应统计。重建时仅按明细重算 `platform_like_count`。收藏继续由默认书单条目写入路径维护现有计数。

**公告。** 所有创建路径由数据库统一分配序号；应用不自行计算 `MAX+1`。并发发布同作品时只争用对应作品行。撤回、修改正文和图片均不重新编号。在默认 READ COMMITTED 下先取得作品行锁，再执行独立的 MAX 查询；唯一约束兜底。采用更高隔离级别时应支持整笔事务冲突重试。对外返回最新公告时从实际公告记录取序号；确认请求校验该序号确实属于这部作品。旧历史回填如在本次迁移后重跑，新增行按实际入库次序取得后续序号。

**规则发布。** 同事务锁住作品行、保存规则、给操作者确认、快照符合条件的关注者并写 Forum 本地通知及其 WS outbox。使用返回的规则版本，给这一批接收者共用同一个来源 ID：

```sql
UPDATE work
SET rules_body = :body,
    rules_version = rules_version + 1,
    rules_updated_at = now() AT TIME ZONE 'UTC',
    updated_at = now() AT TIME ZONE 'UTC'
WHERE id = :work_id
RETURNING rules_version;

SELECT nextval('work_rules_notice_source_seq') AS event_source_id;
```

本期沿用现有通知去重键。每次规则保存只调用一次 `nextval`，所有接收者和同一次扇出的各批次复用该值。事务回滚后重跑可以领取新编号，因为旧编号对应的通知也已回滚。事务提交后，WS 重投只处理已经持久化的 outbox 记录，不再次领取来源编号或创建通知。

规则通知写入 `event_type='work_rules'`、`event_source_platform='forum'`、上述 `event_source_id`、`work_id` 和 `rules_version`。现有通知唯一键含接收人、事件类型、来源编号和来源平台，**不含作品 ID**；因此不能直接把每部作品都会出现的“第 1 版”当来源编号。受众写入复用现有本地扇出和 WS 刷新机制。本期没有 Core 通知待发送任务。

**确认与消除提醒。** 先校验作品可见性并锁定同一作品行，再验证请求确认的序号有效。规则确认进度与标已读在同事务内更新。示例用于具有平台收件人 ID 的本期规则通知：

```sql
INSERT INTO work_ack (user_id, work_id, kind, confirmed_version)
VALUES (:user_id, :work_id, 'rules', :seen_version)
ON CONFLICT (work_id, user_id, kind) DO UPDATE
SET confirmed_version = EXCLUDED.confirmed_version
WHERE work_ack.confirmed_version < EXCLUDED.confirmed_version;

WITH marked AS (
    UPDATE notification
    SET read_at = now() AT TIME ZONE 'UTC'
    WHERE fluxer_user_id = :user_id AND work_id = :work_id
      AND event_type = 'work_rules' AND read_at IS NULL
      AND rules_version <= :seen_version
    RETURNING fluxer_user_id
)
INSERT INTO work_notification_outbox (recipient_id)
SELECT DISTINCT fluxer_user_id FROM marked;
```

“全部已读”仅更新通知表，不写 `work_ack`。从未设置规则时版本为 0，不要求确认。公告确认复用该表的 `announcement` 类型。发布、确认与永久删除使用一致的作品锁顺序；永久删除作品时显式清理点赞、确认、关注及规则通知，软删除时保留。平台账号合并时点赞按作品去重、确认取最大版本，并校准受影响的计数。

## 回填检查与回退

升级前检查逻辑关联，以下查询应返回零行。有孤立公告时先核实来源，避免迁移后出现无法定位作品的历史序号。

```sql
SELECT a.id, a.work_id FROM work_announcement a
LEFT JOIN work w ON w.id = a.work_id WHERE w.id IS NULL;
```

升级后核对每部作品的公告序号，以及平台点赞明细与汇总，以下查询均应返回零行：

```sql
SELECT a.work_id FROM work_announcement a
GROUP BY a.work_id
HAVING min(a.version) < 1
    OR count(*) <> count(DISTINCT a.version);

WITH likes AS (SELECT work_id, count(*) AS n FROM work_like GROUP BY work_id)
SELECT coalesce(s.work_id, l.work_id) AS work_id
FROM work_statistics s FULL JOIN likes l USING (work_id)
WHERE coalesce(s.platform_like_count, 0) <> coalesce(l.n, 0);
```

回退 SQL 仅用于可丢弃的独立测试库。执行前停止新写入和自动关注 worker；本期已产生的点赞、确认、规则内容、主动取关及规则提醒将丢失。需保留测试数据时改为恢复升级前快照。下例约定自动关注本地任务名为 `auto_follow`，已有 Forum 公告与其他通知保留。

```sql
BEGIN;
DELETE FROM platform_outbox WHERE kind = 'auto_follow';
DELETE FROM notification WHERE event_type = 'work_rules';
DROP INDEX ix_notification_unread_work_rules;
ALTER TABLE notification DROP COLUMN rules_version;
-- 回退到原始 schema 时恢复旧版已有的枚举检查。
ALTER TABLE notification ADD CONSTRAINT ck_notification_event_type CHECK (
    event_type IN ('thread_update','author_new_thread','tag_review','work_announcement')
);
DROP SEQUENCE work_rules_notice_source_seq;
DROP TABLE work_ack;
DROP TRIGGER assign_work_announcement_version ON work_announcement;
DROP FUNCTION assign_work_announcement_version();
ALTER TABLE work_announcement DROP COLUMN version;
ALTER TABLE work DROP CONSTRAINT ck_work_rules_state,
    DROP COLUMN rules_body, DROP COLUMN rules_version, DROP COLUMN rules_updated_at;
ALTER TABLE work_statistics DROP COLUMN platform_like_count;
DROP TABLE work_like;
ALTER TABLE thread_follow DROP COLUMN manual_unfollowed_at;
COMMIT;
```
