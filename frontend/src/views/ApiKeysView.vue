<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, Key, Plus, Refresh } from '@element-plus/icons-vue'

import { createApiKey, listApiKeys, listKeyAudit, listScopes, revokeApiKey } from '@/api/keys'
import type { ApiKeyItem, AuditLogItem } from '@/types'

const keys = ref<ApiKeyItem[]>([])
const scopes = ref<Record<string, string>>({})
const loading = ref(false)
const creating = ref(false)
const dialogVisible = ref(false)
const createdKey = ref('')

const form = ref({
  name: '',
  scopes: ['kb:read', 'chat'] as string[],
  description: '',
  expires_in_days: 180 as number | null,
})

const auditVisible = ref(false)
const auditRows = ref<AuditLogItem[]>([])
const auditKeyName = ref('')

const scopeOptions = computed(() =>
  Object.entries(scopes.value).map(([value, description]) => ({ value, description })),
)

const activeCount = computed(() => keys.value.filter((item) => item.usable).length)

async function load(): Promise<void> {
  loading.value = true
  try {
    const [page, scopeMap] = await Promise.all([listApiKeys(), listScopes()])
    keys.value = page.items
    scopes.value = scopeMap
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载 API Key 失败')
  } finally {
    loading.value = false
  }
}

async function submit(): Promise<void> {
  if (!form.value.name.trim()) {
    ElMessage.warning('请填写名称')
    return
  }
  if (!form.value.scopes.length) {
    ElMessage.warning('至少选择一个权限')
    return
  }
  creating.value = true
  try {
    const created = await createApiKey({
      name: form.value.name.trim(),
      scopes: form.value.scopes,
      description: form.value.description || null,
      expires_in_days: form.value.expires_in_days || null,
    })
    createdKey.value = created.key || ''
    dialogVisible.value = false
    form.value = { name: '', scopes: ['kb:read', 'chat'], description: '', expires_in_days: 180 }
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '创建失败')
  } finally {
    creating.value = false
  }
}

async function revoke(key: ApiKeyItem): Promise<void> {
  await ElMessageBox.confirm(
    `吊销后使用 ${key.key_prefix}… 的客户端会立即失效，且无法恢复。`,
    '吊销 API Key',
    { type: 'warning', confirmButtonText: '吊销', cancelButtonText: '取消' },
  )
  try {
    await revokeApiKey(key.id)
    ElMessage.success('已吊销')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '吊销失败')
  }
}

async function showAudit(key: ApiKeyItem): Promise<void> {
  auditKeyName.value = key.name
  auditVisible.value = true
  try {
    const page = await listKeyAudit(key.id)
    auditRows.value = page.items
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '加载审计日志失败')
  }
}

async function copyKey(): Promise<void> {
  try {
    await navigator.clipboard.writeText(createdKey.value)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('浏览器拒绝访问剪贴板，请手动复制')
  }
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2>API Key</h2>
        <p class="muted">
          面向脚本、MCP 客户端和其他服务的机器凭据。密钥只在创建时显示一次，服务端只保存
          SHA-256 哈希；权限按 scope 收敛，越权调用会被依赖层直接拒绝。
        </p>
      </div>
      <div class="actions">
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="dialogVisible = true">创建密钥</el-button>
      </div>
    </div>

    <el-row :gutter="16" class="cards">
      <el-col :span="8">
        <el-card shadow="never">
          <div class="stat-label">密钥总数</div>
          <div class="stat-value">{{ keys.length }}</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <div class="stat-label">可用</div>
          <div class="stat-value">{{ activeCount }}</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <div class="stat-label">可用接口</div>
          <div class="stat-value small">/public/* · /mcp</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never">
      <el-table :data="keys" v-loading="loading" empty-text="还没有 API Key">
        <el-table-column label="名称" min-width="160">
          <template #default="{ row }">
            <div class="key-name">
              <el-icon><Key /></el-icon>
              <span>{{ row.name }}</span>
            </div>
            <div v-if="row.description" class="muted small">{{ row.description }}</div>
          </template>
        </el-table-column>
        <el-table-column label="密钥前缀" width="180">
          <template #default="{ row }">
            <code>{{ row.key_prefix }}…</code>
          </template>
        </el-table-column>
        <el-table-column label="权限" min-width="200">
          <template #default="{ row }">
            <el-tag v-for="scope in row.scopes" :key="scope" size="small" class="scope-tag">
              {{ scope }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="row.usable ? 'success' : 'info'" size="small">
              {{ row.revoked_at ? '已吊销' : row.usable ? '可用' : '已过期' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近使用" width="180">
          <template #default="{ row }">{{ formatTime(row.last_used_at) }}</template>
        </el-table-column>
        <el-table-column label="过期时间" width="180">
          <template #default="{ row }">{{ row.expires_at ? formatTime(row.expires_at) : '不过期' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" @click="showAudit(row)">审计</el-button>
            <el-button
              text
              type="danger"
              size="small"
              :disabled="!row.usable"
              @click="revoke(row)"
            >
              吊销
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" title="创建 API Key" width="520px">
      <el-form label-position="top">
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="例如：claude-desktop / 售后机器人" />
        </el-form-item>
        <el-form-item label="权限（scope）">
          <el-checkbox-group v-model="form.scopes">
            <el-checkbox
              v-for="option in scopeOptions"
              :key="option.value"
              :value="option.value"
            >
              <code>{{ option.value }}</code>
              <span class="muted small"> · {{ option.description }}</span>
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="说明（可选）">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="有效期（天，留空表示不过期）">
          <el-input-number v-model="form.expires_in_days" :min="1" :max="730" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submit">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog
      :model-value="Boolean(createdKey)"
      title="请立即保存密钥"
      width="560px"
      @close="createdKey = ''"
    >
      <el-alert
        type="warning"
        :closable="false"
        title="关闭后无法再次查看明文"
        description="服务端只保存哈希。如果丢失，只能吊销后重新创建。"
        show-icon
      />
      <div class="secret-box">
        <code>{{ createdKey }}</code>
        <el-button :icon="CopyDocument" text @click="copyKey">复制</el-button>
      </div>
      <p class="muted small">
        用法示例：<code>curl -H "Authorization: Bearer &lt;key&gt;" /api/v1/public/kbs</code>
      </p>
    </el-dialog>

    <el-drawer v-model="auditVisible" :title="`审计日志 · ${auditKeyName}`" size="520px">
      <el-timeline v-if="auditRows.length">
        <el-timeline-item
          v-for="row in auditRows"
          :key="row.id"
          :timestamp="formatTime(row.created_at)"
        >
          <div><strong>{{ row.action }}</strong></div>
          <div class="muted small">
            {{ row.ip_address || '未知 IP' }} · request {{ row.request_id || '—' }}
          </div>
          <pre class="audit-detail">{{ JSON.stringify(row.detail, null, 2) }}</pre>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-else description="暂无审计记录" />
    </el-drawer>
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
  max-width: 720px;
  line-height: 1.7;
}

.actions {
  display: flex;
  gap: 8px;
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
}

.stat-value.small {
  font-size: 16px;
  line-height: 1.9;
  color: #334155;
}

.key-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
}

.scope-tag {
  margin-right: 4px;
}

.secret-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 16px 0 8px;
  padding: 12px;
  border-radius: 8px;
  background: #0f172a;
  color: #e2e8f0;
  word-break: break-all;
}

.small {
  font-size: 12px;
}

.audit-detail {
  margin: 6px 0 0;
  padding: 8px;
  border-radius: 6px;
  background: #f8fafc;
  font-size: 12px;
  white-space: pre-wrap;
}
</style>
