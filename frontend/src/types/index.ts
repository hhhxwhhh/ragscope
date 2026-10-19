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
  page_count: number | null
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
  page_number: number | null
  rank: number
  score: number | null
  snippet: string
  cited: boolean
}

export interface MessageFeedback {
  id: string
  message_id: string
  rating: -1 | 1
  reason: string | null
  comment: string | null
  updated_at: string
}

export interface CitationStats {
  markers: number
  valid_markers: number
  invalid_markers: number
  source_count: number
  cited_sources: number
  citation_rate: number
}

export interface FeedbackCaseResult {
  dataset_id: string
  dataset_name: string
  case_id: string
  created: boolean
}

export interface TraceCandidate {
  chunk_id: string
  document_id: string
  filename: string
  chunk_index: number
  heading_path: string | null
  page_number?: number | null
  content: string
  vector_score?: number | null
  bm25_score?: number | null
  vector_rank?: number | null
  bm25_rank?: number | null
  fused_score?: number | null
  rerank_score?: number | null
  matched_queries?: number[] | null
}

export interface RetrievalTrace {
  id?: string
  query: string
  rewritten_query: string
  rewrite_applied: boolean
  rewrite_error?: string | null
  sub_queries?: string[]
  cache_hit?: boolean
  reranked: boolean
  rerank_error?: string | null
  config: Record<string, unknown>
  stage_timings: Record<string, number>
  counts?: Record<string, number>
  candidates: Record<string, TraceCandidate[]>
  created_at?: string
}

export interface AgentStep {
  step_index: number
  tool_name: string
  arguments: Record<string, unknown>
  observation: Record<string, unknown>
  status: 'ok' | 'error' | string
  latency_ms: number
}

export interface AgentRun {
  id: string
  status: string
  stopped_reason: string | null
  step_count: number
  tool_calls: number
  latency_ms: number | null
  error: string | null
  steps: AgentStep[]
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  status: 'streaming' | 'completed' | 'failed' | 'cancelled'
  latency_ms: number | null
  token_usage: Record<string, number> | null
  citation_stats: CitationStats | null
  rewrite_query: string | null
  created_at: string
  citations: Citation[]
  trace: RetrievalTrace | null
  feedback: MessageFeedback | null
  agent: AgentRun | null
}

export type ChatMode = 'rag' | 'agent' | 'auto'

export interface ChatRequest {
  question: string
  conversation_id?: string | null
  mode?: ChatMode
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
  sub_queries: string[]
  multi_query_applied: boolean
  multi_query_error: string | null
  cache_hit: boolean
  reranked: boolean
  rerank_error: string | null
  config: Record<string, unknown>
  stage_timings: Record<string, number>
  counts: Record<string, number>
  candidates: Record<string, TraceCandidate[]>
}

export interface RelevantRef {
  document_id: string
  chunk_index: number
  filename?: string | null
}

export interface EvalDataset {
  id: string
  kb_id: string
  name: string
  description: string | null
  chunk_size: number
  chunk_overlap: number
  case_count: number
  run_count: number
  created_at: string
  updated_at: string
}

export type EvalRunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface EvalRun {
  id: string
  dataset_id: string
  kb_id: string
  base_run_id: string | null
  status: EvalRunStatus
  config: Record<string, unknown>
  metrics: Record<string, number>
  progress: { done?: number; total?: number; current_case?: string | null }
  index_version: number
  error: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  dataset_name: string | null
}

export interface EvalCase {
  id: string
  dataset_id: string
  question: string
  relevant_refs: RelevantRef[]
  answerable: boolean
  expected_mode: 'rag' | 'agent' | null
  notes: string | null
  created_at: string
  updated_at: string
}

export interface RoutingCase {
  case_id: string
  question: string
  answerable: boolean
  expected_mode: 'rag' | 'agent' | null
  predicted_mode: 'rag' | 'agent'
  correct: boolean | null
  signals: string[]
  reason: string
}

export interface RoutingReport {
  dataset_id: string
  total: number
  labeled: number
  unlabeled: number
  accuracy: number | null
  agent_precision: number | null
  agent_recall: number | null
  agent_f1: number | null
  predicted_agent_rate: number
  expected_agent_rate: number | null
  confusion: Record<string, number>
  cases: RoutingCase[]
}

export interface RetrievedItem {
  rank: number
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

export interface EvalResult {
  id: string
  case_id: string
  question: string
  answerable: boolean
  relevant_refs: RelevantRef[]
  retrieved: RetrievedItem[]
  metrics: Record<string, number | boolean | null>
  refused: boolean
  latency_ms: number
  error: string | null
}

export interface EvalRunDetail extends EvalRun {
  results: EvalResult[]
}

export interface MetricDelta {
  key: string
  label: string
  base: number | null
  candidate: number | null
  delta: number | null
}

export interface CompareCase {
  case_id: string
  question: string
  answerable: boolean
  base_rank: number | null
  candidate_rank: number | null
  base_refused: boolean
  candidate_refused: boolean
  outcome: 'better' | 'worse' | 'same'
  base_metrics: Record<string, number | boolean | null>
  candidate_metrics: Record<string, number | boolean | null>
}

export interface EvalCompare {
  base: EvalRun
  candidate: EvalRun
  metric_deltas: MetricDelta[]
  cases: CompareCase[]
  improved: number
  degraded: number
  unchanged: number
}

export interface ChunkSearchItem {
  id: string
  document_id: string
  filename: string
  chunk_index: number
  heading_path: string | null
  content: string
}

export interface RecentRun {
  id: string
  dataset_id: string
  dataset_name: string
  status: EvalRunStatus
  recall_5: number | null
  mrr: number | null
  cases_total: number
  created_at: string
}

export interface DashboardSummary {
  kb_count: number
  document_count: number
  indexed_document_count: number
  chunk_count: number
  conversation_count: number
  message_count: number
  agent_run_count: number
  agent_completed_count: number
  agent_success_rate: number
  agent_avg_steps: number
  agent_avg_tool_calls: number
  agent_avg_latency_ms: number
  agent_total_tokens: number
  eval_dataset_count: number
  eval_case_count: number
  eval_run_count: number
  avg_latency_ms: number
  feedback_positive: number
  feedback_negative: number
  feedback_positive_rate: number
  avg_citation_rate: number
  invalid_citation_markers: number
  recent_runs: RecentRun[]
}

export interface TrendPoint {
  date: string
  messages: number
  conversations: number
  eval_runs: number
}

export interface DashboardTrend {
  days: number
  points: TrendPoint[]
}
