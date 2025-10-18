# migrate.py
import sqlite3
import sys
import shutil
import subprocess
from datetime import datetime
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
    print_color(f"ℹ️  {message}", "94") # Blue

def print_success(message):
    print_color(f"✅ {message}", "92") # Green

def print_warning(message):
    print_color(f"⚠️  {message}", "93") # Yellow

def print_error(message):
    print_color(f"❌ {message}", "91") # Red

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
        
        shutil.copy2(DB_PATH, backup_path) # copy2 会保留元数据
        print_success(f"数据库已成功备份到: '{backup_path}'")
    except Exception as e:
        print_error(f"备份数据库时发生错误: {e}")
        sys.exit(1)

    # 删除旧的缓存文件
    print_info(f"正在检查并删除旧缓存文件 '{CACHE_PATH.name}'...")
    if CACHE_PATH.exists():
        try:
            CACHE_PATH.unlink()
            print_success(f"旧缓存文件 '{CACHE_PATH.name}' 已删除。")
        except Exception as e:
            print_error(f"删除缓存文件时发生错误: {e}")
            sys.exit(1)
    else:
        print_warning(f"缓存文件 '{CACHE_PATH.name}' 未找到，跳过删除。")

    # 运行 Alembic 迁移
    print_info("准备执行 Alembic 数据库迁移...")
    print_warning("这将更新数据库结构。请勿中断此过程。")
    
    try:
        # 使用 subprocess.run 来执行命令，check=True 会在命令失败时抛出异常
        command = ["alembic", "upgrade", "head"]
        result = subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8')
        
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