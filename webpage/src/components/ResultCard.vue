<template>
  <div
    ref="cardEl"
    class="card-wrapper cursor-pointer"
    :class="{ 'is-revealed': revealed }"
    @click="handleClick"
  >
    <div class="card-inner">
      <!-- NEW badge -->
      <span
        v-if="store.view === 'follows' && post.has_update"
        class="absolute top-2 right-2 bg-discord-red text-white text-[10px] font-bold px-2 py-0.5 rounded-full shadow-md z-20"
      >NEW</span>

      <!-- Author header -->
      <div class="flex items-center gap-2 px-3 pt-3 md:px-4 md:pt-4 pb-2">
        <img :src="authorAvatar" class="w-8 h-8 rounded-full ring-2 ring-white/10 flex-shrink-0">
        <div class="flex items-baseline gap-2 min-w-0 flex-wrap">
          <span
            class="text-sm font-semibold truncate max-w-[120px] transition-colors"
            :class="authorUsername ? 'text-discord-primary cursor-pointer hover:text-white' : 'text-gray-300'"
            @click.stop="authorUsername && store.applyAuthorSearch(authorUsername)"
          >{{ authorName }}</span>
          <span class="flex items-center gap-1 text-[11px] text-discord-muted flex-shrink-0">
            <span class="material-symbols-outlined text-[13px]">edit_square</span>
            {{ createdTimeAgo }}
          </span>
        </div>
      </div>

      <!-- Title -->
      <h3 class="px-3 md:px-4 text-white font-bold text-sm md:text-base leading-tight mb-2 line-clamp-2 transition-colors"
          :class="revealed ? 'text-discord-primary' : 'group-hover:text-discord-primary'">
        {{ post.title }}
      </h3>

      <!-- === CONTENT AREA (both image & no-image) === -->
      <div class="card-content-area relative mx-3 md:mx-4 rounded-lg overflow-hidden flex-1 min-h-0">
        <!-- Image layer -->
        <MultiImageGrid v-if="showImages" :images="post.thumbnail_urls" :thread-id="String(post.thread_id)" :channel-id="String(post.channel_id || '')" />

        <!-- No-image: text always visible -->
        <div
          v-if="!showImages"
          class="md-content text-xs text-discord-muted p-3 absolute inset-0 card-noimg-excerpt"
          :class="revealed ? 'overflow-y-auto custom-scrollbar' : 'line-clamp-10'"
          @click="handleExcerptClick"
          v-html="parsedExcerpt"
        />

        <!-- Tags strip (both types, visible when overlay hidden) -->
        <div v-if="hasTags" class="card-tags-strip absolute bottom-0 left-0 right-0 p-2 z-10 transition-opacity duration-200"
          :class="showImages ? 'bg-gradient-to-t from-black/70 to-transparent' : 'bg-gradient-to-t from-[#2b2d31] to-transparent'">
          <div class="flex flex-wrap gap-1">
            <span v-for="t in (post.virtual_tags || []).slice(0, 4)" :key="'v-' + t"
              class="text-[10px] bg-indigo-500/30 text-indigo-200 px-1.5 py-0.5 rounded">#{{ t }}</span>
            <span v-for="t in (post.tags || []).slice(0, 4)" :key="'t-' + t"
              class="text-[10px] bg-black/40 text-gray-300 px-1.5 py-0.5 rounded">#{{ t }}</span>
            <span v-if="totalTagCount > 8" class="text-[10px] text-gray-400 px-1 py-0.5">+{{ totalTagCount - 8 }}</span>
          </div>
        </div>

        <!-- Image card: excerpt overlay on hover/tap -->
        <div
          v-if="showImages"
          class="card-excerpt-overlay absolute inset-0 z-10 bg-[#18191cee] p-3 flex flex-col transition-opacity duration-200 opacity-0 pointer-events-none"
          @click="store.openDetail(post)"
        >
          <div
            class="md-content text-xs text-gray-200 leading-relaxed overflow-y-auto flex-1 min-h-0 custom-scrollbar"
            @click.stop="handleExcerptClick"
            v-html="parsedExcerpt"
          />
        </div>
      </div>

      <!-- Footer -->
      <div class="flex items-center justify-between px-3 md:px-4 py-2.5 mt-auto border-t border-white/5 text-[11px] text-discord-muted flex-shrink-0">
        <span class="flex items-center gap-1">
          <span class="material-symbols-outlined text-[13px]">schedule</span>
          {{ activeTimeAgo }}
        </span>
        <div class="flex items-center gap-3">
          <span class="flex items-center gap-0.5">
            <span class="material-symbols-outlined text-[13px]">chat</span> {{ post.reply_count }}
          </span>
          <span class="flex items-center gap-0.5">
            <span class="material-symbols-outlined text-[13px]">favorite</span> {{ post.reaction_count }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useAppStore } from '../stores/app'
import { parseMarkdown, getAuthorAvatar, getAuthorDisplayName, getAuthorUsername, normalizeThumbnailList } from '../utils'
import MultiImageGrid from './MultiImageGrid.vue'

const props = defineProps({
  post: { type: Object, required: true },
})

const store = useAppStore()
const cardEl = ref(null)
const revealed = ref(false)
const canHover = typeof window !== 'undefined' && window.matchMedia('(hover: hover) and (pointer: fine)').matches

const user = computed(() => props.post.author || {})
const authorAvatar = computed(() => getAuthorAvatar(user.value))
const authorName = computed(() => getAuthorDisplayName(user.value))
const authorUsername = computed(() => getAuthorUsername(user.value))
const parsedExcerpt = computed(() => parseMarkdown(props.post.first_message_excerpt))
const hasImages = computed(() => normalizeThumbnailList(props.post.thumbnail_urls).length > 0)
const showImages = computed(() => hasImages.value && !store.dataSaver)
const hasTags = computed(() => (props.post.virtual_tags?.length || 0) + (props.post.tags?.length || 0) > 0)
const totalTagCount = computed(() => (props.post.virtual_tags?.length || 0) + (props.post.tags?.length || 0))

function handleClick() {
  if (canHover) {
    store.openDetail(props.post)
    return
  }
  if (!revealed.value) {
    revealed.value = true
    return
  }
  store.openDetail(props.post)
}

function handleExcerptClick(e) {
  e.stopPropagation()
  if (e.target.closest('a')) return
  if (!canHover && !revealed.value) {
    revealed.value = true
    return
  }
  store.openDetail(props.post)
}

function onDocumentClick(e) {
  if (revealed.value && cardEl.value && !cardEl.value.contains(e.target)) {
    revealed.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', onDocumentClick, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick, true)
})

function formatRelativeTime(dateStr) {
  if (!dateStr) return '未知'
  const diff = Date.now() - new Date(dateStr).getTime()
  if (diff < 0) return '刚刚'
  const m = Math.floor(diff / 60000)
  if (m < 1) return '刚刚'
  if (m < 60) return `${m}分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}小时前`
  const d = Math.floor(h / 24)
  if (d < 30) return `${d}天前`
  if (d < 365) return `${Math.floor(d / 30)}个月前`
  return `>${Math.floor(d / 365)}年前`
}

const createdTimeAgo = computed(() => formatRelativeTime(props.post.created_at))
const activeTimeAgo = computed(() => formatRelativeTime(props.post.last_active_at))
</script>
