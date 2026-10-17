<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'

import { debugRetrieval } from '@/api/chat'
import { createCase, createDataset, listDatasets } from '@/api/eval'
import type {
  DebugRequest,
  DebugResponse,
  EvalDataset,
  RelevantRef,
  TraceCandidate,
} from '@/types'

const props = defineProps<{
  kbId: string
  defaults?: Record<string, unknown> | null
}>()

type Stage = 'vector' | 'bm25' | 'fused' | 'final'

const query = ref('')
const loading = ref(false)
const result = ref<DebugResponse | null>(null)
const activeStage = ref<Stage>('final')
const overrideEnabled = ref(false)

const form = reactive({
  vector_top_k: 20,
  bm25_top_k: 20,
  fusion_top_k: 20,
  final_top_k: 5,
  rrf_k: 60,
  min_vector_score: 0.45,
  use_rerank: true,
  use_multi_query: false,
  multi_query_count: 3,
  multi_query_timeout_seconds: 8,
  use_cache: true,
})

const stageLabels: Record<Stage, string> = {
  vector: '向量候选',
  bm25: 'BM25 候选',
  fused: 'RRF 融合',
  final: '重排结果',
}

const timingLabels: Record<string, string> = {
  rewrite_ms: 'query 改写',
  cache_ms: '缓存查询',
  multi_query_ms: '多查询扩展',
  embed_ms: '向量化',
  vector_ms: '向量检索',
  bm25_ms: 'BM25',
  fusion_ms: 'RRF 融合',
  rerank_ms: '重排',
  total_ms: '总计',
}

const candidates = computed<Record<Stage, TraceCandidate[]>>(() => ({
  vector: result.value?.candidates.vector || [],
  bm25: result.value?.candidates.bm25 || [],
  fused: result.value?.candidates.fused || [],
  final: result.value?.candidates.final || [],
}))

const currentCandidates = computed(() => {
  if (!result.value) {
    return []
  }
  return candidates.value[activeStage.value] || []
})

const timingRows = computed(() => {
  if (!result.value) {
    return []
  }
  const timings = result.value.stage_timings || {}
  const rows = Object.entries(timings)
    .filter(([stage]) => stage !== 'total_ms')
    .map(([stage, ms]) => ({ stage, label: timingLabels[stage] || stage, ms: Number(ms) }))
  const max = Math.max(...rows.map((row) => row.ms), 1)
  return rows.map((row) => ({ ...row, width: Math.max((row.ms / max) * 100, 1.5) }))
})

const totalMs = computed(() => Number(result.value?.stage_timings.total_ms || 0))

const saveDialogVisible = ref(false)
const saveSaving = ref(false)
const datasets = ref<EvalDataset[]>([])
const saveDatasetId = ref('')
const saveQuestion = ref('')
const saveAnswerable = ref(true)
const saveNotes = ref('')
const saveRefs = ref<RelevantRef[]>([])

const finalCandidates = computed<TraceCandidate[]>(() => result.value?.candidates.final || [])

function isSavedRef(candidate: TraceCandidate): boolean {
  return saveRefs.value.some(
    (ref) =>
      ref.document_id === candidate.document_id && ref.chunk_index === candidate.chunk_index,
  )
}

function toggleSavedRef(candidate: TraceCandidate): void {
  if (isSavedRef(candidate)) {
    saveRefs.value = saveRefs.value.filter(
      (ref) =>
        !(ref.document_id === candidate.document_id && ref.chunk_index === candidate.chunk_index),
    )
    return
  }
  saveRefs.value.push({
    document_id: candidate.document_id,
    chunk_index: candidate.chunk_index,
    filename: candidate.filename,
  })
}

async function loadDatasets(): Promise<void> {
  const page = await listDatasets(props.kbId)
  datasets.value = page.items
  if (!datasets.value.some((item) => item.id === saveDatasetId.value)) {
    saveDatasetId.value = datasets.value[0]?.id || ''
  }
}

async function openSaveDialog(): Promise<void> {
  if (!result.value) {
    return
  }
  saveQuestion.value = result.value.query
  saveAnswerable.value = true
  saveNotes.value = ''
  saveRefs.value = []
  saveDialogVisible.value = true
  try {
    await loadDatasets()
  } catch {
    // Error toast is handled by the axios interceptor.
  }
}

