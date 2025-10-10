# 索引同步服务

这个模块负责将数据库中的帖子数据同步到 JSON 文件中，供网页使用。

## 功能特性

- **定时同步**: 每 30 分钟自动同步一次数据
- **完整数据**: 包含帖子标题、作者、标签、时间等完整信息
- **Discord 集成**: 自动获取用户在服务器中的昵称
- **自动部署**: 支持自动部署到 Cloudflare Pages
- **配置管理**: 自动更新网页配置文件
- **错误处理**: 完善的错误处理和日志记录

## 数据格式

同步的 JSON 文件格式如下：

```json
[
  {
    "channel_id": 123,
    "thread_id": 123,
    "title": "帖子标题",
    "author_id": 123,
    "author": "用户昵称",
    "created_at": "2024-01-01T00:00:00",
    "last_active_at": "2024-01-01T12:00:00",
    "reaction_count": 5,
    "reply_count": 3,
    "first_message_excerpt": "帖子摘要...",
    "thumbnail_url": "https://example.com/image.jpg",
    "tags": ["标签1", "标签2"]
  }
]
```

## 使用方法

### 自动启动

服务会在机器人启动时自动开始运行，无需手动配置。

### 手动同步

如果需要手动触发同步，可以调用：

```python
from src.webpage.index_sync import manual_sync

# 手动同步一次
await manual_sync(bot)
```

### 自定义同步间隔

可以通过修改 `bot_main.py` 中的参数来调整同步间隔：

```python
# 修改间隔为 60 分钟
asyncio.create_task(start_index_sync(self, interval_minutes=60))
```

## 输出文件

同步后的数据会保存到项目根目录的 `webpage/index.json` 文件中。

## Cloudflare Pages 部署

### 配置说明

在 `config.json` 中添加以下配置：

```json
{
  "webpage": {
    "guild_id": "your_guild_id_here",
    "channels": {
      "channel_id_1": "频道名称1",
      "channel_id_2": "频道名称2"
    },
    "cloudflare": {
      "enabled": true,
      "api_token": "your_cloudflare_api_token",
      "account_id": "your_cloudflare_account_id",
      "project_name": "your_project_name"
    }
  }
}
```

### 配置项说明

- `guild_id`: Discord 主服务器 ID
- `channels`: 要展示的频道列表，格式为 `频道ID: 频道名称`
- `cloudflare.enabled`: 是否启用自动部署到 Cloudflare Pages
- `cloudflare.api_token`: Cloudflare API Token（需要有 Pages 编辑权限）
- `cloudflare.account_id`: Cloudflare 账户 ID
- `cloudflare.project_name`: Cloudflare Pages 项目名称

### 部署前准备

1. 安装 Wrangler CLI：
   ```bash
   npm install -g wrangler
   ```
   
   **Windows 用户注意**：如果安装后提示找不到命令，系统会自动在以下路径查找：
   - `%USERPROFILE%\AppData\Roaming\npm\wrangler.cmd`
   - `%USERPROFILE%\AppData\Roaming\npm\wrangler.ps1`
   - `C:\Program Files\nodejs\wrangler.cmd`

2. 在 Cloudflare Dashboard 创建一个 Pages 项目

3. 获取 API Token：
   - 访问 https://dash.cloudflare.com/profile/api-tokens
   - 创建一个具有 "Cloudflare Pages:Edit" 权限的 Token

4. 获取 Account ID：
   - 在 Cloudflare Dashboard 右侧可以找到 Account ID

### 工作流程

每次同步完成后，服务会自动：

1. 更新 `webpage/config.js` 文件，写入 guild_id 和 channels 配置
2. 如果启用了 Cloudflare 部署，将 `webpage/` 目录部署到 Cloudflare Pages

### 手动部署

如果需要手动部署，可以使用以下命令：

```bash
wrangler pages deploy webpage --project-name your_project_name --branch main
```

## 注意事项

1. 确保机器人有足够的权限访问服务器成员信息
2. 如果用户不在服务器中，会尝试通过 Discord API 获取用户名
3. 同步过程是异步的，不会阻塞机器人其他功能
4. 所有操作都有详细的日志记录，便于调试
5. Cloudflare Pages 部署需要安装 Wrangler CLI
6. **Windows 系统**：系统会自动查找 wrangler 命令的多个可能位置，并使用 shell 模式执行命令以确保兼容性
7. 部署过程最多等待 5 分钟，超时会记录错误日志
8. 如果不需要自动部署，将 `cloudflare.enabled` 设置为 `false` 即可
9. `config.js` 会在每次同步后自动更新，无需手动修改
10. 如果遇到 "未找到 wrangler 命令" 错误，请检查日志中的详细信息，系统会尝试多个可能的路径
