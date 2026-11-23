"""平台绑定的单调投影，以及 V1 来源数据的归属。"""

from sqlalchemy import text


async def apply_bindings(session, snapshot):
    """按平台用户串行应用完整快照；保留 Discord 历史作者与书单。"""
    uid = snapshot["userId"]
    version = int(snapshot["version"])
    current = snapshot["currentDiscordId"]
    history = snapshot["historicalDiscordIds"]
    if not isinstance(history, list) or not uid or version < 0:
        raise ValueError("无效绑定快照")
    ids = set(history)
    if current is not None:
        ids.add(current)
    if any(
        not isinstance(k, str)
        or not k.isascii()
        or not k.isdigit()
        or not 0 < int(k) < 2**63
        for k in ids
    ):
        raise ValueError("无效 Discord ID")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:id,0))"),
        {"id": "platform-binding:" + uid},
    )
    params = {"id": uid, "version": version}
    old = (
        await session.execute(
            text("SELECT version FROM platform_binding_version WHERE user_id=:id"),
            params,
        )
    ).scalar_one_or_none()
    if old is not None and old > version:
        return
    # 权威快照只提供平台 ID；展示名称由账号模块提供。
    await session.execute(
        text("""
        INSERT INTO "user"(id,username,updated_at,show_nsfw)
        VALUES(:id,'',now(),false) ON CONFLICT(id) DO NOTHING"""),
        params,
    )
    # 快照删除的绑定不能用于认领未来业务。已归属的历史私有数据不随重新分配转移。
    await session.execute(
        text(
            "DELETE FROM discord_identity WHERE user_id=:id AND NOT(discord_user_id=ANY(CAST(:ids AS text[])))"
        ),
        {**params, "ids": sorted(ids)},
    )
    await session.execute(
        text(
            "UPDATE discord_identity SET is_current=false WHERE user_id=:id AND is_current"
        ),
        params,
    )
    for discord in sorted(ids):
        stored = await session.execute(
            text("""
            INSERT INTO discord_identity AS d(discord_user_id,user_id,is_current,version)
            VALUES(:discord,:id,:current,:version) ON CONFLICT(discord_user_id) DO UPDATE
            SET is_current=excluded.is_current,version=excluded.version WHERE d.user_id=excluded.user_id
            RETURNING d.user_id"""),
            {**params, "discord": discord, "current": discord == current},
        )
        if stored.scalar_one_or_none() is None:
            raise RuntimeError("等待原账户绑定快照确认释放")
    await session.execute(
        text("""
        INSERT INTO platform_binding_version(user_id,version) VALUES(:id,:version)
        ON CONFLICT(user_id) DO UPDATE SET version=excluded.version"""),
        params,
    )
    if not ids:
        return
    params["ids"] = sorted(map(int, ids))
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": "default-booklist:" + uid},
    )
    defaults = list(
        (
            await session.execute(
                text("""
        SELECT id FROM booklist WHERE is_default AND (fluxer_owner_id=:id OR
        (fluxer_owner_id IS NULL AND owner_id=ANY(CAST(:ids AS bigint[]))))
        ORDER BY (fluxer_owner_id IS NOT NULL) DESC,id FOR UPDATE"""),
                params,
            )
        ).scalars()
    )
    if len(defaults) > 1:
        # 保留各来源书单和备注，平台默认收藏夹取并集。
        merge = {"keeper": defaults[0], "others": defaults[1:], "uid": uid}
        await session.execute(
            text("""
            UPDATE booklist SET is_default=false,is_public=false
            WHERE id=ANY(CAST(:others AS bigint[]))"""),
            merge,
        )
        await session.execute(
            text("""
            INSERT INTO booklist_item(booklist_id,owner_id,fluxer_owner_id,work_id,thread_id,
                display_order,comment,created_at,updated_at)
            SELECT :keeper,owner_id,:uid,work_id,thread_id,display_order,comment,created_at,updated_at
            FROM booklist_item WHERE booklist_id=ANY(CAST(:others AS bigint[]))
            ORDER BY created_at,id ON CONFLICT DO NOTHING"""),
            merge,
        )
        await session.execute(
            text("""
            UPDATE booklist SET item_count=(SELECT count(*) FROM booklist_item WHERE booklist_id=:keeper)
            WHERE id=:keeper"""),
            merge,
        )
    # 仅认领尚无平台归属的行；历史 Discord 字段始终保留用于审计。
    for table, column, dest in (
        ("author", "id", "fluxer_user_id"),
        ("thread", "author_id", "fluxer_author_id"),
        ("booklist", "owner_id", "fluxer_owner_id"),
    ):
        source = " AND platform='discord'" if table == "thread" else ""
        await session.execute(
            text(
                f"UPDATE {table} SET {dest}=:id WHERE {column}=ANY(CAST(:ids AS bigint[])) AND {dest} IS NULL{source}"
            ),
            params,
        )
    await session.execute(
        text("""
        UPDATE booklist_item i SET fluxer_owner_id=b.fluxer_owner_id FROM booklist b
        WHERE b.id=i.booklist_id AND b.fluxer_owner_id=:id AND i.fluxer_owner_id IS NULL"""),
        params,
    )
    await session.execute(
        text("""
        UPDATE work w SET author_id=:id FROM thread t WHERE w.source_platform='discord'
        AND t.platform='discord' AND t.work_id=w.id AND t.author_id=ANY(CAST(:ids AS bigint[]))
        AND w.source_thread_id=t.thread_id::text AND w.author_id IS NULL"""),
        params,
    )
    # 两端都获得权威绑定后认领作者关注；历史账号之间的自关注仅保留来源行。
    await session.execute(
        text("""
        WITH pairs AS (
            SELECT DISTINCT ON (u.user_id,a.user_id) f.id,u.user_id AS follower,a.user_id AS followed
            FROM author_follow f JOIN discord_identity u ON u.discord_user_id=f.user_id::text
            JOIN discord_identity a ON a.discord_user_id=f.author_id::text
            WHERE (f.user_id=ANY(CAST(:ids AS bigint[])) OR f.author_id=ANY(CAST(:ids AS bigint[])))
            AND f.fluxer_user_id IS NULL AND f.fluxer_author_id IS NULL AND u.user_id<>a.user_id
            ORDER BY u.user_id,a.user_id,f.active_flag DESC,f.followed_at DESC,f.id
        ) UPDATE author_follow f SET fluxer_user_id=p.follower,fluxer_author_id=p.followed FROM pairs p
        WHERE f.id=p.id AND NOT EXISTS (SELECT 1 FROM author_follow d
            WHERE d.fluxer_user_id=p.follower AND d.fluxer_author_id=p.followed)"""),
        params,
    )
    # 同一目标只选择一条历史记录作为平台投影；重复的 V1 原行继续保留。
    for table, target in (
        ("user_collection", "target_type,target_id"),
        ("thread_follow", "work_id"),
    ):
        keys = target.split(",")
        match = " AND ".join(f"dest.{k}=candidate.{k}" for k in keys)
        complete = " AND ".join(f"{k} IS NOT NULL" for k in keys)
        await session.execute(
            text(f"""
            WITH candidate AS (
                SELECT DISTINCT ON ({target}) id,{target} FROM {table}
                WHERE user_id=ANY(CAST(:ids AS bigint[])) AND fluxer_user_id IS NULL AND {complete}
                ORDER BY {target},{"active_flag DESC," if table == "thread_follow" else ""} id)
            UPDATE {table} r SET fluxer_user_id=:id FROM candidate WHERE candidate.id=r.id
            AND NOT EXISTS(SELECT 1 FROM {table} dest WHERE dest.fluxer_user_id=:id AND {match})"""),
            params,
        )
