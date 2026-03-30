<template>
  <div class="login-bg min-h-screen flex items-center justify-center font-sans text-discord-text p-4">
    <!-- Decorative dots -->
    <div class="fixed inset-0 overflow-hidden pointer-events-none">
      <div class="deco-dot" style="top: 15%; left: 10%;" />
      <div class="deco-dot" style="top: 25%; left: 85%;" />
      <div class="deco-dot" style="top: 70%; left: 15%;" />
      <div class="deco-dot" style="top: 80%; left: 90%;" />
      <div class="deco-dot" style="top: 45%; left: 5%;" />
      <div class="deco-dot" style="top: 55%; left: 95%;" />
    </div>

    <!-- Card -->
    <div class="relative z-10 w-full max-w-md">
      <div class="login-card bg-discord-sidebar rounded-2xl p-8 md:p-10 border border-white/10">
        <!-- Logo -->
        <div class="flex flex-col items-center mb-8">
          <div class="relative mb-6">
            <div class="logo-ring" />
            <img
              src="https://cdn.discordapp.com/icons/1134557553011998840/d419091a2a50009ddee0617ac43b0ead.png"
              alt="类脑ΟΔΥΣΣΕΙΑ"
              class="w-20 h-20 rounded-full border-4 border-discord-element shadow-2xl relative z-10"
            >
          </div>
          <h1 class="text-2xl md:text-3xl font-bold text-white mb-2">类脑ΟΔΥΣΣΕΙΑ</h1>
          <p class="text-discord-muted text-sm text-center">社区帖子索引 · 搜索 · 发现</p>
        </div>

        <!-- Welcome -->
        <div class="text-center mb-8">
          <h2 class="text-lg font-semibold text-white mb-2">欢迎回来</h2>
          <p class="text-discord-muted text-sm leading-relaxed">
            使用 Discord 账号登录以访问索引页面，<br>
            查看、搜索和关注社区帖子。
          </p>
        </div>

        <!-- Login button -->
        <button
          @click="handleLogin"
          class="btn-discord w-full py-4 rounded-xl text-white font-bold text-base flex items-center justify-center gap-3 mb-6"
        >
          <svg class="w-6 h-6" viewBox="0 0 24 24" fill="currentColor">
            <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028 14.09 14.09 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z"/>
          </svg>
          使用 Discord 登录
        </button>
      </div>

      <!-- Footer -->
      <div class="text-center mt-6 text-discord-muted text-xs">
        <p>登录即表示您同意遵守社区规则</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { AUTH_URL } from '../config'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

function handleLogin() {
  const redirect = route.query.redirect
  if (redirect) sessionStorage.setItem('login_redirect', redirect)
  window.location.href = `${AUTH_URL}/auth/login`
}

function handleCallback() {
  const hash = window.location.hash
  const match = hash.match(/[#&]token=([^&]+)/)
  if (match) {
    auth.handleAuthHash()
    redirectBack()
  }
}

function redirectBack() {
  const redirect = route.query.redirect || sessionStorage.getItem('login_redirect')
  sessionStorage.removeItem('login_redirect')
  if (redirect && !redirect.includes('/login')) {
    window.location.href = redirect
  } else {
    router.push({ name: 'main' })
  }
}

onMounted(async () => {
  handleCallback()
  if (auth.isLoggedIn) {
    const valid = await auth.verifyAuth()
    if (valid) redirectBack()
  }
})
</script>
