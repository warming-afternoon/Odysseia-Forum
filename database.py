import aiosqlite
from ranking_config import RankingConfig

DB_PATH = "forum_search.db"

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS threads(
    thread_id INTEGER PRIMARY KEY,
    channel_id INTEGER,
    title TEXT,
    author_id INTEGER,
    created_at TEXT,
    last_active_at TEXT,
    reaction_count INTEGER DEFAULT 0,
    reply_count INTEGER DEFAULT 0,
    tags TEXT,
    first_message_excerpt TEXT,
    thumbnail_url TEXT
);

CREATE TABLE IF NOT EXISTS tags(
    tag_id INTEGER PRIMARY KEY,
    name TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS thread_tags(
    thread_id INTEGER,
    tag_id INTEGER,
    PRIMARY KEY(thread_id, tag_id)
);

CREATE TABLE IF NOT EXISTS tag_votes(
    user_id INTEGER,
    tag_id INTEGER,
    vote INTEGER,
    PRIMARY KEY(user_id, tag_id)
);

CREATE TABLE IF NOT EXISTS user_preferences(
    user_id INTEGER PRIMARY KEY,
    results_per_page INTEGER DEFAULT 6
);

CREATE TABLE IF NOT EXISTS user_search_preferences(
    user_id INTEGER PRIMARY KEY,
    include_authors TEXT,
    exclude_authors TEXT,
    after_date TEXT,
    before_date TEXT
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_TABLES_SQL)
        await db.commit()

async def add_or_update_thread(info: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO threads(thread_id, channel_id, title, author_id,
                            created_at, last_active_at, reaction_count,
                            reply_count, tags, first_message_excerpt,
                            thumbnail_url)
        VALUES(:thread_id, :channel_id, :title, :author_id,
               :created_at, :last_active_at, :reaction_count,
               :reply_count, :tags, :first_message_excerpt,
               :thumbnail_url)
        ON CONFLICT(thread_id) DO UPDATE SET
            last_active_at=excluded.last_active_at,
            reaction_count=excluded.reaction_count,
            reply_count=excluded.reply_count,
            tags=excluded.tags,
            first_message_excerpt=excluded.first_message_excerpt,
            thumbnail_url=excluded.thumbnail_url;
        """, info)
        await db.commit()

async def ensure_tag(tag_id: int, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO tags(tag_id, name) VALUES(?, ?)", (tag_id, name))
        await db.commit()
        return tag_id

async def link_thread_tag(thread_id: int, tag_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO thread_tags(thread_id, tag_id) VALUES(?, ?)", (thread_id, tag_id))
        await db.commit()

async def record_tag_vote(user_id: int, tag_id: int, vote: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO tag_votes(user_id, tag_id, vote)
        VALUES(?, ?, ?)
        ON CONFLICT(user_id, tag_id) DO UPDATE SET vote=excluded.vote;
        """, (user_id, tag_id, vote))
        await db.commit()

async def get_results_per_page(user_id: int) -> int:
    """返回用户设置的每页结果数量，默认 6"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT results_per_page FROM user_preferences WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 6

async def set_results_per_page(user_id: int, num: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_preferences(user_id, results_per_page) VALUES(?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET results_per_page = excluded.results_per_page",
            (user_id, num),
        )
        await db.commit()

async def get_tags_for_channel(channel_id: int):
    """获取频道内出现过的所有标签 (返回 List[(tag_id, name)])"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT tags.tag_id, tags.name FROM tags "
            "JOIN thread_tags ON thread_tags.tag_id = tags.tag_id "
            "JOIN threads ON threads.thread_id = thread_tags.thread_id "
            "WHERE threads.channel_id = ?",
            (channel_id,),
        ) as cursor:
            return await cursor.fetchall()

