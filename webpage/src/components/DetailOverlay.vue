<template>
  <Teleport to="body">
    <Transition name="overlay">
      <div
        v-if="store.detailVisible"
        class="detail-overlay"
        @click="store.closeDetail()"
      >
        <Transition name="detail-card" appear>
          <div
            v-if="store.detailPost"
            class="detail-expanded-card"
            @click.stop
          >
            <!-- Close -->
            <button
              class="absolute top-3 right-3 z-20 bg-black/50 hover:bg-black/70 text-white rounded-full p-1.5 backdrop-blur-sm transition-colors"
              @click="store.closeDetail()"
            >
              <span class="material-symbols-outlined text-lg">close</span>
            </button>

            <!-- Image -->
            <div v-if="!store.dataSaver" class="detail-carousel-container w-full relative flex-shrink-0 border-b border-white/10">
              <ImageCarousel
                :images="store.detailPost.thumbnail_urls"
                :contain="true"
                :thread-id="String(store.detailPost.thread_id)"
                :channel-id="String(store.detailPost.channel_id || '')"
              />
            </div>

            <!-- Scrollable content -->
            <div class="flex-1 overflow-y-auto p-4 md:p-5 custom-scrollbar">
              <!-- Tags -->
              <div class="flex flex-wrap gap-1.5 mb-3">
                <span
                  v-for="t in (store.detailPost.virtual_tags || [])"
                  :key="'v-' + t"
                  class="text-[10px] bg-indigo-500/15 text-indigo-300 px-2 py-1 rounded border border-indigo-500/40"
                >#{{ t }}</span>
                <span
                  v-for="t in (store.detailPost.tags || [])"
                  :key="'t-' + t"
                  class="text-[10px] bg-discord-sidebar text-discord-muted px-2 py-1 rounded border border-white/5"
                >#{{ t }}</span>
              </div>

              <!-- Title -->
              <h3 class="text-white font-bold text-lg mb-3 leading-snug">
                {{ store.detailPost.title }}
              </h3>

              <!-- Excerpt -->
              <div class="md-content text-sm text-gray-300 mb-6" v-html="parsedContent" />
            </div>

            <!-- Bottom actions -->
            <div class="p-4 border-t border-white/10 bg-discord-element/80 backdrop-blur-sm flex flex-col gap-3 flex-shrink-0">
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <img :src="authorAvatar" class="w-7 h-7 rounded-full ring-1 ring-white/10">
                  <span
                    class="text-xs transition-colors"
                    :class="authorUsername ? 'text-discord-primary cursor-pointer hover:text-white' : 'text-gray-400'"
                    @click="authorUsername && store.applyAuthorSearch(authorUsername)"
                  >{{ authorDisplayName }}</span>
                </div>
                <div class="flex items-center gap-2">
                  <a
                    :href="links.appLink"
                    class="bg-discord-primary hover:bg-discord-hover text-white px-3 py-1.5 rounded-lg text-xs font-bold flex items-center gap-1 shadow transition-all"
                  >
                    <span class="material-symbols-outlined text-xs">open_in_new</span> APP
                  </a>
                  <a
                    :href="links.webLink"
                    target="_blank"
                    class="bg-discord-sidebar hover:bg-gray-700 text-white px-3 py-1.5 rounded-lg text-xs font-bold flex items-center gap-1 border border-white/10 transition-colors"
                  >
                    <span class="material-symbols-outlined text-xs">public</span> WEB
                  </a>
                </div>
              </div>
              <!-- Unfollow button for follows view -->
              <div v-if="store.view === 'follows'" class="flex justify-center">
                <button
                  @click="handleUnfollow"
                  class="bg-discord-red/20 hover:bg-discord-red text-discord-red hover:text-white px-4 py-1.5 rounded-lg text-xs font-bold border border-discord-red/30 transition-all flex items-center gap-1"
                >
                  <span class="material-symbols-outlined text-xs">remove_circle</span> 取消关注
                </button>
              </div>
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed } from 'vue'
import { useAppStore } from '../stores/app'
import { parseMarkdown, getAuthorAvatar, getAuthorDisplayName, getAuthorUsername, getPostLinks } from '../utils'
import ImageCarousel from './ImageCarousel.vue'

const store = useAppStore()

const user = computed(() => store.detailPost?.author || {})
const authorAvatar = computed(() => getAuthorAvatar(user.value))
const authorDisplayName = computed(() => getAuthorDisplayName(user.value))
const authorUsername = computed(() => getAuthorUsername(user.value))
const parsedContent = computed(() => parseMarkdown(store.detailPost?.first_message_excerpt, true))
const links = computed(() => getPostLinks(store.detailPost || {}, store.view))

function handleUnfollow() {
  if (store.detailPost) {
    store.removeFollowAction(store.detailPost.thread_id)
    store.closeDetail()
  }
}
</script>
