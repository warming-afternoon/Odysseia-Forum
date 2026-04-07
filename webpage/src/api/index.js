import { AUTH_URL } from '../config'

let authToken = localStorage.getItem('auth_token')
let onUnauthorized = null

export function setToken(token) {
  authToken = token
  if (token) {
    localStorage.setItem('auth_token', token)
  } else {
    localStorage.removeItem('auth_token')
  }
}

export function getToken() {
  return authToken
}

export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler
}

export async function fetchAPI(endpoint, method = 'GET', body = null, signal = null) {
  const headers = { 'Content-Type': 'application/json' }
  if (authToken) headers['Authorization'] = `Bearer ${authToken}`

  try {
    const opts = { method, headers }
    if (body) opts.body = JSON.stringify(body)
    if (signal) opts.signal = signal

    const r = await fetch(`${AUTH_URL}${endpoint}`, opts)
    if (r.status === 401) {
      onUnauthorized?.()
      return null
    }
    if (r.status === 204) return null
    if (!r.ok) throw new Error(String(r.status))
    return await r.json()
  } catch (e) {
    if (e.name === 'AbortError') return null
    return null
  }
}

export async function searchPosts(params, signal) {
  return fetchAPI('/search', 'POST', params, signal)
}

export async function getThreadDetail(threadId) {
  const id = encodeURIComponent(String(threadId))
  return fetchAPI(`/search/thread/${id}`, 'GET')
}

export async function getFollows(limit = 10000, offset = 0) {
  return fetchAPI(`/follows?limit=${limit}&offset=${offset}`, 'GET')
}

export async function getUnreadCount() {
  return fetchAPI('/follows/unread-count', 'GET')
}

export async function markFollowsViewed() {
  return fetchAPI('/follows/mark-viewed', 'POST', {})
}

export async function removeFollow(threadId) {
  return fetchAPI(`/follows/${threadId}`, 'DELETE')
}

export async function checkAuth() {
  return fetchAPI('/auth/checkauth', 'GET')
}

export async function submitBannerApplication(data) {
  return fetchAPI('/banner/apply', 'POST', data)
}

export async function fetchImages(items) {
  return fetchAPI('/fetch-images', 'POST', { items })
}
