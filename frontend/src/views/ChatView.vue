<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  CaretBottom,
  CaretTop,
  Delete,
  Download,
  EditPen,
  Plus,
  Search,
} from '@element-plus/icons-vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

import { getKb } from '@/api/kb'
import {
  convertFeedbackToCase,
  deleteConversation,
  deleteFeedback,
  exportConversation,
  listConversations,
  listMessages,
  renameConversation,
  streamChat,
  upsertFeedback,
} from '@/api/chat'
import type {
  AgentRun,
  AgentStep,
  ChatMessage,
  Citation,
  Conversation,
  KnowledgeBase,
  RetrievalTrace,
  TraceCandidate,
} from '@/types'

const props = defineProps<{ id: string; conversationId?: string }>()
const router = useRouter()

const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true })

const kb = ref<KnowledgeBase | null>(null)
const conversations = ref<Conversation[]>([])
const messages = ref<ChatMessage[]>([])
const input = ref('')
const loadingMessages = ref(false)
const sending = ref(false)
const controller = ref<AbortController | null>(null)
const scrollRef = ref<HTMLElement | null>(null)

const citationDrawer = ref(false)
const activeCitations = ref<Citation[]>([])
const activeCitationRank = ref<number | null>(null)
const expandedTraces = reactive(new Set<string>())
const expandedAgents = reactive(new Set<string>())

// Agent mode is a per-browser preference: it costs extra LLM calls, so it is
// opt-in and remembered across conversations.
const agentMode = ref(localStorage.getItem('ragscope.agent-mode') === '1')

const conversationQuery = ref('')
let searchTimer: ReturnType<typeof setTimeout> | undefined

const feedbackDialogVisible = ref(false)
const feedbackTarget = ref<ChatMessage | null>(null)
const feedbackReason = ref('incorrect')
const feedbackComment = ref('')
const feedbackSaving = ref(false)

const feedbackReasons = [
  { value: 'incorrect', label: '答案不正确' },
  { value: 'outdated', label: '资料过时' },
  { value: 'incomplete', label: '回答不完整' },
  { value: 'citation', label: '引用有问题' },
  { value: 'other', label: '其他原因' },
]

const activeConversationId = computed(() => props.conversationId || '')
const activeConversation = computed(
  () => conversations.value.find((item) => item.id === activeConversationId.value) || null,
)
const sampleQuestions = [
  'X1 的续航是多久？',
  'X2 和 X1 有什么区别？',
  '手表进水了还能保修吗？',
]

const stageLabels: Record<string, string> = {
  rewrite_ms: 'query 改写',
  embed_ms: '向量化',
  vector_ms: '向量检索',
  bm25_ms: 'BM25',
  fusion_ms: 'RRF 融合',
  rerank_ms: '重排',
  agent_ms: 'Agent 规划',
  total_ms: '总计',
}

const toolLabels: Record<string, string> = {
  search_knowledge_base: '检索知识库',
  read_chunk_neighbors: '读取相邻 chunk',
  get_document_outline: '查看文档大纲',
}

