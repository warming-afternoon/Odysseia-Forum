<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="visible"
        class="fixed inset-0 z-[100] bg-black/85 backdrop-blur-md flex items-center justify-center"
        @click="$emit('close')"
      >
        <Transition name="modal-card" appear>
          <div class="banner-application-content" @click.stop>
            <div class="banner-application-header">
              <h3 class="text-lg font-bold text-white flex items-center gap-2">
                <span class="material-symbols-outlined text-discord-primary">add_photo_alternate</span>
                申请Banner展示位
              </h3>
              <button class="close-btn" @click="$emit('close')">
                <span class="material-symbols-outlined">close</span>
              </button>
            </div>

            <div class="banner-rules-notice">
              <span class="material-symbols-outlined">info</span>
              <span>申请前请先阅读</span>
              <a
                href="https://discord.com/channels/1134557553011998840/1307242450300964986/1442755349311651901"
                target="_blank"
                rel="noopener noreferrer"
                class="rules-link"
              >
                《Banner展示位申请规定》
                <span class="material-symbols-outlined text-xs">open_in_new</span>
              </a>
            </div>

            <form class="banner-application-form" @submit.prevent="handleSubmit">
              <div class="form-group">
                <label>
                  <span class="material-symbols-outlined text-sm">tag</span>
                  帖子ID <span class="text-discord-red">*</span>
                </label>
                <input v-model="threadId" type="text" placeholder="请输入帖子的Thread ID（纯数字）" required pattern="\d{17,20}">
                <span class="form-hint">只能为自己的帖子申请Banner</span>
              </div>
              <div class="form-group">
                <label>
                  <span class="material-symbols-outlined text-sm">image</span>
                  封面图链接 <span class="text-discord-red">*</span>
                </label>
                <input v-model="coverUrl" type="url" placeholder="https://..." required>
                <span class="form-hint">推荐尺寸 16:9，支持 JPG/PNG/WebP</span>
              </div>
              <div class="form-group">
                <label>
                  <span class="material-symbols-outlined text-sm">visibility</span>
                  展示范围 <span class="text-discord-red">*</span>
                </label>
                <select v-model="scope" required>
                  <option value="">请选择展示范围</option>
                  <option value="global">全频道（最多3个）</option>
                  <optgroup v-for="cat in CHANNEL_CATEGORIES" :key="cat.name" :label="cat.name">
                    <option v-for="ch in cat.channels" :key="ch.id" :value="ch.id">
                      {{ ch.name }}（最多5个）
                    </option>
                  </optgroup>
                </select>
                <span class="form-hint">全频道最多3个，单频道最多5个</span>
              </div>

              <Transition name="fade">
                <div v-if="error" class="form-error">{{ error }}</div>
              </Transition>

              <div class="form-actions">
                <button type="button" class="btn-cancel" @click="$emit('close')">取消</button>
                <button type="submit" class="btn-submit" :disabled="submitting">
                  <span v-if="submitting" class="material-symbols-outlined animate-spin">progress_activity</span>
                  <span v-else class="material-symbols-outlined">send</span>
                  {{ submitting ? '提交中...' : '提交申请' }}
                </button>
              </div>
            </form>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { ref } from 'vue'
import { CHANNEL_CATEGORIES } from '../config'
import { submitBannerApplication } from '../api'
import { useAppStore } from '../stores/app'

defineProps({ visible: Boolean })
const emit = defineEmits(['close'])

const store = useAppStore()
const threadId = ref('')
const coverUrl = ref('')
const scope = ref('')
const error = ref('')
const submitting = ref(false)

async function handleSubmit() {
  error.value = ''
  if (!threadId.value || !coverUrl.value || !scope.value) {
    error.value = '请填写所有必填字段'
    return
  }
  if (!/^\d{17,20}$/.test(threadId.value)) {
    error.value = '帖子ID必须是17-20位数字'
    return
  }
  if (!coverUrl.value.startsWith('http://') && !coverUrl.value.startsWith('https://')) {
    error.value = '封面图链接必须以http://或https://开头'
    return
  }

  submitting.value = true
  try {
    const res = await submitBannerApplication({
      thread_id: threadId.value,
      cover_image_url: coverUrl.value,
      target_scope: scope.value,
    })
    if (res?.success) {
      store.showToast('Banner申请已提交，等待审核')
      threadId.value = ''
      coverUrl.value = ''
      scope.value = ''
      emit('close')
    } else {
      error.value = res?.message || '提交失败，请重试'
    }
  } catch {
    error.value = '网络错误，请重试'
  } finally {
    submitting.value = false
  }
}
</script>
