<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Link, Plus, Refresh } from '@element-plus/icons-vue'

import {
  createSource,
  deleteSource,
  listSourceRuns,
  listSources,
  previewSource,
  syncSource,
  updateSource,
} from '@/api/sources'
import type { DataSourceItem, SourcePreview, SourceType, SyncRunItem } from '@/types'

const props = defineProps<{ kbId: string }>()

const sources = ref<DataSourceItem[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const saving = ref(false)
const previewing = ref(false)
const preview = ref<SourcePreview | null>(null)

const runsVisible = ref(false)
const runs = ref<SyncRunItem[]>([])
const runsLoading = ref(false)
const activeSource = ref<DataSourceItem | null>(null)

let poll: number | undefined

const TYPE_LABELS: Record<SourceType, string> = {
  web: '网页抓取',
  sitemap: 'Sitemap',
  rss: 'RSS / Atom',
  github: 'Git 仓库',
}

const TYPE_HINTS: Record<SourceType, string> = {
  web: '从起始地址按广度优先抓取，受 max_pages / max_depth 与 robots.txt 约束',
  sitemap: '读取 sitemap.xml，只抓其中的内容页，适合文档站',
  rss: '解析 feed，直接使用条目正文，不额外抓页面',
  github: '读取仓库目录下的 Markdown/文本文件，私有仓库需配置 GITHUB_TOKEN',
}

const form = reactive({
  name: '',
  source_type: 'web' as SourceType,
  schedule_minutes: 0,
  enabled: true,
  // web / sitemap
  start_urls: '',
  sitemap_url: '',
  max_pages: 10,
  max_depth: 1,
  include_patterns: '',
  exclude_patterns: '',
  // rss
  feed_url: '',
  max_items: 20,
  // github
  repo: '',
  path: '',
  ref: '',
  glob: '*.md, *.mdx, *.txt',
  max_files: 20,
})

const runningCount = computed(() => sources.value.filter((item) => item.last_status === 'running').length)

function buildConfig(): Record<string, unknown> {
  const patterns = (value: string) =>
    value
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)

  if (form.source_type === 'web') {
    return {
      start_urls: form.start_urls
        .split('\n')
        .map((item) => item.trim())
        .filter(Boolean),
      max_pages: form.max_pages,
      max_depth: form.max_depth,
      include_patterns: patterns(form.include_patterns),
      exclude_patterns: patterns(form.exclude_patterns),
    }
  }
  if (form.source_type === 'sitemap') {
    return {
      sitemap_url: form.sitemap_url,
      max_pages: form.max_pages,
      include_patterns: patterns(form.include_patterns),
    }
  }
  if (form.source_type === 'rss') {
    return { feed_url: form.feed_url, max_items: form.max_items }
  }
  return {
    repo: form.repo,
    path: form.path,
    ref: form.ref || null,
    glob: patterns(form.glob),
    max_files: form.max_files,
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    sources.value = await listSources(props.kbId)
    if (sources.value.some((item) => item.last_status === 'running')) {
      startPolling()
    } else {
      stopPolling()
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载数据源失败')
  } finally {
    loading.value = false
  }
}

function startPolling(): void {
  if (poll) return
  poll = window.setInterval(async () => {
    try {
      sources.value = await listSources(props.kbId)
      if (!sources.value.some((item) => item.last_status === 'running')) {
        stopPolling()
        await refreshRunsIfOpen()
      }
    } catch {
      stopPolling()
    }
  }, 5000)
}

function stopPolling(): void {
  if (poll) {
    window.clearInterval(poll)
    poll = undefined
  }
}

async function submit(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning('请填写名称')
    return
  }
  saving.value = true
  try {
    await createSource(props.kbId, {
      name: form.name.trim(),
      source_type: form.source_type,
      config: buildConfig(),
      schedule_minutes: form.schedule_minutes,
      enabled: form.enabled,
    })
    ElMessage.success('数据源已创建')
    dialogVisible.value = false
    form.name = ''
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '创建失败')
  } finally {
    saving.value = false
  }
}

async function handlePreview(): Promise<void> {
  previewing.value = true
  try {
    preview.value = await previewSource(props.kbId, {
      source_type: form.source_type,
      config: buildConfig(),
    })
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '预检失败')
  } finally {
    previewing.value = false
  }
}

async function handleSync(source: DataSourceItem): Promise<void> {
  try {
    await syncSource(source.id)
    ElMessage.success('同步任务已开始')
    await load()
    if (runsVisible.value && activeSource.value?.id === source.id) {
      await refreshRunsIfOpen()
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '触发同步失败')
  }
}

async function toggleEnabled(source: DataSourceItem): Promise<void> {
  try {
    await updateSource(source.id, { enabled: !source.enabled })
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '更新失败')
  }
}

