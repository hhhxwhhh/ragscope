<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Checked, Refresh, Search } from '@element-plus/icons-vue'

import {
  convertReviewToCase,
  deleteReview,
  getReviewSummary,
  listReviewQueue,
  upsertReview,
} from '@/api/reviews'
import type {
  ReviewIssueTag,
  ReviewQueueItem,
  ReviewSummary,
  ReviewVerdict,
} from '@/types'

const props = defineProps<{ kbId: string }>()

const VERDICT_LABELS: Record<ReviewVerdict, string> = {
  pass: '通过',
  needs_fix: '需修改',
  fail: '不通过',
}

const VERDICT_TYPES: Record<ReviewVerdict, 'success' | 'warning' | 'danger'> = {
  pass: 'success',
  needs_fix: 'warning',
  fail: 'danger',
}

const ISSUE_LABELS: Record<ReviewIssueTag, string> = {
  unsupported_claim: '无依据陈述',
  wrong_citation: '引用错误',
  incomplete: '回答不完整',
  off_topic: '答非所问',
  refusal_error: '拒答不当',
  formatting: '格式问题',
}

const ISSUE_OPTIONS = Object.entries(ISSUE_LABELS) as [ReviewIssueTag, string][]

const summary = ref<ReviewSummary | null>(null)
const items = ref<ReviewQueueItem[]>([])
const loading = ref(false)
const total = ref(0)
const currentPage = ref(1)
const pageSize = 20

const statusFilter = ref<'unreviewed' | 'reviewed' | 'all'>('unreviewed')
const verdictFilter = ref<ReviewVerdict | ''>('')
const keyword = ref('')

const dialogVisible = ref(false)
const saving = ref(false)
const current = ref<ReviewQueueItem | null>(null)
const form = reactive({
  verdict: 'pass' as ReviewVerdict,
  issue_tags: [] as ReviewIssueTag[],
  note: '',
})

const attentionCount = computed(() =>
  summary.value ? summary.value.needs_fix_count + summary.value.fail_count : 0,
)

const topIssue = computed(() => {
  const counts = summary.value?.issue_counts
  if (!counts) {
    return '暂无问题标签'
  }
  const entries = Object.entries(counts) as [ReviewIssueTag, number][]
  const top = entries.sort((left, right) => right[1] - left[1])[0]
  return top && top[1] > 0 ? `${ISSUE_LABELS[top[0]]} ${top[1]}` : '暂无问题标签'
})

function formatPct(value: number | undefined): string {
  return `${((value ?? 0) * 100).toFixed(1)}%`
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function answerPreview(value: string): string {
  const text = value.replace(/\s+/g, ' ').trim()
  return text.length > 140 ? `${text.slice(0, 140)}…` : text
}

function citationRate(item: ReviewQueueItem): number | null {
  const value = item.citation_stats?.citation_rate
  return typeof value === 'number' ? value : null
}

function citedCount(item: ReviewQueueItem): number {
  return item.citations.filter((citation) => citation.cited).length
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const [queue, stats] = await Promise.all([
      listReviewQueue(props.kbId, {
        status: statusFilter.value,
        verdict: verdictFilter.value,
        q: keyword.value.trim(),
        offset: (currentPage.value - 1) * pageSize,
        limit: pageSize,
      }),
      getReviewSummary(props.kbId),
    ])
    items.value = queue.items
    total.value = queue.total
    summary.value = stats
  } catch {
    // Error toast is handled by the axios interceptor.
  } finally {
    loading.value = false
  }
}

function reload(): void {
  currentPage.value = 1
  load()
}

function openReview(item: ReviewQueueItem): void {
  current.value = item
  form.verdict = item.review?.verdict ?? 'pass'
  form.issue_tags = [...(item.review?.issue_tags ?? [])]
  form.note = item.review?.note ?? ''
  dialogVisible.value = true
}

watch(
  () => form.verdict,
  (verdict) => {
    if (verdict === 'pass') {
      form.issue_tags = []
    }
  },
)

async function submitReview(): Promise<void> {
  if (!current.value) {
    return
  }
  if (form.verdict !== 'pass' && form.issue_tags.length === 0) {
    ElMessage.warning('请至少选择一个不通过原因')
    return
  }
  saving.value = true
  try {
    await upsertReview(current.value.message_id, {
      verdict: form.verdict,
      issue_tags: form.issue_tags,
      note: form.note.trim() || null,
    })
    ElMessage.success('抽检结果已保存')
    dialogVisible.value = false
    await load()
  } catch {
    // Error toast is handled by the axios interceptor.
  } finally {
    saving.value = false
  }
}

