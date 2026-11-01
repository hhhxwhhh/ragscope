<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, VideoPause } from '@element-plus/icons-vue'

import { cancelRun, getRun, listRuns } from '@/api/eval'
import type { EvalResult, EvalRun, EvalRunDetail, EvalRunStatus } from '@/types'

const props = defineProps<{ id: string }>()
const router = useRouter()

const run = ref<EvalRunDetail | null>(null)
const loading = ref(true)
const notFound = ref(false)
const filter = ref<'all' | 'miss' | 'error'>('all')
const compareCandidates = ref<EvalRun[]>([])
const compareTargetId = ref('')

const metricDefs = [
  { key: 'recall@1', label: 'Recall@1' },
  { key: 'recall@3', label: 'Recall@3' },
  { key: 'recall@5', label: 'Recall@5' },
  { key: 'recall@10', label: 'Recall@10' },
  { key: 'hit@5', label: 'Hit@5' },
  { key: 'mrr', label: 'MRR' },
  { key: 'refusal_accuracy', label: '拒答准确率' },
  { key: 'answerable_refusal_rate', label: '误拒答率' },
]

const generationMetricDefs = [
  { key: 'generation_refusal_accuracy', label: '生成拒答准确率' },
  { key: 'generation_false_refusal_rate', label: '生成误拒答率' },
  { key: 'answer_citation_rate', label: '回答引用合规率' },
  { key: 'faithfulness_ratio', label: '答案事实性（接地率）' },
  { key: 'faithfulness_grounded_rate', label: '事实性达标率' },
]

const latencyDefs = [
  { key: 'avg_latency_ms', label: '平均', unit: 'ms' },
  { key: 'p50_latency_ms', label: 'P50', unit: 'ms' },
  { key: 'p95_latency_ms', label: 'P95', unit: 'ms' },
  { key: 'elapsed_seconds', label: '总耗时', unit: 's' },
]