async function createDatasetInline(): Promise<void> {
  try {
    const { value } = await ElMessageBox.prompt('新数据集名称', '新建数据集', {
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputPattern: /\S+/,
      inputErrorMessage: '名称不能为空',
    })
    const dataset = await createDataset(props.kbId, { name: value.trim() })
    await loadDatasets()
    saveDatasetId.value = dataset.id
    ElMessage.success('数据集已创建')
  } catch {
    // Cancelled by the user, or the axios interceptor already showed the error.
  }
}

async function submitSavedCase(): Promise<void> {
  if (!saveDatasetId.value) {
    ElMessage.warning('请选择或新建一个数据集')
    return
  }
  if (!saveQuestion.value.trim()) {
    ElMessage.warning('请输入问题')
    return
  }
  if (saveAnswerable.value && !saveRefs.value.length) {
    ElMessage.warning('请至少勾选一个包含答案的 chunk，或标记为不可答')
    return
  }
  saveSaving.value = true
  try {
    await createCase(saveDatasetId.value, {
      question: saveQuestion.value.trim(),
      answerable: saveAnswerable.value,
      relevant_refs: saveAnswerable.value ? saveRefs.value : [],
      notes: saveNotes.value.trim() || null,
    })
    saveDialogVisible.value = false
    ElMessage.success('已保存为评测用例')
  } finally {
    saveSaving.value = false
  }
}

const effectiveConfig = computed(() => {
  if (!result.value) {
    return []
  }
  return Object.entries(result.value.config).map(([key, value]) => ({
    key,
    value: typeof value === 'number' ? Number(value.toFixed(3)) : String(value),
  }))
})

function applyDefaults(defaults: Record<string, unknown> | null | undefined): void {
  if (!defaults) {
    return
  }
  const numericKeys = [
    'vector_top_k',
    'bm25_top_k',
    'fusion_top_k',
    'final_top_k',
    'rrf_k',
    'min_vector_score',
    'multi_query_count',
    'multi_query_timeout_seconds',
  ] as const
  for (const key of numericKeys) {
    const value = defaults[key]
    if (typeof value === 'number') {
      form[key] = value
    }
  }
  for (const key of ['use_rerank', 'use_multi_query', 'use_cache'] as const) {
    if (typeof defaults[key] === 'boolean') {
      form[key] = defaults[key]
    }
  }
}

async function run(): Promise<void> {
  const text = query.value.trim()
  if (!text || loading.value) {
    return
  }
  loading.value = true
  try {
    const payload: DebugRequest = { query: text }
    if (overrideEnabled.value) {
      payload.config = { ...form }
    }
    result.value = await debugRetrieval(props.kbId, payload)
    activeStage.value = result.value.reranked ? 'final' : 'fused'
  } catch {
    // Error toast is handled by the axios interceptor.
  } finally {
    loading.value = false
  }
}

function stageCount(stage: Stage): number {
  return candidates.value[stage].length
}

function fmt(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : value.toFixed(4)
}

function useQuery(text: string): void {
  query.value = text
  run()
}

function queryLabel(index: number): string {
  return index === 0 ? '原查询' : `扩展${index}`
}

watch(
  () => props.defaults,
  (value) => applyDefaults(value),
  { immediate: true },
)
</script>

