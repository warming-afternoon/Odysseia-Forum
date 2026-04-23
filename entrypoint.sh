#!/bin/bash
set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

# --- 迁移逻辑 ---
# 当环境变量 RUN_MIGRATE=1 时，在启动项目前先运行 migrate.py
if [ "${RUN_MIGRATE:-0}" = "1" ]; then
    log_info "检测到 RUN_MIGRATE=1，开始执行数据库迁移..."
    log_warning "这将更新数据库结构，请勿中断此过程。"

    if uv run python migrate.py; then
        log_success "数据库迁移完成！"
    else
        log_error "数据库迁移失败！容器将停止运行。"
        exit 1
    fi
else
    log_info "RUN_MIGRATE 未设置或为 0，跳过数据库迁移。"
    log_info "如需运行迁移，请使用: RUN_MIGRATE=1 docker compose up -d --build"
fi

# --- 启动项目 ---
log_info "正在启动 Odysseia-Forum Bot..."
exec uv run bot_main.py
