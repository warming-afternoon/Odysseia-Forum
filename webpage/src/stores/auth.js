import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { checkAuth as apiCheckAuth, setToken, getToken, setUnauthorizedHandler } from '../api'
import { AUTH_URL } from '../config'

export const useAuthStore = defineStore('auth', () => {
  const user = ref(null)
  const token = ref(getToken())

  const isLoggedIn = computed(() => !!token.value)
  const avatarUrl = computed(() => {
    if (!user.value) return null
    return user.value.avatar
      ? `https://cdn.discordapp.com/avatars/${user.value.id}/${user.value.avatar}.png`
      : 'https://cdn.discordapp.com/embed/avatars/0.png'
  })

  function handleAuthHash() {
    // Token is already extracted from hash in main.js before router init.
    // Here we just sync the in-memory state from localStorage.
    const stored = getToken()
    if (stored && stored !== token.value) {
      token.value = stored
      return true
    }
    return false
  }

  async function verifyAuth() {
    if (!token.value) return false
    const d = await apiCheckAuth()
    if (d && d.loggedIn) {
      user.value = d.user
      return true
    }
    return false
  }

  function login() {
    window.location.href = `${AUTH_URL}/auth/login`
  }

  function logout(redirect = true) {
    setToken(null)
    token.value = null
    user.value = null
    if (redirect) {
      window.location.href = `${AUTH_URL}/auth/logout`
    }
  }

  function setupUnauthorizedHandler(router) {
    setUnauthorizedHandler(() => {
      logout(false)
      router.push({ name: 'login', query: { redirect: window.location.href } })
    })
  }

  return {
    user, token, isLoggedIn, avatarUrl,
    handleAuthHash, verifyAuth, login, logout, setupUnauthorizedHandler,
  }
})
