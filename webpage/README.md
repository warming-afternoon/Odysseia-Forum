# Odysseia Forum 网页前端

基于 Vue 3 + Vite + Tailwind CSS 构建的前端应用。

## 快速开始

```bash
cd webpage
npm install
npm run dev
```

开发服务器将在 `http://localhost:5173` 启动。

## 配置说明

编辑 `src/config.js`，填入你的实际配置：

```js
export const GUILD_ID = '你的服务器ID'
export const CLIENT_ID = '你的Discord应用ClientID'
export const AUTH_URL = 'https://你的API域名/v1'
```

频道分类在 `CHANNEL_CATEGORIES` 数组中配置。

同时需要在 `config.json` 中配置后端：

```json
{
  "auth": {
    "frontend_url": "http://localhost:5173",
    "redirect_uri": "http://localhost:8000/v1/auth/callback"
  }
}
```

### Discord 开发者后台配置

1. 访问 https://discord.com/developers/applications
2. 选择你的应用
3. 进入 **OAuth2** → **Redirects**
4. 添加重定向URL：`http://localhost:8000/v1/auth/callback`
5. 保存更改

## 构建部署

```bash
npm run build
```

产物输出到 `dist/` 目录，可直接部署到任意静态托管服务。

### 推荐部署方式

- **Cloudflare Pages** — 免费 CDN 和静态托管
- **Nginx** — 传统生产环境
- **Caddy** — 自动 HTTPS

## 技术栈

- Vue 3 (Composition API)
- Vite
- Pinia (状态管理)
- Vue Router (Hash 模式)
- Tailwind CSS