async function handleDelete(source: DataSourceItem): Promise<void> {
  await ElMessageBox.confirm(
    `删除后不再同步，但已经入库的 ${source.name} 文档会保留。`,
    '删除数据源',
    { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
  )
  try {
    await deleteSource(source.id)
    ElMessage.success('已删除')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '删除失败')
  }
}

async function showRuns(source: DataSourceItem): Promise<void> {
  activeSource.value = source
  runsVisible.value = true
  runsLoading.value = true
  try {
    runs.value = await listSourceRuns(source.id)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载历史失败')
  } finally {
    runsLoading.value = false
  }
}

async function refreshRunsIfOpen(): Promise<void> {
  if (!runsVisible.value || !activeSource.value) return
  runs.value = await listSourceRuns(activeSource.value.id).catch(() => runs.value)
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

function typeLabel(type: string): string {
  return TYPE_LABELS[type as SourceType] || type
}

function statusTag(status: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'success') return 'success'
  if (status === 'partial') return 'warning'
  if (status === 'failed') return 'danger'
  if (status === 'running') return 'warning'
  return 'info'
}

function statusLabel(status: string): string {
  return (
    {
      idle: '未运行',
      running: '同步中',
      success: '成功',
      partial: '部分成功',
      failed: '失败',
    }[status] || status
  )
}

function scheduleLabel(minutes: number): string {
  if (!minutes) return '手动'
  if (minutes % 1440 === 0) return `每 ${minutes / 1440} 天`
  if (minutes % 60 === 0) return `每 ${minutes / 60} 小时`
  return `每 ${minutes} 分钟`
}

onMounted(load)
onUnmounted(stopPolling)
</script>

