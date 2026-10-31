<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back, Refresh } from '@element-plus/icons-vue'

import { getKb } from '@/api/kb'
import { getTrace, listTraces } from '@/api/traces'
import type { KnowledgeBase, TraceCandidate, TraceDetail, TraceSummary } from '@/types'

const props = defineProps<{ id: string }>()
const router = useRouter()

const kb = ref<KnowledgeBase | null>(null)
const traces = ref<TraceSummary[]>([])
const detail = ref<TraceDetail | null>(null)
const loading = ref(false)
const detailLoading = ref(false)
const activeStage = ref('final')

const STAGE_LABELS: Record<string, string> = {
  rewrite_ms: '查询改写',
  cache_ms: '缓存查找',
  multi_query_ms: '多查询扩展',
  embed_ms: '向量化',
  vector_ms: '向量召回',
  bm25_ms: 'BM25 召回',
  fusion_ms: 'RRF 融合',
  rerank_ms: '重排',
  crag_ms: 'CRAG 纠错',
  graph_ms: '图谱扩展',
  compress_ms: '上下文压缩',
  total_ms: '总计',
}

const CANDIDATE_TABS = [
  { key: 'final', label: '最终上下文' },
  { key: 'fused', label: '融合后' },
  { key: 'vector', label: '向量召回' },
  { key: 'bm25', label: 'BM25 召回' },
  { key: 'crag', label: 'CRAG 补充' },
  { key: 'graph', label: '图谱补充' },
]

const timings = computed(() => {
  const raw = detail.value?.stage_timings || {}
  // The API returns stages in pipeline order; total is rendered separately.
  const entries = Object.entries(raw).filter(([key]) => key !== 'total_ms')
  const total = Number(raw.total_ms || 0)
  const max = Math.max(...entries.map(([, ms]) => Number(ms) || 0), 1)
  return entries.map(([key, ms]) => ({
    key,
    label: STAGE_LABELS[key] || key,
    ms: Number(ms) || 0,
    // Width is relative to the slowest stage, so a 0.1 ms stage is still a
    // visible sliver rather than an invisible zero-width bar.
    width: Math.max(((Number(ms) || 0) / max) * 100, 0.8),
    share: total > 0 ? ((Number(ms) || 0) / total) * 100 : 0,
  }))
})

const totalMs = computed(() => Number(detail.value?.stage_timings?.total_ms || 0))

const candidates = computed<TraceCandidate[]>(() => {
  // Indexing with the ref object itself is a TS error; read it into a string.
  const key: string = activeStage.value
  return (detail.value?.candidates?.[key] as TraceCandidate[] | undefined) || []
})

const visibleTabs = computed(() =>
  CANDIDATE_TABS.filter((tab) => (detail.value?.candidates?.[tab.key] as TraceCandidate[] | undefined)?.length),
)

const crag = computed(() => {
  const raw = detail.value?.crag || {}
  if (!raw.applied) return null
  return {
    action: String(raw.action || ''),
    confidence: Number(raw.confidence || 0),
    grader: String(raw.grader || ''),
    reason: String(raw.reason || ''),
    retryQueries: (raw.retry_queries as string[] | undefined) || [],
    retrySource: raw.retry_source ? String(raw.retry_source) : '',
    added: Number(raw.added || 0),
    relaxedFloor: raw.relaxed_floor != null ? Number(raw.relaxed_floor) : null,
  }
})

const CRAG_TAGS: Record<string, { label: string; type: 'success' | 'warning' | 'danger' }> = {
  correct: { label: '证据充分', type: 'success' },
  ambiguous: { label: '证据不足，已二次召回', type: 'warning' },
  incorrect: { label: '证据不足，已二次召回', type: 'danger' },
}

const compressInfo = computed(() => {
  const raw = detail.value?.compress || {}
  if (!raw.applied) return null
  return {
    mode: String(raw.mode || ''),
    original: Number(raw.original_chars || 0),
    compressed: Number(raw.compressed_chars || 0),
    saved: Number(raw.saved_chars || 0),
    dropped: Number(raw.dropped_sentences || 0),
  }
})

async function load(traceId?: string) {
  loading.value = true
  try {
    kb.value = await getKb(props.id)
    const page = await listTraces(props.id, { limit: 30 })
    traces.value = page.items
    const target = traceId || page.items[0]?.id
    if (target) await select(target)
    else detail.value = null
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载检索链路失败')
  } finally {
    loading.value = false
  }
}

