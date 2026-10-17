<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Plus } from '@element-plus/icons-vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

import { getKb } from '@/api/kb'
import {
  deleteConversation,
  listConversations,
  listMessages,
  streamChat,
} from '@/api/chat'
import type {
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

const activeConversationId = computed(() => props.conversationId || '')
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
  total_ms: '总计',
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
  const page = await listConversations(props.id)
  conversations.value = page.items
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
    rewrite_query: null,
    created_at: new Date().toISOString(),
    citations: [],
    trace: null,
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
      { question, conversation_id: activeConversationId.value || null },
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
          ElMessage.error(detail)
        },
      },
      controller.value.signal,
    )
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      assistantMessage.status = 'cancelled'
      assistantMessage.content = streamedText
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

onMounted(scrollToBottom)
</script>

<template>
  <div class="chat-page">
    <aside class="conv-sidebar">
      <div class="conv-head">
        <el-button type="primary" :icon="Plus" class="new-chat" @click="startNewChat">
          新对话
        </el-button>
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
          <el-icon class="conv-delete" @click.stop="removeConversation(conversation)">
            <Delete />
          </el-icon>
        </button>
        <div v-if="!conversations.length" class="conv-empty muted">还没有对话</div>
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
        <el-tag v-if="sending" type="warning" size="small">生成中…</el-tag>
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
              <span v-if="message.status === 'cancelled'" class="muted">已停止</span>
              <span v-if="message.status === 'failed'" class="error-text">生成失败</span>
              <span v-if="message.latency_ms !== null" class="muted">{{ message.latency_ms }} ms</span>
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
            {{ citation.heading_path || '无标题' }} · chunk #{{ citation.chunk_index }} · score
            {{ fmt(citation.score) }}
          </div>
          <p class="citation-text">{{ citation.snippet }}</p>
        </el-card>
        <el-empty v-if="!activeCitations.length" description="没有引用" />
      </div>
    </el-drawer>
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

.conv-delete {
  grid-row: 1 / span 2;
  align-self: center;
  color: #cbd5e1;
  font-size: 14px;
  visibility: hidden;
}

.conv-item:hover .conv-delete {
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
