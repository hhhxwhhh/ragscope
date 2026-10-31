<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Back, Delete, MagicStick, Refresh } from '@element-plus/icons-vue'
import * as echarts from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import { buildGraph, clearGraph, getGraph } from '@/api/graph'
import { getKb } from '@/api/kb'
import type { GraphPayload, KnowledgeBase } from '@/types'

echarts.use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{ id: string }>()
const router = useRouter()

const kb = ref<KnowledgeBase | null>(null)
const payload = ref<GraphPayload | null>(null)
const loading = ref(false)
const building = ref(false)
const chartRef = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | undefined
let poll: number | undefined

const TYPE_LABELS: Record<string, string> = {
  product: '产品/型号',
  feature: '参数/功能',
  policy: '政策/条款',
  organization: '组织',
  concept: '概念',
  person: '人物',
  other: '其他',
}

const categories = computed(() => {
  const types = new Set((payload.value?.nodes || []).map((node) => node.type))
  return Array.from(types).map((type) => ({ name: TYPE_LABELS[type] || type }))
})

const topEntities = computed(() =>
  [...(payload.value?.nodes || [])]
    .sort((a, b) => b.mention_count - a.mention_count)
    .slice(0, 12),
)

const isEmpty = computed(() => !payload.value?.nodes.length)

function renderChart(): void {
  const data = payload.value
  if (!chartRef.value) return
  if (!chart) {
    chart = echarts.init(chartRef.value)
  }
  if (!data || !data.nodes.length) {
    chart.clear()
    return
  }

  const index = new Map(data.nodes.map((node) => [node.id, node]))
  const typeIndex = new Map(categories.value.map((item, position) => [item.name, position]))

  chart.setOption(
    {
      tooltip: {
        formatter: (params: any) => {
          if (params.dataType === 'edge') {
            return `${params.data.sourceName} → ${params.data.targetName}<br/>${params.data.relation}`
          }
          const node = params.data
          return `<strong>${node.name}</strong><br/>${node.typeLabel}<br/>被提及 ${node.value} 次${
            node.description ? `<br/><span style="color:#94a3b8">${node.description}</span>` : ''
          }`
        },
      },
      legend: [{ bottom: 0, data: categories.value.map((item) => item.name) }],
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          label: { show: true, fontSize: 11, color: '#0f172a' },
          force: { repulsion: 220, edgeLength: [60, 140], gravity: 0.08 },
          lineStyle: { color: 'source', curveness: 0.12, opacity: 0.55 },
          edgeSymbol: ['none', 'arrow'],
          edgeSymbolSize: [0, 6],
          emphasis: { focus: 'adjacency', lineStyle: { width: 2 } },
          categories: categories.value,
          data: data.nodes.map((node) => ({
            id: node.id,
            name: node.name,
            value: node.mention_count,
            category: typeIndex.get(TYPE_LABELS[node.type] || TYPE_LABELS.other) ?? 0,
            typeLabel: TYPE_LABELS[node.type] || node.type,
            description: node.description,
            symbolSize: Math.min(46, 16 + node.mention_count * 4),
          })),
          links: data.edges
            .filter((edge) => index.has(edge.source) && index.has(edge.target))
            .map((edge) => ({
              source: edge.source,
              target: edge.target,
              relation: edge.relation,
              sourceName: index.get(edge.source)?.name,
              targetName: index.get(edge.target)?.name,
              lineStyle: { width: Math.min(4, 1 + edge.weight * 0.4) },
            })),
        },
      ],
    },
    true,
  )
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const [kbData, graphData] = await Promise.all([getKb(props.id), getGraph(props.id)])
    kb.value = kbData
    payload.value = graphData
    building.value = Boolean(graphData.building)
    await nextTick()
    renderChart()
    if (building.value) {
      startPolling()
    } else {
      stopPolling()
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载图谱失败')
  } finally {
    loading.value = false
  }
}

function startPolling(): void {
  if (poll) return
  poll = window.setInterval(async () => {
    try {
      const graphData = await getGraph(props.id)
      payload.value = graphData
      building.value = Boolean(graphData.building)
      await nextTick()
      renderChart()
      if (!building.value) {
        stopPolling()
        ElMessage.success('图谱构建完成')
      }
    } catch {
      stopPolling()
    }
  }, 4000)
}

