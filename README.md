# Odysseia Forum Search Bot

Discord 论坛搜索机器人，支持智能索引、多维度搜索和用户偏好设置。

## 🌟 主要功能

### 📚 智能索引系统
- **批量构建**：支持对整个论坛频道（包括归档帖子）进行批量索引
- **实时更新**：监听已索引频道的帖子创建、更新、删除、标签、反应数等事件，从而自动更新索引

### 🔍 多维度搜索
- **标签搜索**：支持正选/反选模式，AND/OR逻辑
- **关键词搜索**：支持标题和内容搜索，AND/OR逻辑
- **作者过滤**：支持只看指定作者或屏蔽指定作者
- **范围过滤**：支持按发帖时间/活跃时间/反应数/发言数筛选
- **频道范围**：支持单频道或多频道搜索

### 🧠 智能排序算法
- **综合排序**：基于 UCB1 算法，计算帖子的“实力”（平均反应）和“潜力”（曝光机会）
- **多种排序**：支持按时间、活跃度、反应数、回复数等多种方式排序
- **可配置权重**：管理员可调整排序算法的各项参数

### 👤 用户偏好系统
- **搜索偏好**：保存用户的作者、时间、标签、关键词偏好
- **显示设置**：支持缩略图/大图预览模式
- **结果数量**：可自定义每页显示的搜索结果数量

## 📋 系统要求

- Python 3.13+
- PostgreSQL 16
- Redis
- Docker / Docker Compose（推荐）

## 🚀 快速开始

### 1. 克隆项目
```bash
git clone https://github.com/your-username/Odysseia-Forum.git

cd Odysseia-Forum
```

### 2. 配置文件

1. 复制 `config.example.json` → `config.json`，填入 Discord Bot Token 等配置
2. 复制 `.env.example` → `.env`，设置 PostgreSQL 数据库密码

### 3. Docker Compose 运行（推荐）

项目使用 PostgreSQL 作为数据库、Redis 作为缓存。Docker Compose 一键启动全部服务：

```bash
# 启动所有服务（PostgreSQL + Redis + Bot + API）
docker compose up -d --build
```

服务说明：

| 服务 | 端口 | 说明 |
|------|------|------|
| `odysseia-postgres` | 127.0.0.1:15432 | PostgreSQL 16 数据库 |
| `odysseia-redis` | 11810 | Redis 缓存 |
| `odysseia-forum-bot` | - | Discord 机器人 |
| `odysseia-forum-api` | 10810 | FastAPI 搜索接口 |

常用命令：

```bash
# 查看日志
docker compose logs -f

# 停止所有服务
docker compose down

# 停止并删除数据卷（⚠️ 会删除数据库数据）
docker compose down -v
```

### 4. 从 SQLite 迁移数据（老用户）

如果你有旧的 SQLite 数据库需要迁移到 PostgreSQL：

```bash
# 1. 确保 PostgreSQL 容器已启动
docker compose up -d odysseia-postgres

# 2. 运行迁移脚本
uv run python scripts/migrate_sqlite_to_pg.py
```

### 5. 本地开发运行（不使用 Docker）

```bash
# 安装依赖
uv sync

# 确保 PostgreSQL 和 Redis 已运行，然后：
uv run bot_main.py    # 启动机器人
uv run api_main.py    # 启动 API 服务
```

## 📖 使用指南

### 基础设置

1. **邀请机器人**：确保机器人有以下权限：
   - 查看频道
   - 发送消息
   - 嵌入链接
   - 读取消息历史

2. **构建索引**：在论坛频道中使用 `/构建索引` 命令

3. **创建公开搜索** : 使用 `/创建公开全局搜索` 或 `/创建频道搜索` 命令，创建公开搜索面板

#### 搜索界面说明
- **关键词**：设置包含/排除的关键词
- **排序**：选择排序方式和方向

## 📝 命令列表

### 用户命令

#### 搜索相关 (/命令)
- `/全局搜索` - 开始一次仅自己可见的全局搜索
- `/搜索作者 [作者]` - 搜索指定作者的帖子

#### 搜索相关 (app命令，右键点击用户头像)
- `搜索作品` - 搜索该用户的全部作品
- `加入搜索屏蔽` - 加入屏蔽列表，搜索时不会搜索到该用户的作品
- `移出搜索屏蔽` - 移出搜索屏蔽


#### 偏好设置
- `/搜索偏好 作者` - 管理作者偏好（只看/屏蔽/取消屏蔽/清空）
- `/搜索偏好 设置` - 设置搜索时间范围、预览图显示方式（缩略图/大图）、标签、关键词、每页显示结果数

#### 标签系统
- `/标签评价` - 对当前帖子的标签进行点赞/点踩

### 管理员命令

#### 索引/面板管理
- `/构建索引` - 对当前论坛频道构建索引
- `/创建公开全局搜索` - 创建全局搜索面板
- `/创建频道搜索` - 在当前帖子内创建频道搜索按钮

#### 配置
- `/配置 全局设置` - 配置搜索排序算法参数
- `/配置 互斥标签组` - 配置互斥标签组

## ⚙️ 排序算法说明

### 综合排序算法 (UCB1)

我们采用 UCB1 (Upper Confidence Bound) 算法

- **利用 (Exploitation)**：帖子获得的反应数越多，其”实力分”就越高。
- **探索 (Exploration)**：帖子被展示的次数越少，其”机会分”就越高，从而获得更多曝光机会。

**最终分数** = `实力分 + 机会分`

### Reddit Hot 算法

备选排序方案，基于 Reddit 的热门排序公式：时间越新的帖子获得越高的时间加分，而高分帖子不会获得不成比例的巨大优势。

## 🔍 全文搜索实现

项目使用 PostgreSQL 的 `tsvector`/`tsquery` 实现中文全文搜索：

- **分词**：jieba-rs（Rust 实现的中文分词器），在 Python 端完成分词后通过 `array_to_tsvector` 存入 PostgreSQL
- **索引**：GIN 索引加速全文搜索，2 万帖子下搜索延迟 < 100ms
- **查询语法**：支持 AND/OR 组、前缀匹配、短语精确匹配、排除关键词 + 豁免标记

> 详细技术方案见 [PG 迁移方案文档](docs/PG_MIGRATION.md)

## 🔧 配置参数

### UCB1 算法
- `UCB1_STRENGTH_WEIGHT` (W): 实力权重，放大帖子自身实力（平均反应数）在总分中的占比。
- `UCB1_EXPLORATION_FACTOR` (C): 探索因子，控制对新内容和不确定内容的探索力度。

[UCB1 算法解释](docs/RANKING_ALGORITHM.md)

## 🤝 贡献指南

1. Fork 本项目
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

> 本项目推荐使用 uv 进行项目管理  
> [ uv 中文指南](https://hellowac.github.io/uv-zh-cn/)

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情

## 🐛 问题反馈

如果您遇到任何问题或有功能建议，请在 [Issues](https://github.com/your-username/Odysseia-Forum/issues) 页面提交。

---

**注意**：使用前请确保您已阅读Discord的服务条款和机器人使用政策。

