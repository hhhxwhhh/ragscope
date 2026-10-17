<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'

import { createKb, deleteKb, listKbs } from '@/api/kb'
import type { KnowledgeBase } from '@/types'

const router = useRouter()

const kbs = ref<KnowledgeBase[]>([])
const total = ref(0)
const page = ref(1)
const limit = 10
const loading = ref(false)

const dialogVisible = ref(false)
const creating = ref(false)
const formRef = ref<FormInstance>()
const form = reactive({
  name: '',
  description: '',
  chunk_size: 400,
  chunk_overlap: 60,
})

const rules: FormRules = {
  name: [{ required: true, message: '请输入知识库名称', trigger: 'blur' }],
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await listKbs((page.value - 1) * limit, limit)
    kbs.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  form.name = ''
  form.description = ''
  form.chunk_size = 400
  form.chunk_overlap = 60
  dialogVisible.value = true
}

async function submitCreate(): Promise<void> {
  if (!formRef.value) {
    return
  }
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) {
    return
  }
  if (form.chunk_overlap >= form.chunk_size) {
    ElMessage.error('overlap 必须小于 chunk_size')
    return
  }

  creating.value = true
  try {
    const kb = await createKb({
      name: form.name,
      description: form.description || null,
      chunk_size: form.chunk_size,
      chunk_overlap: form.chunk_overlap,
    })
    dialogVisible.value = false
    ElMessage.success('知识库已创建')
    await router.push(`/kbs/${kb.id}`)
  } catch {
    // Error toast is handled by the axios interceptor.
  } finally {
    creating.value = false
  }
}

async function removeKb(kb: KnowledgeBase): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除「${kb.name}」会同时删除其全部文档与切块，确定继续？`,
      '删除知识库',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  try {
    await deleteKb(kb.id)
    ElMessage.success('已删除')
    if (kbs.value.length === 1 && page.value > 1) {
      page.value -= 1
    }
    await load()
  } catch {
    // Error toast is handled by the axios interceptor.
  }
}

function changePage(next: number): void {
  page.value = next
  load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">知识库</h2>
        <p class="page-subtitle">每个知识库拥有独立的切块参数与索引版本</p>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建知识库</el-button>
    </div>

    <el-card shadow="never">
      <el-table
        v-loading="loading"
        :data="kbs"
        @row-click="(row: KnowledgeBase) => router.push(`/kbs/${row.id}`)"
      >
        <el-table-column prop="name" label="名称" min-width="180" />
        <el-table-column prop="description" label="描述" min-width="240">
          <template #default="{ row }">{{ row.description || '—' }}</template>
        </el-table-column>
        <el-table-column label="切块参数" width="140">
          <template #default="{ row }">{{ row.chunk_size }} / {{ row.chunk_overlap }}</template>
        </el-table-column>
        <el-table-column prop="index_version" label="索引版本" width="100" />
        <el-table-column label="更新时间" width="180">
          <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="110" align="right">
          <template #default="{ row }">
            <el-button text type="danger" @click.stop="removeKb(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && !kbs.length" description="还没有知识库">
        <el-button type="primary" @click="openCreate">创建第一个知识库</el-button>
      </el-empty>

      <div v-if="total > limit" class="pagination">
        <el-pagination
          layout="prev, pager, next"
          :total="total"
          :page-size="limit"
          :current-page="page"
          @current-change="changePage"
        />
      </div>
    </el-card>

    <el-dialog v-model="dialogVisible" title="新建知识库" width="480px">
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" maxlength="100" show-word-limit placeholder="例如：产品手册库" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="2"
            maxlength="500"
            placeholder="这个知识库放什么内容"
          />
        </el-form-item>
        <el-form-item label="切块大小（字符）">
          <el-input-number v-model="form.chunk_size" :min="100" :max="2000" :step="50" />
        </el-form-item>
        <el-form-item label="重叠（字符）">
          <el-input-number v-model="form.chunk_overlap" :min="0" :max="500" :step="10" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

:deep(.el-table__row) {
  cursor: pointer;
}
</style>
