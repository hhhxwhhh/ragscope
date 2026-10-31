<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Collection, Document, Refresh, Upload } from '@element-plus/icons-vue'

import { getCorpusPack, getImportStatus, importCorpus, listCorpusPacks, readCorpusDoc } from '@/api/corpus'
import { listKbs } from '@/api/kb'
import type {
  CorpusDoc,
  CorpusImportStatus,
  CorpusPack,
  CorpusPackDetail,
  KnowledgeBase,
} from '@/types'

const router = useRouter()

const packs = ref<CorpusPack[]>([])
const loading = ref(false)

const detailVisible = ref(false)
const detailLoading = ref(false)
const activePack = ref<CorpusPackDetail | null>(null)
const activeDoc = ref<CorpusDoc | null>(null)
const docContent = ref('')
const docLoading = ref(false)

const importVisible = ref(false)
const importing = ref(false)
const kbs = ref<KnowledgeBase[]>([])
const targetKb = ref('')
const importPack = ref<CorpusPack | null>(null)
const progress = ref<CorpusImportStatus | null>(null)
let poll: number | undefined

function formatChars(value: number): string {
  if (value >= 10_000) return `${(value / 10_000).toFixed(1)} 万字符`
  return `${value} 字符`
}

function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    packs.value = await listCorpusPacks()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载语料包失败')
  } finally {
    loading.value = false
  }
}

async function openPack(pack: CorpusPack): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  activeDoc.value = null
  docContent.value = ''
  try {
    activePack.value = await getCorpusPack(pack.slug)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载语料包失败')
  } finally {
    detailLoading.value = false
  }
}

async function openDoc(doc: CorpusDoc): Promise<void> {
  if (!activePack.value) return
  activeDoc.value = doc
  docLoading.value = true
  try {
    docContent.value = await readCorpusDoc(activePack.value.slug, doc.file)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '读取文档失败')
  } finally {
    docLoading.value = false
  }
}

async function openImport(pack: CorpusPack): Promise<void> {
  importPack.value = pack
  importVisible.value = true
  progress.value = null
  if (!kbs.value.length) {
    try {
      const page = await listKbs(0, 100)
      kbs.value = page.items
    } catch (error) {
      ElMessage.error(error instanceof Error ? error.message : '加载知识库失败')
    }
  }
}

async function startImport(): Promise<void> {
  if (!importPack.value || !targetKb.value) {
    ElMessage.warning('请选择目标知识库')
    return
  }
  importing.value = true
  try {
    progress.value = await importCorpus(importPack.value.slug, targetKb.value)
    startPolling()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '导入失败')
  } finally {
    importing.value = false
  }
}

function startPolling(): void {
  stopPolling()
  poll = window.setInterval(async () => {
    if (!importPack.value || !targetKb.value) return
    try {
      const status = await getImportStatus(importPack.value.slug, targetKb.value)
      progress.value = status
      if (status.status === 'completed' || status.status === 'failed') {
        stopPolling()
        if (status.status === 'completed') {
          ElMessage.success(
            `导入完成：新增 ${status.added} 篇，跳过 ${status.skipped} 篇，失败 ${status.failed} 篇`,
          )
        }
      }
    } catch {
      stopPolling()
    }
  }, 1500)
}

function stopPolling(): void {
  if (poll) {
    window.clearInterval(poll)
    poll = undefined
  }
}