<template>
  <div class="sources">
    <div class="toolbar">
      <div class="intro muted">
        数据源会把外部内容抓取、归一化并<b>增量</b>入库：内容没变就完全跳过，不产生 embedding
        与 token 成本；支持 robots.txt 与请求间隔，定时同步由后台调度触发。
      </div>
      <div class="actions">
        <el-tag v-if="runningCount" type="warning" size="small">{{ runningCount }} 个同步中</el-tag>
        <el-button :icon="Refresh" size="small" :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" size="small" :icon="Plus" @click="dialogVisible = true">
          新建数据源
        </el-button>
      </div>
    </div>

    <el-table :data="sources" v-loading="loading" empty-text="还没有数据源">
      <el-table-column label="名称" min-width="180">
        <template #default="{ row }">
          <div class="name-cell">
            <el-icon><Link /></el-icon>
            <span>{{ row.name }}</span>
          </div>
          <div class="muted small">{{ typeLabel(row.source_type) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="调度" width="120">
        <template #default="{ row }">{{ scheduleLabel(row.schedule_minutes) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="150">
        <template #default="{ row }">
          <el-tag :type="statusTag(row.last_status)" size="small">
            {{ statusLabel(row.last_status) }}
          </el-tag>
          <div v-if="row.last_error" class="error-text small" :title="row.last_error">
            {{ row.last_error.slice(0, 28) }}…
          </div>
        </template>
      </el-table-column>
      <el-table-column label="上次同步" width="180">
        <template #default="{ row }">{{ formatTime(row.last_run_at) }}</template>
      </el-table-column>
      <el-table-column label="下次同步" width="180">
        <template #default="{ row }">
          {{ row.enabled ? formatTime(row.next_run_at) : '已停用' }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button text type="primary" size="small" @click="handleSync(row)">同步</el-button>
          <el-button text size="small" @click="showRuns(row)">历史</el-button>
          <el-button text size="small" @click="toggleEnabled(row)">
            {{ row.enabled ? '停用' : '启用' }}
          </el-button>
          <el-button text type="danger" size="small" @click="handleDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="新建数据源" width="640px">
      <el-form label-position="top">
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="名称">
              <el-input v-model="form.name" placeholder="例如：FastAPI 教程" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="类型">
              <el-select v-model="form.source_type" style="width: 100%">
                <el-option
                  v-for="(label, value) in TYPE_LABELS"
                  :key="value"
                  :value="value"
                  :label="label"
                />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <div class="type-hint muted small">{{ TYPE_HINTS[form.source_type] }}</div>

        <template v-if="form.source_type === 'web'">
          <el-form-item label="起始地址（每行一个）">
            <el-input v-model="form.start_urls" type="textarea" :rows="2" placeholder="https://docs.example.com/guide" />
          </el-form-item>
          <el-row :gutter="12">
            <el-col :span="12">
              <el-form-item label="最多页面数">
                <el-input-number v-model="form.max_pages" :min="1" :max="50" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="抓取深度">
                <el-input-number v-model="form.max_depth" :min="0" :max="3" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="URL 必须包含（逗号分隔，可选）">
            <el-input v-model="form.include_patterns" placeholder="/docs/" />
          </el-form-item>
          <el-form-item label="URL 排除（逗号分隔，可选）">
            <el-input v-model="form.exclude_patterns" placeholder="/blog/, /tags/" />
          </el-form-item>
        </template>

        <template v-else-if="form.source_type === 'sitemap'">
          <el-form-item label="Sitemap 地址">
            <el-input v-model="form.sitemap_url" placeholder="https://docs.example.com/sitemap.xml" />
          </el-form-item>
          <el-form-item label="最多页面数">
            <el-input-number v-model="form.max_pages" :min="1" :max="100" />
          </el-form-item>
          <el-form-item label="URL 必须包含（逗号分隔，可选）">
            <el-input v-model="form.include_patterns" placeholder="/docs/" />
          </el-form-item>
        </template>

        <template v-else-if="form.source_type === 'rss'">
          <el-form-item label="Feed 地址">
            <el-input v-model="form.feed_url" placeholder="https://example.com/feed.xml" />
          </el-form-item>
          <el-form-item label="最多条目">
            <el-input-number v-model="form.max_items" :min="1" :max="100" />
          </el-form-item>
        </template>

        <template v-else>
          <el-form-item label="仓库（owner/name）">
            <el-input v-model="form.repo" placeholder="tiangolo/fastapi" />
          </el-form-item>
          <el-row :gutter="12">
            <el-col :span="12">
              <el-form-item label="目录">
                <el-input v-model="form.path" placeholder="docs/en/docs/tutorial" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="分支 / Tag（可选）">
                <el-input v-model="form.ref" placeholder="main" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="文件匹配（逗号分隔）">
            <el-input v-model="form.glob" />
          </el-form-item>
          <el-form-item label="最多文件数">
            <el-input-number v-model="form.max_files" :min="1" :max="100" />
          </el-form-item>
        </template>

        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="同步间隔（分钟，0 = 仅手动）">
              <el-input-number v-model="form.schedule_minutes" :min="0" :max="10080" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="启用">
              <el-switch v-model="form.enabled" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>

      <div v-if="preview" class="preview-box">
        <div class="muted small">
          预检抓取 {{ preview.pages_fetched }} 页，得到 {{ preview.items.length }} 条候选
        </div>
        <div v-for="item in preview.items" :key="item.url" class="preview-item">
          <div class="preview-title">{{ item.title }} <span class="muted small">· {{ item.chars }} 字</span></div>
          <div class="muted small ellipsis">{{ item.url }}</div>
        </div>
        <div v-for="err in preview.errors" :key="err.url" class="error-text small">
          {{ err.url }}：{{ err.error }}
        </div>
      </div>

      <template #footer>
        <el-button :loading="previewing" @click="handlePreview">预检前 5 条</el-button>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">创建</el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="runsVisible"
      :title="`同步历史 · ${activeSource?.name || ''}`"
      size="560px"
    >
      <el-table :data="runs" v-loading="runsLoading" empty-text="还没有同步记录">
        <el-table-column label="开始时间" width="170">
          <template #default="{ row }">{{ formatTime(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="新增 / 更新 / 跳过" min-width="150">
          <template #default="{ row }">
            {{ row.stats.added ?? 0 }} / {{ row.stats.updated ?? 0 }} /
            {{ row.stats.unchanged ?? 0 }}
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="90">
          <template #default="{ row }">
            {{ row.stats.duration_ms ? `${Math.round(row.stats.duration_ms / 1000)}s` : '—' }}
          </template>
        </el-table-column>
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="run-detail">
              <div>触发方式：{{ row.trigger }} · 抓取页数：{{ row.stats.pages_fetched ?? 0 }}</div>
              <div v-if="row.error" class="error-text">错误：{{ row.error }}</div>
              <div v-for="item in row.stats.errors || []" :key="item.url" class="error-text small">
                {{ item.url }}：{{ item.error }}
              </div>
              <div v-if="!row.stats.errors?.length && !row.error" class="muted small">
                没有记录到错误
              </div>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>

    <el-alert
      class="compliance"
      type="info"
      :closable="false"
      show-icon
      title="抓取合规"
      description="仅抓取你有权使用的内容，并遵守目标站点服务条款。本项目默认遵循 robots.txt、限制请求频率与页面数。"
    />
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
  margin-bottom: 14px;
}

.intro {
  max-width: 720px;
  line-height: 1.7;
  font-size: 13px;
}

.actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.name-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
}

.small {
  font-size: 12px;
}

.type-hint {
  margin: -4px 0 12px;
}

.preview-box {
  margin-top: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #f8fafc;
  border: 1px dashed #cbd5e1;
}

.preview-item {
  padding: 6px 0;
  border-bottom: 1px dashed #e2e8f0;
}

.preview-title {
  font-size: 13px;
  color: #0f172a;
}

.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-detail {
  padding: 6px 12px;
  font-size: 12px;
  line-height: 1.9;
}

.compliance {
  margin-top: 16px;
}
</style>
