import { fetchImages } from '../api'
import { useAppStore } from '../stores/app'
import { getPlaceholderImage, getPrimaryThumbnail, normalizeThumbnailList } from '../utils'

const failedImages = new Map()
let refreshTimer = null
let isRefreshing = false

export function scheduleImageRecovery(threadId, channelId) {
  const key = String(threadId)
  if (!failedImages.has(key)) {
    failedImages.set(key, { thread_id: threadId, channel_id: channelId || null })
  }
  ensureTimerRunning()
}

function ensureTimerRunning() {
  if (refreshTimer) return
  refreshTimer = setInterval(flushQueue, 5000)
}

function cleanupTimer() {
  if (failedImages.size === 0 && refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
}

async function flushQueue() {
  if (failedImages.size === 0 || isRefreshing) {
    cleanupTimer()
    return
  }

  const batch = Array.from(failedImages.entries()).slice(0, 10)
  batch.forEach(([key]) => failedImages.delete(key))

  const items = batch.map(([, entry]) => ({
    thread_id: entry.thread_id,
    channel_id: entry.channel_id || undefined,
  }))

  isRefreshing = true
  const response = await fetchImages(items)
  isRefreshing = false

  if (!response || !Array.isArray(response.results)) {
    batch.forEach(([key, entry]) => failedImages.set(key, entry))
    cleanupTimer()
    return
  }

  const store = useAppStore()
  const responseMap = new Map(
    response.results.map((item) => [String(item.thread_id), item])
  )

  batch.forEach(([, entry]) => {
    const key = String(entry.thread_id)
    const result = responseMap.get(key)

    if (result && result.error != null) {
      return
    }

    const newUrls = normalizeThumbnailList(result?.thumbnail_urls)
    if (newUrls.length) {
      updateLocalThumbnail(store, key, newUrls)
    } else {
      failedImages.set(key, entry)
    }
  })

  cleanupTimer()
}

function updateLocalThumbnail(store, threadId, thumbnailUrls) {
  const targetId = String(threadId)
  const post = store.results.find((p) => String(p.thread_id) === targetId)
  if (post) {
    post.thumbnail_urls = thumbnailUrls
  }
  const followPost = store.followThreads.find((p) => String(p.thread_id) === targetId)
  if (followPost) {
    followPost.thumbnail_urls = thumbnailUrls
  }
}

export function destroyImageRecovery() {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
  failedImages.clear()
  isRefreshing = false
}