<template>
  <div class="debug-panel">
    <div class="query-row">
      <el-input
        v-model="query"
        size="large"
        placeholder="输入一个问题，查看每个检索阶段的候选与耗时"
        clearable
        @keydown.enter="run"
      >
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>
      <el-button type="primary" size="large" :loading="loading" @click="run">检索</el-button>
    </div>

    <div class="samples">
      <span class="muted">示例：</span>
      <el-button
        v-for="sample in ['X1 的续航是多久？', '手表进水还能保修吗？', 'X2 支持离线听歌吗？']"
        :key="sample"
        text
        size="small"
        @click="useQuery(sample)"
      >
        {{ sample }}
      </el-button>
    </div>

    <el-collapse class="override">
      <el-collapse-item name="config">
        <template #title>
          <span class="override-title">检索参数覆盖</span>
          <span class="muted override-hint">
            {{ overrideEnabled ? '本次请求使用下方参数' : '默认沿用知识库配置' }}
          </span>
        </template>
        <div class="override-head">
          <el-switch v-model="overrideEnabled" active-text="启用覆盖" />
        </div>
        <div class="config-grid">
          <label>
            <span>向量 top-k</span>
            <el-input-number v-model="form.vector_top_k" :min="1" :max="50" :disabled="!overrideEnabled" />
          </label>
          <label>
            <span>BM25 top-k</span>
            <el-input-number v-model="form.bm25_top_k" :min="1" :max="50" :disabled="!overrideEnabled" />
          </label>
          <label>
            <span>融合 top-k</span>
            <el-input-number v-model="form.fusion_top_k" :min="1" :max="50" :disabled="!overrideEnabled" />
          </label>
          <label>
            <span>最终 top-k</span>
            <el-input-number v-model="form.final_top_k" :min="1" :max="20" :disabled="!overrideEnabled" />
          </label>
          <label>
            <span>RRF k</span>
            <el-input-number v-model="form.rrf_k" :min="1" :max="200" :disabled="!overrideEnabled" />
          </label>
          <label>
            <span>向量分数下限</span>
            <el-input-number
              v-model="form.min_vector_score"
              :min="-1"
              :max="1"
              :step="0.05"
              :precision="2"
              :disabled="!overrideEnabled"
            />
          </label>
          <label class="switch-label">
            <span>启用重排</span>
            <el-switch v-model="form.use_rerank" :disabled="!overrideEnabled" />
          </label>
          <label class="switch-label">
            <span>多查询扩展</span>
            <el-switch v-model="form.use_multi_query" :disabled="!overrideEnabled" />
          </label>
          <label>
            <span>扩展查询数</span>
            <el-input-number
              v-model="form.multi_query_count"
              :min="1"
              :max="5"
              :disabled="!overrideEnabled || !form.use_multi_query"
            />
          </label>
          <label class="switch-label">
            <span>Redis 检索缓存</span>
            <el-switch v-model="form.use_cache" :disabled="!overrideEnabled" />
          </label>
        </div>
      </el-collapse-item>
    </el-collapse>

    <template v-if="result">
      <div class="summary">
        <div class="summary-main">
          <div class="summary-query">{{ result.rewritten_query }}</div>
          <div class="summary-meta muted">
            <span v-if="result.rewrite_applied">已改写 · 原始：{{ result.query }}</span>
            <span v-else>未改写</span>
            <span>·</span>
            <span :class="result.reranked ? 'ok-text' : 'warn-text'">
              {{ result.reranked ? '重排已生效' : `重排未生效${result.rerank_error ? '：' + result.rerank_error : ''}` }}
            </span>
            <span>·</span>
            <span :class="result.cache_hit ? 'ok-text' : 'muted'">
              {{ result.cache_hit ? 'Redis 缓存命中' : '缓存未命中' }}
            </span>
          </div>
          <div v-if="result.sub_queries.length" class="sub-queries">
            <el-tag
              v-for="subQuery in result.sub_queries"
              :key="subQuery"
              size="small"
              type="info"
            >
              {{ subQuery }}
            </el-tag>
            <span v-if="result.multi_query_error" class="warn-text">
              扩展失败：{{ result.multi_query_error }}
            </span>
          </div>
        </div>
        <div class="summary-total">
          <span class="total-value">{{ totalMs.toFixed(0) }}</span>
          <span class="muted">ms</span>
        </div>
      </div>

      <div class="count-grid">
        <div
          v-for="stage in (['vector', 'bm25', 'fused', 'final'] as Stage[])"
          :key="stage"
          class="count-card"
        >
          <span class="count-value">{{ stageCount(stage) }}</span>
          <span class="count-label muted">{{ stageLabels[stage] }}</span>
        </div>
      </div>

      <div class="timing-panel">
        <div class="panel-title">分阶段耗时</div>
        <div v-for="row in timingRows" :key="row.stage" class="timing-row">
          <span class="timing-label">{{ row.label }}</span>
          <div class="timing-track">
            <div class="timing-bar" :style="{ width: `${row.width}%` }" />
          </div>
          <span class="timing-ms">{{ row.ms.toFixed(1) }} ms</span>
        </div>
      </div>

      <div class="config-chips">
        <el-tag v-for="item in effectiveConfig" :key="item.key" size="small" type="info">
          {{ item.key }}={{ item.value }}
        </el-tag>
      </div>

      <el-tabs v-model="activeStage" class="stage-tabs">
        <el-tab-pane
          v-for="stage in (['vector', 'bm25', 'fused', 'final'] as Stage[])"
          :key="stage"
          :label="`${stageLabels[stage]} (${stageCount(stage)})`"
          :name="stage"
        >
          <el-table :data="currentCandidates" size="small" row-key="chunk_id">
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="chunk-content">{{ row.content }}</div>
              </template>
            </el-table-column>
            <el-table-column type="index" label="#" width="52" />
            <el-table-column label="文件" min-width="160">
              <template #default="{ row }">{{ row.filename }}</template>
            </el-table-column>
            <el-table-column label="标题 / chunk" min-width="180">
              <template #default="{ row }">
                <span>{{ row.heading_path || '无标题' }}</span>
                <span class="muted"> · #{{ row.chunk_index }}</span>
                <span v-if="row.page_number" class="muted"> · 第 {{ row.page_number }} 页</span>
              </template>
            </el-table-column>
            <el-table-column
              v-if="stage === 'fused' || stage === 'final'"
              label="命中查询"
              width="96"
              align="center"
            >
              <template #default="{ row }">
                <span v-if="row.matched_queries?.length" class="muted">
                  {{ row.matched_queries.map(queryLabel).join(' / ') }}
                </span>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column v-if="stage === 'vector'" label="向量分" width="110" align="right">
              <template #default="{ row }">{{ fmt(row.vector_score) }}</template>
            </el-table-column>
            <el-table-column v-if="stage === 'bm25'" label="BM25 分" width="110" align="right">
              <template #default="{ row }">{{ fmt(row.bm25_score) }}</template>
            </el-table-column>
            <template v-if="stage === 'fused' || stage === 'final'">
              <el-table-column label="vec #" width="76" align="right">
                <template #default="{ row }">{{ row.vector_rank ?? '—' }}</template>
              </el-table-column>
              <el-table-column label="bm25 #" width="80" align="right">
                <template #default="{ row }">{{ row.bm25_rank ?? '—' }}</template>
              </el-table-column>
              <el-table-column label="RRF 分" width="100" align="right">
                <template #default="{ row }">{{ fmt(row.fused_score) }}</template>
              </el-table-column>
              <el-table-column v-if="stage === 'final'" label="重排分" width="100" align="right">
                <template #default="{ row }">{{ fmt(row.rerank_score) }}</template>
              </el-table-column>
            </template>
          </el-table>
          <el-empty v-if="!currentCandidates.length" description="没有候选，可能全部被分数阈值过滤" />
        </el-tab-pane>
      </el-tabs>

      <div class="footer-actions">
        <el-button type="primary" plain @click="openSaveDialog">保存为评测用例</el-button>
        <span class="muted">把这次检索固化成可回归的评测用例</span>
      </div>
    </template>

    <el-dialog v-model="saveDialogVisible" title="保存为评测用例" width="680px">
      <el-form label-position="top">
        <el-form-item label="数据集" required>
          <div class="dataset-picker">
            <el-select v-model="saveDatasetId" placeholder="选择数据集" style="flex: 1">
              <el-option
                v-for="dataset in datasets"
                :key="dataset.id"
                :label="`${dataset.name}（${dataset.case_count} 用例）`"
                :value="dataset.id"
              />
            </el-select>
            <el-button @click="createDatasetInline">新建数据集</el-button>
          </div>
          <div v-if="!datasets.length" class="muted form-hint">
            还没有数据集，先新建一个，用例会记录当前知识库的切块参数。
          </div>
        </el-form-item>

        <el-form-item label="问题" required>
          <el-input v-model="saveQuestion" type="textarea" :rows="2" maxlength="2000" />
        </el-form-item>

        <el-form-item label="类型">
          <el-radio-group v-model="saveAnswerable">
            <el-radio :value="true">可回答</el-radio>
            <el-radio :value="false">不可回答（应触发拒答）</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="备注">
          <el-input v-model="saveNotes" maxlength="1000" placeholder="可选，例如：来自调试台的漏检分析" />
        </el-form-item>
      </el-form>

      <template v-if="saveAnswerable">
        <div class="save-ref-title">
          勾选包含答案的 chunk
          <span class="muted">（来自当前最终结果，共 {{ finalCandidates.length }} 条）</span>
        </div>
        <div class="save-ref-list">
          <button
            v-for="candidate in finalCandidates"
            :key="candidate.chunk_id"
            class="save-ref-item"
            :class="{ selected: isSavedRef(candidate) }"
            @click="toggleSavedRef(candidate)"
          >
            <el-checkbox :model-value="isSavedRef(candidate)" @click.prevent />
            <div class="save-ref-main">
              <div class="save-ref-head">
                <span class="save-ref-file">
                  {{ candidate.filename }} #{{ candidate.chunk_index }}
                  <template v-if="candidate.page_number">（第 {{ candidate.page_number }} 页）</template>
                </span>
                <span class="muted">{{ candidate.heading_path || '无标题' }}</span>
              </div>
              <div class="save-ref-content">{{ candidate.content }}</div>
            </div>
          </button>
          <el-empty
            v-if="!finalCandidates.length"
            :image-size="60"
            description="当前没有最终候选，可改为保存不可答案例"
          />
        </div>
        <div class="muted form-hint">
          这里只列出最终结果。若正确答案不在其中，请到知识库「评测」页按关键词搜索 chunk 标注。
        </div>
      </template>

      <template #footer>
        <el-button @click="saveDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saveSaving" @click="submitSavedCase">
          保存用例
        </el-button>
      </template>
    </el-dialog>

    <el-empty
      v-if="!result && !loading"
      description="输入问题后，可以看到向量、BM25、融合与重排四个阶段的候选"
    />
  </div>