async function removeReview(item: ReviewQueueItem): Promise<void> {
  try {
    await ElMessageBox.confirm('撤销后这条回答会回到待抽检队列。', '撤销抽检', {
      type: 'warning',
      confirmButtonText: '撤销',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deleteReview(item.message_id)
    ElMessage.success('已撤销抽检')
    await load()
  } catch {
    // Error toast is handled by the axios interceptor.
  }
}

async function convertToCase(item: ReviewQueueItem): Promise<void> {
  if (!item.review) {
    return
  }
  try {
    const result = await convertReviewToCase(item.review.id)
    ElMessage.success(
      result.created
        ? `已加入「${result.dataset_name}」，可在评测页运行`
        : `用例已存在：${result.dataset_name}`,
    )
  } catch {
    // Error toast is handled by the axios interceptor.
  }
}

watch(
  () => props.kbId,
  () => {
    currentPage.value = 1
    load()
  },
)

watch(statusFilter, () => {
  verdictFilter.value = ''
  reload()
})

watch(verdictFilter, reload)

onMounted(load)
</script>

<template>
  <div class="review-panel">
    <div class="summary-grid">
      <el-card shadow="never" class="summary-card">
        <div class="summary-label">待抽检</div>
        <div class="summary-value">{{ summary?.unreviewed ?? 0 }}</div>
        <div class="summary-hint">已完成回答 {{ summary?.total_answers ?? 0 }} 条</div>
      </el-card>
      <el-card shadow="never" class="summary-card">
        <div class="summary-label">抽检覆盖率</div>
        <div class="summary-value">{{ formatPct(summary?.coverage) }}</div>
        <div class="summary-hint">{{ summary?.reviewed ?? 0 }} 条已抽检</div>
      </el-card>
      <el-card shadow="never" class="summary-card">
        <div class="summary-label">抽检通过率</div>
        <div class="summary-value">{{ formatPct(summary?.pass_rate) }}</div>
        <div class="summary-hint">{{ summary?.pass_count ?? 0 }} 条通过</div>
      </el-card>
      <el-card shadow="never" class="summary-card">
        <div class="summary-label">需关注</div>
        <div class="summary-value attention">{{ attentionCount }}</div>
        <div class="summary-hint">{{ topIssue }}</div>
      </el-card>
    </div>

    <div class="toolbar">
      <el-radio-group v-model="statusFilter">
        <el-radio-button value="unreviewed">待抽检</el-radio-button>
        <el-radio-button value="reviewed">已抽检</el-radio-button>
        <el-radio-button value="all">全部</el-radio-button>
      </el-radio-group>

      <el-select
        v-if="statusFilter !== 'unreviewed'"
        v-model="verdictFilter"
        clearable
        placeholder="全部结论"
        class="verdict-filter"
      >
        <el-option label="通过" value="pass" />
        <el-option label="需修改" value="needs_fix" />
        <el-option label="不通过" value="fail" />
      </el-select>

      <el-input
        v-model="keyword"
        class="search-input"
        clearable
        placeholder="搜索问题或回答"
        @keyup.enter="reload"
        @clear="reload"
      >
        <template #append>
          <el-button :icon="Search" @click="reload" />
        </template>
      </el-input>

      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-table v-loading="loading" :data="items" row-key="message_id">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="answer-detail">
            <div class="detail-block">
              <div class="detail-label">完整回答</div>
              <p class="detail-text">{{ row.answer }}</p>
            </div>
            <div class="detail-block">
              <div class="detail-label">
                引用来源
                <span class="muted">
                  {{ citedCount(row) }}/{{ row.citations.length }} 条被正文引用
                </span>
              </div>
              <el-empty
                v-if="!row.citations.length"
                :image-size="48"
                description="没有引用来源"
              />
              <div v-for="citation in row.citations" :key="citation.id" class="citation-row">
                <el-tag size="small" :type="citation.cited ? 'success' : 'info'">
                  [{{ citation.rank }}]{{ citation.cited ? ' 已引用' : ' 未引用' }}
                </el-tag>
                <span class="citation-file">
                  {{ citation.filename }} #{{ citation.chunk_index }}
                  <span v-if="citation.page_number" class="muted">
                    · 第 {{ citation.page_number }} 页
                  </span>
                </span>
                <p class="citation-snippet">{{ citation.snippet }}</p>
              </div>
            </div>
            <div v-if="row.review" class="detail-block">
              <div class="detail-label">抽检备注</div>
              <p class="detail-text">{{ row.review.note || '无' }}</p>
            </div>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="问题" min-width="200">
        <template #default="{ row }">
          <div class="question-text">{{ row.question || '（未找到对应问题）' }}</div>
          <div class="muted meta-line">
            {{ row.conversation_title }} · {{ formatTime(row.created_at) }}
          </div>
        </template>
      </el-table-column>

      <el-table-column label="回答" min-width="260">
        <template #default="{ row }">
          <div class="answer-preview">{{ answerPreview(row.answer) }}</div>
          <div class="muted meta-line">
            <el-tag v-if="row.prompt_variant" size="small" type="info">
              {{ row.prompt_variant }}
            </el-tag>
            <span v-if="row.latency_ms">{{ row.latency_ms }} ms</span>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="引用有效率" width="120">
        <template #default="{ row }">
          <span v-if="citationRate(row) === null" class="muted">—</span>
          <span v-else>{{ formatPct(citationRate(row) ?? 0) }}</span>
        </template>
      </el-table-column>

      <el-table-column label="结论" width="110">
        <template #default="{ row }">
          <el-tag
            v-if="row.review"
            :type="VERDICT_TYPES[row.review.verdict as ReviewVerdict]"
            size="small"
          >
            {{ VERDICT_LABELS[row.review.verdict as ReviewVerdict] }}
          </el-tag>
          <el-tag v-else size="small" type="info">待抽检</el-tag>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="240" align="right">
        <template #default="{ row }">
          <el-button text type="primary" @click="openReview(row)">
            {{ row.review ? '修改' : '抽检' }}
          </el-button>
          <el-button
            v-if="row.review && row.review.verdict !== 'pass'"
            text
            type="warning"
            @click="convertToCase(row)"
          >
            转回归用例
          </el-button>
          <el-button v-if="row.review" text type="danger" @click="removeReview(row)">
            撤销
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty
      v-if="!loading && !items.length"
      :description="
        statusFilter === 'unreviewed' ? '待抽检队列已清空' : '没有符合条件的回答'
      "
    />

    <div class="pager">
      <el-pagination
        v-model:current-page="currentPage"
        :page-size="pageSize"
        :total="total"
        layout="prev, pager, next, total"
        background
        @current-change="load"
      />
    </div>

    <el-dialog v-model="dialogVisible" title="人工抽检" width="640px">
      <template v-if="current">
        <div class="dialog-question">
          <span class="detail-label">问题</span>
          <p>{{ current.question }}</p>
        </div>
        <div class="dialog-answer">
          <span class="detail-label">回答</span>
          <p>{{ current.answer }}</p>
        </div>
      </template>

      <el-form label-width="90px" class="review-form">
        <el-form-item label="结论">
          <el-radio-group v-model="form.verdict">
            <el-radio value="pass">通过</el-radio>
            <el-radio value="needs_fix">需修改</el-radio>
            <el-radio value="fail">不通过</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item v-if="form.verdict !== 'pass'" label="问题标签">
          <el-checkbox-group v-model="form.issue_tags">
            <el-checkbox v-for="[value, label] in ISSUE_OPTIONS" :key="value" :value="value">
              {{ label }}
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>

        <el-form-item label="备注">
          <el-input
            v-model="form.note"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-word-limit
            placeholder="记录具体哪里有问题，方便后续回归"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" :icon="Checked" @click="submitReview">
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.review-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.summary-card {
  border-radius: 10px;
}

.summary-label {
  color: #64748b;
  font-size: 13px;
}

.summary-value {
  margin: 6px 0 2px;
  color: #0f172a;
  font-size: 26px;
  font-weight: 600;
}

.summary-value.attention {
  color: #d97706;
}

.summary-hint {
  overflow: hidden;
  color: #94a3b8;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.verdict-filter {
  width: 130px;
}

.search-input {
  width: 260px;
}

.question-text {
  color: #0f172a;
  font-weight: 500;
  line-height: 1.6;
}

.answer-preview {
  color: #374151;
  font-size: 13px;
  line-height: 1.6;
}

.meta-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  font-size: 12px;
}

.answer-detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 8px 18px 16px;
}

.detail-label {
  margin-bottom: 6px;
  color: #475569;
  font-size: 13px;
  font-weight: 600;
}

.detail-text {
  margin: 0;
  color: #374151;
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
}

.citation-row {
  margin-bottom: 10px;
}

.citation-file {
  margin-left: 8px;
  color: #475569;
  font-size: 12px;
}

.citation-snippet {
  margin: 5px 0 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: #f8fafc;
  color: #475569;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
}

.pager {
  display: flex;
  justify-content: flex-end;
}

.dialog-question,
.dialog-answer {
  margin-bottom: 14px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #f8fafc;
}

.dialog-question p,
.dialog-answer p {
  margin: 4px 0 0;
  max-height: 180px;
  overflow: auto;
  color: #334155;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.review-form {
  margin-top: 4px;
}
</style>