async function select(traceId: string) {
  detailLoading.value = true
  try {
    detail.value = await getTrace(traceId)
    activeStage.value = visibleTabs.value[0]?.key || 'final'
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载链路详情失败')
  } finally {
    detailLoading.value = false
  }
}

watch(
  () => props.id,
  () => load(),
)

onMounted(() => load())

function fmt(value: number | null | undefined, digits = 3): string {
  if (value == null || Number.isNaN(value)) return '—'
  return value.toFixed(digits)
}

function fmtTime(iso: string): string {
  const date = new Date(iso)
  return `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}`
}

function stageTag(candidate: TraceCandidate): string | null {
  if (candidate.crag_via) return 'CRAG 补充'
  if (candidate.graph_via) return '图谱补充'
  return null
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <el-button text :icon="Back" @click="router.push(`/kbs/${props.id}`)">
          返回知识库
        </el-button>
        <h2>检索链路 · {{ kb?.name || '...' }}</h2>
        <p class="muted">
          每次问答都会落库一条完整的检索记录。选一条可以看清它经过的每个阶段、
          每阶段耗时、候选从召回到重排的变化，以及 CRAG 纠错与上下文压缩是否介入。
        </p>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="load()">刷新</el-button>
    </div>

    <el-empty v-if="!loading && !traces.length" description="还没有检索记录，先去对话页问一个问题" />

    <div v-else class="layout">
      <el-card shadow="never" class="list-card">
        <template #header>
          <span>最近检索</span>
        </template>
        <div class="trace-list">
          <div
            v-for="item in traces"
            :key="item.id"
            class="trace-item"
            :class="{ active: detail?.id === item.id }"
            @click="select(item.id)"
          >
            <div class="trace-item-top">
              <span class="trace-item-time">{{ fmtTime(item.created_at) }}</span>
              <span class="trace-item-ms">{{ item.total_ms.toFixed(0) }} ms</span>
            </div>
            <div class="trace-item-query">{{ item.query }}</div>
            <div class="trace-item-tags">
              <el-tag v-if="item.cache_hit" size="small" type="info">缓存命中</el-tag>
              <el-tag v-if="item.reranked" size="small" type="success">已重排</el-tag>
              <el-tag size="small" type="warning">{{ item.final_count }} 条上下文</el-tag>
            </div>
          </div>
        </div>
      </el-card>

      <el-card v-loading="detailLoading" shadow="never" class="detail-card">
        <template v-if="detail" #header>
          <div class="detail-head">
            <span>链路详情</span>
            <span class="muted">总计 {{ totalMs.toFixed(1) }} ms</span>
          </div>
        </template>

        <div v-if="detail">
          <div class="kv-grid">
            <div class="kv">
              <span class="kv-label">检索 query</span>
              <span>{{ detail.rewritten_query }}</span>
            </div>
            <div v-if="detail.rewrite_applied" class="kv">
              <span class="kv-label">原始问题</span>
              <span class="muted">{{ detail.query }}</span>
            </div>
            <div v-if="detail.sub_queries?.length" class="kv">
              <span class="kv-label">多查询扩展</span>
              <span>
                <el-tag
                  v-for="sub in detail.sub_queries"
                  :key="sub"
                  size="small"
                  type="info"
                  class="sub-tag"
                >
                  {{ sub }}
                </el-tag>
              </span>
            </div>
          </div>

          <h4 class="section-title">阶段耗时</h4>
          <div class="waterfall">
            <div v-for="stage in timings" :key="stage.key" class="waterfall-row">
              <span class="waterfall-label">{{ stage.label }}</span>
              <div class="waterfall-track">
                <div class="waterfall-bar" :style="{ width: `${stage.width}%` }" />
              </div>
              <span class="waterfall-value">{{ stage.ms.toFixed(1) }} ms</span>
            </div>
          </div>

          <div v-if="crag" class="stage-card">
            <div class="stage-card-head">
              <span class="stage-name">CRAG 证据分级</span>
              <el-tag size="small" :type="CRAG_TAGS[crag.action]?.type || 'info'">
                {{ CRAG_TAGS[crag.action]?.label || crag.action }}
              </el-tag>
            </div>
            <div class="muted small">
              判定方式 {{ crag.grader }} · 置信度 {{ crag.confidence.toFixed(2) }} ·
              {{ crag.reason }}
            </div>
            <div v-if="crag.retryQueries.length" class="small">
              二次召回查询（{{ crag.retrySource }}）：
              <el-tag
                v-for="query in crag.retryQueries"
                :key="query"
                size="small"
                type="warning"
                class="sub-tag"
              >
                {{ query }}
              </el-tag>
              <span class="muted">
                · 阈值放宽至 {{ fmt(crag.relaxedFloor) }} · 新增 {{ crag.added }} 条
              </span>
            </div>
          </div>

          <div v-if="compressInfo" class="stage-card">
            <div class="stage-card-head">
              <span class="stage-name">上下文压缩</span>
              <el-tag size="small" type="success">
                省 {{ compressInfo.saved }} 字符（-{{
                  compressInfo.original
                    ? ((compressInfo.saved / compressInfo.original) * 100).toFixed(0)
                    : 0
                }}%）
              </el-tag>
            </div>
            <div class="muted small">
              模式 {{ compressInfo.mode }} · {{ compressInfo.original }} →
              {{ compressInfo.compressed }} 字符 · 丢弃 {{ compressInfo.dropped }} 个句子
            </div>
          </div>

          <h4 class="section-title">候选在各阶段的变化</h4>
          <el-tabs v-model="activeStage">
            <el-tab-pane
              v-for="tab in visibleTabs"
              :key="tab.key"
              :label="tab.label"
              :name="tab.key"
            />
          </el-tabs>
          <div class="candidate-list">
            <div v-for="(candidate, index) in candidates" :key="candidate.chunk_id" class="candidate">
              <span class="candidate-rank">#{{ index + 1 }}</span>
              <div class="candidate-body">
                <div class="candidate-head">
                  <span class="candidate-file">{{ candidate.filename }}</span>
                  <span class="muted">{{ candidate.heading_path || '无标题' }}</span>
                  <el-tag v-if="stageTag(candidate)" size="small" type="warning">
                    {{ stageTag(candidate) }}
                  </el-tag>
                  <el-tag v-if="candidate.compressed" size="small" type="info">已压缩</el-tag>
                </div>
                <div class="candidate-scores">
                  vec {{ fmt(candidate.vector_score) }} · bm25 {{ fmt(candidate.bm25_score) }} ·
                  rrf {{ fmt(candidate.fused_score) }} · rerank {{ fmt(candidate.rerank_score) }}
                </div>
                <div class="candidate-snippet">{{ candidate.content }}</div>
              </div>
            </div>
          </div>
        </div>

        <el-empty v-else-if="!detailLoading" description="选择左侧一条记录查看详情" />
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
}

