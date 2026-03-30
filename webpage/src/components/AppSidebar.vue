<template>
  <!-- Backdrop -->
  <Transition name="backdrop">
    <div
      v-if="store.sidebarOpen"
      class="fixed inset-0 bg-black/70 z-30 md:hidden"
      @click="store.toggleSidebar(false)"
    />
  </Transition>

  <!-- Sidebar -->
  <aside
    class="fixed inset-y-0 left-0 z-40 w-72 bg-discord-sidebar flex flex-col border-r border-white/5 shadow-2xl md:shadow-none md:static md:w-64 md:flex-shrink-0 transition-transform duration-300 ease-[cubic-bezier(0.4,0,0.2,1)]"
    :class="store.sidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'"
  >
    <!-- Header -->
    <div class="p-4 font-bold text-lg text-white border-b border-white/5 flex items-center justify-between">
      <div class="flex items-center gap-2.5">
        <img
          src="https://cdn.discordapp.com/icons/1134557553011998840/d419091a2a50009ddee0617ac43b0ead.png"
          alt="类脑ΟΔΥΣΣΕΙΑ"
          class="w-8 h-8 rounded-full ring-2 ring-discord-primary/30"
        >
        <span class="tracking-tight">类脑ΟΔΥΣΣΕΙΑ</span>
      </div>
      <button class="md:hidden text-discord-muted hover:text-white transition-colors" @click="store.toggleSidebar(false)">
        <span class="material-symbols-outlined">close</span>
      </button>
    </div>

    <!-- Navigation -->
    <nav class="flex-1 overflow-y-auto p-2 space-y-1 custom-scrollbar">
      <!-- Search -->
      <button
        class="nav-btn"
        :class="store.view === 'search' ? 'nav-btn-active' : 'nav-btn-inactive'"
        @click="store.switchView('search')"
      >
        <span class="material-symbols-outlined text-[18px]">search</span>
        <span>搜索发现</span>
      </button>

      <!-- Follows -->
      <button
        class="nav-btn relative"
        :class="store.view === 'follows' ? 'nav-btn-active' : 'nav-btn-inactive'"
        @click="store.switchView('follows')"
      >
        <span class="material-symbols-outlined text-[18px]">bookmarks</span>
        <span>关注列表</span>
        <Transition name="badge">
          <span
            v-if="store.unreadCount > 0"
            class="absolute right-2 bg-discord-red text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full min-w-[18px] text-center"
          >
            {{ Math.min(store.unreadCount, 99) }}
          </span>
        </Transition>
      </button>

      <!-- All Channels -->
      <div class="pt-3 pb-1">
        <button
          class="nav-btn"
          :class="!store.channelId ? 'nav-btn-active' : 'nav-btn-inactive'"
          @click="store.selectChannel('global')"
        >
          <span class="material-symbols-outlined text-[18px]">apps</span>
          <span>全部频道</span>
        </button>
      </div>

      <!-- Channel categories -->
      <div v-for="category in CHANNEL_CATEGORIES" :key="category.name">
        <div class="mt-4 mb-2 px-3 text-[11px] font-bold text-discord-muted uppercase tracking-wider">
          {{ category.name }}
        </div>
        <div class="space-y-0.5">
          <button
            v-for="ch in category.channels"
            :key="ch.id"
            class="nav-btn"
            :class="store.channelId === ch.id ? 'nav-btn-active' : 'nav-btn-inactive'"
            @click="store.selectChannel(ch.id)"
          >
            <span class="material-symbols-outlined text-[18px]">{{ ch.icon || 'tag' }}</span>
            <span>{{ ch.name }}</span>
          </button>
        </div>
      </div>
    </nav>

    <!-- User Area -->
    <div class="p-3 bg-discord-element/50 border-t border-white/5">
      <div v-if="auth.user" class="flex items-center gap-2.5">
        <img :src="auth.avatarUrl" class="w-8 h-8 rounded-full ring-2 ring-white/10">
        <div class="flex-1 min-w-0">
          <div class="text-xs font-bold text-white truncate">{{ auth.user.global_name }}</div>
        </div>
        <button @click="auth.logout()" class="text-discord-muted hover:text-discord-red transition-colors p-1 rounded hover:bg-white/5">
          <span class="material-symbols-outlined text-[20px]">logout</span>
        </button>
      </div>
      <button v-else @click="auth.login()" class="w-full bg-discord-primary hover:bg-discord-hover text-white py-2.5 rounded-lg text-sm font-medium transition-colors">
        Discord 登录
      </button>
    </div>
  </aside>
</template>

<script setup>
import { CHANNEL_CATEGORIES } from '../config'
import { useAppStore } from '../stores/app'
import { useAuthStore } from '../stores/auth'

const store = useAppStore()
const auth = useAuthStore()
</script>
