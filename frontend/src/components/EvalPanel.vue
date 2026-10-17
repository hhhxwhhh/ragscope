<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Plus, Search, VideoPlay } from '@element-plus/icons-vue'

import {
  cancelRun as cancelEvalRun,
  createCase,
  createDataset,
  createRun,
  deleteCase,
  deleteDataset,
  getRun,
  listCases,
  listDatasets,
  listRuns,
  searchChunks,
} from '@/api/eval'
import type { RetrievalOverrides } from '@/api/eval'
import type {
  ChunkSearchItem,
  EvalCase,
  EvalDataset,
  EvalRun,
  EvalRunDetail,
  EvalRunStatus,
  RelevantRef,
} from '@/types'

const props = defineProps<{ kbId: string }>()
const router = useRouter()

const datasets = ref<EvalDataset[]>([])
const activeDatasetId = ref('')
const cases = ref<EvalCase[]>([])
const runs = ref<EvalRun[]>([])
const loading = ref(false)
const caseLoading = ref(false)
const runsLoading = ref(false)
const activeTab = ref<'cases' | 'runs'>('cases')
const caseFilter = ref('')

const createDatasetVisible = ref(false)
const datasetForm = reactive({ name: '', description: '' })

const caseDialogVisible = ref(false)
const caseForm = reactive({ question: '', answerable: true, notes: '' })
const refQuery = ref('')
const refResults = ref<ChunkSearchItem[]>([])
const refLoading = ref(false)
const selectedRefs = ref<RelevantRef[]>([])
const caseSaving = ref(false)

const runDialogVisible = ref(false)
const runStarting = ref(false)
const watchingId = ref('')
const watchingRun = ref<EvalRunDetail | null>(null)

const compareBaseId = ref('')
const compareCandidateId = ref('')

const runForm = reactive({
  preset: 'hybrid',
  final_top_k: 10,
  min_vector_score: 0.45,
  rrf_k: 60,
  vector_weight: 1,
  bm25_weight: 1,
  use_rerank: false,
})

const presets = [
  { key: 'vector', label: '向量基线', config: { bm25_weight: 0, use_rerank: false } },
  { key: 'hybrid', label: '混合检索', config: { bm25_weight: 1, vector_weight: 1, use_rerank: false } },
  {
    key: 'hybrid_rerank',
    label: '混合 + 重排',
    config: { bm25_weight: 1, vector_weight: 1, use_rerank: true },
  },
  {
    key: 'weighted_rerank',
    label: '向量优先 + 重排',
    config: { bm25_weight: 0.5, vector_weight: 2, use_rerank: true },
  },
]

const activeDataset = computed(() =>
  datasets.value.find((dataset) => dataset.id === activeDatasetId.value),
)

const completedRuns = computed(() =>
  runs.value.filter((run) => run.status === 'completed'),
)

const latestCompleted = computed(() => completedRuns.value[0] || null)

const filteredCases = computed(() => {
  const keyword = caseFilter.value.trim().toLowerCase()
  if (!keyword) {
    return cases.value
  }
  return cases.value.filter((item) => item.question.toLowerCase().includes(keyword))
})

const hasActiveRun = computed(() =>
  runs.value.some((run) => run.status === 'pending' || run.status === 'running'),
)

let pollTimer: number | undefined

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function formatPct(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`
}

function statusType(status: EvalRunStatus): 'info' | 'success' | 'warning' | 'danger' {
  if (status === 'completed') {
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

const statusLabels: Record<EvalRunStatus, string> = {
  pending: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

function configSummary(config: Record<string, unknown>): string {
  const rerank = config.use_rerank ? '重排' : '无重排'
  return `vec ${config.vector_weight} : bm25 ${config.bm25_weight} · ${rerank} · k=${config.final_top_k}`
}

function refKey(ref: RelevantRef): string {
  return `${ref.document_id}#${ref.chunk_index}`
}

