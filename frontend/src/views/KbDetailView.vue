<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ElMessage,
  ElMessageBox,
  type UploadRequestOptions,
} from 'element-plus'
import { Refresh, UploadFilled } from '@element-plus/icons-vue'

import EvalPanel from '@/components/EvalPanel.vue'
import RetrievalDebugPanel from '@/components/RetrievalDebugPanel.vue'
import ReviewQueuePanel from '@/components/ReviewQueuePanel.vue'
import SourcesPanel from '@/components/SourcesPanel.vue'
import { getKb } from '@/api/kb'
import {
  deleteDocument,
  listChunks,
  listDocuments,
  retryDocument,
  uploadDocument,
} from '@/api/documents'
import type { ChunkItem, DocumentItem, DocumentStatus, KnowledgeBase } from '@/types'

const props = defineProps<{ id: string }>()
const route = useRoute()
const router = useRouter()

const TABS = ['documents', 'sources', 'debug', 'eval', 'reviews']

function tabFromQuery(): string {
  const tab = route.query.tab
  return typeof tab === 'string' && TABS.includes(tab) ? tab : 'documents'
}

const kb = ref<KnowledgeBase | null>(null)
const documents = ref<DocumentItem[]>([])
const loading = ref(false)
const uploading = ref(false)
const activeTab = ref(tabFromQuery())

const drawerVisible = ref(false)
const chunkLoading = ref(false)
const chunks = ref<ChunkItem[]>([])
const chunkDocName = ref('')

const inProgress = computed(() =>
  documents.value.some((doc) => ['pending', 'parsing', 'indexing'].includes(doc.status)),
)

let pollTimer: number | undefined
let pollingBusy = false

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function statusType(status: DocumentStatus): 'info' | 'warning' | 'success' | 'danger' {
  if (status === 'indexed') {
    return 'success'
  }
  if (status === 'failed') {
    return 'danger'
  }
  if (status === 'pending') {
    return 'info'
  }
  return 'warning'
}

const statusLabels: Record<DocumentStatus, string> = {
  pending: '排队中',
  parsing: '解析中',
  indexing: '入库中',
  indexed: '已入库',
  failed: '失败',
}

async function loadKb(): Promise<void> {
  kb.value = await getKb(props.id)
}

async function loadDocuments(showLoading = true): Promise<void> {
  if (showLoading) {
    loading.value = true
  }
  try {
    const data = await listDocuments(props.id, 0, 100)
    documents.value = data.items
  } finally {
    if (showLoading) {
      loading.value = false
    }
  }
  if (inProgress.value) {
    startPolling()
  } else {
    stopPolling()
  }
}

function startPolling(): void {
  if (pollTimer) {
    return
  }
  pollTimer = window.setInterval(async () => {
    if (pollingBusy) {
      return
    }
    pollingBusy = true
    try {
      await Promise.all([loadDocuments(false), loadKb()])
    } catch {
      stopPolling()
    } finally {
      pollingBusy = false
    }
  }, 1500)
}