const stoppedReasonLabels: Record<string, string> = {
  model_finished: '模型判断证据充足',
  max_steps: '达到最大工具步数',
  turn_timeout: '决策超时',
  llm_error: '模型调用失败',
  cancelled: '已停止',
  failed: '执行失败',
  running: '规划中…',
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function fmt(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : value.toFixed(3)
}

function renderMarkdown(content: string): string {
  const rendered = markdown.render(content)
  // Turn [n] markers into clickable citation chips.
  const withChips = rendered.replace(
    /\[(\d{1,3})\]/g,
    '<span class="citation-ref" data-rank="$1">[$1]</span>',
  )
  return DOMPurify.sanitize(withChips)
}

function finalCandidates(trace: RetrievalTrace): TraceCandidate[] {
  return trace.candidates.final || []
}

function citedCitations(message: ChatMessage): Citation[] {
  return message.citations.filter((citation) => citation.cited)
}

function stageLabel(stage: string): string {
  return stageLabels[stage] || stage
}

function toolLabel(name: string): string {
  return toolLabels[name] || name
}

function stoppedReasonLabel(run: AgentRun): string {
  const reason = run.stopped_reason || (run.status === 'completed' ? 'model_finished' : run.status)
  return stoppedReasonLabels[reason] || reason
}

function agentArgs(step: AgentStep): string {
  const args = step.arguments || {}
  if (typeof args.query === 'string') {
    return `query: ${args.query}`
  }
  if (typeof args.chunk_id === 'string') {
    return `chunk: ${args.chunk_id.slice(0, 8)} · window ${String(args.window ?? 1)}`
  }
  if (typeof args.document_id === 'string') {
    return `document: ${args.document_id.slice(0, 8)}`
  }
  return JSON.stringify(args)
}

function agentObservationSummary(step: AgentStep): string {
  const obs = step.observation || {}
  if (obs.ok === false) {
    return `错误：${String(obs.error || '未知错误')}`
  }
  if (Array.isArray(obs.results)) {
    const first = obs.results[0] as Record<string, unknown> | undefined
    const preview = first && typeof first.preview === 'string' ? first.preview : ''
    return `${String(obs.count ?? obs.results.length)} 条结果${preview ? ` · ${preview}` : ''}`
  }
  if (Array.isArray(obs.neighbors)) {
    return `${String(obs.count ?? obs.neighbors.length)} 个相邻 chunk`
  }
  if (Array.isArray(obs.outline)) {
    return `${String(obs.chunk_count ?? '—')} 个 chunk · ${obs.outline.length} 个标题`
  }
  return '完成'
}

function toggleAgent(message: ChatMessage): void {
  if (expandedAgents.has(message.id)) {
    expandedAgents.delete(message.id)
  } else {
    expandedAgents.add(message.id)
  }
}

function toggleAgentMode(): void {
  if (!sending.value) {
    agentMode.value = !agentMode.value
  }
}

function appendAgentStep(message: ChatMessage, step: AgentStep): void {
  const run: AgentRun = message.agent || {
    id: `local-agent-${message.id}`,
    status: 'running',
    stopped_reason: null,
    step_count: 0,
    tool_calls: 0,
    latency_ms: null,
    error: null,
    steps: [],
  }
  run.steps.push(step)
  run.step_count = run.steps.length
  run.tool_calls = run.steps.length
  message.agent = run
  expandedAgents.add(message.id)
}

function scrollToBottom(): void {
  nextTick(() => {
    const el = scrollRef.value
    if (el) {
      el.scrollTop = el.scrollHeight
    }
  })
}

async function loadKb(): Promise<void> {
  kb.value = await getKb(props.id)
}

async function loadConversations(): Promise<void> {
  const page = await listConversations(props.id, 0, 50, conversationQuery.value.trim() || undefined)
  conversations.value = page.items
}

function onSearchInput(): void {
  if (searchTimer) {
    clearTimeout(searchTimer)
  }
  searchTimer = setTimeout(() => {
    loadConversations().catch(() => undefined)
  }, 250)
}

async function loadMessages(): Promise<void> {
  if (!props.conversationId) {
    messages.value = []
    return
  }
  loadingMessages.value = true
  try {
    const page = await listMessages(props.conversationId)
    messages.value = page.items
    scrollToBottom()
  } finally {
    loadingMessages.value = false
  }
}

function makeLocalMessage(role: 'user' | 'assistant', content: string): ChatMessage {
  return {
    id: `local-${role}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    role,
    content,
    status: role === 'assistant' ? 'streaming' : 'completed',
    latency_ms: null,
    token_usage: null,
    citation_stats: null,
    rewrite_query: null,
    created_at: new Date().toISOString(),
    citations: [],
    trace: null,
    feedback: null,
    agent: null,
  }
}

async function send(): Promise<void> {
  const question = input.value.trim()
  if (!question || sending.value) {
    return
  }

  input.value = ''
  const userMessage = makeLocalMessage('user', question)
  messages.value.push(userMessage, makeLocalMessage('assistant', ''))
  // Read the assistant message back through the reactive array: mutating the
  // raw object pushed above would bypass Vue's proxy and not trigger renders.
  const assistantMessage = messages.value[messages.value.length - 1]
  scrollToBottom()

  sending.value = true
  controller.value = new AbortController()
  let streamedText = ''

  try {
    await streamChat(
      props.id,
      {
        question,
        conversation_id: activeConversationId.value || null,
        mode: agentMode.value ? 'agent' : 'rag',
      },
      {
        onMeta: (meta) => {
          if (!activeConversationId.value) {
            router.replace(`/kbs/${props.id}/chat/${meta.conversation_id}`)
          }
        },
        onRetrieval: (trace) => {
          assistantMessage.trace = trace
          assistantMessage.rewrite_query = trace.rewritten_query
        },
        onAgentStep: (step) => {
          appendAgentStep(assistantMessage, step)
          scrollToBottom()
        },
        onToken: (delta) => {
          streamedText += delta
          assistantMessage.content = streamedText
          scrollToBottom()
        },
        onDone: (message) => {
          if (message) {
            const index = messages.value.findIndex((item) => item.id === assistantMessage.id)
            if (index >= 0) {
              messages.value[index] = message
            }
          } else {
            assistantMessage.content = streamedText
            assistantMessage.status = 'completed'
          }
          scrollToBottom()
        },
        onError: (detail) => {
          assistantMessage.status = 'failed'
          if (assistantMessage.agent) {
            assistantMessage.agent.status = 'failed'
            assistantMessage.agent.stopped_reason = 'failed'
          }
          ElMessage.error(detail)
        },
      },
      controller.value.signal,
    )
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      assistantMessage.status = 'cancelled'
      assistantMessage.content = streamedText
      if (assistantMessage.agent) {
        assistantMessage.agent.status = 'cancelled'
        assistantMessage.agent.stopped_reason = 'cancelled'
      }
      ElMessage.info('已停止生成')
    } else {
      assistantMessage.status = 'failed'
      assistantMessage.content = streamedText || '生成失败'
      ElMessage.error(error instanceof Error ? error.message : '生成失败')
    }
  } finally {
    sending.value = false
    controller.value = null
    await loadConversations().catch(() => {
      // Sidebar refresh is best-effort.
    })
  }
}

function stopGeneration(): void {
  controller.value?.abort()
}

function startNewChat(): void {
  if (sending.value) {
    stopGeneration()
  }
  messages.value = []
  input.value = ''
  router.push(`/kbs/${props.id}/chat`)
}

function openConversation(conversationId: string): void {
  if (sending.value) {
    stopGeneration()
  }
  router.push(`/kbs/${props.id}/chat/${conversationId}`)
}

async function removeConversation(conversation: Conversation): Promise<void> {
  try {
    await ElMessageBox.confirm(`删除会话「${conversation.title}」？`, '删除会话', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await deleteConversation(conversation.id)
  ElMessage.success('已删除')
  if (conversation.id === activeConversationId.value) {
    messages.value = []
    await router.push(`/kbs/${props.id}/chat`)
  }
  await loadConversations()
}

async function renameConversationPrompt(conversation: Conversation): Promise<void> {
  try {
    const { value } = await ElMessageBox.prompt('输入新的会话名称', '重命名会话', {
      inputValue: conversation.title,
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputPattern: /\S+/,
      inputErrorMessage: '名称不能为空',
    })
    const updated = await renameConversation(conversation.id, value.trim())
    const index = conversations.value.findIndex((item) => item.id === conversation.id)
    if (index >= 0) {
      conversations.value[index] = updated
    }
  } catch {
    // Cancelled by the user, or the axios interceptor already showed the error.
  }
}

async function downloadConversation(): Promise<void> {
  if (!activeConversation.value) {
    return
  }
  const blob = await exportConversation(activeConversation.value.id)
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${activeConversation.value.title || 'conversation'}.md`
  link.click()
  URL.revokeObjectURL(url)
}

function canRate(message: ChatMessage): boolean {
  return (
    message.role === 'assistant' &&
    !message.id.startsWith('local-') &&
    (message.status === 'completed' || message.status === 'cancelled')
  )
}

async function rateMessage(message: ChatMessage, rating: -1 | 1): Promise<void> {
  if (!canRate(message)) {
    return
  }
  if (message.feedback?.rating === rating) {
    await deleteFeedback(message.id)
    message.feedback = null
    return
  }
  if (rating === 1) {
    message.feedback = await upsertFeedback(message.id, { rating: 1 })
    ElMessage.success('已记录评价')
    return
  }
  feedbackTarget.value = message
  feedbackReason.value = 'incorrect'
  feedbackComment.value = message.feedback?.comment || ''
  feedbackDialogVisible.value = true
}

async function saveNegativeFeedback(): Promise<void> {
  const message = feedbackTarget.value
  if (!message) {
    return
  }
  feedbackSaving.value = true
  try {
    message.feedback = await upsertFeedback(message.id, {
      rating: -1,
      reason: feedbackReason.value,
      comment: feedbackComment.value.trim() || null,
    })
    feedbackDialogVisible.value = false
    ElMessage.success('已记录差评，可一键转为评测用例')
  } finally {
    feedbackSaving.value = false
  }
}

async function makeEvalCase(message: ChatMessage): Promise<void> {
  const result = await convertFeedbackToCase(message.id)
  ElMessage.success(
    result.created
      ? `已加入评测集「${result.dataset_name}」`
      : `该问题已存在于「${result.dataset_name}」`,
  )
}

function citationSummary(message: ChatMessage): string {
  const stats = message.citation_stats
  if (!stats) {
    return ''
  }
  const parts = [`引用 ${stats.cited_sources}/${stats.source_count}`]
  if (stats.invalid_markers > 0) {
    parts.push(`越界标记 ${stats.invalid_markers}`)
  }
  return parts.join(' · ')
}

function feedbackReasonLabel(value: string | null): string {
  return feedbackReasons.find((item) => item.value === value)?.label || '其他原因'
}

function useSample(question: string): void {
  input.value = question
}

function openCitations(message: ChatMessage, rank: number | null = null): void {
  activeCitations.value = message.citations
  activeCitationRank.value = rank
  citationDrawer.value = true
}

function toggleTrace(message: ChatMessage): void {
  if (expandedTraces.has(message.id)) {
    expandedTraces.delete(message.id)
  } else {
    expandedTraces.add(message.id)
  }
}

function handleCitationClick(event: MouseEvent): void {
  const target = event.target as HTMLElement | null
  const rank = target?.dataset?.rank
  if (!rank) {
    return
  }
  const container = target.closest('.message') as HTMLElement | null
  const messageId = container?.dataset?.messageId
  const message = messages.value.find((item) => item.id === messageId)
  if (message) {
    openCitations(message, Number(rank))
  }
}

watch(
  () => props.id,
  () => {
    loadKb().catch(() => undefined)
    loadConversations().catch(() => undefined)
  },
  { immediate: true },
)

watch(
  () => props.conversationId,
  () => {
    if (!sending.value) {
      loadMessages().catch(() => undefined)
    }
  },
  { immediate: true },
)

watch(agentMode, (value) => {
  localStorage.setItem('ragscope.agent-mode', value ? '1' : '0')
})

onMounted(scrollToBottom)
</script>

<template>
  <div class="chat-page">
    <aside class="conv-sidebar">
      <div class="conv-head">
        <el-button type="primary" :icon="Plus" class="new-chat" @click="startNewChat">
          新对话
        </el-button>
        <el-input
          v-model="conversationQuery"
          class="conv-search"
          size="small"
          placeholder="搜索会话"
          clearable
          @input="onSearchInput"
          @clear="loadConversations"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
      </div>

      <div class="conv-list">
        <button
          v-for="conversation in conversations"
          :key="conversation.id"
          class="conv-item"
          :class="{ active: conversation.id === activeConversationId }"
          @click="openConversation(conversation.id)"
        >
          <span class="conv-title">{{ conversation.title }}</span>
          <span class="conv-time">{{ formatTime(conversation.last_message_at) }}</span>
          <span class="conv-actions">
            <el-icon title="重命名" @click.stop="renameConversationPrompt(conversation)">
              <EditPen />
            </el-icon>
            <el-icon title="删除" @click.stop="removeConversation(conversation)">
              <Delete />
            </el-icon>
          </span>
        </button>
        <div v-if="!conversations.length" class="conv-empty muted">
          {{ conversationQuery ? '没有匹配的会话' : '还没有对话' }}
        </div>
      </div>

      <div class="conv-foot">
        <el-button text @click="router.push(`/kbs/${id}`)">← 返回知识库</el-button>
      </div>
    </aside>

    <section class="chat-main">
      <header class="chat-head">
        <div>
          <div class="chat-title">{{ kb?.name || '知识库问答' }}</div>
          <div class="chat-sub muted">
            {{
              kb
                ? `chunk ${kb.chunk_size} / ${kb.chunk_overlap} · 索引 v${kb.index_version}`
                : '混合检索 + 重排 + 引用'
            }}
          </div>
        </div>
        <div class="chat-head-actions">
          <el-tag v-if="sending" type="warning" size="small">生成中…</el-tag>
          <label class="mode-toggle" title="Agent 模式会先规划并多次检索，再基于证据回答">
            <el-switch v-model="agentMode" size="small" :disabled="sending" />
            <span
              :class="{ active: agentMode, disabled: sending }"
              @click="toggleAgentMode"
            >
              Agent 检索
            </span>
          </label>
          <el-button
            v-if="activeConversationId"
            size="small"
            :icon="Download"
            @click="downloadConversation"
          >
            导出 Markdown
          </el-button>
        </div>
      </header>

      <div ref="scrollRef" class="messages" @click="handleCitationClick">
        <div v-if="!messages.length && !loadingMessages" class="welcome">
          <div class="welcome-mark">R</div>
          <h3>基于知识库回答，并标注来源</h3>
          <p class="muted">每次回答都会记录改写后的 query、各阶段候选与耗时。</p>
          <div class="samples">
            <el-button
              v-for="question in sampleQuestions"
              :key="question"
              round
              @click="useSample(question)"
            >
              {{ question }}
            </el-button>
          </div>
        </div>

        <div
          v-for="message in messages"
          :key="message.id"
          class="message"
          :class="message.role"
          :data-message-id="message.id"
        >
          <div class="bubble">
            <div
              v-if="message.role === 'assistant'"
              class="answer markdown"
            >
              <div v-html="renderMarkdown(message.content || (message.status === 'streaming' ? '…' : ''))" />
              <span v-if="message.status === 'streaming'" class="cursor" />
            </div>
            <div v-else class="question">{{ message.content }}</div>

            <div v-if="message.role === 'assistant'" class="message-actions">
              <el-button
                v-for="citation in citedCitations(message)"
                :key="citation.id"
                text
                size="small"
                class="cite-chip"
                @click.stop="openCitations(message, citation.rank)"
              >
                [{{ citation.rank }}] {{ citation.filename }}
              </el-button>
              <el-button
                v-if="message.citations.length"
                text
                size="small"
                @click.stop="openCitations(message)"
              >
                全部来源
              </el-button>
              <el-button v-if="message.trace" text size="small" @click.stop="toggleTrace(message)">
                {{ expandedTraces.has(message.id) ? '收起检索详情' : '检索详情' }}
              </el-button>
              <el-button v-if="message.agent" text size="small" @click.stop="toggleAgent(message)">
                {{
                  expandedAgents.has(message.id)
                    ? '收起 Agent 步骤'
                    : `Agent 步骤 (${message.agent.steps.length})`
                }}
              </el-button>
              <span v-if="message.status === 'cancelled'" class="muted">已停止</span>
              <span v-if="message.status === 'failed'" class="error-text">生成失败</span>
              <span v-if="message.latency_ms !== null" class="muted">{{ message.latency_ms }} ms</span>
              <span
                v-if="citationSummary(message)"
                class="muted"
                :class="{ 'error-text': (message.citation_stats?.invalid_markers || 0) > 0 }"
              >
                {{ citationSummary(message) }}
              </span>
              <template v-if="canRate(message)">
                <el-button
                  text
                  size="small"
                  :class="{ 'feedback-active': message.feedback?.rating === 1 }"
                  @click.stop="rateMessage(message, 1)"
                >
                  <el-icon><CaretTop /></el-icon>有用
                </el-button>
                <el-button
                  text
                  size="small"
                  :class="{ 'feedback-active negative': message.feedback?.rating === -1 }"
                  @click.stop="rateMessage(message, -1)"
                >
                  <el-icon><CaretBottom /></el-icon>没用
                </el-button>
              </template>
              <span v-if="message.feedback?.rating === -1" class="muted">
                {{ feedbackReasonLabel(message.feedback.reason) }}
              </span>
              <el-button
                v-if="message.feedback?.rating === -1"
                text
                size="small"
                type="primary"
                @click.stop="makeEvalCase(message)"
              >
                转为评测用例
              </el-button>
            </div>

            <div
              v-if="message.trace && expandedTraces.has(message.id)"
              class="trace-panel"
            >
              <div class="trace-row">
                <span class="trace-label">检索 query</span>
                <span>{{ message.trace.rewritten_query }}</span>
              </div>
              <div v-if="message.trace.rewrite_applied" class="trace-row">
                <span class="trace-label">原始问题</span>
                <span class="muted">{{ message.trace.query }}</span>
              </div>
              <div v-if="message.trace.cache_hit" class="trace-row">
                <span class="trace-label">缓存</span>
                <span class="ok-text">命中 Redis 检索缓存，跳过向量化与召回</span>
              </div>
              <div v-if="message.trace.sub_queries?.length" class="trace-row">
                <span class="trace-label">多查询扩展</span>
                <span>
                  <el-tag
                    v-for="subQuery in message.trace.sub_queries"
                    :key="subQuery"
                    size="small"
                    type="info"
                    class="sub-query-tag"
                  >
                    {{ subQuery }}
                  </el-tag>
                </span>
              </div>
              <div class="trace-row">
                <span class="trace-label">重排</span>
                <span :class="message.trace.reranked ? 'ok-text' : 'muted'">
                  {{ message.trace.reranked ? '已启用' : `未启用 ${message.trace.rerank_error || ''}` }}
                </span>
              </div>
              <div class="timing-grid">
                <div
                  v-for="(ms, stage) in message.trace.stage_timings"
                  :key="stage"
                  class="timing"
                >
                  <span class="timing-stage">{{ stageLabel(String(stage)) }}</span>
                  <span class="timing-value">{{ Number(ms).toFixed(1) }} ms</span>
                </div>
              </div>
              <div class="candidate-list">
                <div
                  v-for="(candidate, index) in finalCandidates(message.trace)"
                  :key="candidate.chunk_id"
                  class="candidate"
                >
                  <span class="candidate-rank">#{{ index + 1 }}</span>
                  <div class="candidate-body">
                    <div class="candidate-head">
                      <span class="candidate-file">{{ candidate.filename }}</span>
                      <span class="muted">{{ candidate.heading_path || '无标题' }}</span>
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

            <div
              v-if="message.agent && expandedAgents.has(message.id)"
              class="agent-panel"
            >
              <div class="agent-panel-head">
                <span class="trace-label">Agent 规划</span>
                <el-tag
                  size="small"
                  :type="
                    message.agent.status === 'running'
                      ? 'warning'
                      : message.agent.status === 'completed'
                        ? 'success'
                        : 'info'
                  "
                >
                  {{
                    message.agent.status === 'running'
                      ? '运行中'
                      : message.agent.status === 'completed'
                        ? '已完成'
                        : message.agent.status
                  }}
                </el-tag>
                <span class="muted">{{ stoppedReasonLabel(message.agent) }}</span>
                <span v-if="message.agent.latency_ms !== null" class="muted">
                  · {{ message.agent.latency_ms }} ms
                </span>
                <span class="muted">· {{ message.agent.tool_calls }} 次工具</span>
              </div>
              <div
                v-for="step in message.agent.steps"
                :key="step.step_index"
                class="agent-step"
              >
                <div class="agent-step-head">
                  <span class="agent-step-index">{{ step.step_index }}</span>
                  <span class="agent-step-tool">{{ toolLabel(step.tool_name) }}</span>
                  <el-tag size="small" :type="step.status === 'ok' ? 'success' : 'danger'">
                    {{ step.status }}
                  </el-tag>
                  <span class="muted">{{ step.latency_ms }} ms</span>
                </div>
                <div class="agent-step-args">{{ agentArgs(step) }}</div>
                <div
                  class="agent-step-obs"
                  :class="{ 'error-text': step.status !== 'ok' }"
                >
                  {{ agentObservationSummary(step) }}
                </div>
              </div>
              <div v-if="!message.agent.steps.length" class="muted">没有执行工具调用</div>
            </div>
          </div>
        </div>
      </div>

      <footer class="composer">
        <el-input
          v-model="input"
          type="textarea"
          :rows="2"
          resize="none"
          placeholder="基于知识库提问，Enter 发送，Shift+Enter 换行"
          @keydown.enter.exact.prevent="send"
        />
        <div class="composer-actions">
          <span class="muted">回答只依据已入库资料</span>
          <div>
            <el-button v-if="sending" @click="stopGeneration">停止</el-button>
            <el-button type="primary" :loading="sending" :disabled="!input.trim()" @click="send">
              发送
            </el-button>
          </div>
        </div>
      </footer>
    </section>

    <el-drawer v-model="citationDrawer" title="引用来源" size="520px">
      <div class="citation-list">
        <el-card
          v-for="citation in activeCitations"
          :key="citation.id"
          shadow="never"
          class="citation-card"
          :class="{ highlight: citation.rank === activeCitationRank, dim: !citation.cited }"
        >
          <div class="citation-head">
            <el-tag size="small">[{{ citation.rank }}]</el-tag>
            <span class="citation-file">{{ citation.filename }}</span>
            <el-tag v-if="!citation.cited" size="small" type="info">未引用</el-tag>
          </div>
          <div class="citation-meta muted">
            {{ citation.heading_path || '无标题' }} · chunk #{{ citation.chunk_index }}
            <span v-if="citation.page_number"> · 第 {{ citation.page_number }} 页</span>
            · score {{ fmt(citation.score) }}
          </div>
          <p class="citation-text">{{ citation.snippet }}</p>
        </el-card>
        <el-empty v-if="!activeCitations.length" description="没有引用" />
      </div>
    </el-drawer>

    <el-dialog v-model="feedbackDialogVisible" title="这条回答哪里有问题？" width="460px">
      <el-form label-position="top">
        <el-form-item label="原因">
          <el-select v-model="feedbackReason" style="width: 100%">
            <el-option
              v-for="reason in feedbackReasons"
              :key="reason.value"
              :label="reason.label"
              :value="reason.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="补充说明（可选）">
          <el-input
            v-model="feedbackComment"
            type="textarea"
            :rows="3"
            maxlength="1000"
            show-word-limit
            placeholder="例如：答案引用了已过时的价格，应该是 2999 元"
          />
        </el-form-item>
      </el-form>
      <div class="feedback-hint muted">
        差评可以一键转成评测用例，把这次失败变成下一次回归的基线。
      </div>
      <template #footer>
        <el-button @click="feedbackDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="feedbackSaving" @click="saveNegativeFeedback">
          提交评价
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  height: calc(100vh - 60px);
  gap: 0;
}

.conv-sidebar {
  display: flex;
  flex-direction: column;
  width: 250px;
  border-right: 1px solid #e5e7eb;
  background: #fff;
}

.conv-head {
  padding: 16px;
}

.new-chat {
  width: 100%;
}

.conv-search {
  margin-top: 10px;
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px;
}

.conv-item {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 2px 8px;
  width: 100%;
  padding: 10px 12px;
  border: none;
  border-radius: 10px;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.conv-item:hover {
  background: #f5f7fb;
}

.conv-item.active {
  background: #eef2ff;
}

.conv-title {
  overflow: hidden;
  color: #1f2937;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-time {
  grid-column: 1;
  color: #94a3b8;
  font-size: 11px;
}

.conv-actions {
  display: flex;
  grid-row: 1 / span 2;
  gap: 6px;
  align-self: center;
  color: #cbd5e1;
  font-size: 14px;
  visibility: hidden;
}

.conv-item:hover .conv-actions {
  visibility: visible;
}

.conv-empty {
  padding: 24px 8px;
  font-size: 13px;
  text-align: center;
}

.conv-foot {
  padding: 12px 16px;
  border-top: 1px solid #f1f5f9;
}

.chat-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  background: var(--app-bg);
}

.chat-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 24px;
  border-bottom: 1px solid #e5e7eb;
  background: #fff;
}

