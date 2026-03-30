import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import {
  searchPosts,
  getFollows,
  getUnreadCount,
  markFollowsViewed as apiMarkViewed,
  removeFollow as apiRemoveFollow,
} from '../api'

export const useAppStore = defineStore('app', () => {
  // View state
  const view = ref('search')
  const channelId = ref(null)
  const sidebarOpen = ref(false)

  // Search params
  const query = ref('')
  const dateStart = ref('')
  const dateEnd = ref('')
  const sortMethod = ref('last_active')
  const sortOrder = ref('desc')

  // Tags
  const tagMode = ref('include')
  const tagLogic = ref('and')
  const includedTags = ref(new Set())
  const excludedTags = ref(new Set())
  const availableTags = ref([])
  const virtualTags = ref([])

  // Results
  const results = ref([])
  const totalResults = ref(0)
  const isLoading = ref(false)
  const banners = ref([])

  // Follows
  const followThreads = ref([])
  const followTotal = ref(0)
  const followFetched = ref(false)
  const followAvailableTags = ref([])
  const followNeedsRefresh = ref(false)
  const unreadCount = ref(0)
  const markingFollows = ref(false)

  // Detail overlay
  const detailPost = ref(null)
  const detailVisible = ref(false)

  // Toast
  const toastMessage = ref('')
  const toastVisible = ref(false)
  let toastTimer = null

  // Resume browse
  const resumeData = ref(null)
  const resumeVisible = ref(false)
  let resumePending = false

  // Search abort
  let searchAbortController = null

  // Image recovery
  const failedImages = ref(new Map())
  let imageRefreshTimer = null
  let isRefreshingImages = false

  function showToast(msg) {
    toastMessage.value = msg
    toastVisible.value = true
    clearTimeout(toastTimer)
    toastTimer = setTimeout(() => {
      toastVisible.value = false
    }, 3000)
  }

  function openDetail(post) {
    detailPost.value = post
    detailVisible.value = true
  }

  function closeDetail() {
    detailVisible.value = false
    setTimeout(() => {
      detailPost.value = null
    }, 300)
  }

  function toggleSidebar(show) {
    sidebarOpen.value = show
  }

  function abortSearch() {
    if (searchAbortController) {
      searchAbortController.abort()
      searchAbortController = null
    }
  }

  function collectLoadedThreadIds() {
    if (!results.value?.length) return []
    const ids = new Set()
    results.value.forEach((p) => {
      const id = p?.thread_id?.toString()
      if (id?.trim()) ids.add(id)
    })
    return Array.from(ids)
  }

  async function executeSearch(reset = true) {
    const previousResults = results.value
    const previousTotal = totalResults.value

    if (reset) results.value = []
    isLoading.value = true

    if (view.value === 'follows') {
      await fetchFollowThreads()
      applyFollowFilters()
      await markFollowsViewedAction()
      isLoading.value = false
      if (window.innerWidth < 768) sidebarOpen.value = false
      if (reset) saveStateToUrl()
      return
    }

    abortSearch()
    const controller = new AbortController()
    searchAbortController = controller

    const excludeIds = collectLoadedThreadIds()
    const body = {
      channel_ids: channelId.value ? [channelId.value] : null,
      include_tags: Array.from(includedTags.value),
      exclude_tags: Array.from(excludedTags.value),
      tag_logic: tagLogic.value,
      keywords: query.value || null,
      created_after: dateStart.value || null,
      created_before: dateEnd.value || null,
      sort_method: sortMethod.value,
      sort_order: sortOrder.value,
      limit: 20,
    }

    if (excludeIds.length) body.exclude_thread_ids = excludeIds

    const data = await searchPosts(body, controller.signal)
    if (controller.signal.aborted) return

    if (data) {
      const incoming = Array.isArray(data.results) ? data.results : []
      const existingIds = reset
        ? new Set()
        : new Set(results.value.map((p) => String(p.thread_id)))
      const deduped = incoming.filter((p) => {
        const id = String(p.thread_id)
        if (!id || existingIds.has(id)) return false
        existingIds.add(id)
        return true
      })

      results.value = reset ? deduped : [...results.value, ...deduped]
      totalResults.value = data.total
      availableTags.value = data.available_tags || []
      virtualTags.value = data.virtual_tags || []
      if (reset && data.banner_carousel) banners.value = data.banner_carousel
    } else if (reset) {
      results.value = previousResults
      totalResults.value = previousTotal
    }

    isLoading.value = false
    if (window.innerWidth < 768) sidebarOpen.value = false
    if (reset) saveStateToUrl()
    if (!resumePending) saveBrowseState()
  }

  function loadMore() {
    if (view.value === 'follows') return
    if (isLoading.value) return
    if (results.value.length >= totalResults.value) return
    executeSearch(false)
  }

  async function fetchFollowThreads(force = false) {
    if (followFetched.value && !force && !followNeedsRefresh.value) return
    const data = await getFollows(10000, 0)
    if (!data) return
    const threads = Array.isArray(data.threads) ? data.threads : []
    followThreads.value = threads.map((t) => ({
      ...t,
      thread_id: t.thread_id != null ? String(t.thread_id) : t.thread_id,
      channel_id: t.channel_id != null ? String(t.channel_id) : t.channel_id,
      tags: Array.isArray(t.tags) ? t.tags : [],
    }))
    followTotal.value = data.total ?? followThreads.value.length
    const tagSet = new Set()
    followThreads.value.forEach((t) => (t.tags || []).forEach((tag) => tagSet.add(tag)))
    followAvailableTags.value = Array.from(tagSet)
    followFetched.value = true
    followNeedsRefresh.value = false
  }

  function getFollowSortValue(thread, method) {
    const created = thread.created_at ? new Date(thread.created_at).getTime() : 0
    const active = thread.last_active_at ? new Date(thread.last_active_at).getTime() : created
    const latest = thread.latest_update_at ? new Date(thread.latest_update_at).getTime() : active
    switch (method) {
      case 'created_at': return created
      case 'last_active': return active
      case 'reply_count': return thread.reply_count ?? 0
      case 'reaction_count': return thread.reaction_count ?? 0
      default: return latest
    }
  }

  function applyFollowFilters() {
    let threads = [...followThreads.value]

    const keywordRaw = (query.value || '').trim().toLowerCase()
    let authorQuery = null
    const keywordTokens = []
    if (keywordRaw.length) {
      keywordRaw.split(/\s+/).forEach((token) => {
        if (token.startsWith('author:')) authorQuery = token.slice(7)
        else keywordTokens.push(token)
      })
    }

    const incTags = Array.from(includedTags.value)
    const excTags = Array.from(excludedTags.value)
    const selChannel = channelId.value ? String(channelId.value) : null
    const dStart = dateStart.value ? new Date(dateStart.value) : null
    const dEnd = dateEnd.value ? new Date(dateEnd.value) : null

    const filtered = threads.filter((t) => {
      const tags = t.tags || []
      const matchInc = incTags.length === 0 ||
        (tagLogic.value === 'and'
          ? incTags.every((tag) => tags.includes(tag))
          : incTags.some((tag) => tags.includes(tag)))
      if (!matchInc) return false
      if (excTags.length > 0 && !excTags.every((tag) => !tags.includes(tag))) return false
      if (selChannel && String(t.channel_id) !== selChannel) return false

      const createdAt = t.created_at ? new Date(t.created_at) : null
      if (dStart && (!createdAt || createdAt < dStart)) return false
      if (dEnd && (!createdAt || createdAt > dEnd)) return false

      if (authorQuery) {
        const author = t.author || {}
        const name = (author.username || author.global_name || '').toLowerCase()
        if (!name.includes(authorQuery.toLowerCase())) return false
      }

      if (keywordTokens.length) {
        const haystack = [t.title, t.first_message_excerpt, tags.join(' ')].join(' ').toLowerCase()
        if (!keywordTokens.every((tk) => haystack.includes(tk))) return false
      }

      return true
    })

    const order = sortOrder.value === 'asc' ? 'asc' : 'desc'
    filtered.sort((a, b) => {
      const va = getFollowSortValue(a, sortMethod.value)
      const vb = getFollowSortValue(b, sortMethod.value)
      return order === 'asc' ? va - vb : vb - va
    })

    results.value = filtered
    totalResults.value = filtered.length
  }

  async function refreshUnreadCount() {
    const data = await getUnreadCount()
    if (data && typeof data.unread_count === 'number') {
      unreadCount.value = data.unread_count
    } else {
      unreadCount.value = 0
    }
  }

  async function markFollowsViewedAction() {
    if (markingFollows.value || unreadCount.value === 0) return
    markingFollows.value = true
    try {
      await apiMarkViewed()
      unreadCount.value = 0
      followThreads.value = followThreads.value.map((t) => ({
        ...t,
        has_update: false,
        last_viewed_at: new Date().toISOString(),
      }))
    } catch { /* ignore */ } finally {
      markingFollows.value = false
    }
  }

  async function removeFollowAction(threadId) {
    const response = await apiRemoveFollow(threadId)
    if (response) {
      followThreads.value = followThreads.value.filter(
        (t) => String(t.thread_id) !== String(threadId)
      )
      followTotal.value = Math.max(0, followTotal.value - 1)
      if (view.value === 'follows') {
        applyFollowFilters()
        saveStateToUrl()
      }
      showToast('已取消关注')
    }
  }

  function resetFollowState() {
    followThreads.value = []
    followTotal.value = 0
    followFetched.value = false
    followAvailableTags.value = []
    followNeedsRefresh.value = true
    if (view.value === 'follows') {
      results.value = []
      totalResults.value = 0
    }
  }

  function switchView(v) {
    view.value = v
    executeSearch()
  }

  function selectChannel(id) {
    channelId.value = id === 'global' ? null : id
    if (view.value === 'follows') {
      applyFollowFilters()
      saveStateToUrl()
    } else {
      tryResumeBrowse(channelId.value)
    }
  }

  function handleTagClick(tag) {
    if (includedTags.value.has(tag) || excludedTags.value.has(tag)) {
      includedTags.value.delete(tag)
      excludedTags.value.delete(tag)
    } else {
      tagMode.value === 'include'
        ? includedTags.value.add(tag)
        : excludedTags.value.add(tag)
    }
    includedTags.value = new Set(includedTags.value)
    excludedTags.value = new Set(excludedTags.value)

    if (view.value === 'follows') {
      applyFollowFilters()
      saveStateToUrl()
    } else {
      executeSearch()
    }
  }

  function setTagModeAction(m) {
    tagMode.value = m
    if (view.value === 'follows') applyFollowFilters()
  }

  function setTagLogicAction(l) {
    tagLogic.value = l
    if (view.value === 'follows') {
      applyFollowFilters()
      saveStateToUrl()
    } else {
      executeSearch()
    }
  }

  function toggleSortOrder() {
    sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
    if (view.value === 'follows') {
      applyFollowFilters()
      saveStateToUrl()
    } else {
      executeSearch()
    }
  }

  function applyAuthorSearch(username) {
    const normalized = (username || '').trim()
    if (!normalized) return
    query.value = `author:${normalized}`
    closeDetail()
    if (view.value === 'follows') {
      applyFollowFilters()
      saveStateToUrl()
    } else {
      executeSearch()
    }
  }

  // URL state sync
  function saveStateToUrl() {
    const params = new URLSearchParams()
    if (view.value !== 'search') params.set('view', view.value)
    if (channelId.value) params.set('channel', channelId.value)
    if (query.value) params.set('q', query.value)
    if (dateStart.value) params.set('from', dateStart.value)
    if (dateEnd.value) params.set('to', dateEnd.value)
    if (sortMethod.value && sortMethod.value !== 'comprehensive') params.set('sort', sortMethod.value)
    if (sortOrder.value !== 'desc') params.set('order', sortOrder.value)
    if (includedTags.value.size > 0) params.set('tags', Array.from(includedTags.value).join(','))
    if (excludedTags.value.size > 0) params.set('exclude', Array.from(excludedTags.value).join(','))
    if (tagLogic.value !== 'and') params.set('logic', tagLogic.value)

    const hash = location.hash || '#/'
    const base = location.pathname
    const newUrl = params.toString() ? `${base}?${params}${hash}` : `${base}${hash}`
    history.replaceState(null, '', newUrl)
  }

  function loadStateFromUrl() {
    const params = new URLSearchParams(location.search)
    if (params.get('view') === 'follows') view.value = 'follows'
    if (params.get('channel')) channelId.value = params.get('channel')
    if (params.get('q')) query.value = params.get('q')
    if (params.get('from')) dateStart.value = params.get('from')
    if (params.get('to')) dateEnd.value = params.get('to')
    if (params.get('sort')) sortMethod.value = params.get('sort')
    if (params.get('order') === 'asc') sortOrder.value = 'asc'
    const tags = params.get('tags')
    if (tags) tags.split(',').filter(Boolean).forEach((t) => includedTags.value.add(t.trim()))
    const exclude = params.get('exclude')
    if (exclude) exclude.split(',').filter(Boolean).forEach((t) => excludedTags.value.add(t.trim()))
    if (params.get('logic') === 'or') tagLogic.value = 'or'
  }

  // Browse state persistence
  function browseStateKey(chId) {
    return `browse_state_${chId || 'global'}`
  }

  function saveBrowseState() {
    if (view.value !== 'search') return
    if (!results.value?.length) return
    const key = browseStateKey(channelId.value)
    const payload = {
      results: results.value,
      totalResults: totalResults.value,
      availableTags: availableTags.value,
      virtualTags: virtualTags.value,
      banners: banners.value,
      keywords: query.value,
      includedTags: Array.from(includedTags.value),
      excludedTags: Array.from(excludedTags.value),
      tagLogic: tagLogic.value,
      sortMethod: sortMethod.value,
      sortOrder: sortOrder.value,
      dateStart: dateStart.value,
      dateEnd: dateEnd.value,
      savedAt: Date.now(),
    }
    try { localStorage.setItem(key, JSON.stringify(payload)) } catch { /* */ }
  }

  function loadBrowseState(chId) {
    const key = browseStateKey(chId)
    try {
      const raw = localStorage.getItem(key)
      if (!raw) return null
      const data = JSON.parse(raw)
      if (!data.results?.length) return null
      if (Date.now() - (data.savedAt || 0) > 7 * 24 * 60 * 60 * 1000) {
        localStorage.removeItem(key)
        return null
      }
      return data
    } catch { return null }
  }

  function clearBrowseState(chId) {
    localStorage.removeItem(browseStateKey(chId))
  }

  const scrollToBottomFlag = ref(false)

  function restoreBrowseState(saved) {
    abortSearch()
    results.value = saved.results
    totalResults.value = saved.totalResults
    availableTags.value = saved.availableTags || []
    virtualTags.value = saved.virtualTags || []
    if (saved.banners) banners.value = saved.banners
    includedTags.value = new Set(saved.includedTags || [])
    excludedTags.value = new Set(saved.excludedTags || [])
    tagLogic.value = saved.tagLogic || 'and'
    sortOrder.value = saved.sortOrder || 'desc'
    query.value = saved.keywords || ''
    sortMethod.value = saved.sortMethod || 'comprehensive'
    dateStart.value = saved.dateStart || ''
    dateEnd.value = saved.dateEnd || ''
    isLoading.value = false
    saveStateToUrl()
    scrollToBottomFlag.value = true
  }

  function tryResumeBrowse(chId) {
    const saved = loadBrowseState(chId)
    if (saved) {
      resumePending = true
      resumeData.value = saved
      resumeVisible.value = true
    }
    executeSearch()
  }

  function dismissResume(didRestore = false) {
    resumePending = false
    resumeVisible.value = false
    resumeData.value = null
    if (!didRestore) saveBrowseState()
  }

  function acceptResume() {
    if (resumeData.value) {
      dismissResume(true)
      restoreBrowseState(resumeData.value)
    } else {
      dismissResume()
    }
  }

  function rejectResume() {
    clearBrowseState(channelId.value)
    dismissResume()
  }

  // Computed helpers
  const allTags = computed(() => {
    const base = view.value === 'follows' ? followAvailableTags.value : availableTags.value
    const combined = new Set([...(base || []), ...includedTags.value, ...excludedTags.value])
    return Array.from(combined)
  })

  const virtualTagNames = computed(() => new Set(virtualTags.value || []))

  const canLoadMore = computed(() =>
    view.value === 'search' && results.value.length > 0 && results.value.length < totalResults.value
  )

  return {
    view, channelId, sidebarOpen,
    query, dateStart, dateEnd, sortMethod, sortOrder,
    tagMode, tagLogic, includedTags, excludedTags,
    availableTags, virtualTags, allTags, virtualTagNames,
    results, totalResults, isLoading, banners, canLoadMore,
    followThreads, followTotal, followFetched, followAvailableTags,
    followNeedsRefresh, unreadCount,
    detailPost, detailVisible,
    toastMessage, toastVisible,
    resumeData, resumeVisible,
    scrollToBottomFlag,
    failedImages,

    showToast, openDetail, closeDetail, toggleSidebar,
    executeSearch, loadMore, applyFollowFilters,
    refreshUnreadCount, markFollowsViewedAction, removeFollowAction,
    resetFollowState, switchView, selectChannel,
    handleTagClick, setTagModeAction, setTagLogicAction, toggleSortOrder,
    applyAuthorSearch, saveStateToUrl, loadStateFromUrl,
    saveBrowseState, tryResumeBrowse, acceptResume, rejectResume, dismissResume,
    restoreBrowseState,
  }
})