async def search_threads(
    include_tags: list[str],
    exclude_tags: list[str],
    keywords: str,
    channel_ids: list[int] | None,
    include_authors: list[int] | None,
    exclude_authors: list[int] | None,
    after_ts: str | None,
    before_ts: str | None,
    offset: int,
    limit: int,
    sort_method: str = "comprehensive"  # 新增参数：comprehensive, created_time, active_time, reaction_count
):
    """搜索帖子并按指定方式排序。

    排序方式：
    • comprehensive: 智能混合权重排序（时间+标签+反应）
    • created_time: 按发帖时间倒序
    • active_time: 按最近活跃时间倒序  
    • reaction_count: 按反应数倒序
    """
    conditions: list[str] = []
    params: list = []

    if channel_ids:
        placeholders = ",".join(["?"] * len(channel_ids))
        conditions.append(f"threads.channel_id IN ({placeholders})")
        params.extend(channel_ids)

    if include_authors:
        placeholders = ",".join(["?"] * len(include_authors))
        conditions.append(f"author_id IN ({placeholders})")
        params.extend(include_authors)
    if exclude_authors:
        placeholders = ",".join(["?"] * len(exclude_authors))
        conditions.append(f"author_id NOT IN ({placeholders})")
        params.extend(exclude_authors)

    if after_ts:
        conditions.append("last_active_at >= ?")
        params.append(after_ts)
    if before_ts:
        conditions.append("last_active_at <= ?")
        params.append(before_ts)

    if keywords:
        kw_like = f"%{keywords}%"
        conditions.append("(title LIKE ? OR first_message_excerpt LIKE ?)")
        params.extend([kw_like, kw_like])

    join_clause = " LEFT JOIN thread_tags tt ON tt.thread_id = threads.thread_id "\
                  "LEFT JOIN tags tg ON tg.tag_id = tt.tag_id "\
                  "LEFT JOIN tag_votes tv ON tv.tag_id = tg.tag_id "

    # 处理包含标签 (AND逻辑 - 必须同时拥有所有选中的标签)
    if include_tags:
        placeholders = ",".join(["?"] * len(include_tags))
        conditions.append(
            f"""threads.thread_id IN (
                SELECT thread_id FROM thread_tags it
                JOIN tags t2 ON t2.tag_id = it.tag_id
                WHERE t2.name IN ({placeholders})
                GROUP BY thread_id
                HAVING COUNT(DISTINCT t2.name) = ?
            )"""
        )
        params.extend(include_tags)
        params.append(len(include_tags))
    
    # 处理排除标签 (OR逻辑 - 包含任何一个排除标签就不显示)
    if exclude_tags:
        placeholders = ",".join(["?"] * len(exclude_tags))
        conditions.append(
            f"""threads.thread_id NOT IN (
                SELECT thread_id FROM thread_tags et
                JOIN tags t3 ON t3.tag_id = et.tag_id
                WHERE t3.name IN ({placeholders})
            )"""
        )
        params.extend(exclude_tags)

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    # 根据排序方式决定查询和处理逻辑
    if sort_method == "comprehensive":
        # 智能综合排序 - 需要计算权重
        if include_tags:
            inc_placeholders = ",".join(["?"] * len(include_tags))
            
            # 计算标签的总点赞数、总点踩数、总评价数
            tag_score_expr = f"""
            -- 计算选中标签的综合评分
            SUM(CASE WHEN tg.name IN ({inc_placeholders}) AND tv.vote = 1 THEN 1 ELSE 0 END) AS total_upvotes,
            SUM(CASE WHEN tg.name IN ({inc_placeholders}) AND tv.vote = -1 THEN 1 ELSE 0 END) AS total_downvotes,
            SUM(CASE WHEN tg.name IN ({inc_placeholders}) AND tv.vote IS NOT NULL THEN 1 ELSE 0 END) AS total_votes
            """
            tag_params = include_tags * 3  # 占用三次
        else:
            tag_score_expr = "0 AS total_upvotes, 0 AS total_downvotes, 0 AS total_votes"
            tag_params = []

        select_clause = f"""
        SELECT threads.*, 
               {tag_score_expr},
               -- 时间权重：指数衰减
               EXP(-? * (JULIANDAY('now') - JULIANDAY(last_active_at))) AS time_weight,
               -- 当前时间戳，用于调试
               JULIANDAY('now') - JULIANDAY(last_active_at) AS days_since_active
        FROM threads{join_clause}{where_clause} 
        GROUP BY threads.thread_id
        """

        all_params = tag_params + [RankingConfig.TIME_DECAY_RATE] + params

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(select_clause, all_params) as cursor:
                rows = await cursor.fetchall()
                
                # 在Python中计算最终评分并排序
                scored_rows = []
                for row in rows:
                    row_dict = dict(row)
                    
                    # 计算标签权重 (Wilson Score Lower Bound)
                    upvotes = row_dict['total_upvotes']
                    downvotes = row_dict['total_downvotes']
                    total_votes = row_dict['total_votes']
                    
                    if total_votes > 0:
                        # Wilson Score Lower Bound算法
                        # 更保守的评估，考虑样本量的置信度
                        positive_rate = upvotes / total_votes
                        z = RankingConfig.WILSON_CONFIDENCE_LEVEL  # 置信区间
                        n = total_votes
                        
                        # Wilson Score公式
                        wilson_center = (positive_rate + z*z/(2*n)) / (1 + z*z/n)
                        wilson_radius = z * ((positive_rate*(1-positive_rate) + z*z/(4*n)) / n)**0.5 / (1 + z*z/n)
                        tag_weight = max(0, wilson_center - wilson_radius)  # 下界，更保守
                    else:
                        # 无评价时给予默认评分
                        tag_weight = RankingConfig.DEFAULT_TAG_SCORE
                    
                    # 时间权重 (已在SQL中计算)
                    time_weight = row_dict['time_weight']
                    
                    # 反应权重 (基于对数归一化)
                    reaction_count = row_dict['reaction_count']
                    import math
                    if reaction_count > 0:
                        # 使用对数函数归一化：log(reactions + 1) / log(base + 1)
                        reaction_weight = min(
                            RankingConfig.MAX_REACTION_SCORE,
                            math.log(reaction_count + 1) / math.log(RankingConfig.REACTION_LOG_BASE + 1)
                        )
                    else:
                        reaction_weight = 0.0
                    
                    # 综合评分：使用配置的权重因子
                    time_factor = RankingConfig.TIME_WEIGHT_FACTOR
                    tag_factor = RankingConfig.TAG_WEIGHT_FACTOR
                    reaction_factor = RankingConfig.REACTION_WEIGHT_FACTOR
                    base_score = time_factor * time_weight + tag_factor * tag_weight + reaction_factor * reaction_weight
                    
                    # 恶评惩罚：如果标签评分过低，额外降权
                    if tag_weight < RankingConfig.SEVERE_PENALTY_THRESHOLD and total_votes >= RankingConfig.SEVERE_PENALTY_MIN_VOTES:
                        penalty_factor = RankingConfig.SEVERE_PENALTY_FACTOR
                        final_score = base_score * penalty_factor
                    elif tag_weight < RankingConfig.MILD_PENALTY_THRESHOLD and total_votes >= RankingConfig.MILD_PENALTY_MIN_VOTES:
                        penalty_factor = RankingConfig.MILD_PENALTY_FACTOR
                        final_score = base_score * penalty_factor
                    else:
                        final_score = base_score
                    
                    # 添加评分信息到结果中
                    row_dict['final_score'] = final_score
                    row_dict['tag_weight'] = tag_weight
                    row_dict['time_weight'] = time_weight
                    row_dict['reaction_weight'] = reaction_weight
                    
                    scored_rows.append(row_dict)
                
                # 按最终评分降序排序
                scored_rows.sort(key=lambda x: x['final_score'], reverse=True)
                
                # 应用分页
                start_idx = offset
                end_idx = offset + limit
                
                return scored_rows[start_idx:end_idx]
    
    else:
        # 简单排序方式 - 直接在SQL中排序
        select_clause = f"SELECT threads.* FROM threads{join_clause}{where_clause} GROUP BY threads.thread_id"
        
        # 根据排序方式添加ORDER BY子句
        if sort_method == "created_time":
            order_clause = " ORDER BY datetime(created_at) DESC"
        elif sort_method == "active_time":
            order_clause = " ORDER BY datetime(last_active_at) DESC"
        elif sort_method == "reaction_count":
            order_clause = " ORDER BY reaction_count DESC, datetime(last_active_at) DESC"
        else:
            # 默认按活跃时间排序
            order_clause = " ORDER BY datetime(last_active_at) DESC"
        
        # 添加分页
        order_clause += " LIMIT ? OFFSET ?"
        
        final_query = select_clause + order_clause
        all_params = params + [limit, offset]

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(final_query, all_params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

async def count_threads_for_search(
    include_tags: list[str],
    exclude_tags: list[str],
    keywords: str,
    channel_ids: list[int] | None,
    include_authors: list[int] | None,
    exclude_authors: list[int] | None,
    after_ts: str | None,
    before_ts: str | None,
):
    """统计搜索结果总数"""
    conditions = []
    params = []

    if channel_ids:
        placeholders = ",".join(["?"] * len(channel_ids))
        conditions.append(f"threads.channel_id IN ({placeholders})")
        params.extend(channel_ids)

    if include_authors:
        placeholders = ",".join(["?"] * len(include_authors))
        conditions.append(f"author_id IN ({placeholders})")
        params.extend(include_authors)
    if exclude_authors:
        placeholders = ",".join(["?"] * len(exclude_authors))
        conditions.append(f"author_id NOT IN ({placeholders})")
        params.extend(exclude_authors)

    if after_ts:
        conditions.append("last_active_at >= ?")
        params.append(after_ts)
    if before_ts:
        conditions.append("last_active_at <= ?")
        params.append(before_ts)

    if keywords:
        kw_like = f"%{keywords}%"
        conditions.append("(title LIKE ? OR first_message_excerpt LIKE ?)")
        params.extend([kw_like, kw_like])

    # 处理包含标签 (AND逻辑 - 必须同时拥有所有选中的标签)
    if include_tags:
        placeholders = ",".join(["?"] * len(include_tags))
        conditions.append(
            f"""threads.thread_id IN (
                SELECT thread_id FROM thread_tags it
                JOIN tags t2 ON t2.tag_id = it.tag_id
                WHERE t2.name IN ({placeholders})
                GROUP BY thread_id
                HAVING COUNT(DISTINCT t2.name) = ?
            )"""
        )
        params.extend(include_tags)
        params.append(len(include_tags))
    
    # 处理排除标签 (OR逻辑 - 包含任何一个排除标签就不显示)
    if exclude_tags:
        placeholders = ",".join(["?"] * len(exclude_tags))
        conditions.append(
            f"""threads.thread_id NOT IN (
                SELECT thread_id FROM thread_tags et
                JOIN tags t3 ON t3.tag_id = et.tag_id
                WHERE t3.name IN ({placeholders})
            )"""
        )
        params.extend(exclude_tags)

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    query = f"SELECT COUNT(*) FROM threads{where_clause}"

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query, params) as cursor:
            (cnt,) = await cursor.fetchone()
            return cnt

