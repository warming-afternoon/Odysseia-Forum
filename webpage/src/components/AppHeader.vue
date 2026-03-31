<template>
  <header class="bg-discord-sidebar/95 backdrop-blur-md border-b border-white/5 p-3 md:p-4 shadow-sm z-20 sticky top-0">
    <div class="flex flex-col gap-3">
      <!-- Search row -->
      <div class="flex gap-2 items-center">
        <button
          class="md:hidden p-2 text-discord-muted hover:text-white -ml-1 rounded-lg hover:bg-white/5 transition-colors"
          @click="store.toggleSidebar(true)"
        >
          <span class="material-symbols-outlined">menu</span>
        </button>
        <div class="relative flex-1">
          <input
            v-model="store.query"
            class="w-full bg-discord-element/80 text-white placeholder-discord-muted rounded-lg px-4 py-2.5 pl-10 focus:outline-none focus:ring-2 focus:ring-discord-primary/50 transition-all text-sm border border-white/5 focus:border-discord-primary/30"
            placeholder="搜索关键词..."
            type="text"
            @input="onInput"
            @keydown.enter="store.executeSearch()"
          >
          <span class="material-symbols-outlined absolute left-3 top-2.5 text-discord-muted text-[18px]">search</span>
        </div>
        <button
          class="bg-discord-primary hover:bg-discord-hover text-white px-5 py-2.5 rounded-lg font-medium transition-all text-sm shadow-md shadow-discord-primary/20 active:scale-95"
          @click="store.executeSearch()"
        >
          搜索
        </button>
      </div>

      <!-- Filter row -->
      <div class="flex items-center gap-2 overflow-x-auto pb-0.5 hide-scrollbar text-xs md:text-sm">
        <button
          class="flex items-center gap-1.5 px-3 py-2 rounded-lg flex-shrink-0 border transition-colors"
          :class="store.hasAdvancedFilters
            ? 'bg-discord-primary/15 border-discord-primary/40 text-discord-primary'
            : 'bg-discord-element/60 border-white/5 text-discord-muted hover:text-white hover:border-white/15'"
          @click="store.advancedFilterVisible = true"
        >
          <span class="material-symbols-outlined text-[16px]">tune</span>
          <span>高级筛选</span>
          <span
            v-if="store.hasAdvancedFilters"
            class="w-1.5 h-1.5 rounded-full bg-discord-primary flex-shrink-0"
          />
        </button>
        <div class="sort-select-wrapper">
          <span class="text-discord-muted material-symbols-outlined text-[16px]">sort</span>
          <select v-model="store.sortMethod" class="sort-select" @change="store.executeSearch()">
            <option value="comprehensive">综合排序</option>
            <option value="last_active">最近活跃</option>
            <option value="created_at">最新发布</option>
            <option value="reply_count">回复最多</option>
          </select>
          <span class="sort-select-arrow material-symbols-outlined">expand_more</span>
        </div>
        <button
          class="p-2 rounded-lg hover:bg-white/10 text-discord-muted hover:text-white transition-colors"
          @click="store.toggleSortOrder()"
        >
          <span class="material-symbols-outlined text-[18px]">
            {{ store.sortOrder === 'asc' ? 'arrow_upward' : 'arrow_downward' }}
          </span>
        </button>

        <div class="w-px h-5 bg-white/10 flex-shrink-0" />

        <button
          class="p-2 rounded-lg transition-colors flex-shrink-0"
          :class="store.dataSaver ? 'bg-discord-primary/20 text-discord-primary' : 'text-discord-muted hover:text-white hover:bg-white/10'"
          :title="store.dataSaver ? '省流模式：已开启' : '省流模式：已关闭'"
          @click="store.toggleDataSaver()"
        >
          <span class="material-symbols-outlined text-[18px]">
            {{ store.dataSaver ? 'image_not_supported' : 'image' }}
          </span>
        </button>
      </div>
    </div>
  </header>
</template>

<script setup>
import { useAppStore } from '../stores/app'

const store = useAppStore()

let typingTimer = null
function onInput() {
  clearTimeout(typingTimer)
  typingTimer = setTimeout(() => {
    if (store.view === 'follows') {
      store.applyFollowFilters()
    } else {
      store.executeSearch()
    }
  }, 600)
}
</script>
