# migrate.py
import sqlite3
import sys
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# --- 配置 ---
# 使用 pathlib 确保跨平台路径兼容性
PROJECT_ROOT = Path(__file__).parent.resolve()
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "database.db"
CACHE_PATH = DATA_DIR / "username_cache.json"
# --- 结束配置 ---


# 用于在终端输出彩色文本的辅助函数
def print_color(text, color_code):
    """在终端打印彩色文本"""
    print(f"\033[{color_code}m{text}\033[0m")


def print_info(message):
    print_color(f"ℹ️  {message}", "94")  # Blue


def print_success(message):
    print_color(f"✅ {message}", "92")  # Green


def print_warning(message):
    print_color(f"⚠️  {message}", "93")  # Yellow


def print_error(message):
    print_color(f"❌ {message}", "91")  # Red


def adapt_datetime(dt_obj):
    """将 datetime 对象转换为 'YYYY-MM-DD HH:MM:SS.ffffff' 格式的字符串，以匹配 SQLAlchemy 的默认格式。"""
    return dt_obj.strftime("%Y-%m-%d %H:%M:%S.%f")


def parse_iso_datetime_with_timezone(s):
    """解析带 '+00:00' 时区后缀的日期时间字符串"""
    # Python 3.11+ 的 fromisoformat可以直接处理 'Z' 和 '+00:00'
    # 为了兼容性，我们手动处理
    try:
        # 移除可能存在的时区信息，因为 sqlite3 会存储 naive datetime
        s_str = s.decode("utf-8")
        if "+" in s_str:
            return datetime.fromisoformat(s_str.split("+")[0])
        return datetime.fromisoformat(s_str)
    except (ValueError, TypeError):
        return None