function goToKb(): void {
  if (!targetKb.value) return
  importVisible.value = false
  router.push(`/kbs/${targetKb.value}`)
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2>语料库</h2>
        <p class="muted">
          离线语料包由 <code>scripts/crawl_corpus.py</code> 生成：可断点续爬、带来源与许可
          说明，导入前能先审阅内容。与「数据源」的区别是——语料包是快照，适合复现实验；
          数据源负责持续保鲜。
        </p>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-empty v-if="!loading && !packs.length" description="还没有语料包。运行 scripts/crawl_corpus.py 抓取后会自动出现在这里。" />

    <el-row :gutter="16">
      <el-col v-for="pack in packs" :key="pack.slug" :span="12">
        <el-card shadow="never" class="pack-card">
          <div class="pack-head">
            <div class="pack-icon"><el-icon><Collection /></el-icon></div>
            <div class="pack-title">
              <div class="pack-name">{{ pack.name }}</div>
              <div class="muted small">{{ pack.slug }} · {{ hostOf(pack.source) }}</div>
            </div>
          </div>
          <div class="pack-stats">
            <el-tag size="small" type="info">{{ pack.count }} 篇</el-tag>
            <el-tag size="small" type="info">{{ formatChars(pack.chars) }}</el-tag>
            <el-tag v-if="pack.license" size="small" type="success">{{ pack.license.split('·')[0] }}</el-tag>
          </div>
          <p class="muted small license">{{ pack.license }}</p>
          <div class="pack-actions">
            <el-button size="small" :icon="Document" @click="openPack(pack)">查看文档</el-button>
            <el-button type="primary" size="small" :icon="Upload" @click="openImport(pack)">
              导入知识库
            </el-button>
            <span class="muted small">{{ pack.generated_at?.slice(0, 10) }}</span>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-drawer v-model="detailVisible" :title="activePack?.name || '语料包'" size="900px">
      <div v-loading="detailLoading">
        <div class="detail-meta muted small">
          来源：{{ activePack?.source }} · {{ activePack?.count }} 篇 ·
          {{ activePack ? formatChars(activePack.chars) : '' }}
        </div>
        <el-row :gutter="16">
          <el-col :span="10">
            <el-table
              :data="activePack?.documents || []"
              height="560"
              highlight-current-row
              @row-click="openDoc"
            >
              <el-table-column label="标题" min-width="200">
                <template #default="{ row }">
                  <div class="doc-title">{{ row.title }}</div>
                  <div class="muted small">{{ row.chars }} 字符</div>
                </template>
              </el-table-column>
            </el-table>
          </el-col>
          <el-col :span="14">
            <div v-loading="docLoading" class="doc-preview">
              <div class="doc-preview-head">
                <strong>{{ activeDoc?.title || '选择左侧文档预览' }}</strong>
                <el-link v-if="activeDoc" :href="activeDoc.url" target="_blank" type="primary">
                  原文
                </el-link>
              </div>
              <pre v-if="docContent" class="doc-body">{{ docContent }}</pre>
              <el-empty v-else description="点击左侧任意文档查看抓取结果" :image-size="70" />
            </div>
          </el-col>
        </el-row>
      </div>
    </el-drawer>

    <el-dialog v-model="importVisible" title="导入语料包" width="520px">
      <p class="muted small">
        将把「{{ importPack?.name }}」的全部文档逐篇入库。已存在相同来源 URL 的文档会被跳过，
        不会重复占用索引。
      </p>
      <el-select v-model="targetKb" placeholder="选择目标知识库" style="width: 100%">
        <el-option v-for="kb in kbs" :key="kb.id" :label="kb.name" :value="kb.id" />
      </el-select>

      <div v-if="progress" class="progress-box">
        <el-progress
          :percentage="progress.total ? Math.round((progress.done / progress.total) * 100) : 0"
          :status="progress.status === 'completed' ? 'success' : undefined"
        />
        <div class="muted small">
          进度 {{ progress.done }}/{{ progress.total }} · 新增 {{ progress.added }} · 跳过
          {{ progress.skipped }} · 失败 {{ progress.failed }}
        </div>
        <el-alert v-if="progress.error" type="error" :title="progress.error" :closable="false" />
      </div>

      <template #footer>
        <el-button v-if="progress?.status === 'completed'" type="primary" @click="goToKb">
          去知识库
        </el-button>
        <el-button v-else @click="importVisible = false">关闭</el-button>
        <el-button
          type="primary"
          :loading="importing || progress?.status === 'running'"
          :disabled="!targetKb || progress?.status === 'running'"
          @click="startImport"
        >
          开始导入
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24px;
  margin-bottom: 16px;
}

.page-head h2 {
  margin: 0 0 6px;
}

.page-head p {
  margin: 0;
  max-width: 800px;
  line-height: 1.7;
}

.pack-card {
  margin-bottom: 16px;
}

.pack-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.pack-icon {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #eff6ff;
  color: #2563eb;
  font-size: 20px;
}

.pack-name {
  font-weight: 600;
  color: #0f172a;
}

.pack-stats {
  display: flex;
  gap: 8px;
  margin: 14px 0 8px;
}

.license {
  min-height: 18px;
  margin: 0 0 10px;
}

.pack-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.detail-meta {
  margin-bottom: 10px;
}

.doc-title {
  font-size: 13px;
  color: #0f172a;
}

.doc-preview {
  height: 560px;
  display: flex;
  flex-direction: column;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  overflow: hidden;
}

.doc-preview-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid #e2e8f0;
  background: #f8fafc;
  font-size: 13px;
}

.doc-body {
  flex: 1;
  margin: 0;
  padding: 12px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.7;
  color: #334155;
  background: #fff;
}

.progress-box {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.small {
  font-size: 12px;
}
</style>
