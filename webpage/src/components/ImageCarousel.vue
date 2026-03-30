<template>
  <div v-if="urls.length === 0" class="w-full h-full flex items-center justify-center bg-discord-dark">
    <img :src="placeholder" class="w-full h-full object-cover">
  </div>

  <div v-else-if="urls.length === 1" class="w-full h-full">
    <img
      :src="urls[0]"
      class="w-full h-full"
      :class="contain ? 'object-contain' : 'object-cover'"
      @error="onImgError"
    >
  </div>

  <div v-else class="carousel w-full h-full relative overflow-hidden bg-black">
    <div class="w-full h-full relative">
      <Transition :name="slideDirection">
        <div :key="current" class="absolute inset-0">
          <img
            :src="urls[current]"
            class="w-full h-full"
            :class="contain ? 'object-contain' : 'object-cover'"
            @error="onImgError"
          >
        </div>
      </Transition>
    </div>

    <!-- Nav buttons -->
    <button
      class="carousel-btn left-2"
      @click.stop="prev"
    >
      <span class="material-symbols-outlined text-lg">chevron_left</span>
    </button>
    <button
      class="carousel-btn right-2"
      @click.stop="next"
    >
      <span class="material-symbols-outlined text-lg">chevron_right</span>
    </button>

    <!-- Dots -->
    <div class="absolute bottom-2 left-1/2 -translate-x-1/2 flex gap-1.5 z-10">
      <button
        v-for="(_, i) in urls"
        :key="i"
        class="carousel-dot"
        :class="i === current ? 'active' : ''"
        @click.stop="current = i"
      />
    </div>

    <!-- Counter -->
    <div class="absolute top-2 right-2 bg-black/60 backdrop-blur-sm text-white text-[11px] px-2 py-0.5 rounded-full z-10">
      {{ current + 1 }}/{{ urls.length }}
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { getPlaceholderImage } from '../utils'
import { scheduleImageRecovery } from '../composables/useImageRecovery'

const props = defineProps({
  images: { type: Array, default: () => [] },
  contain: { type: Boolean, default: false },
  threadId: { type: String, default: '' },
  channelId: { type: String, default: '' },
})

const placeholder = getPlaceholderImage('600x400')
const urls = computed(() =>
  (props.images || []).filter((u) => typeof u === 'string' && u.length)
)

const current = ref(0)
const slideDirection = ref('slide-left')

function prev() {
  slideDirection.value = 'slide-right'
  current.value = (current.value - 1 + urls.value.length) % urls.value.length
}

function next() {
  slideDirection.value = 'slide-left'
  current.value = (current.value + 1) % urls.value.length
}

function onImgError(e) {
  e.target.onerror = null
  e.target.src = placeholder
  if (props.threadId) {
    scheduleImageRecovery(props.threadId, props.channelId)
  }
}
</script>