</template>

<style scoped>
.debug-panel {
  padding-top: 4px;
}

.query-row {
  display: flex;
  gap: 10px;
}

.samples {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px;
  margin: 8px 0 4px;
  font-size: 12px;
}

.override {
  margin-bottom: 12px;
}

.override-title {
  font-size: 13px;
  font-weight: 600;
}

.override-hint {
  margin-left: 10px;
  font-size: 12px;
}

.override-head {
  margin-bottom: 10px;
}

.config-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 12px 18px;
}

.config-grid label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: #475569;
  font-size: 12px;
}

.config-grid .switch-label {
  justify-content: flex-end;
}

.summary {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 18px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #f8fafc;
}

.summary-query {
  font-size: 15px;
  font-weight: 600;
}

.summary-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
  font-size: 12px;
}

.sub-queries {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
  font-size: 12px;
}

.summary-total {
  flex: 0 0 auto;
}

.total-value {
  font-size: 28px;
  font-weight: 700;
  color: #4f46e5;
}

.count-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin: 14px 0;
}

.count-card {
  display: flex;
  flex-direction: column;
  padding: 12px 14px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  background: #fff;
}

.count-value {
  font-size: 22px;
  font-weight: 700;
}

.count-label {
  font-size: 12px;
}

.timing-panel {
  padding: 14px 16px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #fff;
}

