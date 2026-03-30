<template>
  <div>
    <!-- Results header -->
    <div class="mb-4 flex items-center justify-between">
      <h2 class="text-lg font-semibold text-white flex items-center gap-2">
        <span class="material-symbols-outlined text-discord-primary">{{ store.view === 'search' ? 'search' : 'bookmarks' }}</span>
        {{ store.view === 'search' ? '搜索结果' : '关注列表' }}
      </h2>
      <div class="text-xs text-discord-muted bg-discord-element/50 px-3 py-1 rounded-full">
        找到 {{ store.totalResults }} 结果
      </div>
    </div>

    <!-- Empty state -->
    <div v-if="!store.isLoading && store.results.length === 0" class="col-span-full text-center py-16 text-discord-muted">
      <span class="material-symbols-outlined text-6xl mb-4 opacity-30 block">search_off</span>
      <p class="text-lg">没有找到相关帖子</p>
      <p class="text-sm mt-1 opacity-70">试试调整搜索条件</p>
    </div>

    <!-- Grid -->
    <TransitionGroup
      name="card"
      tag="div"
      class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 md:gap-5"
      v-else
    >
      <ResultCard
        v-for="post in store.results"
        :key="post.thread_id"
        :post="post"
      />
    </TransitionGroup>

    <!-- Load More -->
    <div class="mt-8 flex justify-center pb-10" v-if="store.canLoadMore && !store.isLoading">
      <button
        class="px-8 py-2.5 bg-discord-element hover:bg-discord-sidebar rounded-full text-white font-medium transition-all disabled:opacity-50 shadow-lg border border-white/5 hover:border-white/10 hover:shadow-xl"
        @click="store.loadMore()"
      >
        加载更多
      </button>
    </div>

    <!-- Spinner -->
    <div v-if="store.isLoading" class="mt-12 flex justify-center">
      <div class="animate-spin rounded-full h-10 w-10 border-2 border-discord-primary border-t-transparent" />
    </div>
  </div>
</template>

<script setup>
import { useAppStore } from '../stores/app'
import ResultCard from './ResultCard.vue'

const store = useAppStore()
</script>