async def get_tag_vote_stats(thread_id: int):
    """返回该线程各标签的投票统计 (tag_name, up, down, total)"""
    query = """
    SELECT tags.name,
           SUM(CASE WHEN vote = 1 THEN 1 ELSE 0 END) AS up_votes,
           SUM(CASE WHEN vote = -1 THEN 1 ELSE 0 END) AS down_votes,
           COALESCE(SUM(vote), 0) AS total_score
    FROM thread_tags
    JOIN tags ON tags.tag_id = thread_tags.tag_id
    LEFT JOIN tag_votes ON tag_votes.tag_id = tags.tag_id
    WHERE thread_tags.thread_id = ?
    GROUP BY tags.name
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query, (thread_id,)) as cursor:
            return await cursor.fetchall()

async def get_user_search_preferences(user_id: int):
    """获取用户搜索偏好"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT include_authors, exclude_authors, after_date, before_date FROM user_search_preferences WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    'include_authors': [int(x) for x in row[0].split(',') if x] if row[0] else [],
                    'exclude_authors': [int(x) for x in row[1].split(',') if x] if row[1] else [],
                    'after_date': row[2],
                    'before_date': row[3]
                }
            return {
                'include_authors': [],
                'exclude_authors': [],
                'after_date': None,
                'before_date': None
            }