.chat-head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.mode-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #64748b;
  font-size: 12px;
  cursor: pointer;
  user-select: none;
}

.mode-toggle .active {
  color: #4338ca;
  font-weight: 600;
}

.mode-toggle .disabled {
  color: #cbd5e1;
  cursor: not-allowed;
}

.chat-title {
  font-size: 16px;
  font-weight: 600;
}

.chat-sub {
  margin-top: 2px;
  font-size: 12px;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px clamp(16px, 5vw, 80px);
}

.welcome {
  max-width: 640px;
  margin: 8vh auto 0;
  text-align: center;
}

.welcome-mark {
  display: grid;
  width: 54px;
  height: 54px;
  margin: 0 auto 16px;
  place-items: center;
  border-radius: 14px;
  background: linear-gradient(135deg, #6366f1, #22d3ee);
  color: #0f172a;
  font-size: 26px;
  font-weight: 800;
}

.welcome h3 {
  margin: 0 0 6px;
  font-size: 18px;
}

.samples {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  margin-top: 18px;
}

.message {
  display: flex;
  margin-bottom: 18px;
}

.message.user {
  justify-content: flex-end;
}

.bubble {
  max-width: min(760px, 88%);
}

.question {
  padding: 10px 14px;
  border-radius: 14px 14px 4px 14px;
  background: #4f46e5;
  color: #fff;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.answer {
  padding: 14px 16px;
  border: 1px solid #e5e7eb;
  border-radius: 4px 14px 14px 14px;
  background: #fff;
  color: #1f2937;
  font-size: 14px;
  line-height: 1.8;
}

.answer :deep(.citation-ref) {
  padding: 0 2px;
  border-radius: 4px;
  background: #eef2ff;
  color: #4338ca;
  font-weight: 600;
  cursor: pointer;
}

.cursor {
  display: inline-block;
  width: 7px;
  height: 15px;
  margin-left: 2px;
  background: #6366f1;
  vertical-align: -2px;
  animation: blink 1s steps(2, start) infinite;
}

@keyframes blink {
  to {
    visibility: hidden;
  }
}

.message-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
  font-size: 12px;
}

