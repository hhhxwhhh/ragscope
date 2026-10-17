<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { listKbs } from '@/api/kb'
import type { KnowledgeBase } from '@/types'

const router = useRouter()
const kbs = ref<KnowledgeBase[]>([])
const total = ref(0)
const loading = ref(false)

const latestUpdate = computed(() => {
  if (!kbs.value.length) {
    return '—'
  }
  const newest = kbs.value
    .map((kb) => kb.updated_at)
    .sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0]
  return formatTime(newest)
})

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const page = await listKbs(0, 5)
    kbs.value = page.items
    total.value = page.total
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">概览</h2>
        <p class="page-subtitle">Week 1 已打通：登录 → 建库 → 上传 → 切块入库</p>
      </div>
      <el-button type="primary" @click="router.push('/kbs')">进入知识库</el-button>
    </div>

    <div class="stat-grid">
      <el-card class="stat-card" shadow="never">
        <div class="stat-label">知识库总数</div>
        <div class="stat-value">{{ total }}</div>
        <div class="muted">每个知识库拥有独立切块参数</div>
      </el-card>
      <el-card class="stat-card" shadow="never">
        <div class="stat-label">最近更新时间</div>
        <div class="stat-value small">{{ latestUpdate }}</div>
        <div class="muted">文档入库后自动递增 index_version</div>
      </el-card>
      <el-card class="stat-card" shadow="never">
        <div class="stat-label">检索与评测</div>
        <div class="stat-value small">Week 2–3</div>
        <div class="muted">BM25 / 向量 / RRF / rerank / 评测台</div>
      </el-card>
    </div>

    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>最近的知识库</span>
          <el-button text type="primary" @click="router.push('/kbs')">查看全部</el-button>
        </div>
      </template>

      <el-skeleton v-if="loading" :rows="3" animated />
      <el-empty v-else-if="!kbs.length" description="还没有知识库，先创建一个">
        <el-button type="primary" @click="router.push('/kbs')">去创建</el-button>
      </el-empty>
      <el-table v-else :data="kbs" @row-click="(row: KnowledgeBase) => router.push(`/kbs/${row.id}`)">
        <el-table-column prop="name" label="名称" min-width="160" />
        <el-table-column prop="description" label="描述" min-width="220">
          <template #default="{ row }">{{ row.description || '—' }}</template>
        </el-table-column>
        <el-table-column label="切块" width="130">
          <template #default="{ row }">{{ row.chunk_size }} / {{ row.chunk_overlap }}</template>
        </el-table-column>
        <el-table-column prop="index_version" label="索引版本" width="100" />
        <el-table-column label="更新时间" width="180">
          <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.stat-value.small {
  font-size: 18px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

:deep(.el-table__row) {
  cursor: pointer;
}
</style>