function stopPolling(): void {
  if (poll) {
    window.clearInterval(poll)
    poll = undefined
  }
}

async function handleBuild(): Promise<void> {
  await ElMessageBox.confirm(
    '构建会对每个 chunk 调用一次大模型，产生 token 成本。已存在的图谱会增量更新，不会重复建边。',
    '构建知识图谱',
    { type: 'warning', confirmButtonText: '开始构建', cancelButtonText: '取消' },
  )
  try {
    const result = await buildGraph(props.id)
    if (result.status === 'already_building') {
      ElMessage.info('已有构建任务在运行')
    } else {
      ElMessage.success('已进入构建队列')
    }
    building.value = true
    startPolling()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '构建失败')
  }
}

async function handleClear(): Promise<void> {
  await ElMessageBox.confirm('清空后检索会退回到纯向量 + BM25，图谱需要重新构建。', '清空图谱', {
    type: 'warning',
    confirmButtonText: '清空',
    cancelButtonText: '取消',
  })
  try {
    await clearGraph(props.id)
    ElMessage.success('已清空')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '清空失败')
  }
}

function handleResize(): void {
  chart?.resize()
}

onMounted(() => {
  void load()
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  stopPolling()
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = undefined
})
</script>

<template>
  <div class="page">
    <el-page-header @back="router.push(`/kbs/${props.id}`)">
      <template #content>
        <span class="title">知识图谱 · {{ kb?.name || '' }}</span>
      </template>
    </el-page-header>

    <div class="toolbar">
      <div class="stats">
        <el-tag size="small" type="info">实体 {{ payload?.stats.entities ?? 0 }}</el-tag>
        <el-tag size="small" type="info">关系 {{ payload?.stats.relations ?? 0 }}</el-tag>
        <el-tag size="small" type="info">提及 {{ payload?.stats.mentions ?? 0 }}</el-tag>
        <el-tag v-if="building" size="small" type="warning">构建中…</el-tag>
      </div>
      <div class="actions">
        <el-button :icon="Back" size="small" @click="router.push(`/kbs/${props.id}/chat`)">
          去问答
        </el-button>
        <el-button :icon="Refresh" size="small" :loading="loading" @click="load">刷新</el-button>
        <el-button :icon="Delete" size="small" :disabled="isEmpty || building" @click="handleClear">
          清空
        </el-button>
        <el-button
          type="primary"
          size="small"
          :icon="MagicStick"
          :loading="building"
          :disabled="building"
          @click="handleBuild"
        >
          构建 / 增量更新
        </el-button>
      </div>
    </div>

    <el-row :gutter="16">
      <el-col :span="18">
        <el-card shadow="never" class="chart-card" v-loading="loading">
          <div v-show="!isEmpty" ref="chartRef" class="chart"></div>
          <el-empty
            v-if="isEmpty && !loading"
            description="还没有图谱。点击「构建」抽取实体与关系后，检索会多一跳图扩展。"
          />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" header="高频实体">
          <div v-for="node in topEntities" :key="node.id" class="entity-row">
            <span class="entity-name">{{ node.name }}</span>
            <el-tag size="small" type="info">{{ TYPE_LABELS[node.type] || node.type }}</el-tag>
            <span class="muted small">×{{ node.mention_count }}</span>
          </div>
          <el-empty v-if="!topEntities.length" description="暂无实体" :image-size="60" />
        </el-card>

        <el-card shadow="never" header="这一页在验证什么" class="explain">
          <p class="muted small">
            图谱是<strong>离线构建、在线一跳</strong>的：查询时只做实体名匹配 + 邻居 chunk 拉取，
            不调用大模型。新增证据会记录 <code>graph_via</code>，在检索调试台可以看到它来自哪条边。
          </p>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.title {
  font-weight: 600;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin: 14px 0 16px;
  gap: 16px;
}

.stats {
  display: flex;
  gap: 8px;
}

.actions {
  display: flex;
  gap: 8px;
}

.chart-card {
  min-height: 560px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.chart {
  width: 100%;
  height: 540px;
}

.entity-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px dashed #e2e8f0;
}

.entity-name {
  flex: 1;
  font-size: 13px;
  color: #0f172a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.explain {
  margin-top: 16px;
  line-height: 1.7;
}

.small {
  font-size: 12px;
}
</style>