async def save_user_search_preferences(user_id: int, include_authors: list[int], exclude_authors: list[int], after_date: str | None, before_date: str | None):
    """保存用户搜索偏好"""
    async with aiosqlite.connect(DB_PATH) as db:
        include_str = ','.join(map(str, include_authors)) if include_authors else ''
        exclude_str = ','.join(map(str, exclude_authors)) if exclude_authors else ''
        
        await db.execute("""
            INSERT INTO user_search_preferences(user_id, include_authors, exclude_authors, after_date, before_date)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                include_authors = excluded.include_authors,
                exclude_authors = excluded.exclude_authors,
                after_date = excluded.after_date,
                before_date = excluded.before_date
        """, (user_id, include_str, exclude_str, after_date, before_date))
        await db.commit()

async def get_thread_basic_info(thread_id: int):
    """获取帖子的基本信息（用于避免重复抓取首楼内容）"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT first_message_excerpt, thumbnail_url, reaction_count FROM threads WHERE thread_id = ?",
            (thread_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else {}

async def delete_thread_index(thread_id: int):
    """删除帖子的所有相关索引数据"""
    async with aiosqlite.connect(DB_PATH) as db:
        # 删除帖子记录
        await db.execute("DELETE FROM threads WHERE thread_id = ?", (thread_id,))
        # 删除标签关联
        await db.execute("DELETE FROM thread_tags WHERE thread_id = ?", (thread_id,))
        # 删除标签投票（如果需要的话，也可以保留投票历史）
        # await db.execute("DELETE FROM tag_votes WHERE tag_id IN (SELECT tag_id FROM thread_tags WHERE thread_id = ?)", (thread_id,))
        await db.commit()

async def get_indexed_channel_ids():
    """获取所有已索引的频道ID列表"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT DISTINCT channel_id FROM threads") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]