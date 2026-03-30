<template>
  <Teleport to="body">
    <Transition name="resume">
      <div
        v-if="store.resumeVisible && store.resumeData"
        class="fixed z-[9999] bottom-4 right-4 max-md:right-2 max-md:left-2"
      >
        <div class="bg-discord-sidebar/95 backdrop-blur-md border border-white/10 rounded-xl p-4 shadow-2xl max-w-xs max-md:max-w-none">
          <div class="flex items-start gap-3">
            <div class="flex-1 min-w-0">
              <p class="text-white text-sm font-medium mb-1">
                上次浏览了 {{ store.resumeData.results.length }}/{{ store.resumeData.totalResults }} 个结果
                <span class="text-discord-muted text-xs">({{ timeAgo }})</span>
              </p>
              <p class="text-discord-muted text-xs truncate">
                {{ filterText || '从上次的位置继续？' }}
              </p>
            </div>
            <button @click="store.dismissResume()" class="text-discord-muted hover:text-white flex-shrink-0 mt-0.5 transition-colors">
              <span class="material-symbols-outlined text-base">close</span>
            </button>
          </div>
          <div class="flex gap-2 mt-3">
            <button
              @click="store.acceptResume()"
              class="flex-1 bg-discord-primary hover:bg-discord-primary/80 text-white text-xs font-bold py-1.5 rounded-lg transition-colors"
            >恢复</button>
            <button
              @click="store.rejectResume()"
              class="flex-1 bg-discord-element hover:bg-discord-element/80 text-discord-muted text-xs font-bold py-1.5 rounded-lg transition-colors border border-white/10"
            >忽略</button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, watch } from 'vue'
import { useAppStore } from '../stores/app'
import { formatTimeAgo } from '../utils'

const store = useAppStore()

const timeAgo = computed(() => {
  if (!store.resumeData?.savedAt) return ''
  return formatTimeAgo(store.resumeData.savedAt)
})

const filterText = computed(() => {
  if (!store.resumeData) return ''
  const parts = []
  if (store.resumeData.keywords) parts.push(store.resumeData.keywords)
  if (store.resumeData.includedTags?.length) {
    parts.push(store.resumeData.includedTags.map((t) => '#' + t).join(' '))
  }
  return parts.length ? '条件: ' + parts.join(' · ') : ''
})

let autoCloseTimer = null
watch(() => store.resumeVisible, (val) => {
  clearTimeout(autoCloseTimer)
  if (val) {
    autoCloseTimer = setTimeout(() => store.dismissResume(), 15000)
  }
})
</script>
