<template>
  <router-view v-slot="{ Component }">
    <transition name="page" mode="out-in">
      <component :is="Component" />
    </transition>
  </router-view>

  <!-- Global Toast -->
  <Transition name="toast">
    <div
      v-if="store.toastVisible"
      class="fixed bottom-20 left-1/2 -translate-x-1/2 bg-discord-element/90 backdrop-blur-md text-white px-5 py-2.5 rounded-xl shadow-2xl z-[999] border border-white/10 text-sm"
    >
      {{ store.toastMessage }}
    </div>
  </Transition>
</template>

<script setup>
import { useAppStore } from './stores/app'
const store = useAppStore()
</script>
