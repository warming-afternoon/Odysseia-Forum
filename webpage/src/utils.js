import { GUILD_ID } from './config'

export function getPrimaryThumbnail(post) {
  if (!post) return null
  if (Array.isArray(post.thumbnail_urls) && post.thumbnail_urls.length) {
    return post.thumbnail_urls.find((url) => typeof url === 'string' && url.length) || null
  }
  if (typeof post.thumbnail_url === 'string' && post.thumbnail_url.length) {
    return post.thumbnail_url
  }
  return null
}

export function normalizeThumbnailList(thumbnailUrls) {
  if (Array.isArray(thumbnailUrls)) {
    return thumbnailUrls.filter((url) => typeof url === 'string' && url.length)
  }
  if (typeof thumbnailUrls === 'string' && thumbnailUrls.length) return [thumbnailUrls]
  return []
}

export function getPlaceholderImage(size = '600x300') {
  return `https://placehold.co/${size}/2f3136/72767d?text=No+Image`
}

export function parseMarkdown(text, expanded = false) {
  if (!text) return ''
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[(.*?)\]\((.*?)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/\n/g, '<br>')
  if (expanded) {
    html = html.replace(/^# (.*$)/gim, '<h1>$1</h1>').replace(/^## (.*$)/gim, '<h2>$1</h2>')
  }
  return html
}

export function formatTimeAgo(timestamp) {
  const diff = Date.now() - timestamp
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  return `${days} 天前`
}

export function getAuthorAvatar(user) {
  if (!user) return 'https://cdn.discordapp.com/embed/avatars/0.png'
  return user.avatar_url ||
    (user.avatar
      ? `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png`
      : 'https://cdn.discordapp.com/embed/avatars/0.png')
}

export function getAuthorDisplayName(user) {
  if (!user) return 'Unknown'
  return user.global_name || user.name || user.username || 'Unknown'
}

export function getAuthorUsername(user) {
  if (!user) return ''
  return user.name || user.username || ''
}

export function getPostLinks(post, viewType) {
  const guildId = post.guild_id || GUILD_ID || '@me'
  const defaultWeb = `https://discord.com/channels/${guildId}/${post.thread_id}`
  const defaultApp = `discord://discord.com/channels/${guildId}/${post.thread_id}`

  const webLink = (viewType === 'follows' && post.latest_update_link) ? post.latest_update_link : defaultWeb
  const appLink = (viewType === 'follows' && post.latest_update_link)
    ? post.latest_update_link.replace('https://discord.com', 'discord://discord.com')
    : defaultApp

  return { webLink, appLink }
}
