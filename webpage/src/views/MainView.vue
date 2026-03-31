<template>
  <div class="bg-discord-bg text-discord-text font-sans h-screen flex overflow-hidden">
    <!-- Sidebar -->
    <AppSidebar />

    <!-- Main content -->
    <main class="flex-1 flex flex-col min-w-0 overflow-hidden relative bg-discord-bg">
      <AppHeader />

      <!-- Scrollable content -->
      <div
        ref="contentScroll"
        class="flex-1 overflow-y-auto p-3 md:p-6 scroll-smooth relative custom-scrollbar"
        @scroll="handleScroll"
      >
        <!-- Banner -->
        <BannerCarousel @apply="showBannerModal = true" />

        <!-- Tags -->
        <TagCloud />

        <!-- Results -->
        <ResultGrid />
      </div>
    </main>

    <!-- Detail overlay -->
    <DetailOverlay />

    <!-- Banner application modal -->
    <BannerApplicationModal :visible="showBannerModal" @close="showBannerModal = false" />

    <!-- Resume toast -->
    <ResumeToast />

    <!-- Advanced filter modal -->
    <AdvancedFilterModal />
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '../stores/app'
import { useAuthStore } from '../stores/auth'
import AppSidebar from '../components/AppSidebar.vue'
import AppHeader from '../components/AppHeader.vue'
import BannerCarousel from '../components/BannerCarousel.vue'
import TagCloud from '../components/TagCloud.vue'
import ResultGrid from '../components/ResultGrid.vue'
import DetailOverlay from '../components/DetailOverlay.vue'
import BannerApplicationModal from '../components/BannerApplicationModal.vue'
import ResumeToast from '../components/ResumeToast.vue'
import AdvancedFilterModal from '../components/AdvancedFilterModal.vue'

const store = useAppStore()
const auth = useAuthStore()
const router = useRouter()
const contentScroll = ref(null)
const showBannerModal = ref(false)

function handleScroll() {
  if (store.view === 'follows') return
  if (store.isLoading || !store.results.length) return
  if (store.results.length >= store.totalResults) return

  const el = contentScroll.value
  if (!el) return
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 200) {
    store.loadMore()
  }
}

function handleResize() {
  if (window.innerWidth >= 768) store.toggleSidebar(false)
}

function handlePopstate() {
  store.loadStateFromUrl()
  store.executeSearch()
}

watch(() => store.scrollToBottomFlag, async (val) => {
  if (val) {
    store.scrollToBottomFlag = false
    await nextTick()
    requestAnimationFrame(() => {
      const el = contentScroll.value
      if (el) el.scrollTop = el.scrollHeight
    })
  }
})

onMounted(async () => {
  auth.handleAuthHash()
  auth.setupUnauthorizedHandler(router)
  store.loadStateFromUrl()

  if (!auth.isLoggedIn) {
    router.push({ name: 'login', query: { redirect: window.location.href } })
    return
  }

  const valid = await auth.verifyAuth()
  if (!valid) {
    router.push({ name: 'login', query: { redirect: window.location.href } })
    return
  }

  store.refreshUnreadCount()
  store.followNeedsRefresh = true
  store.tryResumeBrowse(store.channelId)

  window.addEventListener('resize', handleResize)
  window.addEventListener('popstate', handlePopstate)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  window.removeEventListener('popstate', handlePopstate)
})
</script>
