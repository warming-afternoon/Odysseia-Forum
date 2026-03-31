<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="store.advancedFilterVisible"
        class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4"
        @click.self="close"
      >
        <Transition name="modal-card" appear>
          <div class="bg-discord-sidebar rounded-2xl shadow-2xl border border-white/10 w-full max-w-md overflow-hidden">
            <!-- Header -->
            <div class="flex items-center justify-between px-5 py-4 border-b border-white/8">
              <div class="flex items-center gap-2">
                <span class="material-symbols-outlined text-discord-primary text-xl">tune</span>
                <h3 class="text-white font-bold text-base">高级筛选</h3>
              </div>
              <button
                class="w-8 h-8 rounded-full bg-white/8 hover:bg-discord-red/30 text-discord-muted hover:text-discord-red flex items-center justify-center transition-colors"
                @click="close"
              >
                <span class="material-symbols-outlined text-lg">close</span>
              </button>
            </div>

            <!-- Body -->
            <div class="p-5 space-y-5 max-h-[65vh] overflow-y-auto custom-scrollbar">
              <!-- Created date -->
              <section>
                <label class="flex items-center gap-1.5 text-xs font-semibold text-discord-muted uppercase tracking-wider mb-2.5">
                  <span class="material-symbols-outlined text-sm text-discord-primary">event</span>
                  发帖时间
                </label>
                <div class="grid grid-cols-2 gap-3">
                  <div>
                    <span class="text-[10px] text-discord-muted mb-1 block">开始日期</span>
                    <input
                      v-model="local.dateStart"
                      type="date"
                      class="filter-input"
                    >
                  </div>
                  <div>
                    <span class="text-[10px] text-discord-muted mb-1 block">结束日期</span>
                    <input
                      v-model="local.dateEnd"
                      type="date"
                      class="filter-input"
                    >
                  </div>
                </div>
              </section>

              <!-- Active date -->
              <section>
                <label class="flex items-center gap-1.5 text-xs font-semibold text-discord-muted uppercase tracking-wider mb-2.5">
                  <span class="material-symbols-outlined text-sm text-discord-primary">update</span>
                  活跃时间
                </label>
                <div class="grid grid-cols-2 gap-3">
                  <div>
                    <span class="text-[10px] text-discord-muted mb-1 block">开始日期</span>
                    <input
                      v-model="local.activeStart"
                      type="date"
                      class="filter-input"
                    >
                  </div>
                  <div>
                    <span class="text-[10px] text-discord-muted mb-1 block">结束日期</span>
                    <input
                      v-model="local.activeEnd"
                      type="date"
                      class="filter-input"
                    >
                  </div>
                </div>
              </section>

              <!-- Reaction count -->
              <section>
                <label class="flex items-center gap-1.5 text-xs font-semibold text-discord-muted uppercase tracking-wider mb-2.5">
                  <span class="material-symbols-outlined text-sm text-discord-primary">favorite</span>
                  反应数范围
                </label>
                <div class="grid grid-cols-2 gap-3">
                  <div>
                    <span class="text-[10px] text-discord-muted mb-1 block">最低</span>
                    <input
                      v-model="local.reactionMin"
                      type="number"
                      min="0"
                      placeholder="不限"
                      class="filter-input"
                    >
                  </div>
                  <div>
                    <span class="text-[10px] text-discord-muted mb-1 block">最高</span>
                    <input
                      v-model="local.reactionMax"
                      type="number"
                      min="0"
                      placeholder="不限"
                      class="filter-input"
                    >
                  </div>
                </div>
              </section>
            </div>

            <!-- Footer -->
            <div class="flex items-center justify-between px-5 py-4 border-t border-white/8 bg-discord-element/30">
              <button
                class="text-xs text-discord-muted hover:text-white transition-colors px-3 py-1.5 rounded-lg hover:bg-white/5"
                @click="clearAll"
              >
                清除全部
              </button>
              <div class="flex gap-2">
                <button
                  class="px-4 py-2 text-sm text-discord-muted bg-discord-element hover:bg-discord-element/80 rounded-lg border border-white/10 transition-colors"
                  @click="close"
                >
                  取消
                </button>
                <button
                  class="px-4 py-2 text-sm text-white bg-discord-primary hover:bg-discord-hover rounded-lg font-medium shadow-md shadow-discord-primary/20 transition-colors"
                  @click="apply"
                >
                  应用筛选
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
import { reactive, watch } from 'vue'
import { useAppStore } from '../stores/app'

const store = useAppStore()

const local = reactive({
  dateStart: '',
  dateEnd: '',
  activeStart: '',
  activeEnd: '',
  reactionMin: '',
  reactionMax: '',
})

watch(() => store.advancedFilterVisible, (visible) => {
  if (visible) {
    local.dateStart = store.dateStart
    local.dateEnd = store.dateEnd
    local.activeStart = store.activeStart
    local.activeEnd = store.activeEnd
    local.reactionMin = store.reactionMin
    local.reactionMax = store.reactionMax
  }
})

function close() {
  store.advancedFilterVisible = false
}

function clearAll() {
  local.dateStart = ''
  local.dateEnd = ''
  local.activeStart = ''
  local.activeEnd = ''
  local.reactionMin = ''
  local.reactionMax = ''
}

function apply() {
  store.dateStart = local.dateStart
  store.dateEnd = local.dateEnd
  store.activeStart = local.activeStart
  store.activeEnd = local.activeEnd
  store.reactionMin = local.reactionMin
  store.reactionMax = local.reactionMax
  store.advancedFilterVisible = false
  store.executeSearch()
}
</script>
