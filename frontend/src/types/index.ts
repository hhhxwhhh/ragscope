export interface Page<T> {
  items: T[]
  total: number
  offset: number
  limit: number
}

export interface User {
  id: string
  email: string
  display_name: string | null
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: User
}

export interface LoginPayload {
  email: string
  password: string
}

export interface RegisterPayload extends LoginPayload {
  display_name?: string
}

export interface KnowledgeBase {
  id: string
  name: string
  description: string | null
  chunk_size: number
  chunk_overlap: number
  index_version: number
  retrieval_config: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface KnowledgeBaseCreate {
  name: string
  description?: string | null
  chunk_size?: number
  chunk_overlap?: number
}

export type DocumentStatus = 'pending' | 'parsing' | 'indexing' | 'indexed' | 'failed'

export interface DocumentItem {
  id: string
  kb_id: string
  filename: string
  ext: string
  size_bytes: number
  status: DocumentStatus
  error_stage: string | null
  error_message: string | null
  chunk_count: number
  indexed_at: string | null
  created_at: string
}

export interface ChunkItem {
  id: string
  chunk_index: number
  content: string
  heading_path: string | null
  page_number: number | null
  char_start: number
  char_end: number
}

export interface Conversation {
  id: string
  kb_id: string
  title: string
  last_message_at: string
  created_at: string
}

export interface Citation {
  id: string
  chunk_id: string | null
  document_id: string | null
  filename: string
  chunk_index: number
  heading_path: string | null
  rank: number
  score: number | null
  snippet: string
  cited: boolean
}

export interface TraceCandidate {
  chunk_id: string
  document_id: string
  filename: string
  chunk_index: number
  heading_path: string | null
  content: string
  vector_score?: number | null
  bm25_score?: number | null
  vector_rank?: number | null
  bm25_rank?: number | null
  fused_score?: number | null
  rerank_score?: number | null
}

export interface RetrievalTrace {
  id?: string
  query: string
  rewritten_query: string
  rewrite_applied: boolean
  rewrite_error?: string | null
  reranked: boolean
  rerank_error?: string | null
  config: Record<string, unknown>
  stage_timings: Record<string, number>
  counts?: Record<string, number>
  candidates: Record<string, TraceCandidate[]>
  created_at?: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  status: 'streaming' | 'completed' | 'failed' | 'cancelled'
  latency_ms: number | null
  token_usage: Record<string, number> | null
  rewrite_query: string | null
  created_at: string
  citations: Citation[]
  trace: RetrievalTrace | null
}

export interface ChatRequest {
  question: string
  conversation_id?: string | null
}

export interface DebugRequest {
  query: string
  config?: Record<string, unknown>
}

export interface DebugResponse {
  query: string
  rewritten_query: string
  rewrite_applied: boolean
  rewrite_error: string | null
  reranked: boolean
  rerank_error: string | null
  config: Record<string, unknown>
  stage_timings: Record<string, number>
  counts: Record<string, number>
  candidates: Record<string, TraceCandidate[]>
}
