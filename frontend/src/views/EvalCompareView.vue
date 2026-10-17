<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Sort } from '@element-plus/icons-vue'

import { compareRuns } from '@/api/eval'
import type { CompareCase, EvalCompare, MetricDelta } from '@/types'

const route = useRoute()
const router = useRouter()

const compare = ref<EvalCompare | null>(null)
const loading = ref(true)
const failed = ref(false)
const outcomeFilter = ref<'all' | 'better' | 'worse' | 'same'>('all')

const baseId = computed(() => String(route.query.base || ''))
const candidateId = computed(() => String(route.query.candidate || ''))

const lowerIsBetter = (key: string): boolean =>
  key.includes('latency') ||
  key === 'answerable_refusal_rate' ||
  key === 'elapsed_seconds'

const caseCounts = computed(() => {
  if (!compare.value) {
    return { better: 0, worse: 0, same: 0 }
  }
  return {
    better: compare.value.improved,
    worse: compare.value.degraded,
    same: compare.value.unchanged,
  }
})

const filteredCases = computed<CompareCase[]>(() => {
  const cases = compare.value?.cases || []
  if (outcomeFilter.value === 'all') {
    return cases
  }
  return cases.filter((item) => item.outcome === outcomeFilter.value)
})

function fmt(value: number | null | undefined, isRate = true): string {
  if (value === null || value === undefined) {
    return '—'
  }
  return isRate ? `${(value * 100).toFixed(1)}%` : value.toFixed(1)
}

function deltaText(item: MetricDelta): string {
  if (item.delta === null || item.delta === undefined) {
    return '—'
  }
  const sign = item.delta > 0 ? '+' : ''
  if (item.key.endsWith('_ms')) {
    return `${sign}${item.delta.toFixed(1)} ms`
  }
  return `${sign}${(item.delta * 100).toFixed(1)}pt`
}

function deltaClass(item: MetricDelta): string {
  if (item.delta === null || item.delta === undefined || Math.abs(item.delta) < 1e-9) {
    return 'delta-neutral'
  }
  const good = lowerIsBetter(item.key) ? item.delta < 0 : item.delta > 0
  return good ? 'delta-good' : 'delta-bad'
}

function deltaIcon(item: MetricDelta): string {
  if (item.delta === null || item.delta === undefined || Math.abs(item.delta) < 1e-9) {
    return '='
  }
  return item.delta > 0 ? '↑' : '↓'
}

function formatTime(value: string | null): string {
  return value
    ? new Date(value).toLocaleString('zh-CN', { hour12: false })
    : '—'
}

function fmtNum(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : String(value)
}

async function load(): Promise<void> {
  if (!baseId.value || !candidateId.value) {
    failed.value = true
    loading.value = false
    return
  }
  loading.value = true
  failed.value = false
  try {
    compare.value = await compareRuns(baseId.value, candidateId.value)
  } catch {
    failed.value = true
    compare.value = null
  } finally {
    loading.value = false
  }
}

function swap(): void {
  router.replace({
    path: '/eval/compare',
    query: { base: candidateId.value, candidate: baseId.value },
  })
}

function backToKb(): void {
  router.push({ path: `/kbs/${compare.value?.base.kb_id || ''}`, query: { tab: 'eval' } })
}

function openRun(runId: string): void {
  router.push(`/eval/runs/${runId}`)
}

