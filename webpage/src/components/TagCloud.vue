<template>
  <div v-if="store.allTags.length > 0" class="mb-4">
    <!-- Controls -->
    <div class="flex items-center gap-3 bg-discord-element/60 px-3 py-2 rounded-lg w-full overflow-x-auto border border-white/5 mb-3 hide-scrollbar">
      <button
        class="text-xs font-bold px-2.5 py-1 rounded-md flex-shrink-0 transition-all"
        :class="store.tagLogic === 'and' ? 'bg-discord-primary text-white shadow-sm shadow-discord-primary/20' : 'text-discord-muted hover:text-white'"
        @click="store.setTagLogicAction('and')"
      >AND</button>
      <button
        class="text-xs font-bold px-2.5 py-1 rounded-md flex-shrink-0 transition-all"
        :class="store.tagLogic === 'or' ? 'bg-discord-primary text-white shadow-sm shadow-discord-primary/20' : 'text-discord-muted hover:text-white'"
        @click="store.setTagLogicAction('or')"
      >OR</button>

      <div class="w-px h-4 bg-white/10 mx-1" />

      <button
        class="flex items-center gap-1 text-xs px-2.5 py-1 rounded-md flex-shrink-0 transition-all border"
        :class="store.tagMode === 'include'
          ? 'bg-discord-green/20 text-discord-green border-discord-green/50'
          : 'text-discord-muted border-transparent hover:border-discord-green/30 hover:text-discord-green'"
        @click="store.setTagModeAction('include')"
      >
        <span class="material-symbols-outlined text-[14px]">add</span> 包含
      </button>
      <button
        class="flex items-center gap-1 text-xs px-2.5 py-1 rounded-md flex-shrink-0 transition-all border"
        :class="store.tagMode === 'exclude'
          ? 'bg-discord-red/20 text-discord-red border-discord-red/50'
          : 'text-discord-muted border-transparent hover:border-discord-red/30 hover:text-discord-red'"
        @click="store.setTagModeAction('exclude')"
      >
        <span class="material-symbols-outlined text-[14px]">block</span> 排除
      </button>
    </div>

    <!-- Tag pills -->
    <div class="flex flex-wrap gap-2 max-h-24 overflow-y-auto custom-scrollbar">
      <button
        v-for="tag in store.allTags"
        :key="tag"
        class="tag-pill text-xs px-2.5 py-1 rounded-md flex items-center gap-1 transition-all"
        :class="tagClass(tag)"
        @click="store.handleTagClick(tag)"
      >
        <span v-if="tagIcon(tag)" class="material-symbols-outlined text-[12px]">{{ tagIcon(tag) }}</span>
        #{{ tag }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { useAppStore } from '../stores/app'

const store = useAppStore()

function tagClass(tag) {
  const isVirtual = store.virtualTagNames.has(tag)
  if (store.includedTags.has(tag)) {
    return isVirtual
      ? 'bg-indigo-500/30 border border-indigo-400 text-indigo-200'
      : 'bg-discord-green/20 border border-discord-green text-discord-green'
  }
  if (store.excludedTags.has(tag)) {
    return isVirtual
      ? 'bg-red-500/20 border border-red-400/60 text-red-300'
      : 'bg-discord-red/20 border border-discord-red text-discord-red'
  }
  if (isVirtual) {
    return 'bg-indigo-500/15 border border-indigo-500/40 text-indigo-300 hover:border-indigo-400'
  }
  return 'bg-discord-element/80 border border-transparent text-discord-muted hover:border-gray-500 hover:text-gray-300'
}

function tagIcon(tag) {
  if (store.includedTags.has(tag)) return 'check'
  if (store.excludedTags.has(tag)) return 'block'
  return null
}
</script>