.cite-chip {
  color: #4338ca;
}

.feedback-active {
  color: #16a34a;
  font-weight: 600;
}

.feedback-active.negative {
  color: #dc2626;
}

.sub-query-tag {
  margin: 0 4px 4px 0;
}

.feedback-hint {
  margin-top: 4px;
  font-size: 12px;
}

.trace-panel {
  margin-top: 10px;
  padding: 12px 14px;
  border: 1px dashed #c7d2fe;
  border-radius: 10px;
  background: #f8faff;
  font-size: 12px;
}

.trace-row {
  display: flex;
  gap: 12px;
  margin-bottom: 6px;
}

.agent-panel {
  margin-top: 10px;
  padding: 12px 14px;
  border: 1px dashed #a7f3d0;
  border-radius: 10px;
  background: #f6fffb;
  font-size: 12px;
}

.agent-panel-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.agent-step {
  padding: 8px 0;
  border-top: 1px dashed #d1fae5;
}

.agent-step:first-of-type {
  border-top: none;
}

.agent-step-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.agent-step-index {
  display: grid;
  width: 18px;
  height: 18px;
  place-items: center;
  border-radius: 50%;
  background: #10b981;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
}

.agent-step-tool {
  color: #065f46;
  font-weight: 600;
}

.agent-step-args {
  margin: 4px 0 0 26px;
  color: #334155;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  word-break: break-all;
}