function isRefSelected(chunk: ChunkSearchItem): boolean {
  return selectedRefs.value.some(
    (ref) => ref.document_id === chunk.document_id && ref.chunk_index === chunk.chunk_index,
  )
}

function toggleRef(chunk: ChunkSearchItem): void {
  if (isRefSelected(chunk)) {
    selectedRefs.value = selectedRefs.value.filter(
      (ref) => !(ref.document_id === chunk.document_id && ref.chunk_index === chunk.chunk_index),
    )
    return
  }
  selectedRefs.value.push({
    document_id: chunk.document_id,
    chunk_index: chunk.chunk_index,
    filename: chunk.filename,
  })
}

async function loadDatasets(): Promise<void> {
  const page = await listDatasets(props.kbId)
  datasets.value = page.items
  if (!datasets.value.some((dataset) => dataset.id === activeDatasetId.value)) {
    activeDatasetId.value = datasets.value[0]?.id || ''
  }
}

async function loadCases(): Promise<void> {
  if (!activeDatasetId.value) {
    cases.value = []
    return
  }
  caseLoading.value = true
  try {
    const page = await listCases(activeDatasetId.value)
    cases.value = page.items
  } finally {
    caseLoading.value = false
  }
}

async function loadRuns(showLoading = true): Promise<void> {
  if (!activeDatasetId.value) {
    runs.value = []
    return
  }
  if (showLoading) {
    runsLoading.value = true
  }
  try {
    const page = await listRuns(activeDatasetId.value)
    runs.value = page.items
    syncCompareSelection()
    if (hasActiveRun.value) {
      startPolling()
    } else if (!watchingId.value) {
      stopPolling()
    }
  } finally {
    if (showLoading) {
      runsLoading.value = false
    }
  }
}

function syncCompareSelection(): void {
  const completed = completedRuns.value
  if (completed.length >= 2) {
    if (!completed.some((run) => run.id === compareBaseId.value)) {
      compareBaseId.value = completed[1].id
    }
    if (!completed.some((run) => run.id === compareCandidateId.value)) {
      compareCandidateId.value = completed[0].id
    }
  } else {
    compareBaseId.value = ''
    compareCandidateId.value = ''
  }
}

async function refreshAll(): Promise<void> {
  loading.value = true
  try {
    await loadDatasets()
    await Promise.all([loadCases(), loadRuns(false)])
  } finally {
    loading.value = false
  }
}

async function selectDataset(datasetId: string): Promise<void> {
  activeDatasetId.value = datasetId
  caseFilter.value = ''
  await Promise.all([loadCases(), loadRuns()])
}

function openCreateDataset(): void {
  datasetForm.name = ''
  datasetForm.description = ''
  createDatasetVisible.value = true
}

async function submitDataset(): Promise<void> {
  const name = datasetForm.name.trim()
  if (!name) {
    ElMessage.warning('请输入数据集名称')
    return
  }
  try {
    const dataset = await createDataset(props.kbId, {
      name,
      description: datasetForm.description.trim() || null,
    })
    createDatasetVisible.value = false
    await loadDatasets()
    await selectDataset(dataset.id)
    ElMessage.success('数据集已创建')
  } catch {
    // Error toast is handled by the axios interceptor.
  }
}

