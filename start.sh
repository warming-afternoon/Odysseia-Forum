#!/bin/bash

# Odysseia-Forum Discord Bot 一键启动脚本
# 作者: AI Assistant
# 用途: 自动设置Python环境并启动Discord机器人

set -e  # 遇到错误时立即退出

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查命令是否存在
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# 主函数
main() {
    log_info "开始启动 Odysseia-Forum Discord Bot..."
    
    # 检查是否在正确的目录
    if [ ! -f "bot_main.py" ]; then
        log_error "未找到 bot_main.py 文件，请确保在项目根目录下运行此脚本"
        exit 1
    fi
    
    # 检查Python3是否已安装
    if ! command_exists python3; then
        log_error "未找到 Python3，请先安装 Python 3.8 或更高版本"
        log_info "Ubuntu/Debian: sudo apt update && sudo apt install python3 python3-pip python3-venv"
        log_info "CentOS/RHEL: sudo yum install python3 python3-pip"
        log_info "Arch Linux: sudo pacman -S python python-pip"
        exit 1
    fi
    
    # 检查Python版本
    python_version=$(python3 -c "import sys; print('.'.join(map(str, sys.version_info[:2])))")
    log_info "检测到 Python 版本: $python_version"
    
    # 检查pip是否可用
    if ! command_exists pip3; then
        log_warning "未找到 pip3，尝试安装..."
        if command_exists apt; then
            sudo apt update && sudo apt install python3-pip
        elif command_exists yum; then
            sudo yum install python3-pip
        elif command_exists pacman; then
            sudo pacman -S python-pip
        else
            log_error "无法自动安装 pip，请手动安装"
            exit 1
        fi
    fi
    
    # 创建虚拟环境（如果不存在）
    if [ ! -d ".venv" ]; then
        log_info "创建 Python 虚拟环境..."
        python3 -m venv .venv
        log_success "虚拟环境创建完成"
    else
        log_info "检测到现有虚拟环境"
    fi
    
    # 激活虚拟环境
    log_info "激活虚拟环境..."
    source .venv/bin/activate
    
    # 升级pip
    log_info "升级 pip..."
    pip install --upgrade pip
    
    # 安装依赖
    if [ -f "requirements.txt" ]; then
        log_info "安装项目依赖..."
        pip install -r requirements.txt
        log_success "依赖安装完成"
    else
        log_warning "未找到 requirements.txt 文件"
    fi
    
    # 检查配置文件
    if [ ! -f "config.json" ]; then
        log_error "未找到 config.json 配置文件"
        log_info "请创建 config.json 文件并添加您的 Discord Bot Token"
        exit 1
    fi
    
    # 检查数据库初始化
    log_info "检查数据库文件..."
    if [ ! -f "forum_search.db" ]; then
        log_info "数据库文件不存在，将在首次运行时自动创建"
    fi
    
    # 启动机器人
    log_info "启动 Odysseia-Forum Discord Bot..."
    log_info "按 Ctrl+C 停止机器人"
    echo "=========================================="
    
    # 捕获中断信号以优雅退出
    trap 'echo -e "\n${YELLOW}正在停止机器人...${NC}"; exit 0' SIGINT SIGTERM
    
    # 运行机器人
    python bot_main.py
}

# 使用 restart 参数可以重启机器人
if [ "$1" = "restart" ]; then
    log_info "重启模式：杀死现有进程..."
    pkill -f "python.*bot_main.py" || true
    sleep 2
fi

# 使用 setup 参数只进行环境设置，不启动机器人
if [ "$1" = "setup" ]; then
    log_info "仅进行环境设置..."
    # 重新定义 main 函数以跳过启动部分
    main() {
        log_info "开始设置 Odysseia-Forum Discord Bot 环境..."
        
        if [ ! -f "bot_main.py" ]; then
            log_error "未找到 bot_main.py 文件，请确保在项目根目录下运行此脚本"
            exit 1
        fi
        
        if ! command_exists python3; then
            log_error "未找到 Python3，请先安装 Python 3.8 或更高版本"
            exit 1
        fi
        
        python_version=$(python3 -c "import sys; print('.'.join(map(str, sys.version_info[:2])))")
        log_info "检测到 Python 版本: $python_version"
        
        if [ ! -d ".venv" ]; then
            log_info "创建 Python 虚拟环境..."
            python3 -m venv .venv
            log_success "虚拟环境创建完成"
        fi
        
        log_info "激活虚拟环境..."
        source .venv/bin/activate
        
        log_info "升级 pip..."
        pip install --upgrade pip
        
        if [ -f "requirements.txt" ]; then
            log_info "安装项目依赖..."
            pip install -r requirements.txt
            log_success "依赖安装完成"
        fi
        
        log_success "环境设置完成！运行 './start.sh' 启动机器人"
    }
fi

# 显示帮助信息
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo "Odysseia-Forum Discord Bot 启动脚本"
    echo ""
    echo "用法:"
    echo "  ./start.sh          - 启动机器人"
    echo "  ./start.sh setup    - 仅设置环境，不启动机器人"
    echo "  ./start.sh restart  - 重启机器人（杀死现有进程后重新启动）"
    echo "  ./start.sh --help   - 显示此帮助信息"
    echo ""
    echo "首次运行时，脚本会自动："
    echo "  1. 检查 Python3 环境"
    echo "  2. 创建虚拟环境"
    echo "  3. 安装依赖包"
    echo "  4. 启动 Discord 机器人"
    echo ""
    exit 0
fi

# 执行主函数
main 