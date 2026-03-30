<template>
  <div
    class="card-wrapper group cursor-pointer"
    @click="store.openDetail(post)"
  >
    <div class="card-inner">
      <!-- NEW badge -->
      <span
        v-if="store.view === 'follows' && post.has_update"
        class="absolute top-2 right-2 bg-discord-red text-white text-[10px] font-bold px-2 py-0.5 rounded-full shadow-md z-20"
      >NEW</span>

      <!-- Image area -->
      <div v-if="hasImages && !store.dataSaver" class="card-image-container overflow-hidden">
        <MultiImageGrid :images="post.thumbnail_urls" :thread-id="String(post.thread_id)" :channel-id="String(post.channel_id || '')" />
      </div>

      <!-- Content -->
      <div class="p-3 md:p-4 flex flex-col flex-1 min-h-0 bg-discord-element">
        <!-- Tags -->
        <div class="flex flex-wrap gap-1 mb-2 flex-shrink-0">
          <span
            v-for="t in (post.virtual_tags || [])"
            :key="'v-' + t"
            class="text-[10px] bg-indigo-500/15 text-indigo-300 px-1.5 py-0.5 rounded border border-indigo-500/40"
          >#{{ t }}</span>
          <span
            v-for="t in (post.tags || [])"
            :key="'t-' + t"
            class="text-[10px] bg-discord-sidebar text-discord-muted px-1.5 py-0.5 rounded border border-white/5"
          >#{{ t }}</span>
        </div>

        <!-- Title -->
        <h3 class="text-white font-bold text-sm md:text-base leading-tight mb-2 line-clamp-2 group-hover:text-discord-primary transition-colors">
          {{ post.title }}
        </h3>

        <!-- Excerpt -->
        <div
          class="md-content text-xs text-discord-muted mb-2 flex-1"
          :class="(hasImages && !store.dataSaver) ? 'line-clamp-3' : 'line-clamp-8'"
          v-html="parsedExcerpt"
        />

        <!-- Footer -->
        <div class="flex items-center justify-between pt-2 border-t border-white/5 mt-auto opacity-80 flex-shrink-0">
          <div class="flex items-center gap-2">
            <img :src="authorAvatar" class="w-4 h-4 rounded-full">
            <span
              class="text-[10px] truncate max-w-[80px] transition-colors"
              :class="authorUsername ? 'text-discord-primary cursor-pointer hover:text-white' : 'text-gray-400'"
              @click.stop="authorUsername && store.applyAuthorSearch(authorUsername)"
            >{{ authorName }}</span>
          </div>
          <div class="flex items-center gap-2.5 text-discord-muted text-[10px]">
            <span class="flex items-center gap-0.5">
              <span class="material-symbols-outlined text-[12px]">chat</span> {{ post.reply_count }}
            </span>
            <span class="flex items-center gap-0.5">
              <span class="material-symbols-outlined text-[12px]">favorite</span> {{ post.reaction_count }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useAppStore } from '../stores/app'
import { parseMarkdown, getAuthorAvatar, getAuthorDisplayName, getAuthorUsername, normalizeThumbnailList } from '../utils'
import MultiImageGrid from './MultiImageGrid.vue'

const props = defineProps({
  post: { type: Object, required: true },
})

const store = useAppStore()
const user = computed(() => props.post.author || {})
const authorAvatar = computed(() => getAuthorAvatar(user.value))
const authorName = computed(() => getAuthorDisplayName(user.value))
const authorUsername = computed(() => getAuthorUsername(user.value))
const parsedExcerpt = computed(() => parseMarkdown(props.post.first_message_excerpt))
const hasImages = computed(() => normalizeThumbnailList(props.post.thumbnail_urls).length > 0)
</script>