async function removeDataset(): Promise<void> {
  if (!activeDataset.value) {
    return
  }
  try {
    await ElMessageBox.confirm(
      `删除数据集「${activeDataset.value.name}」及其全部用例和运行记录？`,
      '删除数据集',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await deleteDataset(activeDataset.value.id)
  ElMessage.success('已删除')
  activeDatasetId.value = ''
  await refreshAll()
}

function openCaseDialog(): void {
  caseForm.question = ''
  caseForm.answerable = true
  caseForm.notes = ''
  refQuery.value = ''
  refResults.value = []
  selectedRefs.value = []
  caseDialogVisible.value = true
  // Show the first chunks immediately so a case can be annotated without a search.
  void doSearchChunks()
}

async function doSearchChunks(): Promise<void> {
  refLoading.value = true
  try {
    refResults.value = await searchChunks(props.kbId, refQuery.value.trim(), 40)
  } catch {
    // Error toast is handled by the axios interceptor.
  } finally {
    refLoading.value = false
  }
}

async function submitCase(): Promise<void> {
  const question = caseForm.question.trim()
  if (!question) {
    ElMessage.warning('请输入问题')
    return
  }
  if (caseForm.answerable && !selectedRefs.value.length) {
    ElMessage.warning('可答案例至少标注一个相关 chunk')
    return
  }
  if (!caseForm.answerable && selectedRefs.value.length) {
    ElMessage.warning('不可答案例不需要标注 chunk')
    return
  }
  caseSaving.value = true
  try {
    await createCase(activeDatasetId.value, {
      question,
      answerable: caseForm.answerable,
      relevant_refs: caseForm.answerable ? selectedRefs.value : [],
      notes: caseForm.notes.trim() || null,
    })
    caseDialogVisible.value = false
    await Promise.all([loadCases(), loadDatasets()])
    ElMessage.success('用例已添加')
  } finally {
    caseSaving.value = false
  }
}

async function removeCase(item: EvalCase): Promise<void> {
  try {
    await ElMessageBox.confirm(`删除用例「${item.question}」？`, '删除用例', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await deleteCase(item.id)
  await Promise.all([loadCases(), loadDatasets()])
}

function applyPreset(key: string): void {
  const preset = presets.find((item) => item.key === key)
  if (!preset) {
    return
  }
  runForm.preset = key
  Object.assign(runForm, preset.config)
}

function buildConfig(): RetrievalOverrides {
  return {
    final_top_k: runForm.final_top_k,
    min_vector_score: runForm.min_vector_score,
    rrf_k: runForm.rrf_k,
    vector_weight: runForm.vector_weight,
    bm25_weight: runForm.bm25_weight,
    use_rerank: runForm.use_rerank,
  }
}

function openRunDialog(): void {
  watchingRun.value = null
  watchingId.value = ''
  applyPreset('hybrid')
  runForm.final_top_k = 10
  runDialogVisible.value = true
}

async function startRun(): Promise<void> {
  if (!activeDataset.value || runStarting.value) {
    return
  }
  runStarting.value = true
  try {
    const run = await createRun(activeDataset.value.id, { config: buildConfig() })
    watchingId.value = run.id
    watchingRun.value = null
    await loadRuns()
    startPolling()
  } finally {
    runStarting.value = false
  }
}

async function refreshWatching(): Promise<void> {
  if (!watchingId.value) {
    return
  }
  const detail = await getRun(watchingId.value)
  watchingRun.value = detail
  if (['completed', 'failed', 'cancelled'].includes(detail.status)) {
    watchingId.value = ''
    await Promise.all([loadRuns(false), loadDatasets()])
  }
}

function startPolling(): void {
  if (pollTimer) {
    return
  }
  pollTimer = window.setInterval(async () => {
    try {
      await Promise.all([loadRuns(false), refreshWatching()])
    } catch {
      stopPolling()
    }
  }, 1200)
}

function stopPolling(): void {
  if (pollTimer) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

async function cancelActiveRun(run: EvalRun): Promise<void> {
  await cancelEvalRun(run.id)
  ElMessage.info('已请求取消，当前用例结束后停止')
  await loadRuns(false)
}

function openRunDetail(run: EvalRun): void {
  router.push(`/eval/runs/${run.id}`)
}

function openCompare(): void {
  if (!compareBaseId.value || !compareCandidateId.value) {
    ElMessage.warning('请选择两次已完成的运行')
    return
  }
  router.push(
    `/eval/compare?base=${compareBaseId.value}&candidate=${compareCandidateId.value}`,
  )
}

watch(
  () => props.kbId,
  () => {
    stopPolling()
    refreshAll().catch(() => undefined)
  },
  { immediate: true },
)

watch(
  () => activeDatasetId.value,
  (value) => {
    if (!value) {
      cases.value = []
      runs.value = []
    }
  },
)

onUnmounted(stopPolling)
</script>

<template>
  <div class="eval-panel">
    <div class="eval-toolbar">
      <div class="dataset-list">
        <button
          v-for="dataset in datasets"
          :key="dataset.id"
          class="dataset-item"
          :class="{ active: dataset.id === activeDatasetId }"
          @click="selectDataset(dataset.id)"
        >
          <span class="dataset-name">{{ dataset.name }}</span>
          <span class="dataset-meta muted">
            {{ dataset.case_count }} 用例 · {{ dataset.run_count }} 次运行
          </span>
        </button>
        <el-button text type="primary" :icon="Plus" @click="openCreateDataset">
          新建数据集
        </el-button>
      </div>

      <div v-if="activeDataset" class="dataset-actions">
        <el-button @click="openCaseDialog">添加用例</el-button>
        <el-button type="primary" :icon="VideoPlay" @click="openRunDialog">运行评测</el-button>
        <el-button text type="danger" :icon="Delete" @click="removeDataset">删除数据集</el-button>
      </div>
    </div>

    <el-empty
      v-if="!datasets.length && !loading"
      description="还没有评测数据集。新建数据集，标注问题和相关 chunk，然后对比不同检索配置。"
    >
      <el-button type="primary" @click="openCreateDataset">新建数据集</el-button>
    </el-empty>

    <template v-else-if="activeDataset">
      <div class="dataset-summary">
        <div>
          <div class="dataset-title">{{ activeDataset.name }}</div>
          <div class="muted dataset-desc">
            {{ activeDataset.description || '暂无描述' }} · 标注基于 chunk
            {{ activeDataset.chunk_size }} / {{ activeDataset.chunk_overlap }}
          </div>
        </div>
        <div v-if="latestCompleted" class="summary-metrics">
          <div class="summary-metric">
            <span class="metric-value">{{ formatPct(latestCompleted.metrics['recall@5']) }}</span>
            <span class="metric-label muted">Recall@5</span>
          </div>
          <div class="summary-metric">
            <span class="metric-value">{{ formatPct(latestCompleted.metrics.mrr) }}</span>
            <span class="metric-label muted">MRR</span>
          </div>
          <div class="summary-metric">
            <span class="metric-value">{{ formatPct(latestCompleted.metrics.refusal_accuracy) }}</span>
            <span class="metric-label muted">拒答准确率</span>
          </div>
          <div class="summary-metric">
            <span class="metric-value">{{ latestCompleted.metrics.p95_latency_ms }} ms</span>
            <span class="metric-label muted">P95 耗时</span>
          </div>
        </div>
      </div>

      <el-tabs v-model="activeTab">
        <el-tab-pane :label="`用例 (${cases.length})`" name="cases">
          <div class="tab-toolbar">
            <el-input
              v-model="caseFilter"
              placeholder="过滤问题"
              clearable
              style="max-width: 280px"
            />
            <span class="muted">可答案例按 chunk 计算 recall；不可答案例只计算拒答率</span>
          </div>

          <el-table v-loading="caseLoading" :data="filteredCases" size="small">
            <el-table-column prop="question" label="问题" min-width="280" />
            <el-table-column label="类型" width="90">
              <template #default="{ row }">
                <el-tag :type="row.answerable ? 'success' : 'info'" size="small">
                  {{ row.answerable ? '可答' : '不可答' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="相关 chunk" min-width="220">
              <template #default="{ row }">
                <span v-if="!row.relevant_refs.length" class="muted">—</span>
                <el-tag
                  v-for="ref in row.relevant_refs"
                  v-else
                  :key="refKey(ref)"
                  size="small"
                  type="info"
                  class="ref-tag"
                >
                  {{ ref.filename || 'chunk' }} #{{ ref.chunk_index }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="创建时间" width="150">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="80" align="right">
              <template #default="{ row }">
                <el-button text type="danger" @click="removeCase(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!caseLoading && !filteredCases.length" description="还没有用例" />
        </el-tab-pane>

        <el-tab-pane :label="`运行记录 (${runs.length})`" name="runs">
          <div class="tab-toolbar compare-bar">
            <div class="compare-selects">
              <el-select v-model="compareBaseId" placeholder="基线运行" size="small" style="width: 210px">
                <el-option
                  v-for="run in completedRuns"
                  :key="run.id"
                  :label="`${formatTime(run.created_at)} · MRR ${formatPct(run.metrics.mrr)}`"
                  :value="run.id"
                />
              </el-select>
              <span class="muted">→</span>
              <el-select
                v-model="compareCandidateId"
                placeholder="候选运行"
                size="small"
                style="width: 210px"
              >
                <el-option
                  v-for="run in completedRuns"
                  :key="run.id"
                  :label="`${formatTime(run.created_at)} · MRR ${formatPct(run.metrics.mrr)}`"
                  :value="run.id"
                />
              </el-select>
              <el-button
                size="small"
                :disabled="completedRuns.length < 2"
                @click="openCompare"
              >
                对比
              </el-button>
            </div>
            <span class="muted">同数据集、同索引版本的运行才可对比</span>
          </div>

          <el-table v-loading="runsLoading" :data="runs" size="small">
            <el-table-column label="开始时间" width="150">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="150">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small">
                  {{ statusLabels[row.status as EvalRunStatus] }}
                </el-tag>
                <span
                  v-if="row.status === 'running' || row.status === 'pending'"
                  class="muted progress-text"
                >
                  {{ row.progress.done || 0 }}/{{ row.progress.total || 0 }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="配置" min-width="200">
              <template #default="{ row }">
                <span class="config-text">{{ configSummary(row.config) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="Recall@5" width="100" align="right">
              <template #default="{ row }">{{ formatPct(row.metrics['recall@5']) }}</template>
            </el-table-column>
            <el-table-column label="MRR" width="90" align="right">
              <template #default="{ row }">{{ formatPct(row.metrics.mrr) }}</template>
            </el-table-column>
            <el-table-column label="拒答" width="90" align="right">
              <template #default="{ row }">{{ formatPct(row.metrics.refusal_accuracy) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="150" align="right">
              <template #default="{ row }">
                <el-button text type="primary" @click="openRunDetail(row)">详情</el-button>
                <el-button
                  v-if="row.status === 'pending' || row.status === 'running'"
                  text
                  type="warning"
                  @click="cancelActiveRun(row)"
                >
                  取消
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!runsLoading && !runs.length" description="还没有运行记录" />
        </el-tab-pane>
      </el-tabs>
    </template>

    <el-dialog v-model="createDatasetVisible" title="新建评测数据集" width="460px">
      <el-form label-position="top">
        <el-form-item label="名称" required>
          <el-input v-model="datasetForm.name" maxlength="200" placeholder="例如：FAQ 回归集 v1" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="datasetForm.description"
            type="textarea"
            :rows="2"
            maxlength="1000"
            placeholder="这个数据集覆盖哪些问题、用于对比什么"
          />
        </el-form-item>
      </el-form>
      <div class="dialog-note muted">
        数据集会记录当前知识库的切块参数；之后修改切块参数会导致标注失效。
      </div>
      <template #footer>
        <el-button @click="createDatasetVisible = false">取消</el-button>
        <el-button type="primary" @click="submitDataset">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="caseDialogVisible" title="添加评测用例" width="720px">
      <el-form label-position="top">
        <el-form-item label="问题" required>
          <el-input v-model="caseForm.question" type="textarea" :rows="2" maxlength="2000" />
        </el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="caseForm.answerable">
            <el-radio :value="true">可回答（需要标注相关 chunk）</el-radio>
            <el-radio :value="false">不可回答（应触发拒答）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="caseForm.notes" maxlength="1000" placeholder="可选" />
        </el-form-item>
      </el-form>

      <template v-if="caseForm.answerable">
        <div class="ref-search">
          <el-input
            v-model="refQuery"
            placeholder="按关键词搜索 chunk，例如：续航、保修、蓝牙"
            clearable
            @keydown.enter="doSearchChunks"
          >
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
          <el-button :loading="refLoading" @click="doSearchChunks">搜索</el-button>
        </div>

        <div v-if="selectedRefs.length" class="selected-refs">
          <span class="muted">已选：</span>
          <el-tag
            v-for="ref in selectedRefs"
            :key="refKey(ref)"
            closable
            size="small"
            @close="
              selectedRefs = selectedRefs.filter((item) => refKey(item) !== refKey(ref))
            "
          >
            {{ ref.filename }} #{{ ref.chunk_index }}
          </el-tag>
        </div>

        <div v-loading="refLoading" class="chunk-results">
          <button
            v-for="chunk in refResults"
            :key="chunk.id"
            class="chunk-option"
            :class="{ selected: isRefSelected(chunk) }"
            @click="toggleRef(chunk)"
          >
            <div class="chunk-option-head">
              <el-checkbox :model-value="isRefSelected(chunk)" @click.prevent />
              <span class="chunk-file">{{ chunk.filename }} #{{ chunk.chunk_index }}</span>
              <span class="muted chunk-heading">{{ chunk.heading_path || '无标题' }}</span>
            </div>
            <div class="chunk-preview">{{ chunk.content }}</div>
          </button>
          <el-empty
            v-if="!refResults.length && !refLoading"
            description="输入关键词后回车或点击搜索"
          />
        </div>
      </template>

      <template #footer>
        <el-button @click="caseDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="caseSaving" @click="submitCase">保存用例</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="runDialogVisible" title="运行评测" width="700px">
      <div class="preset-row">
        <el-radio-group v-model="runForm.preset" @change="applyPreset">
          <el-radio-button v-for="preset in presets" :key="preset.key" :value="preset.key">
            {{ preset.label }}
          </el-radio-button>
        </el-radio-group>
      </div>

      <div class="run-config-grid">
        <label>
          <span>最终 top-k</span>
          <el-input-number v-model="runForm.final_top_k" :min="1" :max="20" />
        </label>
        <label>
          <span>向量分数下限</span>
          <el-input-number
            v-model="runForm.min_vector_score"
            :min="-1"
            :max="1"
            :step="0.05"
            :precision="2"
          />
        </label>
        <label>
          <span>RRF k</span>
          <el-input-number v-model="runForm.rrf_k" :min="1" :max="200" />
        </label>
        <label>
          <span>向量权重</span>
          <el-input-number v-model="runForm.vector_weight" :min="0" :max="5" :step="0.1" />
        </label>
        <label>
          <span>BM25 权重</span>
          <el-input-number v-model="runForm.bm25_weight" :min="0" :max="5" :step="0.1" />
        </label>
        <label class="switch-label">
          <span>启用重排</span>
          <el-switch v-model="runForm.use_rerank" />
        </label>
      </div>

      <div class="run-note muted">
        检索-only 运行，不调用 LLM，零 API 成本。用例按顺序执行，进度会实时刷新。
      </div>

      <div v-if="watchingRun" class="watching">
        <div class="watching-head">
          <el-tag :type="statusType(watchingRun.status)" size="small">
            {{ statusLabels[watchingRun.status] }}
          </el-tag>
          <span class="muted">
            {{ watchingRun.progress.done || 0 }} / {{ watchingRun.progress.total || 0 }}
          </span>
        </div>
        <el-progress
          :percentage="
            watchingRun.progress.total
              ? Math.round(
                  ((watchingRun.progress.done || 0) / watchingRun.progress.total) * 100,
                )
              : 0
          "
          :status="watchingRun.status === 'failed' ? 'exception' : undefined"
        />
        <div v-if="watchingRun.status === 'completed'" class="watching-metrics">
          <div class="watching-metric">
            <span class="metric-value">{{ formatPct(watchingRun.metrics['recall@5']) }}</span>
            <span class="muted">Recall@5</span>
          </div>
          <div class="watching-metric">
            <span class="metric-value">{{ formatPct(watchingRun.metrics.mrr) }}</span>
            <span class="muted">MRR</span>
          </div>
          <div class="watching-metric">
            <span class="metric-value">
              {{ formatPct(watchingRun.metrics.refusal_accuracy) }}
            </span>
            <span class="muted">拒答准确率</span>
          </div>
          <div class="watching-metric">
            <span class="metric-value">{{ watchingRun.metrics.p95_latency_ms }} ms</span>
            <span class="muted">P95 耗时</span>
          </div>
          <el-button text type="primary" @click="openRunDetail(watchingRun)">查看逐条结果</el-button>
        </div>
        <div v-if="watchingRun.error" class="error-text">{{ watchingRun.error }}</div>
      </div>

      <template #footer>
        <el-button @click="runDialogVisible = false">关闭</el-button>
        <el-button
          type="primary"
          :loading="runStarting"
          :disabled="Boolean(watchingId) || !cases.length"
          @click="startRun"
        >
          开始运行
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.eval-panel {
  padding-top: 4px;
}

.eval-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.dataset-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.dataset-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 14px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  background: #fff;
  text-align: left;
  cursor: pointer;
}

.dataset-item.active {
  border-color: #6366f1;
  background: #eef2ff;
}

.dataset-name {
  font-size: 13px;
  font-weight: 600;
}

.dataset-meta {
  font-size: 11px;
}

.dataset-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

.dataset-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 14px 18px;
  margin-bottom: 8px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #f8fafc;
}

.dataset-title {
  font-size: 15px;
  font-weight: 600;
}

.dataset-desc {
  margin-top: 3px;
  font-size: 12px;
}

.summary-metrics {
  display: flex;
  gap: 22px;
  flex-shrink: 0;
}

.summary-metric {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.metric-value {
  font-size: 18px;
  font-weight: 700;
  color: #4338ca;
}

.metric-label {
  font-size: 11px;
}

.tab-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  font-size: 12px;
}

.compare-bar {
  flex-wrap: wrap;
}

.compare-selects {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ref-tag {
  margin: 2px 4px 2px 0;
}

.config-text {
  color: #475569;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
}

.progress-text {
  margin-left: 6px;
  font-size: 11px;
}

.dialog-note,
.run-note {
  padding: 8px 10px;
  border-radius: 8px;
  background: #f8fafc;
  font-size: 12px;
}

.ref-search {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}

.selected-refs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
  font-size: 12px;
}

.chunk-results {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 300px;
  overflow-y: auto;
  padding: 2px;
}

.chunk-option {
  padding: 10px 12px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  background: #fff;
  text-align: left;
  cursor: pointer;
}

.chunk-option:hover {
  border-color: #c7d2fe;
}

.chunk-option.selected {
  border-color: #6366f1;
  background: #eef2ff;
}

.chunk-option-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.chunk-file {
  font-size: 12px;
  font-weight: 600;
}

.chunk-heading {
  overflow: hidden;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chunk-preview {
  display: -webkit-box;
  overflow: hidden;
  margin-top: 4px;
  color: #475569;
  font-size: 12px;
  line-height: 1.6;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.preset-row {
  margin-bottom: 14px;
}

.run-config-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px 18px;
  margin-bottom: 12px;
}

.run-config-grid label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: #475569;
  font-size: 12px;
}

.run-config-grid .switch-label {
  justify-content: flex-end;
}

.watching {
  margin-top: 14px;
  padding: 12px 14px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  background: #f8fafc;
}

.watching-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 12px;
}

.watching-metrics {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 20px;
  margin-top: 12px;
}

.watching-metric {
  display: flex;
  flex-direction: column;
}

.error-text {
  margin-top: 8px;
  color: #dc2626;
  font-size: 12px;
}
</style>
