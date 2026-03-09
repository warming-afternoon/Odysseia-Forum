# migrate_to_multi_server.py
"""
多服务器迁移脚本

功能：
1. 运行 Alembic 迁移更新数据库结构（添加 guild_id 列）
2. 将所有现有帖子和用户偏好设置统一设置为指定的服务器 ID

用法：
    python migrate_to_multi_server.py <guild_id>

其中 <guild_id> 是当前单服务器的 Discord 服务器 ID。
"""

import sqlite3
import sys
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "database.db"


def print_color(text, color_code):
    print(f"\033[{color_code}m{text}\033[0m")


def print_info(message):
    print_color(f"ℹ️  {message}", "94")


def print_success(message):
    print_color(f"✅ {message}", "92")


def print_warning(message):
    print_color(f"⚠️  {message}", "93")


def print_error(message):
    print_color(f"❌ {message}", "91")


def update_guild_ids(guild_id: int):
    """将所有现有帖子和用户偏好的 guild_id 更新为指定值"""
    print_info(f"正在将所有现有数据的 guild_id 更新为 {guild_id}...")

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 更新 thread 表
        cursor.execute("UPDATE thread SET guild_id = ? WHERE guild_id = 0", (guild_id,))
        thread_count = cursor.rowcount
        print_success(f"已更新 {thread_count} 条帖子的 guild_id")

        # 更新 usersearchpreferences 表
        cursor.execute(
            "UPDATE usersearchpreferences SET guild_id = ? WHERE guild_id = 0",
            (guild_id,),
        )
        prefs_count = cursor.rowcount
        print_success(f"已更新 {prefs_count} 条用户偏好的 guild_id")

        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        print_error(f"更新 guild_id 时发生错误: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()


def main():
    if len(sys.argv) < 2:
        print_error("用法: python migrate_to_multi_server.py <guild_id>")
        print_info("其中 <guild_id> 是当前单服务器的 Discord 服务器 ID")
        sys.exit(1)

    try:
        guild_id = int(sys.argv[1])
    except ValueError:
        print_error(f"无效的 guild_id: {sys.argv[1]}，必须是整数")
        sys.exit(1)

    print_info("=" * 55)
    print_info("=  多服务器支持迁移脚本")
    print_info("=" * 55)
    print_info(f"目标服务器 ID: {guild_id}")

    # 检查数据库是否存在
    if not DB_PATH.exists():
        print_error(f"数据库文件未找到: {DB_PATH}")
        sys.exit(1)

    # 备份数据库
    print_info(f"正在备份数据库 '{DB_PATH.name}'...")
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"{DB_PATH.stem}.multi_server_backup_{timestamp}{DB_PATH.suffix}"
        backup_path = DATA_DIR / backup_filename
        shutil.copy2(DB_PATH, backup_path)
        print_success(f"数据库已备份到: {backup_path}")
    except Exception as e:
        print_error(f"备份失败: {e}")
        sys.exit(1)

    # 运行 Alembic 迁移
    print_info("正在执行 Alembic 数据库迁移...")
    print_warning("这将更新数据库结构，请勿中断。")
    try:
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        print("--- Alembic 输出 ---")
        print(result.stdout)
        print("--- 结束 ---")
        print_success("数据库结构迁移成功！")
    except FileNotFoundError:
        print_error("'alembic' 命令未找到，请确保已安装 Alembic。")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print_error("Alembic 迁移失败！")
        print(e.stderr)
        print_warning(f"建议使用备份文件 {backup_path} 进行恢复。")
        sys.exit(1)

    # 更新 guild_id
    update_guild_ids(guild_id)

    # VACUUM 优化
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("VACUUM;")
        conn.close()
    except Exception as e:
        print_warning(f"VACUUM 优化失败（不影响使用）: {e}")

    print_info("=" * 55)
    print_success("多服务器迁移完成！")
    print_info(f"所有现有数据已关联到服务器 {guild_id}")
    print_info("现在可以启动机器人了。")
    print_info("=" * 55)


if __name__ == "__main__":
    main()
