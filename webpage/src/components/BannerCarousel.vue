<template>
  <div class="banner-section" :class="{ collapsed: isCollapsed }" v-if="store.view === 'search'">
    <!-- Main Banner -->
    <div class="banner-container relative w-full rounded-xl overflow-hidden shadow-lg bg-discord-element border border-white/5 group">
      <div class="banner-slides-wrapper">
        <TransitionGroup name="banner-fade">
          <div
            v-for="(banner, idx) in displayBanners"
            :key="idx"
            v-show="idx === currentIndex"
            class="banner-slide"
            :class="{ 'cursor-pointer': !!banner.thread_id }"
            @click="onBannerVisualClick"
          >
            <div
              class="banner-blur-bg"
              :style="{ backgroundImage: `url('${banner.cover_image_url}')` }"
            />
            <img
              :src="banner.cover_image_url"
              @error="e => e.target.src = '/banner.png'"
            >
          </div>
        </TransitionGroup>
      </div>

      <!-- Info overlay -->
      <div
        class="banner-overlay absolute bottom-0 left-0 w-full bg-gradient-to-t from-discord-dark via-discord-dark/80 to-transparent p-4 pt-12 flex flex-col justify-end z-[5]"
        :class="{ 'cursor-pointer': !!currentBanner?.thread_id }"
        @click="onBannerVisualClick"
      >
        <div class="flex items-end justify-between gap-4">
          <div class="flex-1 min-w-0">
            <h2 class="text-lg md:text-2xl font-bold text-white mb-1 drop-shadow-lg line-clamp-1">
              {{ currentBanner?.title || '欢迎来到类脑索引' }}
            </h2>
            <p class="text-xs text-gray-300 flex items-center gap-1 drop-shadow">
              <span class="material-symbols-outlined text-xs text-yellow-400">star</span> 推荐
            </p>
          </div>
          <div v-if="currentBanner?.thread_id" class="flex-shrink-0 flex items-center gap-2">
            <button
              @click.stop="openApp"
              class="bg-discord-primary hover:bg-discord-hover text-white px-3 py-1.5 rounded-lg text-xs font-bold flex items-center gap-1 shadow transition-all hover:shadow-discord-primary/30"
            >
              <span class="material-symbols-outlined text-sm">open_in_new</span> APP
            </button>
            <button
              @click.stop="openWeb"
              class="bg-discord-sidebar hover:bg-gray-700 text-white px-3 py-1.5 rounded-lg text-xs font-bold flex items-center gap-1 border border-white/10 transition-colors"
            >
              <span class="material-symbols-outlined text-sm">public</span> WEB
            </button>
          </div>
        </div>
      </div>

      <!-- Nav -->
      <template v-if="displayBanners.length > 1">
        <button class="banner-nav-btn left-3" @click="prevSlide">
          <span class="material-symbols-outlined">chevron_left</span>
        </button>
        <button class="banner-nav-btn right-3" @click="nextSlide">
          <span class="material-symbols-outlined">chevron_right</span>
        </button>
        <div class="absolute bottom-3 left-1/2 -translate-x-1/2 flex gap-2 z-10">
          <button
            v-for="(_, i) in displayBanners"
            :key="i"
            class="banner-dot"
            :class="{ active: i === currentIndex }"
            @click="goTo(i)"
          />
        </div>
      </template>

      <!-- Collapse button -->
      <button class="banner-collapse-btn" @click="toggleCollapse" title="折叠/展开Banner">
        <span class="material-symbols-outlined">expand_less</span>
      </button>

      <!-- Apply button -->
      <button class="banner-apply-btn" @click="$emit('apply')" title="申请Banner展示位">
        <span class="material-symbols-outlined">add_photo_alternate</span>
        <span class="banner-apply-text">申请</span>
      </button>
    </div>

    <!-- Collapsed bar -->
    <div class="banner-collapsed-bar" @click="toggleCollapse">
      <span class="material-symbols-outlined text-sm">image</span>
      <span>展开Banner</span>
      <span class="material-symbols-outlined text-sm">expand_more</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useAppStore } from '../stores/app'
import { GUILD_ID } from '../config'
import { getThreadDetail } from '../api'

defineEmits(['apply'])

const store = useAppStore()
const currentIndex = ref(0)
const isCollapsed = ref(localStorage.getItem('banner_collapsed') === 'true')
let autoplayTimer = null

const displayBanners = computed(() => {
  const b = store.banners || []
  return b.length ? b : [{ title: '欢迎来到类脑索引', cover_image_url: '/banner.png', thread_id: null }]
})

const currentBanner = computed(() => displayBanners.value[currentIndex.value])

function guildIdForBanner(b) {
  const g = b?.guild_id
  if (g != null && String(g).trim() !== '' && String(g) !== '0') return String(g)
  return String(GUILD_ID || '@me')
}

async function onBannerVisualClick() {
  const b = currentBanner.value
  if (!b?.thread_id) return
  const tid = String(b.thread_id)
  const fromList = store.results.find((p) => String(p.thread_id) === tid)
  if (fromList) {
    store.openDetail(fromList)
    return
  }
  const detail = await getThreadDetail(tid)
  if (detail) {
    store.openDetail(detail)
    return
  }
  store.showToast('无法加载帖子详情，请使用 APP / WEB 打开')
}

function goTo(i) {
  currentIndex.value = i
  restartAutoplay()
}

function prevSlide() {
  currentIndex.value = (currentIndex.value - 1 + displayBanners.value.length) % displayBanners.value.length
  restartAutoplay()
}

function nextSlide() {
  currentIndex.value = (currentIndex.value + 1) % displayBanners.value.length
  restartAutoplay()
}

function startAutoplay() {
  stopAutoplay()
  if (displayBanners.value.length <= 1) return
  autoplayTimer = setInterval(nextSlide, 5000)
}

function stopAutoplay() {
  if (autoplayTimer) { clearInterval(autoplayTimer); autoplayTimer = null }
}

function restartAutoplay() {
  startAutoplay()
}

function toggleCollapse() {
  isCollapsed.value = !isCollapsed.value
  localStorage.setItem('banner_collapsed', isCollapsed.value)
  if (isCollapsed.value) stopAutoplay()
  else startAutoplay()
}

function openApp() {
  const b = currentBanner.value
  if (b?.thread_id) {
    const gid = guildIdForBanner(b)
    window.location.href = `discord://discord.com/channels/${gid}/${b.thread_id}`
  }
}

function openWeb() {
  const b = currentBanner.value
  if (b?.thread_id) {
    const gid = guildIdForBanner(b)
    window.open(`https://discord.com/channels/${gid}/${b.thread_id}`, '_blank')
  }
}

onMounted(() => { if (!isCollapsed.value) startAutoplay() })
onUnmounted(stopAutoplay)

watch(() => store.banners, () => {
  currentIndex.value = 0
  if (!isCollapsed.value) startAutoplay()
})
</script>
