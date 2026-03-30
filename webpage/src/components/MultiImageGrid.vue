<template>
  <!-- No images -->
  <div v-if="count === 0" class="w-full h-full">
    <img :src="getPlaceholderImage()" class="w-full h-full object-cover">
  </div>

  <!-- Single image -->
  <div v-else-if="count === 1" class="w-full h-full">
    <img :src="urls[0]" class="w-full h-full object-cover" @error="onImgError">
  </div>

  <!-- 2 images -->
  <div v-else-if="count === 2" class="w-full h-full grid grid-cols-2 gap-0.5 bg-discord-dark">
    <div v-for="(url, i) in urls.slice(0, 2)" :key="i" class="overflow-hidden">
      <img :src="url" class="w-full h-full object-cover hover:scale-105 transition-transform duration-300" @error="onImgError">
    </div>
  </div>

  <!-- 3 images -->
  <div v-else-if="count === 3" class="w-full h-full grid grid-cols-[1.5fr_1fr] grid-rows-2 gap-0.5 bg-discord-dark">
    <div class="row-span-2 overflow-hidden">
      <img :src="urls[0]" class="w-full h-full object-cover hover:scale-105 transition-transform duration-300" @error="onImgError">
    </div>
    <div v-for="i in [1, 2]" :key="i" class="overflow-hidden">
      <img :src="urls[i]" class="w-full h-full object-cover hover:scale-105 transition-transform duration-300" @error="onImgError">
    </div>
  </div>

  <!-- 4+ images -->
  <div v-else class="w-full h-full grid grid-cols-2 grid-rows-2 gap-0.5 bg-discord-dark">
    <div v-for="(url, i) in urls.slice(0, 4)" :key="i" class="overflow-hidden relative">
      <img :src="url" class="w-full h-full object-cover hover:scale-105 transition-transform duration-300" @error="onImgError">
      <div v-if="i === 3 && remaining > 0" class="absolute inset-0 bg-black/50 flex items-center justify-center">
        <span class="text-white text-xl font-bold drop-shadow-lg">+{{ remaining }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { getPlaceholderImage } from '../utils'
import { scheduleImageRecovery } from '../composables/useImageRecovery'

const props = defineProps({
  images: { type: Array, default: () => [] },
  threadId: { type: String, default: '' },
  channelId: { type: String, default: '' },
})

const urls = computed(() =>
  (props.images || []).filter((u) => typeof u === 'string' && u.length)
)
const count = computed(() => urls.value.length)
const remaining = computed(() => Math.max(0, count.value - 4))

function onImgError(e) {
  e.target.onerror = null
  e.target.src = getPlaceholderImage()
  if (props.threadId) {
    scheduleImageRecovery(props.threadId, props.channelId)
  }
}
</script>
