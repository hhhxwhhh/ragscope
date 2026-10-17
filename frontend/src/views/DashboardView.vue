<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import {
  Collection,
  DataAnalysis,
  Document,
  Histogram,
  Link,
  Star,
} from '@element-plus/icons-vue'

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

import { getSummary, getTrend } from '@/api/dashboard'
import type { DashboardSummary, EvalRunStatus, RecentRun, TrendPoint } from '@/types'

const router = useRouter()

const summary = ref<DashboardSummary | null>(null)
const trend = ref<TrendPoint[]>([])
const days = ref(14)
const loading = ref(true)
const trendLoading = ref(false)
const chartRef = ref<HTMLDivElement | null>(null)

let chart: echarts.ECharts | undefined

const statusLabels: Record<EvalRunStatus, string> = {
  pending: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const kpis = computed(() => {
  const data = summary.value
  return [
    {
      label: '知识库',
      value: data?.kb_count ?? 0,
      hint: '每个库独立切块与索引版本',
      icon: Collection,
      color: '#6366f1',
    },
    {
      label: '已入库文档',
      value: `${data?.indexed_document_count ?? 0}/${data?.document_count ?? 0}`,
      hint: `${data?.chunk_count ?? 0} 个 chunk 可检索`,
      icon: Document,
      color: '#0891b2',
    },
    {
      label: '对话消息',
      value: data?.message_count ?? 0,
      hint: `${data?.conversation_count ?? 0} 个会话`,
      icon: DataAnalysis,
      color: '#059669',
    },
    {
      label: '评测覆盖',
      value: `${data?.eval_case_count ?? 0}/${data?.eval_run_count ?? 0}`,
      hint: '用例数 / 运行次数',
      icon: Histogram,
      color: '#d97706',
    },
    {
      label: '用户满意度',
      value: formatPct(data?.feedback_positive_rate ?? null),
      hint: `${data?.feedback_positive ?? 0} 赞 / ${data?.feedback_negative ?? 0} 踩`,
      icon: Star,
      color: '#db2777',
    },
    {
      label: '引用有效率',
      value: formatPct(data?.avg_citation_rate ?? null),
      hint: `${data?.invalid_citation_markers ?? 0} 个无效引用标记`,
      icon: Link,
      color: '#7c3aed',
    },
  ]
})

const avgLatency = computed(() => {
  const ms = summary.value?.avg_latency_ms || 0
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms.toFixed(0)} ms`
})

const activeRuns = computed(() =>
  (summary.value?.recent_runs || []).filter(
    (run) => run.status === 'running' || run.status === 'pending',
  ),
)

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function formatPct(value: number | null): string {
  return value === null ? '—' : `${(value * 100).toFixed(1)}%`
}

function statusType(status: EvalRunStatus): 'info' | 'success' | 'warning' | 'danger' {
  return status === 'completed'
    ? 'success'
    : status === 'failed'
      ? 'danger'
      : status === 'pending'
        ? 'info'
        : 'warning'
}

function shortDate(value: string): string {
  return value.slice(5)
}

function renderChart(): void {
  if (!chartRef.value) {
    return
  }
  if (!chart) {
    chart = echarts.init(chartRef.value)
  }
  chart.setOption(
    {
      tooltip: { trigger: 'axis' },
      legend: { data: ['消息', '会话', '评测运行'], top: 0, right: 0 },
      grid: { left: 36, right: 18, top: 38, bottom: 26 },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: trend.value.map((point) => shortDate(point.date)),
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisLabel: { color: '#94a3b8', fontSize: 11 },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        splitLine: { lineStyle: { color: '#f1f5f9' } },
        axisLabel: { color: '#94a3b8', fontSize: 11 },
      },
      series: [
        {
          name: '消息',
          type: 'line',
          smooth: true,
          symbolSize: 5,
          data: trend.value.map((point) => point.messages),
          itemStyle: { color: '#6366f1' },
          areaStyle: { color: 'rgba(99, 102, 241, 0.12)' },
        },
        {
          name: '会话',
          type: 'line',
          smooth: true,
          symbolSize: 5,
          data: trend.value.map((point) => point.conversations),
          itemStyle: { color: '#22d3ee' },
        },
        {
          name: '评测运行',
          type: 'line',
          smooth: true,
          symbolSize: 5,
          data: trend.value.map((point) => point.eval_runs),
          itemStyle: { color: '#f59e0b' },
        },
      ],
    },
    true,
  )
}

async function loadTrend(): Promise<void> {
  trendLoading.value = true
  try {
    const data = await getTrend(days.value)
    trend.value = data.points
    await nextTick()
    renderChart()
  } finally {
    trendLoading.value = false
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const [summaryData] = await Promise.all([getSummary(), loadTrend()])
    summary.value = summaryData
  } finally {
    loading.value = false
  }
}

function openRun(run: RecentRun): void {
  router.push(`/eval/runs/${run.id}`)
}

function handleResize(): void {
  chart?.resize()
}

onMounted(() => {
  load().catch(() => undefined)
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = undefined
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">概览</h2>
        <p class="page-subtitle">知识库、问答与评测活动的实时摘要</p>
      </div>
      <div class="header-actions">
        <span v-if="activeRuns.length" class="muted">
          {{ activeRuns.length }} 个评测运行进行中
        </span>
        <el-button type="primary" @click="router.push('/kbs')">进入知识库</el-button>
      </div>
    </div>

    <el-skeleton v-if="loading && !summary" :rows="5" animated />

    <template v-else>
      <div class="kpi-grid">
        <el-card v-for="kpi in kpis" :key="kpi.label" shadow="never" class="kpi-card">
          <div class="kpi-top">
            <span class="kpi-label">{{ kpi.label }}</span>
            <el-icon :style="{ color: kpi.color }"><component :is="kpi.icon" /></el-icon>
          </div>
          <div class="kpi-value">{{ kpi.value }}</div>
          <div class="muted kpi-hint">{{ kpi.hint }}</div>
        </el-card>
        <el-card shadow="never" class="kpi-card">
          <div class="kpi-top">
            <span class="kpi-label">平均回答耗时</span>
          </div>
          <div class="kpi-value">{{ avgLatency }}</div>
          <div class="muted kpi-hint">包含检索与生成，来自已完成的回答</div>
        </el-card>
      </div>

      <div class="main-grid">
        <el-card shadow="never" class="chart-card">
          <template #header>
            <div class="card-header">
              <span class="card-title">活动趋势</span>
              <el-radio-group v-model="days" size="small" @change="loadTrend">
                <el-radio-button :value="7">7 天</el-radio-button>
                <el-radio-button :value="14">14 天</el-radio-button>
                <el-radio-button :value="30">30 天</el-radio-button>
              </el-radio-group>
            </div>
          </template>
          <div v-loading="trendLoading" class="chart-wrap">
            <div ref="chartRef" class="chart" />
            <el-empty
              v-if="!trendLoading && !trend.some((point) => point.messages || point.conversations || point.eval_runs)"
              description="这段时间还没有活动"
            />
          </div>
        </el-card>

        <el-card shadow="never" class="recent-card">
          <template #header>
            <div class="card-header">
              <span class="card-title">最近评测</span>
              <el-button text type="primary" @click="router.push('/kbs')">去评测</el-button>
            </div>
          </template>
          <el-empty
            v-if="!summary?.recent_runs.length"
            :image-size="70"
            description="还没有评测运行"
          />
          <div v-else class="recent-list">
            <button
              v-for="run in summary.recent_runs"
              :key="run.id"
              class="recent-item"
              @click="openRun(run)"
            >
              <div class="recent-main">
                <span class="recent-name">{{ run.dataset_name }}</span>
                <span class="muted recent-meta">
                  {{ run.cases_total }} 用例 · {{ formatTime(run.created_at) }}
                </span>
              </div>
              <div class="recent-metrics">
                <el-tag :type="statusType(run.status)" size="small">
                  {{ statusLabels[run.status] }}
                </el-tag>
                <span class="recent-value">R@5 {{ formatPct(run.recall_5) }}</span>
                <span class="recent-value">MRR {{ formatPct(run.mrr) }}</span>
              </div>
            </button>
          </div>
        </el-card>
      </div>

      <el-card shadow="never" class="workflow-card">
        <template #header><span class="card-title">推荐流程</span></template>
        <div class="workflow">
          <div class="workflow-step">
            <span class="step-index">1</span>
            <div>
              <div class="step-title">建库并入库文档</div>
              <div class="muted">上传 .md / .txt，查看切块结果</div>
            </div>
          </div>
          <div class="workflow-step">
            <span class="step-index">2</span>
            <div>
              <div class="step-title">检索调试</div>
              <div class="muted">对比向量、BM25、RRF 与重排候选</div>
            </div>
          </div>
          <div class="workflow-step">
            <span class="step-index">3</span>
            <div>
              <div class="step-title">标注评测集</div>
              <div class="muted">把问题与相关 chunk 固化为回归用例</div>
            </div>
          </div>
          <div class="workflow-step">
            <span class="step-index">4</span>
            <div>
              <div class="step-title">跑配置并对比</div>
              <div class="muted">用 Recall@k / MRR / 拒答率做决策</div>
            </div>
          </div>
        </div>
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.kpi-card {
  border-radius: 12px;
}

.kpi-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.kpi-label {
  color: #64748b;
  font-size: 12px;
}

.kpi-value {
  margin: 6px 0 2px;
  color: #0f172a;
  font-size: 26px;
  font-weight: 700;
}

.kpi-hint {
  font-size: 11px;
}

.main-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(300px, 1fr);
  gap: 14px;
  margin-bottom: 14px;
}

@media (max-width: 1100px) {
  .main-grid {
    grid-template-columns: 1fr;
  }
}

.chart-card,
.recent-card,
.workflow-card {
  border-radius: 12px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.card-title {
  font-size: 13px;
  font-weight: 600;
}

.chart-wrap {
  min-height: 240px;
}

.chart {
  width: 100%;
  height: 240px;
}

.recent-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.recent-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.recent-item:hover {
  border-color: #e5e7eb;
  background: #f8fafc;
}

.recent-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.recent-name {
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recent-meta {
  font-size: 11px;
}

.recent-metrics {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
}

.recent-value {
  color: #475569;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
}

.workflow {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 12px;
}

.workflow-step {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 10px 12px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  background: #f8fafc;
}

.step-index {
  display: grid;
  width: 22px;
  height: 22px;
  flex: 0 0 22px;
  place-items: center;
  border-radius: 999px;
  background: #eef2ff;
  color: #4338ca;
  font-size: 11px;
  font-weight: 700;
}

.step-title {
  font-size: 13px;
  font-weight: 600;
}
</style>