const statusLabels: Record<EvalRunStatus, string> = {
  pending: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const isActive = computed(
  () => run.value?.status === 'pending' || run.value?.status === 'running',
)

const filteredResults = computed<EvalResult[]>(() => {
  const results = run.value?.results || []
  if (filter.value === 'error') {
    return results.filter((item) => Boolean(item.error))
  }
  if (filter.value === 'miss') {
    return results.filter((item) => {
      if (!item.answerable || item.error) {
        return false
      }
      const rank = item.metrics.first_relevant_rank
      return rank === null || (typeof rank === 'number' && rank > 5)
    })
  }
  return results
})

const progressPct = computed(() => {
  const progress = run.value?.progress
  if (!progress?.total) {
    return 0
  }
  return Math.round(((progress.done || 0) / progress.total) * 100)
})

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

function fmt(value: unknown): string {
  if (value === null || value === undefined) {
    return '—'
  }
  if (typeof value === 'number') {
    return `${(value * 100).toFixed(1)}%`
  }
  return String(value)
}

function fmtNum(value: unknown, digits = 0): string {
  return typeof value === 'number' ? value.toFixed(digits) : '—'
}

function formatTime(value: string | null): string {
  return value
    ? new Date(value).toLocaleString('zh-CN', { hour12: false })
    : '—'
}

function configEntries(): Array<{ key: string; value: string }> {
  if (!run.value) {
    return []
  }
  return Object.entries(run.value.config).map(([key, value]) => ({
    key,
    value: typeof value === 'number' ? Number(value.toFixed(3)).toString() : String(value),
  }))
}

async function load(showLoading = true): Promise<void> {
  if (showLoading) {
    loading.value = true
  }
  try {
    const detail = await getRun(props.id)
    run.value = detail
    notFound.value = false
    if (detail.status === 'completed') {
      await loadCompareCandidates(detail)
    }
    if (detail.status === 'pending' || detail.status === 'running') {
      startPolling()
    } else {
      stopPolling()
    }
  } catch {
    notFound.value = true
    stopPolling()
  } finally {
    if (showLoading) {
      loading.value = false
    }
  }
}

async function loadCompareCandidates(detail: EvalRunDetail): Promise<void> {
  const page = await listRuns(detail.dataset_id, 0, 50)
  compareCandidates.value = page.items.filter(
    (item) => item.status === 'completed' && item.id !== detail.id,
  )
}

let pollTimer: number | undefined

function startPolling(): void {
  if (pollTimer) {
    return
  }
  pollTimer = window.setInterval(async () => {
    try {
      await load(false)
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

async function handleCancel(): Promise<void> {
  if (!run.value) {
    return
  }
  await cancelRun(run.value.id)
  ElMessage.info('已请求取消，当前用例结束后停止')
  await load(false)
}

function goCompare(): void {
  if (!run.value || !compareTargetId.value) {
    return
  }
  router.push(`/eval/compare?base=${run.value.id}&candidate=${compareTargetId.value}`)
}

function backToKb(): void {
  router.push({ path: `/kbs/${run.value?.kb_id}`, query: { tab: 'eval' } })
}

function rankLabel(result: EvalResult): string {
  if (!result.answerable) {
    return '—'
  }
  const rank = result.metrics.first_relevant_rank
  return typeof rank === 'number' ? `#${rank}` : '未命中'
}

function isRefusalCorrect(result: EvalResult): boolean {
  return result.answerable ? !result.refused : result.refused
}

function generationTag(
  result: EvalResult,
): { type: 'success' | 'warning' | 'danger' | 'info'; label: string } {
  if (result.metrics.generation_error) {
    return { type: 'danger', label: '生成出错' }
  }
  if (result.metrics.answer_refused) {
    return { type: 'warning', label: '拒答' }
  }
  if (result.metrics.answer_grounded) {
    return { type: 'success', label: '有引用' }
  }
  return { type: 'info', label: '无引用' }
}

watch(
  () => props.id,
  () => {
    stopPolling()
    run.value = null
    load().catch(() => undefined)
  },
  { immediate: true },
)

onUnmounted(stopPolling)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div class="header-left">
        <el-button text :icon="ArrowLeft" @click="backToKb">返回知识库</el-button>
        <div>
          <h2 class="page-title">评测运行详情</h2>
          <p class="page-subtitle">
            {{ run?.dataset_name || '评测数据集' }} · 索引版本 {{ run?.index_version ?? '—' }}
          </p>
        </div>
      </div>
      <div v-if="run" class="header-actions">
        <el-tag :type="statusType(run.status)" size="large">
          {{ statusLabels[run.status] }}
        </el-tag>
        <el-button v-if="isActive" type="warning" :icon="VideoPause" @click="handleCancel">
          取消运行
        </el-button>
      </div>
    </div>

    <el-skeleton v-if="loading" :rows="8" animated />

    <el-empty v-else-if="notFound || !run" description="运行记录不存在，或无权访问">
      <el-button type="primary" @click="router.push('/kbs')">返回知识库</el-button>
    </el-empty>

    <template v-else>
      <el-card v-if="isActive" shadow="never" class="progress-card">
        <div class="progress-head">
          <span>
            正在执行 {{ run.progress.done || 0 }} / {{ run.progress.total || 0 }} 条用例
          </span>
          <span class="muted">{{ progressPct }}%</span>
        </div>
        <el-progress :percentage="progressPct" :show-text="false" />
        <div class="muted progress-hint">
          每条用例完成后提交一次结果；中途取消会保留已完成部分。
        </div>
      </el-card>

      <el-alert
        v-if="run.error"
        type="error"
        :closable="false"
        :title="run.error"
        class="run-error"
      />

      <div v-if="run.status === 'completed'" class="metric-grid">
        <el-card v-for="metric in metricDefs" :key="metric.key" shadow="never" class="metric-card">
          <div class="metric-label">{{ metric.label }}</div>
          <div class="metric-value">{{ fmt(run.metrics[metric.key]) }}</div>
        </el-card>
      </div>

      <el-card
        v-if="run.status === 'completed' && run.include_generation"
        shadow="never"
        class="generation-card"
      >
        <template #header>
          <div class="generation-head">
            <span class="card-title">生成评测</span>
            <el-tag size="small" type="warning">prompt: {{ run.prompt_variant || 'grounded' }}</el-tag>
          </div>
        </template>
        <div class="generation-metrics">
          <div v-for="metric in generationMetricDefs" :key="metric.key" class="generation-metric">
            <span class="generation-value">{{ fmt(run.metrics[metric.key]) }}</span>
            <span class="muted">{{ metric.label }}</span>
          </div>
          <div class="generation-metric">
            <span class="generation-value">
              {{ fmtNum(run.metrics.avg_generation_latency_ms, 0) }}<small> ms</small>
            </span>
            <span class="muted">平均生成耗时</span>
          </div>
          <div class="generation-metric">
            <span class="generation-value">{{ fmtNum(run.metrics.generation_total_tokens) }}</span>
            <span class="muted">生成 tokens</span>
          </div>
        </div>
        <div class="muted generation-note">
          已生成 {{ run.metrics.generation_cases ?? 0 }} 条，出错 {{ run.metrics.generation_errors ?? 0 }} 条。
          引用合规指回答至少含一个有效引用且没有越界编号；拒答用固定短语做确定性判断，不做 LLM 打分。
        </div>
      </el-card>

      <div class="secondary-grid">
        <el-card shadow="never" class="info-card">
          <template #header><span class="card-title">用例与耗时</span></template>
          <div class="latency-row">
            <div v-for="item in latencyDefs" :key="item.key" class="latency-item">
              <span class="latency-value">
                {{ fmtNum(run.metrics[item.key], item.key === 'elapsed_seconds' ? 2 : 1) }}
                <small>{{ item.unit }}</small>
              </span>
              <span class="muted">{{ item.label }}</span>
            </div>
          </div>
          <div class="muted latency-note">
            已评分 {{ run.metrics.cases_scored ?? 0 }} / {{ run.metrics.cases_total ?? 0 }} 条；
            出错用例不参与质量指标。这里的耗时只统计检索，生成耗时见下方。
          </div>
        </el-card>

        <el-card shadow="never" class="info-card">
          <template #header><span class="card-title">运行配置</span></template>
          <div class="config-chips">
            <el-tag v-for="item in configEntries()" :key="item.key" size="small" type="info">
              {{ item.key }}={{ item.value }}
            </el-tag>
          </div>
          <div class="muted config-note">
            开始 {{ formatTime(run.started_at) }} · 结束 {{ formatTime(run.finished_at) }}
          </div>
          <div v-if="run.status === 'completed'" class="compare-row">
            <el-select
              v-model="compareTargetId"
              size="small"
              placeholder="选择另一次已完成运行"
              style="width: 230px"
              :disabled="!compareCandidates.length"
            >
              <el-option
                v-for="item in compareCandidates"
                :key="item.id"
                :label="`${formatTime(item.created_at)} · MRR ${fmt(item.metrics.mrr)}`"
                :value="item.id"
              />
            </el-select>
            <el-button size="small" :disabled="!compareTargetId" @click="goCompare">
              与本次对比
            </el-button>
          </div>
          <div v-if="!compareCandidates.length && run.status === 'completed'" class="muted config-note">
            同数据集还没有其他已完成的运行可对比。
          </div>
        </el-card>
      </div>

      <el-card shadow="never" class="results-card">
        <template #header>
          <div class="results-head">
            <span class="card-title">逐条结果 ({{ run.results.length }})</span>
            <el-radio-group v-model="filter" size="small">
              <el-radio-button value="all">全部</el-radio-button>
              <el-radio-button value="miss">未命中 / 排名靠后</el-radio-button>
              <el-radio-button value="error">出错</el-radio-button>
            </el-radio-group>
          </div>
        </template>

        <el-table :data="filteredResults" size="small" row-key="id">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="result-detail">
                <div class="detail-row">
                  <span class="detail-label">标注相关 chunk：</span>
                  <template v-if="row.relevant_refs.length">
                    <el-tag
                      v-for="ref in row.relevant_refs"
                      :key="`${ref.document_id}#${ref.chunk_index}`"
                      size="small"
                      type="success"
                    >
                      {{ ref.filename || 'chunk' }} #{{ ref.chunk_index }}
                    </el-tag>
                  </template>
                  <span v-else class="muted">无（不可答案例）</span>
                </div>
                <div v-if="row.error" class="detail-error">{{ row.error }}</div>
                <div class="retrieved-list">
                  <div v-for="item in row.retrieved" :key="item.chunk_id" class="retrieved-item">
                    <span class="retrieved-rank">#{{ item.rank }}</span>
                    <div class="retrieved-main">
                      <div class="retrieved-head">
                        <span class="retrieved-file">{{ item.filename }} #{{ item.chunk_index }}</span>
                        <span class="muted">{{ item.heading_path || '无标题' }}</span>
                        <span class="muted mono">
                          fused {{ fmtNum(item.fused_score, 4) }}
                          <template v-if="item.rerank_score !== null && item.rerank_score !== undefined">
                            · rerank {{ fmtNum(item.rerank_score, 4) }}
                          </template>
                        </span>
                      </div>
                      <div class="retrieved-content">{{ item.content }}</div>
                    </div>
                  </div>
                  <el-empty
                    v-if="!row.retrieved.length"
                    :image-size="60"
                    description="检索为空（触发拒答）"
                  />
                </div>
                <div v-if="row.answer" class="answer-block">
                  <div class="detail-label">生成答案</div>
                  <div class="answer-text">{{ row.answer }}</div>
                  <div class="answer-meta muted mono">
                    生成 {{ fmtNum(row.metrics.generation_latency_ms) }} ms ·
                    {{ fmtNum(row.metrics.generation_tokens) }} tokens ·
                    {{ fmtNum(row.metrics.answer_citation_markers) }} 个引用标记
                    <template v-if="row.metrics.answer_invalid_citations">
                      · {{ fmtNum(row.metrics.answer_invalid_citations) }} 个越界
                    </template>
                  </div>
                </div>
                <div
                  v-if="row.metrics.faithfulness_unsupported?.length"
                  class="unsupported-block"
                >
                  <div class="detail-label">
                    事实性裁判判定为无依据的断言（{{
                      row.metrics.faithfulness_unsupported.length
                    }}）
                  </div>
                  <ul class="unsupported-list">
                    <li
                      v-for="(claim, index) in row.metrics.faithfulness_unsupported"
                      :key="index"
                    >
                      {{ claim }}
                    </li>
                  </ul>
                </div>
                <div v-if="row.metrics.generation_error" class="detail-error">
                  生成失败：{{ row.metrics.generation_error }}
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="question" label="问题" min-width="280" />
          <el-table-column label="类型" width="86">
            <template #default="{ row }">
              <el-tag :type="row.answerable ? 'success' : 'info'" size="small">
                {{ row.answerable ? '可答' : '不可答' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="首个命中" width="100" align="right">
            <template #default="{ row }">
              <span :class="{ 'miss-text': row.answerable && !row.metrics.first_relevant_rank }">
                {{ rankLabel(row) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="Recall@5" width="100" align="right">
            <template #default="{ row }">
              {{ row.answerable ? fmt(row.metrics['recall@5']) : '—' }}
            </template>
          </el-table-column>
          <el-table-column label="拒答" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="row.refused ? 'warning' : 'success'" size="small">
                {{ row.refused ? '已拒答' : '正常' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="判定" width="80" align="center">
            <template #default="{ row }">
              <span :class="isRefusalCorrect(row) ? 'ok-text' : 'bad-text'">
                {{ isRefusalCorrect(row) ? '✓' : '✗' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column v-if="run.include_generation" label="生成" width="96" align="center">
            <template #default="{ row }">
              <el-tag :type="generationTag(row).type" size="small">
                {{ generationTag(row).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="90" align="right">
            <template #default="{ row }">{{ row.latency_ms }} ms</template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!filteredResults.length" description="当前筛选下没有用例" />
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.header-left {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.progress-card {
  margin-bottom: 14px;
  border-radius: 12px;
}

.progress-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 13px;
}

.progress-hint {
  margin-top: 8px;
  font-size: 12px;
}

.run-error {
  margin-bottom: 14px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.metric-card {
  border-radius: 12px;
}

.metric-label {
  color: #64748b;
  font-size: 12px;
}

.metric-value {
  margin-top: 6px;
  color: #4338ca;
  font-size: 22px;
  font-weight: 700;
}

.generation-card {
  margin-bottom: 14px;
  border-radius: 12px;
}

.generation-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.generation-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 14px;
}

.generation-metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
}

.generation-value {
  color: #b45309;
  font-size: 20px;
  font-weight: 700;
}

.generation-value small {
  color: #94a3b8;
  font-size: 11px;
  font-weight: 400;
}

.generation-note {
  margin-top: 12px;
  font-size: 12px;
  line-height: 1.6;
}

.answer-block {
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid #fde68a;
  border-radius: 8px;
  background: #fffbeb;
  font-size: 12px;
}

.answer-text {
  margin: 6px 0;
  color: #1f2937;
  line-height: 1.7;
  white-space: pre-wrap;
}

.answer-meta {
  color: #92400e;
}

.secondary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 14px;
  margin-bottom: 14px;
}

.info-card {
  border-radius: 12px;
}

.card-title {
  font-size: 13px;
  font-weight: 600;
}

.latency-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}

.latency-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
}

.latency-value {
  color: #0f172a;
  font-size: 17px;
  font-weight: 600;
}

.latency-value small {
  color: #94a3b8;
  font-size: 11px;
  font-weight: 400;
}

.latency-note,
.config-note {
  margin-top: 10px;
  font-size: 12px;
  line-height: 1.6;
}

.config-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.compare-row {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.results-card {
  border-radius: 12px;
}

.results-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.result-detail {
  padding: 4px 18px 12px;
}

.detail-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
  font-size: 12px;
}

.detail-label {
  color: #64748b;
}

.detail-error {
  margin-bottom: 8px;
  color: #dc2626;
  font-size: 12px;
}

.retrieved-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.retrieved-item {
  display: flex;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #f8fafc;
}

.retrieved-rank {
  flex: 0 0 30px;
  color: #6366f1;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  font-weight: 600;
}

.retrieved-main {
  min-width: 0;
  flex: 1;
}

.retrieved-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.retrieved-file {
  font-weight: 600;
}

.retrieved-content {
  display: -webkit-box;
  overflow: hidden;
  margin-top: 3px;
  color: #475569;
  font-size: 12px;
  line-height: 1.6;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
}

.miss-text {
  color: #dc2626;
  font-weight: 600;
}

.ok-text {
  color: #059669;
  font-weight: 700;
}

.bad-text {
  color: #dc2626;
  font-weight: 700;
}

.unsupported-block {
  margin-top: 10px;
  padding: 8px 10px;
  background: #fef2f2;
  border-left: 3px solid #dc2626;
  border-radius: 4px;
}

.unsupported-list {
  margin: 4px 0 0;
  padding-left: 18px;
  color: #7f1d1d;
  font-size: 12px;
  line-height: 1.7;
}
</style>