.agent-step-obs {
  overflow: hidden;
  margin: 2px 0 0 26px;
  color: #475569;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trace-label {
  flex: 0 0 74px;
  color: #64748b;
}

.timing-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 10px 0;
}

.timing {
  display: flex;
  flex-direction: column;
  padding: 6px 10px;
  border-radius: 8px;
  background: #fff;
  border: 1px solid #e5e7eb;
}

.timing-stage {
  color: #94a3b8;
  font-size: 11px;
}

.timing-value {
  color: #1f2937;
  font-weight: 600;
}

.candidate-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.candidate {
  display: flex;
  gap: 8px;
}

.candidate-rank {
  flex: 0 0 24px;
  color: #6366f1;
  font-weight: 700;
}

.candidate-body {
  min-width: 0;
}

.candidate-head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.candidate-file {
  color: #1f2937;
  font-weight: 600;
}

.candidate-scores {
  margin-top: 2px;
  color: #64748b;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
}

.candidate-snippet {
  display: -webkit-box;
  overflow: hidden;
  margin-top: 3px;
  color: #475569;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.composer {
  padding: 12px clamp(16px, 5vw, 80px) 16px;
  border-top: 1px solid #e5e7eb;
  background: #fff;
}

.composer-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}

.citation-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.citation-card {
  border-radius: 10px;
}

.citation-card.highlight {
  border-color: #6366f1;
  box-shadow: 0 0 0 1px #6366f1;
}

.citation-card.dim {
  opacity: 0.6;
}

.citation-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.citation-file {
  flex: 1;
  font-weight: 600;
}

.citation-meta {
  margin: 6px 0;
  font-size: 12px;
}

.citation-text {
  margin: 0;
  color: #374151;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.error-text {
  color: #dc2626;
}

.ok-text {
  color: #059669;
}
</style>