.page-head h2 {
  margin: 4px 0 6px;
  font-size: 20px;
}

.page-head p {
  max-width: 760px;
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
}

.layout {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

@media (max-width: 1080px) {
  .layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

.trace-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 70vh;
  overflow-y: auto;
}

.trace-item {
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.15s;
}

.trace-item:hover {
  border-color: var(--el-color-primary-light-5);
}

.trace-item.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.trace-item-top {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.trace-item-query {
  margin: 4px 0 6px;
  font-size: 13px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.trace-item-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.detail-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.kv-grid {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.kv {
  display: flex;
  gap: 10px;
  font-size: 13px;
}

.kv-label {
  flex: 0 0 96px;
  color: var(--el-text-color-secondary);
}

.section-title {
  margin: 18px 0 10px;
  font-size: 14px;
}

.waterfall {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.waterfall-row {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr) 76px;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.waterfall-label {
  color: var(--el-text-color-secondary);
}

.waterfall-track {
  height: 10px;
  background: var(--el-fill-color-light);
  border-radius: 5px;
  overflow: hidden;
}

.waterfall-bar {
  height: 100%;
  background: var(--el-color-primary);
  border-radius: 5px;
}

.waterfall-value {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.stage-card {
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.stage-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.stage-name {
  font-size: 13px;
  font-weight: 600;
}

.candidate-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.candidate {
  display: flex;
  gap: 10px;
  padding: 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.candidate-rank {
  flex: 0 0 32px;
  font-weight: 600;
  color: var(--el-color-primary);
}

.candidate-body {
  min-width: 0;
  flex: 1;
}

.candidate-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.candidate-file {
  font-weight: 600;
}

.candidate-scores {
  margin: 4px 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.candidate-snippet {
  font-size: 12px;
  line-height: 1.7;
  color: var(--el-text-color-regular);
  white-space: pre-wrap;
  word-break: break-word;
}

.sub-tag {
  margin-right: 4px;
}

.small {
  font-size: 12px;
  line-height: 1.8;
}

.muted {
  color: var(--el-text-color-secondary);
}
</style>
