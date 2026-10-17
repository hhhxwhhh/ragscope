import { api, tokenStorage } from './client'
import type {
  ChatMessage,
  ChatRequest,
  Conversation,
  DebugRequest,
  DebugResponse,
  FeedbackCaseResult,
  MessageFeedback,
  Page,
  RetrievalTrace,
} from '@/types'

export async function listConversations(
  kbId: string,
  offset = 0,
  limit = 50,
  q?: string,
): Promise<Page<Conversation>> {
  const { data } = await api.get<Page<Conversation>>(`/kbs/${kbId}/conversations`, {
    params: { offset, limit, ...(q ? { q } : {}) },
  })
  return data
}

export async function renameConversation(
  conversationId: string,
  title: string,
): Promise<Conversation> {
  const { data } = await api.patch<Conversation>(`/conversations/${conversationId}`, { title })
  return data
}

export async function exportConversation(conversationId: string): Promise<Blob> {
  const { data } = await api.get<Blob>(`/conversations/${conversationId}/export`, {
    responseType: 'blob',
  })
  return data
}

export async function upsertFeedback(
  messageId: string,
  payload: { rating: -1 | 1; reason?: string | null; comment?: string | null },
): Promise<MessageFeedback> {
  const { data } = await api.put<MessageFeedback>(`/messages/${messageId}/feedback`, payload)
  return data
}

export async function deleteFeedback(messageId: string): Promise<void> {
  await api.delete(`/messages/${messageId}/feedback`)
}

export async function convertFeedbackToCase(messageId: string): Promise<FeedbackCaseResult> {
  const { data } = await api.post<FeedbackCaseResult>(
    `/messages/${messageId}/feedback/eval-case`,
  )
  return data
}

export async function createConversation(kbId: string, title?: string): Promise<Conversation> {
  const { data } = await api.post<Conversation>(`/kbs/${kbId}/conversations`, { title })
  return data
}

export async function listMessages(
  conversationId: string,
  offset = 0,
  limit = 100,
): Promise<Page<ChatMessage>> {
  const { data } = await api.get<Page<ChatMessage>>(
    `/conversations/${conversationId}/messages`,
    { params: { offset, limit } },
  )
  return data
}

export async function deleteConversation(conversationId: string): Promise<void> {
  await api.delete(`/conversations/${conversationId}`)
}

export async function debugRetrieval(kbId: string, payload: DebugRequest): Promise<DebugResponse> {
  const { data } = await api.post<DebugResponse>(`/kbs/${kbId}/retrieval/debug`, payload, {
    timeout: 120_000,
  })
  return data
}

export interface StreamMeta {
  conversation_id: string
  user_message_id: string
  rewritten_query: string
  rewrite_applied: boolean
  reranked: boolean
}

export interface StreamHandlers {
  onMeta?: (meta: StreamMeta) => void
  onRetrieval?: (trace: RetrievalTrace) => void
  onToken?: (delta: string) => void
  onDone?: (message: ChatMessage | null) => void
  onError?: (detail: string) => void
}

/**
 * SSE over fetch: EventSource cannot send a POST body or Authorization header,
 * so we read the response stream and split frames ourselves. TextDecoder with
 * `stream: true` keeps multi-byte Chinese characters intact across chunks.
 */
export async function streamChat(
  kbId: string,
  payload: ChatRequest,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const token = tokenStorage.get()
  const response = await fetch(`/api/v1/kbs/${kbId}/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
    signal,
  })

  if (!response.ok) {
    let detail = `请求失败（${response.status}）`
    try {
      const body = (await response.json()) as { detail?: string }
      detail = body.detail || detail
    } catch {
      // Non-JSON error body: keep the status message.
    }
    throw new Error(detail)
  }
  if (!response.body) {
    throw new Error('浏览器不支持流式响应')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) {
        break
      }
      buffer += decoder.decode(value, { stream: true })
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        dispatchFrame(frame, handlers)
        boundary = buffer.indexOf('\n\n')
      }
    }
    // Flush a final frame that was not terminated by a blank line.
    if (buffer.trim()) {
      dispatchFrame(buffer, handlers)
    }
  } finally {
    reader.releaseLock()
  }
}

function dispatchFrame(frame: string, handlers: StreamHandlers): void {
  let event = 'message'
  const dataLines: string[] = []

  for (const rawLine of frame.split('\n')) {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
    if (!line || line.startsWith(':')) {
      continue
    }
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }
  if (!dataLines.length) {
    return
  }

  let data: unknown
  try {
    data = JSON.parse(dataLines.join('\n'))
  } catch {
    return
  }

  switch (event) {
    case 'meta':
      handlers.onMeta?.(data as StreamMeta)
      break
    case 'retrieval':
      handlers.onRetrieval?.(data as RetrievalTrace)
      break
    case 'token':
      handlers.onToken?.((data as { delta: string }).delta)
      break
    case 'done': {
      const payload = data as { message?: ChatMessage }
      handlers.onDone?.(payload.message ?? null)
      break
    }
    case 'error':
      handlers.onError?.((data as { detail: string }).detail)
      break
    default:
      break
  }
}
