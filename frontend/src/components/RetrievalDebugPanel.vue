<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'

import { debugRetrieval } from '@/api/chat'
import type { DebugRequest, DebugResponse, TraceCandidate } from '@/types'

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
  min_vector_score: 0.35,
  use_rerank: true,
})

const stageLabels: Record<Stage, string> = {
  vector: '向量候选',
  bm25: 'BM25 候选',
  fused: 'RRF 融合',
  final: '重排结果',
}

const timingLabels: Record<string, string> = {
  rewrite_ms: 'query 改写',
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
  ] as const
  for (const key of numericKeys) {
    const value = defaults[key]
    if (typeof value === 'number') {
      form[key] = value
    }
  }
  if (typeof defaults.use_rerank === 'boolean') {
    form.use_rerank = defaults.use_rerank
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

function notReady(): void {
  ElMessage.info('保存为评测用例将在 Week 3 上线')
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
        <el-button disabled @click="notReady">保存为评测用例</el-button>
        <span class="muted">Week 3：把一次检索固化成可回归的用例</span>
      </div>
    </template>

    <el-empty
      v-else-if="!loading"
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

.ok-text {
  color: #059669;
}

.warn-text {
  color: #d97706;
}
</style>