.panel-title {
  margin-bottom: 10px;
  font-size: 13px;
  font-weight: 600;
}

.timing-row {
  display: grid;
  grid-template-columns: 82px 1fr 76px;
  gap: 10px;
  align-items: center;
  margin-bottom: 6px;
  font-size: 12px;
}

.timing-label {
  color: #64748b;
}

.timing-track {
  height: 8px;
  overflow: hidden;
  border-radius: 999px;
  background: #f1f5f9;
}

.timing-bar {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #6366f1, #22d3ee);
}

.timing-ms {
  color: #334155;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  text-align: right;
}

.config-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 12px 0 0;
}

.stage-tabs {
  margin-top: 6px;
}

.chunk-content {
  padding: 4px 16px 10px;
  color: #374151;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.footer-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  font-size: 12px;
}

.dataset-picker {
  display: flex;
  width: 100%;
  gap: 8px;
}

.form-hint {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.6;
}

.save-ref-title {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
}

.save-ref-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 280px;
  overflow-y: auto;
  margin-bottom: 8px;
  padding: 2px;
}

.save-ref-item {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 9px 11px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  background: #fff;
  text-align: left;
  cursor: pointer;
}

.save-ref-item:hover {
  border-color: #c7d2fe;
}

.save-ref-item.selected {
  border-color: #6366f1;
  background: #eef2ff;
}

.save-ref-main {
  min-width: 0;
  flex: 1;
}

.save-ref-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.save-ref-file {
  font-weight: 600;
}

.save-ref-content {
  display: -webkit-box;
  overflow: hidden;
  margin-top: 3px;
  color: #475569;
  font-size: 12px;
  line-height: 1.6;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.ok-text {
  color: #059669;
}

.warn-text {
  color: #d97706;
}
</style>
