<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'

import { getOpsSummary, METRICS_URL } from '@/api/ops'
import { getUsageSummary } from '@/api/usage'
import type { OpsSnapshot, UsageSummary } from '@/types'

const snapshot = ref<OpsSnapshot | null>(null)
const usage = ref<UsageSummary | null>(null)
const loading = ref(false)
const autoRefresh = ref(true)
let timer: number | undefined

const p95 = computed(() => {
  const value = snapshot.value?.http.p95_seconds
  return value === null || value === undefined ? '—' : `${Math.round(value * 1000)} ms`
})

const errorRate = computed(() => {
  const rate = snapshot.value?.http.error_rate
  return rate === undefined ? '—' : `${(rate * 100).toFixed(2)}%`
})

const maxDailyTokens = computed(() =>
  Math.max(1, ...(usage.value?.daily || []).map((point) => point.tokens)),
)

const refusalRate = computed(() => {
  const stats = snapshot.value?.retrieval
  if (!stats || !stats.total) return '—'
  return `${((stats.refusals / stats.total) * 100).toFixed(1)}%`
})

async function load(): Promise<void> {
  loading.value = true
  try {
    const [ops, usageData] = await Promise.all([getOpsSummary(), getUsageSummary(14)])
    snapshot.value = ops
    usage.value = usageData
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载运行指标失败')
  } finally {
    loading.value = false
  }
}

function tick(): void {
  if (autoRefresh.value) {
    void load()
  }
}

onMounted(() => {
  void load()
  timer = window.setInterval(tick, 10_000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2>运行观测</h2>
        <p class="muted">
          数据来自进程内 Prometheus 注册表（<code>/api/v1/ops/summary</code>）。多副本部署时每个实例只报自己的计数，
          聚合与告警请看 Prometheus / Grafana。
        </p>
      </div>
      <div class="actions">
        <el-switch v-model="autoRefresh" active-text="10s 自动刷新" />
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <el-link :href="METRICS_URL" target="_blank" type="primary" class="metrics-link">
          抓取端点 /metrics
        </el-link>
      </div>
    </div>

    <el-row :gutter="16" class="cards">
      <el-col :span="6">
        <el-card shadow="never">
          <div class="stat-label">请求总数</div>
          <div class="stat-value">{{ snapshot?.http.requests_total ?? '—' }}</div>
          <div class="stat-foot">5xx {{ snapshot?.http.errors_total ?? 0 }} · 错误率 {{ errorRate }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never">
          <div class="stat-label">P95 延迟</div>
          <div class="stat-value">{{ p95 }}</div>
          <div class="stat-foot">
            P50 {{ snapshot?.http.p50_seconds ? Math.round(snapshot.http.p50_seconds * 1000) : '—' }} ms
            · P99 {{ snapshot?.http.p99_seconds ? Math.round(snapshot.http.p99_seconds * 1000) : '—' }} ms
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never">
          <div class="stat-label">检索 / 拒答</div>
          <div class="stat-value">{{ snapshot?.retrieval.total ?? '—' }}</div>
          <div class="stat-foot">拒答率 {{ refusalRate }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never">
          <div class="stat-label">LLM token</div>
          <div class="stat-value">{{ snapshot?.llm.tokens_total ?? '—' }}</div>
          <div class="stat-foot">Agent 运行 {{ snapshot?.agent.runs_total ?? 0 }} 次</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>今日 token 预算</template>
          <div class="budget-row">
            <span class="stat-value small">{{ usage?.today.used ?? 0 }}</span>
            <span class="muted">
              / {{ usage?.today.budget ? usage.today.budget : '不限' }}
            </span>
            <el-tag v-if="usage?.today.exceeded" type="danger" size="small">已用尽</el-tag>
          </div>
          <el-progress
            v-if="usage?.today.budget"
            :percentage="Math.min(100, Math.round(((usage?.today.used ?? 0) / (usage?.today.budget || 1)) * 100))"
            :status="usage?.today.exceeded ? 'exception' : undefined"
            :stroke-width="8"
          />
          <div class="spark">
            <div
              v-for="point in usage?.daily || []"
              :key="point.date"
              class="spark-bar"
              :style="{ height: `${Math.max(4, Math.round((point.tokens / maxDailyTokens) * 100))}%` }"
              :title="`${point.date} · ${point.tokens} tokens`"
            />
          </div>
          <div class="muted small">近 14 天 token 用量（悬停看数值）</div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>后台任务</template>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="执行后端">
              <el-tag :type="snapshot?.tasks.backend === 'celery' ? 'success' : 'info'" size="small">
                {{ snapshot?.tasks.backend || '—' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="队列名">
              {{ snapshot?.tasks.queue || '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="积压">
              {{
                snapshot?.tasks.queue_depth === null
                  ? '未启用 Celery'
                  : `${snapshot?.tasks.queue_depth} 条`
              }}
            </el-descriptions-item>
            <el-descriptions-item label="任务类型">
              {{ snapshot?.tasks.inline_tasks.join(' · ') || '—' }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="cards">
      <el-col :span="24">
        <el-card shadow="never">
          <template #header>流水线</template>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="入库">{{ snapshot?.ingest.total ?? 0 }} 次</el-descriptions-item>
            <el-descriptions-item label="图谱操作">{{ snapshot?.graphrag.total ?? 0 }} 次</el-descriptions-item>
            <el-descriptions-item label="评测运行">{{ snapshot?.eval.runs_total ?? 0 }} 次</el-descriptions-item>
            <el-descriptions-item label="日志格式">JSON（request_id / trace_id 自动注入）</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="slo">
      <template #header>这套指标能回答什么</template>
      <ul class="slo-list">
        <li><strong>5xx 率</strong>：网关重试前先看这里，判断是上游还是本服务。</li>
        <li><strong>P95/P99</strong>：SSE 请求的耗时包含整个流，所以它是「用户感知」延迟。</li>
        <li><strong>拒答率</strong>：突然升高通常意味着语料或阈值被改动，而不是模型变差。</li>
        <li><strong>队列积压</strong>：worker 挂了或并发不足时，上传会在这一步堆积。</li>
        <li><strong>追踪</strong>：响应头 <code>X-Trace-ID</code> 可直接贴进 Jaeger 查完整链路。</li>
      </ul>
    </el-card>
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
  max-width: 760px;
  line-height: 1.7;
}

.actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.metrics-link {
  font-size: 13px;
}

.cards {
  margin-bottom: 16px;
}

.stat-label {
  color: #64748b;
  font-size: 13px;
}

.stat-value {
  font-size: 26px;
  font-weight: 600;
  color: #0f172a;
  margin: 4px 0;
}

.stat-foot {
  color: #94a3b8;
  font-size: 12px;
}

.budget-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
}

.spark {
  display: flex;
  align-items: flex-end;
  gap: 4px;
  height: 72px;
  margin: 14px 0 6px;
}

.spark-bar {
  flex: 1;
  min-height: 4px;
  border-radius: 3px 3px 0 0;
  background: linear-gradient(180deg, #60a5fa, #2563eb);
}

.slo {
  margin-top: 16px;
}

.slo-list {
  margin: 0;
  padding-left: 18px;
  line-height: 2;
  color: #334155;
}
</style>