watch([baseId, candidateId], () => load(), { immediate: true })
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div class="header-left">
        <el-button text :icon="ArrowLeft" @click="backToKb">返回知识库</el-button>
        <div>
          <h2 class="page-title">运行对比</h2>
          <p class="page-subtitle">
            同数据集、同索引版本；差异小于 5 个点在小样本上视为噪声
          </p>
        </div>
      </div>
      <el-button :icon="Sort" :disabled="!compare" @click="swap">交换方向</el-button>
    </div>

    <el-skeleton v-if="loading" :rows="8" animated />

    <el-empty
      v-else-if="failed || !compare"
      description="无法对比：请确认两次运行属于同一数据集、同一索引版本，且均已完成"
    />

    <template v-else>
      <div class="run-summary">
        <button class="run-card base" @click="openRun(compare.base.id)">
          <span class="run-role">基线</span>
          <span class="run-time">{{ formatTime(compare.base.created_at) }}</span>
          <span class="muted">
            Recall@5 {{ fmt(compare.base.metrics['recall@5']) }} · MRR
            {{ fmt(compare.base.metrics.mrr) }}
          </span>
        </button>
        <div class="arrow">→</div>
        <button class="run-card candidate" @click="openRun(compare.candidate.id)">
          <span class="run-role">候选</span>
          <span class="run-time">{{ formatTime(compare.candidate.created_at) }}</span>
          <span class="muted">
            Recall@5 {{ fmt(compare.candidate.metrics['recall@5']) }} · MRR
            {{ fmt(compare.candidate.metrics.mrr) }}
          </span>
        </button>
      </div>

      <div class="outcome-grid">
        <el-card shadow="never" class="outcome-card better">
          <div class="outcome-value">{{ caseCounts.better }}</div>
          <div class="muted">变好</div>
        </el-card>
        <el-card shadow="never" class="outcome-card worse">
          <div class="outcome-value">{{ caseCounts.worse }}</div>
          <div class="muted">变差</div>
        </el-card>
        <el-card shadow="never" class="outcome-card same">
          <div class="outcome-value">{{ caseCounts.same }}</div>
          <div class="muted">不变</div>
        </el-card>
        <el-card shadow="never" class="outcome-card">
          <div class="outcome-value">{{ compare.cases.length }}</div>
          <div class="muted">用例总数</div>
        </el-card>
      </div>

      <el-card shadow="never" class="metric-delta-card">
        <template #header><span class="card-title">指标变化</span></template>
        <div class="delta-grid">
          <div v-for="item in compare.metric_deltas" :key="item.key" class="delta-item">
            <span class="delta-label muted">{{ item.label }}</span>
            <div class="delta-values">
              <span>{{ fmt(item.base, !item.key.includes('latency')) }}</span>
              <span class="delta-arrow">→</span>
              <span class="delta-candidate">
                {{ fmt(item.candidate, !item.key.includes('latency')) }}
              </span>
            </div>
            <span class="delta-change" :class="deltaClass(item)">
              {{ deltaIcon(item) }} {{ deltaText(item) }}
            </span>
          </div>
        </div>
        <div class="muted delta-note">
          延迟类指标越低越好；其余指标越高越好。Recall / MRR 的单位为百分点（pt），延迟为毫秒差值。
        </div>
      </el-card>

      <el-card shadow="never" class="cases-card">
        <template #header>
          <div class="cases-head">
            <span class="card-title">逐条对比 ({{ filteredCases.length }})</span>
            <el-radio-group v-model="outcomeFilter" size="small">
              <el-radio-button value="all">全部</el-radio-button>
              <el-radio-button value="worse">只看变差</el-radio-button>
              <el-radio-button value="better">只看变好</el-radio-button>
              <el-radio-button value="same">不变</el-radio-button>
            </el-radio-group>
          </div>
        </template>

        <el-table :data="filteredCases" size="small">
          <el-table-column prop="question" label="问题" min-width="300" />
          <el-table-column label="类型" width="86">
            <template #default="{ row }">
              <el-tag :type="row.answerable ? 'success' : 'info'" size="small">
                {{ row.answerable ? '可答' : '不可答' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="基线排名" width="100" align="right">
            <template #default="{ row }">{{ fmtNum(row.base_rank) }}</template>
          </el-table-column>
          <el-table-column label="候选排名" width="100" align="right">
            <template #default="{ row }">{{ fmtNum(row.candidate_rank) }}</template>
          </el-table-column>
          <el-table-column label="基线 Recall@5" width="120" align="right">
            <template #default="{ row }">
              {{ row.answerable ? fmt(row.base_metrics['recall@5']) : '—' }}
            </template>
          </el-table-column>
          <el-table-column label="候选 Recall@5" width="120" align="right">
            <template #default="{ row }">
              {{ row.answerable ? fmt(row.candidate_metrics['recall@5']) : '—' }}
            </template>
          </el-table-column>
          <el-table-column label="拒答" width="150">
            <template #default="{ row }">
              <span class="refusal-pair">
                {{ row.base_refused ? '拒' : '答' }}
                <span class="muted">→</span>
                {{ row.candidate_refused ? '拒' : '答' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="结果" width="90" align="center">
            <template #default="{ row }">
              <el-tag
                :type="row.outcome === 'better' ? 'success' : row.outcome === 'worse' ? 'danger' : 'info'"
                size="small"
              >
                {{ row.outcome === 'better' ? '变好' : row.outcome === 'worse' ? '变差' : '不变' }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!filteredCases.length" description="当前筛选下没有用例" />
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

.run-summary {
  display: flex;
  align-items: stretch;
  gap: 12px;
  margin-bottom: 14px;
}

.run-card {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
  padding: 12px 16px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #fff;
  text-align: left;
  cursor: pointer;
}

.run-card.base {
  border-left: 3px solid #94a3b8;
}

.run-card.candidate {
  border-left: 3px solid #6366f1;
}

.run-role {
  font-size: 12px;
  font-weight: 600;
  color: #4338ca;
}

.run-time {
  font-size: 14px;
  font-weight: 600;
}

.arrow {
  display: flex;
  align-items: center;
  color: #94a3b8;
  font-size: 18px;
}

.outcome-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 14px;
}

.outcome-card {
  border-radius: 12px;
}

.outcome-value {
  font-size: 24px;
  font-weight: 700;
  color: #0f172a;
}

.outcome-card.better .outcome-value {
  color: #059669;
}

.outcome-card.worse .outcome-value {
  color: #dc2626;
}

.metric-delta-card,
.cases-card {
  margin-bottom: 14px;
  border-radius: 12px;
}

.card-title {
  font-size: 13px;
  font-weight: 600;
}

.delta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}

.delta-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  background: #f8fafc;
}

.delta-label {
  font-size: 12px;
}

.delta-values {
  font-size: 16px;
  font-weight: 600;
}

.delta-arrow {
  margin: 0 6px;
  color: #94a3b8;
  font-weight: 400;
}

.delta-candidate {
  color: #4338ca;
}

.delta-change {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

.delta-good {
  color: #059669;
}

.delta-bad {
  color: #dc2626;
}

.delta-neutral {
  color: #94a3b8;
}

.delta-note {
  margin-top: 12px;
  font-size: 12px;
}

.cases-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.refusal-pair {
  font-size: 12px;
}
</style>