def migrate_favorites_from_follow_bot():
    """将旧 follow_bot.db 的 thread_favorites 迁移到当前系统的默认书单 (BooklistItem)"""
    old_db_path = DATA_DIR / "follow_bot.db"
    if not old_db_path.exists():
        print_info("未找到旧的收藏数据库 (follow_bot.db)，跳过数据迁移。")
        return

    print_info("=" * 50)
    print_info("检测到旧的收藏数据库，开始迁移数据...")

    batch_size = 100  # 每次从旧库分批读取的记录数
    total_attempted = 0  # 旧库中尝试迁移的总记录数
    total_migrated = 0  # 成功迁移（非重复）的记录数
    old_conn = None  # 旧数据库连接
    main_conn = None  # 主数据库连接

    try:
        sqlite3.register_adapter(datetime, adapt_datetime)
        sqlite3.register_converter("timestamp", parse_iso_datetime_with_timezone)

        old_conn = sqlite3.connect(
            old_db_path,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        old_cursor = old_conn.cursor()

        main_conn = sqlite3.connect(DB_PATH)
        main_cursor = main_conn.cursor()

        # Step 1: 查询旧库中所有收藏用户 ID
        old_cursor.execute("SELECT DISTINCT user_id FROM thread_favorites")
        old_user_ids = [row[0] for row in old_cursor.fetchall()]
        if not old_user_ids:
            print_info("旧数据库中没有收藏记录，跳过数据迁移。")
            return
        print_info(f"旧数据库中共有 {len(old_user_ids)} 个不同的用户有收藏记录。")

        # Step 2: 批量查询这些用户在当前库中的默认书单
        placeholders = ",".join("?" * len(old_user_ids))
        main_cursor.execute(
            f"SELECT id, owner_id FROM booklist WHERE owner_id IN ({placeholders}) AND is_default = 1",
            old_user_ids,
        )
        existing_booklists = {
            row[1]: row[0] for row in main_cursor.fetchall()
        }  # owner_id -> booklist_id
        print_info(f"其中 {len(existing_booklists)} 个用户已有默认书单。")

        # Step 3: 为没有默认书单的用户批量创建默认书单
        users_without_booklist = [
            uid for uid in old_user_ids if uid not in existing_booklists
        ]
        if users_without_booklist:
            now = datetime.now(timezone.utc)
            for user_id in users_without_booklist:
                main_cursor.execute(
                    "INSERT INTO booklist (owner_id, title, description, is_public,"
                    " is_default, display_type, item_count, collection_count,"
                    " view_count, created_at, updated_at)"
                    " VALUES (?, '默认收藏', '默认收藏夹', 0, 1, 1, 0, 0, 0, ?, ?)",
                    (user_id, now, now),
                )
                existing_booklists[user_id] = main_cursor.lastrowid
            main_conn.commit()
            print_success(f"为 {len(users_without_booklist)} 个用户创建了默认书单。")

        # Step 4: 分批读取旧收藏数据，按用户分组后批量插入
        old_cursor.execute(
            "SELECT user_id, thread_id, added_at FROM thread_favorites"
            " ORDER BY user_id, added_at"
        )

        # 缓存每个书单当前的 max(display_order)，避免逐批重复查询
        booklist_max_order: dict[int, int] = {}

        while True:
            batch = old_cursor.fetchmany(batch_size)
            if not batch:
                break

            # 按 user_id 分组
            grouped: dict = {}
            for user_id, thread_id, added_at in batch:
                grouped.setdefault(user_id, []).append((thread_id, added_at))

            for user_id, items in grouped.items():
                booklist_id = existing_booklists.get(user_id)
                if booklist_id is None:
                    total_attempted += len(items)
                    continue

                thread_ids = [item[0] for item in items]

                # 查询该书单中已存在的 thread_id（去重）
                ph = ",".join("?" * len(thread_ids))
                main_cursor.execute(
                    f"SELECT thread_id FROM booklist_item"
                    f" WHERE booklist_id = ? AND thread_id IN ({ph})",
                    [booklist_id] + thread_ids,
                )
                existing_thread_ids = set(row[0] for row in main_cursor.fetchall())

                # 过滤出待插入的新项
                new_items = [
                    item for item in items if item[0] not in existing_thread_ids
                ]
                total_attempted += len(items)

                if new_items:
                    # 获取该书单当前的最大 display_order
                    if booklist_id not in booklist_max_order:
                        main_cursor.execute(
                            "SELECT MAX(display_order) FROM booklist_item"
                            " WHERE booklist_id = ?",
                            (booklist_id,),
                        )
                        row = main_cursor.fetchone()
                        booklist_max_order[booklist_id] = (
                            row[0] if row[0] is not None else 0
                        )

                    insert_data = []
                    for i, (thread_id, added_at) in enumerate(new_items):
                        booklist_max_order[booklist_id] += 1
                        insert_data.append(
                            (
                                booklist_id,
                                thread_id,
                                user_id,
                                booklist_max_order[booklist_id],
                                added_at,
                                added_at,
                            )
                        )

                    main_cursor.executemany(
                        "INSERT INTO booklist_item"
                        " (booklist_id, thread_id, owner_id, display_order,"
                        " created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        insert_data,
                    )
                    total_migrated += len(new_items)

                    # 更新对应书单的 item_count
                    main_cursor.execute(
                        "UPDATE booklist SET item_count = item_count + ? WHERE id = ?",
                        (len(new_items), booklist_id),
                    )

            main_conn.commit()

        total_ignored = total_attempted - total_migrated
        print_success(
            f"成功迁移 {total_migrated} 条收藏记录 (忽略了 {total_ignored} 条重复记录)。"
        )

        # Step 5: 更新受影响帖子的收藏计数
        try:
            old_cursor.execute(
                "SELECT thread_id, COUNT(DISTINCT user_id) FROM thread_favorites"
                " GROUP BY thread_id"
            )
            collection_counts = old_cursor.fetchall()

            if collection_counts:
                update_data = [(count, tid) for tid, count in collection_counts]
                main_cursor.executemany(
                    "UPDATE thread SET collection_count = collection_count + ?"
                    " WHERE thread_id = ?",
                    update_data,
                )
                main_conn.commit()
                print_success(f"成功更新了 {len(collection_counts)} 个帖子的收藏计数。")
            else:
                print_info("旧数据库中没有收藏记录，无需更新计数。")

        except Exception as e:
            print_error(f"更新帖子收藏计数时出错: {e}")
            if main_conn:
                main_conn.rollback()

    except Exception as e:
        if main_conn:
            main_conn.rollback()
        print_error(f"从 follow_bot.db 迁移数据时发生错误: {e}")
        print_warning("数据迁移失败，但不会影响 Alembic 的迁移结果。")
    finally:
        if old_conn:
            old_conn.close()
        if main_conn:
            main_conn.close()

    # 迁移完成后删除旧数据库文件
    try:
        old_db_path.unlink()
        print_success(f"已成功删除旧的收藏数据库 '{old_db_path.name}'。")
    except Exception as e:
        print_error(f"删除旧的收藏数据库时出错: {e}")

    print_info("旧数据迁移流程结束。")


def main():
    """执行完整的数据库迁移流程"""
    print_info("=" * 50)
    print_info("=  数据库自动迁移脚本启动")
    print_info("=" * 50)

    # 备份数据库
    print_info(f"正在备份数据库 '{DB_PATH.name}'...")
    if not DB_PATH.exists():
        print_error(f"错误：数据库文件未找到于 '{DB_PATH}'。请确保文件存在。")
        sys.exit(1)

    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"{DB_PATH.stem}.backup_{timestamp}{DB_PATH.suffix}"
        backup_path = DATA_DIR / backup_filename

        shutil.copy2(DB_PATH, backup_path)  # copy2 会保留元数据
        print_success(f"数据库已成功备份到: '{backup_path}'")
    except Exception as e:
        print_error(f"备份数据库时发生错误: {e}")
        sys.exit(1)

    # 运行 Alembic 迁移
    print_info("准备执行 Alembic 数据库迁移...")
    print_warning("这将更新数据库结构。请勿中断此过程。")

    try:
        # 使用 subprocess.run 来执行命令，check=True 会在命令失败时抛出异常
        command = ["alembic", "upgrade", "head"]
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, encoding="utf-8"
        )

        # 打印 Alembic 的输出信息
        print("--- Alembic 输出开始 ---")
        print(result.stdout)
        print("--- Alembic 输出结束 ---")

        print_success("数据库迁移成功完成！")
    except FileNotFoundError:
        print_error("错误：'alembic' 命令未找到。")
        print_error("请确保 Alembic 已通过 uv 安装在项目的开发依赖中。")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print_error("Alembic 迁移过程中发生错误！")
        print_error("--- Alembic 错误输出开始 ---")
        print(e.stderr)
        print_error("--- Alembic 错误输出结束 ---")
        print_warning("数据库结构可能处于不一致状态。建议使用备份文件进行恢复。")
        sys.exit(1)
    except Exception as e:
        print_error(f"执行迁移时发生未知错误: {e}")
        sys.exit(1)

    # 从 follow_bot.db 迁移收藏数据
    migrate_favorites_from_follow_bot()

    try:
        # 使用标准库 sqlite3 连接数据库
        conn = sqlite3.connect(DB_PATH)
        conn.execute("VACUUM;")
        conn.close()
    except Exception as e:
        print_error(f"执行 VACUUM 时发生错误: {e}")
        print_warning("数据库结构已更新，但优化步骤失败。机器人仍可正常运行。")

    print_info("=" * 50)
    print_success(" 所有操作已成功完成！现在可以启动机器人了。")
    print_info("=" * 50)


if __name__ == "__main__":
    main()