function stopPolling(): void {
  if (pollTimer) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

async function loadAll(): Promise<void> {
  loading.value = true
  try {
    await Promise.all([loadKb(), loadDocuments(false)])
  } finally {
    loading.value = false
  }
}

async function handleUpload(options: UploadRequestOptions): Promise<void> {
  uploading.value = true
  try {
    await uploadDocument(props.id, options.file)
    ElMessage.success('已上传，正在后台入库')
    await loadDocuments(false)
  } catch {
    // Error toast is handled by the axios interceptor.
  } finally {
    uploading.value = false
  }
}

async function handleRetry(doc: DocumentItem): Promise<void> {
  try {
    await retryDocument(doc.id)
    ElMessage.success('已重新排队')
    await loadDocuments(false)
  } catch {
    // Error toast is handled by the axios interceptor.
  }
}

async function handleDelete(doc: DocumentItem): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除「${doc.filename}」？`, '删除文档', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deleteDocument(doc.id)
    ElMessage.success('已删除')
    await loadDocuments(false)
  } catch {
    // Error toast is handled by the axios interceptor.
  }
}

async function openChunks(doc: DocumentItem): Promise<void> {
  chunkDocName.value = doc.filename
  chunkLoading.value = true
  drawerVisible.value = true
  chunks.value = []
  try {
    const data = await listChunks(doc.id)
    chunks.value = data.items
  } finally {
    chunkLoading.value = false
  }
}

watch(
  () => props.id,
  () => {
    stopPolling()
    loadAll().catch(() => {
      // Error toast is handled by the axios interceptor.
    })
  },
  { immediate: true },
)

watch(
  () => route.query.tab,
  () => {
    activeTab.value = tabFromQuery()
  },
)

onUnmounted(stopPolling)
</script>

<template>
  <div class="page">
    <el-page-header class="breadcrumb" @back="router.push('/kbs')">
      <template #content>
        <span class="kb-title">{{ kb?.name || '知识库' }}</span>
      </template>
    </el-page-header>

    <div v-if="kb" class="kb-meta">
      <span class="muted">{{ kb.description || '暂无描述' }}</span>
      <div class="tags">
        <el-tag size="small" type="info">chunk {{ kb.chunk_size }} / {{ kb.chunk_overlap }}</el-tag>
        <el-tag size="small" type="info">索引版本 {{ kb.index_version }}</el-tag>
        <el-button size="small" @click="router.push(`/kbs/${kb.id}/graph`)">
          知识图谱
        </el-button>
        <el-button type="primary" size="small" @click="router.push(`/kbs/${kb.id}/chat`)">
          开始问答
        </el-button>
      </div>
    </div>

    <el-card shadow="never" class="content-card">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="文档" name="documents">
          <el-upload
            drag
            class="uploader"
            :show-file-list="false"
            :http-request="handleUpload"
            :disabled="uploading"
            accept=".md,.txt,.pdf,.docx"
          >
            <el-icon class="upload-icon"><UploadFilled /></el-icon>
            <div class="el-upload__text">
              拖拽 .md / .txt / .pdf / .docx 到这里，或 <em>点击选择</em>
            </div>
            <template #tip>
              <div class="el-upload__tip">
                单文件不超过 5 MB；PDF 会保留页码，扫描件需先做 OCR
              </div>
            </template>
          </el-upload>

          <div class="docs-head">
            <span class="muted">共 {{ documents.length }} 篇文档</span>
            <span v-if="inProgress" class="muted"><el-icon class="spin"><Refresh /></el-icon> 入库中…</span>
          </div>

          <el-table v-loading="loading" :data="documents">
            <el-table-column prop="filename" label="文件名" min-width="220" />
            <el-table-column label="大小" width="100">
              <template #default="{ row }">{{ formatSize(row.size_bytes) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="180">
              <template #default="{ row }">
                <el-tooltip
                  v-if="row.status === 'failed'"
                  :content="`${row.error_stage || ''}: ${row.error_message || ''}`"
                  placement="top"
                >
                  <el-tag :type="statusType(row.status)" size="small">
                    {{ statusLabels[row.status as DocumentStatus] }}
                  </el-tag>
                </el-tooltip>
                <el-tag v-else :type="statusType(row.status)" size="small">
                  {{ statusLabels[row.status as DocumentStatus] }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="Chunk / 页" width="120">
              <template #default="{ row }">
                {{ row.chunk_count }}
                <span v-if="row.page_count" class="muted">/ {{ row.page_count }} 页</span>
              </template>
            </el-table-column>
            <el-table-column label="上传时间" width="180">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="220" align="right">
              <template #default="{ row }">
                <el-button text type="primary" @click="openChunks(row)">查看切块</el-button>
                <el-button
                  v-if="row.status === 'failed'"
                  text
                  type="warning"
                  @click="handleRetry(row)"
                >
                  重试
                </el-button>
                <el-button text type="danger" @click="handleDelete(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>

          <el-empty
            v-if="!loading && !documents.length"
            description="还没有文档，上传一篇 Markdown、TXT、PDF 或 DOCX 开始"
          />
        </el-tab-pane>

        <el-tab-pane label="数据源" name="sources" lazy>
          <SourcesPanel :kb-id="id" />
        </el-tab-pane>

        <el-tab-pane label="检索调试" name="debug" lazy>
          <RetrievalDebugPanel :kb-id="id" :defaults="kb?.retrieval_config" />
        </el-tab-pane>

        <el-tab-pane label="评测" name="eval" lazy>
          <EvalPanel :kb-id="id" />
        </el-tab-pane>

        <el-tab-pane label="人工抽检" name="reviews" lazy>
          <ReviewQueuePanel :kb-id="id" />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <el-drawer v-model="drawerVisible" :title="`Chunk 预览 · ${chunkDocName}`" size="620px">
      <el-skeleton v-if="chunkLoading" :rows="6" animated />
      <el-empty v-else-if="!chunks.length" description="没有切块" />
      <div v-else class="chunk-list">
        <el-card v-for="chunk in chunks" :key="chunk.id" shadow="never" class="chunk-card">
          <div class="chunk-head">
            <el-tag size="small">#{{ chunk.chunk_index }}</el-tag>
            <span class="muted chunk-heading">{{ chunk.heading_path || '无标题' }}</span>
            <span class="muted mono">{{ chunk.char_start }}–{{ chunk.char_end }}</span>
          </div>
          <p class="chunk-text">{{ chunk.content }}</p>
        </el-card>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.breadcrumb {
  margin-bottom: 14px;
}

.kb-title {
  font-size: 18px;
  font-weight: 600;
}

.kb-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.tags {
  display: flex;
  gap: 8px;
  white-space: nowrap;
}

.content-card {
  border-radius: 12px;
}

.uploader {
  margin-bottom: 8px;
}

.upload-icon {
  margin-bottom: 8px;
  font-size: 40px;
  color: #94a3b8;
}

.docs-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 14px 0 8px;
  font-size: 13px;
}

.spin {
  animation: spin 1.2s linear infinite;
  vertical-align: -2px;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.chunk-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chunk-card {
  border-radius: 10px;
}

.chunk-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.chunk-heading {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chunk-text {
  margin: 0;
  color: #374151;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}
</style>
